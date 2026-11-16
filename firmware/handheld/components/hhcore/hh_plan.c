/* hhcore: which (addr, length) requests to send, and in what order.
 *
 * hh_plan_build is hondalog/link/planner.py (spans_from_channels + plan_blocks):
 * the exact optimum over all ways of cutting the address-sorted channel list
 * into blocks, minimising  sum(rate(block) * time(block)).
 * hh_sched_* is hondalog/link/scheduler.py: most overdue first, never two
 * slower-class blocks in a row, spare wire time to the fast class.
 *
 * The arithmetic is written in the same order as the Python so that both
 * give the same plan and the same pick for the same inputs (the host tests
 * compare them). */
#include <math.h>
#include <string.h>

#include "hhcore.h"

void hh_cost_from_framing(hh_cost_t *c, const hh_framing_t *f, double turnaround_s, double gap_s)
{
    c->byte_s = 10.0 / f->baud;
    c->echo_bytes = HH_REQUEST_BYTES;
    c->frame_bytes = HH_REPLY_OVERHEAD;
    c->turnaround_s = turnaround_s < 0 ? f->turnaround_s : turnaround_s;
    c->gap_s = gap_s < 0 ? f->inter_request_delay_s : gap_s;
}

double hh_cost_overhead(const hh_cost_t *c)
{
    return (c->echo_bytes + c->frame_bytes) * c->byte_s + c->turnaround_s + c->gap_s;
}

double hh_cost_request(const hh_cost_t *c, int length)
{
    return hh_cost_overhead(c) + length * c->byte_s;
}

typedef struct { uint16_t addr, end; double rate; uint8_t first, count; } span_t;

int hh_plan_build(hh_plan_t *p, const hh_plan_ch_t *ch, int n_ch, int max_len, const hh_cost_t *cost)
{
    uint8_t idx[HH_MAX_CH];
    span_t spans[HH_MAX_CH];
    double best[HH_MAX_CH + 1];
    uint8_t cut[HH_MAX_CH + 1];
    int n = 0;

    memset(p, 0, sizeof(*p));
    if (n_ch < 0 || n_ch > HH_MAX_CH || max_len < 1) return HH_E_ARG;

    /* channels sorted by (addr, end), ties in map order: an insertion sort is stable */
    for (int i = 0; i < n_ch; i++) {
        int j = i;
        unsigned a = ch[i].addr, e = (unsigned)ch[i].addr + ch[i].length;
        while (j > 0) {
            unsigned pa = ch[idx[j - 1]].addr, pe = pa + ch[idx[j - 1]].length;
            if (pa < a || (pa == a && pe <= e)) break;
            idx[j] = idx[j - 1];
            j--;
        }
        idx[j] = (uint8_t)i;
    }
    /* spans: overlapping channels merged; a multi-byte channel is never split */
    for (int i = 0; i < n_ch; i++) {
        const hh_plan_ch_t *c = &ch[idx[i]];
        uint16_t lo = c->addr, hi = (uint16_t)(c->addr + c->length);
        p->order[i] = idx[i];
        if (n && lo < spans[n - 1].end) {
            span_t *s = &spans[n - 1];
            if (hi > s->end) s->end = hi;
            if (c->rate_hz > s->rate) s->rate = c->rate_hz;
            s->count++;
        } else {
            spans[n].addr = lo; spans[n].end = hi; spans[n].rate = c->rate_hz;
            spans[n].first = (uint8_t)i; spans[n].count = 1;
            n++;
        }
    }

    double overhead = hh_cost_overhead(cost);
    best[0] = 0.0; cut[0] = 0;
    for (int k = 1; k <= n; k++) {
        int end = spans[k - 1].end;
        double rate = 0.0;
        best[k] = INFINITY; cut[k] = 0;
        for (int i = k - 1; i >= 0; i--) {
            int length = end - spans[i].addr;
            if (length > max_len && i < k - 1) break;
            if (spans[i].rate > rate) rate = spans[i].rate;
            int pieces = (length + max_len - 1) / max_len;      /* > 1 only for an oversize span */
            double c = best[i] + rate * (pieces * overhead + length * cost->byte_s);
            if (c < best[k]) { best[k] = c; cut[k] = (uint8_t)i; }
        }
    }

    /* walk the cuts back; blocks come out last first */
    hh_block_t rev[HH_MAX_BLOCKS];
    int nb = 0;
    for (int k = n; k > 0;) {
        int i = cut[k];
        int addr = spans[i].addr, end = spans[k - 1].end;
        double rate = 0.0;
        int count = 0;
        for (int s = i; s < k; s++) {
            if (spans[s].rate > rate) rate = spans[s].rate;
            count += spans[s].count;
        }
        int parts = (end - addr + max_len - 1) / max_len;
        for (int q = parts - 1; q >= 0; q--) {
            int a = addr + q * max_len;
            if (nb >= HH_MAX_BLOCKS) return HH_E_ARG;
            rev[nb].addr = (uint16_t)a;
            rev[nb].length = (uint16_t)(end - a < max_len ? end - a : max_len);
            rev[nb].rate_hz = rate;
            rev[nb].first = spans[i].first;
            rev[nb].count = (uint8_t)count;
            nb++;
        }
        k = i;
    }
    for (int b = 0; b < nb; b++) p->blocks[b] = rev[nb - 1 - b];
    p->n_blocks = (uint8_t)nb;
    return HH_OK;
}

double hh_plan_bus_load(const hh_plan_t *p, const hh_cost_t *cost)
{
    double load = 0.0;
    for (int i = 0; i < p->n_blocks; i++) load += p->blocks[i].rate_hz * hh_cost_request(cost, p->blocks[i].length);
    return load;
}

/* ---- scheduler ------------------------------------------------------------ */

void hh_sched_init(hh_sched_t *s, const hh_plan_t *p)
{
    double top = 0.0;
    memset(s, 0, sizeof(*s));
    s->n = p->n_blocks;
    for (int i = 0; i < s->n; i++) {
        s->rate[i] = p->blocks[i].rate_hz > 1e-6 ? p->blocks[i].rate_hz : 1e-6;
        s->last[i] = -INFINITY;
        if (s->rate[i] > top) top = s->rate[i];
    }
    for (int i = 0; i < s->n; i++) s->top[i] = s->rate[i] >= top;
    s->prev_done = -INFINITY;
}

int hh_sched_next(hh_sched_t *s, double now)
{
    int best = 0, top = -1, low = -1, pick;
    double best_u = -INFINITY, top_u = -INFINITY, low_u = -INFINITY;
    for (int i = 0; i < s->n; i++) {
        double u = (now - s->last[i] + s->lead) * s->rate[i];
        if (u > best_u || (u == best_u && s->rate[i] > s->rate[best])) { best = i; best_u = u; }
        if (s->top[i]) {
            if (u > top_u) { top = i; top_u = u; }
        } else if (u > low_u) {
            low = i; low_u = u;
        }
    }
    if (best_u == INFINITY) pick = best;                        /* a never-read block */
    else if (low_u >= 1.0 && !s->low_last) pick = low;          /* a slower block that is due */
    else if (best_u >= 1.0 || top < 0) pick = (s->top[best] || !s->low_last || top < 0) ? best : top;
    else pick = top;                                            /* spare time: the fast class */
    s->low_last = s->n ? !s->top[pick] : 0;
    return pick;
}

void hh_sched_done(hh_sched_t *s, int index, double t_done)
{
    /* A block picked now is finished one request later, so "due" looks ahead
     * by the typical request time. */
    double step = t_done - s->prev_done;
    if (step > 0.0 && step < 0.2) s->lead = s->lead == 0.0 ? step : 0.9 * s->lead + 0.1 * step;
    s->prev_done = t_done;
    s->last[index] = t_done;
    s->reads[index]++;
}

void hh_sched_failed(hh_sched_t *s, int index, double now)
{
    s->last[index] = now;       /* back one period, so it cannot hog the wire */
    s->failures[index]++;
}

void hh_sched_restart(hh_sched_t *s)
{
    for (int i = 0; i < s->n; i++) s->last[i] = -INFINITY;
}
