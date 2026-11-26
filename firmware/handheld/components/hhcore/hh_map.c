/* hhcore: HANDHELD.MAP (the laptop's address map, compiled by
 * `hondalog-handheld export-map`) and HANDHELD.CFG (the handheld's own
 * settings, text). Layouts: FORMAT.md. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hhcore.h"

#ifndef HH_ROTATE_DEFAULT
#define HH_ROTATE_DEFAULT 0     /* build with -DHH_ROTATE_DEFAULT=180 for a board mounted upside down */
#endif

/* ---- HANDHELD.MAP ---------------------------------------------------------- */

void hh_map_chan(const hh_map_t *m, int i, hh_map_chan_t *out)
{
    memcpy(out, m->chan + (size_t)i * sizeof(*out), sizeof(*out));
    out->name[sizeof(out->name) - 1] = 0;
    out->label[sizeof(out->label) - 1] = 0;
    out->units[sizeof(out->units) - 1] = 0;
}

void hh_map_alarm(const hh_map_t *m, int i, hh_map_alarm_t *out)
{
    memcpy(out, m->alarm + (size_t)i * sizeof(*out), sizeof(*out));
    out->name[sizeof(out->name) - 1] = 0;
    out->label[sizeof(out->label) - 1] = 0;
}

static int prog_ok(const hh_map_t *m, unsigned off, unsigned len, int may_be_empty)
{
    if (!len) return may_be_empty;
    if (off + len > m->prog_len) return 0;
    return hh_expr_check(m->prog + off, len, m->n_const, m->n_ch) == 0;
}

int hh_map_parse(hh_map_t *m, const uint8_t *buf, size_t len)
{
    memset(m, 0, sizeof(*m));
    if (len < sizeof(hh_map_hdr_t)) return -1;
    memcpy(&m->hdr, buf, sizeof(m->hdr));
    if (m->hdr.magic != HH_MAP_MAGIC || m->hdr.hdr_len != sizeof(hh_map_hdr_t)) return -1;
    if (m->hdr.schema != HH_MAP_SCHEMA) return -2;
    if (m->hdr.core_len < sizeof(hh_map_hdr_t) || m->hdr.core_len > len || m->hdr.core_len > HH_MAP_MAX_CORE)
        return -1;
    {
        static const uint8_t zero[4] = {0, 0, 0, 0};
        size_t at = offsetof(hh_map_hdr_t, core_crc);
        uint32_t crc = hh_crc32(0, buf, at);
        crc = hh_crc32(crc, zero, 4);
        crc = hh_crc32(crc, buf + at + 4, m->hdr.core_len - at - 4);
        if (crc != m->hdr.core_crc) return -3;
    }
    hh_framing_default(&m->framing);
    m->info.ecu_id_addr = -1;
    m->stall.rpm_channel = -1;

    size_t pos = sizeof(hh_map_hdr_t), end = m->hdr.core_len;
    int have_chan = 0;
    while (pos + sizeof(hh_map_sec_t) <= end) {
        hh_map_sec_t sec;
        memcpy(&sec, buf + pos, sizeof(sec));
        pos += sizeof(sec);
        if (sec.len > end - pos) return -4;
        const uint8_t *body = buf + pos;
        switch (sec.type) {
        case HH_MS_INFO:
            if (sec.len != sizeof(hh_map_info_t)) return -4;
            memcpy(&m->info, body, sizeof(m->info));
            m->info.version[sizeof(m->info.version) - 1] = 0;
            m->info.variant[sizeof(m->info.variant) - 1] = 0;
            break;
        case HH_MS_PROTO: {
            hh_map_proto_t p;
            hh_framing_t *f = &m->framing;
            if (sec.len != sizeof(p)) return -4;
            memcpy(&p, body, sizeof(p));
            if (p.baud < 300 || p.baud > 115200 || p.init_len > sizeof(p.init_seq) || !p.max_read_len) return -4;
            f->baud = p.baud; f->cmd_read = p.cmd_read; f->request_len = p.request_len;
            f->reply_header = p.reply_header; f->reply_len_extra = p.reply_len_extra;
            f->max_read_len = p.max_read_len > HH_MAX_READ ? HH_MAX_READ : p.max_read_len;
            f->adaptive_timing = p.adaptive_timing; f->retries = p.retries;
            f->init_len = p.init_len; memcpy(f->init_seq, p.init_seq, sizeof(f->init_seq));
            f->init_delay_s = p.init_delay_s; f->reply_timeout_s = p.reply_timeout_s;
            f->inter_request_delay_s = p.inter_request_delay_s; f->min_reply_timeout_s = p.min_reply_timeout_s;
            f->inter_byte_timeout_s = p.inter_byte_timeout_s;
            f->max_inter_request_delay_s = p.max_inter_request_delay_s; f->turnaround_s = p.turnaround_s;
            for (int i = 0; i < 3; i++) f->rate_hz[i] = p.rate_hz[i];
            break;
        }
        case HH_MS_CONST:
            if (sec.len != (uint32_t)sec.count * sizeof(double) || sec.count > HH_MAX_CONST) return -4;
            m->consts = (const double *)(const void *)body; m->n_const = sec.count;
            break;
        case HH_MS_CHAN:
            if (sec.len != (uint32_t)sec.count * sizeof(hh_map_chan_t)) return -4;
            if (sec.count > HH_MAX_CH) return -6;
            m->chan = body; m->n_ch = sec.count; have_chan = 1;
            break;
        case HH_MS_PROG:
            m->prog = body; m->prog_len = sec.len;
            break;
        case HH_MS_ALARM:
            if (sec.len != (uint32_t)sec.count * sizeof(hh_map_alarm_t)) return -4;
            if (sec.count > HH_MAX_ALARMS) return -6;
            m->alarm = body; m->n_alarm = sec.count;
            break;
        case HH_MS_STALL:
            if (sec.len != sizeof(hh_map_stall_t)) return -4;
            memcpy(&m->stall, body, sizeof(m->stall));
            break;
        default:
            break;      /* a newer exporter's section: skip it */
        }
        pos += sec.len;
    }
    if (pos != end || !have_chan || !m->n_ch) return -4;

    for (int i = 0; i < m->n_ch; i++) {
        hh_map_chan_t c;
        hh_map_chan(m, i, &c);
        if (c.kind > HH_KIND_MATH || c.rate_class > 2) return -4;
        if (c.kind != HH_KIND_MATH && (c.length < 1 || c.length > 4 || (unsigned)c.addr + c.length > 0x100))
            return -4;
        if (!prog_ok(m, c.prog_off, c.prog_len, 0)) return -5;
    }
    for (int i = 0; i < m->n_alarm; i++) {
        hh_map_alarm_t a;
        hh_map_alarm(m, i, &a);
        if (a.channel >= (int)m->n_ch || a.channel < -1) return -4;
        if ((a.flags & (HH_AF_LOW | HH_AF_HIGH | HH_AF_RATE_LOW | HH_AF_RATE_HIGH)) && a.channel < 0) return -4;
        if (!prog_ok(m, a.when_off, a.when_len, 1) || !prog_ok(m, a.clear_off, a.clear_len, 1)
            || !prog_ok(m, a.gate_off, a.gate_len, 1)) return -5;
    }
    if (m->stall.rpm_channel >= (int)m->n_ch) return -4;
    return 0;
}

/* ---- HANDHELD.CFG ------------------------------------------------------------ */

#define OFF(field) offsetof(hh_config_t, field)
static const struct { const char *key; size_t off; int32_t def, lo, hi; } KEYS[] = {
    {"backend", OFF(backend), HH_CFG_BACKEND_AUTO, 0, 2},
    {"gps_baud", OFF(gps_baud), 9600, 0, 115200},
    {"iso_fallback", OFF(iso_fallback), 1, 0, 1},
    {"iso_after_s", OFF(iso_after_s), 12, 2, 600},
    {"drive_gap_s", OFF(drive_gap_s), 120, 5, 3600},
    {"sync_ms", OFF(sync_ms), 1000, 100, 60000},
    {"block_ms", OFF(block_ms), 250, 20, 5000},
    {"prealloc_mb", OFF(prealloc_mb), 16, 0, 512},
    {"stall_pre_s", OFF(stall_pre_s), -1, -1, 120},
    {"stall_post_s", OFF(stall_post_s), -1, -1, 120},
    {"capture_files", OFF(capture_files), 1, 0, 1},
    {"sleep_after_s", OFF(sleep_after_s), 300, 0, 86400},
    {"sleep_probe_s", OFF(sleep_probe_s), 20, 2, 3600},
    {"usb_latency_ms", OFF(usb_latency_ms), 1, 1, 255},
    {"utc_offset_min", OFF(utc_offset_min), 0, -840, 840},
    {"serial", OFF(serial), 1, 0, 1},
    {"serial_baud", OFF(serial_baud), 921600, 9600, 2000000},
    {"rotate", OFF(rotate), HH_ROTATE_DEFAULT, 0, 180},
};
#define N_KEYS (sizeof(KEYS) / sizeof(KEYS[0]))

static int32_t *field(hh_config_t *c, size_t off) { return (int32_t *)(void *)((char *)c + off); }

void hh_config_default(hh_config_t *c)
{
    memset(c, 0, sizeof(*c));
    for (size_t i = 0; i < N_KEYS; i++) *field(c, KEYS[i].off) = KEYS[i].def;
}

static const char *const BACKENDS[] = {"auto", "usb", "uart"};

static int set_key(hh_config_t *c, const char *key, const char *val)
{
    for (size_t i = 0; i < N_KEYS; i++) {
        if (strcmp(key, KEYS[i].key) != 0) continue;
        long v;
        char *endp;
        if (KEYS[i].off == OFF(backend)) {
            for (v = 0; v < 3; v++) if (strcmp(val, BACKENDS[v]) == 0) break;
            if (v == 3) return -1;
        } else {
            v = strtol(val, &endp, 10);
            if (endp == val || *endp) return -1;
        }
        if (v < KEYS[i].lo || v > KEYS[i].hi) return -1;
        *field(c, KEYS[i].off) = (int32_t)v;
        return 0;
    }
    return -1;
}

int hh_config_parse(hh_config_t *c, const char *text, size_t len)
{
    int bad = 0;
    size_t pos = 0;
    while (pos < len) {
        char line[96];
        size_t n = 0;
        while (pos < len && text[pos] != '\n') {
            if (n < sizeof(line) - 1) line[n++] = text[pos];
            pos++;
        }
        pos++;
        line[n] = 0;
        char *hash = strchr(line, '#');
        if (hash) *hash = 0;
        char *eq = strchr(line, '=');
        char *key = line;
        while (*key == ' ' || *key == '\t' || *key == '\r') key++;
        if (!*key) continue;
        if (!eq) { bad++; continue; }
        *eq = 0;
        char *val = eq + 1;
        for (char *e = eq - 1; e >= key && (*e == ' ' || *e == '\t'); e--) *e = 0;
        while (*val == ' ' || *val == '\t') val++;
        for (char *e = val + strlen(val) - 1; e >= val && (*e == ' ' || *e == '\t' || *e == '\r'); e--) *e = 0;
        if (set_key(c, key, val) != 0) bad++;
    }
    return bad;
}

size_t hh_config_dump(const hh_config_t *c, char *out, size_t cap)
{
    size_t w = 0;
    for (size_t i = 0; i < N_KEYS && w < cap; i++) {
        int32_t v = *field((hh_config_t *)(uintptr_t)c, KEYS[i].off);
        int n;
        if (KEYS[i].off == OFF(backend)) n = snprintf(out + w, cap - w, "%s = %s\n", KEYS[i].key, BACKENDS[v % 3]);
        else n = snprintf(out + w, cap - w, "%s = %ld\n", KEYS[i].key, (long)v);
        if (n < 0 || (size_t)n >= cap - w) break;
        w += (size_t)n;
    }
    return w;
}
