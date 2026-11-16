/* hhcore: NMEA (RMC + GGA). This is the black box's parser
 * (firmware/blackbox/components/bbcore/bb_util.c, bb_nmea_feed) with one
 * addition: HDOP from GGA goes into the record's reserved word. Kept as a
 * copy so that the two firmwares build on their own; the record layout is
 * the same 28 bytes. */
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#include "hhcore.h"

static int32_t nmea_deg_e7(const char *f, const char *hemi)
{
    /* ddmm.mmmm or dddmm.mmmm */
    const char *dot = strchr(f, '.');
    size_t ilen = dot ? (size_t)(dot - f) : strlen(f);
    if (ilen < 3) return 0;
    long deg = 0;
    for (size_t i = 0; i + 2 < ilen; i++) deg = deg * 10 + (f[i] - '0');
    /* minutes in 1e-5 minute units */
    long min5 = ((f[ilen - 2] - '0') * 10 + (f[ilen - 1] - '0')) * 100000L;
    if (dot) {
        long scale = 10000;
        for (const char *p = dot + 1; *p >= '0' && *p <= '9' && scale; p++, scale /= 10)
            min5 += (*p - '0') * scale;
    }
    int64_t e7 = (int64_t)deg * 10000000 + (int64_t)min5 * 100 / 60;
    if (*hemi == 'S' || *hemi == 'W') e7 = -e7;
    return (int32_t)e7;
}

static uint32_t unix_from_ymdhms(int y, int mo, int d, int h, int mi, int s)
{
    /* days from civil, valid 2000..2099 */
    y -= mo <= 2;
    long era = y / 400, yoe = y - era * 400;
    long doy = (153 * (mo + (mo > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    long days = era * 146097 + doe - 719468;
    return (uint32_t)(days * 86400L + h * 3600L + mi * 60L + s);
}

static uint32_t scaled(const char *f, unsigned mul_num, unsigned mul_den)
{
    /* parse a decimal number, return value * 1000 * num / den */
    uint64_t v = 0, frac = 0, scale = 1;
    while (*f >= '0' && *f <= '9') v = v * 10 + (uint64_t)(*f++ - '0');
    if (*f == '.') for (f++; *f >= '0' && *f <= '9' && scale < 1000; f++) { frac = frac * 10 + (uint64_t)(*f - '0'); scale *= 10; }
    return (uint32_t)((v * 1000 + frac * 1000 / scale) * mul_num / mul_den);
}

int hh_nmea_feed(hh_nmea_t *g, char c)
{
    if (c == '$') { g->n = 0; g->line[g->n++] = c; return 0; }
    if (c != '\n' && c != '\r') {
        if (g->n && g->n < sizeof(g->line) - 1) g->line[g->n++] = c;
        return 0;
    }
    if (g->n < 8) { g->n = 0; return 0; }
    g->line[g->n] = 0;
    g->n = 0;
    char *star = strchr(g->line, '*');
    if (!star) return 0;
    uint8_t sum = 0;
    for (char *p = g->line + 1; p < star; p++) sum ^= (uint8_t)*p;
    /* exactly two hex digits after '*', nothing else */
    char *endp;
    if (strlen(star + 1) != 2 || !isxdigit((unsigned char)star[1]) || !isxdigit((unsigned char)star[2]))
        return 0;
    if ((uint8_t)strtol(star + 1, &endp, 16) != sum || *endp) return 0;
    *star = 0;
    char *f[16];
    int nf = 0;
    for (char *p = g->line; nf < 16; ) {
        f[nf++] = p;
        p = strchr(p, ',');
        if (!p) break;
        *p++ = 0;
    }
    const char *id = f[0] + 3;                  /* skip "$GP" / "$GN" */
    if (strlen(f[0]) < 6) return 0;
    if (strcmp(id, "GGA") == 0 && nf >= 9) {
        uint32_t hdop = scaled(f[8], 1, 10);    /* x 100 */
        g->fix.sats = (uint8_t)atoi(f[7]);
        g->fix.hdop_c = (uint16_t)(hdop > 65535 ? 65535 : hdop);
        return 0;
    }
    if (strcmp(id, "RMC") != 0 || nf < 10) return 0;
    g->fix.fix = f[2][0] == 'A';
    if (g->fix.fix) {
        g->fix.lat_e7 = nmea_deg_e7(f[3], f[4]);
        g->fix.lon_e7 = nmea_deg_e7(f[5], f[6]);
        g->fix.speed_cms = (uint16_t)(scaled(f[7], 5144, 100000));      /* knots -> cm/s */
        g->fix.course_cdeg = (uint16_t)(scaled(f[8], 1, 10));
    }
    if (strlen(f[1]) >= 6 && strlen(f[9]) == 6) {
        #define D2(s, i) (((s)[i] - '0') * 10 + ((s)[(i) + 1] - '0'))
        g->fix.utc = unix_from_ymdhms(2000 + D2(f[9], 4), D2(f[9], 2), D2(f[9], 0),
                                      D2(f[1], 0), D2(f[1], 2), D2(f[1], 4));
        #undef D2
    }
    return 1;
}
