#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "hhhost.h"

static const uint8_t DEFAULT_MAP[] = {
#include "../../components/hhcore/port/hh_default_map.inc"
};

struct hhhost {
    hhhost_cb_t cb;
    hh_transport_t tr;
    hh_platform_t plat;
    hh_map_t map;
    hh_config_t cfg;
    hh_core_t *core;
    uint8_t *map_buf, *ring, *queue;
    char root[256], stale[256];
    long budget;
    int64_t time_set, time_base;    /* unix us at clock 0 once set */
    uint32_t syncs;
    hh_serial_t *ser;
    hh_serfs_t serfs;
    uint8_t *ser_ring;
};

size_t hhhost_default_map(const uint8_t **out) { *out = DEFAULT_MAP; return sizeof(DEFAULT_MAP); }

/* ---- transport and clock: forward to the callbacks ------------------------- */

static int t_open(void *ctx) { return ((hhhost_t *)ctx)->cb.open(); }
static void t_close(void *ctx) { ((hhhost_t *)ctx)->cb.close(); }
static int t_write(void *ctx, const uint8_t *d, size_t n) { return ((hhhost_t *)ctx)->cb.write(d, n); }
static int t_read(void *ctx, uint8_t *b, size_t max, double to) { return ((hhhost_t *)ctx)->cb.read_some(b, max, to); }
static void t_flush(void *ctx) { ((hhhost_t *)ctx)->cb.flush_input(); }
static int t_baud(void *ctx, uint32_t baud) { return ((hhhost_t *)ctx)->cb.set_baud(baud); }
static int t_break(void *ctx, int on) { return ((hhhost_t *)ctx)->cb.set_break(on); }
static double c_now(void *ctx) { return ((hhhost_t *)ctx)->cb.now(); }
static void c_sleep(void *ctx, double s) { ((hhhost_t *)ctx)->cb.sleep(s); }

static void set_time(void *ctx, int64_t unix_us)
{
    hhhost_t *h = ctx;
    h->time_set = unix_us;
    h->time_base = unix_us - (int64_t)(h->cb.now() * 1e6);
}
static int64_t get_time(void *ctx)
{
    hhhost_t *h = ctx;
    return h->time_set ? h->time_base + (int64_t)(h->cb.now() * 1e6) : 0;
}

/* ---- files -------------------------------------------------------------------- */

static void *io_open(void *ctx, const char *path, const char *mode)
{
    hhhost_t *h = ctx;
    char full[400], dir[400];
    snprintf(full, sizeof(full), "%s%s", h->root, path);
    snprintf(dir, sizeof(dir), "%s", full);
    char *slash = strrchr(dir, '/');
    if (slash) { *slash = 0; mkdir(dir, 0777); }
    if (h->budget == 0) return NULL;
    return fopen(full, mode);
}
static int io_write(void *ctx, void *fh, const void *data, size_t len)
{
    hhhost_t *h = ctx;
    if (h->budget >= 0) {               /* a power cut tears the write that is in flight */
        if ((long)len > h->budget) len = (size_t)h->budget;
        h->budget -= (long)len;
        if (!len) return 0;
    }
    return fwrite(data, 1, len, fh) == len ? 0 : -1;
}
static int io_sync(void *ctx, void *fh) { ((hhhost_t *)ctx)->syncs++; return fflush(fh); }
static int io_close(void *ctx, void *fh) { (void)ctx; return fclose(fh); }
static int io_prealloc(void *ctx, void *fh, uint32_t len)
{
    hhhost_t *h = ctx;
    /* A real card hands back whatever was in those sectors. Fill the space
     * with an older file's bytes if the test gave one, else with 0xA5. */
    uint8_t chunk[4096];
    FILE *old = h->stale[0] ? fopen(h->stale, "rb") : NULL;
    if (len > (64u << 20)) return -1;
    for (uint32_t done = 0; done < len;) {
        size_t n = len - done < sizeof(chunk) ? len - done : sizeof(chunk);
        size_t got = old ? fread(chunk, 1, n, old) : 0;
        if (got < n) memset(chunk + got, 0xA5, n - got);
        if (fwrite(chunk, 1, n, fh) != n) { if (old) fclose(old); return -1; }
        done += (uint32_t)n;
    }
    if (old) fclose(old);
    return fseek(fh, 0, SEEK_SET);
}
static int io_truncate(void *ctx, void *fh, uint32_t len)
{
    hhhost_t *h = ctx;
    if (h->budget >= 0) return 0;       /* no power, no tidy end */
    fflush(fh);
    return ftruncate(fileno(fh), (off_t)len);
}

/* ---- API ------------------------------------------------------------------------ */

hhhost_t *hhhost_new(const char *sd_root, const uint8_t *map, size_t map_len, const char *cfg_text,
                     const hhhost_cb_t *cb, uint32_t boot_no, uint32_t last_drive_no)
{
    hhhost_t *h = calloc(1, sizeof(*h));
    if (!h) return NULL;
    h->cb = *cb;
    h->budget = -1;
    snprintf(h->root, sizeof(h->root), "%s", sd_root);
    mkdir(h->root, 0777);
    if (!map) { map = DEFAULT_MAP; map_len = sizeof(DEFAULT_MAP); h->plat.builtin_map = 1; }
    h->map_buf = malloc(map_len);
    memcpy(h->map_buf, map, map_len);
    if (hh_map_parse(&h->map, h->map_buf, map_len) != 0) { free(h->map_buf); free(h); return NULL; }
    hh_config_default(&h->cfg);
    if (cfg_text) hh_config_parse(&h->cfg, cfg_text, strlen(cfg_text));

    h->tr.ctx = h; h->tr.open = t_open; h->tr.close = t_close; h->tr.write = t_write;
    h->tr.read_some = t_read; h->tr.flush_input = t_flush; h->tr.set_baud = t_baud; h->tr.set_break = t_break;
    h->tr.usb_latency_s = cb->usb_latency_s;
    h->tr.backend = HH_BACKEND_USB;

    hh_platform_t *p = &h->plat;
    p->clk.ctx = h; p->clk.now = c_now; p->clk.sleep = c_sleep;
    p->transport = &h->tr;
    p->io.ctx = h; p->io.open = io_open; p->io.write = io_write; p->io.sync = io_sync; p->io.close = io_close;
    p->io.preallocate = io_prealloc; p->io.truncate = io_truncate;
    p->set_time = set_time; p->get_time = get_time; p->time_ctx = h;
    p->device = "HOSTTEST";
    p->boot_no = boot_no;
    p->last_drive_no = last_drive_no;
    p->nonce_seed = 0x1234ABCDu ^ boot_no;
    p->ring_size = 256 * 1024;
    h->ring = malloc(p->ring_size); p->ring_mem = h->ring;
    p->queue_size = 512 * 1024;
    h->queue = malloc(p->queue_size); p->queue_mem = h->queue;
    h->core = hh_core_init(malloc(hh_core_size()), &h->map, &h->cfg, p);
    hh_core_set_storage(h->core, 1, 1000.0f);
    return h;
}

void hhhost_free(hhhost_t *h)
{
    if (!h) return;
    hh_core_stop(h->core, HH_END_STOPPED);
    while (hh_core_storage_step(h->core)) {}
    if (h->serfs.fh) fclose(h->serfs.fh);
    if (h->serfs.put) fclose(h->serfs.put);
    free(h->ser); free(h->ser_ring);
    free(h->core); free(h->ring); free(h->queue); free(h->map_buf); free(h);
}

hh_core_t *hhhost_core(hhhost_t *h) { return h->core; }

void hhhost_run(hhhost_t *h, int steps)
{
    for (int i = 0; i < steps; i++) {
        hh_core_step(h->core);
        while (hh_core_storage_step(h->core)) {}
    }
}

void hhhost_run_until(hhhost_t *h, double t)
{
    while (h->cb.now() < t) hhhost_run(h, 1);
}

void hhhost_gps(hhhost_t *h, const char *nmea)
{
    hh_core_gps_bytes(h->core, (const uint8_t *)nmea, strlen(nmea));
    while (hh_core_storage_step(h->core)) {}
}

void hhhost_snapshot(hhhost_t *h, hh_snapshot_t *out) { hh_core_snapshot(h->core, out); }
void hhhost_set_write_budget(hhhost_t *h, long bytes) { h->budget = bytes; }
void hhhost_set_stale(hhhost_t *h, const char *path) { snprintf(h->stale, sizeof(h->stale), "%s", path ? path : ""); }
int64_t hhhost_time_set(const hhhost_t *h) { return h->time_set; }
uint32_t hhhost_syncs(const hhhost_t *h) { return h->syncs; }

/* ---- the laptop link ------------------------------------------------------------ */

hh_serial_t *hhhost_serial(hhhost_t *h, int tx_only, uint32_t ring_size)
{
    if (h->ser) return h->ser;
    if (!ring_size) ring_size = 32 * 1024;
    h->ser = calloc(1, sizeof(*h->ser));
    h->ser_ring = malloc(ring_size);
    hh_serial_init(h->ser, &h->plat.clk, h->ser_ring, ring_size,
                   tx_only ? HH_SER_VARIANT_GPS_P1 : HH_SER_VARIANT_DEFAULT,
                   tx_only ? HH_SER_CAP_LOG : HH_SER_CAP_RX | HH_SER_CAP_FILES | HH_SER_CAP_PUT | HH_SER_CAP_MAPUP | HH_SER_CAP_LOG, 921600);
    if (!tx_only) hh_serfs_init(&h->serfs, h->root, h->core, &h->ser->fs);
    hh_serial_attach(h->ser, h->core);
    hh_core_set_tap(h->core, hh_serial_tap, h->ser);
    return h->ser;
}

size_t hhhost_serial_poll(hhhost_t *h, uint8_t *out, size_t cap) { return h->ser ? hh_serial_poll(h->ser, out, cap) : 0; }

void hhhost_serial_rx(hhhost_t *h, const uint8_t *data, size_t len)
{
    if (!h->ser) return;
    hh_serial_rx(h->ser, data, len);
    while (hh_core_storage_step(h->core)) {}
}

uint32_t hhhost_serial_stat(hhhost_t *h, const char *what)
{
    const hh_serial_t *s = h->ser;
    if (!s) return 0;
    if (!strcmp(what, "tx_frames")) return s->tx_frames;
    if (!strcmp(what, "tx_dropped")) return s->tx_dropped;
    if (!strcmp(what, "tx_bytes")) return s->tx_bytes;
    if (!strcmp(what, "rx_frames")) return s->rx_frames;
    if (!strcmp(what, "rx_bad")) return s->rx_bad;
    if (!strcmp(what, "ring_peak")) return s->ring_peak;
    if (!strcmp(what, "caps")) return s->caps;
    if (!strcmp(what, "host")) return (uint32_t)hh_serial_host_attached(s);
    if (!strcmp(what, "blocked")) return hh_core_link_stats(h->core)->blocked;
    return 0;
}

/* ---- flat entry points for the Python tests (ctypes) -------------------------- */

int hhhost_plan(int n, const uint8_t *addr, const uint8_t *length, const double *rate, int max_len,
                uint32_t baud, double turnaround_s, double gap_s,
                uint16_t *b_addr, uint16_t *b_len, double *b_rate, uint8_t *b_first, uint8_t *b_count,
                uint8_t *order, double *load)
{
    hh_plan_ch_t ch[HH_MAX_CH];
    hh_plan_t plan;
    hh_cost_t cost = {10.0 / baud, HH_REQUEST_BYTES, HH_REPLY_OVERHEAD, turnaround_s, gap_s};
    if (n > HH_MAX_CH) return -1;
    for (int i = 0; i < n; i++) { ch[i].addr = addr[i]; ch[i].length = length[i]; ch[i].rate_hz = rate[i]; }
    if (hh_plan_build(&plan, ch, n, max_len, &cost) != HH_OK) return -1;
    for (int i = 0; i < plan.n_blocks; i++) {
        b_addr[i] = plan.blocks[i].addr; b_len[i] = plan.blocks[i].length; b_rate[i] = plan.blocks[i].rate_hz;
        b_first[i] = plan.blocks[i].first; b_count[i] = plan.blocks[i].count;
    }
    memcpy(order, plan.order, (size_t)n);
    *load = hh_plan_bus_load(&plan, &cost);
    return plan.n_blocks;
}

hh_sched_t *hhhost_sched_new(int n, const double *rate)
{
    hh_plan_t plan;
    memset(&plan, 0, sizeof(plan));
    plan.n_blocks = (uint8_t)n;
    for (int i = 0; i < n; i++) plan.blocks[i].rate_hz = rate[i];
    hh_sched_t *s = malloc(sizeof(*s));
    hh_sched_init(s, &plan);
    return s;
}
void hhhost_sched_free(hh_sched_t *s) { free(s); }

double hhhost_eval(const uint8_t *prog, size_t len, const double *consts, uint16_t n_const,
                   const float *ch, uint16_t n_ch, const uint8_t *raw, uint8_t raw_len, double x, double t)
{
    hh_expr_env_t env = {consts, n_const, ch, n_ch, raw, raw_len, x, t};
    return hh_expr_eval(prog, len, &env);
}

int hhhost_map_check(const uint8_t *map, size_t len)
{
    hh_map_t m;
    return hh_map_parse(&m, map, len);
}

/* Decode raw bytes with channel `index` of a map, as the engine does. */
double hhhost_map_decode(const uint8_t *map, size_t len, int index, const uint8_t *raw)
{
    hh_map_t m;
    hh_map_chan_t c;
    if (hh_map_parse(&m, map, len) != 0 || index < 0 || index >= m.n_ch) return -12345.0;
    hh_map_chan(&m, index, &c);
    uint32_t x = 0;
    for (int i = 0; i < c.length; i++) x = c.little_endian ? x | ((uint32_t)raw[i] << (8 * i)) : (x << 8) | raw[i];
    hh_expr_env_t env = {m.consts, m.n_const, NULL, 0, raw, c.length, (double)x, 0.0};
    return (double)(float)hh_expr_eval(m.prog + c.prog_off, c.prog_len, &env);
}

/* A math channel of a map over the given channel values. */
double hhhost_map_math(const uint8_t *map, size_t len, int index, const float *values, double t)
{
    hh_map_t m;
    hh_map_chan_t c;
    if (hh_map_parse(&m, map, len) != 0 || index < 0 || index >= m.n_ch) return -12345.0;
    hh_map_chan(&m, index, &c);
    hh_expr_env_t env = {m.consts, m.n_const, values, m.n_ch, NULL, 0, 0.0 / 0.0, t};
    return (double)(float)hh_expr_eval(m.prog + c.prog_off, c.prog_len, &env);
}

typedef struct { hh_map_t map; hh_alarms_t a; uint8_t *buf; } hhhost_alarms_t;

hhhost_alarms_t *hhhost_alarms_new(const uint8_t *map, size_t len)
{
    hhhost_alarms_t *h = calloc(1, sizeof(*h));
    h->buf = malloc(len);
    memcpy(h->buf, map, len);
    if (hh_map_parse(&h->map, h->buf, len) != 0) { free(h->buf); free(h); return NULL; }
    hh_alarms_init(&h->a, &h->map);
    return h;
}
void hhhost_alarms_free(hhhost_alarms_t *h) { if (h) { free(h->buf); free(h); } }
/* Events come back as kind * 256 + index. */
int hhhost_alarms_update(hhhost_alarms_t *h, const float *values, int n, double t, int *events, int max)
{
    hh_alarm_event_t ev[16];
    int got = hh_alarms_update(&h->a, values, (uint16_t)n, t, ev, max < 16 ? max : 16);
    for (int i = 0; i < got; i++) events[i] = ev[i].kind * 256 + ev[i].index;
    return got;
}
int hhhost_alarms_ack(hhhost_alarms_t *h, double t, int *events, int max)
{
    hh_alarm_event_t ev[16];
    int got = hh_alarms_ack(&h->a, t, ev, max < 16 ? max : 16);
    for (int i = 0; i < got; i++) events[i] = ev[i].kind * 256 + ev[i].index;
    return got;
}
int hhhost_alarms_state(const hhhost_alarms_t *h, int index) { return h->a.st[index].state; }
int hhhost_alarms_top(const hhhost_alarms_t *h) { return hh_alarms_top(&h->a); }

/* The snapshot, one number at a time (the struct stays out of ctypes). */
double hhhost_get(hhhost_t *h, const char *what, int index)
{
    hh_snapshot_t s;
    hh_core_snapshot(h->core, &s);
    const hh_link_stats_t *st = hh_core_link_stats(h->core);
    if (!strcmp(what, "value")) return index < HH_MAX_CH ? s.value[index] : 0;
    if (!strcmp(what, "fresh")) return index < HH_MAX_CH ? s.fresh[index] : 0;
    if (!strcmp(what, "alarm")) return index < HH_MAX_CH ? s.alarm[index] : 0;
    if (!strcmp(what, "seq")) return s.seq;
    if (!strcmp(what, "n_ch")) return s.n_ch;
    if (!strcmp(what, "link.state")) return s.link.state;
    if (!strcmp(what, "link.hz_fast")) return s.link.hz_fast;
    if (!strcmp(what, "link.reads")) return s.link.reads;
    if (!strcmp(what, "link.errors")) return s.link.errors;
    if (!strcmp(what, "gps.fix")) return s.gps.fix;
    if (!strcmp(what, "gps.lat")) return s.gps.lat;
    if (!strcmp(what, "gps.lon")) return s.gps.lon;
    if (!strcmp(what, "log.recording")) return s.log.recording;
    if (!strcmp(what, "log.drive_no")) return s.log.drive_no;
    if (!strcmp(what, "log.bytes")) return s.log.bytes;
    if (!strcmp(what, "stall.armed")) return s.stall.armed;
    if (!strcmp(what, "stall.triggered")) return s.stall.triggered;
    if (!strcmp(what, "stall.count")) return s.stall.count;
    if (!strcmp(what, "top_alarm_ch")) return s.top_alarm_ch;
    if (!strcmp(what, "top_alarm_level")) return s.top_alarm_level;
    if (!strcmp(what, "dtc.n")) return s.dtc.valid ? s.dtc.n : -1;
    if (!strcmp(what, "dtc.code")) return index < 6 ? s.dtc.code[index] : 0;
    if (!strcmp(what, "markers")) return s.markers;
    if (!strcmp(what, "standby")) return s.standby;
    if (!strcmp(what, "stats.blocked")) return st->blocked;
    if (!strcmp(what, "stats.ok")) return st->ok;
    if (!strcmp(what, "stats.retries")) return st->retries;
    if (!strcmp(what, "stats.timeouts")) return st->timeouts;
    if (!strcmp(what, "stats.checksum_failures")) return st->checksum_failures;
    if (!strcmp(what, "stats.reconnects")) return st->reconnects;
    if (!strcmp(what, "plan.n")) return hh_core_plan(h->core)->n_blocks;
    if (!strcmp(what, "plan.addr")) return hh_core_plan(h->core)->blocks[index].addr;
    if (!strcmp(what, "plan.length")) return hh_core_plan(h->core)->blocks[index].length;
    return -99999.0;
}

void hhhost_text(hhhost_t *h, const char *what, char *out, size_t cap)
{
    hh_snapshot_t s;
    hh_core_snapshot(h->core, &s);
    const char *text = !strcmp(what, "ecu_id") ? s.link.ecu_id : !strcmp(what, "status") ? s.status
                       : !strcmp(what, "stall.last_reason") ? s.stall.last_reason
                       : !strcmp(what, "top_alarm_text") ? s.top_alarm_text : "";
    snprintf(out, cap, "%s", text);
}

void hhhost_mark(hhhost_t *h) { hh_core_mark(h->core); }
void hhhost_ack(hhhost_t *h) { hh_core_ack(h->core); }
void hhhost_request_dtc(hhhost_t *h) { hh_core_request_dtc(h->core); }
void hhhost_mark_at(hhhost_t *h, int64_t t_us) { hh_core_mark_at(h->core, t_us); }
