/* Frame shared by every screen (480 x 272):
 *
 *   y   0  h 30   top bar: screen title, then ECU / REC / STALL / GPS chips.
 *                 A warn or alarm replaces it with the banner, so the body
 *                 underneath never moves.
 *   y  30  h 216  the screen
 *   y 246  h 26   hint strip: what the button does now; fills while held
 *
 * and, over everything, the stall-captured screen.
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "ui.h"
#include "ui_data.h"
#include "ui_input.h"
#include "ui_perf.h"
#include "ui_screens.h"
#include "ui_shapes.h"
#include "ui_theme.h"

#define STALL_SHOW_MS   10000
#define STALL_FLASH_MS  2400
#define NOTE_MS         1500

typedef struct { lv_obj_t *shape, *text; } chip_t;

static struct {
    lv_obj_t *top, *title, *body, *hint, *hint_fill, *hint_text;
    chip_t ecu, rec, stall, gps;
    lv_obj_t *banner, *banner_shape, *banner_text;
    lv_obj_t *flash, *flash_shape, *flash_title, *flash_reason, *flash_line, *flash_hint;
} w;

static struct {
    int stop;                       /* index into the press cycle */
    ui_btn_t btn, btn_back;
    uint32_t now, t_data, t_press, t_note, t_flash;
    const char *note;
    bool have_snapshot, flash_on;
    int flash_inverted;             /* -1: colours not applied yet */
    int banner_level, banner_ch;
    bool acked;
    int acked_ch;
    uint32_t stall_count, drive_no;
    uint8_t stall_triggered;
    int64_t t_prev_us, t_drive_us;
    double trip_km;
    hh_snapshot_t snap;
} st;

/* ---- the press cycle: Gauges, Sensors 1..n, GPS, Link ---------------------- */

int ui_stops(void) { return 3 + ui_sensor_pages(); }

static ui_screen_id_t stop_screen(int stop, int *page)
{
    int pages = ui_sensor_pages();
    *page = 0;
    if (stop == 0) return UI_SCR_GAUGES;
    if (stop <= pages) { *page = stop - 1; return UI_SCR_SENSORS; }
    return stop == pages + 1 ? UI_SCR_GPS : UI_SCR_LINK;
}

static void show_stop(void)
{
    int page;
    char title[24];
    ui_screen_id_t id = stop_screen(st.stop, &page);
    lv_obj_clean(w.body);
    ui_screen_create(id, page, w.body);
    ui_screen_title(id, page, title, sizeof(title));
    ui_set_text(w.title, title);
}

/* ---- construction ----------------------------------------------------------- */

static chip_t chip_create(lv_obj_t *parent, int x)
{
    chip_t c;
    c.shape = ui_shape_create(parent, 16);
    lv_obj_set_pos(c.shape, x, (UI_TOP_H - UI_RULE - 16) / 2);
    c.text = ui_label(parent, &hh_font_16, x + 20, (UI_TOP_H - UI_RULE - 20) / 2, 0, LV_TEXT_ALIGN_LEFT);
    ui_ink(c.text, TK(fg));
    return c;
}

static void build(void)
{
    lv_obj_t *scr = lv_screen_active();
    lv_obj_clean(scr);
    lv_obj_clean(lv_layer_top());
    lv_obj_clean(lv_layer_sys());
    memset(&w, 0, sizeof(w));
    lv_obj_remove_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
    ui_fill(scr, TK(bg));

    w.top = ui_box(scr, 0, 0, UI_W, UI_TOP_H);
    ui_fill(w.top, TK(sunk));
    ui_border(w.top, TK(rule), UI_RULE, LV_BORDER_SIDE_BOTTOM);
    w.title = ui_label(w.top, &hh_font_20, 6, (UI_TOP_H - UI_RULE - 24) / 2, 142, LV_TEXT_ALIGN_LEFT);
    ui_ink(w.title, TK(fg));
    w.ecu = chip_create(w.top, 152);
    w.rec = chip_create(w.top, 214);
    w.stall = chip_create(w.top, 312);
    w.gps = chip_create(w.top, 394);
    lv_label_set_text(w.ecu.text, "ECU");
    lv_label_set_text(w.stall.text, "STALL");

    w.body = ui_box(scr, 0, UI_BODY_Y, UI_W, UI_BODY_H);

    w.hint = ui_box(scr, 0, UI_H - UI_HINT_H, UI_W, UI_HINT_H);
    ui_fill(w.hint, TK(sunk));
    ui_border(w.hint, TK(rule), UI_RULE, LV_BORDER_SIDE_TOP);
    w.hint_fill = ui_box(w.hint, 0, 0, 0, UI_HINT_H - UI_RULE);
    w.hint_text = ui_label(w.hint, &hh_font_16, 0, 0, UI_W, LV_TEXT_ALIGN_CENTER);

    /* banner: same rectangle as the top bar, on the top layer */
    w.banner = ui_box(lv_layer_top(), 0, 0, UI_W, UI_TOP_H);
    ui_border(w.banner, TK(rule), UI_RULE, LV_BORDER_SIDE_BOTTOM);
    w.banner_shape = ui_shape_create(w.banner, 20);
    lv_obj_set_pos(w.banner_shape, 6, (UI_TOP_H - UI_RULE - 20) / 2);
    w.banner_text = ui_label(w.banner, &hh_font_20, 34, (UI_TOP_H - UI_RULE - 24) / 2, UI_W - 40, LV_TEXT_ALIGN_LEFT);
    lv_obj_add_flag(w.banner, LV_OBJ_FLAG_HIDDEN);

    /* stall captured: the whole screen */
    w.flash = ui_box(lv_layer_top(), 0, 0, UI_W, UI_H);
    w.flash_shape = ui_shape_create(w.flash, 72);
    lv_obj_set_pos(w.flash_shape, 24, 28);
    w.flash_title = ui_label(w.flash, &hh_font_28, 116, 28, UI_W - 124, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(w.flash_title, "STALL CAPTURED");
    w.flash_line = ui_label(w.flash, &hh_font_28, 116, 66, UI_W - 124, LV_TEXT_ALIGN_LEFT);
    w.flash_reason = ui_label(w.flash, &hh_font_28, 24, 124, UI_W - 48, LV_TEXT_ALIGN_LEFT);
    lv_label_set_long_mode(w.flash_reason, LV_LABEL_LONG_WRAP);
    lv_obj_set_height(w.flash_reason, 2 * 35);
    w.flash_hint = ui_label(w.flash, &hh_font_20, 24, UI_H - 40, UI_W - 48, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(w.flash_hint, "PRESS TO DISMISS");
    lv_obj_add_flag(w.flash, LV_OBJ_FLAG_HIDDEN);

    ui_perf_create();
    st.banner_level = -1;
    st.flash_inverted = -1;
    st.flash_on = false;
    show_stop();
}

void ui_create(bool night)
{
    memset(&st, 0, sizeof(st));
    st.acked_ch = -1;
    ui_theme_set(night);
    build();
}

bool ui_night(void) { return ui_theme_night(); }

void ui_goto(int stop)
{
    st.stop = ((stop % ui_stops()) + ui_stops()) % ui_stops();
    show_stop();
}

/* ---- per-snapshot updates --------------------------------------------------- */

static void update_chips(const hh_snapshot_t *s)
{
    char text[16];
    ui_link_shape(w.ecu.shape, s->link.state);

    if (!s->log.sd_ok) {
        ui_shape_set(w.rec.shape, UI_SHAPE_CROSS, TK(alarm), TK(alarm));
        ui_set_text(w.rec.text, "NO SD");
    } else {
        snprintf(text, sizeof(text), "REC %u", (unsigned)s->log.drive_no);
        if (s->log.recording) ui_shape_set(w.rec.shape, UI_SHAPE_DOT, TK(alarm), TK(alarm));
        else ui_shape_set(w.rec.shape, UI_SHAPE_SQUARE, TK(fg), TK(fg));
        ui_set_text(w.rec.text, s->log.recording ? text : "IDLE");
    }

    if (s->stall.triggered) ui_shape_set(w.stall.shape, UI_SHAPE_OCTAGON, TK(alarm), TK(alarm));
    else ui_shape_set(w.stall.shape, s->stall.armed ? UI_SHAPE_RING : UI_SHAPE_SQUARE, TK(fg), TK(fg));

    ui_shape_set(w.gps.shape, s->gps.fix ? UI_SHAPE_DOT : UI_SHAPE_SQUARE, s->gps.fix ? TK(ok) : TK(fg), TK(fg));
    snprintf(text, sizeof(text), "GPS %u", (unsigned)s->gps.sats);
    ui_set_text(w.gps.text, text);
}

static void update_banner(const hh_snapshot_t *s, bool blink_on)
{
    uint16_t n;
    const hh_channel_info_t *info = ui_channels(&n);
    int level = s->top_alarm_level, ch = s->top_alarm_ch;
    if (level < 1 || ch < 0 || ch >= n || ch >= s->n_ch) level = 0;

    /* an acknowledgement lasts until the alarm clears or another channel takes over */
    if (level < 2 || ch != st.acked_ch) { st.acked = false; st.acked_ch = -1; }

    if (level != st.banner_level || ch != st.banner_ch) {
        st.banner_level = level;
        st.banner_ch = ch;
        if (!level) {
            lv_obj_add_flag(w.banner, LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_color_t ink = level >= 2 ? TK(on_alarm) : TK(on_warn);
            lv_obj_remove_flag(w.banner, LV_OBJ_FLAG_HIDDEN);
            ui_fill(w.banner, level >= 2 ? TK(alarm) : TK(warn));
            ui_ink(w.banner_text, ink);
            ui_shape_set(w.banner_shape, level >= 2 ? UI_SHAPE_OCTAGON : UI_SHAPE_TRIANGLE, ink, ink);
        }
    }
    if (!level) return;

    char label[24], value[16], limit[24], text[96];
    ui_upper(label, sizeof(label), info[ch].label[0] ? info[ch].label : info[ch].name);
    ui_fmt_value(value, sizeof(value), &info[ch], s->value[ch]);
    ui_fmt_limit(limit, sizeof(limit), &info[ch], s->value[ch], level);
    snprintf(text, sizeof(text), "%s  %s %s%s%s", label, value, info[ch].units, limit[0] ? "   limit " : "", limit);
    ui_set_text(w.banner_text, text);

    bool show = level < 2 || st.acked || blink_on;
    if (show) lv_obj_remove_flag(w.banner_shape, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(w.banner_shape, LV_OBJ_FLAG_HIDDEN);
}

static void flash_colours(bool inverted)
{
    if ((int)inverted == st.flash_inverted) return;
    st.flash_inverted = inverted;
    lv_color_t ground = inverted ? TK(on_alarm) : TK(alarm), ink = inverted ? TK(alarm) : TK(on_alarm);
    ui_fill(w.flash, ground);
    ui_ink(w.flash_title, ink); ui_ink(w.flash_reason, ink); ui_ink(w.flash_line, ink); ui_ink(w.flash_hint, ink);
    ui_shape_set(w.flash_shape, UI_SHAPE_OCTAGON, ink, ink);
}

static void update_flash(const hh_snapshot_t *s)
{
    bool fired = st.have_snapshot &&
                 ((s->stall.triggered && !st.stall_triggered) || s->stall.count > st.stall_count);
    st.stall_triggered = s->stall.triggered;
    st.stall_count = s->stall.count;

    if (fired) {
        char text[48], reason[sizeof(s->stall.last_reason) + 1];
        memcpy(reason, s->stall.last_reason, sizeof(s->stall.last_reason));
        reason[sizeof(s->stall.last_reason)] = 0;
        ui_set_text(w.flash_reason, reason[0] ? reason : "no reason given");
        if (s->log.sd_ok) snprintf(text, sizeof(text), "No. %u   drive %u", (unsigned)s->stall.count, (unsigned)s->log.drive_no);
        else snprintf(text, sizeof(text), "No. %u   NO SD CARD", (unsigned)s->stall.count);
        ui_set_text(w.flash_line, text);
        st.flash_on = true;
        st.t_flash = st.now;
        lv_obj_remove_flag(w.flash, LV_OBJ_FLAG_HIDDEN);
    }
    if (!st.flash_on) return;
    uint32_t age = st.now - st.t_flash;
    if (age >= STALL_SHOW_MS) {
        st.flash_on = false;
        lv_obj_add_flag(w.flash, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    flash_colours(age < STALL_FLASH_MS && (age / 300) % 2 == 1);
}

static void update_hint(void)
{
    uint32_t held = ui_btn_held_ms(&st.btn, st.now);
    bool alarm = st.banner_level >= 2 && !st.acked;
    const char *far = alarm ? "ACK ALARM" : ui_theme_night() ? "DAY" : "NIGHT";
    char text[64];
    lv_color_t ground = TK(sunk), ink = TK(fg);
    int fill = 0;

    if (held >= UI_BTN_LONG_MS) {
        snprintf(text, sizeof(text), "RELEASE = MARK      KEEP HOLDING = %s", far);
        ground = TK(accent); ink = TK(on_accent);
        fill = (int)((held - UI_BTN_LONG_MS) * UI_W / (UI_BTN_VLONG_MS - UI_BTN_LONG_MS));
    } else if (held >= 250) {
        snprintf(text, sizeof(text), "HOLD FOR MARK");
        fill = (int)(held * UI_W / UI_BTN_LONG_MS);
    } else if (st.note && st.now - st.t_note < NOTE_MS) {
        snprintf(text, sizeof(text), "%s", st.note);
        ground = TK(fg); ink = TK(bg);
    } else if (st.flash_on) {
        snprintf(text, sizeof(text), "PRESS  dismiss");
    } else {
        snprintf(text, sizeof(text), "PRESS next    HOLD 1s mark    HOLD 3s %s", alarm ? "ack" : ui_theme_night() ? "day" : "night");
    }
    ui_set_text(w.hint_text, text);
    if (!lv_color_eq(lv_obj_get_style_bg_color(w.hint, 0), ground)) ui_fill(w.hint, ground);
    if (!lv_color_eq(lv_obj_get_style_text_color(w.hint_text, 0), ink)) ui_ink(w.hint_text, ink);
    /* progress: a bar growing behind the text would cost contrast, so it is a
     * 4 px bar under the text, in the text colour */
    if (fill > UI_W) fill = UI_W;
    if (lv_obj_get_width(w.hint_fill) != fill) {
        lv_obj_set_size(w.hint_fill, fill, 4);
        lv_obj_set_y(w.hint_fill, UI_HINT_H - UI_RULE - 4);
    }
    if (!lv_color_eq(lv_obj_get_style_bg_color(w.hint_fill, 0), ink)) ui_fill(w.hint_fill, ink);
}

static void integrate_trip(const hh_snapshot_t *s)
{
    if (!st.have_snapshot || s->log.drive_no != st.drive_no) {
        st.drive_no = s->log.drive_no;
        st.t_drive_us = s->t_us;
        st.trip_km = 0;
    } else if (s->gps.fix && isfinite(s->gps.speed_kmh) && s->gps.speed_kmh > 1.0f) {
        double dt = (double)(s->t_us - st.t_prev_us) / 1e6;
        if (dt > 0 && dt < 2.0) st.trip_km += (double)s->gps.speed_kmh * dt / 3600.0;
    }
    st.t_prev_us = s->t_us;
}

static void on_data(void)
{
    hh_snapshot(&st.snap);
    const hh_snapshot_t *s = &st.snap;
    integrate_trip(s);

    bool blink_on = (st.now / 500) % 2 == 0;
    update_chips(s);
    update_banner(s, blink_on);
    update_flash(s);

    ui_derived_t d = {
        .trip_km = (float)st.trip_km,
        .trip_s = (uint32_t)((s->t_us - st.t_drive_us) / 1000000),
        .acked = st.acked, .blink_on = blink_on,
    };
    int page;
    ui_screen_update(stop_screen(st.stop, &page), s, &d);
    st.have_snapshot = true;
}

/* ---- input ------------------------------------------------------------------ */

static void note(const char *text)
{
    st.note = text;
    st.t_note = st.now;
}

static void step(int by)
{
    st.stop = (st.stop + by + ui_stops()) % ui_stops();
    show_stop();
    st.t_data = st.now - UI_DATA_PERIOD_MS;      /* fill the new screen this tick */
}

static bool dismiss_flash(void)
{
    if (!st.flash_on) return false;
    st.flash_on = false;
    lv_obj_add_flag(w.flash, LV_OBJ_FLAG_HIDDEN);
    return true;
}

static void on_button(ui_btn_event_t ev, bool back)
{
    if (ev == UI_BTN_NONE) return;
    st.t_press = st.now;
    if (dismiss_flash()) return;
    if (back) {
        if (ev == UI_BTN_SHORT) step(-1);
        return;
    }
    switch (ev) {
    case UI_BTN_SHORT:
        step(+1);
        break;
    case UI_BTN_LONG:
        hh_mark();
        note("MARKED");
        break;
    case UI_BTN_VLONG:
        if (st.banner_level >= 2 && !st.acked) {
            hh_ack_alarm();
            st.acked = true;
            st.acked_ch = st.banner_ch;
            note("ALARM ACKNOWLEDGED");
        } else {
            ui_theme_set(!ui_theme_night());
            build();
            ui_port_theme_changed(ui_theme_night());
            st.t_data = st.now - UI_DATA_PERIOD_MS;
        }
        break;
    default:
        break;
    }
}

void ui_tick(uint32_t now_ms, bool button, bool button_back)
{
    st.now = now_ms;
    on_button(ui_btn_step(&st.btn, button, now_ms), false);
    on_button(ui_btn_step(&st.btn_back, button_back, now_ms), true);

#if HH_UI_AUTOCYCLE_S > 0
    int page;
    if (stop_screen(st.stop, &page) == UI_SCR_SENSORS && ui_sensor_pages() > 1 &&
        now_ms - st.t_press >= (uint32_t)HH_UI_AUTOCYCLE_S * 1000u) {
        st.t_press = now_ms;
        st.stop = 1 + (page + 1) % ui_sensor_pages();
        show_stop();
        st.t_data = now_ms - UI_DATA_PERIOD_MS;
    }
#endif

    if (now_ms - st.t_data >= UI_DATA_PERIOD_MS) {
        st.t_data = now_ms;
        on_data();
    }
    update_hint();
    ui_perf_tick(now_ms);
}
