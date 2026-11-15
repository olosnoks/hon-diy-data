#include "ui_input.h"

ui_btn_event_t ui_btn_step(ui_btn_t *b, bool pressed, uint32_t now_ms)
{
    if (pressed != b->raw) {
        b->raw = pressed;
        b->t_raw = now_ms;
    }
    bool settled = (uint32_t)(now_ms - b->t_raw) >= UI_BTN_DEBOUNCE_MS;

    if (!b->down) {
        if (b->raw && settled) {
            b->down = true;
            b->spent = false;
            b->t_down = b->t_raw;
        }
        return UI_BTN_NONE;
    }

    uint32_t held = now_ms - b->t_down;
    if (!b->raw && settled) {
        b->down = false;
        if (b->spent) return UI_BTN_NONE;
        held = b->t_raw - b->t_down;
        return held >= UI_BTN_LONG_MS ? UI_BTN_LONG : UI_BTN_SHORT;
    }
    if (!b->spent && b->raw && held >= UI_BTN_VLONG_MS) {
        b->spent = true;
        return UI_BTN_VLONG;
    }
    return UI_BTN_NONE;
}

uint32_t ui_btn_held_ms(const ui_btn_t *b, uint32_t now_ms)
{
    return (b->down && !b->spent) ? now_ms - b->t_down : 0;
}
