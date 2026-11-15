/* hhcore: request/reply exchanges on a transport (hondalog/transport.py: Link).
 *
 * Adaptive timeouts, echo checking, resync, retry with backoff, counters and
 * frame timestamps. Read-only: hh_link_tx() is the only way onto the wire and
 * it passes read requests and the wake-up bytes, nothing else. There is no
 * unlock on the handheld. */
#include <string.h>

#include "hhcore.h"

#define BACKOFF_BASE_S 0.005
#define BACKOFF_MAX_S  0.08
#define RESYNC_MAX_S   0.25
#define RXBUF          (HH_REQUEST_BYTES + HH_MAX_READ + HH_REPLY_OVERHEAD + 56)

static double now(hh_link_t *l) { return l->clk.now(l->clk.ctx); }
static void nap(hh_link_t *l, double s) { if (s > 0) l->clk.sleep(l->clk.ctx, s); }

void hh_link_init(hh_link_t *l, hh_transport_t *tr, const hh_clock_t *clk, const hh_framing_t *f)
{
    memset(l, 0, sizeof(*l));
    l->tr = tr;
    l->clk = *clk;
    l->f = *f;
    if (l->f.max_read_len > HH_MAX_READ) l->f.max_read_len = HH_MAX_READ;
    hh_link_reset(l);
}

void hh_link_reset(hh_link_t *l)
{
    hh_timing_init(&l->tm, &l->f, l->tr->usb_latency_s);
    l->echo_mode = -1;
    l->next_tx = 0.0;
    l->guard_until = 0.0;
}

int hh_link_tx(hh_link_t *l, const uint8_t *data, size_t len)
{
    if (!hh_frame_allowed(&l->f, data, len)) {
        l->stats.blocked++;
        return HH_E_BLOCKED;
    }
    return l->tr->write(l->tr->ctx, data, len) < 0 ? HH_E_PORT : HH_OK;
}

int hh_link_resync(hh_link_t *l, double quiet_until)
{
    uint8_t scratch[64];
    double idle = hh_timing_idle_gap(&l->tm);
    double end = now(l) + RESYNC_MAX_S;
    int dropped = 0;
    if (quiet_until > end) end = quiet_until;
    for (;;) {
        double t = now(l);
        if (t >= end) break;
        double wait = (dropped || t + idle >= quiet_until) ? idle : quiet_until - t;
        int n = l->tr->read_some(l->tr->ctx, scratch, sizeof(scratch), wait);
        if (n < 0) return HH_E_PORT;
        if (n > 0) dropped += n;
        else if (dropped || now(l) >= quiet_until) break;
    }
    l->stats.resyncs++;
    l->stats.discarded_bytes += (uint32_t)dropped;
    l->next_tx = now(l) + l->tm.gap;
    return dropped;
}

/* Finish the late-reply watch left behind by an unanswered request. */
static int settle(hh_link_t *l)
{
    double until = l->guard_until;
    l->guard_until = 0.0;
    return hh_link_resync(l, until) < 0 ? HH_E_PORT : HH_OK;
}

int hh_link_wake(hh_link_t *l)
{
    uint8_t scratch[64];
    if (!l->f.init_len) return HH_OK;
    l->stats.wakes++;
    if (l->guard_until != 0.0 && settle(l) < 0) return HH_E_PORT;
    l->tr->flush_input(l->tr->ctx);
    int rc = hh_link_tx(l, l->f.init_seq, l->f.init_len);
    if (rc < 0) return rc;
    nap(l, l->f.init_delay_s);
    /* throw away whatever came back (the echo, maybe an answer) */
    double end = now(l) + 0.05;
    for (;;) {
        double left = end - now(l);
        if (left <= 0) break;
        int n = l->tr->read_some(l->tr->ctx, scratch, sizeof(scratch), left);
        if (n < 0) return HH_E_PORT;
    }
    l->next_tx = now(l) + l->tm.gap;
    return HH_OK;
}

static int exchange(hh_link_t *l, uint8_t addr, uint8_t len, const uint8_t *request, int attempt, hh_read_t *out)
{
    const hh_framing_t *f = &l->f;
    hh_timing_t *tm = &l->tm;
    hh_link_stats_t *st = &l->stats;
    double byte_s = tm->byte_s;
    size_t nreq = l->echo_mode != 0 ? HH_REQUEST_BYTES : 0;
    size_t size = (size_t)len + HH_REPLY_OVERHEAD;
    size_t want = nreq + size;
    uint8_t want_len = (uint8_t)(len + f->reply_len_extra);
    uint8_t buf[RXBUF];
    size_t n = 0;
    int found = 0, started = 0, rc;

    if (l->guard_until != 0.0 && settle(l) < 0) return HH_E_PORT;
    nap(l, l->next_tx - now(l));
    double t_req = now(l);
    rc = hh_link_tx(l, request, HH_REQUEST_BYTES);
    if (rc < 0) return rc;
    st->requests++;

    double tx_end = l->last_tx_end = t_req + HH_REQUEST_BYTES * byte_s;
    double deadline = tx_end + byte_s + tm->response_timeout;
    double hard = t_req + (HH_REQUEST_BYTES + size) * byte_s + tm->response_timeout + tm->inter_byte_timeout;
    double ibt = tm->inter_byte_timeout;
    double t_first = 0.0, last_rx = 0.0, max_gap = 0.0, t = t_req;

    for (;;) {
        t = now(l);
        double remaining = deadline - t;
        if (remaining <= 0 || n >= sizeof(buf)) break;
        size_t ask = want > n ? want - n : 1;
        if (ask > sizeof(buf) - n) ask = sizeof(buf) - n;
        int got = l->tr->read_some(l->tr->ctx, buf + n, ask, remaining);
        if (got < 0) return HH_E_PORT;
        if (got == 0) continue;
        t = now(l);
        n += (size_t)got;
        if (started) {
            if (t - last_rx > max_gap) max_gap = t - last_rx;
        } else if (n > nreq || memcmp(buf, request, n) != 0) {
            started = 1;                        /* past the echo, or not an echo at all */
            t_first = t;
        }
        last_rx = t;
        if (started) deadline = hard < t + ibt ? hard : t + ibt;
        if (n >= want) {
            if (memcmp(buf, request, nreq) == 0) {      /* the lean, normal path */
                const uint8_t *frame = buf + nreq;
                if ((f->reply_header < 0 || frame[0] == (uint8_t)f->reply_header) && frame[1] == want_len
                    && hh_verify(frame, size)) {
                    memcpy(out->data, frame + 2, len);
                    found = 1;
                    break;
                }
            }
            size_t off = hh_strip_echo(buf, n, request, HH_REQUEST_BYTES, NULL);
            if (hh_find_reply(f, buf + off, n - off, len, out->data)) { found = 1; break; }
        } else if (n >= size) {
            size_t k = n < nreq ? n : nreq;
            if (memcmp(buf, request, k) != 0) {
                size_t off = hh_strip_echo(buf, n, request, HH_REQUEST_BYTES, NULL);
                if (hh_find_reply(f, buf + off, n - off, len, out->data)) { found = 1; break; }
            }
        }
    }

    int echo_here = 0;
    hh_strip_echo(buf, n, request, HH_REQUEST_BYTES, &echo_here);
    if (!found) {
        l->next_tx = t + tm->gap;
        if (l->echo_mode > 0 && n && !echo_here) st->echo_mismatches++;
        rc = hh_parse_reply(f, buf, n, request, HH_REQUEST_BYTES, len, out->data);
        switch (rc) {
        case HH_OK: break;
        case HH_E_NO_REPLY: st->timeouts++; hh_timing_failure(tm, 0, 1); return rc;
        case HH_E_SHORT: st->timeouts++; st->short_replies++; hh_timing_failure(tm, 1, 0); return rc;
        case HH_E_CHECKSUM: st->checksum_failures++; hh_timing_failure(tm, 0, 0); return rc;
        default: st->bad_frames++; hh_timing_failure(tm, 0, 0); return HH_E_BAD_FRAME;
        }
    }

    if (echo_here) {
        l->echo_mode = 1; l->echo_seen = 1;
    } else {
        if (l->echo_mode > 0) st->echo_mismatches++;    /* the frame survived, the echo did not */
        else l->echo_mode = 0;
        l->echo_seen = 0;
    }
    double on_wire = (HH_REQUEST_BYTES + size) * byte_s;
    long extra = (long)n - (long)(size + (echo_here ? HH_REQUEST_BYTES : 0));
    if (extra > 0) st->junk_bytes += (uint32_t)extra;
    hh_timing_success(tm, t - t_req - on_wire, max_gap);
    st->ok++;
    l->next_tx = t + tm->gap;
    out->addr = addr; out->len = len; out->attempts = (uint8_t)attempt;
    out->t_req = t_req; out->t_done = t;
    out->latency_s = size * byte_s + tm->usb_latency_s / 2;
    out->turnaround_s = t_first - tx_end - byte_s;
    if (out->turnaround_s < 0) out->turnaround_s = 0;
    return HH_OK;
}

int hh_link_read_block(hh_link_t *l, uint8_t addr, uint8_t len, int retries, hh_read_t *out)
{
    uint8_t request[HH_REQUEST_BYTES];
    if (len < 1 || len > HH_MAX_READ) return HH_E_ARG;
    hh_build_read_request(&l->f, addr, len, request);
    int attempts = 1 + (retries < 0 ? l->f.retries : retries);
    for (int attempt = 1;; attempt++) {
        int rc = exchange(l, addr, len, request, attempt, out);
        if (rc == HH_OK || rc == HH_E_PORT || rc == HH_E_BLOCKED) return rc;
        if (rc == HH_E_NO_REPLY) {
            /* Nothing came back. The caller hears about it now, but the line
             * is watched for a late reply before anything is sent again, or
             * that reply would pass for the next answer. */
            l->guard_until = l->last_tx_end + l->tm.timeout_ceiling;
        } else if (hh_link_resync(l, 0.0) < 0) {
            return HH_E_PORT;
        }
        if (attempt >= attempts) return rc;
        l->stats.retries++;
        double wait = BACKOFF_BASE_S * (double)(1u << (attempt - 1));
        nap(l, wait < BACKOFF_MAX_S ? wait : BACKOFF_MAX_S);
    }
}
