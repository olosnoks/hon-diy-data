/* PC simulator for the handheld UI. Headless: LVGL renders RGB565 into a
 * 480 x 272 buffer exactly as on the board, and this writes PNGs from it.
 *
 *   hhsim shots DIR    every screen and state, both themes
 *   hhsim test         button logic, contrast, tabular digits, layout
 *   hhsim budget       pixels redrawn per frame on each screen
 *
 * Time is simulated (10 ms steps), so runs are repeatable.
 */
#include <math.h>
#include <png.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <dirent.h>
#include <unistd.h>

#include "lvgl.h"
#include "hh_data.h"
#include "hh_stub.h"
#include "ui.h"
#include "ui_data.h"
#include "ui_input.h"
#include "ui_map.h"
#include "ui_mapsvc.h"
#include "ui_perf.h"
#include "ui_screens.h"
#include "ui_theme.h"

static uint16_t fb[UI_W * UI_H];
static uint32_t clock_ms = 1000;
static bool button, button_back;
static int theme_changes;
#define STALL_SHOT_MS 3000      /* past the 2.4 s of flashing: the steady capture screen */

static uint32_t tick_cb(void) { return clock_ms; }
static int64_t stub_clock(void) { return (int64_t)clock_ms * 1000; }

void ui_port_theme_changed(bool darker) { (void)darker; theme_changes++; }

/* ---- the map: the worker runs in place, the "NVS" is two variables ------------- */
static const char *map_dir = "testmap/MAP";
static bool have_last_pos;
static double last_lat, last_lon;
static int pos_saves;

void ui_port_map_kick(void)
{
    static int depth;
    if (depth) return;
    depth++;
    for (int i = 0; i < 200 && mapsvc_work(); i++) {}
    depth--;
}
void ui_port_map_lock(void) {}
void ui_port_map_unlock(void) {}
void ui_port_map_wait(int ms) { (void)ms; }       /* the worker runs in place: nothing to wait for */
void *ui_port_map_alloc(size_t n) { return malloc(n); }
void ui_port_map_free(void *p) { free(p); }
const char *ui_port_map_dir(void) { return map_dir; }
bool ui_port_last_pos(double *lat, double *lon)
{
    if (have_last_pos) { *lat = last_lat; *lon = last_lon; }
    return have_last_pos;
}
void ui_port_save_pos(double lat, double lon) { last_lat = lat; last_lon = lon; have_last_pos = true; pos_saves++; }
void ui_port_cpu(int *c0, int *c1) { *c0 = *c1 = -1; }
int64_t ui_port_micros(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

static void flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px)
{
    (void)px;
    ui_perf_flushed(area);
    lv_display_flush_ready(disp);
}

static void run(uint32_t ms)
{
    for (uint32_t t = 0; t < ms; t += 10) {
        clock_ms += 10;
        ui_tick(clock_ms, button, button_back);
        lv_timer_handler();
    }
}

static void press(uint32_t ms)
{
    button = true;  run(ms);
    button = false; run(100);
}

static void start(bool darker, hh_stub_scene_t scene)
{
    hh_stub_set_scene(scene);
    hh_stub_set_route(HH_ROUTE_NONE, 0);
    ui_create(darker);
    run(300);
}

static int map_stop(void) { return ui_sensor_pages() + 2; }

/* drive a route for a while, then open the Map screen */
static void start_map(bool darker, hh_stub_scene_t scene, hh_stub_route_t route, double start_m, uint32_t drive_ms)
{
    hh_stub_set_scene(HH_SCENE_DRIVE);
    hh_stub_set_route(route, start_m);
    ui_create(darker);
    run(drive_ms);
    hh_stub_set_scene(scene);
    ui_goto(map_stop());
    run(1500);
}

/* ---- PNG -------------------------------------------------------------------- */

static int save_png(const char *dir, const char *name)
{
    char path[512];
    snprintf(path, sizeof(path), "%s/%s.png", dir, name);
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); return 1; }
    png_structp png = png_create_write_struct(PNG_LIBPNG_VER_STRING, NULL, NULL, NULL);
    png_infop info = png_create_info_struct(png);
    if (setjmp(png_jmpbuf(png))) { fclose(f); return 1; }
    png_init_io(png, f);
    png_set_IHDR(png, info, UI_W, UI_H, 8, PNG_COLOR_TYPE_RGB, PNG_INTERLACE_NONE,
                 PNG_COMPRESSION_TYPE_DEFAULT, PNG_FILTER_TYPE_DEFAULT);
    png_write_info(png, info);
    uint8_t row[UI_W * 3];
    for (int y = 0; y < UI_H; y++) {
        for (int x = 0; x < UI_W; x++) {
            uint16_t p = fb[y * UI_W + x];           /* 565 -> 888 by bit replication */
            uint8_t r = (p >> 11) & 31, g = (p >> 5) & 63, b = p & 31;
            row[x * 3] = (uint8_t)(r << 3 | r >> 2);
            row[x * 3 + 1] = (uint8_t)(g << 2 | g >> 4);
            row[x * 3 + 2] = (uint8_t)(b << 3 | b >> 2);
        }
        png_write_row(png, row);
    }
    png_write_end(png, NULL);
    png_destroy_write_struct(&png, &info);
    fclose(f);
    printf("  %s\n", path);
    return 0;
}

/* ---- layout check: nothing cut off, nothing outside its parent --------------- */

static int layout_faults;

static void check_tree(lv_obj_t *obj, const char *where)
{
    if (lv_obj_has_flag(obj, LV_OBJ_FLAG_HIDDEN)) return;
    lv_obj_t *parent = lv_obj_get_parent(obj);
    lv_area_t a, pa;
    lv_obj_get_coords(obj, &a);
    if (parent) {
        lv_obj_get_coords(parent, &pa);
        if (a.x1 < pa.x1 || a.y1 < pa.y1 || a.x2 > pa.x2 || a.y2 > pa.y2) {
            printf("  FAIL %s: object %d,%d..%d,%d leaves its parent %d,%d..%d,%d\n", where,
                   (int)a.x1, (int)a.y1, (int)a.x2, (int)a.y2, (int)pa.x1, (int)pa.y1, (int)pa.x2, (int)pa.y2);
            layout_faults++;
        }
    }
    if (lv_obj_check_type(obj, &lv_label_class)) {
        const lv_font_t *font = lv_obj_get_style_text_font(obj, 0);
        const char *text = lv_label_get_text(obj);
        lv_point_t size;
        int w = lv_obj_get_width(obj), h = lv_obj_get_height(obj);
        bool wrap = lv_label_get_long_mode(obj) == LV_LABEL_LONG_WRAP;
        lv_text_get_size(&size, text, font, 0, 0, wrap ? w : LV_COORD_MAX, LV_TEXT_FLAG_NONE);
        if (size.x > w || size.y > h || strstr(text, "...")) {
            printf("  FAIL %s: \"%s\" needs %dx%d, has %dx%d\n", where, text, (int)size.x, (int)size.y, w, h);
            layout_faults++;
        }
        if (lv_font_get_line_height(font) < 20 && font != &hh_num_36) {
            printf("  FAIL %s: \"%s\" is set below 16 px\n", where, text);
            layout_faults++;
        }
    }
    uint32_t n = lv_obj_get_child_count(obj);
    for (uint32_t i = 0; i < n; i++) check_tree(lv_obj_get_child(obj, (int32_t)i), where);
}

static void check_layout(const char *where)
{
    lv_obj_update_layout(lv_screen_active());
    check_tree(lv_screen_active(), where);
    check_tree(lv_layer_top(), where);
}

/* ---- screenshots -------------------------------------------------------------- */

static int shots(const char *dir)
{
    int rc = 0;
    char name[64], title[24];
    for (int darker = 0; darker < 2; darker++) {
        const char *th = darker ? "darker" : "dracula";
        start(darker, HH_SCENE_DRIVE);
        run(4000);
        for (int stop = 0; stop < ui_stops(); stop++) {
            ui_goto(stop);
            run(700);
            int pages = ui_sensor_pages();
            if (stop == 0) snprintf(title, sizeof(title), "gauges");
            else if (stop <= pages) snprintf(title, sizeof(title), "sensors-%d", stop);
            else snprintf(title, sizeof(title), stop == pages + 1 ? "gps" : stop == pages + 2 ? "map" : "link");
            snprintf(name, sizeof(name), "%s-%d-%s", th, stop + 1, title);
            rc |= save_png(dir, name);
            check_layout(name);
        }

        static const struct { hh_stub_scene_t scene; int stop; const char *name; } states[] = {
            {HH_SCENE_WARN, 1, "warn-sensors"},  {HH_SCENE_WARN, 0, "warn-gauges"},
            {HH_SCENE_ALARM, 0, "alarm-gauges"}, {HH_SCENE_ALARM, 1, "alarm-sensors"},
            {HH_SCENE_LINK_LOST, 0, "linklost-gauges"}, {HH_SCENE_LINK_LOST, 1, "linklost-sensors"},
            {HH_SCENE_CONNECTING, 3, "connecting-gps"}, {HH_SCENE_CONNECTING, 5, "connecting-link"},
        };
        for (size_t i = 0; i < sizeof(states) / sizeof(states[0]); i++) {
            start(darker, HH_SCENE_DRIVE);
            ui_goto(states[i].stop);
            run(500);
            hh_stub_set_scene(states[i].scene);
            run(1100 - clock_ms % 1000);            /* land in the "octagon shown" blink phase */
            snprintf(name, sizeof(name), "%s-%s", th, states[i].name);
            rc |= save_png(dir, name);
            check_layout(name);
        }

        /* stall: the full-screen capture, then what stays after it is dismissed */
        start(darker, HH_SCENE_DRIVE);
        run(500);
        hh_stub_set_scene(HH_SCENE_STALL);
        run(STALL_SHOT_MS);
        snprintf(name, sizeof(name), "%s-stall-captured", th);
        rc |= save_png(dir, name);
        check_layout(name);
        press(100);
        run(1700);
        run(1100 - clock_ms % 1000);
        snprintf(name, sizeof(name), "%s-stall-after", th);
        rc |= save_png(dir, name);
        check_layout(name);

        /* the Map screen: downtown at walking pace with a mark and a stall on the way, I-55 at
         * 70 mph, no fix (last known position), no map on the card */
        start_map(darker, HH_SCENE_DRIVE, HH_ROUTE_STATE_ST, 1500, 30000);
        press(1500);                                 /* a marker here */
        run(25000);
        hh_stub_set_scene(HH_SCENE_STALL);           /* a stall here */
        run(400);
        press(100);                                  /* dismiss the capture screen */
        hh_stub_set_scene(HH_SCENE_DRIVE);
        run(20000);
        snprintf(name, sizeof(name), "%s-map-downtown", th);
        rc |= save_png(dir, name);
        check_layout(name);

        start_map(darker, HH_SCENE_DRIVE, HH_ROUTE_I55, 2500, 75000);
        snprintf(name, sizeof(name), "%s-map-i55", th);
        rc |= save_png(dir, name);
        check_layout(name);

        start_map(darker, HH_SCENE_CONNECTING, HH_ROUTE_STATE_ST, 1500, 20000);
        run(1000);
        snprintf(name, sizeof(name), "%s-map-nofix", th);
        rc |= save_png(dir, name);
        check_layout(name);

        const char *keep = map_dir;
        map_dir = "testmap/none";
        mapsvc_reopen();
        start_map(darker, HH_SCENE_DRIVE, HH_ROUTE_STATE_ST, 1500, 2000);
        snprintf(name, sizeof(name), "%s-map-nomap", th);
        rc |= save_png(dir, name);
        check_layout(name);
        map_dir = keep;
        mapsvc_reopen();

        /* the hint strip while the button is held, and after a mark */
        start(darker, HH_SCENE_DRIVE);
        button = true;
        run(600);
        snprintf(name, sizeof(name), "%s-hold-0.6s", th);
        rc |= save_png(dir, name);
        check_layout(name);
        run(1200);
        snprintf(name, sizeof(name), "%s-hold-1.8s", th);
        rc |= save_png(dir, name);
        check_layout(name);
        button = false;
        run(300);
        snprintf(name, sizeof(name), "%s-marked", th);
        rc |= save_png(dir, name);
        check_layout(name);
    }
    if (layout_faults) printf("%d layout fault(s)\n", layout_faults);
    return rc || layout_faults;
}

/* ---- tests -------------------------------------------------------------------- */

static int failures;
#define CHECK(cond, ...) do { if (!(cond)) { failures++; printf("  FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static double lum(uint32_t c, bool q565)
{
    int ch[3] = {(int)(c >> 16) & 255, (int)(c >> 8) & 255, (int)c & 255};
    if (q565) {                                   /* what the 16-bit panel is actually sent */
        int r = ch[0] >> 3, g = ch[1] >> 2, b = ch[2] >> 3;
        ch[0] = r << 3 | r >> 2; ch[1] = g << 2 | g >> 4; ch[2] = b << 3 | b >> 2;
    }
    double l[3];
    for (int i = 0; i < 3; i++) {
        double v = ch[i] / 255.0;
        l[i] = v <= 0.03928 ? v / 12.92 : pow((v + 0.055) / 1.055, 2.4);
    }
    return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2];
}

static double contrast(uint32_t a, uint32_t b, bool q565)
{
    double la = lum(a, q565), lb = lum(b, q565);
    return la > lb ? (la + 0.05) / (lb + 0.05) : (lb + 0.05) / (la + 0.05);
}

static void test_contrast(void)
{
    printf("contrast (WCAG 2.x), sRGB / as RGB565:\n");
    const ui_tokens_t *themes[] = {&UI_DRACULA, &UI_DARKER};
    for (int t = 0; t < 2; t++) {
        ui_pair_t pairs[24];
        int n = ui_theme_pairs(themes[t], pairs, 24);
        for (int i = 0; i < n; i++) {
            double a = contrast(pairs[i].fg, pairs[i].bg, false), b = contrast(pairs[i].fg, pairs[i].bg, true);
            printf("  %-8s %-24s %5.2f / %5.2f  (%.1f:1)\n", t ? "darker" : "dracula", pairs[i].name, a, b, (double)pairs[i].min);
            CHECK(a >= pairs[i].min && b >= pairs[i].min, "%s %s is below %.1f:1", t ? "darker" : "dracula", pairs[i].name,
                  (double)pairs[i].min);
        }
        n = ui_map_pairs(themes[t], pairs, 24);
        for (int i = 0; i < n; i++) {
            double a = contrast(pairs[i].fg, pairs[i].bg, false), b = contrast(pairs[i].fg, pairs[i].bg, true);
            printf("  %-8s %-24s %5.2f / %5.2f  (graphics, %.1f:1)\n", t ? "darker" : "dracula", pairs[i].name, a, b,
                   (double)pairs[i].min);
            CHECK(a >= pairs[i].min && b >= pairs[i].min, "%s %s is below %.1f:1", t ? "darker" : "dracula", pairs[i].name,
                  (double)pairs[i].min);
        }
    }
}

static void test_button(void)
{
    ui_btn_t b = {0};
    uint32_t t = 0;
    int seen[4] = {0};
#define FEED(level, ms) for (uint32_t i = 0; i < (ms); i += 10, t += 10) seen[ui_btn_step(&b, level, t)]++
    FEED(false, 100); FEED(true, 200); FEED(false, 100);
    CHECK(seen[UI_BTN_SHORT] == 1 && !seen[UI_BTN_LONG] && !seen[UI_BTN_VLONG], "short press");
    memset(seen, 0, sizeof(seen));
    FEED(true, 1500);
    CHECK(!seen[UI_BTN_LONG], "MARK must wait for the release");
    CHECK(ui_btn_held_ms(&b, t) >= 1400, "held time");
    FEED(false, 100);
    CHECK(seen[UI_BTN_LONG] == 1 && !seen[UI_BTN_SHORT] && !seen[UI_BTN_VLONG], "long press");
    memset(seen, 0, sizeof(seen));
    FEED(true, 3500); FEED(false, 100);
    CHECK(seen[UI_BTN_VLONG] == 1 && !seen[UI_BTN_LONG] && !seen[UI_BTN_SHORT], "very long press fires once and nothing else");
    memset(seen, 0, sizeof(seen));
    FEED(true, 10); FEED(false, 10); FEED(true, 10); FEED(false, 100);
    CHECK(!seen[UI_BTN_SHORT], "a 10 ms glitch is not a press");
    memset(seen, 0, sizeof(seen));
    t = 0xFFFFFF00u;                              /* across the 49-day wrap of the ms clock */
    b = (ui_btn_t){0};
    FEED(false, 50); FEED(true, 1200); FEED(false, 100);
    CHECK(seen[UI_BTN_LONG] == 1, "long press across the clock wrap");
#undef FEED
}

static void test_fonts(void)
{
    const lv_font_t *fonts[] = {&hh_font_16, &hh_font_20, &hh_font_28, &hh_num_36, &hh_num_52, &hh_num_84};
    for (size_t f = 0; f < sizeof(fonts) / sizeof(fonts[0]); f++) {
        int w0 = lv_font_get_glyph_width(fonts[f], '0', 0);
        CHECK(w0 > 0, "font %d has no digits", (int)f);
        for (char c = '1'; c <= '9'; c++)
            CHECK(lv_font_get_glyph_width(fonts[f], (uint32_t)c, 0) == w0, "font %d: digit %c is not tabular", (int)f, c);
        CHECK(lv_font_get_glyph_width(fonts[f], '1', '1') == w0, "font %d kerns digits", (int)f);
    }
}

static void test_format(void)
{
    char b[24];
    hh_channel_info_t c = {.lo = 0, .hi = 16, .decimals = 1, .alarm_lo = 11.5f, .alarm_hi = NAN, .warn_lo = NAN, .warn_hi = NAN};
    ui_fmt_value(b, sizeof(b), &c, 14.06f);  CHECK(!strcmp(b, "14.1"), "got %s", b);
    ui_fmt_value(b, sizeof(b), &c, -0.04f);  CHECK(!strcmp(b, "0.0"), "got %s", b);
    ui_fmt_value(b, sizeof(b), &c, NAN);     CHECK(!strcmp(b, "---"), "got %s", b);
    ui_fmt_limit(b, sizeof(b), &c, 11.0f, 2); CHECK(!strcmp(b, "< 11.5"), "got %s", b);
    ui_fmt_limit(b, sizeof(b), &c, 11.0f, 1); CHECK(!strcmp(b, ""), "got %s", b);
    hh_channel_info_t f = {.lo = 0, .hi = 1, .decimals = 0};
    ui_fmt_value(b, sizeof(b), &f, 1);       CHECK(!strcmp(b, "ON"), "got %s", b);
    CHECK(ui_find_channel("nope|RPM") == 0, "name lookup is case-insensitive and takes alternatives");
    CHECK(ui_find_channel("rp") < 0 && ui_find_channel("rpmx") < 0, "name lookup matches whole names");
}

static bool tree_has(lv_obj_t *obj, const char *text)
{
    if (lv_obj_has_flag(obj, LV_OBJ_FLAG_HIDDEN)) return false;
    if (lv_obj_check_type(obj, &lv_label_class) && strstr(lv_label_get_text(obj), text)) return true;
    uint32_t n = lv_obj_get_child_count(obj);
    for (uint32_t i = 0; i < n; i++) if (tree_has(lv_obj_get_child(obj, (int32_t)i), text)) return true;
    return false;
}

static bool screen_has(const char *text) { return tree_has(lv_screen_active(), text) || tree_has(lv_layer_top(), text); }

/* HANDHELD.CFG units: US by default, metric on request; the engine's values are
 * the same either way, only the drawing changes */
static void test_units(void)
{
    uint16_t n;
    hh_stub_set_units(HH_UNITS_US);
    start(false, HH_SCENE_ALARM);                       /* coolant over its alarm limit */
    const hh_channel_info_t *info = ui_channels(&n);
    int ect = ui_find_channel("ect"), map = ui_find_channel("map"), vss = ui_find_channel("vss");
    CHECK(ect >= 0 && !strcmp(info[ect].units, "\xC2\xB0""F") && fabsf(info[ect].alarm_hi - 226.4f) < 0.01f,
          "coolant in °F, alarm at 226.4 °F");
    CHECK(map >= 0 && !strcmp(info[map].units, "inHg") && info[map].decimals == 1, "MAP in inHg, 1 decimal");
    CHECK(vss >= 0 && !strcmp(info[vss].units, "mph"), "speed in mph");
    CHECK(fabsf(ui_value(ect, 100.0f) - 212.0f) < 1e-3f && isnan(ui_value(ect, NAN)), "ui_value converts, NaN stays");
    CHECK(screen_has("\xC2\xB0""F") && !screen_has("\xC2\xB0""C"), "the screen shows °F and no °C");
    CHECK(screen_has("limit > 226.4") || screen_has("limit > 226"), "the banner's limit is in °F");
    ui_goto(ui_sensor_pages() + 1);                     /* GPS */
    run(1200);
    CHECK(screen_has("mph") && screen_has(" mi") && !screen_has("km"), "GPS speed in mph, trip in miles");

    hh_stub_set_units(HH_UNITS_METRIC);
    start(false, HH_SCENE_ALARM);
    info = ui_channels(&n);
    CHECK(!strcmp(info[ect].units, "\xC2\xB0""C") && info[ect].alarm_hi == 108.0f && ui_value(ect, 100.0f) == 100.0f,
          "metric: the map's °C");
    CHECK(screen_has("\xC2\xB0""C") && !screen_has("\xC2\xB0""F"), "metric screen shows °C");
    ui_goto(ui_sensor_pages() + 1);
    run(1200);
    CHECK(screen_has("km/h") && screen_has(" km") && !screen_has("mph"), "metric GPS in km/h and km");
    hh_stub_set_units(HH_UNITS_US);
}

static void test_flow(void)
{
    start(false, HH_SCENE_DRIVE);
    uint32_t marks = hh_stub_marks(), acks = hh_stub_acks();
    int changes = theme_changes;

    press(1500);
    CHECK(hh_stub_marks() == marks + 1, "1.5 s press marks once");
    press(3300);
    CHECK(hh_stub_marks() == marks + 1, "3 s press does not mark");
    CHECK(ui_darker() && theme_changes == changes + 1, "3 s press toggles the theme when no alarm is blinking");
    press(3300);
    CHECK(!ui_darker(), "and back");

    hh_stub_set_scene(HH_SCENE_ALARM);
    run(300);
    press(3300);
    CHECK(hh_stub_acks() == acks + 1 && !ui_darker(), "3 s press acknowledges a blinking alarm instead");
    press(3300);
    CHECK(hh_stub_acks() == acks + 1 && ui_darker(), "once acknowledged, 3 s is the theme again");

    /* every stop, every scene, both themes, both unit sets (US: the longer
     * °F, inHg, gal/h, mph): nothing cut off */
    static const hh_stub_scene_t scenes[] = {HH_SCENE_DRIVE, HH_SCENE_WARN, HH_SCENE_ALARM, HH_SCENE_STALL,
                                             HH_SCENE_LINK_LOST, HH_SCENE_CONNECTING};
    for (int units = HH_UNITS_US; units >= HH_UNITS_METRIC; units--) {
        hh_stub_set_units(units);
        for (int darker = 0; darker < 2; darker++)
            for (size_t s = 0; s < sizeof(scenes) / sizeof(scenes[0]); s++) {
                start(darker, HH_SCENE_DRIVE);
                hh_stub_set_scene(scenes[s]);
                run(300);
                if (scenes[s] == HH_SCENE_STALL) check_layout("stall screen");
                press(100);
                for (int stop = 0; stop < ui_stops(); stop++) {
                    char where[64];
                    snprintf(where, sizeof(where), "%s %s scene %d stop %d", units ? "us" : "metric", darker ? "darker" : "dracula",
                             (int)scenes[s], stop);
                    ui_goto(stop);
                    run(1200);
                    check_layout(where);
                }
            }
    }
    hh_stub_set_units(HH_UNITS_US);
    CHECK(layout_faults == 0, "%d layout fault(s)", layout_faults);

    /* the press cycle visits every stop and comes back */
    start(false, HH_SCENE_DRIVE);
    lv_obj_t *title = lv_obj_get_child(lv_obj_get_child(lv_screen_active(), 0), 0);
    char first[24];
    snprintf(first, sizeof(first), "%s", lv_label_get_text(title));
    for (int i = 0; i < ui_stops(); i++) {
        press(100);
        title = lv_obj_get_child(lv_obj_get_child(lv_screen_active(), 0), 0);
        CHECK((i == ui_stops() - 1) == !strcmp(first, lv_label_get_text(title)), "press %d landed on %s", i + 1, lv_label_get_text(title));
    }
    button_back = true; run(100); button_back = false; run(100);
    title = lv_obj_get_child(lv_obj_get_child(lv_screen_active(), 0), 0);
    CHECK(!strcmp(lv_label_get_text(title), "LINK"), "the second button steps back, got %s", lv_label_get_text(title));
}

/* ---- the Map screen ------------------------------------------------------------- */

static int body_pixels(uint32_t hex)
{
    uint16_t c = lv_color_to_u16(lv_color_hex(hex));
    int n = 0;
    for (int y = UI_BODY_Y; y < UI_BODY_Y + UI_BODY_H; y++)
        for (int x = 0; x < UI_W; x++) n += fb[y * UI_W + x] == c;
    return n;
}

#define RETRY_MS_SIM 5000       /* ui_map.c's RETRY_MS */

/* how many of this process's open files are the map's MAP.IDX / MAP.DAT */
static int open_map_files(void)
{
    int n = 0;
    DIR *d = opendir("/proc/self/fd");
    struct dirent *e;
    while (d && (e = readdir(d)) != NULL) {
        char link[300], target[512];
        snprintf(link, sizeof(link), "/proc/self/fd/%s", e->d_name);
        ssize_t len = readlink(link, target, sizeof(target) - 1);
        if (len <= 0) continue;
        target[len] = 0;
        n += strstr(target, "/testmap/MAP/MAP.IDX") != NULL || strstr(target, "/testmap/MAP/MAP.DAT") != NULL;
    }
    if (d) closedir(d);
    return n;
}

static bool has_label(const ui_map_info_t *mi, const char *text)
{
    for (int i = 0; i < mi->n_labels; i++) if (strstr(mi->labels[i], text)) return true;
    return false;
}

static bool find_text(lv_obj_t *obj, const char *text)
{
    if (lv_obj_has_flag(obj, LV_OBJ_FLAG_HIDDEN)) return false;
    if (lv_obj_check_type(obj, &lv_label_class) && strstr(lv_label_get_text(obj), text)) return true;
    for (uint32_t i = 0; i < lv_obj_get_child_count(obj); i++)
        if (find_text(lv_obj_get_child(obj, (int32_t)i), text)) return true;
    return false;
}

static const char *title_text(void)
{
    return lv_label_get_text(lv_obj_get_child(lv_obj_get_child(lv_screen_active(), 0), 0));
}

static void test_map(void)
{
    ui_map_info_t mi;
    printf("map screen:\n");
    pos_saves = 0;
    have_last_pos = false;

    /* downtown at 25 km/h: the closest level, real streets, labels, a trail */
    start_map(false, HH_SCENE_DRIVE, HH_ROUTE_STATE_ST, 1500, 20000);
    ui_map_info(&mi);
    CHECK(!strcmp(title_text(), "MAP"), "title %s", title_text());
    CHECK(mi.status == MAPSVC_OK, "a map is drawn (status %d; run 'make testmap' if testmap/MAP is missing)", mi.status);
    CHECK(mi.level == 0 && mi.manual < 0 && mi.mpp == 4.0f, "25 km/h: AUTO 1/4 (level %d)", mi.level + 1);
    CHECK(mi.n_trail >= 5, "a trail (%d points)", mi.n_trail);
    CHECK(!mi.banner && !mi.message, "no banner, no message with a fix and a map");
    CHECK(mi.n_labels >= 3 && has_label(&mi, "US 51"), "labels (%d): %s, %s, %s ...",
          mi.n_labels, mi.labels[0], mi.labels[1], mi.labels[2]);
    int ink = body_pixels(UI_DRACULA.fg), water = body_pixels(UI_DRACULA.water);
    CHECK(ink > 4000, "streets drawn in fg (%d px)", ink);
    printf("  downtown: %d labels, %d px of road ink, %d px of water, worker %u us\n", mi.n_labels, ink, water,
           (unsigned)mi.render_us);
    CHECK(pos_saves == 0, "no flash writes while driving a short way (%d)", pos_saves);

    /* the picture is redrawn only when the car has moved off its centre */
    uint32_t r0 = mi.renders;
    run(10000);
    ui_map_info(&mi);
    CHECK(mi.renders - r0 <= 3, "10 s at 25 km/h: %u new pictures (at most 3)", (unsigned)(mi.renders - r0));

    /* a mark and a stall drop pins */
    press(1500);
    run(300);
    ui_map_info(&mi);
    CHECK(mi.n_pins == 1, "MARK drops a pin (%d)", mi.n_pins);
    hh_stub_set_scene(HH_SCENE_STALL);
    run(300);
    press(100);
    hh_stub_set_scene(HH_SCENE_DRIVE);
    run(15000);                                      /* drive on: the car no longer covers it */
    ui_map_info(&mi);
    CHECK(mi.n_pins == 2, "a stall drops a pin (%d)", mi.n_pins);
    CHECK(body_pixels(UI_DRACULA.alarm) > 40, "the stall pin is drawn (an octagon in the alarm colour)");

    /* the second button: zoom steps, then back to AUTO; a hold steps back a screen */
    for (int i = 0; i < 5; i++) {
        button_back = true; run(100); button_back = false; run(1200);
        ui_map_info(&mi);
        if (i < 4) CHECK(mi.manual == i && mi.level == i && mi.mpp == mapsvc_mpp(i), "zoom press %d: FIXED %d/4 (%d, %.0f m/px)", i + 1, i + 1, mi.level + 1, (double)mi.mpp);
        else CHECK(mi.manual < 0 && mi.level == 0, "fifth press: AUTO again");
    }
    button_back = true; run(1300); button_back = false; run(200);
    CHECK(!strcmp(title_text(), "GPS TRIP"), "holding the second button steps back (%s)", title_text());

    /* the theme: the same picture in Darker colours */
    ui_goto(map_stop());
    run(1200);
    press(3300);
    run(1500);
    ui_map_info(&mi);
    CHECK(ui_darker() && mi.status == MAPSVC_OK && body_pixels(UI_DARKER.fg) > 4000 &&
          body_pixels(UI_DARKER.bg) > UI_W * UI_BODY_H / 2 && body_pixels(UI_DARKER.water) > 1000,
          "Darker: fg roads on the Darker ground, water");
    press(3300);

    /* I-55 at 70 mph: the widest level */
    start_map(false, HH_SCENE_DRIVE, HH_ROUTE_I55, 2500, 40000);
    ui_map_info(&mi);
    CHECK(mi.level == 3 && mi.mpp == 60.0f, "113 km/h: AUTO 4/4 (level %d)", mi.level + 1);
    CHECK(has_label(&mi, "I-55"), "the I-55 shield (%s, %s, %s ...)", mi.labels[0], mi.labels[1], mi.labels[2]);
    printf("  I-55: %d labels, worker %u us\n", mi.n_labels, (unsigned)mi.render_us);

    /* no fix: the banner, the last position; the drive ends (recording stops): the position is kept */
    int saves = pos_saves;
    start_map(false, HH_SCENE_CONNECTING, HH_ROUTE_STATE_ST, 1500, 5000);
    ui_map_info(&mi);
    CHECK(mi.banner && !strcmp(mi.banner_text, "NO GPS FIX"), "NO GPS FIX banner (%s)", mi.banner_text);
    CHECK(mi.status == MAPSVC_OK, "still a map, at the last fix");
    CHECK(pos_saves == saves + 1 && have_last_pos && last_lat > 32.278 && last_lat < 32.30, "the end of the drive keeps the position (%d)",
          pos_saves - saves);
    have_last_pos = true;
    last_lat = 32.30; last_lon = -90.18;
    hh_stub_set_scene(HH_SCENE_CONNECTING);
    ui_create(false);                                /* a power-up with no fix yet: the position from NVS */
    ui_goto(map_stop());
    run(1500);
    ui_map_info(&mi);
    CHECK(mi.banner && mi.status == MAPSVC_OK && mi.n_trail == 0, "power-up without a fix: last known position");

    /* no map on the card: says so, and how to make one */
    const char *keep = map_dir;
    map_dir = "testmap/none";
    mapsvc_reopen();
    start_map(false, HH_SCENE_DRIVE, HH_ROUTE_STATE_ST, 1500, 2000);
    ui_map_info(&mi);
    CHECK(mi.message && strstr(mi.message_title, "NO MAP"), "no map: %s", mi.message_title);
    CHECK(find_text(lv_screen_active(), "hondalog-handheld map-build"), "the message gives the map-build command");
    map_dir = keep;
    mapsvc_reopen();

    /* a MAP/ upload over the USB cable has the folder: the files are closed (a rename needs that), the screen says
     * why, and the map comes back when the upload lets go */
    start_map(false, HH_SCENE_DRIVE, HH_ROUTE_STATE_ST, 1500, 2000);
    ui_map_info(&mi);
    CHECK(mi.status == MAPSVC_OK && open_map_files() == 2, "before an upload: a map, MAP.IDX and MAP.DAT open (%d)", open_map_files());
    CHECK(mapsvc_hold(1) == 0, "hold: the worker lets go");
    CHECK(open_map_files() == 0, "hold: no map file is open (%d)", open_map_files());
    run(30000);                                      /* the car moves on: the next picture is drawn without the files */
    ui_map_info(&mi);
    CHECK(mi.status == MAPSVC_UPDATING && mi.message && strstr(mi.message_title, "UPDATED"), "the screen says the map is being updated (status %d, %s)",
          mi.status, mi.message_title);
    CHECK(open_map_files() == 0, "held through a redraw: still closed (%d)", open_map_files());
    CHECK(mapsvc_hold(1) == 0, "asking again is fine");
    mapsvc_hold(0);
    run(RETRY_MS_SIM + 2000);
    ui_map_info(&mi);
    CHECK(mi.status == MAPSVC_OK && !mi.message && open_map_files() == 2, "after the upload: the map is back (status %d, %d files open)",
          mi.status, open_map_files());
}

/* ---- render budget ------------------------------------------------------------ */

static void budget(void)
{
    printf("Redraw per frame with the stub's driving data (20 Hz updates, 30 ms refresh timer):\n");
    printf("  %-14s %8s %9s %10s %12s\n", "screen", "frames/s", "px/frame", "of screen", "host us/frame");
    for (int stop = 0; stop < ui_stops(); stop++) {
        start(false, HH_SCENE_DRIVE);
        ui_goto(stop);
        run(1000);
        ui_perf_totals_t a = *ui_perf_totals();
        run(10000);
        ui_perf_totals_t b = *ui_perf_totals();
        uint32_t frames = b.frames - a.frames, px = b.px - a.px, us = b.render_us - a.render_us;
        char title[24];
        int pages = ui_sensor_pages();
        if (stop == 0) snprintf(title, sizeof(title), "gauges");
        else if (stop <= pages) snprintf(title, sizeof(title), "sensors %d", stop);
        else snprintf(title, sizeof(title), stop == pages + 1 ? "gps" : stop == pages + 2 ? "map" : "link");
        printf("  %-14s %8.1f %9u %9.1f%% %12u\n", title, frames / 10.0, frames ? px / frames : 0,
               frames ? 100.0 * px / frames / (UI_W * UI_H) : 0, frames ? us / frames : 0);
    }
}

/* the Map screen while driving: the car moves every frame, a new picture now and then */
static void budget_map(void)
{
    static const struct { hh_stub_route_t route; double start_m; const char *name; } runs[] = {
        {HH_ROUTE_STATE_ST, 1500, "downtown 25 km/h"}, {HH_ROUTE_I55, 2500, "I-55 113 km/h"},
    };
    printf("\nMap screen, 30 s of driving (new pictures redraw the 480 x 216 body once each):\n");
    printf("  %-18s %8s %9s %10s %9s %14s\n", "scene", "frames/s", "px/frame", "of screen", "pictures", "worker us/pic");
    for (size_t i = 0; i < sizeof(runs) / sizeof(runs[0]); i++) {
        ui_map_info_t a, b;
        start_map(false, HH_SCENE_DRIVE, runs[i].route, runs[i].start_m, 8000);
        run(2000);
        ui_map_info(&a);
        ui_perf_totals_t pa = *ui_perf_totals();
        uint32_t worker = 0, n = 0, last = a.renders;
        for (int k = 0; k < 300; k++) {
            run(100);
            ui_map_info(&b);
            if (b.renders != last) { worker += b.render_us; n++; last = b.renders; }
        }
        ui_perf_totals_t pb = *ui_perf_totals();
        uint32_t frames = pb.frames - pa.frames, px = pb.px - pa.px;
        printf("  %-18s %8.1f %9u %9.1f%% %9u %14u\n", runs[i].name, frames / 30.0, frames ? px / frames : 0,
               frames ? 100.0 * px / frames / (UI_W * UI_H) : 0, (unsigned)n, n ? (unsigned)(worker / n) : 0);
    }
}

int main(int argc, char **argv)
{
    lv_init();
    lv_tick_set_cb(tick_cb);
    lv_display_t *disp = lv_display_create(UI_W, UI_H);
    lv_display_set_buffers(disp, fb, NULL, sizeof(fb), LV_DISPLAY_RENDER_MODE_DIRECT);
    lv_display_set_flush_cb(disp, flush_cb);
    ui_perf_attach(disp);
    hh_stub_set_clock(stub_clock);
    hh_core_start();
    if (getenv("HH_SIM_MAP")) map_dir = getenv("HH_SIM_MAP");     /* another MAP folder, e.g. a full build */

    const char *cmd = argc > 1 ? argv[1] : "";
    if (!strcmp(cmd, "shots") && argc > 2) return shots(argv[2]);
    if (!strcmp(cmd, "budget")) { budget(); budget_map(); return 0; }
    if (!strcmp(cmd, "test")) {
        test_contrast(); test_button(); test_fonts(); test_format(); test_units(); test_flow(); test_map();
        printf(failures ? "%d FAILED\n" : "all passed\n", failures);
        return failures != 0;
    }
    fprintf(stderr, "usage: hhsim shots DIR | test | budget\n");
    return 2;
}
