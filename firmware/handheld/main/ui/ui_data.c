#include <math.h>
#include <stdio.h>
#include <string.h>

#include "ui_data.h"

const hh_channel_info_t *ui_channels(uint16_t *n)
{
    uint16_t count = 0;
    const hh_channel_info_t *ch = hh_channels(&count);
    if (!ch) count = 0;
    if (count > HH_MAX_CH) count = HH_MAX_CH;
    if (n) *n = count;
    return ch;
}

static bool same_name(const char *a, const char *b, size_t blen)
{
    for (size_t i = 0; i < blen; i++) {
        char x = a[i], y = b[i];
        if (x >= 'A' && x <= 'Z') x = (char)(x + 32);
        if (y >= 'A' && y <= 'Z') y = (char)(y + 32);
        if (x != y || !x) return false;
    }
    return a[blen] == 0;
}

int ui_find_channel(const char *candidates)
{
    uint16_t n;
    const hh_channel_info_t *ch = ui_channels(&n);
    for (const char *p = candidates; *p;) {
        const char *end = strchr(p, '|');
        size_t len = end ? (size_t)(end - p) : strlen(p);
        for (uint16_t i = 0; i < n; i++) {
            char name[sizeof(ch[i].name) + 1];
            memcpy(name, ch[i].name, sizeof(ch[i].name));
            name[sizeof(ch[i].name)] = 0;
            if (same_name(name, p, len)) return i;
        }
        p += len + (end ? 1 : 0);
    }
    return -1;
}

bool ui_is_flag(const hh_channel_info_t *c)
{
    /* hh-core says so (kind 1); a 0..1 whole-number channel is shown the same way */
    return c->kind == 1 || (c->lo == 0.0f && c->hi == 1.0f && c->decimals == 0);
}

void ui_fmt_float(char *buf, size_t n, float v, int decimals)
{
    if (!isfinite(v)) { snprintf(buf, n, "---"); return; }
    if (decimals < 0) decimals = 0;
    if (decimals > 3) decimals = 3;
    snprintf(buf, n, "%.*f", decimals, (double)v);
    /* no "-0" or "-0.0" */
    if (buf[0] == '-' && strspn(buf + 1, "0.") == strlen(buf + 1)) memmove(buf, buf + 1, strlen(buf));
}

void ui_fmt_value(char *buf, size_t n, const hh_channel_info_t *c, float v)
{
    if (ui_is_flag(c)) snprintf(buf, n, "%s", v >= 0.5f ? "ON" : "OFF");
    else ui_fmt_float(buf, n, v, c->decimals);
}

void ui_fmt_limit(char *buf, size_t n, const hh_channel_info_t *c, float v, int level)
{
    float hi = level >= 2 ? c->alarm_hi : c->warn_hi;
    float lo = level >= 2 ? c->alarm_lo : c->warn_lo;
    char num[16];
    buf[0] = 0;
    if (ui_is_flag(c)) return;                 /* "ON" says it all */
    if (isfinite(hi) && v >= hi) {
        ui_fmt_float(num, sizeof(num), hi, c->decimals);
        snprintf(buf, n, "> %s", num);
    } else if (isfinite(lo) && v <= lo) {
        ui_fmt_float(num, sizeof(num), lo, c->decimals);
        snprintf(buf, n, "< %s", num);
    }
}

void ui_upper(char *dst, size_t n, const char *src)
{
    size_t i = 0;
    for (; src[i] && i + 1 < n; i++) dst[i] = (src[i] >= 'a' && src[i] <= 'z') ? (char)(src[i] - 32) : src[i];
    dst[i] = 0;
}

void ui_set_text(lv_obj_t *label, const char *text)
{
    if (strcmp(lv_label_get_text(label), text) != 0) lv_label_set_text(label, text);
}

lv_obj_t *ui_box(lv_obj_t *parent, int x, int y, int w, int h)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_size(o, w, h);
    return o;
}

lv_obj_t *ui_label(lv_obj_t *parent, const lv_font_t *font, int x, int y, int w, lv_text_align_t align)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_align(l, align, 0);
    lv_obj_set_pos(l, x, y);
    if (w > 0) {
        /* one line, fixed width; too long ends in "..." instead of being cut */
        lv_label_set_long_mode(l, LV_LABEL_LONG_DOT);
        lv_obj_set_size(l, w, lv_font_get_line_height(font));
    }
    lv_label_set_text(l, "");
    return l;
}

void ui_fill(lv_obj_t *obj, lv_color_t c)
{
    lv_obj_set_style_bg_color(obj, c, 0);
    lv_obj_set_style_bg_opa(obj, LV_OPA_COVER, 0);
}

void ui_border(lv_obj_t *obj, lv_color_t c, int width, lv_border_side_t sides)
{
    lv_obj_set_style_border_color(obj, c, 0);
    lv_obj_set_style_border_width(obj, width, 0);
    lv_obj_set_style_border_side(obj, sides, 0);
    lv_obj_set_style_border_opa(obj, LV_OPA_COVER, 0);
}

void ui_ink(lv_obj_t *label, lv_color_t c)
{
    lv_obj_set_style_text_color(label, c, 0);
}
