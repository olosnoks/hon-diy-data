#include "ui_shapes.h"
#include "ui_theme.h"

typedef struct { ui_shape_t kind; lv_color_t colour, ink; } shape_t;

static void rect(lv_layer_t *layer, int x1, int y1, int x2, int y2, lv_color_t c, int radius, int border)
{
    lv_draw_rect_dsc_t d;
    lv_draw_rect_dsc_init(&d);
    d.radius = radius;
    if (border) {
        d.bg_opa = LV_OPA_TRANSP;
        d.border_color = c; d.border_width = border; d.border_opa = LV_OPA_COVER;
    } else {
        d.bg_color = c; d.bg_opa = LV_OPA_COVER;
    }
    lv_area_t a = {x1, y1, x2, y2};
    lv_draw_rect(layer, &d, &a);
}

static void tri(lv_layer_t *layer, int ax, int ay, int bx, int by, int cx, int cy, lv_color_t c)
{
    lv_draw_triangle_dsc_t d;
    lv_draw_triangle_dsc_init(&d);
    d.bg_color = c; d.bg_opa = LV_OPA_COVER;
    d.p[0].x = ax; d.p[0].y = ay; d.p[1].x = bx; d.p[1].y = by; d.p[2].x = cx; d.p[2].y = cy;
    lv_draw_triangle(layer, &d);
}

static void line(lv_layer_t *layer, int ax, int ay, int bx, int by, lv_color_t c, int w, int dash)
{
    lv_draw_line_dsc_t d;
    lv_draw_line_dsc_init(&d);
    d.color = c; d.width = w; d.opa = LV_OPA_COVER;
    d.dash_width = dash; d.dash_gap = dash ? dash - 1 : 0;
    d.p1.x = ax; d.p1.y = ay; d.p2.x = bx; d.p2.y = by;
    lv_draw_line(layer, &d);
}

static void draw_cb(lv_event_t *e)
{
    lv_obj_t *obj = lv_event_get_target(e);
    shape_t *s = lv_obj_get_user_data(obj);
    lv_layer_t *layer = lv_event_get_layer(e);
    lv_area_t a;
    lv_obj_get_coords(obj, &a);
    int x1 = a.x1, y1 = a.y1, x2 = a.x2, y2 = a.y2;
    int n = x2 - x1 + 1;
    lv_color_t c = s->colour;

    switch (s->kind) {
    case UI_SHAPE_DOT:
        rect(layer, x1, y1, x2, y2, c, LV_RADIUS_CIRCLE, 0);
        break;
    case UI_SHAPE_SQUARE:
        rect(layer, x1 + 1, y1 + 1, x2 - 1, y2 - 1, c, 0, LV_MAX(3, n / 6));
        break;
    case UI_SHAPE_SQUARE_FILLED:
        rect(layer, x1 + 1, y1 + 1, x2 - 1, y2 - 1, c, 0, 0);
        break;
    case UI_SHAPE_RING:
        rect(layer, x1, y1, x2, y2, c, LV_RADIUS_CIRCLE, LV_MAX(4, n / 4));
        break;
    case UI_SHAPE_HALF: {
        rect(layer, x1, y1, x2, y2, c, LV_RADIUS_CIRCLE, 2);
        lv_draw_arc_dsc_t d;
        lv_draw_arc_dsc_init(&d);
        d.color = c; d.opa = LV_OPA_COVER;
        d.center.x = x1 + n / 2; d.center.y = y1 + n / 2;
        d.radius = (uint16_t)(n / 2); d.width = n / 2;
        d.start_angle = 90; d.end_angle = 270;
        lv_draw_arc(layer, &d);
        break;
    }
    case UI_SHAPE_CROSS: {
        int w = LV_MAX(3, n / 5), m = w / 2 + 1;
        line(layer, x1 + m, y1 + m, x2 - m, y2 - m, c, w, 0);
        line(layer, x1 + m, y2 - m, x2 - m, y1 + m, c, w, 0);
        break;
    }
    case UI_SHAPE_TRIANGLE:
        tri(layer, x1 + n / 2, y1, x1 - 1, y2, x2 + 1, y2, c);
        break;
    case UI_SHAPE_OCTAGON: {
        int k = (n * 293 + 500) / 1000;                    /* corner cut: n * (1 - 1/sqrt 2) */
        rect(layer, x1, y1 + k, x2, y2 - k, c, 0, 0);
        rect(layer, x1 + k, y1, x2 - k, y1 + k - 1, c, 0, 0);
        rect(layer, x1 + k, y2 - k + 1, x2 - k, y2, c, 0, 0);
        tri(layer, x1, y1 + k, x1 + k, y1, x1 + k, y1 + k, c);
        tri(layer, x2 + 1, y1 + k, x2 + 1 - k, y1, x2 + 1 - k, y1 + k, c);
        tri(layer, x1, y2 + 1 - k, x1 + k, y2 + 1, x1 + k, y2 + 1 - k, c);
        tri(layer, x2 + 1, y2 + 1 - k, x2 + 1 - k, y2 + 1, x2 + 1 - k, y2 + 1 - k, c);
        break;
    }
    case UI_SHAPE_VERIFIED: {
        int w = LV_MAX(2, n / 7);
        rect(layer, x1, y1, x2, y2, c, 0, 0);
        line(layer, x1 + n * 22 / 100, y1 + n * 52 / 100, x1 + n * 42 / 100, y1 + n * 72 / 100, s->ink, w, 0);
        line(layer, x1 + n * 42 / 100, y1 + n * 72 / 100, x1 + n * 80 / 100, y1 + n * 28 / 100, s->ink, w, 0);
        break;
    }
    case UI_SHAPE_UNVERIFIED: {
        /* dashed box: 4 px dashes, 3 px gaps, corners always drawn */
        line(layer, x1, y1 + 1, x2 + 1, y1 + 1, c, 2, 4);
        line(layer, x1, y2, x2 + 1, y2, c, 2, 4);
        line(layer, x1 + 1, y1, x1 + 1, y2 + 1, c, 2, 4);
        line(layer, x2, y1, x2, y2 + 1, c, 2, 4);
        lv_draw_label_dsc_t d;
        lv_draw_label_dsc_init(&d);
        d.font = &hh_font_16; d.color = c; d.text = "?"; d.align = LV_TEXT_ALIGN_CENTER;
        int top = y1 + (n - lv_font_get_line_height(&hh_font_16)) / 2;
        lv_area_t t = {x1 + 1, top, x2 + 1, top + lv_font_get_line_height(&hh_font_16)};
        lv_draw_label(layer, &d, &t);
        break;
    }
    default:
        break;
    }
}

static void delete_cb(lv_event_t *e)
{
    lv_free(lv_obj_get_user_data(lv_event_get_target(e)));
}

lv_obj_t *ui_shape_create(lv_obj_t *parent, int size)
{
    lv_obj_t *obj = lv_obj_create(parent);
    shape_t *s = lv_malloc(sizeof(*s));
    LV_ASSERT_MALLOC(s);
    s->kind = UI_SHAPE_NONE;
    s->colour = s->ink = lv_color_black();
    lv_obj_set_user_data(obj, s);
    lv_obj_set_size(obj, size, size);
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(obj, draw_cb, LV_EVENT_DRAW_MAIN, NULL);
    lv_obj_add_event_cb(obj, delete_cb, LV_EVENT_DELETE, NULL);
    return obj;
}

void ui_shape_set(lv_obj_t *obj, ui_shape_t kind, lv_color_t colour, lv_color_t ink)
{
    shape_t *s = lv_obj_get_user_data(obj);
    if (s->kind == kind && lv_color_eq(s->colour, colour) && lv_color_eq(s->ink, ink)) return;
    s->kind = kind; s->colour = colour; s->ink = ink;
    lv_obj_invalidate(obj);
}
