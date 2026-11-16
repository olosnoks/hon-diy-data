/* hhcore: the engine. hondalog/poller.py (the link state machine, one block
 * per step) and hondalog/logger.py (drives, the stall capture) in one place,
 * plus the snapshot the display reads.
 *
 * Threads. hh_core_step() runs on the link task and is the only caller of
 * the transport. Everything it shares with the other tasks (the snapshot,
 * the log blocks, the queue to the card, the alarm states) is touched under
 * the platform lock, which is never held across a K-line exchange or a file
 * operation.
 *
 * Recovery, as on the laptop:
 *   ECU stops answering   NO_REPLY: probe with one short read and a growing
 *                         pause, wake-up bytes now and then; carries on by
 *                         itself when the ECU is back.
 *   cable pulled          PORT_LOST: reopen every 0.25-2 s, then probe.
 *   Honda reads never     after iso_after_s: ISO 9141-2 5-baud init; if that
 *   answered              answers, live PIDs and stored codes (ISO_ONLY).
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "hhcore.h"

enum { ST_CONNECTING, ST_OK, ST_NO_REPLY, ST_PORT_LOST, ST_ISO };

#define FAILS_TO_NO_REPLY 3
#define PROBE_MIN_S   0.05
#define PROBE_MAX_S   0.25
#define REOPEN_MIN_S  0.25
#define REOPEN_MAX_S  2.0
#define REPLAN_EVERY  256
#define SWITCH_BACK_S 8.0       /* grace for the ECU to drop an ISO session */
#define ISO_RETRY_S   30.0
#define ISO_DTC_EVERY_S 60.0

/* queue items: one byte of kind, then the bytes for the file */
enum { Q_DRIVE_OPEN = 1, Q_DRIVE_DATA, Q_DRIVE_CLOSE, Q_CAP_OPEN, Q_CAP_DATA, Q_CAP_CLOSE, Q_SYNC };
#define Q_ITEM_MAX (HH_BLOCK_PAYLOAD + 80 + 28 + 1)

/* ISO PIDs polled in ISO-only mode, and the map channel each one fills */
static const struct { uint8_t pid; const char *name; } ISO_PIDS[] = {
    {0x0C, "rpm"}, {0x0D, "vss"}, {0x0B, "map"}, {0x11, "tps"}, {0x0E, "ign"}, {0x14, "o2"},
    {0x06, "stft"}, {0x07, "ltft"}, {0x05, "ect"}, {0x0F, "iat"},
};
#define N_ISO_PIDS ((int)(sizeof(ISO_PIDS) / sizeof(ISO_PIDS[0])))

struct hh_core {
    hh_platform_t plat;
    const hh_map_t *map;
    hh_config_t cfg;
    hh_link_t link;
    hh_iso_t iso;
    hh_cost_t cost;
    hh_plan_t plan;
    hh_sched_t sched;
    hh_alarms_t alarms;
    hh_stall_t stall;
    hh_ring_t ring, queue;

    hh_channel_info_t info[HH_MAX_CH];
    hh_map_chan_t ch[HH_MAX_CH];
    uint16_t n_ch;
    hh_plan_ch_t pch[HH_MAX_CH];        /* the measured channels, as the planner sees them */
    uint8_t pch_ch[HH_MAX_CH];          /* planner index -> channel index */
    int n_pch;
    int16_t rpm_ch;
    float last_rpm;                     /* the reading before a stall fired */

    /* link task */
    uint8_t state, started, iso_mode;
    uint8_t unread[HH_MAX_BLOCKS], pending[HH_MAX_BLOCKS];
    int fails, probes, iso_fails;
    double probe_wait, reopen_wait, quiet_until, honda_last_ok, next_iso_try, next_dtc;
    double last_emit;                   /* < 0 = none */
    uint16_t tick_latency_us;           /* age of the read that closes the sample being emitted */
    uint32_t reads, errors, next_replan;
    uint8_t ecu_id[10], ecu_id_len;
    uint8_t iso_pid[N_ISO_PIDS]; int16_t iso_ch[N_ISO_PIDS]; int n_iso, iso_i;

    /* shared (under the lock) */
    float value[HH_MAX_CH];
    uint8_t fresh[HH_MAX_CH];           /* read since the last sample */
    double down_since;                  /* < 0 = the ECU is answering */
    uint8_t req_dtc, dtc_busy;
    hh_nmea_t nmea;
    hh_gps_t gps;
    double gps_at;                      /* clock time of the last RMC, < 0 = never */
    uint8_t time_valid;
    hh_pps_t pps;                       /* the pulse clock (hh_pps.c) */
    uint8_t ts_seen;                    /* the time source as of the last look: HH_TS_* */
    int64_t ts_log_at;                  /* device us: write the next TIME record for PPS then */
    uint32_t markers, dropped, dropped_logged;
    hh_snapshot_t snap;

    /* files, as queued (under the lock) */
    uint8_t drive_open, cap_open, drive_hold;
    uint32_t drive_no, cap_no, nonce, cap_nonce, seq, cap_seq, n_reads, queued_bytes;
    hh_blk_t blk_reads, blk_iso, blk_gps;

    /* storage task */
    void *fh_drive, *fh_cap;
    uint32_t fh_drive_no, fh_cap_no;    /* the files those are */
    uint32_t written, cap_written, sd_fail;
    double last_sync;
    uint8_t dirty, sd_ok, sd_err;
    float free_mb;
};

static double now(hh_core_t *c) { return c->plat.clk.now(c->plat.clk.ctx); }
static void nap(hh_core_t *c, double s) { if (s > 0) c->plat.clk.sleep(c->plat.clk.ctx, s); }
static void lock(hh_core_t *c) { if (c->plat.lock) c->plat.lock(c->plat.lock_ctx); }
static void unlock(hh_core_t *c) { if (c->plat.unlock) c->plat.unlock(c->plat.lock_ctx); }
static uint64_t us(double t) { return t <= 0 ? 0 : (uint64_t)(t * 1e6 + 0.5); }
static void tap(hh_core_t *c, uint8_t kind, const void *rec, size_t len, const void *data, size_t data_len)
{
    if (c->plat.tap) c->plat.tap(c->plat.tap_ctx, kind, rec, len, data, data_len);
}

size_t hh_core_size(void) { return sizeof(hh_core_t); }
const hh_link_stats_t *hh_core_link_stats(const hh_core_t *c) { return &c->link.stats; }
const hh_plan_t *hh_core_plan(const hh_core_t *c) { return &c->plan; }
const hh_channel_info_t *hh_core_channels(const hh_core_t *c, uint16_t *n) { if (n) *n = c->n_ch; return c->info; }

/* ---- the queue to the card (lock held) --------------------------------------- */

static void q_push(hh_core_t *c, uint8_t kind, const uint8_t *data, size_t len)
{
    uint8_t item[Q_ITEM_MAX];
    if (!c->queue.buf || len + 1 > sizeof(item) || c->queue.cap - c->queue.used < len + 3) {
        c->dropped++;           /* the card is too slow or gone: the reader sees a gap in seq */
        return;
    }
    item[0] = kind;
    if (len) memcpy(item + 1, data, len);
    hh_ring_push(&c->queue, item, (uint16_t)(len + 1));
}

/* One block into the drive file, and into the capture file while one is open. */
static void put_block(hh_core_t *c, uint8_t type, uint16_t count, uint64_t t0_us, const void *payload, uint32_t len)
{
    uint8_t out[Q_ITEM_MAX];
    if (c->drive_open) {
        size_t n = hh_block_encode(c->nonce, type, count, c->seq++, t0_us, payload, len, out);
        q_push(c, Q_DRIVE_DATA, out, n);
        c->queued_bytes += (uint32_t)n;
    }
    if (c->cap_open) {
        size_t n = hh_block_encode(c->cap_nonce, type, count, c->cap_seq++, t0_us, payload, len, out);
        q_push(c, Q_CAP_DATA, out, n);
    }
}

static void blk_flush(hh_core_t *c, hh_blk_t *b)
{
    if (b->count) put_block(c, b->type, b->count, b->t0_us, b->payload, b->len);
    b->count = 0; b->len = 0;
}

static uint8_t *blk_room(hh_core_t *c, hh_blk_t *b, uint64_t t_us, size_t need, uint32_t *dt)
{
    if (b->count && (b->len + need > HH_BLOCK_PAYLOAD || t_us < b->t0_us || t_us - b->t0_us > 0xFFFFFFFFu))
        blk_flush(c, b);
    if (!b->count) { b->t0_us = t_us; b->opened_at = now(c); }
    if (dt) *dt = (uint32_t)(t_us - b->t0_us);
    uint8_t *at = b->payload + b->len;
    b->len += (uint32_t)need;
    b->count++;
    return at;
}

static void flush_all(hh_core_t *c)
{
    blk_flush(c, &c->blk_reads);
    blk_flush(c, &c->blk_iso);
    blk_flush(c, &c->blk_gps);
}

static void log_event(hh_core_t *c, double t, uint8_t code, uint8_t src, uint16_t aux, int32_t iv, float fv, int is_float)
{
    hh_rec_event_t e;
    memset(&e, 0, sizeof(e));
    e.t_us = us(t); e.code = code; e.src = src; e.aux = aux;
    if (is_float) e.v.f = fv; else e.v.i = iv;
    tap(c, HH_TAP_EVENT, &e, sizeof(e), NULL, 0);
    if (!c->drive_open && !c->cap_open) return;
    flush_all(c);                       /* keep what led up to it in front of it */
    put_block(c, HH_B_EVENTS, 1, e.t_us, &e, sizeof(e));
    q_push(c, Q_SYNC, NULL, 0);         /* events are rare and are what the drive is kept for */
}

static void log_time_rec(hh_core_t *c, uint64_t t_us, int64_t unix_us, uint8_t source, uint32_t err_us, int32_t drift_ppb)
{
    hh_rec_time_t r;
    if (!c->drive_open && !c->cap_open) return;
    memset(&r, 0, sizeof(r));
    r.t_us = t_us; r.unix_us = unix_us; r.source = source;
    r.err_us = (uint16_t)(err_us > 65535 ? 65535 : err_us);
    r.drift_ppb = drift_ppb;
    put_block(c, HH_B_TIME, 1, r.t_us, &r, sizeof(r));
}

static void log_time(hh_core_t *c, double t, int64_t unix_us, uint8_t source)
{
    log_time_rec(c, us(t), unix_us, source, 0, 0);
}

/* ---- the clock: pulse, sentences, or nothing (lock held) ---------------------- */

static int64_t sys_time(hh_core_t *c) { return c->plat.get_time ? c->plat.get_time(c->plat.time_ctx) : 0; }

static uint8_t time_source(hh_core_t *c, int64_t t_us)
{
    if (hh_pps_active(&c->pps, t_us)) return HH_TS_PPS;
    if (c->time_valid) return HH_TS_NMEA;
    return sys_time(c) > 0 ? HH_TS_HOST : HH_TS_NONE;
}

/* The wall clock at device time t_us (which is now): the pulse model while it holds, else the system clock. */
static int64_t wall_us(hh_core_t *c, int64_t t_us)
{
    return hh_pps_active(&c->pps, t_us) ? hh_pps_unix_us(&c->pps, t_us) : sys_time(c);
}

static void log_time_pps(hh_core_t *c, int64_t t_us)
{
    log_time_rec(c, (uint64_t)t_us, hh_pps_unix_us(&c->pps, t_us), HH_TIME_PPS, hh_pps_err_us(&c->pps, t_us),
                 (int32_t)llround(c->pps.drift * 1e9));
}

/* Look at the source. A TIME record when it becomes PPS, once a minute while it is (so a reader has a fresh
 * anchor against the crystal's drift), and when it falls back to the sentences' time. */
static void time_track(hh_core_t *c, int64_t t_us)
{
    uint8_t src = time_source(c, t_us), was = c->ts_seen;
    c->ts_seen = src;
    if (src == HH_TS_PPS) {
        if (was != src || t_us >= c->ts_log_at) {
            log_time_pps(c, t_us);
            c->ts_log_at = t_us + 60000000;
        }
    } else if (was == HH_TS_PPS) {
        log_time_rec(c, (uint64_t)t_us, sys_time(c), src == HH_TS_NMEA ? HH_TIME_GPS : HH_TIME_CLOCK, 0, 0);
    }
}

/* A pulse and the time of the second it started: feed the filter, and put the system clock right once it holds. */
static void pps_pair(hh_core_t *c, int64_t t_rx_us, int64_t utc_s)
{
    int r = hh_pps_time(&c->pps, t_rx_us, utc_s);
    int64_t tn = us(now(c));
    if ((r == HH_PPS_PAIR || r == HH_PPS_RELOCK) && c->pps.locked) {
        int64_t model = hh_pps_unix_us(&c->pps, tn), have = sys_time(c), d = have - model;
        if (c->plat.set_time && (!have || d > 2000 || d < -2000)) c->plat.set_time(c->plat.time_ctx, model);
        c->time_valid = 1;
    }
    time_track(c, tn);
}

/* A read: always into the pre-trigger ring, and into the drive if one is open. */
static void log_read(hh_core_t *c, double t_sampled, uint8_t addr, const uint8_t *data, uint8_t len, double latency)
{
    struct HH_PACKED { uint64_t t_us; hh_rec_read_t r; uint8_t data[HH_MAX_READ]; } item;
    uint64_t t_us = us(t_sampled);
    double lat_us = latency * 1e6;
    item.t_us = t_us;
    item.r.dt_us = 0; item.r.addr = addr; item.r.len = len;
    item.r.latency_us = (uint16_t)(lat_us > 65535 ? 65535 : lat_us);
    memcpy(item.data, data, len);
    if (c->plat.tap) {
        hh_tap_read_t tr = {t_us, addr, len, item.r.latency_us};
        tap(c, HH_TAP_READ, &tr, sizeof(tr), data, len);
    }
    if (c->ring.buf) {
        /* keep pre_s seconds: drop what is older */
        uint64_t keep = us(c->stall.pre_s + 1.0);
        for (;;) {
            uint32_t pos = c->ring.tail;
            uint64_t oldest;
            uint8_t tmp[sizeof(item)];
            if (!hh_ring_peek(&c->ring, &pos, tmp, sizeof(tmp))) break;
            memcpy(&oldest, tmp, 8);
            if (oldest + keep >= t_us) break;
            hh_ring_pop(&c->ring);
        }
        hh_ring_push(&c->ring, &item, (uint16_t)(8 + sizeof(hh_rec_read_t) + len));
    }
    if (c->drive_open || c->cap_open) {
        uint32_t dt;
        uint8_t *at = blk_room(c, &c->blk_reads, t_us, sizeof(hh_rec_read_t) + len, &dt);
        item.r.dt_us = dt;
        memcpy(at, &item.r, sizeof(hh_rec_read_t));
        memcpy(at + sizeof(hh_rec_read_t), data, len);
        c->n_reads++;
    }
}

static void log_iso(hh_core_t *c, double t, uint8_t mode, uint8_t pid, const uint8_t *data, uint8_t len)
{
    hh_rec_iso_t r;
    uint32_t dt;
    if (c->plat.tap) {
        hh_tap_iso_t ti = {us(t), mode, pid, len, 0};
        tap(c, HH_TAP_ISO, &ti, sizeof(ti), data, len);
    }
    if (!c->drive_open && !c->cap_open) return;
    uint8_t *at = blk_room(c, &c->blk_iso, us(t), sizeof(r) + len, &dt);
    r.dt_us = dt; r.mode = mode; r.pid = pid; r.len = len; r.flags = 0;
    memcpy(at, &r, sizeof(r));
    memcpy(at + sizeof(r), data, len);
    c->n_reads++;
}

static uint32_t make_nonce(hh_core_t *c, uint32_t no, double t)
{
    uint32_t parts[4] = {c->plat.nonce_seed, c->plat.boot_no, no, (uint32_t)us(t)};
    uint32_t n = hh_crc32(0x48484446u, parts, sizeof(parts));
    return n ? n : 1;
}

static void fill_header(hh_core_t *c, hh_file_hdr_t *h, uint8_t kind, uint32_t no, uint32_t nonce, double t)
{
    memset(h, 0, sizeof(*h));
    h->file_no = no; h->boot_no = c->plat.boot_no;
    h->kind = kind;
    h->backend = c->plat.transport->backend;
    h->link_mode = c->iso_mode;
    h->nonce = nonce;
    h->t0_us = us(t);
    int64_t wall = wall_us(c, (int64_t)us(now(c)));
    if (wall > 0) {
        h->unix_us_at_t0 = wall - (int64_t)(us(now(c)) - h->t0_us);
        h->flags |= HH_HF_TIME_VALID | (c->time_valid ? HH_HF_TIME_GPS : 0);
    }
    if (c->plat.builtin_map) h->flags |= HH_HF_BUILTIN_MAP;
    memcpy(h->map_hash, c->map->hdr.map_hash, sizeof(h->map_hash));
    memcpy(h->map_sha, c->map->hdr.map_sha256, sizeof(h->map_sha));
    strncpy(h->fw, HH_FW_VERSION, sizeof(h->fw) - 1);
    if (c->plat.device) strncpy(h->device, c->plat.device, sizeof(h->device) - 1);
    memcpy(h->ecu_id, c->ecu_id, sizeof(h->ecu_id));
    h->ecu_id_len = c->ecu_id_len;
}

/* The text blocks every file starts with: settings and the alarm names. */
static void put_preamble(hh_core_t *c, double t)
{
    char text[HH_BLOCK_PAYLOAD];
    size_t n = hh_config_dump(&c->cfg, text, sizeof(text));
    put_block(c, HH_B_CONFIG, 1, us(t), text, (uint32_t)n);
    n = 0;
    for (int i = 0; i < c->alarms.n; i++) {
        hh_map_alarm_t a;
        hh_map_alarm(c->map, i, &a);
        int w = snprintf(text + n, sizeof(text) - n, "A %d %s\n", i, a.name);
        if (w < 0 || (size_t)w >= sizeof(text) - n) break;
        n += (size_t)w;
    }
    if (n) put_block(c, HH_B_NAMES, (uint16_t)c->alarms.n, us(t), text, (uint32_t)n);
    int64_t tn = (int64_t)us(now(c)), wall = wall_us(c, tn);
    if (hh_pps_active(&c->pps, tn)) { log_time_pps(c, tn); c->ts_log_at = tn + 60000000; }
    else if (wall > 0) log_time(c, now(c), wall, c->time_valid ? HH_TIME_GPS : HH_TIME_CLOCK);
}

static void drive_begin(hh_core_t *c, double t)
{
    hh_file_hdr_t h;
    if (c->drive_open) return;
    c->drive_no = ++c->plat.last_drive_no;
    c->nonce = make_nonce(c, c->drive_no, t);
    c->seq = 0; c->n_reads = 0; c->queued_bytes = sizeof(h);
    fill_header(c, &h, HH_FK_DRIVE, c->drive_no, c->nonce, t);
    h.prealloc = (uint32_t)c->cfg.prealloc_mb << 20;
    if (h.prealloc && c->plat.io.preallocate) h.flags |= HH_HF_PREALLOC; else h.prealloc = 0;
    hh_file_hdr_seal(&h);
    q_push(c, Q_DRIVE_OPEN, (const uint8_t *)&h, sizeof(h));
    c->drive_open = 1;
    put_preamble(c, t);
    log_event(c, t, HH_EV_DRIVE_START, c->plat.transport->backend, c->iso_mode, (int32_t)c->drive_no, 0, 0);
}

static void put_capture_record(hh_core_t *c, int complete)
{
    hh_rec_capture_t r;
    memset(&r, 0, sizeof(r));
    r.t_trigger_us = us(c->stall.t_trigger);
    r.t_start_us = us(c->stall.t_start);
    r.t_end_us = us(complete ? c->stall.t_end : now(c));
    r.reason = c->stall.last_reason; r.complete = (uint8_t)complete;
    r.n_fired = c->stall.fired_in_window;
    flush_all(c);
    put_block(c, HH_B_CAPTURE, 1, r.t_trigger_us, &r, sizeof(r));
}

static void put_end(hh_core_t *c, uint32_t nonce, uint32_t *seq, uint8_t kind, uint32_t reason, uint32_t n_reads)
{
    uint8_t out[sizeof(hh_block_hdr_t) + sizeof(hh_rec_end_t) + 4];
    hh_rec_end_t e;
    e.n_blocks = *seq; e.n_reads = n_reads; e.status = 1; e.reason = reason; e.t_end_us = us(now(c));
    size_t n = hh_block_encode(nonce, HH_B_END, 1, (*seq)++, e.t_end_us, &e, sizeof(e), out);
    q_push(c, kind, out, n);
}

static void capture_finish(hh_core_t *c, int complete)
{
    put_capture_record(c, complete);
    if (c->cap_open) {
        put_end(c, c->cap_nonce, &c->cap_seq, Q_CAP_DATA, HH_END_STOPPED, 0);
        q_push(c, Q_CAP_CLOSE, NULL, 0);
        c->cap_open = 0;
    }
    q_push(c, Q_SYNC, NULL, 0);
}

static void drive_end(hh_core_t *c, uint32_t reason)
{
    if (c->stall.capturing) { c->stall.capturing = 0; capture_finish(c, 0); }
    if (!c->drive_open) return;
    flush_all(c);
    put_end(c, c->nonce, &c->seq, Q_DRIVE_DATA, reason, c->n_reads);
    q_push(c, Q_DRIVE_CLOSE, NULL, 0);
    c->drive_open = 0;
}

/* A stall rule fired: open the capture file and put the ring into it. */
static void capture_begin(hh_core_t *c, double t, int reason, float rpm_before)
{
    snprintf(c->snap.stall.last_reason, sizeof(c->snap.stall.last_reason), "%s",
             reason == HH_STALL_RPM ? "rpm dropped to 0" : reason == HH_STALL_LINK ? "link lost (no reply from ECU)"
             : "alarm");
    log_event(c, t, HH_EV_STALL, (uint8_t)reason, 0, 0, rpm_before, 1);
    if (!c->cfg.capture_files || c->cap_open || c->stall.fired_in_window > 1) return;
    hh_file_hdr_t h;
    c->cap_no = ++c->plat.last_capture_no;
    c->cap_nonce = make_nonce(c, 0x80000000u | c->cap_no, t);
    c->cap_seq = 0;
    fill_header(c, &h, HH_FK_CAPTURE, c->cap_no, c->cap_nonce, c->stall.t_start > 0 ? c->stall.t_start : 0.0);
    h.drive_no = c->drive_open ? c->drive_no : 0;
    h.reason = (uint8_t)reason;
    h.trigger_t_us = us(t);
    hh_file_hdr_seal(&h);
    q_push(c, Q_CAP_OPEN, (const uint8_t *)&h, sizeof(h));
    /* the last pre_s seconds, oldest first, as READS blocks of the capture file only */
    hh_blk_t *b = &c->blk_gps;          /* borrowed: flushed first, so it is empty */
    uint8_t was_open = c->drive_open;
    flush_all(c);
    c->drive_open = 0; c->cap_open = 1;
    b->type = HH_B_READS;
    uint32_t pos = c->ring.tail;
    uint8_t tmp[8 + sizeof(hh_rec_read_t) + HH_MAX_READ];
    uint16_t n;
    while ((n = hh_ring_peek(&c->ring, &pos, tmp, sizeof(tmp))) != 0) {
        uint64_t t_us;
        uint32_t dt;
        memcpy(&t_us, tmp, 8);
        uint8_t *at = blk_room(c, b, t_us, (size_t)n - 8, &dt);
        memcpy(tmp + 8, &dt, 4);        /* hh_rec_read_t.dt_us */
        memcpy(at, tmp + 8, (size_t)n - 8);
    }
    blk_flush(c, b);
    b->type = HH_B_GPS;
    c->drive_open = was_open;
    put_preamble(c, t);                 /* goes to both files; harmless twice in the drive */
    q_push(c, Q_SYNC, NULL, 0);
}

/* ---- snapshot pieces (lock held) ---------------------------------------------- */

static void set_status(hh_core_t *c, const char *text)
{
    snprintf(c->snap.status, sizeof(c->snap.status), "%s", text);
}

static void publish_state(hh_core_t *c)
{
    static const uint8_t map[] = {HH_LINK_CONNECTING, HH_LINK_OK, HH_LINK_OFF, HH_LINK_ERROR, HH_LINK_ISO_ONLY};
    c->snap.link.state = map[c->state];
    c->snap.link.backend = c->plat.transport->backend;
    c->snap.link.reads = c->reads;
    c->snap.link.errors = c->errors;
    switch (c->state) {
    case ST_OK: set_status(c, "ECU ok"); break;
    case ST_ISO: set_status(c, "OBD2 only (ISO 9141-2)"); break;
    case ST_NO_REPLY: set_status(c, "no reply from the ECU"); break;
    case ST_PORT_LOST:
        set_status(c, c->plat.transport->backend == HH_BACKEND_USB ? "USB cable not found" : "K-line port failed");
        break;
    default: set_status(c, "connecting"); break;
    }
    c->snap.seq++;
    if (c->plat.tap) {
        hh_tap_link_t tl = {us(now(c)), c->snap.link.state, (uint8_t)(c->down_since < 0), c->snap.link.backend, c->iso_mode};
        tap(c, HH_TAP_LINK, &tl, sizeof(tl), NULL, 0);
    }
}

static void values_unknown(hh_core_t *c)
{
    for (int i = 0; i < c->n_ch; i++) { c->value[i] = NAN; c->fresh[i] = 0; c->snap.value[i] = NAN; c->snap.fresh[i] = 0; }
}

static void stall_feed(hh_core_t *c, double t, double rpm, int link)
{
    float before = c->last_rpm;
    if (c->rpm_ch < 0) return;
    if (!isnan(rpm)) c->last_rpm = (float)rpm;
    int fired = hh_stall_feed(&c->stall, t, rpm, link);
    if (fired) capture_begin(c, t, fired, before);
    c->snap.stall.armed = c->stall.armed;
    c->snap.stall.triggered = c->stall.capturing;
    c->snap.stall.count = c->stall.count;
}

/* One sample: math channels, alarms, the snapshot. */
static void emit(hh_core_t *c, double t)
{
    hh_alarm_event_t ev[8];
    hh_expr_env_t env = {c->map->consts, c->map->n_const, c->value, c->n_ch, NULL, 0, NAN, t};
    for (int i = 0; i < c->n_ch; i++) {
        if (c->ch[i].kind != HH_KIND_MATH) continue;
        c->value[i] = (float)hh_expr_eval(c->map->prog + c->ch[i].prog_off, c->ch[i].prog_len, &env);
        c->fresh[i] = !isnan(c->value[i]);
    }
    int n = hh_alarms_update(&c->alarms, c->value, c->n_ch, t, ev, 8);
    for (int k = 0; k < n; k++) {
        hh_map_alarm_t a;
        hh_map_alarm(c->map, ev[k].index, &a);
        uint8_t code = ev[k].kind == HH_ALEV_RAISED ? HH_EV_ALARM_RAISED : HH_EV_ALARM_CLEARED;
        log_event(c, ev[k].t, code, ev[k].index, ev[k].severity, 0, ev[k].value, 1);
        if (ev[k].kind == HH_ALEV_RAISED && (a.flags & HH_AF_CAPTURE)) {
            hh_stall_fire(&c->stall, t, HH_STALL_ALARM);
            capture_begin(c, t, HH_STALL_ALARM, ev[k].value);
        }
    }
    memcpy(c->snap.value, c->value, sizeof(float) * c->n_ch);
    memcpy(c->snap.fresh, c->fresh, c->n_ch);
    memset(c->fresh, 0, c->n_ch);
    hh_alarms_levels(&c->alarms, c->snap.alarm, c->n_ch);
    int top = hh_alarms_top(&c->alarms);
    c->snap.top_alarm_ch = -1; c->snap.top_alarm_level = 0; c->snap.top_alarm_text[0] = 0; c->snap.top_alarm_acked = 0;
    if (top >= 0) {
        hh_map_alarm_t a;
        hh_map_alarm(c->map, top, &a);
        c->snap.top_alarm_ch = a.channel;
        c->snap.top_alarm_level = a.severity >= 2 ? 2 : 1;
        c->snap.top_alarm_acked = c->alarms.st[top].acknowledged;
        snprintf(c->snap.top_alarm_text, sizeof(c->snap.top_alarm_text), "%s", a.label);
    }
    if (c->last_emit >= 0 && t > c->last_emit) {
        float inst = (float)(1.0 / (t - c->last_emit));
        c->snap.link.hz_fast = c->snap.link.hz_fast == 0 ? inst : 0.9f * c->snap.link.hz_fast + 0.1f * inst;
    }
    c->last_emit = t;
    c->snap.t_us = (int64_t)us(t);
    c->snap.link.reads = c->reads;
    c->snap.link.errors = c->errors;
    c->snap.seq++;
    if (c->plat.tap) {
        float hz = c->snap.link.hz_fast > 650 ? 650 : c->snap.link.hz_fast;
        hh_tap_tick_t tt = {us(t), c->tick_latency_us, (uint16_t)(hz * 100.0f + 0.5f), c->snap.seq};
        tap(c, HH_TAP_TICK, &tt, sizeof(tt), NULL, 0);
    }
}

/* The ECU is not answering right now. Called on every failed probe, so the
 * link-loss rule sees time pass. */
static void emit_down(hh_core_t *c)
{
    double t = now(c);
    lock(c);
    c->snap.link.hz_fast = 0;
    c->last_emit = -1;
    if (c->down_since < 0) {
        c->down_since = t;
        log_event(c, t, HH_EV_LINK_DOWN, 0, 0, 0, 0, 0);
    }
    values_unknown(c);
    stall_feed(c, t, NAN, 0);
    publish_state(c);
    unlock(c);
}

/* ---- plan ------------------------------------------------------------------------ */

static void build_plan(hh_core_t *c)
{
    hh_plan_build(&c->plan, c->pch, c->n_pch, c->map->framing.max_read_len, &c->cost);
    hh_sched_init(&c->sched, &c->plan);
    for (int i = 0; i < c->plan.n_blocks; i++) { c->unread[i] = 1; c->pending[i] = c->sched.top[i]; }
}

static int none_set(const uint8_t *flags, int n)
{
    for (int i = 0; i < n; i++) if (flags[i]) return 0;
    return 1;
}

/* Re-plan if the measured per-request overhead is not what was assumed. */
static void maybe_replan(hh_core_t *c)
{
    hh_cost_t measured;
    hh_plan_t trial;
    if (!c->link.tm.have_srtt) return;
    hh_cost_from_framing(&measured, &c->map->framing, c->link.tm.srtt, c->link.tm.gap);
    double diff = hh_cost_overhead(&measured) - hh_cost_overhead(&c->cost);
    if ((diff < 0 ? -diff : diff) < 0.2 * hh_cost_overhead(&c->cost)) return;
    c->cost = measured;
    if (hh_plan_build(&trial, c->pch, c->n_pch, c->map->framing.max_read_len, &measured) != HH_OK) return;
    int same = trial.n_blocks == c->plan.n_blocks;
    for (int i = 0; same && i < trial.n_blocks; i++)
        same = trial.blocks[i].addr == c->plan.blocks[i].addr && trial.blocks[i].length == c->plan.blocks[i].length;
    if (!same) build_plan(c);
}

/* ---- link states ------------------------------------------------------------------ */

static void link_down(hh_core_t *c)
{
    c->state = ST_NO_REPLY;
    c->fails = 0;
    c->probes = 0;
    c->probe_wait = PROBE_MIN_S;
}

static void port_lost(hh_core_t *c)
{
    c->errors++;
    if (c->iso.up) c->iso.up = 0;
    link_down(c);
    c->state = ST_PORT_LOST;
    c->reopen_wait = REOPEN_MIN_S;
    c->plat.transport->close(c->plat.transport->ctx);
    lock(c);
    log_event(c, now(c), HH_EV_PORT_LOST, 0, 0, 0, 0, 0);
    unlock(c);
    emit_down(c);
}

static int read_ecu_id(hh_core_t *c)
{
    hh_read_t r;
    int addr = c->map->info.ecu_id_addr, len = c->map->info.ecu_id_len;
    if (addr < 0 || len < 1) return HH_OK;
    if (len > (int)sizeof(c->ecu_id)) len = sizeof(c->ecu_id);
    for (int k = 0; k < 2; k++) {
        int rc = hh_link_read_block(&c->link, (uint8_t)addr, (uint8_t)len, 0, &r);
        if (rc == HH_E_PORT) return rc;
        if (rc == HH_OK) {
            c->reads++;
            lock(c);
            memcpy(c->ecu_id, r.data, (size_t)len);
            c->ecu_id_len = (uint8_t)len;
            for (int i = 0; i < len && i < (int)sizeof(c->snap.link.ecu_id) - 1; i++)
                c->snap.link.ecu_id[i] = r.data[i] >= 32 && r.data[i] < 127 ? (char)r.data[i] : '.';
            unlock(c);
            return 1;
        }
        c->errors++;
    }
    return HH_OK;
}

/* The ECU answers (again): a drive starts if none is open. */
static void went_up(hh_core_t *c, uint8_t iso)
{
    double t = now(c);
    c->state = iso ? ST_ISO : ST_OK;
    c->fails = 0;
    c->quiet_until = 0;
    lock(c);
    if (c->drive_open && c->iso_mode != iso) drive_end(c, HH_END_STOPPED);  /* another protocol: another drive */
    c->iso_mode = iso;
    c->down_since = -1;
    if (!c->drive_hold) drive_begin(c, t);
    log_event(c, t, HH_EV_LINK_UP, c->plat.transport->backend, iso, 0, 0, 0);
    stall_feed(c, t, NAN, 1);
    publish_state(c);
    unlock(c);
}

static void link_up(hh_core_t *c)
{
    hh_timing_relax(&c->link.tm);
    hh_sched_restart(&c->sched);
    for (int i = 0; i < c->plan.n_blocks; i++) { c->unread[i] = 1; c->pending[i] = c->sched.top[i]; }
    c->honda_last_ok = now(c);
    if (!c->ecu_id_len && read_ecu_id(c) == HH_E_PORT) { port_lost(c); return; }
    went_up(c, 0);
}

static void connect(hh_core_t *c)
{
    hh_transport_t *tr = c->plat.transport;
    c->honda_last_ok = now(c);
    lock(c); publish_state(c); unlock(c);
    if (tr->open(tr->ctx) < 0) { port_lost(c); return; }
    hh_link_reset(&c->link);
    if (hh_link_wake(&c->link) == HH_E_PORT) { port_lost(c); return; }
    int rc = read_ecu_id(c);
    if (rc == HH_E_PORT) { port_lost(c); return; }
    if (rc == 1) { link_up(c); return; }
    emit_down(c);
    c->state = ST_NO_REPLY;
    lock(c); publish_state(c); unlock(c);
}

static void reopen(hh_core_t *c)
{
    hh_transport_t *tr = c->plat.transport;
    if (tr->open(tr->ctx) < 0) {
        c->reopen_wait = c->reopen_wait * 1.5 < REOPEN_MAX_S ? c->reopen_wait * 1.5 : REOPEN_MAX_S;
        return;
    }
    c->link.stats.reconnects++;
    hh_link_reset(&c->link);
    c->state = ST_NO_REPLY;
    c->probes = 3;                      /* wake on the very next probe */
    c->honda_last_ok = now(c);
    lock(c);
    log_event(c, now(c), HH_EV_PORT_BACK, 0, 0, 0, 0, 0);
    publish_state(c);
    unlock(c);
}

/* ---- ISO 9141-2 --------------------------------------------------------------------- */

static void read_dtcs(hh_core_t *c)
{
    uint16_t codes[6];
    int n = hh_iso_read_dtcs(&c->iso, codes, 6);
    double t = now(c);
    lock(c);
    if (n >= 0) {
        c->snap.dtc.valid = 1; c->snap.dtc.n = (uint8_t)n; c->snap.dtc.t_us = (int64_t)us(t);
        memset(c->snap.dtc.code, 0, sizeof(c->snap.dtc.code));
        for (int i = 0; i < n; i++) {
            c->snap.dtc.code[i] = codes[i];
            log_event(c, t, HH_EV_DTC, (uint8_t)i, (uint16_t)n, codes[i], 0, 0);
        }
        if (!n) log_event(c, t, HH_EV_DTC_NONE, 0, 0, 0, 0, 0);
        c->snap.seq++;
    }
    unlock(c);
}

/* Honda reads have gone unanswered for iso_after_s: does the ECU speak plain OBD2? */
static void try_iso(hh_core_t *c)
{
    uint8_t d[4];
    lock(c); set_status(c, "trying OBD2 (ISO 9141-2)"); c->snap.seq++; unlock(c);
    int rc = hh_iso_slow_init(&c->iso);
    if (rc == HH_E_PORT) { port_lost(c); return; }
    if (rc != HH_OK) {
        hh_iso_close(&c->iso, c->map->framing.baud);
        c->next_iso_try = now(c) + ISO_RETRY_S;
        lock(c); publish_state(c); unlock(c);
        return;
    }
    /* which of our PIDs does it have? (PID 00: bit 31 = PID 01 ... bit 0 = PID 20) */
    uint32_t have = 0xFFFFFFFFu;
    if (hh_iso_read_pid(&c->iso, 0x00, d, 4) == 4)
        have = ((uint32_t)d[0] << 24) | ((uint32_t)d[1] << 16) | ((uint32_t)d[2] << 8) | d[3];
    c->n_iso = 0;
    for (int i = 0; i < N_ISO_PIDS; i++) {
        uint8_t pid = ISO_PIDS[i].pid;
        if (!(have & (0x80000000u >> (pid - 1)))) continue;
        c->iso_pid[c->n_iso] = pid;
        c->iso_ch[c->n_iso] = -1;
        for (int k = 0; k < c->n_ch; k++)
            if (c->ch[k].kind != HH_KIND_MATH && strcmp(c->ch[k].name, ISO_PIDS[i].name) == 0) c->iso_ch[c->n_iso] = (int16_t)k;
        c->n_iso++;
    }
    c->iso_i = 0; c->iso_fails = 0;
    went_up(c, 1);
    lock(c);
    log_event(c, now(c), HH_EV_ISO_SESSION, c->iso.kb1, c->iso.kb2, (int32_t)have, 0, 0);
    unlock(c);
    read_dtcs(c);
    c->next_dtc = now(c) + ISO_DTC_EVERY_S;
}

static double iso_value(uint8_t pid, const uint8_t *d)
{
    switch (pid) {
    case 0x05: case 0x0F: return d[0] - 40.0;
    case 0x06: case 0x07: return (d[0] - 128) * 100.0 / 128.0;
    case 0x0B: case 0x0D: return d[0];
    case 0x0C: return (d[0] * 256 + d[1]) / 4.0;
    case 0x0E: return d[0] / 2.0 - 64.0;
    case 0x11: return d[0] * 100.0 / 255.0;
    case 0x14: return d[0] / 200.0;
    default: return NAN;
    }
}

static void iso_leave(hh_core_t *c)
{
    hh_iso_close(&c->iso, c->map->framing.baud);
    hh_link_reset(&c->link);
    link_down(c);
    c->probes = 3;
    c->honda_last_ok = now(c);
    c->next_iso_try = now(c) + ISO_RETRY_S;
    emit_down(c);
}

static void iso_step(hh_core_t *c)
{
    uint8_t d[8];
    if (!c->n_iso) { c->iso_pid[0] = 0x0C; c->iso_ch[0] = c->rpm_ch; c->n_iso = 1; }
    if (now(c) >= c->next_dtc) { read_dtcs(c); c->next_dtc = now(c) + ISO_DTC_EVERY_S; }
    uint8_t pid = c->iso_pid[c->iso_i];
    int n = hh_iso_read_pid(&c->iso, pid, d, sizeof(d));
    if (n == HH_E_PORT) { port_lost(c); return; }
    if (n <= 0) {
        c->errors++;
        if (n == HH_E_INIT || ++c->iso_fails >= 3) iso_leave(c);
        return;
    }
    double t = now(c);
    c->iso_fails = 0;
    c->reads++;
    lock(c);
    log_iso(c, t, 0x41, pid, d, (uint8_t)n);
    if (n >= hh_iso_pid_len(pid)) {
        double v = iso_value(pid, d);
        int ch = c->iso_ch[c->iso_i];
        if (ch >= 0) { c->value[ch] = (float)v; c->fresh[ch] = 1; }
        if (pid == 0x0C) stall_feed(c, t, v, 1);
    }
    if (++c->iso_i >= c->n_iso) { c->iso_i = 0; c->tick_latency_us = 0; emit(c, t); }
    unlock(c);
}

/* Stored codes on request while Honda reads are running: Honda polling
 * pauses, the 5-baud init runs, then the line goes back to 9600 and polling
 * resumes once the ECU has let go of the ISO session (nothing is logged as a
 * link loss meanwhile). */
static void run_dtc(hh_core_t *c)
{
    if (c->state == ST_ISO) { read_dtcs(c); return; }
    if (c->state == ST_PORT_LOST) return;
    lock(c); c->dtc_busy = 1; set_status(c, "reading trouble codes"); c->snap.link.state = HH_LINK_CONNECTING; c->snap.seq++; unlock(c);
    int rc = hh_iso_slow_init(&c->iso);
    if (rc == HH_OK) read_dtcs(c);
    hh_iso_close(&c->iso, c->map->framing.baud);
    lock(c); c->dtc_busy = 0; unlock(c);
    if (rc == HH_E_PORT) { port_lost(c); return; }
    hh_link_reset(&c->link);
    link_down(c);
    c->state = ST_CONNECTING;
    c->probes = 3;
    c->quiet_until = now(c) + SWITCH_BACK_S;
    c->honda_last_ok = now(c);
    lock(c); values_unknown(c); publish_state(c); unlock(c);
}

/* ---- the hot loop: one block per call ------------------------------------------------ */

static void step_read(hh_core_t *c)
{
    hh_read_t r;
    int index = hh_sched_next(&c->sched, now(c));
    const hh_block_t *b = &c->plan.blocks[index];
    int rc = hh_link_read_block(&c->link, (uint8_t)b->addr, (uint8_t)b->length, -1, &r);
    if (rc == HH_E_PORT) { port_lost(c); return; }
    if (rc != HH_OK) {
        c->errors++;
        hh_sched_failed(&c->sched, index, now(c));
        /* Silence that outlasted the retry means the ECU is gone; damaged
         * frames mean a bad line, which gets a few more chances. */
        c->fails += rc == HH_E_NO_REPLY ? FAILS_TO_NO_REPLY : 1;
        if (c->fails >= FAILS_TO_NO_REPLY) {
            link_down(c);
            emit_down(c);               /* tell the stall catcher now, not a probe later */
        }
        return;
    }
    c->reads++;
    c->fails = 0;
    c->honda_last_ok = r.t_done;
    hh_sched_done(&c->sched, index, r.t_done);

    double t = r.t_done, t_sampled = r.t_done - r.latency_s, rpm = NAN;
    lock(c);
    for (int k = 0; k < b->count; k++) {
        int ci = c->pch_ch[c->plan.order[b->first + k]];
        const hh_map_chan_t *ch = &c->ch[ci];
        int off = (int)ch->addr - (int)b->addr;
        if (off < 0 || off + ch->length > b->length) continue;      /* another piece of an oversize span */
        const uint8_t *raw = r.data + off;
        uint32_t x = 0;
        for (int i = 0; i < ch->length; i++)
            x = ch->little_endian ? x | ((uint32_t)raw[i] << (8 * i)) : (x << 8) | raw[i];
        hh_expr_env_t env = {c->map->consts, c->map->n_const, NULL, 0, raw, ch->length, (double)x, t};
        c->value[ci] = (float)hh_expr_eval(c->map->prog + ch->prog_off, ch->prog_len, &env);
        c->fresh[ci] = 1;
        if (ci == c->rpm_ch) rpm = c->value[ci];
    }
    log_read(c, t_sampled, (uint8_t)b->addr, r.data, (uint8_t)b->length, r.latency_s);
    stall_feed(c, t, rpm, 1);
    /* a sample per fast cycle, once every block has been seen */
    c->unread[index] = 0;
    c->pending[index] = 0;
    if (none_set(c->pending, c->plan.n_blocks)) {
        memcpy(c->pending, c->sched.top, c->plan.n_blocks);
        if (none_set(c->unread, c->plan.n_blocks)) {
            double lat_us = r.latency_s * 1e6;
            c->tick_latency_us = (uint16_t)(lat_us > 65535 ? 65535 : lat_us < 0 ? 0 : lat_us);
            emit(c, t);
        }
    }
    unlock(c);
    if (c->reads >= c->next_replan) {
        c->next_replan = c->reads + REPLAN_EVERY;
        maybe_replan(c);
    }
}

/* The ECU is silent: ask for one block now and then until it answers. */
static void probe(hh_core_t *c)
{
    hh_read_t r;
    int quiet = now(c) < c->quiet_until;
    c->probes++;
    if (c->probes % 4 == 0 && hh_link_wake(&c->link) == HH_E_PORT) { port_lost(c); return; }
    uint8_t addr = c->plan.n_blocks ? (uint8_t)c->plan.blocks[0].addr : 0;
    uint8_t len = c->plan.n_blocks ? (uint8_t)c->plan.blocks[0].length : 1;
    int rc = hh_link_read_block(&c->link, addr, len, 0, &r);
    if (rc == HH_E_PORT) { port_lost(c); return; }
    if (rc == HH_OK) { c->reads++; link_up(c); return; }
    c->errors++;
    if (!quiet) {
        if (c->state == ST_CONNECTING) c->state = ST_NO_REPLY;
        emit_down(c);
    }
    nap(c, c->probe_wait);
    c->probe_wait = c->probe_wait * 1.5 < PROBE_MAX_S ? c->probe_wait * 1.5 : PROBE_MAX_S;
    if (!quiet && c->cfg.iso_fallback && now(c) - c->honda_last_ok >= c->cfg.iso_after_s && now(c) >= c->next_iso_try)
        try_iso(c);
}

/* Things that depend on time passing, not on a reply (lock held). */
static void housekeeping(hh_core_t *c)
{
    double t = now(c);
    hh_blk_t *blks[3] = {&c->blk_reads, &c->blk_iso, &c->blk_gps};
    for (int i = 0; i < 3; i++)
        if (blks[i]->count && (t - blks[i]->opened_at) * 1000.0 >= c->cfg.block_ms) blk_flush(c, blks[i]);
    if (hh_stall_window_done(&c->stall, t)) capture_finish(c, 1);
    c->snap.stall.triggered = c->stall.capturing;
    /* key off: the drive is over. Not while a capture window is open. */
    if (c->drive_open && c->down_since >= 0 && t - c->down_since >= c->cfg.drive_gap_s && !c->stall.capturing)
        drive_end(c, HH_END_LINK_LOST);
    if (c->dropped != c->dropped_logged && (c->drive_open || c->cap_open)) {
        uint32_t n = c->dropped - c->dropped_logged;
        c->dropped_logged = c->dropped;
        log_event(c, t, HH_EV_LOG_DROPPED, 0, 0, (int32_t)n, 0, 0);
        c->dropped_logged = c->dropped;     /* do not count the note itself if it was dropped too */
    }
}

void hh_core_step(hh_core_t *c)
{
    if (!c->started) {
        c->started = 1;
        connect(c);
    } else {
        int want_dtc;
        lock(c); want_dtc = c->req_dtc; c->req_dtc = 0; unlock(c);
        if (want_dtc) run_dtc(c);
        switch (c->state) {
        case ST_OK: step_read(c); break;
        case ST_ISO: iso_step(c); break;
        case ST_PORT_LOST: emit_down(c); nap(c, c->reopen_wait); reopen(c); break;
        default: probe(c); break;
        }
    }
    lock(c);
    housekeeping(c);
    unlock(c);
}

/* ---- other tasks ----------------------------------------------------------------------- */

void hh_core_pps_edge(hh_core_t *c, int64_t t_us)
{
    lock(c);
    hh_pps_edge(&c->pps, t_us);
    time_track(c, (int64_t)us(now(c)));
    unlock(c);
}

void hh_core_gps_bytes(hh_core_t *c, const uint8_t *data, size_t len)
{
    hh_core_gps_bytes_at(c, data, len, (int64_t)us(now(c)));
}

void hh_core_gps_bytes_at(hh_core_t *c, const uint8_t *data, size_t len, int64_t t_rx_us)
{
    for (size_t i = 0; i < len; i++) {
        int got = hh_nmea_feed(&c->nmea, (char)data[i]);
        if (!got) continue;
        double t = now(c);
        lock(c);
        if (got == 2) {                 /* GGA: the first sentence after the pulse. Its date is the RMC's. */
            int64_t last = c->gps.utc;
            if (last > 1577836800) {
                int64_t at = last / 86400 * 86400 + c->nmea.gga_tod;
                if (at - last > 43200) at -= 86400;
                else if (last - at > 43200) at += 86400;
                pps_pair(c, t_rx_us, at);
            }
            unlock(c);
            continue;
        }
        c->gps = c->nmea.fix;
        c->gps.t_us = us(t);
        c->gps_at = t;
        tap(c, HH_TAP_GPS, &c->gps, sizeof(c->gps), NULL, 0);
        if (c->gps.fix && c->gps.utc > 1577836800u) pps_pair(c, t_rx_us, c->gps.utc);
        if (c->gps.fix && c->gps.utc > 1577836800u && !hh_pps_active(&c->pps, (int64_t)us(t))) {
            /* a date after 2020: the receiver knows the time (to the second; the pulse, when there, does better) */
            int64_t unix_us = (int64_t)c->gps.utc * 1000000;
            int64_t have = c->plat.get_time ? c->plat.get_time(c->plat.time_ctx) : 0;
            int64_t off = have - unix_us;
            if (!c->time_valid || off > 2000000 || off < -2000000) {
                if (c->plat.set_time) c->plat.set_time(c->plat.time_ctx, unix_us);
                c->time_valid = 1;
                log_time(c, t, unix_us, 1);
            }
        }
        if (c->drive_open || c->cap_open) {
            uint8_t *at = blk_room(c, &c->blk_gps, c->gps.t_us, sizeof(hh_gps_t), NULL);
            memcpy(at, &c->gps, sizeof(hh_gps_t));
        }
        housekeeping(c);
        unlock(c);
    }
}

void hh_core_mark_at(hh_core_t *c, int64_t t_us)
{
    double t = now(c), at = (double)t_us / 1e6;
    if (t_us <= 0 || at > t || at < t - 10.0) at = t;
    lock(c);
    c->markers++;
    log_event(c, at, HH_EV_MARKER, 0, 0, (int32_t)c->markers, 0, 0);
    c->snap.seq++;
    unlock(c);
}

void hh_core_mark(hh_core_t *c) { hh_core_mark_at(c, 0); }

void hh_core_ack(hh_core_t *c)
{
    hh_alarm_event_t ev[8];
    double t = now(c);
    lock(c);
    int n = hh_alarms_ack(&c->alarms, t, ev, 8);
    for (int k = 0; k < n; k++)
        log_event(c, t, ev[k].kind == HH_ALEV_ACK ? HH_EV_ALARM_ACK : HH_EV_ALARM_CLEARED, ev[k].index,
                  ev[k].severity, 0, ev[k].value, 1);
    if (n) {
        int top = hh_alarms_top(&c->alarms);
        hh_alarms_levels(&c->alarms, c->snap.alarm, c->n_ch);
        c->snap.top_alarm_acked = top >= 0 ? c->alarms.st[top].acknowledged : 0;
        if (top < 0) { c->snap.top_alarm_ch = -1; c->snap.top_alarm_level = 0; c->snap.top_alarm_text[0] = 0; }
        c->snap.seq++;
    }
    unlock(c);
}

void hh_core_request_dtc(hh_core_t *c) { lock(c); c->req_dtc = 1; unlock(c); }

double hh_core_idle_s(hh_core_t *c)
{
    lock(c);
    double idle = c->down_since < 0 || c->drive_open || c->dtc_busy ? 0.0 : now(c) - c->down_since;
    unlock(c);
    return idle;
}

void hh_core_stop(hh_core_t *c, int reason)
{
    lock(c);
    drive_end(c, (uint32_t)reason);
    unlock(c);
}

void hh_core_set_storage(hh_core_t *c, int sd_ok, float free_mb)
{
    lock(c);
    c->sd_ok = (uint8_t)(sd_ok != 0);
    c->free_mb = free_mb;
    unlock(c);
}

void hh_core_snapshot(hh_core_t *c, hh_snapshot_t *out)
{
    double t = now(c);
    lock(c);
    hh_snapshot_t *s = &c->snap;
    s->n_ch = c->n_ch;
    int fresh_fix = c->gps_at >= 0 && t - c->gps_at < 3.0;
    s->gps.fix = (uint8_t)(fresh_fix && c->gps.fix);
    s->gps.sats = fresh_fix ? c->gps.sats : 0;
    s->gps.lat = c->gps.lat_e7 / 1e7; s->gps.lon = c->gps.lon_e7 / 1e7;
    s->gps.speed_kmh = c->gps.speed_cms * 0.036f;
    s->gps.heading = c->gps.course_cdeg / 100.0f;
    s->gps.hdop = c->gps.hdop_c ? c->gps.hdop_c / 100.0f : NAN;
    s->nmea.good = c->nmea.good;
    s->nmea.bad = c->nmea.bad;
    s->log.sd_ok = (uint8_t)(c->sd_ok && !c->sd_err);
    s->log.recording = (uint8_t)(c->drive_open && s->log.sd_ok);
    s->log.drive_no = c->drive_no;
    s->log.bytes = c->drive_open ? c->queued_bytes : 0;
    s->log.free_mb = c->free_mb;
    s->map_ok = !c->plat.builtin_map;
    s->time_valid = c->time_valid;
    {
        int64_t tn = (int64_t)us(t);
        s->time_source = time_source(c, tn);
        s->time_err_us = s->time_source == HH_TS_PPS ? hh_pps_err_us(&c->pps, tn) : s->time_source == HH_TS_NMEA ? 1000000u : 0;
        s->unix_us = wall_us(c, tn);
        s->time_t_us = tn;
        time_track(c, tn);
    }
    s->standby = (uint8_t)(!c->drive_open && c->down_since >= 0 && t - c->down_since >= c->cfg.drive_gap_s);
    s->markers = c->markers;
    *out = *s;
    unlock(c);
}

/* ---- for the laptop link ----------------------------------------------------------------- */

void hh_core_set_tap(hh_core_t *c, hh_tap_fn fn, void *ctx)
{
    lock(c);
    c->plat.tap = NULL;
    c->plat.tap_ctx = ctx;
    c->plat.tap = fn;
    unlock(c);
}

void hh_core_info(hh_core_t *c, hh_core_info_t *out)
{
    memset(out, 0, sizeof(*out));
    lock(c);
    memcpy(out->map_hash, c->map->hdr.map_hash, sizeof(out->map_hash));
    memcpy(out->map_sha, c->map->hdr.map_sha256, sizeof(out->map_sha));
    if (c->plat.device) strncpy(out->device, c->plat.device, sizeof(out->device) - 1);
    memcpy(out->ecu_id, c->ecu_id, sizeof(out->ecu_id));
    out->ecu_id_len = c->ecu_id_len;
    out->link_state = c->snap.link.state;
    out->backend = c->plat.transport->backend;
    out->link_mode = c->iso_mode;
    out->builtin_map = c->plat.builtin_map;
    out->time_valid = c->time_valid;
    out->time_source = time_source(c, (int64_t)us(now(c)));
    out->sd_ok = (uint8_t)(c->sd_ok && !c->sd_err);
    out->n_ch = (uint8_t)c->n_ch;
    out->boot_no = c->plat.boot_no;
    unlock(c);
}

void hh_core_pps_info(hh_core_t *c, hh_pps_info_t *out)
{
    int64_t tn = (int64_t)us(now(c));
    memset(out, 0, sizeof(*out));
    lock(c);
    out->source = time_source(c, tn);
    out->locked = c->pps.locked;
    out->err_us = c->pps.n_ok ? hh_pps_err_us(&c->pps, tn) : 0;
    out->offset_us = c->pps.n_ok ? hh_pps_unix_us(&c->pps, tn) - tn : 0;
    out->drift_ppm = c->pps.drift * 1e6;
    out->jitter_us = hh_pps_jitter_us(&c->pps);
    out->resid_us = c->pps.resid_us;
    out->edges = c->pps.edges; out->glitches = c->pps.glitches; out->missed = c->pps.missed;
    out->pairs = c->pps.pairs; out->stale = c->pps.stale; out->outliers = c->pps.outliers;
    out->relocks = c->pps.relocks;
    unlock(c);
}

int hh_core_set_time(hh_core_t *c, int64_t unix_us)
{
    int rc = 1;
    lock(c);
    if (!c->time_valid && unix_us > 0 && c->plat.set_time) {    /* GPS time, once there, is the better clock */
        c->plat.set_time(c->plat.time_ctx, unix_us);
        log_time(c, now(c), unix_us, 3);
        rc = 0;
    }
    unlock(c);
    return rc;
}

void hh_core_drive(hh_core_t *c, int on)
{
    lock(c);
    if (!on) {
        c->drive_hold = 1;
        drive_end(c, HH_END_STOPPED);
    } else {
        c->drive_hold = 0;
        if (c->down_since < 0 && !c->drive_open) drive_begin(c, now(c));
    }
    unlock(c);
}

int hh_core_file_busy(hh_core_t *c, uint8_t kind, uint32_t file_no)
{
    int busy;
    lock(c);
    if (kind == HH_FK_DRIVE)
        busy = (c->drive_open && c->drive_no == file_no) || (c->fh_drive && c->fh_drive_no == file_no);
    else
        busy = (c->cap_open && c->cap_no == file_no) || (c->fh_cap && c->fh_cap_no == file_no);
    unlock(c);
    return busy;
}

/* ---- storage task ------------------------------------------------------------------------ */

static void file_open(hh_core_t *c, void **fh, uint32_t *fh_no, const char *dir, char letter, const uint8_t *hdr_bytes,
                      uint32_t *written)
{
    hh_file_hdr_t h;
    char path[40];
    memcpy(&h, hdr_bytes, sizeof(h));
    if (*fh) { c->plat.io.close(c->plat.io.ctx, *fh); *fh = NULL; }
    *fh_no = h.file_no;
    snprintf(path, sizeof(path), "/%s/%c%06lu.HHD", dir, letter, (unsigned long)(h.file_no % 1000000u));
    *fh = c->plat.io.open(c->plat.io.ctx, path, "wb");
    *written = 0;
    if (!*fh) { c->sd_fail++; c->sd_err = 1; return; }
    c->sd_err = 0;
    if ((h.flags & HH_HF_PREALLOC) && c->plat.io.preallocate
        && c->plat.io.preallocate(c->plat.io.ctx, *fh, h.prealloc) != 0) {
        /* no room, or the card cannot: go on without; the header then says so */
        h.flags &= (uint8_t)~HH_HF_PREALLOC; h.prealloc = 0;
        hh_file_hdr_seal(&h);
    }
    if (c->plat.io.write(c->plat.io.ctx, *fh, &h, sizeof(h)) != 0) c->sd_fail++;
    *written = sizeof(h);
    c->plat.io.sync(c->plat.io.ctx, *fh);
}

static void file_close(hh_core_t *c, void **fh, uint32_t written)
{
    if (!*fh) return;
    if (c->plat.io.truncate) c->plat.io.truncate(c->plat.io.ctx, *fh, written);
    c->plat.io.sync(c->plat.io.ctx, *fh);
    c->plat.io.close(c->plat.io.ctx, *fh);
    *fh = NULL;
}

int hh_core_storage_step(hh_core_t *c)
{
    uint8_t item[Q_ITEM_MAX];
    uint32_t pos;
    uint16_t n;
    lock(c);
    pos = c->queue.tail;
    n = hh_ring_peek(&c->queue, &pos, item, sizeof(item));
    if (n) hh_ring_pop(&c->queue);
    unlock(c);
    double t = now(c);
    if (!n) {
        if (c->dirty && (t - c->last_sync) * 1000.0 >= c->cfg.sync_ms) {
            if (c->fh_drive) c->plat.io.sync(c->plat.io.ctx, c->fh_drive);
            if (c->fh_cap) c->plat.io.sync(c->plat.io.ctx, c->fh_cap);
            c->dirty = 0; c->last_sync = t;
            return 1;
        }
        return 0;
    }
    const uint8_t *body = item + 1;
    size_t len = (size_t)n - 1;
    switch (item[0]) {
    case Q_DRIVE_OPEN: file_open(c, &c->fh_drive, &c->fh_drive_no, "DRIVES", 'D', body, &c->written); c->last_sync = t; break;
    case Q_CAP_OPEN: file_open(c, &c->fh_cap, &c->fh_cap_no, "CAPTURES", 'C', body, &c->cap_written); break;
    case Q_DRIVE_DATA:
        if (c->fh_drive) {
            if (c->plat.io.write(c->plat.io.ctx, c->fh_drive, body, len) != 0) { c->sd_fail++; c->sd_err = 1; }
            else c->written += (uint32_t)len;
            c->dirty = 1;
        }
        break;
    case Q_CAP_DATA:
        if (c->fh_cap) {
            if (c->plat.io.write(c->plat.io.ctx, c->fh_cap, body, len) != 0) c->sd_fail++;
            else c->cap_written += (uint32_t)len;
            c->dirty = 1;
        }
        break;
    case Q_DRIVE_CLOSE: file_close(c, &c->fh_drive, c->written); break;
    case Q_CAP_CLOSE: file_close(c, &c->fh_cap, c->cap_written); break;
    case Q_SYNC:
        if (c->fh_drive) c->plat.io.sync(c->plat.io.ctx, c->fh_drive);
        if (c->fh_cap) c->plat.io.sync(c->plat.io.ctx, c->fh_cap);
        c->dirty = 0; c->last_sync = t;
        break;
    default: break;
    }
    if (c->dirty && (t - c->last_sync) * 1000.0 >= c->cfg.sync_ms) {
        if (c->fh_drive) c->plat.io.sync(c->plat.io.ctx, c->fh_drive);
        if (c->fh_cap) c->plat.io.sync(c->plat.io.ctx, c->fh_cap);
        c->dirty = 0; c->last_sync = t;
    }
    return 1;
}

/* ---- init --------------------------------------------------------------------------------- */

hh_core_t *hh_core_init(void *mem, const hh_map_t *map, const hh_config_t *cfg, const hh_platform_t *plat)
{
    hh_core_t *c = mem;
    hh_map_stall_t stall = map->stall;
    memset(c, 0, sizeof(*c));
    c->plat = *plat;
    c->map = map;
    c->cfg = *cfg;
    c->n_ch = map->n_ch;
    c->rpm_ch = stall.rpm_channel;
    for (int i = 0; i < c->n_ch; i++) {
        hh_map_chan_t *ch = &c->ch[i];
        hh_channel_info_t *info = &c->info[i];
        hh_map_chan(map, i, ch);
        memcpy(info->name, ch->name, sizeof(info->name));
        memcpy(info->label, ch->label, sizeof(info->label));
        memcpy(info->units, ch->units, sizeof(info->units));
        info->lo = ch->lo; info->hi = ch->hi;
        info->warn_lo = ch->warn_lo; info->warn_hi = ch->warn_hi;
        info->alarm_lo = ch->alarm_lo; info->alarm_hi = ch->alarm_hi;
        info->decimals = ch->decimals; info->verified = ch->verified;
        info->rate_class = ch->rate_class; info->kind = ch->kind;
        c->value[i] = NAN; c->snap.value[i] = NAN;
        if (ch->kind == HH_KIND_MATH) continue;
        c->pch[c->n_pch].addr = ch->addr; c->pch[c->n_pch].length = ch->length;
        c->pch[c->n_pch].rate_hz = map->framing.rate_hz[ch->rate_class];
        c->pch_ch[c->n_pch++] = (uint8_t)i;
    }
    c->snap.n_ch = c->n_ch;
    c->snap.top_alarm_ch = -1;
    if (cfg->stall_pre_s >= 0) stall.pre_s = (float)cfg->stall_pre_s;
    if (cfg->stall_post_s >= 0) stall.post_s = (float)cfg->stall_post_s;
    hh_stall_init(&c->stall, &stall);
    hh_alarms_init(&c->alarms, map);
    hh_ring_init(&c->ring, plat->ring_mem, plat->ring_mem ? plat->ring_size : 0);
    hh_ring_init(&c->queue, plat->queue_mem, plat->queue_mem ? plat->queue_size : 0);
    hh_link_init(&c->link, plat->transport, &plat->clk, &map->framing);
    hh_iso_init(&c->iso, plat->transport, &plat->clk);
    hh_cost_from_framing(&c->cost, &map->framing, -1, -1);
    build_plan(c);
    c->blk_reads.type = HH_B_READS; c->blk_iso.type = HH_B_ISO; c->blk_gps.type = HH_B_GPS;
    c->state = ST_CONNECTING;
    c->probe_wait = PROBE_MIN_S; c->reopen_wait = REOPEN_MIN_S;
    c->last_emit = -1; c->gps_at = -1;
    hh_pps_init(&c->pps);
    c->down_since = now(c);
    c->next_replan = REPLAN_EVERY;
    c->snap.link.state = HH_LINK_CONNECTING;
    c->snap.link.backend = plat->transport->backend;
    set_status(c, "starting");
    return c;
}
