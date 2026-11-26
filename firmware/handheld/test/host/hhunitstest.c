/* The handheld UI's display units (main/ui/ui_units.c): °C -> °F, km/h -> mph,
 * kPa -> inHg, L/h -> gal/h, km -> mi; limits and bar ranges with them; NaN,
 * unknown units and the metric setting left alone. Factors and decimals as the
 * laptop's hondalog/units.py and hondalog/ui/units.py. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "ui_units.h"

static int checks, failures;
#define CHECK(cond, ...) do { checks++; if (!(cond)) { failures++; printf("  FAIL %s:%d: ", __FILE__, __LINE__); \
    printf(__VA_ARGS__); printf("\n"); } } while (0)
#define NEAR(a, b, tol) (fabs((double)(a) - (double)(b)) <= (tol))

#define DEG "\xC2\xB0"

static hh_channel_info_t chan(const char *units, float lo, float hi, float wlo, float whi, float alo, float ahi, int dec)
{
    hh_channel_info_t c;
    memset(&c, 0, sizeof(c));
    snprintf(c.name, sizeof(c.name), "x");
    memcpy(c.units, units, strlen(units) < sizeof(c.units) ? strlen(units) : sizeof(c.units));
    c.lo = lo; c.hi = hi; c.warn_lo = wlo; c.warn_hi = whi; c.alarm_lo = alo; c.alarm_hi = ahi;
    c.decimals = (uint8_t)dec;
    return c;
}

/* where a value sits on the bar: the same before and after converting */
static float bar(const hh_channel_info_t *c, float v) { return (v - c->lo) / (c->hi - c->lo); }

static void test_conv(void)
{
    ui_conv_t k;
    ui_units_set(UI_UNITS_US);
    CHECK(ui_units_get() == UI_UNITS_US, "US set");

    CHECK(ui_units_conv(DEG "C", &k) && !strcmp(k.unit, DEG "F") && k.extra_decimals == 0, "°C -> °F, %s", k.unit);
    CHECK(NEAR(ui_units_apply(&k, 100), 212, 1e-3) && NEAR(ui_units_apply(&k, -40), -40, 1e-3)
          && NEAR(ui_units_apply(&k, 0), 32, 1e-4) && NEAR(ui_units_apply(&k, -10), 14, 1e-4), "°F values");
    CHECK(isnan(ui_units_apply(&k, NAN)), "NaN stays NaN");
    CHECK(isinf(ui_units_apply(&k, INFINITY)) && ui_units_apply(&k, -INFINITY) < 0, "infinities pass");

    CHECK(ui_units_conv("km/h", &k) && !strcmp(k.unit, "mph") && k.extra_decimals == 0, "km/h -> mph");
    CHECK(NEAR(ui_units_apply(&k, 100), 62.137, 1e-3), "100 km/h = 62.137 mph");
    CHECK(ui_units_conv("kPa", &k) && !strcmp(k.unit, "inHg") && k.extra_decimals == 1, "kPa -> inHg, +1 decimal");
    CHECK(NEAR(ui_units_apply(&k, 101.325f), 29.921, 2e-3), "1 atm = 29.92 inHg");
    CHECK(ui_units_conv("L/h", &k) && !strcmp(k.unit, "gal/h") && k.extra_decimals == 1, "L/h -> gal/h, +1 decimal");
    CHECK(NEAR(ui_units_apply(&k, 10), 2.6417, 1e-3), "10 L/h = 2.64 gal/h");
    CHECK(ui_units_conv("km", &k) && !strcmp(k.unit, "mi"), "km -> mi");
    CHECK(ui_units_conv("m", &k) && !strcmp(k.unit, "ft") && NEAR(ui_units_apply(&k, 100), 328.084, 1e-2), "m -> ft");

    /* spellings the laptop takes too */
    CHECK(ui_units_conv("kph", &k) && !strcmp(k.unit, "mph"), "kph");
    CHECK(ui_units_conv("KPA", &k) && !strcmp(k.unit, "inHg"), "KPA");
    CHECK(ui_units_conv("deg C", &k) && !strcmp(k.unit, DEG "F"), "deg C");
    CHECK(ui_units_conv("l/h", &k) && !strcmp(k.unit, "gal/h"), "l/h");

    /* a rate converts as a span: 1 °C/s is 1.8 °F/s, no +32 */
    CHECK(ui_units_conv(DEG "C/s", &k) && !strcmp(k.unit, DEG "F/s") && k.offset == 0.0f, "°C/s -> °F/s");
    CHECK(NEAR(ui_units_apply(&k, 1), 1.8, 1e-5) && NEAR(ui_units_apply(&k, -2), -3.6, 1e-5), "°F/s values");

    /* shown as they are */
    static const char *const same[] = {"V", "%", "ms", "rpm", "rpm/s", "AFR", "A", DEG, DEG " BTDC", "", "lambda", "Hz"};
    for (size_t i = 0; i < sizeof(same) / sizeof(same[0]); i++) {
        CHECK(!ui_units_conv(same[i], &k) && k.scale == 1.0f && k.offset == 0.0f && !strcmp(k.unit, same[i])
              && k.extra_decimals == 0, "%s is left alone", same[i]);
        CHECK(ui_units_apply(&k, 12.5f) == 12.5f, "%s value unchanged", same[i]);
    }
    CHECK(!ui_units_conv(NULL, &k) && k.unit[0] == 0, "NULL unit");

    /* metric: the map's units, nothing converted */
    ui_units_set(UI_UNITS_METRIC);
    CHECK(ui_units_get() == UI_UNITS_METRIC, "metric set");
    static const char *const base[] = {DEG "C", "km/h", "kPa", "L/h", "km", DEG "C/s"};
    for (size_t i = 0; i < sizeof(base) / sizeof(base[0]); i++)
        CHECK(!ui_units_conv(base[i], &k) && !strcmp(k.unit, base[i]) && ui_units_apply(&k, 88) == 88.0f,
              "metric keeps %s", base[i]);
    ui_units_set(7);
    CHECK(ui_units_get() == UI_UNITS_US, "anything but metric is US");
}

static void test_channel(void)
{
    hh_channel_info_t s;
    ui_conv_t k;
    ui_units_set(UI_UNITS_US);

    /* coolant: range, warn and alarm limits in °F; NaN limits stay NaN */
    hh_channel_info_t ect = chan(DEG "C", -40, 130, NAN, 100, NAN, 108, 0);
    ui_units_channel(&ect, &s, &k);
    CHECK(!strcmp(s.units, DEG "F") && s.decimals == 0, "ect in °F, 0 decimals (%s, %d)", s.units, s.decimals);
    CHECK(NEAR(s.lo, -40, 1e-3) && NEAR(s.hi, 266, 1e-3), "ect range %g..%g", s.lo, s.hi);
    CHECK(NEAR(s.warn_hi, 212, 1e-3) && NEAR(s.alarm_hi, 226.4, 1e-3), "ect limits %g %g", s.warn_hi, s.alarm_hi);
    CHECK(isnan(s.warn_lo) && isnan(s.alarm_lo), "no low limit stays no low limit");
    CHECK(!strcmp(s.name, "x") && s.verified == ect.verified && s.kind == ect.kind, "the rest is copied");
    /* the bar: same fill fraction, the alarm limit at the same place */
    for (float v = -40; v <= 130; v += 17) {
        float f0 = bar(&ect, v), f1 = bar(&s, ui_units_apply(&k, v));
        CHECK(NEAR(f0, f1, 1e-5), "bar at %g °C: %g vs %g", v, f0, f1);
    }
    /* a value past the alarm limit in °C is past it in °F */
    CHECK(ui_units_apply(&k, 109) >= s.alarm_hi && ui_units_apply(&k, 107) < s.alarm_hi, "alarm limit compares the same");

    /* MAP 0..110 kPa, 0 decimals -> inHg with 1 */
    hh_channel_info_t map = chan("kPa", 0, 110, NAN, NAN, NAN, NAN, 0);
    ui_units_channel(&map, &s, &k);
    CHECK(!strcmp(s.units, "inHg") && s.decimals == 1 && NEAR(s.hi, 32.48, 1e-2), "MAP inHg %s %d %g", s.units, s.decimals, s.hi);

    /* fuel flow 2 decimals -> 3; VSS 0 -> 0 */
    hh_channel_info_t ff = chan("L/h", 0, 40, NAN, NAN, NAN, NAN, 2);
    ui_units_channel(&ff, &s, &k);
    CHECK(!strcmp(s.units, "gal/h") && s.decimals == 3, "gal/h %d decimals", s.decimals);
    hh_channel_info_t vss = chan("km/h", 0, 200, NAN, NAN, NAN, NAN, 0);
    ui_units_channel(&vss, &s, &k);
    CHECK(!strcmp(s.units, "mph") && s.decimals == 0 && NEAR(s.hi, 124.27, 1e-2), "mph range %g", s.hi);
    /* decimals never go past 3 or below 0 */
    hh_channel_info_t fine = chan("L/h", 0, 1, NAN, NAN, NAN, NAN, 3);
    ui_units_channel(&fine, &s, &k);
    CHECK(s.decimals == 3, "decimals capped at 3");
    hh_channel_info_t len = chan("m", 0, 10, NAN, NAN, NAN, NAN, 0);
    ui_units_channel(&len, &s, &k);
    CHECK(s.decimals == 0 && !strcmp(s.units, "ft"), "ft: decimals not below 0");

    /* a negative range and limits (a temperature low alarm) */
    hh_channel_info_t iat = chan(DEG "C", -40, 100, -20, 45, -30, NAN, 1);
    ui_units_channel(&iat, &s, &k);
    CHECK(NEAR(s.warn_lo, -4, 1e-3) && NEAR(s.alarm_lo, -22, 1e-3) && NEAR(s.warn_hi, 113, 1e-3) && isnan(s.alarm_hi)
          && s.decimals == 1, "iat limits %g %g %g", s.warn_lo, s.alarm_lo, s.warn_hi);

    /* unknown units and flags are copied as they are */
    hh_channel_info_t batt = chan("V", 8, 16, 12.8f, NAN, 11.5f, 15.2f, 1);
    ui_units_channel(&batt, &s, &k);
    CHECK(!memcmp(&s, &batt, sizeof(s)) && k.scale == 1.0f, "volts untouched");
    hh_channel_info_t flag = chan(DEG "C", 0, 1, NAN, NAN, NAN, 0.5f, 0);
    ui_units_channel(&flag, &s, &k);
    CHECK(!memcmp(&s, &flag, sizeof(s)) && k.scale == 1.0f && k.offset == 0.0f, "a 0/1 flag is never converted");
    hh_channel_info_t kind = chan("kPa", 0, 200, NAN, NAN, NAN, NAN, 0);
    kind.kind = 1;
    ui_units_channel(&kind, &s, &k);
    CHECK(!strcmp(s.units, "kPa"), "kind 1 is a flag");
    /* a units field with no terminator (8 bytes) */
    hh_channel_info_t full = chan("abcdefgh", 0, 1, NAN, NAN, NAN, NAN, 1);
    ui_units_channel(&full, &s, &k);
    CHECK(!memcmp(s.units, "abcdefgh", 8), "8-byte unit copied");

    /* metric: every channel exactly as the map has it */
    ui_units_set(UI_UNITS_METRIC);
    const hh_channel_info_t *all[] = {&ect, &map, &ff, &vss, &iat};
    for (size_t i = 0; i < sizeof(all) / sizeof(all[0]); i++) {
        ui_units_channel(all[i], &s, &k);
        CHECK(!memcmp(&s, all[i], sizeof(s)) && k.scale == 1.0f && k.offset == 0.0f, "metric channel %zu unchanged", i);
    }
}

static void test_gps(void)
{
    char b[24];
    size_t n;
    ui_units_set(UI_UNITS_US);
    CHECK(!strcmp(ui_units_speed_unit(), "mph") && NEAR(ui_units_speed(113), 70.21, 1e-2), "GPS speed in mph");
    CHECK(isnan(ui_units_speed(NAN)), "NaN speed");
    ui_units_distance(b, sizeof(b), 10.0);   CHECK(!strcmp(b, "6.21 mi"), "got %s", b);
    ui_units_distance(b, sizeof(b), 200.0);  CHECK(!strcmp(b, "124.3 mi"), "got %s", b);
    ui_units_distance(b, sizeof(b), 0.0);    CHECK(!strcmp(b, "0.00 mi"), "got %s", b);
    ui_units_distance(b, sizeof(b), -0.001); CHECK(!strcmp(b, "0.00 mi"), "no -0.00: %s", b);
    ui_units_distance(b, sizeof(b), NAN);    CHECK(!strcmp(b, "---"), "got %s", b);
    const ui_scale_step_t *st = ui_units_scale_steps(&n);
    CHECK(n >= 5 && !strcmp(st[0].text, "20 mi") && !strcmp(st[n - 1].text, "250 ft"), "scale steps in mi, then ft");
    for (size_t i = 1; i < n; i++) CHECK(st[i].m < st[i - 1].m, "steps get shorter");
    CHECK(NEAR(st[4].m, 1609.344, 1e-2), "1 mi = 1609 m");

    ui_units_set(UI_UNITS_METRIC);
    CHECK(!strcmp(ui_units_speed_unit(), "km/h") && ui_units_speed(113) == 113.0f, "metric speed");
    ui_units_distance(b, sizeof(b), 10.0);   CHECK(!strcmp(b, "10.00 km"), "got %s", b);
    ui_units_distance(b, sizeof(b), 123.45); CHECK(!strcmp(b, "123.5 km") || !strcmp(b, "123.4 km"), "got %s", b);
    st = ui_units_scale_steps(&n);
    CHECK(!strcmp(st[0].text, "20 km") && st[0].m == 20000 && !strcmp(st[n - 1].text, "50 m"), "metric steps");
    for (size_t i = 1; i < n; i++) CHECK(st[i].m < st[i - 1].m, "metric steps get shorter");
}

int main(void)
{
    test_conv();
    test_channel();
    test_gps();
    printf("units: %d checks, %s\n", checks, failures ? "FAILED" : "all passed");
    return failures != 0;
}
