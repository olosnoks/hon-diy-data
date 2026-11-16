/* hh_data.h: what the display side sees of the data side.
 *
 * The UI calls hh_core_start() once, then hh_snapshot() from its own task as
 * often as it redraws. Nothing here blocks on the K-line, the GPS or the card.
 * Fields are only ever added at the end of a struct; none is renamed.
 */
#ifndef HH_DATA_H
#define HH_DATA_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HH_MAX_CH 48

typedef struct {
    char name[16];              /* address map name: "rpm", "ect", ... */
    char label[20];             /* UTF-8, cut on a character boundary */
    char units[8];              /* UTF-8; may hold a degree sign (2 bytes) */
    float lo, hi;               /* display range */
    float warn_lo, warn_hi;     /* limits of the map's alarm rules on this channel; */
    float alarm_lo, alarm_hi;   /* NaN = no such limit */
    uint8_t decimals;
    uint8_t verified;           /* 0 = the address is a guess: mark the tile */
    uint8_t rate_class;         /* 0 fast, 1 medium, 2 slow */
    /* added */
    uint8_t kind;               /* 0 value, 1 flag (0/1), 2 math (computed from others) */
} hh_channel_info_t;

/* link.state */
enum { HH_LINK_OFF = 0, HH_LINK_CONNECTING = 1, HH_LINK_OK = 2, HH_LINK_ISO_ONLY = 3, HH_LINK_ERROR = 4 };
/* link.backend */
enum { HH_BACKEND_USB = 0, HH_BACKEND_UART = 1 };

typedef struct {
    uint32_t seq;               /* +1 for every new sample (one fast cycle) or state change */
    int64_t t_us;               /* device time of that sample, microseconds since boot */
    uint16_t n_ch;
    float value[HH_MAX_CH];     /* NaN = no value */
    uint8_t fresh[HH_MAX_CH];   /* 1 = read in the latest sample; 0 = held, or never read */
    uint8_t alarm[HH_MAX_CH];   /* 0 ok 1 warn 2 alarm */
    struct {
        uint8_t state;          /* 0 off 1 connecting 2 ok 3 iso_only 4 error */
        uint8_t backend;        /* 0 usb 1 uart */
        float hz_fast;          /* samples per second (fast cycle rate) */
        uint32_t reads, errors;
        char ecu_id[12];
    } link;
    struct { uint8_t fix; uint8_t sats; double lat, lon; float speed_kmh, heading, hdop; } gps;
    struct { uint8_t sd_ok, recording; uint32_t drive_no, bytes; float free_mb; } log;
    struct { uint8_t armed, triggered; uint32_t count; char last_reason[32]; } stall;
    int16_t top_alarm_ch;       /* channel of the most important active alarm, -1 = none or not on a channel */
    uint8_t top_alarm_level;    /* 0 none 1 warn 2 alarm */
    /* ---- added after the first spec; everything above is fixed ---- */
    char top_alarm_text[32];    /* label of that alarm ("Coolant hot"), "" = none */
    uint8_t top_alarm_acked;    /* 1 = acknowledged and still tripped: stop flashing */
    uint8_t map_ok;             /* 0 = running on the built-in map (no HANDHELD.MAP on the card) */
    uint8_t time_valid;         /* 0 no clock yet, 1 set from GPS */
    uint8_t standby;            /* 1 = ECU silent past the drive gap: dim, the core may sleep */
    uint32_t markers;           /* markers placed since boot */
    int64_t unix_us;            /* wall clock now, 0 = unknown */
    char status[40];            /* one line for a status bar: "USB cable not found", "no reply" ... */
    struct { uint8_t valid, n; uint16_t code[6]; int64_t t_us; } dtc;   /* last Mode 03 read; code = raw 2 bytes */
} hh_snapshot_t;

const hh_channel_info_t *hh_channels(uint16_t *n);
void hh_snapshot(hh_snapshot_t *out);   /* lock-protected copy, cheap, safe at 30 Hz */
void hh_mark(void);                     /* button long-press: marker */
void hh_ack_alarm(void);
void hh_core_start(void);               /* starts link, gps, logger tasks */

/* ---- added ---- */
/* A marker at an earlier moment: t_us is device time (the clock of
 * hh_snapshot_t.t_us, esp_timer microseconds). The UI decides on release that
 * a press was a MARK and passes the time the button went down. A time in the
 * future or more than 10 s back is taken as "now". */
void hh_mark_at(int64_t t_us);
int16_t hh_channel_index(const char *name);     /* -1 = not in the map */
void hh_request_dtc(void);              /* read stored trouble codes (ISO 9141-2 Mode 03, about 8 s without live data) */
void hh_dtc_text(uint16_t code, char out[6]);   /* raw code -> "P0301" */
/* Called by the core before it sleeps (ECU silent, sleep_after_s). Weak: the
 * UI may define it to switch the backlight off. */
void hh_ui_before_sleep(void);

#ifdef __cplusplus
}
#endif
#endif
