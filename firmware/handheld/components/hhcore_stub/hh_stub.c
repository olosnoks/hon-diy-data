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

/* (lat, lon) along real roads (OpenStreetMap, ODbL): see hh_stub_set_route */
static const double ROUTE_STATE_ST[][2] = {
    {32.278223, -90.188691}, {32.278777, -90.187396}, {32.279046, -90.186813}, {32.279387, -90.186117},
    {32.279728, -90.185654}, {32.280345, -90.185139}, {32.281179, -90.184736}, {32.281547, -90.184549},
    {32.282368, -90.184161}, {32.282744, -90.183949}, {32.283111, -90.183768}, {32.283636, -90.183517},
    {32.284434, -90.183245}, {32.284815, -90.183181}, {32.285479, -90.183045}, {32.286375, -90.182873},
    {32.288257, -90.182506}, {32.289372, -90.182272}, {32.289932, -90.182166}, {32.290373, -90.182083},
    {32.290945, -90.181972}, {32.291580, -90.181849}, {32.293572, -90.181458}, {32.294021, -90.181372},
    {32.294462, -90.181291}, {32.294902, -90.181210}, {32.295397, -90.181113}, {32.296056, -90.180997},
    {32.296500, -90.180916}, {32.296863, -90.180881}, {32.298132, -90.180642}, {32.298673, -90.180514},
    {32.299122, -90.180436}, {32.299199, -90.180421},
};
static const double ROUTE_I55[][2] = {
    {32.279867, -90.172965}, {32.281085, -90.172332}, {32.282215, -90.171622}, {32.285298, -90.169084},
    {32.286718, -90.168195}, {32.287948, -90.167702}, {32.289248, -90.167410}, {32.290355, -90.167384},
    {32.293167, -90.167594}, {32.294342, -90.167613}, {32.296446, -90.167467}, {32.298401, -90.167146},
    {32.301510, -90.166583}, {32.303236, -90.166111}, {32.306654, -90.164751}, {32.309221, -90.163647},
    {32.310423, -90.162988}, {32.311366, -90.162216}, {32.313211, -90.160366}, {32.314753, -90.158852},
    {32.315739, -90.158265}, {32.317051, -90.158140}, {32.318988, -90.158740}, {32.320160, -90.158821},
    {32.321509, -90.158514}, {32.322604, -90.158472}, {32.324454, -90.159235}, {32.325738, -90.159828},
    {32.329349, -90.161325}, {32.330901, -90.161965}, {32.332301, -90.162334}, {32.333447, -90.162454},
    {32.334723, -90.162394}, {32.336185, -90.162053}, {32.337378, -90.161476}, {32.339054, -90.160398},
    {32.341119, -90.159029}, {32.342721, -90.157972}, {32.347534, -90.154838}, {32.348556, -90.154228},
    {32.349811, -90.153585}, {32.351449, -90.152907}, {32.352805, -90.152452}, {32.354415, -90.152048},
    {32.356329, -90.151674}, {32.360585, -90.150854}, {32.362079, -90.150566}, {32.363995, -90.150217},
    {32.365189, -90.149948}, {32.367256, -90.149543}, {32.371033, -90.148794}, {32.374038, -90.148218},
    {32.375692, -90.147900}, {32.377572, -90.147524}, {32.380109, -90.147068},
};

static hh_stub_route_t route = HH_ROUTE_NONE;
static double route_start_m;
static int64_t route_t0_us;

void hh_stub_set_scene(hh_stub_scene_t s) { scene = s; }
void hh_stub_set_route(hh_stub_route_t r, double start_m)
{
    route = r;
    route_start_m = start_m;
    route_t0_us = clock_us();
}

/* position, heading and speed at time t (s) on the route */
static void drive(float t, double *lat, double *lon, float *heading, float *kmh)
{
    const double (*pts)[2] = route == HH_ROUTE_I55 ? ROUTE_I55 : ROUTE_STATE_ST;
    int n = route == HH_ROUTE_I55 ? (int)(sizeof(ROUTE_I55) / sizeof(ROUTE_I55[0]))
                                  : (int)(sizeof(ROUTE_STATE_ST) / sizeof(ROUTE_STATE_ST[0]));
    *kmh = route == HH_ROUTE_I55 ? 113.0f : route == HH_ROUTE_PARKED ? 0.0f : 25.0f;
    double kx = 111319.49 * cos(pts[0][0] * 0.017453292519943295), ky = 110900.0, total = 0;
    for (int i = 1; i < n; i++) total += hypot((pts[i][0] - pts[i - 1][0]) * ky, (pts[i][1] - pts[i - 1][1]) * kx);
    double along = fmod(route_start_m + (double)*kmh / 3.6 * t, 2 * total);
    int back = along > total;
    if (back) along = 2 * total - along;
    for (int i = 1; i < n; i++) {
        double dy = (pts[i][0] - pts[i - 1][0]) * ky, dx = (pts[i][1] - pts[i - 1][1]) * kx, seg = hypot(dx, dy);
        if (along > seg && i < n - 1) { along -= seg; continue; }
        double f = seg > 0 ? along / seg : 0;
        if (f > 1) f = 1;
        *lat = pts[i - 1][0] + f * (pts[i][0] - pts[i - 1][0]);
        *lon = pts[i - 1][1] + f * (pts[i][1] - pts[i - 1][1]);
        float h = (float)(atan2(dx, dy) * 57.29577951308232);
        if (back) h += 180;
        *heading = fmodf(h + 360.0f, 360.0f);
        return;
    }
}
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
void hh_serial_port_set_map_hold(int (*hold)(int on)) { (void)hold; }
int hh_display_rotation(void) { return 0; }

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
    if (route == HH_ROUTE_NONE) {
        out->gps.lat = 32.27412 + 0.0001 * t; out->gps.lon = -90.13021 - 0.00007 * t;
        out->gps.speed_kmh = v[C_VSS] - 1.4f;
        out->gps.heading = 247 + 4 * sinf(t * 0.2f);
    } else {
        float kmh;
        drive((float)((now - route_t0_us) / 1000) / 1000.0f, &out->gps.lat, &out->gps.lon, &out->gps.heading, &kmh);
        out->gps.speed_kmh = kmh;
        v[C_VSS] = kmh + 1.4f;
    }
    out->gps.hdop = 0.9f;
    out->markers = marks;

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
