/* Display units. Everything behind the screen stays in the address map's units:
 * the engine, the alarms, the drive files, the stream to the laptop. Only what
 * is drawn is converted, here, by the channel's unit string:
 *
 *   °C -> °F    km/h -> mph    km -> mi    kPa -> inHg    L/h -> gal/h
 *
 * A "/s" rate (°C/s) converts as a span: scale only, no offset. Any other unit
 * (V, %, ms, rpm, AFR, degrees of timing) is shown as it is. The factors and
 * the decimals follow the laptop's hondalog/units.py and hondalog/ui/units.py,
 * so both show the same numbers.
 *
 * HANDHELD.CFG `units = us` (the default) or `units = metric` picks the set;
 * metric shows everything as the map has it. Plain C, no LVGL: the host tests
 * build it on its own (test/host/hhunitstest.c).
 */
#ifndef UI_UNITS_H
#define UI_UNITS_H

#include <stdbool.h>
#include <stddef.h>
#include "hh_data.h"

enum { UI_UNITS_METRIC = 0, UI_UNITS_US = 1 };

void ui_units_set(int system);          /* anything but UI_UNITS_METRIC is US */
int  ui_units_get(void);

/* display = base * scale + offset */
typedef struct {
    float scale, offset;
    int extra_decimals;                 /* added to the channel's decimals (inHg, gal/h: +1) */
    char unit[8];                       /* the display unit string */
} ui_conv_t;

/* The conversion for a base unit in the current set. False (and an identity
 * conversion with the unit copied) when it is shown as it is. */
bool ui_units_conv(const char *base_unit, ui_conv_t *out);
/* NaN and infinities pass through. */
float ui_units_apply(const ui_conv_t *c, float v);
/* The channel as the screen shows it: units, range (lo, hi), the four limits
 * and decimals converted; flags are left alone. NaN limits stay NaN. */
void ui_units_channel(const hh_channel_info_t *base, hh_channel_info_t *shown, ui_conv_t *conv);

/* GPS speed and trip distance (the core gives km/h and km). */
float ui_units_speed(float kmh);
const char *ui_units_speed_unit(void);              /* "mph" / "km/h" */
/* "12.34 mi" (2 decimals under 100, then 1); "---" for NaN */
void ui_units_distance(char *buf, size_t n, double km);

/* The map's scale bar: lengths to pick from, longest first, in metres with
 * their text ("20 mi" ... "0.1 mi", "250 ft"; metric "20 km" ... "50 m"). */
typedef struct { float m; const char *text; } ui_scale_step_t;
const ui_scale_step_t *ui_units_scale_steps(size_t *n);

#endif
