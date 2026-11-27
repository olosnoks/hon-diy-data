#include "ui_theme.h"

/* web/src/ui/theme.ts, unchanged where it has the token. rule and rule2 are
 * both Dracula's comment blue #6272A4 (borders, dividers, bar outlines, map
 * railways: never text); its "line" #44475A is too faint on a TN panel in
 * sun. water is the Map screen's own: a muted blue between comment and cyan,
 * 3:1 against bg with the fg roads still 3:1 on it. on_alarm is the CYD's
 * own, on purpose: #191A21 (Darker's bg) in both themes, 5.5:1 on the red
 * fill for a TN panel in sun, where the desktop/web theme's bg #282A36 is
 * only 4.53:1. */
const ui_tokens_t UI_DRACULA = {
    .bg = 0x282A36, .panel = 0x21222C, .sunk = 0x343746,
    .fg = 0xF8F8F2, .fg2 = 0xC5C8D6,
    .rule = 0x6272A4, .rule2 = 0x6272A4,
    .accent = 0xBD93F9, .on_accent = 0x282A36,
    .warn = 0xF1FA8C, .on_warn = 0x282A36,
    .alarm = 0xFF5555, .on_alarm = 0x191A21,
    .ok = 0x50FA7B, .unver = 0xFFB86C,
    .water = 0x55809F,
};

const ui_tokens_t UI_DARKER = {
    .bg = 0x191A21, .panel = 0x121318, .sunk = 0x21222C,
    .fg = 0xF8F8F2, .fg2 = 0xC5C8D6,
    .rule = 0x6272A4, .rule2 = 0x6272A4,
    .accent = 0xBD93F9, .on_accent = 0x191A21,
    .warn = 0xF1FA8C, .on_warn = 0x191A21,
    .alarm = 0xFF5555, .on_alarm = 0x191A21,
    .ok = 0x50FA7B, .unver = 0xFFB86C,
    .water = 0x55809F,
};

static bool darker;

void ui_theme_set(bool d) { darker = d; }
bool ui_theme_darker(void) { return darker; }
const ui_tokens_t *ui_tokens(void) { return darker ? &UI_DARKER : &UI_DRACULA; }

int ui_theme_pairs(const ui_tokens_t *t, ui_pair_t *out, int max)
{
    const ui_pair_t pairs[] = {
        {"fg on bg", t->fg, t->bg, 7},           {"fg on panel", t->fg, t->panel, 7},
        {"fg on sunk", t->fg, t->sunk, 7},       {"fg2 on bg", t->fg2, t->bg, 7},
        {"fg2 on panel", t->fg2, t->panel, 7},   {"fg2 on sunk", t->fg2, t->sunk, 7},
        {"on_accent on accent", t->on_accent, t->accent, 4.5f},
        {"on_warn on warn", t->on_warn, t->warn, 7},
        {"on_alarm on alarm", t->on_alarm, t->alarm, 5.5f},
        {"panel on ok (marker)", t->panel, t->ok, 7}, {"panel on unver (marker)", t->panel, t->unver, 7},
        {"unver on panel", t->unver, t->panel, 4.5f}, {"unver on sunk", t->unver, t->sunk, 4.5f},
        {"ok on bg", t->ok, t->bg, 4.5f},        {"alarm on bg", t->alarm, t->bg, 4.5f},
    };
    int n = (int)(sizeof(pairs) / sizeof(pairs[0]));
    if (n > max) n = max;
    for (int i = 0; i < n; i++) out[i] = pairs[i];
    return n;
}

int ui_map_pairs(const ui_tokens_t *t, ui_pair_t *out, int max)
{
    const ui_pair_t pairs[] = {
        {"water on bg", t->water, t->bg, 3},     {"road (fg) on water", t->fg, t->water, 3},
        {"road (fg) on bg", t->fg, t->bg, 3},    {"rail (rule2) on bg", t->rule2, t->bg, 3},
        {"trail (accent) on bg", t->accent, t->bg, 3}, {"stall pin (alarm) on bg", t->alarm, t->bg, 3},
        {"border (rule) on bg", t->rule, t->bg, 3}, {"border (rule) on panel", t->rule, t->panel, 3},
    };
    int n = (int)(sizeof(pairs) / sizeof(pairs[0]));
    if (n > max) n = max;
    for (int i = 0; i < n; i++) out[i] = pairs[i];
    return n;
}
