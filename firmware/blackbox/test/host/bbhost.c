/* Host harness: the real bbcore engine behind a flat C API that the Python
 * tests load with ctypes. Files go to a directory, "USB" bytes to a buffer,
 * time is whatever the test says. Build: make -C firmware/blackbox/test/host */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "bbcore.h"

typedef struct {
    bb_engine_t *e;
    void *mem;
    char root[400];
    uint64_t now;
    uint8_t *tx; size_t tx_len, tx_cap;
    long write_budget;          /* bytes until the "power fails"; <0 = unlimited */
    int syncs;
} host_t;

static void *io_open(void *ctx, const char *path, const char *mode)
{
    host_t *h = ctx;
    char full[512];
    snprintf(full, sizeof(full), "%s%s", h->root, path);
    return fopen(full, mode);
}
static int io_write(void *ctx, void *fh, const void *data, size_t len)
{
    host_t *h = ctx;
    if (h->write_budget >= 0) {
        if ((long)len > h->write_budget) {      /* torn write, then nothing more */
            fwrite(data, 1, (size_t)h->write_budget, fh);
            fflush(fh);
            h->write_budget = 0;
            return -1;
        }
        h->write_budget -= (long)len;
    }
    return fwrite(data, 1, len, fh) == len ? 0 : -1;
}
static int io_read(void *ctx, void *fh, void *data, size_t len) { (void)ctx; return (int)fread(data, 1, len, fh); }
static int io_seek(void *ctx, void *fh, uint32_t off) { (void)ctx; return fseek(fh, (long)off, SEEK_SET); }
static long io_size(void *ctx, void *fh)
{
    (void)ctx;
    long pos = ftell(fh), size;
    fseek(fh, 0, SEEK_END); size = ftell(fh); fseek(fh, pos, SEEK_SET);
    return size;
}
static int io_sync(void *ctx, void *fh) { ((host_t *)ctx)->syncs++; return fflush(fh); }
static int io_close(void *ctx, void *fh) { (void)ctx; return fclose(fh); }

static void tx(void *ctx, const uint8_t *wire, size_t len)
{
    host_t *h = ctx;
    if (h->tx_len + len > h->tx_cap) {
        h->tx_cap = (h->tx_len + len) * 2;
        h->tx = realloc(h->tx, h->tx_cap);
    }
    memcpy(h->tx + h->tx_len, wire, len);
    h->tx_len += len;
}
static uint64_t now(void *ctx) { return ((host_t *)ctx)->now; }

host_t *bbhost_new(const char *root, const char *cfg_text, uint32_t adc_conv_hz, uint32_t last_event_no)
{
    host_t *h = calloc(1, sizeof(*h));
    bb_config_t cfg;
    bb_platform_t plat;
    char dir[512];
    snprintf(h->root, sizeof(h->root), "%s", root);
    snprintf(dir, sizeof(dir), "%s/EVENTS", root);  mkdir(dir, 0777);
    snprintf(dir, sizeof(dir), "%s/SUMMARY", root); mkdir(dir, 0777);
    bb_config_defaults(&cfg);
    if (cfg_text && bb_config_parse(&cfg, cfg_text, strlen(cfg_text), NULL) != 0) { free(h); return NULL; }
    memset(&plat, 0, sizeof(plat));
    plat.io.ctx = h; plat.io.open = io_open; plat.io.write = io_write; plat.io.read = io_read;
    plat.io.seek = io_seek; plat.io.size = io_size; plat.io.sync = io_sync; plat.io.close = io_close;
    plat.tx = tx; plat.tx_ctx = h; plat.now_us = now; plat.now_ctx = h;
    plat.adc_conv_hz = adc_conv_hz; plat.device = "HOSTTEST"; plat.boot_no = 7;
    plat.last_event_no = last_event_no; plat.sd_ok = 1;
    h->write_budget = -1;
    size_t size = bb_engine_mem_size(&cfg);
    h->mem = malloc(size);
    h->e = bb_engine_init(h->mem, size, &cfg, &plat);
    return h;
}

void bbhost_free(host_t *h) { free(h->mem); free(h->tx); free(h); }
void bbhost_set_now(host_t *h, uint64_t t) { h->now = t; }
void bbhost_set_write_budget(host_t *h, long bytes) { h->write_budget = bytes; }
int  bbhost_syncs(const host_t *h) { return h->syncs; }
void bbhost_edge(host_t *h, unsigned ch, int level, uint64_t t) { bb_engine_edge(h->e, ch, level, t); }
void bbhost_button(host_t *h, int pressed) { bb_engine_button(h->e, pressed); }

void bbhost_adc(host_t *h, uint64_t t_arrival, const uint8_t *ch, const uint16_t *mv, size_t n)
{
    bb_conv_t conv[1024];
    while (n) {
        size_t k = n > 1024 ? 1024 : n;
        for (size_t i = 0; i < k; i++) { conv[i].ch = ch[i]; conv[i].mv = mv[i]; }
        /* a split chunk keeps its arrival time on the last part only */
        bb_engine_adc_chunk(h->e, t_arrival, conv, k);
        ch += k; mv += k; n -= k;
    }
    h->now = t_arrival;
}

/* Run the storage task until idle. Returns the number of steps that did work. */
int bbhost_storage(host_t *h, int max_steps)
{
    int n = 0;
    while (n < max_steps && bb_engine_storage_step(h->e)) n++;
    return n;
}

void bbhost_rx(host_t *h, const uint8_t *data, size_t len) { bb_engine_rx(h->e, data, len); }
void bbhost_link_poll(host_t *h) { bb_engine_link_poll(h->e); }
size_t bbhost_tx_take(host_t *h, uint8_t *out, size_t cap)
{
    size_t n = h->tx_len < cap ? h->tx_len : cap;
    memcpy(out, h->tx, n);
    memmove(h->tx, h->tx + n, h->tx_len - n);
    h->tx_len -= n;
    return n;
}
void bbhost_status(const host_t *h, bb_status_t *st) { bb_engine_status(h->e, st); }
void bbhost_gps(host_t *h, const bb_gps_t *fix) { bb_engine_gps(h->e, fix); }
int  bbhost_pre_s(const host_t *h) { return bb_engine_config(h->e)->pre_s; }

/* ---- direct access to the pieces, for vector tests ---------------------- */

typedef struct { bb_config_t cfg; bb_trigger_t tr; } trig_t;

trig_t *bbhost_trig_new(const char *cfg_text)
{
    trig_t *t = calloc(1, sizeof(*t));
    bb_config_defaults(&t->cfg);
    if (cfg_text && bb_config_parse(&t->cfg, cfg_text, strlen(cfg_text), NULL) != 0) { free(t); return NULL; }
    return t;
}
void bbhost_trig_free(trig_t *t) { free(t); }

/* row: mv[5], min[5], max[5], rpm_tach, rpm_ign, inj_pw, inj_duty, age[3], button = 23 ints.
 * out: up to 12 x (code, src, value, action). Returns the event count; *dig gets the state bits. */
int bbhost_trig_step(trig_t *t, uint64_t t_us, const int32_t *row, int32_t *out, uint8_t *dig)
{
    bb_sample_t s;
    bb_trig_in_t in;
    bb_event_t ev[BB_TRIG_MAX_OUT];
    memset(&s, 0, sizeof(s)); memset(&in, 0, sizeof(in));
    for (int i = 0; i < 5; i++) {
        s.mv[i] = (uint16_t)row[i]; in.min_mv[i] = (uint16_t)row[5 + i]; in.max_mv[i] = (uint16_t)row[10 + i];
    }
    s.rpm_tach = (uint16_t)row[15]; s.rpm_ign = (uint16_t)row[16];
    s.inj_pw_us = (uint16_t)row[17]; s.inj_duty = (uint8_t)row[18];
    for (int i = 0; i < 3; i++) in.age_ms[i] = (uint32_t)row[19 + i];
    in.button = row[22]; in.s = &s; in.t_us = t_us;
    int n = bb_trigger_step(&t->tr, &t->cfg, &in, ev, dig);
    for (int i = 0; i < n; i++) {
        out[4 * i] = ev[i].code; out[4 * i + 1] = ev[i].src; out[4 * i + 2] = ev[i].value; out[4 * i + 3] = ev[i].rsv;
    }
    return n;
}

size_t bbhost_config_dump(const char *cfg_text, char *out, size_t cap, int *bad, int *bad_line)
{
    bb_config_t cfg;
    bb_config_defaults(&cfg);
    *bad = cfg_text ? bb_config_parse(&cfg, cfg_text, strlen(cfg_text), bad_line) : 0;
    return bb_config_dump(&cfg, out, cap);
}

int bbhost_nmea(const char *text, bb_gps_t *fix)
{
    bb_nmea_t g;
    int got = 0;
    memset(&g, 0, sizeof(g));
    for (const char *p = text; *p; p++) got |= bb_nmea_feed(&g, *p);
    *fix = g.fix;
    return got;
}

size_t bbhost_sizes(uint32_t *out)
{
    const uint32_t v[] = {sizeof(bb_sample_t), sizeof(bb_event_t), sizeof(bb_gps_t), sizeof(bb_file_hdr_t),
                          sizeof(bb_block_hdr_t), sizeof(bb_index_t), sizeof(bb_end_t), sizeof(bb_hello_t),
                          sizeof(bb_live_t), sizeof(bb_event_msg_t), sizeof(bb_time_rsp_t),
                          sizeof(bb_file_info_t), sizeof(bb_read_file_t), sizeof(bb_status_t)};
    memcpy(out, v, sizeof(v));
    return sizeof(v) / sizeof(v[0]);
}
