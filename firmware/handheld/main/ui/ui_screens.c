#include <math.h>
#include <stdio.h>
#include <string.h>

#include "ui_data.h"
#include "ui_map.h"
#include "ui_screens.h"
#include "ui_shapes.h"
#include "ui_theme.h"
#include "ui_tile.h"

#define GAP 4
#define COLS 3
#define TILE_W 154
#define GRID_X ((UI_W - COLS * TILE_W - (COLS - 1) * GAP) / 2)

/* hh-core has not published names for link.backend. Until it does the number
 * is shown; define HH_BACKEND_NAMES in hh_data.h as { "…", "…" } to name them. */
#ifdef HH_BACKEND_NAMES
static const char *const BACKENDS[] = HH_BACKEND_NAMES;
#endif

const char *ui_link_word(uint8_t state)
{
    static const char *const words[] = {"OFF", "CONNECTING", "OK", "ISO ONLY", "ERROR"};
    return state < 5 ? words[state] : "?";
}

void ui_link_shape(lv_obj_t *shape, uint8_t state)
{
    switch (state) {
    case 1:  ui_shape_set(shape, UI_SHAPE_HALF, TK(fg), TK(fg)); break;
    case 2:  ui_shape_set(shape, UI_SHAPE_DOT, TK(ok), TK(ok)); break;
    case 3:  ui_shape_set(shape, UI_SHAPE_HALF, TK(ok), TK(ok)); break;
    case 4:  ui_shape_set(shape, UI_SHAPE_CROSS, TK(alarm), TK(alarm)); break;
    default: ui_shape_set(shape, UI_SHAPE_SQUARE, TK(fg), TK(fg)); break;
    }
}

/* ---- Gauges: five big values and the stall catcher ------------------------ */

#define N_GAUGE 5
static ui_tile_t *gauge[N_GAUGE];
static struct { lv_obj_t *root, *shape, *title, *word, *line; int state; } stall;

static void gauges_create(lv_obj_t *body)
{
    /* Slots by channel name; hh-core's names are not fixed yet, so each slot
     * takes the first candidate that exists and otherwise stays empty. */
    static const char *const slots[N_GAUGE] = {
        "rpm|engine_rpm", "vss|speed|speed_kmh", "ect|coolant|clt", "map|afr|afr_est", "batt|battery|vbat|bat",
    };
    int h = (UI_BODY_H - 2 * GAP - GAP) / 2;
    for (int i = 0; i < N_GAUGE + 1; i++) {
        int x = GRID_X + (i % COLS) * (TILE_W + GAP), y = GAP + (i / COLS) * (h + GAP);
        if (i < N_GAUGE) {
            gauge[i] = ui_tile_create(body, UI_TILE_BIG, x, y, TILE_W, h, ui_find_channel(slots[i]));
            continue;
        }
        stall.root = ui_box(body, x, y, TILE_W, h);
        ui_border(stall.root, TK(rule), UI_RULE, LV_BORDER_SIDE_FULL);
        stall.shape = ui_shape_create(stall.root, 18);
        lv_obj_set_pos(stall.shape, 5, 4);
        stall.title = ui_label(stall.root, &hh_font_20, 28, 1, 0, LV_TEXT_ALIGN_LEFT);
        lv_label_set_text(stall.title, "STALL");
        stall.word = ui_label(stall.root, &hh_font_28, 5, 30, TILE_W - 14, LV_TEXT_ALIGN_LEFT);
        stall.line = ui_label(stall.root, &hh_font_16, 5, 72, TILE_W - 14, LV_TEXT_ALIGN_LEFT);
        stall.state = -1;
    }
}

static void gauges_update(const hh_snapshot_t *s, const ui_derived_t *d)
{
    for (int i = 0; i < N_GAUGE; i++) ui_tile_update(gauge[i], s, d->blink_on || d->acked);

    int state = s->stall.triggered ? 2 : s->stall.armed ? 1 : 0;
    if (state != stall.state) {
        stall.state = state;
        lv_color_t ground = state == 2 ? TK(alarm) : TK(panel), ink = state == 2 ? TK(on_alarm) : TK(fg);
        ui_fill(stall.root, ground);
        ui_ink(stall.title, ink); ui_ink(stall.word, ink); ui_ink(stall.line, ink);
        ui_shape_set(stall.shape, state == 2 ? UI_SHAPE_OCTAGON : state == 1 ? UI_SHAPE_RING : UI_SHAPE_SQUARE, ink, ink);
        ui_set_text(stall.word, state == 2 ? "CAUGHT" : state == 1 ? "ARMED" : "OFF");
    }
    char text[24];
    snprintf(text, sizeof(text), "COUNT %u", (unsigned)s->stall.count);
    ui_set_text(stall.line, text);
}

/* ---- All sensors: 12 tiles a page ----------------------------------------- */

static ui_tile_t *sensor[UI_SENSORS_PER_PAGE];

int ui_sensor_pages(void)
{
    uint16_t n;
    ui_channels(&n);
    return n ? (n + UI_SENSORS_PER_PAGE - 1) / UI_SENSORS_PER_PAGE : 1;
}

static void sensors_create(lv_obj_t *body, int page)
{
    uint16_t n;
    ui_channels(&n);
    int rows = UI_SENSORS_PER_PAGE / COLS;
    int pad = 2, gap = 2, h = (UI_BODY_H - 2 * pad - (rows - 1) * gap) / rows;
    for (int i = 0; i < UI_SENSORS_PER_PAGE; i++) {
        int ch = page * UI_SENSORS_PER_PAGE + i;
        sensor[i] = NULL;
        if (ch >= n) continue;
        sensor[i] = ui_tile_create(body, UI_TILE_SMALL, GRID_X + (i % COLS) * (TILE_W + GAP),
                                   pad + (i / COLS) * (h + gap), TILE_W, h, ch);
    }
}

static void sensors_update(const hh_snapshot_t *s, const ui_derived_t *d)
{
    for (int i = 0; i < UI_SENSORS_PER_PAGE; i++)
        if (sensor[i]) ui_tile_update(sensor[i], s, d->blink_on || d->acked);
}

/* ---- key / value rows for the GPS and Link screens ------------------------- */

typedef struct { lv_obj_t *shape, *value; } row_t;

/* KEY ........ [shape] value, with a 2 px divider under it */
static row_t row_create(lv_obj_t *body, int x, int y, int w, int h, const char *key, const lv_font_t *font, bool shape)
{
    row_t r = {0};
    lv_obj_t *box = ui_box(body, x, y, w, h);
    ui_border(box, TK(rule2), UI_RULE, LV_BORDER_SIDE_BOTTOM);
    lv_obj_t *k = ui_label(box, &hh_font_16, 2, (h - UI_RULE - 20) / 2, 0, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(k, key);
    ui_ink(k, TK(fg2));
    lv_obj_update_layout(k);
    int key_w = lv_obj_get_width(k) + 8;
    int line = lv_font_get_line_height(font);
    r.value = ui_label(box, font, key_w, (h - UI_RULE - line) / 2, w - key_w - 2, LV_TEXT_ALIGN_RIGHT);
    ui_ink(r.value, TK(fg));
    if (shape) {
        r.shape = ui_shape_create(box, 16);
        lv_obj_set_pos(r.shape, key_w, (h - UI_RULE - 16) / 2);
    }
    return r;
}

/* ---- GPS / trip ------------------------------------------------------------ */

static struct { lv_obj_t *speed, *heading, *pos; row_t fix, hdop, time, dist, drive; } gps;

static const char *cardinal(float deg)
{
    static const char *const names[] = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"};
    int i = (int)((deg + 22.5f) / 45.0f);
    return names[((i % 8) + 8) % 8];
}

static void gps_create(lv_obj_t *body)
{
    int left_w = 222, h = UI_BODY_H - 2 * GAP;
    lv_obj_t *box = ui_box(body, GRID_X, GAP, left_w, h);
    ui_fill(box, TK(panel));
    ui_border(box, TK(rule), UI_RULE, LV_BORDER_SIDE_FULL);
    lv_obj_t *l = ui_label(box, &hh_font_20, 5, 1, 0, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(l, "GPS SPEED");
    ui_ink(l, TK(fg));
    l = ui_label(box, &hh_font_16, 5, 4, left_w - 14, LV_TEXT_ALIGN_RIGHT);
    lv_label_set_text(l, "km/h");
    ui_ink(l, TK(fg2));
    gps.speed = ui_label(box, &hh_num_84, 5, 34, left_w - 14, LV_TEXT_ALIGN_RIGHT);
    lv_label_set_long_mode(gps.speed, LV_LABEL_LONG_CLIP);
    ui_ink(gps.speed, TK(fg));
    gps.heading = ui_label(box, &hh_font_28, 5, 112, left_w - 14, LV_TEXT_ALIGN_RIGHT);
    ui_ink(gps.heading, TK(fg));
    l = ui_label(box, &hh_font_16, 5, 120, 0, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(l, "HEADING");
    ui_ink(l, TK(fg2));
    gps.pos = ui_label(box, &hh_font_20, 5, 154, left_w - 14, LV_TEXT_ALIGN_LEFT);
    lv_label_set_long_mode(gps.pos, LV_LABEL_LONG_WRAP);
    lv_obj_set_height(gps.pos, 48);
    ui_ink(gps.pos, TK(fg));

    int x = GRID_X + left_w + 8, w = UI_W - x - GRID_X, rh = h / 5;
    gps.fix   = row_create(body, x, GAP + 0 * rh, w, rh, "FIX", &hh_font_28, true);
    gps.hdop  = row_create(body, x, GAP + 1 * rh, w, rh, "HDOP", &hh_font_28, false);
    gps.time  = row_create(body, x, GAP + 2 * rh, w, rh, "TRIP", &hh_font_28, false);
    gps.dist  = row_create(body, x, GAP + 3 * rh, w, rh, "DIST", &hh_font_28, false);
    gps.drive = row_create(body, x, GAP + 4 * rh, w, rh, "DRIVE", &hh_font_28, true);
}

static void gps_update(const hh_snapshot_t *s, const ui_derived_t *d)
{
    char text[48], num[16];
    bool fix = s->gps.fix != 0;

    if (fix) ui_fmt_float(text, sizeof(text), s->gps.speed_kmh, 0);
    else snprintf(text, sizeof(text), "---");
    ui_set_text(gps.speed, text);

    if (fix) snprintf(text, sizeof(text), "%03d\xC2\xB0 %s", ((int)lroundf(s->gps.heading) % 360 + 360) % 360, cardinal(s->gps.heading));
    else snprintf(text, sizeof(text), "---");
    ui_set_text(gps.heading, text);

    if (fix) snprintf(text, sizeof(text), "%.5f\n%.5f", s->gps.lat, s->gps.lon);
    else snprintf(text, sizeof(text), "NO POSITION");
    ui_set_text(gps.pos, text);

    snprintf(text, sizeof(text), "%s  %u SAT", s->gps.fix >= 3 ? "3D" : s->gps.fix == 2 ? "2D" : "NO", (unsigned)s->gps.sats);
    ui_set_text(gps.fix.value, text);
    ui_shape_set(gps.fix.shape, fix ? UI_SHAPE_DOT : UI_SHAPE_SQUARE, fix ? TK(ok) : TK(fg), TK(fg));

    if (fix && s->gps.hdop < 50) ui_fmt_float(text, sizeof(text), s->gps.hdop, 1);
    else snprintf(text, sizeof(text), "---");
    ui_set_text(gps.hdop.value, text);

    snprintf(text, sizeof(text), "%u:%02u:%02u", (unsigned)(d->trip_s / 3600), (unsigned)(d->trip_s / 60 % 60), (unsigned)(d->trip_s % 60));
    ui_set_text(gps.time.value, text);
    ui_fmt_float(num, sizeof(num), d->trip_km, d->trip_km < 100 ? 2 : 1);
    snprintf(text, sizeof(text), "%s km", num);
    ui_set_text(gps.dist.value, text);

    if (!s->log.sd_ok) snprintf(text, sizeof(text), "NO CARD");
    else if (s->log.recording) snprintf(text, sizeof(text), "REC %u", (unsigned)s->log.drive_no);
    else snprintf(text, sizeof(text), "IDLE %u", (unsigned)s->log.drive_no);
    ui_set_text(gps.drive.value, text);
    if (!s->log.sd_ok) ui_shape_set(gps.drive.shape, UI_SHAPE_CROSS, TK(alarm), TK(alarm));
    else if (s->log.recording) ui_shape_set(gps.drive.shape, UI_SHAPE_DOT, TK(alarm), TK(alarm));
    else ui_shape_set(gps.drive.shape, UI_SHAPE_SQUARE, TK(fg), TK(fg));
}

/* ---- Link / status --------------------------------------------------------- */

static struct { row_t link, backend, ecu, rate, reads, errors, sd, log, pc, pc_lost, stall; lv_obj_t *reason; } lnk;

static void link_create(lv_obj_t *body)
{
    int w = (UI_W - 2 * GRID_X - 12) / 2, x2 = GRID_X + w + 12, rh = (UI_BODY_H - 2 * GAP) / 7;
    const lv_font_t *f = &hh_font_20;
    lnk.link    = row_create(body, GRID_X, GAP + 0 * rh, w, rh, "LINK", f, true);
    lnk.backend = row_create(body, GRID_X, GAP + 1 * rh, w, rh, "BACKEND", f, false);
    lnk.ecu     = row_create(body, GRID_X, GAP + 2 * rh, w, rh, "ECU", f, false);
    lnk.rate    = row_create(body, GRID_X, GAP + 3 * rh, w, rh, "RATE", f, false);
    lnk.reads   = row_create(body, x2, GAP + 0 * rh, w, rh, "GPS OK/BAD", f, false);
    lnk.errors  = row_create(body, x2, GAP + 1 * rh, w, rh, "ERRORS", f, false);
    lnk.sd      = row_create(body, x2, GAP + 2 * rh, w, rh, "SD", f, true);
    lnk.log     = row_create(body, x2, GAP + 3 * rh, w, rh, "LOG", f, false);
    /* the laptop on the USB-C port (SERIAL.md) */
    lnk.pc      = row_create(body, GRID_X, GAP + 4 * rh, w, rh, "PC", f, true);
    lnk.pc_lost = row_create(body, x2, GAP + 4 * rh, w, rh, "PC SENT/LOST", f, false);
    lnk.stall   = row_create(body, GRID_X, GAP + 5 * rh, UI_W - 2 * GRID_X, rh, "STALL CATCHER", f, true);
    lv_obj_t *k = ui_label(body, &hh_font_16, GRID_X + 2, GAP + 6 * rh + (rh - 20) / 2, 0, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(k, "LAST");
    ui_ink(k, TK(fg2));
    lnk.reason = ui_label(body, f, GRID_X + 50, GAP + 6 * rh + (rh - 24) / 2, UI_W - 2 * GRID_X - 52, LV_TEXT_ALIGN_RIGHT);
    ui_ink(lnk.reason, TK(fg));
}

static void link_update(const hh_snapshot_t *s, const ui_derived_t *d)
{
    char text[48], num[16];
    (void)d;

    ui_set_text(lnk.link.value, ui_link_word(s->link.state));
    ui_link_shape(lnk.link.shape, s->link.state);

    snprintf(text, sizeof(text), "%u", (unsigned)s->link.backend);
#ifdef HH_BACKEND_NAMES
    if (s->link.backend < sizeof(BACKENDS) / sizeof(BACKENDS[0])) snprintf(text, sizeof(text), "%s", BACKENDS[s->link.backend]);
#endif
    ui_set_text(lnk.backend.value, text);

    char id[sizeof(s->link.ecu_id) + 1];
    memcpy(id, s->link.ecu_id, sizeof(s->link.ecu_id));
    id[sizeof(s->link.ecu_id)] = 0;
    ui_set_text(lnk.ecu.value, id[0] ? id : "---");

    ui_fmt_float(num, sizeof(num), s->link.hz_fast, 1);
    snprintf(text, sizeof(text), "%s Hz", num);
    ui_set_text(lnk.rate.value, text);

    snprintf(text, sizeof(text), "%u / %u", (unsigned)s->nmea.good, (unsigned)s->nmea.bad);
    ui_set_text(lnk.reads.value, text);
    uint32_t total = s->link.reads + s->link.errors;
    ui_fmt_float(num, sizeof(num), total ? 100.0f * (float)s->link.errors / (float)total : 0, 1);
    snprintf(text, sizeof(text), "%u  %s%%", (unsigned)s->link.errors, num);
    ui_set_text(lnk.errors.value, text);

    if (!s->log.sd_ok) snprintf(text, sizeof(text), "NO CARD");
    else if (s->log.free_mb >= 1024) { ui_fmt_float(num, sizeof(num), s->log.free_mb / 1024, 1); snprintf(text, sizeof(text), "%s GB free", num); }
    else { ui_fmt_float(num, sizeof(num), s->log.free_mb, 0); snprintf(text, sizeof(text), "%s MB free", num); }
    ui_set_text(lnk.sd.value, text);
    ui_shape_set(lnk.sd.shape, s->log.sd_ok ? UI_SHAPE_DOT : UI_SHAPE_CROSS, s->log.sd_ok ? TK(ok) : TK(alarm), TK(fg));

    ui_fmt_float(num, sizeof(num), (float)s->log.bytes / 1048576.0f, 1);
    if (s->log.recording) snprintf(text, sizeof(text), "#%u  %s MB", (unsigned)s->log.drive_no, num);
    else snprintf(text, sizeof(text), "NOT RECORDING");
    ui_set_text(lnk.log.value, text);

    /* PC: two-way knows whether the laptop is there (it is heard); tx-only can only say that it is sending */
    if (s->pc.mode == 0) ui_shape_set(lnk.pc.shape, UI_SHAPE_SQUARE, TK(fg), TK(fg));
    else if (s->pc.mode == 1 || s->pc.host) ui_shape_set(lnk.pc.shape, UI_SHAPE_DOT, TK(ok), TK(fg));
    else ui_shape_set(lnk.pc.shape, UI_SHAPE_RING, TK(fg), TK(fg));
    ui_set_text(lnk.pc.value, s->pc.mode == 0 ? "OFF" : s->pc.mode == 1 ? "TX" : s->pc.host ? "PC" : "NO PC");
    if (s->pc.frames >= 100000) snprintf(text, sizeof(text), "%uk / %u", (unsigned)(s->pc.frames / 1000), (unsigned)s->pc.dropped);
    else snprintf(text, sizeof(text), "%u / %u", (unsigned)s->pc.frames, (unsigned)s->pc.dropped);
    ui_set_text(lnk.pc_lost.value, text);

    snprintf(text, sizeof(text), "%s   COUNT %u", s->stall.triggered ? "CAUGHT" : s->stall.armed ? "ARMED" : "OFF", (unsigned)s->stall.count);
    ui_set_text(lnk.stall.value, text);
    if (s->stall.triggered) ui_shape_set(lnk.stall.shape, UI_SHAPE_OCTAGON, TK(alarm), TK(alarm));
    else ui_shape_set(lnk.stall.shape, s->stall.armed ? UI_SHAPE_RING : UI_SHAPE_SQUARE, TK(fg), TK(fg));

    char reason[sizeof(s->stall.last_reason) + 1];
    memcpy(reason, s->stall.last_reason, sizeof(s->stall.last_reason));
    reason[sizeof(s->stall.last_reason)] = 0;
    ui_set_text(lnk.reason, reason[0] ? reason : "no stall yet");
}

/* ---- dispatch -------------------------------------------------------------- */

void ui_screen_title(ui_screen_id_t id, int page, char *buf, size_t n)
{
    switch (id) {
    case UI_SCR_GAUGES: snprintf(buf, n, "GAUGES"); break;
    case UI_SCR_SENSORS:
        if (ui_sensor_pages() > 1) snprintf(buf, n, "SENSORS %d/%d", page + 1, ui_sensor_pages());
        else snprintf(buf, n, "SENSORS");
        break;
    case UI_SCR_GPS: snprintf(buf, n, "GPS TRIP"); break;
    case UI_SCR_MAP: snprintf(buf, n, "MAP"); break;
    default: snprintf(buf, n, "LINK"); break;
    }
}

void ui_screen_create(ui_screen_id_t id, int page, lv_obj_t *body)
{
    switch (id) {
    case UI_SCR_GAUGES: gauges_create(body); break;
    case UI_SCR_SENSORS: sensors_create(body, page); break;
    case UI_SCR_GPS: gps_create(body); break;
    case UI_SCR_MAP: ui_map_create(body); break;
    default: link_create(body); break;
    }
}

void ui_screen_update(ui_screen_id_t id, const hh_snapshot_t *s, const ui_derived_t *d)
{
    switch (id) {
    case UI_SCR_GAUGES: gauges_update(s, d); break;
    case UI_SCR_SENSORS: sensors_update(s, d); break;
    case UI_SCR_GPS: gps_update(s, d); break;
    case UI_SCR_MAP: ui_map_update(s, d->now_ms); break;
    default: link_update(s, d); break;
    }
}
