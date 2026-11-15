/* hhcore: CRC, Honda K-line framing (hondalog/protocol.py) and the adaptive
 * timers (hondalog/link/timing.py). Pure functions, no I/O. */
#include <string.h>

#include "hhcore.h"

uint32_t hh_crc32(uint32_t crc, const void *data, size_t len)
{
    static const uint32_t table[16] = {
        0x00000000, 0x1DB71064, 0x3B6E20C8, 0x26D930AC, 0x76DC4190, 0x6B6B51F4, 0x4DB26158, 0x5005713C,
        0xEDB88320, 0xF00F9344, 0xD6D6A3E8, 0xCB61B38C, 0x9B64C2B0, 0x86D3D2D4, 0xA00AE278, 0xBDBDF21C,
    };
    const uint8_t *p = data;
    crc = ~crc;
    while (len--) {
        crc ^= *p++;
        crc = table[crc & 15] ^ (crc >> 4);
        crc = table[crc & 15] ^ (crc >> 4);
    }
    return ~crc;
}

/* ---- framing ------------------------------------------------------------- */

void hh_framing_default(hh_framing_t *f)
{
    static const uint8_t init[11] = {0x68, 0x6a, 0xf5, 0xaf, 0xbf, 0xb3, 0xb2, 0xc1, 0xdb, 0xb3, 0xe9};
    memset(f, 0, sizeof(*f));
    f->baud = 9600;
    f->cmd_read = 0x20;
    f->request_len = 5;
    f->reply_header = 0x00;
    f->reply_len_extra = 3;
    f->max_read_len = 16;
    f->init_len = sizeof(init);
    memcpy(f->init_seq, init, sizeof(init));
    f->init_delay_s = 0.3;
    f->reply_timeout_s = 0.2;
    f->inter_request_delay_s = 0.002;
    f->adaptive_timing = 1;
    f->min_reply_timeout_s = 0.02;
    f->inter_byte_timeout_s = 0.05;
    f->max_inter_request_delay_s = 0.05;
    f->retries = 1;
    f->turnaround_s = 0.002;
    f->rate_hz[0] = 20.0; f->rate_hz[1] = 5.0; f->rate_hz[2] = 1.0;
}

uint8_t hh_checksum(const uint8_t *data, size_t len)
{
    unsigned sum = 0;
    while (len--) sum += *data++;
    return (uint8_t)(0u - sum);
}

int hh_verify(const uint8_t *frame, size_t len)
{
    unsigned sum = 0;
    if (!len) return 0;
    for (size_t i = 0; i < len; i++) sum += frame[i];
    return (sum & 0xFF) == 0;
}

size_t hh_build_read_request(const hh_framing_t *f, uint8_t addr, uint8_t len, uint8_t out[HH_REQUEST_BYTES])
{
    out[0] = f->cmd_read; out[1] = f->request_len; out[2] = addr; out[3] = len;
    out[4] = hh_checksum(out, 4);
    return HH_REQUEST_BYTES;
}

size_t hh_build_reply(const hh_framing_t *f, const uint8_t *data, uint8_t len, uint8_t *out)
{
    out[0] = f->reply_header >= 0 ? (uint8_t)f->reply_header : 0;
    out[1] = (uint8_t)(len + f->reply_len_extra);
    memcpy(out + 2, data, len);
    out[2 + len] = hh_checksum(out, 2u + len);
    return (size_t)len + HH_REPLY_OVERHEAD;
}

size_t hh_strip_echo(const uint8_t *buf, size_t n, const uint8_t *req, size_t nreq, int *echo_seen)
{
    if (echo_seen) *echo_seen = 0;
    if (!nreq || n < nreq) return 0;
    for (size_t i = 0; i + nreq <= n; i++) {
        if (buf[i] == req[0] && memcmp(buf + i, req, nreq) == 0) {
            if (echo_seen) *echo_seen = 1;
            return i + nreq;
        }
    }
    return 0;
}

/* Could a reply header for this read start at rest[i]? */
static int candidate(const hh_framing_t *f, const uint8_t *rest, size_t n, size_t i, uint8_t want_len)
{
    if (f->reply_header >= 0 && rest[i] != (uint8_t)f->reply_header) return 0;
    if (i + 1 < n && rest[i + 1] != want_len) return 0;
    return 1;
}

int hh_find_reply(const hh_framing_t *f, const uint8_t *rest, size_t n, uint8_t len, uint8_t *data)
{
    size_t size = (size_t)len + HH_REPLY_OVERHEAD;
    uint8_t want_len = (uint8_t)(len + f->reply_len_extra);
    for (size_t i = 0; i + size <= n; i++) {
        if (!candidate(f, rest, n, i, want_len)) continue;
        if (hh_verify(rest + i, size)) {
            if (data) memcpy(data, rest + i + 2, len);
            return 1;
        }
    }
    return 0;
}

int hh_parse_reply(const hh_framing_t *f, const uint8_t *buf, size_t n, const uint8_t *req, size_t nreq,
                   uint8_t len, uint8_t *data)
{
    size_t off = hh_strip_echo(buf, n, req, nreq, NULL);
    const uint8_t *rest = buf + off;
    size_t nrest = n - off;
    size_t size = (size_t)len + HH_REPLY_OVERHEAD;
    uint8_t want_len = (uint8_t)(len + f->reply_len_extra);
    int any = 0;

    if (hh_find_reply(f, rest, nrest, len, data)) return HH_OK;
    if (!n || !nrest) return HH_E_NO_REPLY;
    for (size_t i = 0; i < nrest; i++) {
        if (!candidate(f, rest, nrest, i, want_len)) continue;
        if (nrest - i >= size) return HH_E_CHECKSUM;
        any = 1;
    }
    return any ? HH_E_SHORT : HH_E_BAD_FRAME;
}

int hh_frame_allowed(const hh_framing_t *f, const uint8_t *data, size_t len)
{
    if (!len) return 1;
    if (f->init_len && len == f->init_len && memcmp(data, f->init_seq, len) == 0) return 1;
    return len == f->request_len && len == HH_REQUEST_BYTES && data[0] == f->cmd_read
           && data[1] == f->request_len && hh_verify(data, len);
}

/* ---- adaptive timing ----------------------------------------------------- */

#define ERR_CLUSTER 0.2     /* smoothed failure share above which the gap grows */
#define ERR_CLEAN   0.05    /* ... and below which it shrinks again */

static double dmin(double a, double b) { return a < b ? a : b; }
static double dmax(double a, double b) { return a > b ? a : b; }

void hh_timing_init(hh_timing_t *t, const hh_framing_t *f, double usb_latency_s)
{
    memset(t, 0, sizeof(*t));
    t->byte_s = 10.0 / f->baud;
    t->adaptive = f->adaptive_timing;
    t->usb_latency_s = dmax(0.0, usb_latency_s);
    t->timeout_ceiling = f->reply_timeout_s;
    t->timeout_floor = dmin(f->min_reply_timeout_s, f->reply_timeout_s);
    t->ibt_ceiling = dmax(f->inter_byte_timeout_s, 2 * t->byte_s);
    t->gap_floor = dmax(0.0, f->inter_request_delay_s);
    t->gap_ceiling = dmax(t->gap_floor, f->max_inter_request_delay_s);
    t->response_timeout = t->timeout_ceiling;
    t->inter_byte_timeout = t->ibt_ceiling;
    t->gap = t->gap_floor;
}

void hh_timing_relax(hh_timing_t *t)
{
    t->error_share = 0.0;
    t->gap = t->gap_floor;
}

double hh_timing_ibt_floor(const hh_timing_t *t)
{
    return dmin(t->ibt_ceiling, 4 * t->byte_s + 2 * t->usb_latency_s);
}

double hh_timing_idle_gap(const hh_timing_t *t)
{
    return 4 * t->byte_s + 2 * t->usb_latency_s;
}

void hh_timing_success(hh_timing_t *t, double overhead_s, double chunk_gap_s)
{
    double r = dmax(0.0, overhead_s);
    if (!t->have_srtt) {
        t->srtt = r; t->rttvar = r / 2; t->have_srtt = 1;
    } else {
        double d = t->srtt - r;
        t->rttvar = 0.75 * t->rttvar + 0.25 * (d < 0 ? -d : d);
        t->srtt = 0.875 * t->srtt + 0.125 * r;
    }
    t->peak_chunk_gap = dmax(chunk_gap_s, 0.95 * t->peak_chunk_gap);
    t->error_share *= 0.9;
    if (!t->adaptive) return;
    double want = 2 * t->srtt + 4 * t->rttvar + 2 * t->byte_s + 2 * t->usb_latency_s;
    t->response_timeout = dmin(t->timeout_ceiling, dmax(t->timeout_floor, want));
    double ibt = 4 * t->peak_chunk_gap + 2 * t->byte_s + 2 * t->usb_latency_s;
    t->inter_byte_timeout = dmin(t->ibt_ceiling, dmax(hh_timing_ibt_floor(t), ibt));
    if (t->error_share < ERR_CLEAN && t->gap > t->gap_floor) t->gap = dmax(t->gap_floor, t->gap * 0.8);
}

void hh_timing_failure(hh_timing_t *t, int is_short, int silent)
{
    t->error_share = 0.9 * t->error_share + 0.1;
    if (!t->adaptive) return;
    if (silent) t->response_timeout = dmin(t->timeout_ceiling, t->response_timeout * 2);
    if (is_short) t->inter_byte_timeout = dmin(t->ibt_ceiling, t->inter_byte_timeout * 2);
    if (t->error_share > ERR_CLUSTER) t->gap = dmin(t->gap_ceiling, dmax(t->gap * 2, 0.002));
}
