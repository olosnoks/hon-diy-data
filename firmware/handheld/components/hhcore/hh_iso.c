/* hhcore: ISO 9141-2 on the K-line (hondalog/obd2.py: Iso9141Link), the
 * read-only part: 5-baud init by break toggling, Mode 01 live PIDs and
 * Mode 03 stored trouble codes. There is no Mode 04 here and hh_iso_allowed
 * would not let one out. The KWP2000 fast init of the laptop code is left
 * out: a 2000 Civic answers the 5-baud init or nothing. */
#include <string.h>

#include "hhcore.h"

#define INIT_ADDRESS 0x33
#define BIT_S        0.2        /* one bit at 5 baud */
#define W5_S         0.3        /* bus idle before an init */
#define P3_MIN_S     0.055      /* end of reply -> next request */
#define P3_MAX_S     5.0        /* session timeout */

static double now(hh_iso_t *i) { return i->clk.now(i->clk.ctx); }
static void nap(hh_iso_t *i, double s) { if (s > 0) i->clk.sleep(i->clk.ctx, s); }
static void sleep_until(hh_iso_t *i, double t) { nap(i, t - now(i)); }

void hh_iso_init(hh_iso_t *iso, hh_transport_t *tr, const hh_clock_t *clk)
{
    memset(iso, 0, sizeof(*iso));
    iso->tr = tr;
    iso->clk = *clk;
}

static uint8_t sum8(const uint8_t *d, size_t n)
{
    unsigned s = 0;
    while (n--) s += *d++;
    return (uint8_t)s;
}

int hh_iso_allowed(const uint8_t *f, size_t len)
{
    if (len < 5 || f[0] != 0x68 || f[1] != 0x6A || f[2] != 0xF1 || sum8(f, len - 1) != f[len - 1]) return 0;
    if (f[3] == 0x01) return len == 6;      /* Mode 01 <pid> */
    if (f[3] == 0x03) return len == 5;      /* Mode 03 */
    return 0;
}

/* 1 = a byte, 0 = timeout, <0 = port lost */
static int read_byte(hh_iso_t *iso, double timeout, uint8_t *b)
{
    return iso->tr->read_some(iso->tr->ctx, b, 1, timeout > 0 ? timeout : 0.0);
}

int hh_iso_slow_init(hh_iso_t *iso)
{
    hh_transport_t *t = iso->tr;
    uint8_t b = 0, kb1, kb2;
    int rc, low = 0, garbage = 0;

    iso->up = 0;
    if (t->set_break(t->ctx, 0) < 0 || t->set_baud(t->ctx, HH_ISO_BAUD) < 0) return HH_E_PORT;
    nap(iso, W5_S + 0.05);                      /* bus idle */
    t->flush_input(t->ctx);
    /* start bit, 8 data bits LSB first, stop bit: 200 ms each, by break on/off */
    double t0 = now(iso);
    for (int k = 0; k < 10; k++) {
        int bit = k == 0 ? 0 : k == 9 ? 1 : (INIT_ADDRESS >> (k - 1)) & 1;
        if ((bit == 0) != low) {
            sleep_until(iso, t0 + k * BIT_S);
            low = bit == 0;
            if (t->set_break(t->ctx, low) < 0) { t->set_break(t->ctx, 0); return HH_E_PORT; }
        }
    }
    if (low) t->set_break(t->ctx, 0);           /* never leave the line held low */
    double t_end = t0 + 10 * BIT_S;
    /* The breaks come back as framing garbage. Drop it late in the stop bit. */
    sleep_until(iso, t_end - 0.03);
    t->flush_input(t->ctx);

    double deadline = t_end + 0.3 + 0.2;        /* W1 max + margin */
    for (;;) {
        rc = read_byte(iso, deadline - now(iso), &b);
        if (rc < 0) return HH_E_PORT;
        if (rc == 0) return HH_E_INIT;          /* no sync byte: the ECU did not answer */
        if (b == 0x55) break;
        if (++garbage > 16) return HH_E_INIT;
    }
    if ((rc = read_byte(iso, 0.12, &kb1)) <= 0) return rc < 0 ? HH_E_PORT : HH_E_INIT;
    if ((rc = read_byte(iso, 0.12, &kb2)) <= 0) return rc < 0 ? HH_E_PORT : HH_E_INIT;
    nap(iso, 0.03);                             /* W4: 25-50 ms */
    b = (uint8_t)~kb2;                          /* the one byte of the handshake we send */
    if (t->write(t->ctx, &b, 1) < 0) return HH_E_PORT;
    uint8_t want = (uint8_t)~INIT_ADDRESS, last = 0;
    int seen = 0;
    deadline = now(iso) + 0.05 + 0.15;
    while (!(seen && last == want)) {
        rc = read_byte(iso, deadline - now(iso), &last);
        if (rc < 0) return HH_E_PORT;
        if (rc == 0) return HH_E_INIT;          /* key bytes seen, no confirmation */
        seen = 1;
    }
    iso->kb1 = kb1; iso->kb2 = kb2;
    iso->up = 1;
    iso->last_activity = now(iso);
    iso->next_tx = iso->last_activity + P3_MIN_S + 2 * t->usb_latency_s;
    return HH_OK;
}

void hh_iso_close(hh_iso_t *iso, uint32_t honda_baud)
{
    /* There is no "stop" in ISO 9141-2: the ECU ends the session by itself
     * after 5 s without a request. */
    iso->up = 0;
    iso->tr->set_break(iso->tr->ctx, 0);
    iso->tr->set_baud(iso->tr->ctx, honda_baud);
    iso->tr->flush_input(iso->tr->ctx);
}

int hh_iso_pid_len(uint8_t pid)
{
    static const uint8_t len[0x22] = {4, 4, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 2, 1, 1, 1,
                                      2, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 2, 4, 2};
    if (pid < sizeof(len)) return len[pid];
    return pid == 0x40 ? 4 : 0;
}

/* Total length a message starting at buf[i] most likely has, 0 = no idea. */
static size_t likely_len(const uint8_t *buf, size_t n, size_t i)
{
    if (i + 4 > n) return 0;
    uint8_t mode = buf[i + 3];
    if (mode == 0x43 || mode == 0x47) return 11;
    if (mode == 0x44) return 5;
    if (mode == 0x41 && i + 5 <= n && hh_iso_pid_len(buf[i + 4])) return 3u + 2u + (size_t)hh_iso_pid_len(buf[i + 4]) + 1u;
    return 0;
}

int hh_iso_split(const uint8_t *buf, size_t n, void (*fn)(void *ctx, const uint8_t *payload, size_t len), void *ctx)
{
    int found = 0;
    /* The messages carry no length: the cuts are found by checksum, trying
     * the length the mode and PID imply first. Damaged bytes are skipped. */
    for (size_t i = 0; i + 5 <= n;) {
        size_t hit = 0;
        if (buf[i] == 0x48 && buf[i + 1] == 0x6B) {
            size_t likely = likely_len(buf, n, i);
            for (int k = -1; k < 7 && !hit; k++) {
                size_t len = k < 0 ? likely : (size_t)(5 + k);
                if (!len || (k >= 0 && len == likely)) continue;
                if (i + len <= n && sum8(buf + i, len - 1) == buf[i + len - 1]) hit = len;
            }
        }
        if (hit) {
            if (fn) fn(ctx, buf + i + 3, hit - 4);
            found++;
            i += hit;
        } else {
            i++;
        }
    }
    return found;
}

/* Send one request; collect until the ECU has been quiet for longer than
 * P2max. Returns the bytes after our own echo, in buf. */
static int request(hh_iso_t *iso, const uint8_t *payload, size_t plen, uint8_t *buf, size_t cap)
{
    hh_transport_t *t = iso->tr;
    uint8_t frame[8];
    size_t flen = 3 + plen + 1, n = 0;
    if (!iso->up) return HH_E_INIT;
    if (now(iso) - iso->last_activity > P3_MAX_S - 0.5) { iso->up = 0; return HH_E_INIT; }  /* gone stale */
    frame[0] = 0x68; frame[1] = 0x6A; frame[2] = 0xF1;
    memcpy(frame + 3, payload, plen);
    frame[flen - 1] = sum8(frame, flen - 1);
    if (!hh_iso_allowed(frame, flen)) { iso->blocked++; return HH_E_BLOCKED; }
    nap(iso, iso->next_tx - now(iso));
    t->flush_input(t->ctx);
    if (t->write(t->ctx, frame, flen) < 0) return HH_E_PORT;
    iso->requests++;
    double quiet = 0.06 + 2 * t->usb_latency_s;
    double deadline = now(iso) + (double)flen * 10.0 / HH_ISO_BAUD + 0.25;
    while (n < cap) {
        double left = deadline - now(iso);
        if (left <= 0) break;
        int got = t->read_some(t->ctx, buf + n, cap - n, left);
        if (got < 0) return HH_E_PORT;
        if (got > 0) {
            n += (size_t)got;
            if (n > flen) deadline = now(iso) + quiet;
        }
    }
    iso->next_tx = now(iso);        /* the quiet wait above already covers P3min */
    if (n >= flen && memcmp(buf, frame, flen) == 0) {
        memmove(buf, buf + flen, n - flen);
        n -= flen;
    }
    return (int)n;
}

typedef struct { uint8_t mode, pid; uint8_t *out; size_t max; int n; uint16_t *codes; int max_codes, n_codes, seen; } pick_t;

static void pick(void *ctx, const uint8_t *p, size_t len)
{
    pick_t *k = ctx;
    if (!len || p[0] != k->mode) return;
    k->seen = 1;
    if (k->mode == 0x41) {
        if (len < 2 || p[1] != k->pid || k->n) return;
        size_t n = len - 2 < k->max ? len - 2 : k->max;
        memcpy(k->out, p + 2, n);
        k->n = (int)n;
    } else {
        for (size_t i = 1; i + 1 < len; i += 2) {
            uint16_t code = (uint16_t)((p[i] << 8) | p[i + 1]);
            if (code && k->n_codes < k->max_codes) k->codes[k->n_codes++] = code;
        }
    }
}

int hh_iso_read_pid(hh_iso_t *iso, uint8_t pid, uint8_t *data, size_t max)
{
    uint8_t payload[2] = {0x01, pid}, buf[96];
    for (int attempt = 0; attempt < 2; attempt++) {
        int n = request(iso, payload, 2, buf, sizeof(buf));
        if (n < 0) return n;
        pick_t k = {0x41, pid, data, max, 0, NULL, 0, 0, 0};
        hh_iso_split(buf, (size_t)n, pick, &k);
        if (k.n) { iso->last_activity = now(iso); return k.n; }
    }
    return 0;
}

int hh_iso_read_dtcs(hh_iso_t *iso, uint16_t *codes, int max)
{
    uint8_t payload[1] = {0x03}, buf[128];
    for (int attempt = 0; attempt < 2; attempt++) {
        int n = request(iso, payload, 1, buf, sizeof(buf));
        if (n < 0) return n;
        pick_t k = {0x43, 0, NULL, 0, 0, codes, max, 0, 0};
        hh_iso_split(buf, (size_t)n, pick, &k);
        if (k.seen) { iso->last_activity = now(iso); return k.n_codes; }
    }
    return HH_E_NO_REPLY;
}
