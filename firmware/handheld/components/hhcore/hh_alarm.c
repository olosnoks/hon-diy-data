/* hhcore: the alarm state machine (hondalog/alarms/engine.py), the stall
 * trigger (hondalog/triggers: the two stall rules) and the byte ring that
 * holds the last seconds of reads for a capture. No I/O. */
#include <math.h>
#include <string.h>

#include "hhcore.h"

/* ---- slope(x, ms) ---------------------------------------------------------- */

void hh_slope_reset(hh_slope_t *s) { s->head = 0; s->n = 0; }

double hh_slope_update(hh_slope_t *s, double t, double x)
{
    if (s->n) {
        int newest = (s->head + s->n - 1) % HH_SLOPE_N;
        if (t < s->t[newest]) hh_slope_reset(s);        /* time went backwards */
    }
    if (s->n == HH_SLOPE_N) { s->head = (uint8_t)((s->head + 1) % HH_SLOPE_N); s->n--; }
    int at = (s->head + s->n) % HH_SLOPE_N;
    s->t[at] = t; s->x[at] = x; s->n++;
    double lim = t - s->w;
    while (s->n && s->t[s->head] < lim) { s->head = (uint8_t)((s->head + 1) % HH_SLOPE_N); s->n--; }
    int n = s->n;
    if (n < 2) return NAN;
    double t0 = s->t[s->head], st = 0, sx = 0, stt = 0, stx = 0;
    for (int i = 0; i < n; i++) {
        int k = (s->head + i) % HH_SLOPE_N;
        double r = s->t[k] - t0;
        st += r; sx += s->x[k]; stt += r * r; stx += r * s->x[k];
    }
    double den = n * stt - st * st;
    if (den <= 1e-12) return NAN;
    return (n * stx - st * sx) / den;
}

/* ---- alarms ------------------------------------------------------------------ */

void hh_alarms_init(hh_alarms_t *a, const hh_map_t *map)
{
    memset(a, 0, sizeof(*a));
    a->map = map;
    a->n = (uint8_t)(map->n_alarm > HH_MAX_ALARMS ? HH_MAX_ALARMS : map->n_alarm);
    for (int i = 0; i < a->n; i++) {
        hh_map_alarm_t r;
        hh_map_alarm(map, i, &r);
        a->st[i].clear_since = -1.0;
        a->st[i].value = NAN;
        if ((r.flags & (HH_AF_RATE_LOW | HH_AF_RATE_HIGH)) && a->n_slope < 4) {
            a->slope[i] = &a->slope_pool[a->n_slope++];
            a->slope[i]->w = r.rate_window_ms / 1000.0;
            hh_slope_reset(a->slope[i]);
        }
    }
}

/* 1 true, 0 false, -1 unknown */
static int cond(const hh_alarms_t *a, unsigned off, unsigned len, const float *values, uint16_t n, double t)
{
    hh_expr_env_t env = {a->map->consts, a->map->n_const, values, n, NULL, 0, NAN, t};
    double r = hh_expr_eval(a->map->prog + off, len, &env);
    return isnan(r) ? -1 : r != 0.0;
}

/* 1 tripped, 0 clear, -1 cannot tell (an input has no value): hold the state. */
static int tripped(hh_alarms_t *a, int i, const hh_map_alarm_t *r, const float *values, uint16_t n, double t)
{
    hh_alarm_state_t *st = &a->st[i];
    int trip = 0, clear = 1;
    if (r->gate_len) {
        int gate = cond(a, r->gate_off, r->gate_len, values, n, t);
        if (gate < 0) return -1;
        if (!gate) {
            if (a->slope[i]) hh_slope_reset(a->slope[i]);
            return 0;
        }
    }
    if (r->channel >= 0) {
        double value = r->channel < (int)n ? (double)values[r->channel] : NAN;
        if (isnan(value)) return -1;
        st->value = (float)value;
        if (r->flags & HH_AF_LOW) {
            if (value < r->low) trip = 1;
            else if (value < (double)r->low + r->hysteresis) clear = 0;
        }
        if (r->flags & HH_AF_HIGH) {
            if (value > r->high) trip = 1;
            else if (value > (double)r->high - r->hysteresis) clear = 0;
        }
        if (a->slope[i]) {
            double rate = hh_slope_update(a->slope[i], t, value);
            if (!isnan(rate)) {
                if ((r->flags & HH_AF_RATE_LOW) && rate < r->rate_low) trip = 1;
                if ((r->flags & HH_AF_RATE_HIGH) && rate > r->rate_high) trip = 1;
            }
        }
    }
    if (r->when_len) {
        int c = cond(a, r->when_off, r->when_len, values, n, t);
        if (c > 0) trip = 1;
        else if (c < 0) { if (!trip) return -1; }
        else if (r->clear_len && !trip) {
            if (cond(a, r->clear_off, r->clear_len, values, n, t) <= 0) clear = 0;  /* false or unknown: not yet */
        }
    }
    if (trip) return 1;
    if (clear) return 0;
    return st->state == HH_AL_ACTIVE;       /* in the re-arm band: an active alarm stays up */
}

static int emit(hh_alarm_event_t *ev, int n, int max, uint8_t kind, int index, const hh_map_alarm_t *r,
                double t, float value)
{
    if (n < max) {
        ev[n].kind = kind; ev[n].index = (uint8_t)index; ev[n].severity = r->severity;
        ev[n].t = t; ev[n].value = value;
    }
    return n + 1;
}

int hh_alarms_update(hh_alarms_t *a, const float *values, uint16_t n_values, double t,
                     hh_alarm_event_t *ev, int max)
{
    int n_ev = 0;
    for (int i = 0; i < a->n; i++) {
        hh_map_alarm_t r;
        hh_alarm_state_t *st = &a->st[i];
        hh_map_alarm(a->map, i, &r);
        int trip = tripped(a, i, &r, values, n_values, t);
        if (trip < 0) continue;
        if (trip) {
            st->clear_since = -1.0;
            if (st->state == HH_AL_IDLE) { st->state = HH_AL_PENDING; st->since = t; }
            if (st->state == HH_AL_PENDING) {
                if (t - st->since >= r.hold_s) {
                    st->state = HH_AL_ACTIVE; st->since = t; st->raised_at = t;
                    st->acknowledged = 0; st->count++;
                    n_ev = emit(ev, n_ev, max, HH_ALEV_RAISED, i, &r, t, st->value);
                }
            } else if (st->state == HH_AL_LATCHED) {
                st->state = HH_AL_ACTIVE; st->since = t;        /* came back before the ack */
            }
        } else if (st->state == HH_AL_PENDING) {
            st->state = HH_AL_IDLE; st->since = t;
        } else if (st->state == HH_AL_ACTIVE) {
            if (st->clear_since < 0) st->clear_since = t;
            if (t - st->clear_since >= r.clear_hold_s) {
                st->clear_since = -1.0;
                if ((r.flags & HH_AF_LATCHING) && !st->acknowledged) {
                    st->state = HH_AL_LATCHED; st->since = t;
                } else {
                    st->state = HH_AL_IDLE; st->since = t; st->acknowledged = 0;
                    n_ev = emit(ev, n_ev, max, HH_ALEV_CLEARED, i, &r, t, st->value);
                }
            }
        }
    }
    return n_ev < max ? n_ev : max;
}

int hh_alarms_ack(hh_alarms_t *a, double t, hh_alarm_event_t *ev, int max)
{
    int n_ev = 0;
    for (int i = 0; i < a->n; i++) {
        hh_map_alarm_t r;
        hh_alarm_state_t *st = &a->st[i];
        hh_map_alarm(a->map, i, &r);
        if (st->state == HH_AL_LATCHED) {       /* the condition is gone: clears now */
            n_ev = emit(ev, n_ev, max, HH_ALEV_ACK, i, &r, t, st->value);
            st->state = HH_AL_IDLE; st->since = t; st->acknowledged = 0;
            n_ev = emit(ev, n_ev, max, HH_ALEV_CLEARED, i, &r, t, st->value);
        } else if (st->state == HH_AL_ACTIVE && !st->acknowledged) {
            st->acknowledged = 1;               /* stays up, clears by itself later */
            n_ev = emit(ev, n_ev, max, HH_ALEV_ACK, i, &r, t, st->value);
        }
    }
    return n_ev < max ? n_ev : max;
}

static int shown(const hh_alarm_state_t *st) { return st->state == HH_AL_ACTIVE || st->state == HH_AL_LATCHED; }

int hh_alarms_top(const hh_alarms_t *a)
{
    int best = -1, best_pri = -1;
    for (int i = 0; i < a->n; i++) {
        hh_map_alarm_t r;
        if (!shown(&a->st[i])) continue;
        hh_map_alarm(a->map, i, &r);
        if (best < 0 || r.priority > best_pri
            || (r.priority == best_pri && a->st[i].raised_at < a->st[best].raised_at)) {
            best = i; best_pri = r.priority;
        }
    }
    return best;
}

void hh_alarms_levels(const hh_alarms_t *a, uint8_t *level, uint16_t n_ch)
{
    memset(level, 0, n_ch);
    for (int i = 0; i < a->n; i++) {
        hh_map_alarm_t r;
        if (!shown(&a->st[i])) continue;
        hh_map_alarm(a->map, i, &r);
        if (r.channel < 0 || r.channel >= (int)n_ch || !(r.flags & HH_AF_TILE)) continue;
        uint8_t lv = r.severity >= 2 ? 2 : 1;
        if (lv > level[r.channel]) level[r.channel] = lv;
    }
}

/* ---- stall trigger ------------------------------------------------------------ */

void hh_stall_init(hh_stall_t *s, const hh_map_stall_t *cfg)
{
    memset(s, 0, sizeof(*s));
    s->rpm_run = cfg->rpm_run; s->rpm_zero = cfg->rpm_zero; s->link_down_s = cfg->link_down_s;
    s->pre_s = cfg->pre_s; s->post_s = cfg->post_s;
    s->link_false_since = -1.0;
}

static void open_window(hh_stall_t *s, double t, int reason)
{
    if (!s->capturing) {
        s->capturing = 1;
        s->t_trigger = t;
        s->t_start = t - s->pre_s;
        s->t_end = t + s->post_s;
        s->last_reason = (uint8_t)reason;
        s->fired_in_window = 0;
        s->count++;
    } else if (t + s->post_s > s->t_end) {
        s->t_end = t + s->post_s;           /* a second firing inside the window extends it */
    }
    s->fired_in_window++;
}

int hh_stall_feed(hh_stall_t *s, double t, double rpm, int link)
{
    int fired = 0;
    /* Both rules arm once the engine has been seen running and disarm
     * together: after "rpm dropped to 0" the key goes off, the ECU goes
     * silent, and that must not be a second capture. */
    if (!isnan(rpm) && rpm > s->rpm_run) s->armed = 1;
    if (!isnan(rpm) && rpm < s->rpm_zero) {
        if (s->armed) { fired = HH_STALL_RPM; s->armed = 0; }
    }
    if (link == 0) {
        if (s->link_false_since < 0) s->link_false_since = t;
        if (!fired && s->armed && t - s->link_false_since >= s->link_down_s) { fired = HH_STALL_LINK; s->armed = 0; }
    } else if (link > 0) {
        s->link_false_since = -1.0;
    }
    if (fired) open_window(s, t, fired);
    return fired;
}

int hh_stall_fire(hh_stall_t *s, double t, int reason)
{
    open_window(s, t, reason);
    return reason;
}

int hh_stall_window_done(hh_stall_t *s, double t)
{
    if (!s->capturing || t < s->t_end) return 0;
    s->capturing = 0;
    return 1;
}

/* ---- byte ring ---------------------------------------------------------------- */

void hh_ring_init(hh_ring_t *r, uint8_t *buf, uint32_t cap) { r->buf = buf; r->cap = cap; hh_ring_clear(r); }
void hh_ring_clear(hh_ring_t *r) { r->head = r->tail = r->used = 0; }

static void ring_put(hh_ring_t *r, const uint8_t *p, uint32_t n)
{
    uint32_t first = r->cap - r->head < n ? r->cap - r->head : n;
    memcpy(r->buf + r->head, p, first);
    memcpy(r->buf, p + first, n - first);
    r->head = (r->head + n) % r->cap;
    r->used += n;
}

static void ring_get(const hh_ring_t *r, uint32_t pos, uint8_t *p, uint32_t n)
{
    uint32_t first = r->cap - pos < n ? r->cap - pos : n;
    memcpy(p, r->buf + pos, first);
    memcpy(p + first, r->buf, n - first);
}

void hh_ring_pop(hh_ring_t *r)
{
    uint8_t l[2];
    if (r->used < 2) { hh_ring_clear(r); return; }
    ring_get(r, r->tail, l, 2);
    uint32_t n = 2u + (l[0] | ((uint32_t)l[1] << 8));
    if (n > r->used) { hh_ring_clear(r); return; }
    r->tail = (r->tail + n) % r->cap;
    r->used -= n;
}

void hh_ring_push(hh_ring_t *r, const void *item, uint16_t len)
{
    uint8_t l[2] = {(uint8_t)len, (uint8_t)(len >> 8)};
    if (!r->buf || (uint32_t)len + 2 > r->cap) return;
    while (r->cap - r->used < (uint32_t)len + 2) hh_ring_pop(r);
    ring_put(r, l, 2);
    ring_put(r, item, len);
}

uint16_t hh_ring_peek(const hh_ring_t *r, uint32_t *pos, void *out, uint16_t max)
{
    uint8_t l[2];
    uint32_t done = (*pos + r->cap - r->tail) % r->cap;     /* bytes already walked */
    if (!r->buf || !r->used || (done == 0 && *pos != r->tail)) return 0;
    if (done >= r->used) return 0;
    ring_get(r, *pos, l, 2);
    uint16_t n = (uint16_t)(l[0] | (l[1] << 8));
    if (n > max || 2u + n > r->used - done) return 0;
    ring_get(r, (*pos + 2) % r->cap, out, n);
    *pos = (*pos + 2u + n) % r->cap;
    return n;
}
