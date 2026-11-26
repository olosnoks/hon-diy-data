#include <math.h>
#include <stdio.h>
#include <string.h>

#include "ui_units.h"

static int sys = UI_UNITS_US;

void ui_units_set(int system) { sys = system == UI_UNITS_METRIC ? UI_UNITS_METRIC : UI_UNITS_US; }
int ui_units_get(void) { return sys; }

/* base unit spellings (lower case, no spaces) -> the US unit. Factors as in
 * hondalog/units.py. */
static const struct {
    const char *names[4];
    float scale, offset;
    const char *us;
} TABLE[] = {
    {{"\xC2\xB0""c", "c", "degc", NULL}, 1.8f, 32.0f, "\xC2\xB0""F"},
    {{"km/h", "kph", "kmh", NULL}, 0.6213712f, 0.0f, "mph"},
    {{"km", NULL}, 0.6213712f, 0.0f, "mi"},
    {{"m", NULL}, 3.2808399f, 0.0f, "ft"},
    {{"kpa", NULL}, 0.2952998f, 0.0f, "inHg"},
    {{"l/h", NULL}, 0.2641721f, 0.0f, "gal/h"},
};
#define N_TABLE (sizeof(TABLE) / sizeof(TABLE[0]))

/* lower case ASCII, spaces dropped; false if it does not fit */
static bool canon(const char *in, size_t len, char *out, size_t cap)
{
    size_t w = 0;
    for (size_t i = 0; i < len && in[i]; i++) {
        char c = in[i];
        if (c == ' ' || c == '\t') continue;
        if (c >= 'A' && c <= 'Z') c = (char)(c + 32);
        if (w + 1 >= cap) return false;
        out[w++] = c;
    }
    out[w] = 0;
    return true;
}

static int lookup(const char *name)
{
    for (size_t i = 0; i < N_TABLE; i++)
        for (int k = 0; k < 4 && TABLE[i].names[k]; k++)
            if (strcmp(name, TABLE[i].names[k]) == 0) return (int)i;
    return -1;
}

static void identity(const char *base_unit, ui_conv_t *out)
{
    out->scale = 1.0f;
    out->offset = 0.0f;
    out->extra_decimals = 0;
    snprintf(out->unit, sizeof(out->unit), "%s", base_unit ? base_unit : "");
}

bool ui_units_conv(const char *base_unit, ui_conv_t *out)
{
    identity(base_unit, out);
    if (sys == UI_UNITS_METRIC || !base_unit || !base_unit[0]) return false;
    char name[16];
    size_t len = strlen(base_unit);
    bool rate = false;
    if (!canon(base_unit, len, name, sizeof(name))) return false;
    int i = lookup(name);
    if (i < 0) {
        /* "°C/s": a rate of change converts as a span */
        size_t n = strlen(name);
        if (n > 2 && strcmp(name + n - 2, "/s") == 0) {
            name[n - 2] = 0;
            i = lookup(name);
            rate = true;
        }
        if (i < 0) return false;
    }
    char unit[sizeof(out->unit)];
    int w = snprintf(unit, sizeof(unit), "%s%s", TABLE[i].us, rate ? "/s" : "");
    if (w < 0 || (size_t)w >= sizeof(unit)) return false;     /* would not fit: leave it alone */
    out->scale = TABLE[i].scale;
    out->offset = rate ? 0.0f : TABLE[i].offset;
    /* keep about the same resolution (hondalog/ui/units.py): inHg and gal/h
     * need a decimal more than kPa and L/h; °F and mph none */
    long extra = lroundf(-log10f(fabsf(TABLE[i].scale)));
    out->extra_decimals = (int)(extra < -2 ? -2 : extra > 2 ? 2 : extra);
    memcpy(out->unit, unit, sizeof(unit));
    return true;
}

float ui_units_apply(const ui_conv_t *c, float v)
{
    if (!isfinite(v)) return v;
    return v * c->scale + c->offset;
}

void ui_units_channel(const hh_channel_info_t *base, hh_channel_info_t *shown, ui_conv_t *conv)
{
    *shown = *base;
    /* a flag is ON / OFF whatever its unit says */
    bool flag = base->kind == 1 || (base->lo == 0.0f && base->hi == 1.0f && base->decimals == 0);
    char unit[sizeof(base->units) + 1];
    memcpy(unit, base->units, sizeof(base->units));
    unit[sizeof(base->units)] = 0;
    if (flag || !ui_units_conv(unit, conv)) {
        identity(unit, conv);
        return;
    }
    memcpy(shown->units, conv->unit, sizeof(shown->units));
    shown->lo = ui_units_apply(conv, base->lo);
    shown->hi = ui_units_apply(conv, base->hi);
    if (shown->lo > shown->hi) { float t = shown->lo; shown->lo = shown->hi; shown->hi = t; }
    shown->warn_lo = ui_units_apply(conv, base->warn_lo);
    shown->warn_hi = ui_units_apply(conv, base->warn_hi);
    shown->alarm_lo = ui_units_apply(conv, base->alarm_lo);
    shown->alarm_hi = ui_units_apply(conv, base->alarm_hi);
    int d = (int)base->decimals + conv->extra_decimals;
    shown->decimals = (uint8_t)(d < 0 ? 0 : d > 3 ? 3 : d);
}

float ui_units_speed(float kmh)
{
    return sys == UI_UNITS_METRIC ? kmh : kmh * 0.6213712f;
}

const char *ui_units_speed_unit(void) { return sys == UI_UNITS_METRIC ? "km/h" : "mph"; }

void ui_units_distance(char *buf, size_t n, double km)
{
    if (!isfinite(km)) { snprintf(buf, n, "---"); return; }
    double v = sys == UI_UNITS_METRIC ? km : km * 0.6213712;
    if (v < 0 && v > -0.005) v = 0;
    snprintf(buf, n, "%.*f %s", fabs(v) < 100 ? 2 : 1, v, sys == UI_UNITS_METRIC ? "km" : "mi");
}

const ui_scale_step_t *ui_units_scale_steps(size_t *n)
{
#define MI 1609.344f
    static const ui_scale_step_t US[] = {
        {20 * MI, "20 mi"}, {10 * MI, "10 mi"}, {5 * MI, "5 mi"}, {2 * MI, "2 mi"}, {1 * MI, "1 mi"},
        {0.5f * MI, "0.5 mi"}, {0.25f * MI, "0.25 mi"}, {0.1f * MI, "0.1 mi"}, {250 * 0.3048f, "250 ft"},
    };
    static const ui_scale_step_t METRIC[] = {
        {20000, "20 km"}, {10000, "10 km"}, {5000, "5 km"}, {2000, "2 km"}, {1000, "1 km"},
        {500, "500 m"}, {200, "200 m"}, {100, "100 m"}, {50, "50 m"},
    };
#undef MI
    if (sys == UI_UNITS_METRIC) { *n = sizeof(METRIC) / sizeof(METRIC[0]); return METRIC; }
    *n = sizeof(US) / sizeof(US[0]);
    return US;
}
