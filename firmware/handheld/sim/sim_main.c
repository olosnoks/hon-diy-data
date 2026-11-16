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

#include "lvgl.h"
#include "hh_data.h"
#include "hh_stub.h"
#include "ui.h"
#include "ui_data.h"
#include "ui_input.h"
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

void ui_port_theme_changed(bool night) { (void)night; theme_changes++; }
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

static void start(bool night, hh_stub_scene_t scene)
{
    hh_stub_set_scene(scene);
    ui_create(night);
    run(300);
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
    for (int night = 0; night < 2; night++) {
        const char *th = night ? "night" : "day";
        start(night, HH_SCENE_DRIVE);
        run(4000);
        for (int stop = 0; stop < ui_stops(); stop++) {
            ui_goto(stop);
            run(700);
            int pages = ui_sensor_pages();
            if (stop == 0) snprintf(title, sizeof(title), "gauges");
            else if (stop <= pages) snprintf(title, sizeof(title), "sensors-%d", stop);
            else snprintf(title, sizeof(title), stop == pages + 1 ? "gps" : "link");
            snprintf(name, sizeof(name), "%s-%d-%s", th, stop + 1, title);
            rc |= save_png(dir, name);
            check_layout(name);
        }

        static const struct { hh_stub_scene_t scene; int stop; const char *name; } states[] = {
            {HH_SCENE_WARN, 1, "warn-sensors"},  {HH_SCENE_WARN, 0, "warn-gauges"},
            {HH_SCENE_ALARM, 0, "alarm-gauges"}, {HH_SCENE_ALARM, 1, "alarm-sensors"},
            {HH_SCENE_LINK_LOST, 0, "linklost-gauges"}, {HH_SCENE_LINK_LOST, 1, "linklost-sensors"},
            {HH_SCENE_CONNECTING, 3, "connecting-gps"}, {HH_SCENE_CONNECTING, 4, "connecting-link"},
        };
        for (size_t i = 0; i < sizeof(states) / sizeof(states[0]); i++) {
            start(night, HH_SCENE_DRIVE);
            ui_goto(states[i].stop);
            run(500);
            hh_stub_set_scene(states[i].scene);
            run(1100 - clock_ms % 1000);            /* land in the "octagon shown" blink phase */
            snprintf(name, sizeof(name), "%s-%s", th, states[i].name);
            rc |= save_png(dir, name);
            check_layout(name);
        }

        /* stall: the full-screen capture, then what stays after it is dismissed */
        start(night, HH_SCENE_DRIVE);
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

        /* the hint strip while the button is held, and after a mark */
        start(night, HH_SCENE_DRIVE);
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
    const ui_tokens_t *themes[] = {&UI_DAYLIGHT, &UI_NIGHT};
    for (int t = 0; t < 2; t++) {
        ui_pair_t pairs[24];
        int n = ui_theme_pairs(themes[t], pairs, 24);
        for (int i = 0; i < n; i++) {
            double a = contrast(pairs[i].fg, pairs[i].bg, false), b = contrast(pairs[i].fg, pairs[i].bg, true);
            printf("  %-9s %-22s %5.2f / %5.2f\n", t ? "night" : "daylight", pairs[i].name, a, b);
            CHECK(a >= 7.0 && b >= 7.0, "%s %s is below 7:1", t ? "night" : "daylight", pairs[i].name);
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

static void test_flow(void)
{
    start(false, HH_SCENE_DRIVE);
    uint32_t marks = hh_stub_marks(), acks = hh_stub_acks();
    int changes = theme_changes;

    press(1500);
    CHECK(hh_stub_marks() == marks + 1, "1.5 s press marks once");
    press(3300);
    CHECK(hh_stub_marks() == marks + 1, "3 s press does not mark");
    CHECK(ui_night() && theme_changes == changes + 1, "3 s press toggles the theme when no alarm is blinking");
    press(3300);
    CHECK(!ui_night(), "and back");

    hh_stub_set_scene(HH_SCENE_ALARM);
    run(300);
    press(3300);
    CHECK(hh_stub_acks() == acks + 1 && !ui_night(), "3 s press acknowledges a blinking alarm instead");
    press(3300);
    CHECK(hh_stub_acks() == acks + 1 && ui_night(), "once acknowledged, 3 s is the theme again");

    /* every stop, every scene, both themes: nothing cut off */
    static const hh_stub_scene_t scenes[] = {HH_SCENE_DRIVE, HH_SCENE_WARN, HH_SCENE_ALARM, HH_SCENE_STALL,
                                             HH_SCENE_LINK_LOST, HH_SCENE_CONNECTING};
    for (int night = 0; night < 2; night++)
        for (size_t s = 0; s < sizeof(scenes) / sizeof(scenes[0]); s++) {
            start(night, HH_SCENE_DRIVE);
            hh_stub_set_scene(scenes[s]);
            run(300);
            if (scenes[s] == HH_SCENE_STALL) check_layout("stall screen");
            press(100);
            for (int stop = 0; stop < ui_stops(); stop++) {
                char where[48];
                snprintf(where, sizeof(where), "%s scene %d stop %d", night ? "night" : "day", (int)scenes[s], stop);
                ui_goto(stop);
                run(1200);
                check_layout(where);
            }
        }
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
        else snprintf(title, sizeof(title), stop == pages + 1 ? "gps" : "link");
        printf("  %-14s %8.1f %9u %9.1f%% %12u\n", title, frames / 10.0, frames ? px / frames : 0,
               frames ? 100.0 * px / frames / (UI_W * UI_H) : 0, frames ? us / frames : 0);
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

    const char *cmd = argc > 1 ? argv[1] : "";
    if (!strcmp(cmd, "shots") && argc > 2) return shots(argv[2]);
    if (!strcmp(cmd, "budget")) { budget(); return 0; }
    if (!strcmp(cmd, "test")) {
        test_contrast(); test_button(); test_fonts(); test_format(); test_flow();
        printf(failures ? "%d FAILED\n" : "all passed\n", failures);
        return failures != 0;
    }
    fprintf(stderr, "usage: hhsim shots DIR | test | budget\n");
    return 2;
}
