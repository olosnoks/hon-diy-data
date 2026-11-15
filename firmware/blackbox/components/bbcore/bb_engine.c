/* bbcore: the engine. Resampler -> ring buffers -> trigger rules -> event
 * file writer, plus the USB protocol handler. See bbcore.h for the calling
 * rules; FORMAT.md and PROTOCOL.md describe what it writes and speaks. */
#include <stdio.h>
#include <string.h>

#include "bbcore.h"

#define MARGIN_S      5         /* ring space beyond pre + post, for a slow card */
#define EV_RING       128
#define GPS_RING      64
#define AGE_MAX_MS    60000u
#define SYNC_FIRST    4         /* sync once this many pre-trigger blocks are down */

enum { W_IDLE, W_OPEN, W_PRE, W_POST, W_FINISH, W_CSV };

struct bb_engine {
    bb_config_t cfg;
    bb_platform_t plat;

    /* rings */
    bb_sample_t *s_ring; uint32_t s_cap, s_cnt, s_valid_from;
    uint64_t t_base_us;         /* record n is at t_base_us + n * 1000 */
    bb_edge_t *e_ring; uint32_t e_cap, e_cnt;
    bb_event_t ev_ring[EV_RING]; uint32_t ev_cnt;
    bb_gps_t gps_ring[GPS_RING]; uint32_t gps_cnt;
    bb_index_t *index;

    /* acquisition */
    bb_resamp_t rs;
    bb_edge_ch_t ech[BB_N_EDGE];
    uint64_t last_edge_us[BB_N_EDGE]; uint8_t edge_seen[BB_N_EDGE];
    bb_trigger_t trig;
    uint8_t button, sd_ok, selftest_failed;
    bb_sample_t latest; uint64_t latest_t_us;
    uint16_t live_min[BB_N_ANALOG];
    uint64_t ig1_off_since_us;
    uint32_t overruns, io_errors, edges_total;
    int64_t unix_us_at_t0; uint8_t time_flags;

    /* capture / writer */
    uint8_t w_state, pending, last_reason;
    bb_event_t pend_ev; uint32_t pend_n;
    bb_event_t trig_ev;
    uint32_t trig_n, pre_lo, pre_hi, post_lo, end_n;
    uint32_t e_lo, e_fwd, ev_fwd;
    uint32_t event_no, captures, seq, n_index, file_off, blocks_since_sync, n_samples_written;
    uint64_t last_capture_end_us;
    void *fh, *csv_in, *csv_out;
    uint64_t csv_last_t0; uint8_t csv_started;

    /* summary */
    struct { uint32_t n; uint64_t sum[BB_N_ANALOG]; uint16_t lo[BB_N_ANALOG]; } sum;
    char sum_line[224]; uint8_t sum_ready; uint32_t sum_lines, ev_sum;
    void *sum_fh;

    /* link */
    bb_deframer_t rx;
    uint8_t tx_seq;
    uint64_t last_live_us; uint32_t live_period_us;
    uint32_t ev_link;
    void *rd_fh; uint32_t rd_no;

    uint64_t scratch[(BB_SCRATCH + 7) / 8];     /* 8-aligned: edges are 64-bit */
};

/* ---- small helpers ------------------------------------------------------ */

#define IO_BEGIN(e) do { if ((e)->plat.unlock) (e)->plat.unlock((e)->plat.lock_ctx); } while (0)
#define IO_END(e)   do { if ((e)->plat.lock) (e)->plat.lock((e)->plat.lock_ctx); } while (0)

static uint64_t now_us(const bb_engine_t *e)
{
    return e->plat.now_us ? e->plat.now_us(e->plat.now_ctx) : e->latest_t_us;
}

static void send(bb_engine_t *e, uint8_t type, const void *payload, size_t len)
{
    uint8_t wire[BB_FRAME_MAX_WIRE];
    if (!e->plat.tx) return;
    size_t n = bb_frame_encode(type, e->tx_seq++, payload, len, wire);
    if (n) e->plat.tx(e->plat.tx_ctx, wire, n);
}

void bb_engine_text(bb_engine_t *e, const char *text)
{
    size_t len = strlen(text);
    while (len) {
        size_t n = len > 240 ? 240 : len;
        send(e, BB_F_TEXT, text, n);
        text += n; len -= n;
    }
}

static void push_event(bb_engine_t *e, const bb_event_t *ev)
{
    e->ev_ring[e->ev_cnt % EV_RING] = *ev;
    e->ev_cnt++;
}

static void push_simple(bb_engine_t *e, uint64_t t, uint8_t code, int32_t value)
{
    bb_event_t ev;
    memset(&ev, 0, sizeof(ev));
    ev.t_us = t; ev.code = code; ev.src = 0xFF; ev.value = value;
    push_event(e, &ev);
}

static void path_event(char *out, size_t cap, uint32_t no, const char *ext)
{
    snprintf(out, cap, "/EVENTS/EV%06lu.%s", (unsigned long)(no % 1000000u), ext);
}

/* ---- init --------------------------------------------------------------- */

static size_t align8(size_t n) { return (n + 7u) & ~(size_t)7u; }

static size_t mem_for(int32_t pre_s, int32_t post_s, int32_t edge_ring)
{
    return align8(sizeof(bb_engine_t)) + BB_MAX_INDEX * sizeof(bb_index_t)
           + (size_t)(pre_s + post_s + MARGIN_S) * BB_SAMPLE_HZ * sizeof(bb_sample_t)
           + (size_t)edge_ring * sizeof(bb_edge_t);
}

size_t bb_engine_mem_size(const bb_config_t *cfg)
{
    return mem_for(cfg->pre_s, cfg->post_s, cfg->edge_ring);
}

bb_engine_t *bb_engine_init(void *mem, size_t mem_size, const bb_config_t *cfg,
                            const bb_platform_t *plat)
{
    bb_config_t c = *cfg;
    while (mem_for(c.pre_s, c.post_s, c.edge_ring) > mem_size) {
        if (c.edge_ring > 4096) c.edge_ring /= 2;
        else if (c.pre_s > 1) c.pre_s--;
        else if (c.post_s > 1) c.post_s--;
        else return NULL;
    }
    bb_engine_t *e = mem;
    memset(e, 0, sizeof(*e));
    e->cfg = c;
    e->plat = *plat;
    uint8_t *p = (uint8_t *)mem + align8(sizeof(*e));
    e->e_ring = (bb_edge_t *)p;          e->e_cap = (uint32_t)c.edge_ring;
    p += (size_t)e->e_cap * sizeof(bb_edge_t);
    e->index = (bb_index_t *)p;          p += BB_MAX_INDEX * sizeof(bb_index_t);
    e->s_ring = (bb_sample_t *)p;        e->s_cap = (uint32_t)(c.pre_s + c.post_s + MARGIN_S) * BB_SAMPLE_HZ;
    e->event_no = plat->last_event_no;
    e->sd_ok = (uint8_t)plat->sd_ok;
    e->live_period_us = 1000000u / (uint32_t)c.live_hz;
    e->rs.period_q8 = plat->adc_conv_hz ? (uint32_t)(256000000ull / plat->adc_conv_hz) : 256u * 40u;
    for (int i = 0; i < BB_N_ANALOG; i++) e->live_min[i] = 0xFFFF;
    return e;
}

const bb_config_t *bb_engine_config(const bb_engine_t *e) { return &e->cfg; }
void bb_engine_set_sd(bb_engine_t *e, int ok) { e->sd_ok = (uint8_t)(ok ? 1 : 0); }
void bb_engine_set_selftest_failed(bb_engine_t *e, int failed) { e->selftest_failed = (uint8_t)(failed ? 1 : 0); }
void bb_engine_button(bb_engine_t *e, int pressed) { e->button = (uint8_t)(pressed ? 1 : 0); }

void bb_engine_gps(bb_engine_t *e, const bb_gps_t *fix)
{
    e->gps_ring[e->gps_cnt % GPS_RING] = *fix;
    e->gps_cnt++;
    if (fix->fix && fix->utc && !(e->time_flags & BB_HF_TIME_GPS)) {
        e->unix_us_at_t0 = (int64_t)fix->utc * 1000000 - (int64_t)fix->t_us;
        e->time_flags = BB_HF_TIME_VALID | BB_HF_TIME_GPS;
        push_simple(e, fix->t_us, BB_EV_TIME_SYNC, 1);
    }
}

/* ---- acquisition -------------------------------------------------------- */

void bb_engine_edge(bb_engine_t *e, unsigned ch, int gpio_level, uint64_t t_us)
{
    if (ch >= BB_N_EDGE) return;
    int level = e->cfg.invert[ch] ? !gpio_level : !!gpio_level;
    bb_edge_feed(&e->ech[ch], t_us, level, (uint32_t)e->cfg.edge_min_us);
    e->last_edge_us[ch] = t_us; e->edge_seen[ch] = 1;
    e->e_ring[e->e_cnt % e->e_cap] = BB_EDGE(t_us, ch, level);
    e->e_cnt++; e->edges_total++;
}

static int capture_allowed(const bb_engine_t *e, const bb_event_t *ev, uint64_t t)
{
    if (e->w_state != W_IDLE && e->w_state != W_CSV) return 0;
    if (e->pending) return 0;
    if (ev->code == BB_R_BUTTON || ev->code == BB_R_HOST || ev->code == BB_R_BROWNOUT) return 1;
    return e->captures == 0 || t >= e->last_capture_end_us + (uint64_t)e->cfg.holdoff_s * 1000000u;
}

static void request_capture(bb_engine_t *e, const bb_event_t *ev, uint32_t n)
{
    e->pending = 1; e->pend_ev = *ev; e->pend_n = n; e->last_reason = ev->code;
}

static void emit_record(bb_engine_t *e, uint64_t t, const uint16_t *mean, const uint16_t *lo,
                        const uint16_t *hi, int gap)
{
    bb_sample_t s;
    bb_trig_in_t in;
    memset(&s, 0, sizeof(s));
    memset(&in, 0, sizeof(in));
    for (int i = 0; i < BB_N_ANALOG; i++) {
        const uint16_t pin[3] = {mean[i], lo[i], hi[i]};
        uint16_t car[3];
        for (int k = 0; k < 3; k++) {
            int64_t v = (int64_t)pin[k] * e->cfg.scale_milli[i] / 1000 + e->cfg.offset_mv[i];
            car[k] = (uint16_t)(v < 0 ? 0 : v > 65535 ? 65535 : v);
        }
        s.mv[i] = car[0]; in.min_mv[i] = car[1]; in.max_mv[i] = car[2];
        if (i < BB_N_RAILS) {
            unsigned a = car[1] / 100u, b = (car[2] + 99u) / 100u;
            s.min_dv[i] = (uint8_t)(a > 255 ? 255 : a);
            s.max_dv[i] = (uint8_t)(b > 255 ? 255 : b);
        }
    }
    const uint32_t timeout_us = (uint32_t)e->cfg.pulse_timeout_ms * 1000u;
    s.rpm_tach = (uint16_t)bb_edge_rpm(&e->ech[BB_E_TACH], t, (uint32_t)e->cfg.tach_ppr, timeout_us);
    s.rpm_ign = (uint16_t)bb_edge_rpm(&e->ech[BB_E_IGN], t, (uint32_t)e->cfg.ign_ppr, timeout_us);
    const bb_edge_ch_t *inj = &e->ech[BB_E_INJ];
    s.inj_pw_us = (uint16_t)(inj->pw_us > 65535 ? 65535 : inj->pw_us);
    s.inj_duty = (uint8_t)bb_edge_duty200(inj, t, 2u * timeout_us);
    for (int ch = 0; ch < BB_N_EDGE; ch++) {
        uint64_t age = !e->edge_seen[ch] ? AGE_MAX_MS
                     : t > e->last_edge_us[ch] ? (t - e->last_edge_us[ch]) / 1000u : 0;
        in.age_ms[ch] = age > AGE_MAX_MS ? AGE_MAX_MS : (uint32_t)age;
        if (e->ech[ch].level) s.dig |= (uint8_t)(BB_DIG_TACH << ch);
    }
    if (e->button) s.dig |= BB_DIG_BUTTON;
    if (gap) s.dig |= BB_DIG_GAP;

    bb_event_t out[BB_TRIG_MAX_OUT];
    uint8_t dig = 0;
    in.s = &s; in.t_us = t; in.button = e->button;
    int n_ev = bb_trigger_step(&e->trig, &e->cfg, &in, out, &dig);
    s.dig |= dig;
    for (int i = 0; i < n_ev; i++) {
        push_event(e, &out[i]);
        if (out[i].code <= BB_R_MAX_TRIGGER) {
            e->last_reason = out[i].code;
            if (out[i].rsv == BB_ACT_CAPTURE && capture_allowed(e, &out[i], t))
                request_capture(e, &out[i], e->s_cnt);
        }
    }
    if (e->w_state != W_IDLE && e->w_state != W_CSV) s.dig |= BB_DIG_CAPTURING;

    if (dig & BB_DIG_IG1_ON) e->ig1_off_since_us = 0;
    else if (!e->ig1_off_since_us) e->ig1_off_since_us = t ? t : 1;

    e->s_ring[e->s_cnt % e->s_cap] = s;
    e->s_cnt++;
    e->latest = s; e->latest_t_us = t;

    for (int i = 0; i < BB_N_ANALOG; i++) {
        if (in.min_mv[i] < e->live_min[i]) e->live_min[i] = in.min_mv[i];
        if (e->sum.n == 0 || in.min_mv[i] < e->sum.lo[i]) e->sum.lo[i] = in.min_mv[i];
        e->sum.sum[i] += s.mv[i];
    }
    if (++e->sum.n >= (uint32_t)e->cfg.summary_s * BB_SAMPLE_HZ) {
        if (!e->sum_ready) {
            char *o = e->sum_line;
            size_t cap = sizeof(e->sum_line), w;
            int64_t unix_s = e->time_flags ? (e->unix_us_at_t0 + (int64_t)t) / 1000000 : 0;
            w = (size_t)snprintf(o, cap, "%lu.%03lu,%lld", (unsigned long)(t / 1000000u),
                                 (unsigned long)(t / 1000u % 1000u), (long long)unix_s);
            for (int i = 0; i < BB_N_ANALOG; i++)
                w += (size_t)snprintf(o + w, cap - w, ",%lu,%u", (unsigned long)(e->sum.sum[i] / e->sum.n),
                                      e->sum.lo[i]);
            snprintf(o + w, cap - w, ",%u,%u,%u,%u,%u,%lu\n", s.rpm_tach, s.rpm_ign, s.inj_pw_us,
                     s.inj_duty, s.dig, (unsigned long)e->captures);
            e->sum_ready = 1;
        }
        memset(&e->sum, 0, sizeof(e->sum));
    }
}

void bb_engine_adc_chunk(bb_engine_t *e, uint64_t t_arrival_us, const bb_conv_t *conv, size_t n)
{
    bb_resamp_t *r = &e->rs;
    if (!n) return;
    uint64_t span = ((uint64_t)n * r->period_q8) >> 8;
    uint64_t end;
    if (!r->started) {
        r->started = 1;
        end = t_arrival_us;
        r->next_tick_us = (t_arrival_us > span ? (t_arrival_us - span) / BB_TICK_US + 1 : 1) * BB_TICK_US;
        e->t_base_us = r->next_tick_us - (uint64_t)e->s_cnt * BB_TICK_US;
        for (int i = 0; i < BB_N_ANALOG; i++) { r->lo[i] = 0xFFFF; r->hi[i] = 0; }
    } else {
        uint64_t predicted = r->end_us + span;
        int64_t err = (int64_t)(t_arrival_us - predicted);
        if (err > 50000 || err < -50000) {
            end = t_arrival_us;                 /* lost data or a long stall: resync */
        } else {
            end = predicted + (uint64_t)(err / 8);
            /* track the real conversion rate, slowly */
            int64_t meas_q8 = (int64_t)((t_arrival_us - r->end_us) << 8) / (int64_t)n;
            int64_t p = (int64_t)r->period_q8 + (meas_q8 - (int64_t)r->period_q8) / 64;
            int64_t nominal = e->plat.adc_conv_hz ? (int64_t)(256000000ull / e->plat.adc_conv_hz) : p;
            if (p < nominal * 4 / 5) p = nominal * 4 / 5;
            if (p > nominal * 6 / 5) p = nominal * 6 / 5;
            r->period_q8 = (uint32_t)p;
        }
        span = ((uint64_t)n * r->period_q8) >> 8;
    }
    uint64_t start = end > span ? end - span : 0;
    if (start < r->end_us && r->end_us <= end) start = r->end_us;       /* never step back */

    /* a hole longer than the ring: restart the time base instead of filling it */
    if (start > r->next_tick_us && start - r->next_tick_us > (uint64_t)e->s_cap * BB_TICK_US / 2) {
        r->next_tick_us = (start / BB_TICK_US + 1) * BB_TICK_US;
        e->t_base_us = r->next_tick_us - (uint64_t)e->s_cnt * BB_TICK_US;
        e->s_valid_from = e->s_cnt;
        e->overruns++;
        push_simple(e, r->next_tick_us, BB_EV_OVERRUN, 0);
    }

    uint64_t step_q8 = n ? ((end - start) << 8) / n : 0;
    for (size_t i = 0; i < n; i++) {
        uint64_t t_i = start + (((uint64_t)(i + 1) * step_q8) >> 8);
        while (t_i >= r->next_tick_us) {
            uint16_t mean[BB_N_ANALOG], lo[BB_N_ANALOG], hi[BB_N_ANALOG];
            int gap = 1;
            for (int c = 0; c < BB_N_ANALOG; c++) {
                if (r->n[c]) {
                    mean[c] = (uint16_t)(r->acc[c] / r->n[c]); lo[c] = r->lo[c]; hi[c] = r->hi[c];
                    r->last[c] = mean[c];
                    if (c < BB_N_RAILS) gap = 0;
                } else {
                    mean[c] = lo[c] = hi[c] = r->last[c];
                }
                r->acc[c] = 0; r->n[c] = 0; r->lo[c] = 0xFFFF; r->hi[c] = 0;
            }
            emit_record(e, r->next_tick_us, mean, lo, hi, gap);
            r->next_tick_us += BB_TICK_US;
        }
        unsigned c = conv[i].ch;
        if (c < BB_N_ANALOG && r->n[c] < 0xFFFF) {
            r->acc[c] += conv[i].mv; r->n[c]++;
            if (conv[i].mv < r->lo[c]) r->lo[c] = conv[i].mv;
            if (conv[i].mv > r->hi[c]) r->hi[c] = conv[i].mv;
        }
    }
    r->end_us = end;
}

/* ---- event file writer -------------------------------------------------- */

static int io_write(bb_engine_t *e, void *fh, const void *data, size_t len)
{
    if (e->plat.io.write(e->plat.io.ctx, fh, data, len) != 0) { e->io_errors++; return -1; }
    return 0;
}

/* Write one block. Caller is inside IO_BEGIN/IO_END. */
static int blk_write(bb_engine_t *e, uint8_t type, uint8_t flags, uint16_t count, uint64_t t0,
                     const void *payload, uint32_t len)
{
    bb_block_hdr_t h;
    h.magic = BB_BLOCK_MAGIC; h.type = type; h.flags = flags; h.count = count;
    h.payload_len = len; h.seq = e->seq++; h.t0_us = t0;
    uint32_t crc = bb_crc32(bb_crc32(0, &h, sizeof(h)), payload, len);
    if (e->n_index < BB_MAX_INDEX && type != BB_B_INDEX && type != BB_B_END) {
        bb_index_t *ix = &e->index[e->n_index++];
        ix->offset = e->file_off; ix->type = type; ix->flags = flags; ix->count = count; ix->t0_us = t0;
    }
    int rc = io_write(e, e->fh, &h, sizeof(h));
    if (!rc && len) rc = io_write(e, e->fh, payload, len);
    if (!rc) rc = io_write(e, e->fh, &crc, 4);
    e->file_off += (uint32_t)sizeof(h) + len + 4;
    return rc;
}

static void w_sync(bb_engine_t *e)
{
    if (e->plat.io.sync(e->plat.io.ctx, e->fh) != 0) e->io_errors++;
    e->blocks_since_sync = 0;
}

/* Copy records [lo, hi) out of the sample ring. 0 if they were overwritten. */
static int copy_samples(bb_engine_t *e, uint32_t lo, uint32_t hi, bb_sample_t *out)
{
    if (e->s_cnt - lo > e->s_cap) return 0;
    for (uint32_t i = lo; i != hi; i++) *out++ = e->s_ring[i % e->s_cap];
    return 1;
}

/* Write edges [lo, hi) in blocks. Lock held on entry and exit. */
static void write_edges(bb_engine_t *e, uint32_t lo, uint32_t hi)
{
    bb_edge_t *buf = (bb_edge_t *)(void *)e->scratch;
    while (lo != hi) {
        uint32_t n = hi - lo;
        if (n > BB_BLOCK_EDGES) n = BB_BLOCK_EDGES;
        if (e->e_cnt - lo > e->e_cap) {         /* overwritten: skip ahead */
            lo = e->e_cnt - e->e_cap;
            e->overruns++;
            if ((int32_t)(hi - lo) <= 0) return;
            continue;
        }
        for (uint32_t i = 0; i < n; i++) buf[i] = e->e_ring[(lo + i) % e->e_cap];
        IO_BEGIN(e);
        blk_write(e, BB_B_EDGES, 0, (uint16_t)n, BB_EDGE_T(buf[0]), buf, n * (uint32_t)sizeof(bb_edge_t));
        IO_END(e);
        lo += n;
    }
}

static void w_abort_csv(bb_engine_t *e)
{
    IO_BEGIN(e);
    if (e->csv_in) e->plat.io.close(e->plat.io.ctx, e->csv_in);
    if (e->csv_out) e->plat.io.close(e->plat.io.ctx, e->csv_out);
    IO_END(e);
    e->csv_in = e->csv_out = NULL;
    e->w_state = W_IDLE;
}

static void w_open(bb_engine_t *e)
{
    char path[32];
    bb_file_hdr_t h;
    uint32_t n = e->pend_n;
    e->pending = 0;
    e->trig_ev = e->pend_ev; e->trig_n = n;
    uint32_t pre = (uint32_t)e->cfg.pre_s * BB_SAMPLE_HZ;
    uint32_t oldest = e->s_cnt > e->s_cap ? e->s_cnt - e->s_cap : 0;
    if (oldest < e->s_valid_from) oldest = e->s_valid_from;
    e->pre_lo = n > pre ? n - pre : 0;
    if (e->pre_lo < oldest) e->pre_lo = oldest;
    e->pre_hi = n + 1 <= e->s_cnt ? n + 1 : e->s_cnt;
    e->post_lo = e->pre_hi;
    e->end_n = n + (uint32_t)e->cfg.post_s * BB_SAMPLE_HZ;
    e->e_lo = e->e_fwd = e->e_cnt;
    e->seq = 0; e->n_index = 0; e->file_off = 0; e->blocks_since_sync = 0; e->n_samples_written = 0;
    e->event_no++; e->captures++;
    if (e->rd_fh) { e->plat.io.close(e->plat.io.ctx, e->rd_fh); e->rd_fh = NULL; }

    memset(&h, 0, sizeof(h));
    h.magic = BB_FILE_MAGIC; h.version = BB_FILE_VERSION; h.hdr_len = sizeof(h);
    h.event_no = e->event_no; h.boot_no = e->plat.boot_no;
    h.reason = e->trig_ev.code; h.n_analog = BB_N_ANALOG; h.n_edge = BB_N_EDGE; h.flags = e->time_flags;
    h.sample_hz = BB_SAMPLE_HZ; h.pre_ms = (n - e->pre_lo); h.post_ms = (uint32_t)e->cfg.post_s * 1000u;
    h.trigger_t_us = e->trig_ev.t_us; h.unix_us_at_t0 = e->unix_us_at_t0;
    h.trigger_value = e->trig_ev.value;
    strncpy(h.fw, BB_FW_VERSION, sizeof(h.fw) - 1);
    if (e->plat.device) strncpy(h.device, e->plat.device, sizeof(h.device) - 1);
    h.tach_ppr = (uint8_t)e->cfg.tach_ppr; h.ign_ppr = (uint8_t)e->cfg.ign_ppr;
    h.crc = bb_crc32(0, &h, offsetof(bb_file_hdr_t, crc));

    /* events so far inside the pre-trigger window, oldest first */
    bb_event_t *evs = (bb_event_t *)(void *)e->scratch;
    uint32_t n_ev = 0, first = e->ev_cnt > EV_RING ? e->ev_cnt - EV_RING : 0;
    uint64_t t_lo = e->t_base_us + (uint64_t)e->pre_lo * BB_TICK_US;
    for (uint32_t i = first; i != e->ev_cnt; i++)
        if (e->ev_ring[i % EV_RING].t_us >= t_lo) evs[n_ev++] = e->ev_ring[i % EV_RING];
    e->ev_fwd = e->ev_cnt;

    char cfg_text[2600];
    size_t cfg_len = bb_config_dump(&e->cfg, cfg_text, sizeof(cfg_text));

    path_event(path, sizeof(path), e->event_no, "BBX");
    IO_BEGIN(e);
    e->fh = e->sd_ok ? e->plat.io.open(e->plat.io.ctx, path, "wb") : NULL;
    if (e->fh) {
        io_write(e, e->fh, &h, sizeof(h));
        e->file_off = sizeof(h);
        blk_write(e, BB_B_EVENTS, 0, (uint16_t)n_ev, n_ev ? evs[0].t_us : e->trig_ev.t_us, evs,
                  n_ev * (uint32_t)sizeof(bb_event_t));
        blk_write(e, BB_B_CONFIG, 0, 0, e->trig_ev.t_us, cfg_text, (uint32_t)cfg_len);
    }
    IO_END(e);
    if (!e->fh) { e->io_errors++; e->w_state = W_IDLE; e->last_capture_end_us = e->trig_ev.t_us; return; }
    e->w_state = W_PRE;
}

static void pre_done(bb_engine_t *e)
{
    IO_BEGIN(e); w_sync(e); IO_END(e);
    e->w_state = W_POST;
}

/* One pre-trigger block, newest first, so a dying supply keeps the part
 * that matters. */
static void w_pre(bb_engine_t *e)
{
    if (e->pre_hi <= e->pre_lo) { pre_done(e); return; }
    uint32_t n = e->pre_hi - e->pre_lo;
    if (n > BB_BLOCK_SAMPLES) n = BB_BLOCK_SAMPLES;
    uint32_t lo = e->pre_hi - n;
    bb_sample_t *buf = (bb_sample_t *)(void *)e->scratch;
    uint64_t t0 = e->t_base_us + (uint64_t)lo * BB_TICK_US;
    if (!copy_samples(e, lo, e->pre_hi, buf)) {
        e->overruns++;
        push_simple(e, t0, BB_EV_OVERRUN, (int32_t)(e->pre_hi - e->pre_lo));
        pre_done(e);
        return;
    }
    IO_BEGIN(e);
    blk_write(e, BB_B_SAMPLES, 0, (uint16_t)n, t0, buf, n * (uint32_t)sizeof(bb_sample_t));
    IO_END(e);
    e->n_samples_written += n;
    e->pre_hi = lo;
    /* edges at or after this block's start that are not on the card yet */
    uint32_t oldest = e->e_cnt > e->e_cap ? e->e_cnt - e->e_cap : 0, new_lo = e->e_lo;
    while (new_lo > oldest && BB_EDGE_T(e->e_ring[(new_lo - 1) % e->e_cap]) >= t0) new_lo--;
    if (new_lo != e->e_lo) { uint32_t hi = e->e_lo; e->e_lo = new_lo; write_edges(e, new_lo, hi); }
    if (++e->blocks_since_sync == SYNC_FIRST || e->blocks_since_sync >= SYNC_FIRST + 16) {
        IO_BEGIN(e); w_sync(e); IO_END(e);
        if (e->pre_hi > e->pre_lo) e->blocks_since_sync = SYNC_FIRST + 1;
    }
}

static int w_post(bb_engine_t *e)
{
    uint32_t stop = e->end_n + 1;
    uint32_t avail = (e->s_cnt < stop ? e->s_cnt : stop) - e->post_lo;
    if (e->post_lo >= stop) { e->w_state = W_FINISH; return 1; }
    if (avail < BB_BLOCK_SAMPLES && e->s_cnt < stop) return 0;      /* wait for a full block */
    uint32_t n = avail > BB_BLOCK_SAMPLES ? BB_BLOCK_SAMPLES : avail;
    bb_sample_t *buf = (bb_sample_t *)(void *)e->scratch;
    uint64_t t0 = e->t_base_us + (uint64_t)e->post_lo * BB_TICK_US;
    if (copy_samples(e, e->post_lo, e->post_lo + n, buf)) {
        IO_BEGIN(e);
        blk_write(e, BB_B_SAMPLES, 0, (uint16_t)n, t0, buf, n * (uint32_t)sizeof(bb_sample_t));
        IO_END(e);
        e->n_samples_written += n;
    } else {
        e->overruns++;
    }
    e->post_lo += n;
    uint32_t hi = e->e_cnt;
    write_edges(e, e->e_fwd, hi);
    e->e_fwd = hi;
    if (++e->blocks_since_sync >= 4) { IO_BEGIN(e); w_sync(e); IO_END(e); }
    return 1;
}

static void w_finish(bb_engine_t *e)
{
    uint64_t t_end = e->t_base_us + (uint64_t)e->post_lo * BB_TICK_US;
    uint64_t t_lo = e->t_base_us + (uint64_t)e->pre_lo * BB_TICK_US;
    /* edges that came in after the last post-trigger block */
    if (e->e_fwd != e->e_cnt) { uint32_t hi = e->e_cnt; write_edges(e, e->e_fwd, hi); e->e_fwd = hi; }
    bb_event_t *evs = (bb_event_t *)(void *)e->scratch;
    uint32_t n_ev = 0;
    if (e->ev_cnt - e->ev_fwd > EV_RING) e->ev_fwd = e->ev_cnt - EV_RING;
    for (uint32_t i = e->ev_fwd; i != e->ev_cnt; i++) evs[n_ev++] = e->ev_ring[i % EV_RING];
    IO_BEGIN(e);
    if (n_ev) blk_write(e, BB_B_EVENTS, 0, (uint16_t)n_ev, evs[0].t_us, evs, n_ev * (uint32_t)sizeof(bb_event_t));
    IO_END(e);
    bb_gps_t *gps = (bb_gps_t *)(void *)e->scratch;
    uint32_t n_gps = 0, first = e->gps_cnt > GPS_RING ? e->gps_cnt - GPS_RING : 0;
    for (uint32_t i = first; i != e->gps_cnt; i++) {
        const bb_gps_t *g = &e->gps_ring[i % GPS_RING];
        if (g->t_us >= t_lo && g->t_us <= t_end) gps[n_gps++] = *g;
    }
    bb_end_t end;
    IO_BEGIN(e);
    if (n_gps) blk_write(e, BB_B_GPS, 0, (uint16_t)n_gps, gps[0].t_us, gps, n_gps * (uint32_t)sizeof(bb_gps_t));
    end.index_offset = e->file_off; end.n_blocks = e->n_index;
    end.status = BB_END_COMPLETE; end.n_samples = e->n_samples_written;
    blk_write(e, BB_B_INDEX, 0, (uint16_t)e->n_index, t_lo, e->index, e->n_index * (uint32_t)sizeof(bb_index_t));
    blk_write(e, BB_B_END, 0, 1, t_end, &end, sizeof(end));
    w_sync(e);
    e->plat.io.close(e->plat.io.ctx, e->fh);
    IO_END(e);
    e->fh = NULL;
    e->last_capture_end_us = t_end;
    e->w_state = W_IDLE;
    if (e->cfg.csv_export) {
        char path[32];
        path_event(path, sizeof(path), e->event_no, "BBX");
        IO_BEGIN(e);
        e->csv_in = e->plat.io.open(e->plat.io.ctx, path, "rb");
        path_event(path, sizeof(path), e->event_no, "CSV");
        e->csv_out = e->csv_in ? e->plat.io.open(e->plat.io.ctx, path, "wb") : NULL;
        IO_END(e);
        if (e->csv_out) { e->w_state = W_CSV; e->csv_started = 0; e->csv_last_t0 = 0; }
        else if (e->csv_in) { IO_BEGIN(e); e->plat.io.close(e->plat.io.ctx, e->csv_in); IO_END(e); e->csv_in = NULL; }
    }
}

/* CSV export: one sample block per step, in time order, read back from the
 * card and CRC-checked, so the CSV is also a read-back test of the capture. */
static void w_csv(bb_engine_t *e)
{
    char line[256];
    const bb_index_t *best = NULL;
    for (uint32_t i = 0; i < e->n_index; i++) {
        const bb_index_t *ix = &e->index[i];
        if (ix->type != BB_B_SAMPLES) continue;
        if (e->csv_started && ix->t0_us <= e->csv_last_t0) continue;
        if (!best || ix->t0_us < best->t0_us) best = ix;
    }
    IO_BEGIN(e);
    if (!e->csv_started) {
        size_t n = bb_csv_header(line, sizeof(line));
        io_write(e, e->csv_out, line, n);
    }
    int ok = 0;
    if (best) {
        bb_block_hdr_t h;
        uint32_t crc = 0;
        uint32_t len = (uint32_t)best->count * (uint32_t)sizeof(bb_sample_t);
        bb_sample_t *buf = (bb_sample_t *)(void *)e->scratch;
        ok = best->count <= BB_BLOCK_SAMPLES
             && e->plat.io.seek(e->plat.io.ctx, e->csv_in, best->offset) == 0
             && e->plat.io.read(e->plat.io.ctx, e->csv_in, &h, sizeof(h)) == (int)sizeof(h)
             && h.magic == BB_BLOCK_MAGIC && h.payload_len == len
             && e->plat.io.read(e->plat.io.ctx, e->csv_in, buf, len) == (int)len
             && e->plat.io.read(e->plat.io.ctx, e->csv_in, &crc, 4) == 4
             && crc == bb_crc32(bb_crc32(0, &h, sizeof(h)), buf, len);
        if (ok) {
            for (uint32_t i = 0; i < best->count; i++) {
                size_t n = bb_csv_row(line, sizeof(line), best->t0_us + (uint64_t)i * BB_TICK_US, &buf[i]);
                if (io_write(e, e->csv_out, line, n) != 0) { ok = 0; break; }
            }
        } else {
            e->io_errors++;
        }
    }
    IO_END(e);
    e->csv_started = 1;
    if (best && ok) { e->csv_last_t0 = best->t0_us; return; }
    w_abort_csv(e);     /* finished, or a read-back error: close both files */
}

static void summary_step(bb_engine_t *e)
{
    char line[224];
    if (!e->sd_ok) { e->sum_ready = 0; e->ev_sum = e->ev_cnt; return; }
    if (!e->sum_fh) {
        char path[32];
        snprintf(path, sizeof(path), "/SUMMARY/S%07lu.CSV", (unsigned long)(e->plat.boot_no % 10000000u));
        IO_BEGIN(e);
        e->sum_fh = e->plat.io.open(e->plat.io.ctx, path, "ab");
        if (e->sum_fh) {
            static const char head[] = "time_s,unix_s,vbat_mv,vbat_min,ecub_mv,ecub_min,pump_mv,pump_min,"
                                       "ig1_mv,ig1_min,alt_mv,alt_min,rpm_tach,rpm_ign,inj_pw_us,"
                                       "inj_duty200,dig,captures\n";
            io_write(e, e->sum_fh, head, sizeof(head) - 1);
        }
        IO_END(e);
        if (!e->sum_fh) { e->io_errors++; e->sum_ready = 0; return; }
    }
    /* events go in as comment lines: "#E,time_s,code,src,value" */
    if (e->ev_cnt - e->ev_sum > EV_RING) e->ev_sum = e->ev_cnt - EV_RING;
    while (e->ev_sum != e->ev_cnt) {
        bb_event_t ev = e->ev_ring[e->ev_sum % EV_RING];
        e->ev_sum++;
        int n = snprintf(line, sizeof(line), "#E,%lu.%03lu,%u,%u,%ld\n", (unsigned long)(ev.t_us / 1000000u),
                         (unsigned long)(ev.t_us / 1000u % 1000u), ev.code, ev.src, (long)ev.value);
        IO_BEGIN(e); io_write(e, e->sum_fh, line, (size_t)n); IO_END(e);
    }
    if (e->sum_ready) {
        memcpy(line, e->sum_line, sizeof(line));
        e->sum_ready = 0;
        IO_BEGIN(e);
        io_write(e, e->sum_fh, line, strlen(line));
        /* sync every line while no capture is being written (a power cut
         * then loses at most one line), every tenth while one is */
        if (++e->sum_lines % 10 == 0 || e->w_state == W_IDLE)
            e->plat.io.sync(e->plat.io.ctx, e->sum_fh);
        IO_END(e);
    }
}

int bb_engine_storage_step(bb_engine_t *e)
{
    /* The pre-trigger flush owns the card: after a trigger the supply may be
     * about to go, so the summary waits until those blocks are down. */
    int urgent = e->pending || e->w_state == W_OPEN || e->w_state == W_PRE;
    if (!urgent && (e->sum_ready || e->ev_sum != e->ev_cnt)) summary_step(e);
    if (e->pending && (e->w_state == W_IDLE || e->w_state == W_CSV)) {
        if (e->w_state == W_CSV) w_abort_csv(e);
        e->w_state = W_OPEN;
    }
    switch (e->w_state) {
    case W_OPEN:   w_open(e); return 1;
    case W_PRE:    w_pre(e); return 1;
    case W_POST:   return w_post(e);
    case W_FINISH: w_finish(e); return 1;
    case W_CSV:    w_csv(e); return 1;
    default:       return 0;
    }
}

/* ---- link --------------------------------------------------------------- */

static void send_hello(bb_engine_t *e)
{
    bb_hello_t h;
    memset(&h, 0, sizeof(h));
    h.proto = BB_PROTO_VERSION; h.file_ver = BB_FILE_VERSION;
    strncpy(h.fw, BB_FW_VERSION, sizeof(h.fw) - 1);
    if (e->plat.device) strncpy(h.device, e->plat.device, sizeof(h.device) - 1);
    h.sample_hz = BB_SAMPLE_HZ; h.live_hz = (uint16_t)(e->live_period_us ? 1000000u / e->live_period_us : 0);
    h.boot_no = e->plat.boot_no; h.event_count = e->event_no;
    h.n_analog = BB_N_ANALOG; h.n_edge = BB_N_EDGE;
    send(e, BB_F_HELLO, &h, sizeof(h));
}

static uint8_t state_bits(const bb_engine_t *e)
{
    return (uint8_t)((e->sd_ok ? BB_ST_SD_OK : 0)
                     | (e->w_state != W_IDLE && e->w_state != W_CSV ? BB_ST_CAPTURING : 0)
                     | (e->time_flags ? BB_ST_TIME_VALID : 0)
                     | (e->latest.dig & BB_DIG_RUNNING ? BB_ST_RUNNING : 0)
                     | (e->latest.dig & BB_DIG_IG1_ON ? BB_ST_IG1_ON : 0)
                     | (e->gps_cnt && e->gps_ring[(e->gps_cnt - 1) % GPS_RING].fix ? BB_ST_GPS_FIX : 0)
                     | (e->selftest_failed ? BB_ST_SELFTEST_FAIL : 0));
}

static void list_files(bb_engine_t *e)
{
    bb_file_info_t info;
    uint32_t last = e->event_no, first = last > 32 ? last - 31 : 1;
    int busy = e->w_state != W_IDLE && e->w_state != W_CSV;
    for (uint32_t no = last; no >= first && no > 0; no--) {
        char path[32];
        bb_file_hdr_t h;
        if (busy && no == e->event_no) continue;        /* still being written */
        path_event(path, sizeof(path), no, "BBX");
        IO_BEGIN(e);
        void *fh = e->plat.io.open(e->plat.io.ctx, path, "rb");
        int ok = 0;
        if (fh) {
            long size = e->plat.io.size(e->plat.io.ctx, fh);
            bb_block_hdr_t bh;
            ok = e->plat.io.read(e->plat.io.ctx, fh, &h, sizeof(h)) == (int)sizeof(h) && h.magic == BB_FILE_MAGIC;
            memset(&info, 0, sizeof(info));
            info.event_no = no; info.size = size > 0 ? (uint32_t)size : 0; info.reason = h.reason;
            const long tail = (long)(sizeof(bh) + sizeof(bb_end_t) + 4);
            if (ok && size >= tail && e->plat.io.seek(e->plat.io.ctx, fh, (uint32_t)(size - tail)) == 0
                && e->plat.io.read(e->plat.io.ctx, fh, &bh, sizeof(bh)) == (int)sizeof(bh))
                info.complete = bh.magic == BB_BLOCK_MAGIC && bh.type == BB_B_END;
            e->plat.io.close(e->plat.io.ctx, fh);
        }
        IO_END(e);
        if (ok) send(e, BB_F_FILE_INFO, &info, sizeof(info));
    }
    memset(&info, 0, sizeof(info));
    info.event_no = 0xFFFFFFFFu;
    send(e, BB_F_FILE_INFO, &info, sizeof(info));
}

static void read_file(bb_engine_t *e, const bb_read_file_t *rq)
{
    struct BB_PACKED { uint32_t event_no, offset; uint8_t data[BB_FILE_CHUNK]; } out;
    int n = -1;
    uint16_t want = rq->len > BB_FILE_CHUNK ? BB_FILE_CHUNK : rq->len;
    out.event_no = rq->event_no; out.offset = rq->offset;
    int busy = e->w_state != W_IDLE && e->w_state != W_CSV && rq->event_no == e->event_no;
    if (!busy) {
        IO_BEGIN(e);
        if (e->rd_fh && e->rd_no != rq->event_no) { e->plat.io.close(e->plat.io.ctx, e->rd_fh); e->rd_fh = NULL; }
        if (!e->rd_fh) {
            char path[32];
            path_event(path, sizeof(path), rq->event_no, "BBX");
            e->rd_fh = e->plat.io.open(e->plat.io.ctx, path, "rb");
            e->rd_no = rq->event_no;
        }
        if (e->rd_fh && e->plat.io.seek(e->plat.io.ctx, e->rd_fh, rq->offset) == 0)
            n = e->plat.io.read(e->plat.io.ctx, e->rd_fh, out.data, want);
        if (e->rd_fh && n < (int)want) { e->plat.io.close(e->plat.io.ctx, e->rd_fh); e->rd_fh = NULL; }
        IO_END(e);
    }
    if (n < 0) { out.offset = 0xFFFFFFFFu; n = 0; }       /* no such file / busy */
    send(e, BB_F_FILE_DATA, &out, 8u + (size_t)n);
}

static void handle(bb_engine_t *e, uint8_t type, const uint8_t *p, size_t len)
{
    const uint64_t t = now_us(e);
    char text[2700];
    switch (type) {
    case BB_F_GET_HELLO:
        send_hello(e);
        break;
    case BB_F_TIME_REQ:
        if (len >= 8) {
            bb_time_rsp_t r;
            memcpy(&r.host_tx_us, p, 8);
            r.dev_rx_us = t; r.dev_tx_us = now_us(e);
            send(e, BB_F_TIME_RSP, &r, sizeof(r));
        }
        break;
    case BB_F_TIME_SET:
        if (len >= 8 && !(e->time_flags & BB_HF_TIME_GPS)) {
            memcpy(&e->unix_us_at_t0, p, 8);
            if (!e->time_flags) push_simple(e, t, BB_EV_TIME_SYNC, 0);
            e->time_flags = BB_HF_TIME_VALID;
        }
        break;
    case BB_F_MARK: {
        int32_t id = 0;
        if (len >= 4) memcpy(&id, p, 4);
        push_simple(e, t, BB_EV_HOST_MARK, id);
        if (!e->cfg.host_mark_triggers) break;
    }   /* fall through */
    case BB_F_TRIGGER: {
        bb_event_t ev;
        memset(&ev, 0, sizeof(ev));
        ev.t_us = e->latest_t_us; ev.code = BB_R_HOST; ev.src = 0xFF; ev.rsv = BB_ACT_CAPTURE;
        push_event(e, &ev);
        if (e->s_cnt && capture_allowed(e, &ev, t)) request_capture(e, &ev, e->s_cnt - 1);
        break;
    }
    case BB_F_SELFTEST:
        if (e->plat.selftest) { IO_BEGIN(e); e->plat.selftest(e->plat.selftest_ctx); IO_END(e); }
        else bb_engine_text(e, bb_core_selftest() == 0 ? "selftest core: PASS\n" : "selftest core: FAIL\n");
        break;
    case BB_F_GET_CONFIG: {
        size_t n = bb_config_dump(&e->cfg, text, sizeof(text) - 16);
        memcpy(text + n, "# end\n", 7);
        /* whole lines per frame */
        for (char *s = text; *s; ) {
            char *end = s, *nl;
            while ((nl = strchr(end, '\n')) != NULL && (size_t)(nl + 1 - s) <= 240) end = nl + 1;
            if (end == s) end = s + strlen(s);
            send(e, BB_F_TEXT, s, (size_t)(end - s));
            s = end;
        }
        break;
    }
    case BB_F_SET_CONFIG: {
        char kv[100];
        size_t n = len < sizeof(kv) - 1 ? len : sizeof(kv) - 1;
        memcpy(kv, p, n); kv[n] = 0;
        int bad = 0;
        int rc = bb_config_parse(&e->cfg, kv, n, &bad);
        e->live_period_us = 1000000u / (uint32_t)e->cfg.live_hz;
        snprintf(text, sizeof(text), "%s %s\n", rc ? "error" : "ok", kv);
        bb_engine_text(e, text);
        break;
    }
    case BB_F_SAVE_CONFIG: {
        size_t n = bb_config_dump(&e->cfg, text, sizeof(text));
        int rc = -1;
        IO_BEGIN(e);
        void *fh = e->sd_ok ? e->plat.io.open(e->plat.io.ctx, "/BLACKBOX.CFG", "wb") : NULL;
        if (fh) {
            rc = e->plat.io.write(e->plat.io.ctx, fh, text, n);
            e->plat.io.sync(e->plat.io.ctx, fh);
            e->plat.io.close(e->plat.io.ctx, fh);
        }
        IO_END(e);
        bb_engine_text(e, rc == 0 ? "ok saved\n" : "error save failed\n");
        break;
    }
    case BB_F_LIST_FILES:
        list_files(e);
        break;
    case BB_F_READ_FILE:
        if (len >= sizeof(bb_read_file_t)) { bb_read_file_t rq; memcpy(&rq, p, sizeof(rq)); read_file(e, &rq); }
        break;
    case BB_F_LIVE_RATE:
        if (len >= 2) {
            uint16_t hz; memcpy(&hz, p, 2);
            e->live_period_us = hz ? 1000000u / (hz > 500 ? 500 : hz) : 0;
        }
        break;
    default:
        break;
    }
}

void bb_engine_rx(bb_engine_t *e, const uint8_t *data, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        uint8_t type, seq;
        const uint8_t *p;
        size_t n;
        if (bb_deframe(&e->rx, data[i], &type, &seq, &p, &n)) {
            uint8_t copy[BB_FRAME_MAX_PAYLOAD];
            memcpy(copy, p, n);          /* handlers may run while more bytes arrive */
            handle(e, type, copy, n);
        }
    }
}

void bb_engine_link_poll(bb_engine_t *e)
{
    /* events first, so the laptop sees a trigger before the next live frame */
    if (e->ev_cnt - e->ev_link > EV_RING) e->ev_link = e->ev_cnt - EV_RING;
    while (e->ev_link != e->ev_cnt) {
        bb_event_msg_t m;
        m.ev = e->ev_ring[e->ev_link % EV_RING];
        e->ev_link++;
        int started = (e->w_state != W_IDLE && e->w_state != W_CSV) || e->pending;
        m.started_file = (uint8_t)(started && m.ev.t_us == (e->pending ? e->pend_ev.t_us : e->trig_ev.t_us)
                                   && m.ev.code == (e->pending ? e->pend_ev.code : e->trig_ev.code));
        m.event_no = m.started_file ? e->event_no + (e->pending ? 1u : 0u) : 0;
        send(e, BB_F_EVENT, &m, sizeof(m));
    }
    uint64_t t = now_us(e);
    if (!e->live_period_us || !e->s_cnt || t - e->last_live_us < e->live_period_us) return;
    e->last_live_us = t;
    bb_live_t l;
    memset(&l, 0, sizeof(l));
    l.t_us = e->latest_t_us; l.now = e->latest;
    for (int i = 0; i < BB_N_ANALOG; i++) {
        l.mv_min[i] = e->live_min[i] == 0xFFFF ? e->latest.mv[i] : e->live_min[i];
        e->live_min[i] = 0xFFFF;
    }
    l.event_count = e->event_no; l.state = state_bits(e); l.last_reason = e->last_reason;
    send(e, BB_F_LIVE, &l, sizeof(l));
}

void bb_engine_status(const bb_engine_t *e, bb_status_t *st)
{
    memset(st, 0, sizeof(*st));
    st->t_us = e->latest_t_us; st->now = e->latest;
    st->event_count = e->event_no; st->samples = e->s_cnt; st->edges = e->edges_total;
    st->overruns = e->overruns; st->io_errors = e->io_errors;
    st->state = state_bits(e); st->last_reason = e->last_reason;
    st->capturing = (uint8_t)((e->w_state != W_IDLE && e->w_state != W_CSV) || e->pending);
    st->sd_ok = e->sd_ok; st->captures_this_boot = e->captures;
    st->ig1_off_ms = e->ig1_off_since_us && e->latest_t_us > e->ig1_off_since_us
                     ? (uint32_t)((e->latest_t_us - e->ig1_off_since_us) / 1000u) : 0;
    st->unix_us_at_t0 = e->unix_us_at_t0;
}

int bb_core_selftest(void)
{
    static const uint8_t msg[] = "123456789";
    uint8_t enc[16], dec[16];
    const uint8_t z[] = {0x11, 0x00, 0x00, 0x22};
    if (bb_crc32(0, msg, 9) != 0xCBF43926u) return 1;
    if (bb_crc16(msg, 9) != 0x29B1) return 2;
    size_t n = bb_cobs_encode(z, 4, enc);
    if (n != 5 || memchr(enc, 0, n) || bb_cobs_decode(enc, n, dec) != 4 || memcmp(dec, z, 4)) return 3;
    if (sizeof(bb_sample_t) != 28 || sizeof(bb_file_hdr_t) != 84 || sizeof(bb_block_hdr_t) != 24
        || sizeof(bb_event_t) != 16 || sizeof(bb_live_t) != 54 || sizeof(bb_gps_t) != 28) return 4;
    if (bb_extend32(0x1FFFFFFF0ull, 0x00000005u) != 0x200000005ull) return 5;
    return 0;
}
