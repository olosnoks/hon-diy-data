/* The Map screen (480 x 216 body):
 *
 *   the map picture, full size, from the worker
 *   NO GPS FIX banner        top, accent band, hollow square (shape-coded "off")
 *   zoom chip                top right: AUTO 2/4 or FIXED 2/4
 *   labels                   road shields, street and place names on plates,
 *                            placed best first where they collide with nothing
 *   the car                  an arrow when moving, a dot when stopped,
 *                            a hollow square at the last known position
 *   scale bar                bottom left, in miles (feet when short), or km and m
 *   attribution              bottom right: (c) OpenStreetMap contributors
 *
 * The picture is redrawn only when the car has moved REDRAW_PX from its
 * centre, the zoom or theme changed, or a pin was added; in between only the
 * car moves (a 32 x 32 object), so the screen costs little to refresh.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ui.h"
#include "ui_data.h"
#include "ui_map.h"
#include "ui_mapsvc.h"
#include "ui_shapes.h"
#include "ui_theme.h"
#include "ui_units.h"

#define TRAIL_STEP_M    25.0            /* a trail point every 25 m: 512 points, the last 12.8 km */
#define MOVING_KMH      5.0f
#define REDRAW_PX       24              /* the car this far off the picture's centre: draw a new one */
#define RETRY_MS        5000            /* no map: ask again this often (the card may come back) */
#define ZOOM_OUT_MS     2000            /* faster for this long: one level wider */
#define ZOOM_IN_MS      6000            /* slower for this long: one level closer (not at every light) */
/* The last position goes to NVS (flash) when the drive ends: recording stops
 * or the ECU goes quiet. While driving only every SAVE_MS and after SAVE_M,
 * so that a power cut mid-drive still leaves a recent one: a flash write
 * stalls PSRAM for a few ms, which is better done rarely (see HARDWARE.md). */
#define SAVE_MS         (15u * 60u * 1000u)
#define SAVE_M          1000.0
#define N_LABELS        10
#define PAD             4
#define CAR             32

/* zoom: speed (km/h) at which the next wider level takes over; back below it less 8 */
static const float ZOOM_KMH[UI_MAP_LEVELS - 1] = {30, 60, 90};
#define ZOOM_HYST_KMH 8

/* ---- tracking: lives across screens and theme changes ------------------------------ */

static struct {
    bool have_drive, have_counts, have_pos, from_nvs, fix, saved, was_recording, was_standby, unsaved;
    uint32_t drive_no, stall_count, markers;
    mapsvc_ll_t trail[MAPSVC_TRAIL];            /* a ring */
    int trail_head, trail_n;
    mapsvc_pin_t pins[MAPSVC_PINS];
    int n_pins;
    uint32_t pins_version;
    double lat, lon, saved_lat, saved_lon;
    float speed, heading;
    uint32_t t_saved, t_level, t_want;
    int level, want, manual;
    uint32_t renders;
} tk;

static double dist_m(double lat1, double lon1, double lat2, double lon2)
{
    double ky = 111132.954, kx = 111319.49 * cos(lat1 * 0.017453292519943295);
    double dx = (lon2 - lon1) * kx, dy = (lat2 - lat1) * ky;
    return sqrt(dx * dx + dy * dy);
}

void ui_map_reset(void)
{
    memset(&tk, 0, sizeof(tk));
    tk.manual = -1;
    tk.have_pos = tk.from_nvs = ui_port_last_pos(&tk.lat, &tk.lon);
    mapsvc_init();
}

static void add_pin(uint8_t kind)
{
    if (!tk.have_pos) return;
    if (tk.n_pins == MAPSVC_PINS) {                 /* full: drop the oldest */
        memmove(tk.pins, tk.pins + 1, (MAPSVC_PINS - 1) * sizeof(tk.pins[0]));
        tk.n_pins--;
    }
    tk.pins[tk.n_pins++] = (mapsvc_pin_t){(float)tk.lat, (float)tk.lon, kind};
    tk.pins_version++;
}

static int level_for(float kmh, int cur)
{
    int lv = cur;
    while (lv < UI_MAP_LEVELS - 1 && kmh >= ZOOM_KMH[lv]) lv++;
    while (lv > 0 && kmh < ZOOM_KMH[lv - 1] - ZOOM_HYST_KMH) lv--;
    return lv;
}

void ui_map_track(const hh_snapshot_t *s, uint32_t now)
{
    if (!tk.have_drive || s->log.drive_no != tk.drive_no) {
        tk.trail_n = tk.trail_head = 0;                 /* a new drive: a new trail, no pins */
        tk.n_pins = 0;
        tk.pins_version++;
        tk.drive_no = s->log.drive_no;
        tk.have_drive = true;
    }
    tk.fix = s->gps.fix != 0 && isfinite(s->gps.lat) && isfinite(s->gps.lon) &&
             (s->gps.lat != 0 || s->gps.lon != 0) && fabs(s->gps.lat) <= 90 && fabs(s->gps.lon) <= 180;
    if (tk.fix) {
        tk.lat = s->gps.lat;
        tk.lon = s->gps.lon;
        tk.speed = isfinite(s->gps.speed_kmh) ? s->gps.speed_kmh : 0;
        tk.heading = isfinite(s->gps.heading) ? s->gps.heading : 0;
        tk.have_pos = true;
        tk.from_nvs = false;
        const mapsvc_ll_t *last = tk.trail_n ? &tk.trail[(tk.trail_head + MAPSVC_TRAIL - 1) % MAPSVC_TRAIL] : NULL;
        if (!last || dist_m(last->lat, last->lon, tk.lat, tk.lon) >= TRAIL_STEP_M) {
            tk.trail[tk.trail_head] = (mapsvc_ll_t){(float)tk.lat, (float)tk.lon};
            tk.trail_head = (tk.trail_head + 1) % MAPSVC_TRAIL;
            if (tk.trail_n < MAPSVC_TRAIL) tk.trail_n++;
        }
        tk.unsaved = true;
        if (!tk.saved) { tk.saved = true; tk.t_saved = now; tk.saved_lat = tk.lat; tk.saved_lon = tk.lon; }
        /* the automatic zoom: a new level has to hold for a while */
        int want = level_for(tk.speed, tk.level);
        if (want != tk.want) { tk.want = want; tk.t_want = now; }
        if (want != tk.level && now - tk.t_want >= (want > tk.level ? ZOOM_OUT_MS : ZOOM_IN_MS)) {
            tk.level = want;
            tk.t_level = now;
        }
    }
    /* keep the position: at the end of a drive, and now and then on a long one */
    bool ended = (tk.was_recording && !s->log.recording) || (!tk.was_standby && s->standby);
    bool due = s->log.recording && now - tk.t_saved >= SAVE_MS && dist_m(tk.saved_lat, tk.saved_lon, tk.lat, tk.lon) >= SAVE_M;
    if (tk.unsaved && (ended || due)) {
        ui_port_save_pos(tk.lat, tk.lon);
        tk.unsaved = false;
        tk.t_saved = now;
        tk.saved_lat = tk.lat;
        tk.saved_lon = tk.lon;
    }
    tk.was_recording = s->log.recording;
    tk.was_standby = s->standby;

    if (tk.have_counts) {
        if (s->stall.count > tk.stall_count) add_pin(HM_PIN_STALL);
        for (uint32_t i = tk.markers; i < s->markers && i < tk.markers + 4; i++) add_pin(HM_PIN_MARK);
    }
    tk.stall_count = s->stall.count;
    tk.markers = s->markers;
    tk.have_counts = true;
}

void ui_map_zoom_step(void)
{
    tk.manual = tk.manual >= UI_MAP_LEVELS - 1 ? -1 : tk.manual + 1;
}

static int view_level(void) { return tk.manual >= 0 ? tk.manual : tk.level; }

/* ---- the screen ---------------------------------------------------------------- */

typedef enum { CAR_NONE, CAR_ARROW, CAR_DOT, CAR_LOST } car_t;

static struct {
    lv_obj_t *root, *img, *car, *banner, *banner_shape, *banner_text, *banner_sub, *zoom;
    lv_obj_t *scale, *scale_bar, *scale_l, *scale_r, *scale_text, *attrib;
    lv_obj_t *msg, *msg_shape, *msg_title, *msg_body;
    lv_obj_t *label[N_LABELS];
    lv_area_t keep_out[6];
    int n_keep_out;
    lv_image_dsc_t dsc;
    const mapsvc_result_t *res;         /* the picture on screen */
    uint32_t want_seq, t_req, pins_version;
    bool requested;
    car_t car_kind;
    int car_heading;
    bool banner_was;
    int n_labels;
    char labels[N_LABELS][32];
} sc;

static const mapsvc_result_t *last_res;   /* survives the screen: the worker's canvas still holds it */

static uint16_t c565(uint32_t hex) { return lv_color_to_u16(lv_color_hex(hex)); }

static void palette(hm_palette_t *p)
{
    const ui_tokens_t *t = ui_tokens();
    p->bg = c565(t->bg);
    p->road = c565(t->fg);
    p->water = c565(t->water);
    p->rail = c565(t->rule2);
    p->trail = c565(t->accent);
    p->stall = c565(t->alarm);
    p->mark = c565(t->accent);
    p->place = c565(t->fg);
    p->fuel = c565(t->fg2);
}

/* the car: drawn in place, so it can turn without an image */
static void car_draw(lv_event_t *e)
{
    lv_obj_t *obj = lv_event_get_target(e);
    lv_layer_t *layer = lv_event_get_layer(e);
    lv_area_t a;
    lv_obj_get_coords(obj, &a);
    int cx = (a.x1 + a.x2 + 1) / 2, cy = (a.y1 + a.y2 + 1) / 2;
    lv_draw_rect_dsc_t r;
    switch (sc.car_kind) {
    case CAR_ARROW: {
        /* a dart pointing along the heading, accent on a bg halo */
        static const float shape[4][2] = {{0, -13}, {9, 10}, {0, 5}, {-9, 10}};
        float h = (float)sc.car_heading * 0.017453293f, ch = cosf(h), sh = sinf(h);
        for (int pass = 0; pass < 2; pass++) {
            float k = pass ? 1.0f : 1.38f;
            lv_point_precise_t p[4];
            for (int i = 0; i < 4; i++) {
                float x = shape[i][0] * k, y = shape[i][1] * k + (pass ? 0 : 1.0f);
                p[i].x = (lv_value_precise_t)lroundf((float)cx + x * ch - y * sh);
                p[i].y = (lv_value_precise_t)lroundf((float)cy + x * sh + y * ch);
            }
            lv_draw_triangle_dsc_t d;
            lv_draw_triangle_dsc_init(&d);
            d.bg_color = pass ? TK(accent) : TK(bg);
            d.bg_opa = LV_OPA_COVER;
            d.p[0] = p[0]; d.p[1] = p[1]; d.p[2] = p[2];
            lv_draw_triangle(layer, &d);
            d.p[0] = p[0]; d.p[1] = p[2]; d.p[2] = p[3];
            lv_draw_triangle(layer, &d);
        }
        break;
    }
    case CAR_DOT:
        for (int pass = 0; pass < 2; pass++) {
            int rad = pass ? 6 : 9;
            lv_draw_rect_dsc_init(&r);
            r.radius = LV_RADIUS_CIRCLE;
            r.bg_color = pass ? TK(accent) : TK(bg);
            r.bg_opa = LV_OPA_COVER;
            lv_area_t b = {cx - rad, cy - rad, cx + rad - 1, cy + rad - 1};
            lv_draw_rect(layer, &r, &b);
        }
        break;
    case CAR_LOST: {                    /* hollow square: "not connected", as on the GPS chip */
        lv_draw_rect_dsc_init(&r);
        r.bg_color = TK(bg);
        r.bg_opa = LV_OPA_COVER;
        r.border_color = TK(fg);
        r.border_width = 3;
        r.border_opa = LV_OPA_COVER;
        lv_area_t b = {cx - 8, cy - 8, cx + 7, cy + 7};
        lv_draw_rect(layer, &r, &b);
        break;
    }
    default:
        break;
    }
}

static lv_obj_t *plate(lv_obj_t *parent, const lv_font_t *font, int border)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_remove_flag(l, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, TK(fg), 0);
    lv_obj_set_style_bg_color(l, TK(bg), 0);
    lv_obj_set_style_bg_opa(l, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(l, TK(fg), 0);
    lv_obj_set_style_border_width(l, border, 0);
    lv_obj_set_style_pad_hor(l, 3, 0);
    lv_obj_set_style_pad_ver(l, 0, 0);
    lv_obj_set_style_radius(l, 0, 0);
    lv_label_set_long_mode(l, LV_LABEL_LONG_CLIP);
    return l;
}

/* size a plate to its text; returns its outer size */
static lv_point_t plate_fit(lv_obj_t *l, const char *text)
{
    lv_point_t sz;
    const lv_font_t *font = lv_obj_get_style_text_font(l, 0);
    lv_text_get_size(&sz, text, font, 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
    int b = lv_obj_get_style_border_width(l, 0), ph = lv_obj_get_style_pad_left(l, 0);
    sz.x += 2 * (b + ph);
    sz.y += 2 * b;
    ui_set_text(l, text);
    lv_obj_set_size(l, sz.x, sz.y);
    return sz;
}

static void on_delete(lv_event_t *e)
{
    (void)e;
    sc.root = NULL;
}

static void message(const char *title, const char *body, ui_shape_t shape, lv_color_t colour)
{
    if (!title) {
        lv_obj_add_flag(sc.msg, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    ui_set_text(sc.msg_title, title);
    ui_set_text(sc.msg_body, body);
    ui_shape_set(sc.msg_shape, shape, colour, colour);
    lv_obj_remove_flag(sc.msg, LV_OBJ_FLAG_HIDDEN);
}

void ui_map_create(lv_obj_t *body)
{
    memset(&sc, 0, sizeof(sc));
    sc.root = ui_box(body, 0, 0, UI_W, UI_BODY_H);
    ui_fill(sc.root, TK(bg));
    lv_obj_add_event_cb(sc.root, on_delete, LV_EVENT_DELETE, NULL);

    sc.img = lv_image_create(sc.root);
    lv_obj_set_pos(sc.img, 0, 0);
    lv_obj_set_size(sc.img, MAPSVC_W, MAPSVC_H);
    lv_obj_add_flag(sc.img, LV_OBJ_FLAG_HIDDEN);

    for (int i = 0; i < N_LABELS; i++) {
        sc.label[i] = plate(sc.root, &hh_font_16, 0);
        lv_obj_add_flag(sc.label[i], LV_OBJ_FLAG_HIDDEN);
    }

    sc.car = ui_box(sc.root, (UI_W - CAR) / 2, (UI_BODY_H - CAR) / 2, CAR, CAR);
    lv_obj_add_event_cb(sc.car, car_draw, LV_EVENT_DRAW_MAIN, NULL);
    sc.car_kind = CAR_NONE;

    /* scale bar: text over a bar with end ticks, on a plate */
    sc.scale = ui_box(sc.root, PAD, UI_BODY_H - PAD - 30, 120, 30);
    ui_fill(sc.scale, TK(bg));
    sc.scale_text = ui_label(sc.scale, &hh_font_16, 4, 0, 0, LV_TEXT_ALIGN_LEFT);
    ui_ink(sc.scale_text, TK(fg));
    sc.scale_bar = ui_box(sc.scale, 4, 22, 100, 4);
    ui_fill(sc.scale_bar, TK(fg));
    sc.scale_l = ui_box(sc.scale, 4, 16, 3, 12);
    ui_fill(sc.scale_l, TK(fg));
    sc.scale_r = ui_box(sc.scale, 100, 16, 3, 12);
    ui_fill(sc.scale_r, TK(fg));

    sc.attrib = plate(sc.root, &hh_font_16, 0);
    lv_obj_set_style_text_color(sc.attrib, TK(fg2), 0);
    lv_point_t a = plate_fit(sc.attrib, "\xC2\xA9 OpenStreetMap contributors");
    lv_obj_set_pos(sc.attrib, UI_W - PAD - a.x, UI_BODY_H - PAD - a.y);

    sc.zoom = plate(sc.root, &hh_font_16, UI_RULE);
    lv_obj_set_style_border_color(sc.zoom, TK(rule), 0);
    lv_obj_add_flag(sc.zoom, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(sc.scale, LV_OBJ_FLAG_HIDDEN);

    /* NO GPS FIX: the top of the map, an accent band (no light surfaces) */
    sc.banner = ui_box(sc.root, 0, 0, UI_W, 28);
    ui_fill(sc.banner, TK(accent));
    sc.banner_shape = ui_shape_create(sc.banner, 16);
    lv_obj_set_pos(sc.banner_shape, 6, 6);
    ui_shape_set(sc.banner_shape, UI_SHAPE_SQUARE, TK(on_accent), TK(on_accent));
    sc.banner_text = ui_label(sc.banner, &hh_font_20, 28, 2, 0, LV_TEXT_ALIGN_LEFT);
    ui_ink(sc.banner_text, TK(on_accent));
    lv_label_set_text(sc.banner_text, "NO GPS FIX");
    sc.banner_sub = ui_label(sc.banner, &hh_font_16, 160, 4, UI_W - 166, LV_TEXT_ALIGN_RIGHT);
    ui_ink(sc.banner_sub, TK(on_accent));
    lv_obj_add_flag(sc.banner, LV_OBJ_FLAG_HIDDEN);

    /* no map / no card / damaged: a panel in the middle */
    sc.msg = ui_box(sc.root, 12, 12, UI_W - 24, UI_BODY_H - 24 - 30);
    ui_fill(sc.msg, TK(panel));
    ui_border(sc.msg, TK(rule), UI_RULE, LV_BORDER_SIDE_FULL);
    sc.msg_shape = ui_shape_create(sc.msg, 20);
    lv_obj_set_pos(sc.msg_shape, 8, 8);
    sc.msg_title = ui_label(sc.msg, &hh_font_20, 36, 5, UI_W - 24 - 46, LV_TEXT_ALIGN_LEFT);
    ui_ink(sc.msg_title, TK(fg));
    sc.msg_body = ui_label(sc.msg, &hh_font_16, 8, 36, UI_W - 24 - 20, LV_TEXT_ALIGN_LEFT);
    lv_label_set_long_mode(sc.msg_body, LV_LABEL_LONG_WRAP);
    lv_obj_set_height(sc.msg_body, 5 * 20);
    ui_ink(sc.msg_body, TK(fg));
    lv_obj_add_flag(sc.msg, LV_OBJ_FLAG_HIDDEN);

    sc.pins_version = tk.pins_version - 1;          /* ask for a picture at the first update */
    sc.res = NULL;
    /* the last picture is still in the worker's canvas: show it at once if it fits the theme */
    if (last_res && last_res->night == ui_theme_darker()) sc.res = last_res;
}

/* ---- labels --------------------------------------------------------------------------- */

static bool overlaps(const lv_area_t *a, const lv_area_t *b)
{
    return a->x1 <= b->x2 + 2 && b->x1 <= a->x2 + 2 && a->y1 <= b->y2 + 2 && b->y1 <= a->y2 + 2;
}

static bool free_at(const lv_area_t *r, const lv_area_t *placed, int n)
{
    if (r->x1 < 2 || r->y1 < 2 || r->x2 > UI_W - 3 || r->y2 > UI_BODY_H - 3) return false;
    for (int i = 0; i < sc.n_keep_out; i++) if (overlaps(r, &sc.keep_out[i])) return false;
    for (int i = 0; i < n; i++) if (overlaps(r, &placed[i])) return false;
    return true;
}

static void keep_out(lv_obj_t *obj)
{
    if (lv_obj_has_flag(obj, LV_OBJ_FLAG_HIDDEN) || sc.n_keep_out >= 6) return;
    lv_obj_update_layout(obj);
    lv_area_t a;
    lv_obj_get_coords(obj, &a);
    lv_area_t root;
    lv_obj_get_coords(sc.root, &root);
    lv_area_move(&a, -root.x1, -root.y1);
    sc.keep_out[sc.n_keep_out++] = a;
}

static void place_labels(const hm_render_out_t *out)
{
    lv_area_t placed[N_LABELS];
    int n = 0;
    sc.n_keep_out = 0;
    keep_out(sc.scale);
    keep_out(sc.attrib);
    keep_out(sc.zoom);
    keep_out(sc.banner);
    sc.keep_out[sc.n_keep_out++] = (lv_area_t){UI_W / 2 - 22, UI_BODY_H / 2 - 22, UI_W / 2 + 21, UI_BODY_H / 2 + 21};

    for (int i = 0; i < out->n_labels && n < N_LABELS; i++) {
        const hm_label_t *c = &out->labels[i];
        lv_obj_t *l = sc.label[n];
        bool ref = c->kind == HM_LABEL_ROAD_REF, place = c->kind == HM_LABEL_PLACE;
        lv_obj_set_style_text_font(l, place && c->cls == HM_CITY ? &hh_font_20 : &hh_font_16, 0);
        lv_obj_set_style_border_width(l, ref ? UI_RULE : 0, 0);
        lv_obj_set_style_text_color(l, place && c->cls >= HM_VILLAGE ? TK(fg2) : TK(fg), 0);
        lv_point_t sz = plate_fit(l, c->text);
        /* a road's label sits on the road; a place's beside its dot */
        int cand[4][2], k = 0;
        if (!place) {
            cand[k][0] = c->x - sz.x / 2; cand[k][1] = c->y - sz.y / 2; k++;
        } else {
            cand[k][0] = c->x + 8;            cand[k][1] = c->y - sz.y / 2; k++;
            cand[k][0] = c->x - 8 - sz.x;     cand[k][1] = c->y - sz.y / 2; k++;
            cand[k][0] = c->x - sz.x / 2;     cand[k][1] = c->y - 8 - sz.y; k++;
            cand[k][0] = c->x - sz.x / 2;     cand[k][1] = c->y + 8; k++;
        }
        for (int j = 0; j < k; j++) {
            lv_area_t r = {cand[j][0], cand[j][1], cand[j][0] + sz.x - 1, cand[j][1] + sz.y - 1};
            if (!free_at(&r, placed, n)) continue;
            lv_obj_set_pos(l, r.x1, r.y1);
            lv_obj_remove_flag(l, LV_OBJ_FLAG_HIDDEN);
            snprintf(sc.labels[n], sizeof(sc.labels[n]), "%s", c->text);
            placed[n++] = r;
            break;
        }
    }
    sc.n_labels = n;
    for (int i = n; i < N_LABELS; i++) lv_obj_add_flag(sc.label[i], LV_OBJ_FLAG_HIDDEN);
}

/* ---- scale and zoom ---------------------------------------------------------------- */

static void scale_bar(float mpp)
{
    size_t n, i = 0;
    const ui_scale_step_t *steps = ui_units_scale_steps(&n);    /* miles and feet, or km and m */
    while (i + 1 < n && steps[i].m / mpp > 112) i++;
    int px = (int)lroundf(steps[i].m / mpp);
    ui_set_text(sc.scale_text, steps[i].text);
    lv_obj_update_layout(sc.scale_text);
    int w = LV_MAX(px + 3, lv_obj_get_width(sc.scale_text)) + 8;
    lv_obj_set_width(sc.scale, w);
    lv_obj_set_width(sc.scale_bar, px);
    lv_obj_set_x(sc.scale_r, 4 + px - 3);
}

static void zoom_chip(int top)
{
    char text[24];
    snprintf(text, sizeof(text), "%s %d/%d", tk.manual >= 0 ? "FIXED" : "AUTO", view_level() + 1, UI_MAP_LEVELS);
    lv_point_t sz = plate_fit(sc.zoom, text);
    lv_obj_set_pos(sc.zoom, UI_W - PAD - sz.x, top);
}

/* ---- per snapshot ----------------------------------------------------------------- */

static void show_result(const mapsvc_result_t *r, const hh_snapshot_t *s)
{
    if (r->status == MAPSVC_OK || r->status == MAPSVC_NO_MAP || r->status == MAPSVC_BAD_MAP || r->status == MAPSVC_UPDATING) {
        sc.dsc.header.magic = LV_IMAGE_HEADER_MAGIC;
        sc.dsc.header.cf = LV_COLOR_FORMAT_RGB565;
        sc.dsc.header.w = MAPSVC_W;
        sc.dsc.header.h = MAPSVC_H;
        sc.dsc.header.stride = MAPSVC_W * 2;
        sc.dsc.data_size = MAPSVC_W * MAPSVC_H * 2;
        sc.dsc.data = (const uint8_t *)r->px;
        lv_image_cache_drop(&sc.dsc);
        lv_image_set_src(sc.img, &sc.dsc);                 /* the same descriptor: LVGL redraws it */
        lv_obj_remove_flag(sc.img, LV_OBJ_FLAG_HIDDEN);
    }
    scale_bar(r->mpp);
    /* no map: no scale, no zoom, no attribution, only the message */
    lv_obj_t *const overlays[] = {sc.scale, sc.attrib, sc.zoom};
    for (size_t i = 0; i < sizeof(overlays) / sizeof(overlays[0]); i++) {
        if (r->status == MAPSVC_OK) lv_obj_remove_flag(overlays[i], LV_OBJ_FLAG_HIDDEN);
        else lv_obj_add_flag(overlays[i], LV_OBJ_FLAG_HIDDEN);
    }
    if (r->status == MAPSVC_OK) {
        message(NULL, NULL, UI_SHAPE_NONE, TK(fg));
        place_labels(&r->out);
    } else {
        for (int i = 0; i < N_LABELS; i++) lv_obj_add_flag(sc.label[i], LV_OBJ_FLAG_HIDDEN);
        sc.n_labels = 0;
        char text[200];
        if (r->status == MAPSVC_NO_MAP && !s->log.sd_ok) {
            message("NO SD CARD", "The map is read from the MAP folder on the SD card. Put the card in and "
                    "restart the handheld.", UI_SHAPE_CROSS, TK(alarm));
        } else if (r->status == MAPSVC_NO_MAP) {
            message("NO MAP ON THE SD CARD", "Build one on the laptop and copy its MAP folder to the "
                    "card's root:\n  hondalog-handheld map-build --pbf STATE.osm.pbf\n"
                    "    --center LAT,LON --radius-mi 100 --out SDCARD", UI_SHAPE_SQUARE, TK(fg));
        } else if (r->status == MAPSVC_UPDATING) {
            message("MAP BEING UPDATED", "New map files are coming in over the USB cable. The map comes back when "
                    "they are all in.", UI_SHAPE_SQUARE, TK(fg));
        } else if (r->status == MAPSVC_BAD_MAP) {
            snprintf(text, sizeof(text), "MAP/ on the card: %s. Build it again:\n  hondalog-handheld map-build "
                     "--pbf STATE.osm.pbf\n    --center LAT,LON --radius-mi 100 --out SDCARD", hm_strerror(r->err));
            message("THE MAP CANNOT BE READ", text, UI_SHAPE_CROSS, TK(alarm));
        } else {
            message("NO MEMORY FOR THE MAP", "The map needs about 2 MB of PSRAM.", UI_SHAPE_CROSS, TK(alarm));
        }
    }
}

static void request(uint32_t now)
{
    mapsvc_req_t *q = mapsvc_scratch();
    if (!q) return;
    int level = view_level();
    q->lat = tk.have_pos ? tk.lat : UI_MAP_HOME_LAT;
    q->lon = tk.have_pos ? tk.lon : UI_MAP_HOME_LON;
    q->level = level;
    q->mpp = mapsvc_mpp(level);
    q->night = ui_theme_darker();
    palette(&q->pal);
    /* the scale bar and the attribution along the bottom, the banner or the zoom chip at the top */
    q->inset[0] = 16; q->inset[1] = 40; q->inset[2] = 16; q->inset[3] = 44;
    q->n_trail = (uint16_t)tk.trail_n;
    for (int i = 0; i < tk.trail_n; i++)
        q->trail[i] = tk.trail[(tk.trail_head + MAPSVC_TRAIL - tk.trail_n + i) % MAPSVC_TRAIL];
    q->n_pins = (uint16_t)tk.n_pins;
    memcpy(q->pins, tk.pins, (size_t)tk.n_pins * sizeof(tk.pins[0]));
    sc.want_seq = mapsvc_submit(q);
    sc.requested = true;
    sc.t_req = now;
    sc.pins_version = tk.pins_version;
}

static bool want_new(uint32_t now, float off_x, float off_y)
{
    const mapsvc_result_t *r = sc.res;
    if (!sc.requested) return true;
    if (!r) return false;                               /* the first is on its way */
    if (r->night != ui_theme_darker() || r->level != view_level() || sc.pins_version != tk.pins_version) return true;
    if (r->status != MAPSVC_OK) return now - sc.t_req >= RETRY_MS;
    return fabsf(off_x) > REDRAW_PX || fabsf(off_y) > REDRAW_PX;
}

static bool banner_update(double lat, double lon)
{
    const mapsvc_result_t *r = sc.res;
    bool outside = r && r->status == MAPSVC_OK && tk.fix && r->radius_m > 0 &&
                   dist_m(r->lat0, r->lon0, lat, lon) > r->radius_m;
    bool banner = !tk.fix || outside;
    if (banner) {
        ui_set_text(sc.banner_text, tk.fix ? "OUTSIDE THE MAP" : "NO GPS FIX");
        ui_set_text(sc.banner_sub, tk.fix ? "beyond MAP/ on the card" :
                    tk.have_pos ? (tk.from_nvs ? "showing the last known position" : "showing the last fix")
                                : "no position yet: Jackson");
        lv_obj_remove_flag(sc.banner, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(sc.banner, LV_OBJ_FLAG_HIDDEN);
    }
    zoom_chip(banner ? 28 + PAD : PAD);
    return banner;
}

void ui_map_update(const hh_snapshot_t *s, uint32_t now)
{
    if (!sc.root) return;
    double lat = tk.have_pos ? tk.lat : UI_MAP_HOME_LAT, lon = tk.have_pos ? tk.lon : UI_MAP_HOME_LON;
    const mapsvc_result_t *r = mapsvc_take();
    bool fresh = r != NULL || (sc.res && !sc.requested);  /* a new picture, or the one kept from before */
    if (r) {
        last_res = r;
        tk.renders++;
        sc.res = r;
    }
    /* banner and zoom chip first: the labels are placed around them */
    bool banner = banner_update(lat, lon);
    if (sc.res && (fresh || banner != sc.banner_was)) show_result(sc.res, s);
    sc.banner_was = banner;

    float off_x = 0, off_y = 0;
    bool placed = sc.res && sc.res->status == MAPSVC_OK && sc.res->kx > 0;
    if (placed) {
        off_x = (float)((lon - sc.res->lon) * sc.res->kx / sc.res->mpp);
        off_y = (float)(-(lat - sc.res->lat) * sc.res->ky / sc.res->mpp);
    }
    if (want_new(now, off_x, off_y) && (!mapsvc_busy() || !sc.res || sc.res->level != view_level())) request(now);

    /* the car */
    car_t kind = !placed || !tk.have_pos ? CAR_NONE : !tk.fix ? CAR_LOST : tk.speed >= MOVING_KMH ? CAR_ARROW : CAR_DOT;
    int heading = ((int)lroundf(tk.heading) % 360 + 360) % 360;
    int x = UI_W / 2 + (int)lroundf(off_x) - CAR / 2, y = UI_BODY_H / 2 + (int)lroundf(off_y) - CAR / 2;
    if (x < 0 || y < 0 || x > UI_W - CAR || y > UI_BODY_H - CAR) kind = CAR_NONE;
    if (kind == CAR_NONE) lv_obj_add_flag(sc.car, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_remove_flag(sc.car, LV_OBJ_FLAG_HIDDEN);
    if (lv_obj_get_x(sc.car) != x || lv_obj_get_y(sc.car) != y) lv_obj_set_pos(sc.car, x, y);
    int turn = abs(heading - sc.car_heading);
    if (kind != sc.car_kind || (kind == CAR_ARROW && LV_MIN(turn, 360 - turn) >= 3)) {
        sc.car_kind = kind;
        sc.car_heading = heading;
        lv_obj_invalidate(sc.car);
    }
}

void ui_map_info(ui_map_info_t *o)
{
    memset(o, 0, sizeof(*o));
    o->level = view_level();
    o->manual = tk.manual;
    o->n_trail = tk.trail_n;
    o->n_pins = tk.n_pins;
    o->fix = tk.fix;
    o->renders = tk.renders;
    if (!sc.root) return;
    o->status = sc.res ? (int)sc.res->status : 0;
    o->mpp = sc.res ? sc.res->mpp : 0;
    o->render_us = sc.res ? sc.res->render_us : 0;
    o->banner = !lv_obj_has_flag(sc.banner, LV_OBJ_FLAG_HIDDEN);
    o->message = !lv_obj_has_flag(sc.msg, LV_OBJ_FLAG_HIDDEN);
    if (o->banner) snprintf(o->banner_text, sizeof(o->banner_text), "%s", lv_label_get_text(sc.banner_text));
    if (o->message) snprintf(o->message_title, sizeof(o->message_title), "%s", lv_label_get_text(sc.msg_title));
    o->n_labels = sc.n_labels;
    for (int i = 0; i < sc.n_labels && i < 12; i++) snprintf(o->labels[i], sizeof(o->labels[i]), "%s", sc.labels[i]);
}
