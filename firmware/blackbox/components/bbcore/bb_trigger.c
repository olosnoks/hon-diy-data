/* bbcore: the trigger engine. One call per 1 ms sample, integer only.
 *
 * Two built-in state machines (IG1 on/off with hysteresis, "engine running"
 * from each pulse input) feed a table of rules, bb_config_t.rule[]. Nothing
 * about a particular fault is hard-coded: the stock stall triggers are just
 * the default rule table in bb_util.c.
 *
 * Mirrored in hondalog/blackbox/triggers.py; the tests feed both the same
 * vectors and compare.
 */
#include <string.h>

#include "bbcore.h"

static int deb(bb_debounce_t *d, int cond, int32_t ms)
{
    cond = cond ? 1 : 0;
    if (cond == d->state) { d->cnt = 0; return 0; }
    if (++d->cnt >= (ms < 1 ? 1 : ms)) { d->state = (uint8_t)cond; d->cnt = 0; return cond ? 1 : -1; }
    return 0;
}

static int emit(bb_event_t *out, int n, uint64_t t, uint8_t code, uint8_t src, int32_t value,
                uint8_t action)
{
    if (n >= BB_TRIG_MAX_OUT) return n;
    memset(&out[n], 0, sizeof(out[n]));
    out[n].t_us = t; out[n].code = code; out[n].src = src; out[n].value = value; out[n].rsv = action;
    return n + 1;
}

static int32_t source_value(const bb_trigger_t *tr, const bb_trig_in_t *in, int src, int op)
{
    if (src <= BB_S_ALT) return op == BB_OP_BELOW ? in->min_mv[src] : in->max_mv[src];
    if (src <= BB_S_SLEW_ALT) {
        int ch = src - BB_S_SLEW_VBAT;
        return tr->have_prev ? (int32_t)in->s->mv[ch] - (int32_t)tr->prev_mv[ch] : 0;
    }
    switch (src) {
    case BB_S_TACH_AGE: return (int32_t)in->age_ms[BB_E_TACH];
    case BB_S_IGN_AGE:  return (int32_t)in->age_ms[BB_E_IGN];
    case BB_S_INJ_AGE:  return (int32_t)in->age_ms[BB_E_INJ];
    case BB_S_RPM_TACH: return in->s->rpm_tach;
    case BB_S_RPM_IGN:  return in->s->rpm_ign;
    case BB_S_INJ_PW:   return in->s->inj_pw_us;
    case BB_S_INJ_DUTY: return in->s->inj_duty;
    default:            return in->button ? 1 : 0;
    }
}

int bb_trigger_step(bb_trigger_t *tr, const bb_config_t *cfg, const bb_trig_in_t *in,
                    bb_event_t *out, uint8_t *dig)
{
    const uint64_t t = in->t_us;
    const uint16_t ig1_mv = in->s->mv[BB_A_IG1];
    int n = 0;

    /* 1. rules, judged against the state as it was before this sample, so
     *    "tach stopped while running" still sees running = 1 */
    for (int code = 1; code < BB_MAX_RULES; code++) {
        const bb_rule_t *r = &cfg->rule[code];
        if (r->action == BB_ACT_OFF) continue;
        int32_t v = source_value(tr, in, r->source, r->op);
        int qual = (tr->qual & r->qual) == r->qual;
        /* "IG1 on" must also hold in this very sample, so a key-off that takes
         * every rail down in the same millisecond is not read as a relay fault */
        if ((r->qual & BB_Q_IG1) && in->min_mv[BB_A_IG1] < cfg->ig1_on_mv) qual = 0;
        int hit = r->op == BB_OP_BELOW ? v < r->level : v > r->level;
        int good = r->op == BB_OP_BELOW ? v >= r->level + r->hyst : v <= r->level - r->hyst;
        if (!qual) { tr->rule[code].armed = 0; tr->rule[code].cnt = 0; continue; }
        if (good) tr->rule[code].armed = 1;
        if (tr->rule[code].armed && hit) {
            if (++tr->rule[code].cnt >= r->hold_ms) {
                tr->rule[code].armed = 0; tr->rule[code].cnt = 0;
                n = emit(out, n, t, (uint8_t)code, r->source, v, r->action);
            }
        } else {
            tr->rule[code].cnt = 0;
        }
    }

    /* 2. IG1 with hysteresis */
    int ig_cond = tr->ig1.state ? ig1_mv > cfg->ig1_off_mv : ig1_mv >= cfg->ig1_on_mv;
    int r = deb(&tr->ig1, ig_cond, cfg->ig1_debounce_ms);
    if (r) n = emit(out, n, t, r > 0 ? BB_EV_IG1_ON : BB_EV_IG1_OFF, BB_S_IG1, ig1_mv, 0);

    /* 3. running latches: set after run_ms above run_rpm, cleared by pulse timeout */
    if (in->s->rpm_tach >= cfg->run_rpm) {
        if (!tr->running && ++tr->run_cnt >= cfg->run_ms) {
            tr->running = 1;
            n = emit(out, n, t, BB_EV_RUNNING, BB_S_RPM_TACH, in->s->rpm_tach, 0);
        }
    } else if (!tr->running) {
        tr->run_cnt = 0;
    }
    if (tr->running && in->age_ms[BB_E_TACH] >= (uint32_t)cfg->pulse_timeout_ms) { tr->running = 0; tr->run_cnt = 0; }

    if (in->s->rpm_ign >= cfg->run_rpm) {
        if (!tr->ign_running && ++tr->ign_run_cnt >= cfg->run_ms) tr->ign_running = 1;
    } else if (!tr->ign_running) {
        tr->ign_run_cnt = 0;
    }
    if (tr->ign_running && in->age_ms[BB_E_IGN] >= (uint32_t)cfg->pulse_timeout_ms) { tr->ign_running = 0; tr->ign_run_cnt = 0; }

    /* 4. qualifiers for the next step */
    tr->qual = (uint8_t)((tr->ig1.state && ig1_mv >= cfg->ig1_on_mv ? BB_Q_IG1 : 0)
                         | (tr->running ? BB_Q_RUNNING : 0)
                         | (tr->ign_running ? BB_Q_IGN_RUNNING : 0)
                         | (in->age_ms[BB_E_TACH] < (uint32_t)cfg->pulse_timeout_ms ? BB_Q_TACH_ALIVE : 0));
    memcpy(tr->prev_mv, in->s->mv, sizeof(tr->prev_mv));
    tr->have_prev = 1;

    *dig = (uint8_t)((tr->ig1.state ? BB_DIG_IG1_ON : 0) | (tr->running ? BB_DIG_RUNNING : 0));
    return n;
}
