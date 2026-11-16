/* STUB: fake-data generator behind the hh-core API (hh_data.h).
 *
 * Not the data side. No link, no GPS, no SD card: every number here is made
 * up. It exists so the UI can be built, simulated on a PC and shown on the
 * board before components/hhcore lands. The channel names, limits and the
 * backend number are guesses and are not a contract; the UI finds channels
 * by name with fallbacks (see main/ui/ui_data.c).
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "hh_data.h"
#include "hh_stub.h"

#ifdef ESP_PLATFORM
#include "esp_timer.h"
static int64_t platform_now_us(void) { return esp_timer_get_time(); }
#else
#include <time.h>
static int64_t platform_now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}
#endif

#define NO NAN

/* lo hi warn_lo warn_hi alarm_lo alarm_hi decimals verified rate_class kind */
static const hh_channel_info_t CH[] = {
    {"rpm",   "RPM",        "rpm",  0, 8000, NO, 6500, NO, 6900, 0, 1, 0, 0},
    {"vss",   "Speed",      "km/h", 0, 200,  NO, NO,   NO, NO,   0, 0, 0, 0},
    {"ect",   "Coolant",    "\xC2\xB0""C", -40, 130, NO, 100, NO, 108, 0, 0, 1, 0},
    {"map",   "MAP",        "kPa",  0, 110,  NO, NO,   NO, NO,   0, 0, 0, 0},
    {"batt",  "Battery",    "V",    8, 16,   12.8f, NO, 11.5f, 15.2f, 1, 1, 1, 0},
    {"tps",   "Throttle",   "%",    0, 100,  NO, NO,   NO, NO,   0, 0, 0, 0},
    {"iat",   "Intake air", "\xC2\xB0""C", -40, 100, NO, 45, NO, NO, 0, 0, 1, 0},
    {"inj",   "Injector",   "ms",   0, 20,   NO, NO,   NO, NO,   2, 0, 0, 0},
    {"ign",   "Ignition",   "\xC2\xB0", -10, 50, NO, NO, NO, NO,  1, 0, 0, 0},
    {"o2",    "O2",         "V",    0, 1.2f, NO, NO,   NO, NO,   2, 0, 0, 0},
    {"stft",  "Short trim", "%",    -30, 30, -12, 15,  NO, NO,   1, 0, 0, 0},
    {"ltft",  "Long trim",  "%",    -30, 30, -12, 12,  NO, NO,   1, 0, 1, 0},
    {"baro",  "Baro",       "kPa",  60, 110, NO, NO,   NO, NO,   0, 0, 1, 0},
    {"iacv",  "Idle valve", "%",    0, 100,  NO, NO,   NO, NO,   0, 0, 1, 0},
    {"eld",   "Elec load",  "A",    0, 100,  NO, NO,   NO, NO,   0, 0, 1, 0},
    {"knock", "Knock",      "",     0, 10,   NO, 2,    NO, NO,   0, 0, 1, 0},
    {"cl",    "Closed loop","",     0, 1,    NO, NO,   NO, NO,   0, 0, 1, 1},
    {"vtec",  "VTEC",       "",     0, 1,    NO, NO,   NO, NO,   0, 0, 1, 1},
    {"ac",    "A/C",        "",     0, 1,    NO, NO,   NO, NO,   0, 0, 1, 1},
    {"fan",   "Rad fan",    "",     0, 1,    NO, NO,   NO, NO,   0, 0, 1, 1},
    {"pump",  "Fuel pump",  "",     0, 1,    NO, NO,   NO, NO,   0, 1, 1, 1},
    {"mil",   "MIL",        "",     0, 1,    NO, NO,   NO, 0.5f, 0, 0, 1, 1},
};
#define N_CH ((uint16_t)(sizeof(CH) / sizeof(CH[0])))
enum { C_RPM, C_VSS, C_ECT, C_MAP, C_BATT, C_TPS, C_IAT, C_INJ, C_IGN, C_O2, C_STFT, C_LTFT,
       C_BARO, C_IACV, C_ELD, C_KNOCK, C_CL, C_VTEC, C_AC, C_FAN, C_PUMP, C_MIL };

static int64_t (*clock_us)(void) = platform_now_us;
static hh_stub_scene_t scene = HH_SCENE_DEMO;
static int64_t t0_us;
static uint32_t seq, marks, acks;

void hh_stub_set_scene(hh_stub_scene_t s) { scene = s; }
void hh_stub_set_clock(int64_t (*now_us)(void)) { clock_us = now_us ? now_us : platform_now_us; }
uint32_t hh_stub_marks(void) { return marks; }
uint32_t hh_stub_acks(void) { return acks; }

const hh_channel_info_t *hh_channels(uint16_t *n)
{
    if (n) *n = N_CH;
    return CH;
}

void hh_mark(void) { marks++; }
void hh_mark_at(int64_t t_us) { (void)t_us; marks++; }
void hh_request_dtc(void) {}
int16_t hh_channel_index(const char *name)
{
    for (uint16_t i = 0; i < N_CH; i++) if (strcmp(CH[i].name, name) == 0) return (int16_t)i;
    return -1;
}
void hh_ack_alarm(void) { acks++; }
void hh_core_start(void) { t0_us = clock_us(); }

static uint8_t level(const hh_channel_info_t *c, float v)
{
    if ((!isnan(c->alarm_hi) && v >= c->alarm_hi) || (!isnan(c->alarm_lo) && v <= c->alarm_lo)) return 2;
    if ((!isnan(c->warn_hi) && v >= c->warn_hi) || (!isnan(c->warn_lo) && v <= c->warn_lo)) return 1;
    return 0;
}

void hh_snapshot(hh_snapshot_t *out)
{
    int64_t now = clock_us();
    if (!t0_us) t0_us = now;
    float t = (float)((now - t0_us) / 1000) / 1000.0f;
    hh_stub_scene_t s = scene;
    if (s == HH_SCENE_DEMO) {
        static const hh_stub_scene_t order[] = {
            HH_SCENE_CONNECTING, HH_SCENE_DRIVE, HH_SCENE_DRIVE, HH_SCENE_WARN, HH_SCENE_DRIVE,
            HH_SCENE_ALARM, HH_SCENE_DRIVE, HH_SCENE_STALL, HH_SCENE_LINK_LOST };
        s = order[((uint32_t)(t / 8.0f)) % (sizeof(order) / sizeof(order[0]))];
    }

    memset(out, 0, sizeof(*out));
    out->seq = ++seq;
    out->t_us = now;
    out->n_ch = N_CH;
    float *v = out->value;
    float wave = sinf(t * 0.7f), fast = sinf(t * 5.3f);

    v[C_RPM]  = 2950 + 780 * wave + 25 * fast;
    v[C_VSS]  = 73 + 9 * sinf(t * 0.31f);
    v[C_ECT]  = 87 + 1.2f * sinf(t * 0.05f);
    v[C_MAP]  = 58 + 24 * sinf(t * 0.9f);
    v[C_BATT] = 14.1f + 0.08f * fast;
    v[C_TPS]  = 22 + 14 * sinf(t * 0.9f);
    v[C_IAT]  = 31;
    v[C_INJ]  = 3.4f + 1.1f * sinf(t * 0.9f);
    v[C_IGN]  = 24.5f + 6 * wave;
    v[C_O2]   = 0.45f + 0.38f * sinf(t * 3.1f);
    v[C_STFT] = 1.5f + 3.2f * sinf(t * 1.7f);
    v[C_LTFT] = -2.3f;
    v[C_BARO] = 101;
    v[C_IACV] = 31 + 2 * fast;
    v[C_ELD]  = 23 + 3 * wave;
    v[C_KNOCK] = 0;
    v[C_CL] = 1; v[C_VTEC] = v[C_RPM] > 5500; v[C_AC] = 0; v[C_FAN] = 0; v[C_PUMP] = 1; v[C_MIL] = 0;

    out->link.state = 2;
    out->link.backend = 1;
    out->link.hz_fast = 10.8f + 0.2f * fast;
    out->link.reads = (uint32_t)(t * 43);
    out->pc.mode = 1; out->pc.frames = (uint32_t)(t * 80); out->pc.dropped = 0;    /* streaming to a laptop, GPS on P1 */
    out->link.errors = (uint32_t)(t / 40);
    snprintf(out->link.ecu_id, sizeof(out->link.ecu_id), "37820-P2P");

    out->gps.fix = 3; out->gps.sats = 9;
    out->gps.lat = 32.27412 + 0.0001 * t; out->gps.lon = -90.13021 - 0.00007 * t;
    out->gps.speed_kmh = v[C_VSS] - 1.4f;
    out->gps.heading = 247 + 4 * sinf(t * 0.2f);
    out->gps.hdop = 0.9f;

    out->log.sd_ok = 1; out->log.recording = 1; out->log.drive_no = 14;
    out->log.bytes = (uint32_t)(t * 5200) + 2412544;
    out->log.free_mb = 14820.5f - t * 0.005f;

    out->stall.armed = 1; out->stall.count = 2;
    snprintf(out->stall.last_reason, sizeof(out->stall.last_reason), "RPM 0 after running");

    uint8_t ecu_fresh = 1;
    switch (s) {
    case HH_SCENE_WARN:
        v[C_IAT] = 46;
        break;
    case HH_SCENE_ALARM:
        v[C_ECT] = 109; v[C_BATT] = 12.6f; v[C_FAN] = 1;
        break;
    case HH_SCENE_STALL:
        v[C_RPM] = 0; v[C_VSS] = 31; v[C_MAP] = 99; v[C_BATT] = 12.1f; v[C_INJ] = 0; v[C_IGN] = 0;
        v[C_TPS] = 0; v[C_IACV] = 0; v[C_CL] = 0; v[C_PUMP] = 0; v[C_MIL] = 1;
        out->gps.speed_kmh = 30;
        out->stall.triggered = 1; out->stall.count = 3;
        break;
    case HH_SCENE_LINK_LOST:
        ecu_fresh = 0;
        out->link.state = 4; out->link.hz_fast = 0; out->link.errors += 37;
        break;
    case HH_SCENE_CONNECTING:
        ecu_fresh = 0;
        out->link.state = 1; out->link.hz_fast = 0; out->link.reads = 0; out->link.errors = 0;
        out->link.ecu_id[0] = 0;
        out->gps.fix = 0; out->gps.sats = 2; out->gps.speed_kmh = 0; out->gps.hdop = 99;
        out->log.sd_ok = 0; out->log.recording = 0; out->log.free_mb = 0; out->log.bytes = 0;
        out->stall.armed = 0; out->stall.count = 0; out->stall.last_reason[0] = 0;
        break;
    default:
        break;
    }

    out->top_alarm_ch = -1;
    for (uint16_t i = 0; i < N_CH; i++) {
        out->fresh[i] = ecu_fresh;
        out->alarm[i] = ecu_fresh ? level(&CH[i], v[i]) : 0;
        if (out->alarm[i] > out->top_alarm_level) {
            out->top_alarm_level = out->alarm[i];
            out->top_alarm_ch = (int16_t)i;
        }
    }
}
