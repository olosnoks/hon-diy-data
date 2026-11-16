#include <stdio.h>

#include "ui.h"
#include "ui_data.h"
#include "ui_perf.h"
#include "ui_theme.h"

static ui_perf_totals_t total;
static int64_t t_render;
static bool rendered;
static uint32_t waited_us;

static void render_cb(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_RENDER_START) {
        t_render = ui_port_micros();
        waited_us = 0;
        rendered = true;
    } else if (code == LV_EVENT_RENDER_READY && rendered) {
        /* the last flush happens inside the render; its wait for the swap is not work */
        uint32_t us = (uint32_t)(ui_port_micros() - t_render);
        us = us > waited_us ? us - waited_us : 0;
        rendered = false;
        total.frames++;
        total.render_us += us;
        if (us > total.render_us_max) total.render_us_max = us;
    }
}

void ui_perf_attach(lv_display_t *disp)
{
    lv_display_add_event_cb(disp, render_cb, LV_EVENT_RENDER_START, NULL);
    lv_display_add_event_cb(disp, render_cb, LV_EVENT_RENDER_READY, NULL);
}

void ui_perf_flushed(const lv_area_t *area)
{
    total.px += (uint32_t)(lv_area_get_width(area) * lv_area_get_height(area));
}

void ui_perf_waited(uint32_t us) { waited_us += us; }

const ui_perf_totals_t *ui_perf_totals(void) { return &total; }

#if HH_UI_PERF
static ui_perf_totals_t last;
static lv_obj_t *label;
static uint32_t t_last;

void ui_perf_create(void)
{
    label = lv_label_create(lv_layer_sys());
    lv_obj_set_style_text_font(label, &hh_font_16, 0);
    ui_fill(label, TK(fg));
    ui_ink(label, TK(bg));
    lv_obj_set_style_pad_hor(label, 4, 0);
    lv_obj_align(label, LV_ALIGN_BOTTOM_RIGHT, 0, 0);
    lv_label_set_text(label, "perf");
}

void ui_perf_tick(uint32_t now_ms)
{
    if (!label || now_ms - t_last < 1000) return;
    uint32_t ms = now_ms - t_last;
    t_last = now_ms;
    uint32_t frames = total.frames - last.frames, px = total.px - last.px, us = total.render_us - last.render_us;
    uint32_t worst = total.render_us_max;
    total.render_us_max = 0;
    last = total;
    int c0 = -1, c1 = -1;
    ui_port_cpu(&c0, &c1);
    char text[64];
    uint32_t mean = frames ? us / frames : 0;
    int n = snprintf(text, sizeof(text), "%ufps %u.%u/%u.%ums %ukpx",
                     (unsigned)((frames * 1000 + ms / 2) / ms),
                     (unsigned)(mean / 1000), (unsigned)(mean % 1000 / 100),
                     (unsigned)(worst / 1000), (unsigned)(worst % 1000 / 100),
                     (unsigned)(frames ? px / frames / 1000 : 0));
    if (c0 >= 0 && n > 0 && n < (int)sizeof(text)) snprintf(text + n, sizeof(text) - (size_t)n, " c0 %d c1 %d", c0, c1);
    lv_label_set_text(label, text);
}
#else
void ui_perf_create(void) {}
void ui_perf_tick(uint32_t now_ms) { (void)now_ms; }
#endif
