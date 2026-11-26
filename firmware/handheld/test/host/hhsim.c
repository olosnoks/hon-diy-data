#include <string.h>

#include "hhsim.h"

hhsim_t SIM;

#define QMAX 512
static struct { double at; uint8_t b; } q[QMAX];
static int q_head, q_n;
static uint32_t baud = 9600;
static int is_open;
/* 5-baud init decoding */
static double brk_t[24]; static int brk_lvl[24], brk_n, brk_now;
static int iso_stage;       /* 0 none, 1 waiting for the inverted key byte, 2 session up */
static double iso_last;

static double byte_s(void) { return 10.0 / baud; }

static uint32_t rnd(void)
{
    SIM.rng = SIM.rng * 1664525u + 1013904223u;
    return SIM.rng >> 8;
}
static int chance(double p) { return p > 0 && (rnd() % 100000) < p * 100000; }

static double q_tail_time(void)
{
    return q_n ? q[(q_head + q_n - 1) % QMAX].at : 0;
}

static double push(double at, const uint8_t *d, size_t n)
{
    for (size_t i = 0; i < n && q_n < QMAX; i++) {
        at += byte_s();
        q[(q_head + q_n++) % QMAX].at = at;
        q[(q_head + q_n - 1) % QMAX].b = d[i];
    }
    return at;
}

void hhsim_set_rpm(double rpm)
{
    unsigned x = (unsigned)(rpm * 4);
    SIM.mem[0] = (uint8_t)(x >> 8); SIM.mem[1] = (uint8_t)x;
}

void hhsim_reset(void)
{
    memset(&SIM, 0, sizeof(SIM));
    for (int i = 0; i < 256; i++) SIM.mem[i] = (uint8_t)(i * 7 + 3);
    memcpy(SIM.mem + 0x76, "37820-P2P-", 10);
    SIM.honda = 1; SIM.echo = 1; SIM.powered = 1; SIM.port_ok = 1;
    SIM.turnaround_s = 0.002;
    /* a stock P2P, as on the 2000 Civic EX and in the built-in map: Honda
     * reads at 10400 baud, only after the wake-up bytes */
    SIM.honda_baud = 10400;
    SIM.need_wake = 1;
    SIM.rng = 12345;
    SIM.mem[0x17] = 144;        /* 13.8 V */
    SIM.mem[0x10] = 40;         /* warm coolant */
    hhsim_set_rpm(800);
    q_head = q_n = 0; baud = 9600; is_open = 0; brk_n = 0; brk_now = 0; iso_stage = 0;
}

static int s_open(void) { SIM.opens++; if (!SIM.port_ok) return -1; is_open = 1; baud = 9600; return 0; }
static void s_close(void) { is_open = 0; q_head = q_n = 0; }
static void s_flush(void) { while (q_n && q[q_head].at <= SIM.now) { q_head = (q_head + 1) % QMAX; q_n--; } }
static int s_baud(uint32_t b) { if (!SIM.port_ok) return -1; baud = b; return 0; }
static double s_now(void) { return SIM.now; }
static void s_sleep(double s) { if (s > 0) SIM.now += s; }

static uint8_t sum8(const uint8_t *d, size_t n) { unsigned s = 0; while (n--) s += *d++; return (uint8_t)s; }

static void honda_reply(const uint8_t *req, double tx_end)
{
    hh_framing_t f;
    uint8_t out[80], junk[6] = {0xFF, 0x00, 0x13, 0x55, 0xAA, 0x20};
    hh_framing_default(&f);
    uint8_t addr = req[2], len = req[3];
    if (len > 64) return;
    uint8_t data[64];
    for (int i = 0; i < len; i++) data[i] = SIM.mem[(addr + i) & 0xFF];
    size_t n = hh_build_reply(&f, data, len, out);
    double at = tx_end + SIM.turnaround_s;
    if (SIM.drop_next || chance(SIM.p_drop)) { SIM.drop_next = 0; return; }
    if (SIM.late_next_s > 0) { at += SIM.late_next_s; SIM.late_next_s = 0; }
    if (SIM.junk_before_next || chance(SIM.p_junk)) { SIM.junk_before_next = 0; at = push(at, junk, 1 + rnd() % 4); }
    if (SIM.corrupt_next || chance(SIM.p_corrupt)) { SIM.corrupt_next = 0; out[2 + rnd() % len] ^= 0x10; }
    if (SIM.truncate_next || chance(SIM.p_truncate)) { SIM.truncate_next = 0; n = 1 + rnd() % (n - 1); }
    at = push(at, out, n);
    if (SIM.junk_after_next) { SIM.junk_after_next = 0; push(at, junk, 2); }
}

static void iso_reply(const uint8_t *req, size_t n, double tx_end)
{
    uint8_t out[16] = {0x48, 0x6B, 0x10};
    size_t len = 0;
    double at = tx_end + 0.03;
    if (req[3] == 0x04) { SIM.mode4_seen++; return; }
    if (req[3] == 0x01 && n == 6) {
        uint8_t pid = req[4];
        if (pid >= 1 && pid <= 32) SIM.pid_seen |= 1u << (pid - 1);
        out[3] = 0x41; out[4] = pid;
        switch (pid) {
        case 0x00: out[5] = 0xBE; out[6] = 0x3E; out[7] = 0xB8; out[8] = 0x10; len = 9; break;
        case 0x0C: out[5] = SIM.mem[0]; out[6] = SIM.mem[1]; len = 7; break;    /* same scaling: x / 4 */
        case 0x0D: out[5] = 42; len = 6; break;
        case 0x05: out[5] = 90 + 40; len = 6; break;
        case 0x0F: out[5] = 25 + 40; len = 6; break;
        case 0x0B: out[5] = 35; len = 6; break;
        case 0x11: out[5] = 51; len = 6; break;
        case 0x06: case 0x07: out[5] = 128; len = 6; break;
        case 0x0E: out[5] = 148; len = 6; break;
        case 0x14: out[5] = 90; out[6] = 128; len = 7; break;
        case 0x15: out[5] = 140; out[6] = 0xFF; len = 7; break;          /* 0.70 V downstream */
        case 0x01: out[5] = 0x82; out[6] = 0x07; out[7] = 0x65; out[8] = 0x00; len = 9; break;  /* MIL on, 2 codes */
        case 0x03: out[5] = SIM.closed_loop ? 0x02 : 0x01; out[6] = 0; len = 7; break;
        case 0x04: out[5] = 102; len = 6; break;                         /* 40 % */
        default: return;        /* not supported: silence */
        }
    } else if (req[3] == 0x03 && n == 5) {
        out[3] = 0x43;
        for (int i = 0; i < 3; i++) { out[4 + 2 * i] = (uint8_t)(SIM.dtc[i] >> 8); out[5 + 2 * i] = (uint8_t)SIM.dtc[i]; }
        len = 10;
    } else {
        return;
    }
    out[len] = sum8(out, len);
    push(at, out, len + 1);
    iso_last = at;
}

static int s_write(const uint8_t *d, size_t n)
{
    static const uint8_t wake[11] = {0x68, 0x6a, 0xf5, 0xaf, 0xbf, 0xb3, 0xb2, 0xc1, 0xdb, 0xb3, 0xe9};
    if (!SIM.port_ok || !is_open) return -1;
    double start = SIM.now > q_tail_time() ? SIM.now : q_tail_time();
    double tx_end = start + n * byte_s();
    if (SIM.echo) push(start, d, n);
    SIM.requests++;
    if (!SIM.powered) SIM.woken = 0;
    if (iso_stage != 1 && baud == SIM.honda_baud) {
        hh_framing_t f;
        hh_framing_default(&f);
        if (n == 11 && memcmp(d, wake, 11) == 0) { SIM.woken = SIM.powered; SIM.wakes++; return 0; }
        if (hh_frame_allowed(&f, d, n)) {
            /* the ECU keeps an ISO session for 5 s of silence and ignores Honda reads meanwhile */
            if (iso_stage == 2 && SIM.now - iso_last < 5.0) return 0;
            iso_stage = 0;
            if (SIM.powered && SIM.honda && (SIM.woken || !SIM.need_wake)) honda_reply(d, tx_end);
            else SIM.honda_ignored++;
            return 0;
        }
        if (baud != 10400) { SIM.bad_frames_sent++; return 0; }
    }
    if (baud != 10400) { SIM.bad_frames_sent++; return 0; }      /* the wrong baud rate for anything */
    if (iso_stage == 1 && n == 1) {                 /* inverted key byte 2 */
        uint8_t cc = 0xCC;
        if (d[0] == (uint8_t)~0x08) { push(tx_end + 0.03, &cc, 1); iso_stage = 2; iso_last = tx_end; }
        return 0;
    }
    if (!hh_iso_allowed(d, n)) {
        SIM.bad_frames_sent++;
        if (n >= 4 && d[3] == 0x04) SIM.mode4_seen++;
        return 0;
    }
    if (iso_stage == 2 && SIM.powered && SIM.iso && SIM.now - iso_last < 5.0) iso_reply(d, n, tx_end);
    return 0;
}

static int s_break(int on)
{
    if (!SIM.port_ok) return -1;
    if (on == brk_now) return 0;
    brk_now = on;
    if (brk_n && SIM.now - brk_t[0] > 2.5) brk_n = 0;      /* an old, unfinished pattern */
    if (brk_n < 24) { brk_t[brk_n] = SIM.now; brk_lvl[brk_n++] = on; }
    if (on || brk_n < 2) return 0;
    /* line released: is this the end of 0x33 at 5 baud? sample each 200 ms bit in its middle */
    double t0 = brk_t[0];
    if (SIM.now - t0 < 1.5) return 0;
    unsigned value = 0;
    for (int bit = 0; bit < 10; bit++) {
        double mid = t0 + (bit + 0.5) * 0.2;
        int low = 0;
        for (int k = 0; k < brk_n && brk_t[k] <= mid; k++) low = brk_lvl[k];
        if (!low) value |= 1u << bit;
    }
    brk_n = 0;
    /* bit 0 is the start bit (0), bits 1..8 the data LSB first, bit 9 the stop bit (1) */
    if ((value & 1) || !(value & 0x200) || ((value >> 1) & 0xFF) != 0x33) return 0;
    SIM.iso_inits++;
    if (SIM.powered && SIM.iso && baud == 10400) {
        static const uint8_t hello[3] = {0x55, 0x08, 0x08};
        double t_end = t0 + 2.0;
        q_head = q_n = 0;
        push(t_end + 0.1, hello, 3);
        iso_stage = 1;
    }
    return 0;
}

static int s_read(uint8_t *buf, size_t max, double timeout)
{
    if (!SIM.port_ok || !is_open) return -1;
    if (!q_n || q[q_head].at > SIM.now + timeout) {
        if (timeout > 0) SIM.now += timeout;
        return 0;
    }
    if (q[q_head].at > SIM.now) SIM.now = q[q_head].at;
    int n = 0;
    while (q_n && (size_t)n < max && q[q_head].at <= SIM.now) {
        buf[n++] = q[q_head].b;
        q_head = (q_head + 1) % QMAX; q_n--;
    }
    return n;
}

const hhhost_cb_t *hhsim_callbacks(void)
{
    static const hhhost_cb_t cb = {s_open, s_close, s_write, s_read, s_flush, s_baud, s_break, s_now, s_sleep, 0.001};
    return &cb;
}
