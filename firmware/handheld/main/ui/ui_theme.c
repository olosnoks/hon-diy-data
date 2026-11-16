#include "ui_theme.h"

/* HANDOFF.md section 2, unchanged. on_warn / on_alarm are the text colours the
 * handoff lists against each fill. */
const ui_tokens_t UI_DAYLIGHT = {
    .bg = 0xFFFFFF, .panel = 0xFFFFFF, .sunk = 0xE9ECEF,
    .fg = 0x000000, .fg2 = 0x1F2428,
    .rule = 0x000000, .rule2 = 0x5A6570,
    .accent = 0x0A3FA8, .on_accent = 0xFFFFFF,
    .warn = 0xFFD000, .on_warn = 0x000000,
    .alarm = 0xB00020, .on_alarm = 0xFFFFFF,
    .ok = 0x00572D, .unver = 0x7A3300,
};

const ui_tokens_t UI_NIGHT = {
    .bg = 0x000000, .panel = 0x0B0E11, .sunk = 0x1A1F25,
    .fg = 0xFFFFFF, .fg2 = 0xD6DCE2,
    .rule = 0x8B96A1, .rule2 = 0x8B96A1,
    .accent = 0x6CB6FF, .on_accent = 0x000000,
    .warn = 0xFFC400, .on_warn = 0x000000,
    .alarm = 0xFF7A66, .on_alarm = 0x000000,
    .ok = 0x4ADE80, .unver = 0xFFB454,
};

static bool night;

void ui_theme_set(bool n) { night = n; }
bool ui_theme_night(void) { return night; }
const ui_tokens_t *ui_tokens(void) { return night ? &UI_NIGHT : &UI_DAYLIGHT; }

int ui_theme_pairs(const ui_tokens_t *t, ui_pair_t *out, int max)
{
    const ui_pair_t pairs[] = {
        {"fg on bg", t->fg, t->bg},           {"fg on panel", t->fg, t->panel},
        {"fg on sunk", t->fg, t->sunk},       {"fg2 on bg", t->fg2, t->bg},
        {"fg2 on panel", t->fg2, t->panel},   {"fg2 on sunk", t->fg2, t->sunk},
        {"on_accent on accent", t->on_accent, t->accent},
        {"on_warn on warn", t->on_warn, t->warn},
        {"on_alarm on alarm", t->on_alarm, t->alarm},
        {"bg on fg (inverted)", t->bg, t->fg},
        {"unver on panel", t->unver, t->panel}, {"unver on sunk", t->unver, t->sunk},
        {"ok on bg", t->ok, t->bg},           {"alarm on bg", t->alarm, t->bg},
    };
    int n = (int)(sizeof(pairs) / sizeof(pairs[0]));
    if (n > max) n = max;
    for (int i = 0; i < n; i++) out[i] = pairs[i];
    return n;
}
