/* bbcore: checksums, COBS framing, config text, edge timing, CSV, NMEA, LED. */
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bbcore.h"

/* ---- checksums ---------------------------------------------------------- */

uint32_t bb_crc32(uint32_t crc, const void *data, size_t len)
{
    static uint32_t table[16];
    const uint8_t *p = data;
    if (!table[1]) {
        for (uint32_t i = 0; i < 16; i++) {
            uint32_t c = i;
            for (int k = 0; k < 4; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            table[i] = c;
        }
    }
    crc = ~crc;
    while (len--) {
        crc ^= *p++;
        crc = table[crc & 15] ^ (crc >> 4);
        crc = table[crc & 15] ^ (crc >> 4);
    }
    return ~crc;
}

uint16_t bb_crc16(const void *data, size_t len)
{
    const uint8_t *p = data;
    uint16_t crc = 0xFFFF;
    while (len--) {
        crc ^= (uint16_t)(*p++) << 8;
        for (int k = 0; k < 8; k++) crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}

/* ---- COBS and frames ---------------------------------------------------- */

size_t bb_cobs_encode(const uint8_t *in, size_t len, uint8_t *out)
{
    size_t code_at = 0, w = 1;
    uint8_t code = 1;
    for (size_t i = 0; i < len; i++) {
        if (in[i] == 0) {
            out[code_at] = code; code_at = w++; code = 1;
        } else {
            out[w++] = in[i];
            if (++code == 0xFF) { out[code_at] = code; code_at = w++; code = 1; }
        }
    }
    out[code_at] = code;
    return w;
}

size_t bb_cobs_decode(const uint8_t *in, size_t len, uint8_t *out)
{
    size_t r = 0, w = 0;
    while (r < len) {
        uint8_t code = in[r++];
        if (code == 0 || r + code - 1 > len) return 0;
        for (uint8_t i = 1; i < code; i++) out[w++] = in[r++];
        if (code != 0xFF && r < len) out[w++] = 0;
    }
    return w;
}

size_t bb_frame_encode(uint8_t type, uint8_t seq, const void *payload, size_t len, uint8_t *wire)
{
    uint8_t raw[BB_FRAME_MAX_PAYLOAD + 5];
    if (len > BB_FRAME_MAX_PAYLOAD) return 0;
    raw[0] = BB_PROTO_VERSION; raw[1] = type; raw[2] = seq;
    if (len) memcpy(raw + 3, payload, len);
    uint16_t crc = bb_crc16(raw, len + 3);
    raw[len + 3] = (uint8_t)crc; raw[len + 4] = (uint8_t)(crc >> 8);
    size_t n = bb_cobs_encode(raw, len + 5, wire);
    wire[n++] = 0;
    return n;
}

int bb_deframe(bb_deframer_t *d, uint8_t byte, uint8_t *type, uint8_t *seq,
               const uint8_t **payload, size_t *len)
{
    if (byte != 0) {
        if (d->n < sizeof(d->buf)) d->buf[d->n++] = byte; else d->overflow = 1;
        return 0;
    }
    size_t n = d->n;
    int bad = d->overflow;
    d->n = 0; d->overflow = 0;
    if (n == 0 && !bad) return 0;               /* idle delimiter */
    if (!bad) {
        n = bb_cobs_decode(d->buf, n, d->buf);  /* in place: output never passes input */
        if (n < 5 || d->buf[0] != BB_PROTO_VERSION) bad = 1;
        else {
            uint16_t crc = (uint16_t)(d->buf[n - 2] | (d->buf[n - 1] << 8));
            if (crc != bb_crc16(d->buf, n - 2)) bad = 1;
        }
    }
    if (bad) { d->bad++; return 0; }
    *type = d->buf[1]; *seq = d->buf[2];
    *payload = d->buf + 3; *len = n - 5;
    return 1;
}

/* ---- configuration ------------------------------------------------------ */

#define OFF(f) offsetof(bb_config_t, f)
static const struct { const char *key; size_t off; int32_t def, lo, hi; } KEYS[] = {
    {"pre_s", OFF(pre_s), 30, 1, 120},
    {"post_s", OFF(post_s), 10, 1, 60},
    {"vbat_scale", OFF(scale_milli[BB_A_VBAT]), 7912, 1, 100000},
    {"ecub_scale", OFF(scale_milli[BB_A_ECUB]), 7912, 1, 100000},
    {"pump_scale", OFF(scale_milli[BB_A_PUMP]), 7912, 1, 100000},
    {"ig1_scale", OFF(scale_milli[BB_A_IG1]), 7912, 1, 100000},
    {"alt_scale", OFF(scale_milli[BB_A_ALT]), 7912, 1, 100000},
    {"vbat_offset_mv", OFF(offset_mv[BB_A_VBAT]), 0, -5000, 5000},
    {"ecub_offset_mv", OFF(offset_mv[BB_A_ECUB]), 0, -5000, 5000},
    {"pump_offset_mv", OFF(offset_mv[BB_A_PUMP]), 0, -5000, 5000},
    {"ig1_offset_mv", OFF(offset_mv[BB_A_IG1]), 0, -5000, 5000},
    {"alt_offset_mv", OFF(offset_mv[BB_A_ALT]), 0, -5000, 5000},
    {"tach_invert", OFF(invert[BB_E_TACH]), 1, 0, 1},
    {"ign_invert", OFF(invert[BB_E_IGN]), 1, 0, 1},
    {"inj_invert", OFF(invert[BB_E_INJ]), 1, 0, 1},
    {"tach_ppr", OFF(tach_ppr), 2, 1, 60},
    {"ign_ppr", OFF(ign_ppr), 2, 1, 60},
    {"edge_min_us", OFF(edge_min_us), 300, 0, 100000},
    {"run_rpm", OFF(run_rpm), 400, 50, 5000},
    {"run_ms", OFF(run_ms), 500, 1, 60000},
    {"pulse_timeout_ms", OFF(pulse_timeout_ms), 300, 20, 5000},
    {"ig1_on_mv", OFF(ig1_on_mv), 8000, 0, 60000},
    {"ig1_off_mv", OFF(ig1_off_mv), 6000, 0, 60000},
    {"ig1_debounce_ms", OFF(ig1_debounce_ms), 10, 1, 5000},
    {"holdoff_s", OFF(holdoff_s), 5, 0, 3600},
    {"host_mark_triggers", OFF(host_mark_triggers), 0, 0, 1},
    {"live_hz", OFF(live_hz), 50, 1, 500},
    {"summary_s", OFF(summary_s), 1, 1, 3600},
    {"csv_export", OFF(csv_export), 1, 0, 1},
    {"sleep_after_s", OFF(sleep_after_s), 300, 0, 86400},
    {"gps_baud", OFF(gps_baud), 0, 0, 115200},
    {"alt_enable", OFF(alt_enable), 0, 0, 1},
    {"edge_ring", OFF(edge_ring), 32768, 1024, 1000000},
};
#define N_KEYS (sizeof(KEYS) / sizeof(KEYS[0]))
#define FIELD(c, i) (*(int32_t *)((char *)(c) + KEYS[i].off))

const char *const bb_source_names[BB_N_SOURCES] = {
    "vbat", "ecub", "pump", "ig1", "alt",
    "slew_vbat", "slew_ecub", "slew_pump", "slew_ig1", "slew_alt",
    "tach_age", "ign_age", "inj_age", "rpm_tach", "rpm_ign", "inj_pw", "inj_duty", "button",
};
static const char *const QUAL_NAMES[] = {"ig1", "running", "ign_running", "tach_alive"};
static const char *const ACT_NAMES[] = {"off", "mark", "capture"};

static const struct { uint8_t code; bb_rule_t r; } DEFAULT_RULES[] = {
    {BB_R_TACH_STOP, {BB_ACT_CAPTURE, BB_S_TACH_AGE, BB_OP_ABOVE, BB_Q_IG1 | BB_Q_RUNNING, 250, 150, 1}},
    {BB_R_IGN_STOP,  {BB_ACT_CAPTURE, BB_S_IGN_AGE, BB_OP_ABOVE, BB_Q_IG1 | BB_Q_IGN_RUNNING, 250, 150, 1}},
    {BB_R_IG1_DROP,  {BB_ACT_CAPTURE, BB_S_IG1, BB_OP_BELOW, BB_Q_RUNNING, 6000, 2000, 10}},
    {BB_R_ECUB_DROP, {BB_ACT_CAPTURE, BB_S_ECUB, BB_OP_BELOW, BB_Q_IG1, 6000, 1000, 10}},
    {BB_R_PUMP_DROP, {BB_ACT_CAPTURE, BB_S_PUMP, BB_OP_BELOW, BB_Q_IG1 | BB_Q_RUNNING, 6000, 1000, 20}},
    {BB_R_VBAT_DIP,  {BB_ACT_CAPTURE, BB_S_VBAT, BB_OP_BELOW, BB_Q_RUNNING, 9000, 500, 20}},
    {BB_R_BUTTON,    {BB_ACT_CAPTURE, BB_S_BUTTON, BB_OP_ABOVE, 0, 0, 0, 30}},
    {BB_R_BROWNOUT,  {BB_ACT_CAPTURE, BB_S_VBAT, BB_OP_BELOW, 0, 6500, 1000, 3}},
    {BB_R_INJ_STOP,  {BB_ACT_MARK, BB_S_INJ_AGE, BB_OP_ABOVE, BB_Q_IG1 | BB_Q_RUNNING | BB_Q_TACH_ALIVE, 2000, 1500, 1}},
    {BB_R_ECUB_GLITCH, {BB_ACT_CAPTURE, BB_S_SLEW_ECUB, BB_OP_BELOW, BB_Q_IG1 | BB_Q_RUNNING, -3000, 2000, 1}},
};

void bb_config_defaults(bb_config_t *c)
{
    memset(c, 0, sizeof(*c));
    for (size_t i = 0; i < N_KEYS; i++) FIELD(c, i) = KEYS[i].def;
    for (size_t i = 0; i < sizeof(DEFAULT_RULES) / sizeof(DEFAULT_RULES[0]); i++)
        c->rule[DEFAULT_RULES[i].code] = DEFAULT_RULES[i].r;
}

static int lookup(const char *const *names, int n, const char *s, size_t len)
{
    for (int i = 0; i < n; i++)
        if (strlen(names[i]) == len && memcmp(names[i], s, len) == 0) return i;
    return -1;
}

/* "<source> <op> <level> <hyst> <hold_ms> <qual[+qual]|-> <action>" */
static int rule_parse(bb_rule_t *out, const char *v)
{
    char buf[80], *tok[7];
    int n = 0;
    strncpy(buf, v, sizeof(buf) - 1); buf[sizeof(buf) - 1] = 0;
    for (char *p = buf; n < 7; ) {
        while (*p == ' ' || *p == '\t') p++;
        if (!*p) break;
        tok[n++] = p;
        while (*p && *p != ' ' && *p != '\t' && *p != '\r') p++;
        if (*p) *p++ = 0;
    }
    if (n == 1 && strcmp(tok[0], "off") == 0) { out->action = BB_ACT_OFF; return 0; }
    if (n != 7) return -2;
    bb_rule_t r;
    memset(&r, 0, sizeof(r));
    int src = lookup(bb_source_names, BB_N_SOURCES, tok[0], strlen(tok[0]));
    int act = lookup(ACT_NAMES, 3, tok[6], strlen(tok[6]));
    if (src < 0 || act < 0) return -2;
    if (strcmp(tok[1], "<") == 0) r.op = BB_OP_BELOW;
    else if (strcmp(tok[1], ">") == 0) r.op = BB_OP_ABOVE;
    else return -2;
    char *end;
    long level = strtol(tok[2], &end, 0); if (*end || end == tok[2]) return -2;
    long hyst = strtol(tok[3], &end, 0);  if (*end || end == tok[3] || hyst < 0) return -2;
    long hold = strtol(tok[4], &end, 0);  if (*end || end == tok[4] || hold < 1 || hold > 60000) return -2;
    if (strcmp(tok[5], "-") != 0) {
        for (const char *q = tok[5]; ; ) {
            const char *plus = strchr(q, '+');
            size_t len = plus ? (size_t)(plus - q) : strlen(q);
            int qi = lookup(QUAL_NAMES, 4, q, len);
            if (qi < 0) return -2;
            r.qual |= (uint8_t)(1 << qi);
            if (!plus) break;
            q = plus + 1;
        }
    }
    r.action = (uint8_t)act; r.source = (uint8_t)src;
    r.level = (int32_t)level; r.hyst = (int32_t)hyst; r.hold_ms = (uint16_t)hold;
    *out = r;
    return 0;
}

static size_t rule_dump(const bb_rule_t *r, int code, char *out, size_t cap)
{
    char q[48] = "-";
    size_t w = 0;
    for (int i = 0; i < 4; i++)
        if (r->qual & (1 << i))
            w += (size_t)snprintf(q + w, sizeof(q) - w, "%s%s", w ? "+" : "", QUAL_NAMES[i]);
    return (size_t)snprintf(out, cap, "rule.%d = %s %c %ld %ld %u %s %s\n", code,
                            bb_source_names[r->source], r->op == BB_OP_BELOW ? '<' : '>',
                            (long)r->level, (long)r->hyst, r->hold_ms, q, ACT_NAMES[r->action]);
}

int bb_config_set(bb_config_t *c, const char *key, const char *value)
{
    if (strncmp(key, "rule.", 5) == 0) {
        char *end;
        long code = strtol(key + 5, &end, 10);
        if (*end || end == key + 5 || code < 1 || code >= BB_MAX_RULES || code == BB_R_HOST) return -1;
        return rule_parse(&c->rule[code], value);
    }
    for (size_t i = 0; i < N_KEYS; i++) {
        if (strcmp(key, KEYS[i].key) != 0) continue;
        char *end;
        long v = strtol(value, &end, 0);
        while (*end == ' ' || *end == '\t' || *end == '\r') end++;
        if (end == value || *end != 0 || v < KEYS[i].lo || v > KEYS[i].hi) return -2;
        FIELD(c, i) = (int32_t)v;
        return 0;
    }
    return -1;
}

int bb_config_parse(bb_config_t *c, const char *text, size_t len, int *bad_line)
{
    int bad = 0, line_no = 0;
    size_t i = 0;
    if (bad_line) *bad_line = 0;
    while (i < len) {
        char line[96];
        size_t n = 0;
        while (i < len && text[i] != '\n') { if (n < sizeof(line) - 1) line[n++] = text[i]; i++; }
        i++; line_no++;
        line[n] = 0;
        char *hash = strchr(line, '#');
        if (hash) *hash = 0;
        char *k = line;
        while (*k == ' ' || *k == '\t') k++;
        char *e = k + strlen(k);
        while (e > k && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r')) *--e = 0;
        if (!*k) continue;
        char *eq = strchr(k, '=');
        int rc = -1;
        if (eq) {
            char *ke = eq;
            while (ke > k && (ke[-1] == ' ' || ke[-1] == '\t')) ke--;
            *ke = 0;
            char *v = eq + 1;
            while (*v == ' ' || *v == '\t') v++;
            rc = bb_config_set(c, k, v);
        }
        if (rc != 0) { if (!bad && bad_line) *bad_line = line_no; bad++; }
    }
    return bad;
}

size_t bb_config_dump(const bb_config_t *c, char *out, size_t cap)
{
    size_t w = 0;
    for (size_t i = 0; i < N_KEYS && w + 48 < cap; i++)
        w += (size_t)snprintf(out + w, cap - w, "%s = %ld\n", KEYS[i].key, (long)FIELD(c, i));
    for (int code = 1; code < BB_MAX_RULES && w + 96 < cap; code++)
        if (c->rule[code].action != BB_ACT_OFF || c->rule[code].hold_ms)
            w += rule_dump(&c->rule[code], code, out + w, cap - w);
    return w;
}

/* ---- edge timing -------------------------------------------------------- */

void bb_edge_feed(bb_edge_ch_t *c, uint64_t t_us, int level, uint32_t min_us)
{
    level = level ? 1 : 0;
    if (level && !c->level) {
        if (c->seen) {
            uint64_t d = t_us - c->last_act_us;
            if (d < min_us) return;             /* glitch: keep the old reference */
            c->period_us = d > 0xFFFFFFFFu ? 0 : (uint32_t)d;
        }
        c->last_act_us = t_us;
        c->seen = 1;
    } else if (!level && c->level && c->seen) {
        uint64_t d = t_us - c->last_act_us;
        c->pw_us = d > 0xFFFFFFFFu ? 0xFFFFFFFFu : (uint32_t)d;
    }
    c->level = (uint8_t)level;
}

uint32_t bb_edge_rpm(const bb_edge_ch_t *c, uint64_t now_us, uint32_t ppr, uint32_t timeout_us)
{
    if (!c->seen || !c->period_us || !ppr) return 0;
    uint64_t age = now_us > c->last_act_us ? now_us - c->last_act_us : 0;
    if (age >= timeout_us) return 0;
    /* a late pulse can only mean the engine is slower than the last period says */
    uint64_t period = age > c->period_us ? age : c->period_us;
    uint64_t rpm = 60000000ull / (period * ppr);
    return rpm > 65535 ? 65535 : (uint32_t)rpm;
}

uint32_t bb_edge_duty200(const bb_edge_ch_t *c, uint64_t now_us, uint32_t timeout_us)
{
    if (!c->seen) return 0;
    uint64_t age = now_us > c->last_act_us ? now_us - c->last_act_us : 0;
    if (age >= timeout_us) return c->level ? 200 : 0;       /* stuck on / off */
    if (!c->period_us) return 0;
    uint64_t d = (uint64_t)c->pw_us * 200u / c->period_us;
    return d > 200 ? 200 : (uint32_t)d;
}

uint64_t bb_extend32(uint64_t hint, uint32_t low)
{
    int32_t diff = (int32_t)(low - (uint32_t)hint);
    return hint + (int64_t)diff;
}

/* ---- CSV ---------------------------------------------------------------- */

size_t bb_csv_header(char *out, size_t cap)
{
    return (size_t)snprintf(out, cap, "time_s,vbat_v,ecub_v,pump_v,ig1_v,alt_v,"
                            "vbat_min,ecub_min,pump_min,ig1_min,vbat_max,ecub_max,pump_max,ig1_max,"
                            "rpm_tach,rpm_ign,inj_pw_ms,inj_duty_pct,tach,ign,inj,button,ig1_on,"
                            "running,gap\n");
}

static size_t put_fixed(char *out, size_t cap, uint32_t v, unsigned div, unsigned digits)
{
    return (size_t)snprintf(out, cap, "%lu.%0*lu", (unsigned long)(v / div), (int)digits,
                            (unsigned long)(v % div));
}

size_t bb_csv_row(char *out, size_t cap, uint64_t t_us, const bb_sample_t *s)
{
    if (cap < 200) return 0;
    size_t w = (size_t)snprintf(out, cap, "%lu.%03lu", (unsigned long)(t_us / 1000000u),
                                (unsigned long)(t_us / 1000u % 1000u));
    for (int i = 0; i < BB_N_ANALOG; i++) {
        out[w++] = ',';
        w += put_fixed(out + w, cap - w, s->mv[i], 1000, 3);
    }
    for (int i = 0; i < 2 * BB_N_RAILS; i++) {
        out[w++] = ',';
        w += put_fixed(out + w, cap - w, i < BB_N_RAILS ? s->min_dv[i] : s->max_dv[i - BB_N_RAILS], 10, 1);
    }
    w += (size_t)snprintf(out + w, cap - w, ",%u,%u,", s->rpm_tach, s->rpm_ign);
    w += put_fixed(out + w, cap - w, s->inj_pw_us, 1000, 3);
    w += (size_t)snprintf(out + w, cap - w, ",%u.%u,%d,%d,%d,%d,%d,%d,%d\n", s->inj_duty / 2,
                          (s->inj_duty & 1) * 5, !!(s->dig & BB_DIG_TACH), !!(s->dig & BB_DIG_IGN),
                          !!(s->dig & BB_DIG_INJ), !!(s->dig & BB_DIG_BUTTON),
                          !!(s->dig & BB_DIG_IG1_ON), !!(s->dig & BB_DIG_RUNNING),
                          !!(s->dig & BB_DIG_GAP));
    return w;
}

/* ---- NMEA (RMC + GGA) --------------------------------------------------- */

static int32_t nmea_deg_e7(const char *f, const char *hemi)
{
    /* ddmm.mmmm or dddmm.mmmm */
    const char *dot = strchr(f, '.');
    size_t ilen = dot ? (size_t)(dot - f) : strlen(f);
    if (ilen < 3) return 0;
    long deg = 0;
    for (size_t i = 0; i + 2 < ilen; i++) deg = deg * 10 + (f[i] - '0');
    /* minutes in 1e-5 minute units */
    long min5 = ((f[ilen - 2] - '0') * 10 + (f[ilen - 1] - '0')) * 100000L;
    if (dot) {
        long scale = 10000;
        for (const char *p = dot + 1; *p >= '0' && *p <= '9' && scale; p++, scale /= 10)
            min5 += (*p - '0') * scale;
    }
    int64_t e7 = (int64_t)deg * 10000000 + (int64_t)min5 * 100 / 60;
    if (*hemi == 'S' || *hemi == 'W') e7 = -e7;
    return (int32_t)e7;
}

static uint32_t unix_from_ymdhms(int y, int mo, int d, int h, int mi, int s)
{
    /* days from civil, valid 2000..2099 */
    y -= mo <= 2;
    long era = y / 400, yoe = y - era * 400;
    long doy = (153 * (mo + (mo > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    long days = era * 146097 + doe - 719468;
    return (uint32_t)(days * 86400L + h * 3600L + mi * 60L + s);
}

static uint32_t tenths(const char *f, unsigned mul_num, unsigned mul_den)
{
    /* parse a decimal number, return value * 1000 * num / den */
    uint64_t v = 0, frac = 0, scale = 1;
    while (*f >= '0' && *f <= '9') v = v * 10 + (uint64_t)(*f++ - '0');
    if (*f == '.') for (f++; *f >= '0' && *f <= '9' && scale < 1000; f++) { frac = frac * 10 + (uint64_t)(*f - '0'); scale *= 10; }
    return (uint32_t)((v * 1000 + frac * 1000 / scale) * mul_num / mul_den);
}

int bb_nmea_feed(bb_nmea_t *g, char c)
{
    if (c == '$') { g->n = 0; g->line[g->n++] = c; return 0; }
    if (c != '\n' && c != '\r') {
        if (g->n && g->n < sizeof(g->line) - 1) g->line[g->n++] = c;
        return 0;
    }
    if (g->n < 8) { g->n = 0; return 0; }
    g->line[g->n] = 0;
    g->n = 0;
    char *star = strchr(g->line, '*');
    if (!star) return 0;
    uint8_t sum = 0;
    for (char *p = g->line + 1; p < star; p++) sum ^= (uint8_t)*p;
    /* exactly two hex digits after '*', nothing else */
    char *endp;
    if (strlen(star + 1) != 2 || !isxdigit((unsigned char)star[1]) || !isxdigit((unsigned char)star[2]))
        return 0;
    if ((uint8_t)strtol(star + 1, &endp, 16) != sum || *endp) return 0;
    *star = 0;
    char *f[16];
    int nf = 0;
    for (char *p = g->line; nf < 16; ) {
        f[nf++] = p;
        p = strchr(p, ',');
        if (!p) break;
        *p++ = 0;
    }
    const char *id = f[0] + 3;                  /* skip "$GP" / "$GN" */
    if (strlen(f[0]) < 6) return 0;
    if (strcmp(id, "GGA") == 0 && nf >= 8) {
        g->fix.sats = (uint8_t)atoi(f[7]);
        return 0;
    }
    if (strcmp(id, "RMC") != 0 || nf < 10) return 0;
    g->fix.fix = f[2][0] == 'A';
    if (g->fix.fix) {
        g->fix.lat_e7 = nmea_deg_e7(f[3], f[4]);
        g->fix.lon_e7 = nmea_deg_e7(f[5], f[6]);
        g->fix.speed_cms = (uint16_t)(tenths(f[7], 5144, 100000));     /* knots -> cm/s */
        g->fix.course_cdeg = (uint16_t)(tenths(f[8], 1, 10));
    }
    if (strlen(f[1]) >= 6 && strlen(f[9]) == 6) {
        #define D2(s, i) (((s)[i] - '0') * 10 + ((s)[(i) + 1] - '0'))
        g->fix.utc = unix_from_ymdhms(2000 + D2(f[9], 4), D2(f[9], 2), D2(f[9], 0),
                                      D2(f[1], 0), D2(f[1], 2), D2(f[1], 4));
        #undef D2
    }
    return 1;
}

/* ---- status LED --------------------------------------------------------- */

int bb_led_pattern(int state, uint32_t t_ms)
{
    uint32_t p = t_ms % 2000;
    switch (state) {
    case BB_LED_BOOT:    return 1;
    case BB_LED_IDLE:    return p < 60;                             /* one short blink / 2 s */
    case BB_LED_RUNNING: return p < 60 || (p >= 250 && p < 310);    /* double blink */
    case BB_LED_CAPTURE: return (t_ms % 100) < 50;                  /* 10 Hz flicker */
    case BB_LED_WAITING: return p < 60 || (p >= 250 && p < 310) || (p >= 500 && p < 560);  /* triple */
    case BB_LED_NO_SD:   return (t_ms % 1000) < 500;                /* slow even blink */
    default:             return (t_ms % 200) < 100;                 /* fault: 5 Hz */
    }
}
