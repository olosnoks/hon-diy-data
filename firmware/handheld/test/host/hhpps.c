/* Host tests of the pulse clock (components/hhcore/hh_pps.c) and of the engine's use of it.
 *
 * The first half drives hh_pps_t directly with a simulated receiver: a device
 * crystal that runs fast or slow by some ppm, interrupt jitter on every edge,
 * NMEA sentences that follow each pulse (GGA at 120 ms, RMC at 650 ms, like a
 * NEO-6M at 9600 baud), and then the faults: missing pulses, noise pulses,
 * a sentence matched to the wrong pulse, a step of the device clock, a fix
 * lost and found again. "Truth" is UTC second S starting at true time S s;
 * the device clock reads  dev0 + true * (1 + ppm/1e6).
 *
 * The second half runs the whole engine (hh_core) with the pulse and the
 * sentences fed through hh_core_pps_edge / hh_core_gps_bytes_at: the
 * snapshot's time source, TIME records in the drive file, the fall back. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#include "hhhost.h"
#include "hhsim.h"

static int failures, checks;
static const char *current = "";

#define CHECK(cond) do { checks++; if (!(cond)) { failures++; \
    printf("  FAIL %s:%d [%s] %s\n", __FILE__, __LINE__, current, #cond); } } while (0)
#define NEAR(a, b, eps) (fabs((double)(a) - (double)(b)) <= (eps))
#define TEST(name) static void name(void)
#define RUN(name) do { current = #name; int before = failures; name(); \
    printf("%-34s %s\n", #name, failures == before ? "ok" : "FAILED"); } while (0)

static char TMP[200];
static int tmp_n;
static const char *fresh_dir(void)
{
    static char path[256];
    snprintf(path, sizeof(path), "%s/sd%d", TMP, tmp_n++);
    mkdir(path, 0777);
    return path;
}

/* ---- the simulated receiver ---------------------------------------------------- */

#define U0 1794746119LL                         /* 2026-11-15 12:35:19 UTC */

static uint32_t rng_state = 1;
static double frand(void) { rng_state = rng_state * 1664525u + 1013904223u; return (double)(rng_state >> 8) / 16777216.0; }
static double jit(double amp) { return (frand() * 2 - 1) * amp; }      /* uniform, +-amp */

typedef struct {
    double ppm;                 /* the device crystal's error */
    int64_t dev0;               /* device time at true time 0 */
    double isr_jitter_us;       /* +- on every edge */
    int64_t step_us;            /* added to the device clock from step_at_s on (a timer jump) */
    int step_at_s;
} sim_t;

/* device time of true time `true_us` */
static int64_t dev_of(const sim_t *s, double true_us, int sec)
{
    return s->dev0 + (int64_t)llround(true_us * (1.0 + s->ppm * 1e-6)) + (sec >= s->step_at_s && s->step_at_s ? s->step_us : 0);
}

/* the true unix time (us) of a device time, with no steps: the reference the filter is judged by */
static double truth_of(const sim_t *s, int64_t dev_us) { return (double)(dev_us - s->dev0) / (1.0 + s->ppm * 1e-6); }

typedef struct {
    int drop_edge_from, drop_edge_to;       /* seconds whose pulse is missing */
    int drop_nmea_from, drop_nmea_to;       /* seconds whose sentences are missing (no fix) */
    int noise;                              /* a noise pulse in every second that is a multiple of this, 0 = none */
    int wrong_second_at;                    /* the sentence of this second carries the next second's time (arrived late) */
} faults_t;

typedef struct {
    sim_t s;
    faults_t f;
    hh_pps_t p;
    double worst_after_lock_us;             /* worst |model - truth| over the run once locked and active */
    int active_seconds;
    uint32_t outliers_seen;
} run_t;

static void run_init(run_t *r, double ppm)
{
    memset(r, 0, sizeof(*r));
    r->s.ppm = ppm;
    r->s.dev0 = 3600LL * 1000000;           /* it booted an hour before true time 0 */
    r->s.isr_jitter_us = 8;
    r->f.drop_edge_from = r->f.drop_nmea_from = -1;      /* nothing dropped: from -1 to -2 is an empty range */
    r->f.drop_edge_to = r->f.drop_nmea_to = -2;
    r->f.wrong_second_at = -1;
    hh_pps_init(&r->p);
}

/* second `sec` of the run: its pulse, the noise, then the two sentences; the model is judged at the end of it */
static void run_second(run_t *r, int sec)
{
    int64_t utc = U0 + sec;
    double true_edge = (double)utc * 1e6;
    int64_t edge = dev_of(&r->s, true_edge, sec) + (int64_t)llround(frand() * r->s.isr_jitter_us);
    int nmea_on = !(sec >= r->f.drop_nmea_from && sec <= r->f.drop_nmea_to);
    int64_t u = utc + (sec == r->f.wrong_second_at ? 1 : 0);
    if (!(sec >= r->f.drop_edge_from && sec <= r->f.drop_edge_to)) hh_pps_edge(&r->p, edge);
    if (nmea_on) hh_pps_time(&r->p, dev_of(&r->s, true_edge + 120000, sec), u);        /* GGA */
    if (r->f.noise && sec % r->f.noise == 0)                                           /* a spike between GGA and RMC */
        hh_pps_edge(&r->p, dev_of(&r->s, true_edge + 300000 + jit(100000), sec));
    if (nmea_on) hh_pps_time(&r->p, dev_of(&r->s, true_edge + 650000, sec), u);        /* RMC: the pulse is spent, no second pair */
    int64_t now = dev_of(&r->s, true_edge + 900000, sec);
    if (hh_pps_active(&r->p, now) && !r->s.step_at_s) {
        double err = fabs((double)hh_pps_unix_us(&r->p, now) - truth_of(&r->s, now));
        if (err > r->worst_after_lock_us) r->worst_after_lock_us = err;
        r->active_seconds++;
    }
}

static void run_seconds(run_t *r, int from, int to) { for (int s = from; s < to; s++) run_second(r, s); }

/* ---- the filter ------------------------------------------------------------------ */

TEST(test_clean_lock_and_accuracy)
{
    static const double ppms[] = {0, 18, -23, 45};
    for (unsigned i = 0; i < sizeof(ppms) / sizeof(ppms[0]); i++) {
        run_t r;
        rng_state = 7 + i;
        run_init(&r, ppms[i]);
        /* the first pulse is not believed (nothing a second before it), and not PPS before three agreeing pairs */
        run_second(&r, 0);
        CHECK(r.p.pairs == 0 && !hh_pps_active(&r.p, dev_of(&r.s, (double)(U0 * 1000000LL) + 900000, 0)));
        run_second(&r, 1);
        run_second(&r, 2);
        CHECK(!r.p.locked && r.p.pairs == 2);
        run_second(&r, 3);
        CHECK(r.p.locked && r.p.pairs == 3 && r.p.outliers == 0);
        r.worst_after_lock_us = 0;
        run_seconds(&r, 4, 300);
        CHECK(r.active_seconds >= 295);
        /* tens of microseconds with 8 us of interrupt jitter, with a crystal 45 ppm off */
        CHECK(r.worst_after_lock_us < 30);
        CHECK(NEAR(r.p.drift * 1e6, -ppms[i], 1.5));            /* the crystal's error is found (the offset moves the other way) */
        CHECK(hh_pps_jitter_us(&r.p) < 12 && hh_pps_err_us(&r.p, r.p.last_pair_us) < 20);
        CHECK(r.p.glitches == 0 && r.p.gaps == 0 && r.p.stale == 0 && r.p.relocks == 0);
    }
}

TEST(test_error_estimate_follows_the_noise)
{
    run_t quiet, noisy;
    rng_state = 3;
    run_init(&quiet, 10); quiet.s.isr_jitter_us = 2;
    run_init(&noisy, 10); noisy.s.isr_jitter_us = 60;
    run_seconds(&quiet, 0, 120);
    run_seconds(&noisy, 0, 120);
    uint32_t eq = hh_pps_err_us(&quiet.p, quiet.p.last_pair_us), en = hh_pps_err_us(&noisy.p, noisy.p.last_pair_us);
    CHECK(eq < 5 && en > 4 * eq && en < 80);
    /* and the estimate grows while no pair comes: 2 us per second */
    CHECK(hh_pps_err_us(&quiet.p, quiet.p.last_pair_us + 5000000) >= eq + 9);
}

TEST(test_missing_pulses)
{
    run_t r;
    rng_state = 11;
    run_init(&r, 20);
    r.f.drop_edge_from = 40; r.f.drop_edge_to = 43;             /* four pulses gone, the sentences keep coming */
    run_seconds(&r, 0, 39);
    r.worst_after_lock_us = 0;
    run_seconds(&r, 39, 200);
    /* no pair for the four seconds; the one after is a 5 s gap on the edge chain and pairs on the sentence's second */
    CHECK(r.p.gaps == 1 && r.p.missed == 4 && r.p.relocks == 0 && r.p.outliers == 0 && r.p.glitches == 0);
    CHECK(r.p.locked && r.worst_after_lock_us < 40);
    CHECK(r.p.pairs == 195);                                     /* 200 seconds: the first pulse is not believed, 4 are missing */
}

TEST(test_noise_pulses)
{
    run_t r;
    rng_state = 13;
    run_init(&r, -15);
    r.f.noise = 3;                                               /* a spike 300 ms (+-100) after every third pulse */
    run_seconds(&r, 0, 300);
    CHECK(r.p.glitches == 100 && r.p.relocks == 0 && r.p.outliers == 0 && r.p.resets == 0);
    CHECK(r.p.locked && r.worst_after_lock_us < 40);
    CHECK(r.p.pairs == 299);
}

TEST(test_edge_interval_rule)
{
    hh_pps_t p;
    hh_pps_init(&p);
    int64_t t = 1000000;
    CHECK(hh_pps_edge(&p, t) == 0);                              /* the first of a chain: noted, not believed */
    CHECK(hh_pps_edge(&p, t += 1000000 + 999) == 1);             /* 1 s + 0.999 ms */
    CHECK(hh_pps_edge(&p, t += 1000000 - 999) == 1);             /* back: 1 s - 0.999 ms */
    CHECK(hh_pps_edge(&p, t + 1000000 + 1500) == 0);             /* 1.5 ms off: not a pulse */
    CHECK(hh_pps_edge(&p, t + 500000) == 0);                     /* half a second: not a pulse */
    CHECK(hh_pps_edge(&p, t += 1000000) == 1);                   /* the real one, still on the chain */
    CHECK(p.glitches == 2 && p.resets == 0);
    /* a gap of 3 s (two missing pulses) is on the chain, with a little more room for the crystal */
    CHECK(hh_pps_edge(&p, t += 3000000 + 1100) == 1 && p.gaps == 1 && p.missed == 2);
    CHECK(hh_pps_edge(&p, t + 1000000 + 1500) == 0);
}

TEST(test_chain_starts_on_a_glitch)
{
    /* the very first edge is noise: the real pulses fit no chain until three have gone by, then the chain moves */
    run_t r;
    rng_state = 17;
    run_init(&r, 5);
    hh_pps_edge(&r.p, dev_of(&r.s, (double)(U0 * 1000000LL) - 370000, 0));
    run_seconds(&r, 0, 30);
    CHECK(r.p.resets == 1 && r.p.glitches == 3 && r.p.locked);
    CHECK(r.p.pairs >= 25);
}

TEST(test_sentence_matched_to_the_wrong_pulse)
{
    run_t r;
    rng_state = 19;
    run_init(&r, 12);
    r.f.wrong_second_at = 60;
    run_seconds(&r, 0, 59);
    int64_t before = hh_pps_unix_us(&r.p, dev_of(&r.s, (double)((U0 + 59) * 1000000LL) + 900000, 59));
    run_second(&r, 59);
    run_second(&r, 60);                                          /* the pair is a second off: an outlier, ignored */
    CHECK(r.p.outliers == 1 && r.p.locked);
    run_seconds(&r, 61, 120);
    CHECK(r.p.outliers == 1 && r.p.relocks == 0);
    (void)before;
    r.worst_after_lock_us = 0;
    run_seconds(&r, 120, 150);
    CHECK(r.worst_after_lock_us < 30 && hh_pps_active(&r.p, r.p.last_pair_us));
}

TEST(test_clock_step_relocks)
{
    run_t r;
    rng_state = 23;
    run_init(&r, 8);
    r.s.step_us = 5000; r.s.step_at_s = 50;                      /* the device timer jumps 5 ms: nothing a crystal does */
    run_seconds(&r, 0, 50);
    CHECK(r.p.locked && r.p.glitches == 0);
    run_seconds(&r, 50, 53);
    /* the 1 s chain breaks (the edges are 5 ms late), the third such edge starts a new chain */
    CHECK(r.p.glitches == 3 && r.p.resets == 1);
    run_seconds(&r, 53, 57);
    /* its pairs are 5 ms off the held model: outliers, each of them ignored */
    CHECK(r.p.outliers == 4 && r.p.relocks == 0 && r.p.locked);
    run_second(&r, 57);
    CHECK(r.p.relocks == 1 && !r.p.locked);                      /* the fifth means the model is wrong, not the pairs */
    run_seconds(&r, 58, 60);
    CHECK(r.p.locked);                                           /* three agreeing pairs and it is back */
    run_seconds(&r, 60, 100);
    int64_t now = dev_of(&r.s, (double)((U0 + 99) * 1000000LL) + 900000, 99);
    double err = fabs((double)hh_pps_unix_us(&r.p, now) - ((double)((U0 + 99) * 1000000LL) + 900000));
    CHECK(err < 40 && r.p.relocks == 1 && r.p.outliers == 5);
}

TEST(test_fix_lost_and_found)
{
    run_t r;
    rng_state = 29;
    run_init(&r, 25);
    r.f.drop_nmea_from = 60; r.f.drop_nmea_to = 89;              /* no sentences for 30 s; the pulse goes on */
    run_seconds(&r, 0, 60);
    CHECK(hh_pps_active(&r.p, dev_of(&r.s, (double)((U0 + 60) * 1000000LL), 60)));
    /* held for HH_PPS_HOLD_US, with a growing error ... */
    int64_t t_last = r.p.last_pair_us;
    CHECK(hh_pps_active(&r.p, t_last + 9000000));
    CHECK(!hh_pps_active(&r.p, t_last + 10000001));
    CHECK(hh_pps_err_us(&r.p, t_last + 9000000) > hh_pps_err_us(&r.p, t_last) + 15);
    /* ... and the model is still right to a few tens of us after 9 s (the drift was learnt) */
    int64_t t9 = t_last + 9000000;
    double err9 = fabs((double)hh_pps_unix_us(&r.p, t9) - truth_of(&r.s, t9));
    CHECK(err9 < 30);
    run_seconds(&r, 60, 90);
    CHECK(r.p.pairs == 59);
    run_seconds(&r, 90, 100);
    /* the first pair after 30 s is within the gate of the held model: no relock, no new lock needed */
    CHECK(r.p.locked && r.p.relocks == 0 && r.p.outliers == 0 && hh_pps_active(&r.p, r.p.last_pair_us));
    CHECK(r.p.pairs == 69);
}

TEST(test_random_faults)
{
    /* many seeds: crystals of -40..+40 ppm, 1 pulse in 15 lost, a noise spike every 7th second, an interrupt that is
     * 100 us late once in 20 (a flash write holding the interrupt off): never worse than 100 us once locked (a late interrupt is at most one
     * spike of its own size, and the filter moves by a fraction of it) */
    double worst = 0;
    int bad_seeds = 0;
    for (uint32_t seed = 1; seed <= 60; seed++) {
        run_t r;
        rng_state = seed * 7919;
        run_init(&r, jit(40));
        r.f.noise = 7;
        for (int sec = 0; sec < 400; sec++) {
            r.f.drop_edge_from = r.f.drop_edge_to = frand() < 1.0 / 15 ? sec : -1;
            r.s.isr_jitter_us = frand() < 0.05 ? 100 : 6;
            run_second(&r, sec);
        }
        if (r.worst_after_lock_us > worst) worst = r.worst_after_lock_us;
        bad_seeds += r.worst_after_lock_us > 100 || !r.p.locked || r.active_seconds < 380;
    }
    CHECK(bad_seeds == 0);
    CHECK(worst < 100);
}

TEST(test_stale_sentence_is_not_paired)
{
    hh_pps_t p;
    hh_pps_init(&p);
    hh_pps_edge(&p, 4000000);
    hh_pps_edge(&p, 5000000);
    CHECK(hh_pps_time(&p, 5000000 + 20000, 1794746119) == HH_PPS_STALE);   /* 20 ms after: it was the last second's */
    hh_pps_edge(&p, 6000000);
    CHECK(hh_pps_time(&p, 6000000 + 950000, 1794746120) == HH_PPS_STALE);  /* 950 ms after: too late to trust */
    hh_pps_edge(&p, 7000000);
    CHECK(hh_pps_time(&p, 7000000 + 120000, 1794746121) == HH_PPS_PAIR);
    CHECK(hh_pps_time(&p, 7000000 + 650000, 1794746121) == HH_PPS_NONE);    /* one pair per pulse */
    /* a pulse nobody has vouched for yet (the first after noise) is not paired either */
    hh_pps_edge(&p, 7300000);
    CHECK(hh_pps_time(&p, 7300000 + 120000, 1794746122) == HH_PPS_NONE);
    CHECK(p.stale == 2 && p.pairs == 1);
}

TEST(test_gps_without_pulse_never_locks)
{
    hh_pps_t p;
    hh_pps_init(&p);
    for (int s = 0; s < 60; s++) CHECK(hh_pps_time(&p, 1000000LL * s + 120000, U0 + s) == HH_PPS_NONE);
    CHECK(!p.locked && !hh_pps_active(&p, 60000000) && p.pairs == 0);
}

/* ---- the engine -------------------------------------------------------------------- */

static void nmea_sentence(char *out, size_t cap, const char *body)
{
    uint8_t sum = 0;
    for (const char *c = body; *c; c++) sum ^= (uint8_t)*c;
    snprintf(out, cap, "$%s*%02X\r\n", body, sum);
}

/* GGA and RMC for the second `utc` (unix seconds) */
static void sentences(int64_t utc, int fix, char *gga, char *rmc)
{
    time_t tt = (time_t)utc;
    struct tm tm;
    char body[160], hms[32], dmy[32];
    gmtime_r(&tt, &tm);
    snprintf(hms, sizeof(hms), "%02d%02d%02d", tm.tm_hour, tm.tm_min, tm.tm_sec);
    snprintf(dmy, sizeof(dmy), "%02d%02d%02d", tm.tm_mday, tm.tm_mon + 1, tm.tm_year % 100);
    snprintf(body, sizeof(body), "GPGGA,%s,3217.100,N,09007.200,W,%d,08,0.9,95.4,M,-29.0,M,,", hms, fix ? 1 : 0);
    nmea_sentence(gga, 128, body);
    snprintf(body, sizeof(body), "GPRMC,%s,%c,3217.100,N,09007.200,W,022.4,084.4,%s,003.1,W", hms, fix ? 'A' : 'V', dmy);
    nmea_sentence(rmc, 128, body);
}

/* The engine's clock is the harness's (SIM.now, seconds); the device crystal is ideal here, so device us = SIM.now * 1e6.
 * Second k starts at SIM time T0 + k and is UTC U0 + k. */
typedef struct { hhhost_t *h; double T0; int64_t u0; } eng_t;

static void eng_second(eng_t *e, int k, int pulse, int sentences_on, int fix, double isr_jitter_us)
{
    double t = e->T0 + k;
    int64_t edge = (int64_t)llround(t * 1e6 + frand() * isr_jitter_us);
    char gga[128], rmc[128];
    hhhost_run_until(e->h, t);
    sentences(e->u0 + k, fix, gga, rmc);
    if (pulse) hh_core_pps_edge(hhhost_core(e->h), edge);
    hhhost_run_until(e->h, t + 0.12);
    if (sentences_on) hh_core_gps_bytes_at(hhhost_core(e->h), (const uint8_t *)gga, strlen(gga), (int64_t)llround((t + 0.12) * 1e6));
    hhhost_run_until(e->h, t + 0.65);
    if (sentences_on) hh_core_gps_bytes_at(hhhost_core(e->h), (const uint8_t *)rmc, strlen(rmc), (int64_t)llround((t + 0.65) * 1e6));
    hhhost_run_until(e->h, t + 0.9);
}

/* TIME records of the first drive file: source and error of each, in order */
typedef struct { int n; uint8_t source[64]; uint16_t err[64]; int32_t drift_ppb[64]; int64_t unix_us[64]; uint64_t t_us[64]; } times_t;

static void read_times(const char *root, times_t *out)
{
    char path[400];
    memset(out, 0, sizeof(*out));
    snprintf(path, sizeof(path), "%s/DRIVES/D000001.HHD", root);
    FILE *f = fopen(path, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *buf = malloc((size_t)size);
    if (fread(buf, 1, (size_t)size, f) != (size_t)size) { fclose(f); free(buf); return; }
    fclose(f);
    size_t pos = sizeof(hh_file_hdr_t);
    while (pos + sizeof(hh_block_hdr_t) <= (size_t)size) {
        hh_block_hdr_t b;
        memcpy(&b, buf + pos, sizeof(b));
        if (b.magic != HH_BLOCK_MAGIC || pos + sizeof(b) + b.payload_len + 4 > (size_t)size) break;
        if (b.type == HH_B_TIME)
            for (uint32_t o = 0; o + sizeof(hh_rec_time_t) <= b.payload_len && out->n < 64; o += sizeof(hh_rec_time_t)) {
                hh_rec_time_t r;
                memcpy(&r, buf + pos + sizeof(b) + o, sizeof(r));
                out->source[out->n] = r.source; out->err[out->n] = r.err_us; out->drift_ppb[out->n] = r.drift_ppb;
                out->unix_us[out->n] = r.unix_us; out->t_us[out->n] = r.t_us;
                out->n++;
            }
        pos += sizeof(b) + b.payload_len + 4;
    }
    free(buf);
}

TEST(test_engine_pps_lifecycle)
{
    hh_snapshot_t s;
    const char *root = fresh_dir();
    rng_state = 31;
    hhsim_reset();
    hhhost_t *h = hhhost_new(root, NULL, 0, "", hhsim_callbacks(), 4, 0);
    hhhost_run_until(h, 2.0);                                   /* the drive is open before any GPS */
    hhhost_snapshot(h, &s);
    CHECK(s.time_source == HH_TS_NONE && s.unix_us == 0 && s.time_err_us == 0 && !s.time_valid);

    eng_t e = {h, 3.0, U0 + 100000};

    /* sentences only: the clock is set from them, as before, and is only good to the second */
    for (int k = 0; k < 4; k++) eng_second(&e, k, 0, 1, 1, 0);
    hhhost_snapshot(h, &s);
    CHECK(s.time_source == HH_TS_NMEA && s.time_valid && s.time_err_us == 1000000 && s.unix_us != 0);
    CHECK(hhhost_time_set(h) == (e.u0 + 0) * 1000000LL || hhhost_time_set(h) != 0);

    /* the pulse arrives: three pairs and the source is PPS, with an error estimate of a few us */
    for (int k = 4; k < 14; k++) eng_second(&e, k, 1, 1, 1, 6);
    hhhost_snapshot(h, &s);
    CHECK(s.time_source == HH_TS_PPS && s.time_valid && s.time_err_us >= 1 && s.time_err_us < 30);
    double true_us = (double)e.u0 * 1e6 + ((double)s.time_t_us - e.T0 * 1e6);
    CHECK(NEAR((double)s.unix_us, true_us, 30));
    hh_pps_info_t pi;
    hh_core_pps_info(hhhost_core(h), &pi);
    CHECK(pi.source == HH_TS_PPS && pi.locked && pi.pairs >= 8 && pi.edges == 10 && pi.outliers == 0);
    CHECK(NEAR(pi.drift_ppm, 0, 4) && pi.jitter_us < 12);
    CHECK(NEAR((double)pi.offset_us, (double)e.u0 * 1e6 - e.T0 * 1e6, 40));

    CHECK(hhhost_time_set(h) != 0);                             /* the system clock was put right when it locked (checked below) */

    /* a no-fix second: V in the RMC, GGA quality 0. No pair, the source holds */
    eng_second(&e, 14, 1, 1, 0, 6);
    hhhost_snapshot(h, &s);
    CHECK(s.time_source == HH_TS_PPS);

    /* the pulse stops, sentences go on: the PPS time is held for 10 s, then the source is NMEA */
    for (int k = 15; k < 23; k++) eng_second(&e, k, 0, 1, 1, 0);
    hhhost_snapshot(h, &s);
    CHECK(s.time_source == HH_TS_PPS && s.time_err_us > 10);    /* 8 s without a pair: the estimate has grown */
    for (int k = 23; k < 28; k++) eng_second(&e, k, 0, 1, 1, 0);
    hhhost_snapshot(h, &s);
    CHECK(s.time_source == HH_TS_NMEA && s.time_err_us == 1000000 && s.unix_us != 0);
    /* the clock it falls back to is the system clock, which the pulse had set: still right to a few ms */
    CHECK(NEAR((double)s.unix_us, (double)e.u0 * 1e6 + ((double)s.time_t_us - e.T0 * 1e6), 3000));

    /* and the pulse comes back */
    for (int k = 28; k < 38; k++) eng_second(&e, k, 1, 1, 1, 6);
    hhhost_snapshot(h, &s);
    CHECK(s.time_source == HH_TS_PPS);

    hhhost_free(h);

    /* the drive file: TIME 1 (GPS) ... 4 (PPS) ... 1 (back to the sentences) ... 4 (PPS again) */
    times_t t;
    read_times(root, &t);
    int pps_recs = 0, to_nmea = 0, first_pps = -1, seq_ok = 1;
    for (int i = 0; i < t.n; i++) {
        if (t.source[i] == HH_TIME_PPS) { pps_recs++; if (first_pps < 0) first_pps = i; seq_ok &= t.err[i] >= 1 && t.err[i] < 200; }
        if (i > 0 && t.source[i] == HH_TIME_GPS && t.source[i - 1] == HH_TIME_PPS) to_nmea++;
    }
    CHECK(t.n >= 4 && pps_recs >= 2 && to_nmea == 1 && first_pps > 0 && seq_ok);
    CHECK(t.source[0] == HH_TIME_GPS);                          /* the first GPS time, before the pulse */
    /* a PPS record is exact: its unix time against the true time of its device time */
    double err0 = (double)t.unix_us[first_pps] - ((double)e.u0 * 1e6 + ((double)t.t_us[first_pps] - e.T0 * 1e6));
    CHECK(fabs(err0) < 40);
    /* the minute refresh: another record at least 60 s later is not due in this short run */
    int unsorted = 0;
    for (int i = 1; i < t.n; i++) unsorted += t.t_us[i] < t.t_us[i - 1];
    CHECK(unsorted == 0);
}

TEST(test_engine_gga_pairs_first_and_midnight)
{
    hh_snapshot_t s;
    const char *root = fresh_dir();
    rng_state = 37;
    hhsim_reset();
    hhhost_t *h = hhhost_new(root, NULL, 0, "", hhsim_callbacks(), 5, 0);
    hhhost_run_until(h, 2.0);
    /* the second before midnight UTC, running through it: GGA has no date, the RMC's is carried over */
    int64_t u0 = (U0 / 86400 + 1) * 86400 - 6;                  /* 23:59:54 */
    eng_t e = {h, 3.0, u0};
    for (int k = 0; k < 14; k++) eng_second(&e, k, 1, 1, 1, 5);
    hh_pps_info_t pi;
    hh_core_pps_info(hhhost_core(h), &pi);
    hhhost_snapshot(h, &s);
    CHECK(pi.source == HH_TS_PPS && pi.outliers == 0 && pi.relocks == 0 && pi.pairs >= 12);
    double true_us = (double)u0 * 1e6 + ((double)s.time_t_us - e.T0 * 1e6);
    CHECK(NEAR((double)s.unix_us, true_us, 30));
    hhhost_free(h);
}

TEST(test_engine_no_pulse_is_unchanged)
{
    /* what the old firmware did: a clock set from the sentences once, and nothing else */
    hh_snapshot_t s;
    const char *root = fresh_dir();
    hhsim_reset();
    hhhost_t *h = hhhost_new(root, NULL, 0, "", hhsim_callbacks(), 6, 0);
    hhhost_run_until(h, 2.0);
    eng_t e = {h, 3.0, U0};
    for (int k = 0; k < 20; k++) eng_second(&e, k, 0, 1, 1, 0);
    hhhost_snapshot(h, &s);
    hh_pps_info_t pi;
    hh_core_pps_info(hhhost_core(h), &pi);
    CHECK(s.time_source == HH_TS_NMEA && pi.edges == 0 && pi.pairs == 0 && !pi.locked);
    /* set once (an RMC second, up to a second stale), then left alone while it agrees to within 2 s */
    CHECK(NEAR((double)s.unix_us, (double)(U0 + 19) * 1e6, 1.5e6));
    hhhost_free(h);
}

int main(void)
{
    const char *base = getenv("TMPDIR");
    snprintf(TMP, sizeof(TMP), "%s/hhpps.XXXXXX", base && *base ? base : "/tmp");
    if (!mkdtemp(TMP)) { perror("mkdtemp"); return 2; }
    if (sizeof(hh_rec_time_t) != 24) { printf("the TIME record has the wrong size\n"); return 2; }

    RUN(test_clean_lock_and_accuracy);
    RUN(test_error_estimate_follows_the_noise);
    RUN(test_missing_pulses);
    RUN(test_noise_pulses);
    RUN(test_edge_interval_rule);
    RUN(test_chain_starts_on_a_glitch);
    RUN(test_sentence_matched_to_the_wrong_pulse);
    RUN(test_clock_step_relocks);
    RUN(test_fix_lost_and_found);
    RUN(test_random_faults);
    RUN(test_stale_sentence_is_not_paired);
    RUN(test_gps_without_pulse_never_locks);
    RUN(test_engine_pps_lifecycle);
    RUN(test_engine_gga_pairs_first_and_midnight);
    RUN(test_engine_no_pulse_is_unchanged);
    printf("%d checks, %d failed (files left in %s)\n", checks, failures, TMP);
    return failures ? 1 : 0;
}
