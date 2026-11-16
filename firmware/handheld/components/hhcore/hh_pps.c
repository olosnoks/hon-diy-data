/* hhcore: the wall clock from the GPS pulse. See hhcore.h (PPS) for the idea.
 *
 * Two stages. Edges are qualified on their own (is this a pulse? 1 s after
 * the last good one, give or take a millisecond), then each good edge is
 * paired with the UTC second of the first time sentence that follows it
 * within 900 ms. A pair is one exact fact: at device time T it was second S.
 * The offset (unix us - device us) and its drift are kept by a small
 * alpha-beta filter; a pair that is far off the prediction is an outlier
 * (a sentence matched with the wrong pulse, a spike) and does not move it. */
#include <math.h>
#include <string.h>

#include "hhcore.h"

#define SEC_US          1000000LL
#define EDGE_TOL_US     1000        /* 1 s +- 1 ms between good edges */
#define EDGE_TOL_PER_S  50          /* a gap of n s may be off by this much more per extra second (crystal) */
#define EDGE_MAX_GAP    60          /* more missing seconds than this: start the edge chain again */
#define EDGE_RESET_AFTER 3          /* edges in a row that fit no chain: the chain was the glitch, start again */
#define PAIR_MIN_LAT_US 40000       /* no sentence is over this soon after its pulse (70 characters take 73 ms at 9600 baud) */ 
#define PAIR_MAX_LAT_US 900000
#define GATE_US         250.0       /* an offset this far off the prediction is an outlier ... */
#define GATE_PER_S      50.0        /* ... more allowed per second since the last pair (crystal drift) */
#define RELOCK_AFTER    5           /* outliers in a row: the model is wrong, not the pairs */
#define LOCK_PAIRS      3           /* agreeing pairs before the source is called PPS */
#define ALPHA           0.25
#define BETA            0.035
#define DRIFT_MAX       2e-4        /* 200 ppm: more than any crystal does, so a bad estimate */
#define JITTER_W        0.2
#define HOLD_UNC_US_S   2.0         /* error grows this much per second without a pair */

void hh_pps_init(hh_pps_t *p)
{
    memset(p, 0, sizeof(*p));
    p->jitter2 = (GATE_US / 2) * (GATE_US / 2);
}

int hh_pps_edge(hh_pps_t *p, int64_t t_us)
{
    int confirmed = 0;                      /* a pulse is only believed once the one before it was a second earlier */
    p->edges++;
    if (p->have_edge) {
        int64_t dt = t_us - p->last_good_us;
        int64_t n = (dt + SEC_US / 2) / SEC_US;
        int64_t err = dt - n * SEC_US;
        if (err < 0) err = -err;
        if (n < 1 || err > EDGE_TOL_US + EDGE_TOL_PER_S * (n - 1)) {
            /* not on the chain. Three in a row and it is the chain that was the glitch */
            p->glitches++;
            p->pending = 0;                 /* whatever the chain's last edge was, this is no reason to pair it */
            if (++p->bad_run < EDGE_RESET_AFTER) return 0;
            p->bad_run = 0;
            p->last_good_us = t_us;
            p->resets++;
            return 0;
        }
        if (n > EDGE_MAX_GAP) p->resets++;
        else {
            confirmed = 1;
            if (n > 1) { p->gaps++; p->missed += (uint32_t)(n - 1); }
        }
    }
    p->bad_run = 0;
    p->have_edge = 1;
    p->last_good_us = t_us;
    if (p->pending) p->unpaired++;          /* the one before never met a sentence */
    p->pending = (uint8_t)confirmed;
    p->pend_us = t_us;
    return confirmed;
}

static void restart(hh_pps_t *p, int64_t t_us, double m)
{
    p->n_ok = 1;
    p->n_pairs = 1;
    p->locked = 0;
    p->ref_us = t_us;
    p->off_us = m;
    p->drift = 0;
    p->bad_pairs = 0;
    p->jitter2 = (GATE_US / 2) * (GATE_US / 2);
    p->last_pair_us = t_us;
    p->resid_us = 0;
}

int hh_pps_time(hh_pps_t *p, int64_t t_rx_us, int64_t utc_s)
{
    if (!p->pending || utc_s <= 0) return HH_PPS_NONE;
    int64_t lat = t_rx_us - p->pend_us;
    int64_t t_edge = p->pend_us;
    p->pending = 0;                         /* one pair per pulse, whichever sentence comes first */
    if (lat < PAIR_MIN_LAT_US || lat > PAIR_MAX_LAT_US) { p->stale++; return HH_PPS_STALE; }

    double m = (double)(utc_s * SEC_US - t_edge);
    if (!p->n_ok) { restart(p, t_edge, m); p->pairs++; return HH_PPS_PAIR; }

    double dt = (double)(t_edge - p->ref_us);
    if (dt <= 0) { p->stale++; return HH_PPS_STALE; }
    double pred = p->off_us + p->drift * dt;
    double r = m - pred;
    double gate = GATE_US + GATE_PER_S * (dt / 1e6);
    if (fabs(r) > gate) {
        p->outliers++;
        if (++p->bad_pairs >= RELOCK_AFTER) { restart(p, t_edge, m); p->relocks++; p->pairs++; return HH_PPS_RELOCK; }
        return HH_PPS_OUTLIER;
    }
    p->bad_pairs = 0;
    p->pairs++;
    if (p->n_ok < 2) {
        p->drift = (m - p->off_us) / dt;    /* the first two pairs give a rate */
        if (fabs(p->drift) > DRIFT_MAX) p->drift = 0;
        p->off_us = m;
    } else {
        /* growing memory at first (a least-squares fit while there are few pairs), the steady gains after */
        double k = (double)(p->n_pairs + 1), a = 2 * (2 * k - 1) / (k * (k + 1)), b = 6 / (k * (k + 1));
        if (a < ALPHA) a = ALPHA;
        if (b < BETA) b = BETA;
        p->off_us = pred + a * r;
        p->drift += b * r / dt;
        if (p->drift > DRIFT_MAX) p->drift = DRIFT_MAX;
        if (p->drift < -DRIFT_MAX) p->drift = -DRIFT_MAX;
        if (p->n_ok < LOCK_PAIRS) p->jitter2 = r * r > 1.0 ? r * r : 1.0;       /* the first residual is the first look at the noise */
        else p->jitter2 += JITTER_W * (r * r - p->jitter2);
    }
    if (p->n_pairs < 60000) p->n_pairs++;
    p->resid_us = r;
    p->ref_us = t_edge;
    p->last_pair_us = t_edge;
    if (p->n_ok < LOCK_PAIRS) p->n_ok++;
    if (p->n_ok >= LOCK_PAIRS) p->locked = 1;
    return HH_PPS_PAIR;
}

int hh_pps_active(const hh_pps_t *p, int64_t t_us)
{
    return p->locked && t_us - p->last_pair_us <= HH_PPS_HOLD_US && t_us >= p->last_pair_us;
}

int64_t hh_pps_unix_us(const hh_pps_t *p, int64_t t_us)
{
    return t_us + (int64_t)llround(p->off_us + p->drift * (double)(t_us - p->ref_us));
}

double hh_pps_jitter_us(const hh_pps_t *p) { return sqrt(p->jitter2); }

uint32_t hh_pps_err_us(const hh_pps_t *p, int64_t t_us)
{
    double age_s = t_us > p->last_pair_us ? (double)(t_us - p->last_pair_us) / 1e6 : 0;
    double e = sqrt(p->jitter2) + HOLD_UNC_US_S * age_s;
    return e < 1.0 ? 1u : e > 1e6 ? 1000000u : (uint32_t)(e + 0.5);
}
