/* hhcore: the portable part of the hondalog handheld.
 *
 * Plain C99, no hardware or OS calls: the same code runs in the firmware and
 * in the host tests (test/host). It is a port of the laptop's link code and
 * keeps its behaviour:
 *
 *   hh_proto.c   hondalog/protocol.py, link/timing.py   framing, checksum, timeouts
 *   hh_link.c    hondalog/transport.py (Link)            exchanges, resync, retry, read-only guard
 *   hh_plan.c    link/planner.py, link/scheduler.py      block planner, tiered scheduler
 *   hh_expr.c    hondalog/expr (evaluation only)         formulas as postfix programs
 *   hh_map.c     addressmap.py (loading only)            HANDHELD.MAP, HANDHELD.CFG
 *   hh_alarm.c   alarms/engine.py, triggers (stall)      alarms, stall trigger, pre-trigger ring
 *   hh_iso.c     hondalog/obd2.py                        ISO 9141-2: 5-baud init, Mode 01, Mode 03
 *   hh_nmea.c    bbcore's NMEA parser                    GPS
 *   hh_log.c     (FORMAT.md)                             drive and capture files
 *   hh_core.c    hondalog/poller.py + logger.py          the engine: all of it wired together
 *
 * Times are double seconds from the platform clock where the Python uses
 * float seconds (the planner and scheduler give the same answers bit for
 * bit), and uint64 microseconds on disk.
 */
#ifndef HHCORE_H
#define HHCORE_H

#include <stddef.h>
#include <stdint.h>

#include "hh_data.h"

#ifdef __cplusplus
extern "C" {
#endif

#define HH_FW_VERSION   "0.1.0"
#define HH_PACKED       __attribute__((packed))
#define HH_MAX_READ     64          /* largest data length of one read request */
#define HH_MAX_BLOCKS   64
#define HH_MAX_ALARMS   24
#define HH_MAX_CONST    256

enum {
    HH_OK = 0,
    HH_E_NO_REPLY = -1,     /* nothing after the echo, or nothing at all */
    HH_E_SHORT = -2,        /* a reply started and stopped */
    HH_E_CHECKSUM = -3,     /* full length, wrong checksum */
    HH_E_BAD_FRAME = -4,    /* bytes, none of them a reply */
    HH_E_PORT = -5,         /* the transport failed (cable pulled) */
    HH_E_BLOCKED = -6,      /* the read-only guard refused the frame */
    HH_E_INIT = -7,         /* ISO wake-up did not complete */
    HH_E_ARG = -8,
};

uint32_t hh_crc32(uint32_t crc, const void *data, size_t len);      /* IEEE, start with 0 */

/* ---- platform: clock and K-line transport -------------------------------- */

typedef struct {
    void *ctx;
    double (*now)(void *ctx);                   /* monotonic seconds */
    void (*sleep)(void *ctx, double seconds);
} hh_clock_t;

/* Bytes in, bytes out, baud, break: what hondalog/transport.py's Transport is.
 * Two implementations in the firmware (USB host + FTDI cable, UART + L9637D),
 * one in the tests (the simulated ECU). */
typedef struct {
    void *ctx;
    int  (*open)(void *ctx);                    /* 0 ok, <0 not there (yet) */
    void (*close)(void *ctx);
    int  (*write)(void *ctx, const uint8_t *data, size_t len);     /* 0 ok, <0 port lost */
    /* up to max bytes, returns as soon as any have arrived; 0 = timeout, <0 = port lost */
    int  (*read_some)(void *ctx, uint8_t *buf, size_t max, double timeout_s);
    void (*flush_input)(void *ctx);
    int  (*set_baud)(void *ctx, uint32_t baud); /* 0 ok */
    int  (*set_break)(void *ctx, int on);       /* hold the line low; 0 ok */
    double usb_latency_s;                       /* how long the adapter may sit on received bytes */
    uint8_t backend;                            /* HH_BACKEND_USB / HH_BACKEND_UART */
} hh_transport_t;

/* ---- protocol (hondalog/protocol.py) ------------------------------------- */

typedef struct {
    uint32_t baud;
    uint8_t  cmd_read, request_len;
    int16_t  reply_header;          /* -1 = accept any value */
    uint8_t  reply_len_extra, max_read_len;
    uint8_t  init_len, init_seq[16];
    double   init_delay_s, reply_timeout_s, inter_request_delay_s;
    uint8_t  adaptive_timing, retries;
    double   min_reply_timeout_s, inter_byte_timeout_s, max_inter_request_delay_s, turnaround_s;
    double   rate_hz[3];            /* fast, medium, slow */
} hh_framing_t;

#define HH_REQUEST_BYTES  5
#define HH_REPLY_OVERHEAD 3

void    hh_framing_default(hh_framing_t *f);
uint8_t hh_checksum(const uint8_t *data, size_t len);
int     hh_verify(const uint8_t *frame, size_t len);
size_t  hh_build_read_request(const hh_framing_t *f, uint8_t addr, uint8_t len, uint8_t out[HH_REQUEST_BYTES]);
size_t  hh_build_reply(const hh_framing_t *f, const uint8_t *data, uint8_t len, uint8_t *out);
/* Offset of what follows our own request in buf (junk before the echo goes with it). */
size_t  hh_strip_echo(const uint8_t *buf, size_t n, const uint8_t *req, size_t nreq, int *echo_seen);
int     hh_find_reply(const hh_framing_t *f, const uint8_t *rest, size_t n, uint8_t len, uint8_t *data);   /* 1 found */
int     hh_parse_reply(const hh_framing_t *f, const uint8_t *buf, size_t n, const uint8_t *req, size_t nreq,
                       uint8_t len, uint8_t *data);     /* HH_OK or which error */
/* The read-only guard: 1 only for a well-formed read request or the wake-up bytes. */
int     hh_frame_allowed(const hh_framing_t *f, const uint8_t *data, size_t len);

/* ---- adaptive timing (link/timing.py) ------------------------------------ */

typedef struct {
    double byte_s, usb_latency_s;
    double timeout_ceiling, timeout_floor, ibt_ceiling, gap_floor, gap_ceiling;
    double srtt, rttvar, peak_chunk_gap;
    double response_timeout, inter_byte_timeout, gap, error_share;
    uint8_t adaptive, have_srtt;
} hh_timing_t;

void   hh_timing_init(hh_timing_t *t, const hh_framing_t *f, double usb_latency_s);
void   hh_timing_relax(hh_timing_t *t);
void   hh_timing_success(hh_timing_t *t, double overhead_s, double chunk_gap_s);
void   hh_timing_failure(hh_timing_t *t, int is_short, int silent);
double hh_timing_idle_gap(const hh_timing_t *t);
double hh_timing_ibt_floor(const hh_timing_t *t);

/* ---- link (transport.py: Link) -------------------------------------------- */

typedef struct {
    uint32_t requests, ok, timeouts, short_replies, checksum_failures, bad_frames, echo_mismatches,
             junk_bytes, resyncs, discarded_bytes, retries, wakes, reconnects;
    uint32_t blocked;               /* frames the read-only guard refused: must stay 0 */
} hh_link_stats_t;

typedef struct {
    uint8_t addr, len, attempts;
    uint8_t data[HH_MAX_READ];
    double  t_req, t_done, latency_s, turnaround_s;
} hh_read_t;

typedef struct {
    hh_transport_t *tr;
    hh_clock_t clk;
    hh_framing_t f;
    hh_link_stats_t stats;
    hh_timing_t tm;
    int8_t  echo_mode;              /* -1 not known yet, 0 the cable does not echo, 1 it does */
    uint8_t echo_seen;
    double  next_tx, last_tx_end, guard_until;
} hh_link_t;

void hh_link_init(hh_link_t *l, hh_transport_t *tr, const hh_clock_t *clk, const hh_framing_t *f);
void hh_link_reset(hh_link_t *l);
/* Every byte the Honda side sends goes through this one function. */
int  hh_link_tx(hh_link_t *l, const uint8_t *data, size_t len);
int  hh_link_wake(hh_link_t *l);
int  hh_link_resync(hh_link_t *l, double quiet_until);          /* bytes dropped, or HH_E_PORT */
/* retries < 0: the framing's count. Returns HH_OK or the last error. */
int  hh_link_read_block(hh_link_t *l, uint8_t addr, uint8_t len, int retries, hh_read_t *out);

/* ---- planner and scheduler (link/planner.py, link/scheduler.py) ---------- */

typedef struct { double byte_s; int echo_bytes, frame_bytes; double turnaround_s, gap_s; } hh_cost_t;
void   hh_cost_from_framing(hh_cost_t *c, const hh_framing_t *f, double turnaround_s, double gap_s); /* <0 = framing's */
double hh_cost_overhead(const hh_cost_t *c);
double hh_cost_request(const hh_cost_t *c, int length);

typedef struct { uint8_t addr, length; double rate_hz; } hh_plan_ch_t;      /* one channel as the planner sees it */

typedef struct {
    uint16_t addr, length;
    double   rate_hz;
    uint8_t  first, count;          /* its channels: plan->order[first .. first + count) */
} hh_block_t;

typedef struct {
    hh_block_t blocks[HH_MAX_BLOCKS];
    uint8_t n_blocks;
    uint8_t order[HH_MAX_CH];       /* channel indexes in block order */
} hh_plan_t;

/* Cheapest set of reads covering every channel (exact, dynamic programme). 0 ok. */
int    hh_plan_build(hh_plan_t *p, const hh_plan_ch_t *ch, int n_ch, int max_len, const hh_cost_t *cost);
double hh_plan_bus_load(const hh_plan_t *p, const hh_cost_t *cost);

typedef struct {
    uint8_t  n;
    double   rate[HH_MAX_BLOCKS], last[HH_MAX_BLOCKS];
    uint32_t reads[HH_MAX_BLOCKS], failures[HH_MAX_BLOCKS];
    uint8_t  top[HH_MAX_BLOCKS];    /* 1 = in the highest rate class */
    double   lead, prev_done;
    uint8_t  low_last;
} hh_sched_t;

void hh_sched_init(hh_sched_t *s, const hh_plan_t *p);
int  hh_sched_next(hh_sched_t *s, double now);
void hh_sched_done(hh_sched_t *s, int index, double t_done);
void hh_sched_failed(hh_sched_t *s, int index, double now);
void hh_sched_restart(hh_sched_t *s);

/* ---- expressions: postfix programs compiled on the laptop ---------------- */

/* Why not a parser on the device: the grammar lives once, in hondalog/expr.
 * `hondalog-handheld export-map` walks the same syntax tree the laptop
 * evaluates and writes it out as the byte code below, so a formula cannot
 * mean one thing on the laptop and another here. A scale/offset/bit form
 * would not hold the default map (its temperatures are 5th-order
 * polynomials), and a text parser in C would be a second grammar to keep in
 * step. The machine has no jumps: a program always ends, in at most its own
 * length in steps. NaN is "no value" and travels through every operator. */
enum {
    HH_OP_END = 0x00, HH_OP_CONST = 0x01 /* u16 pool index */, HH_OP_IMM = 0x02 /* u8 */,
    HH_OP_X = 0x03, HH_OP_BYTE = 0x04 /* u8 index */, HH_OP_CH = 0x05 /* u8 channel */, HH_OP_TIME = 0x06,
    HH_OP_NEG = 0x10, HH_OP_NOT, HH_OP_BITNOT, HH_OP_VALID,
    HH_OP_ADD = 0x20, HH_OP_SUB, HH_OP_MUL, HH_OP_DIV, HH_OP_FLOORDIV, HH_OP_MOD, HH_OP_POW,
    HH_OP_BAND, HH_OP_BOR, HH_OP_BXOR, HH_OP_SHL, HH_OP_SHR,
    HH_OP_LT = 0x30, HH_OP_LE, HH_OP_GT, HH_OP_GE, HH_OP_EQ, HH_OP_NE,
    HH_OP_AND = 0x38, HH_OP_OR, HH_OP_SELECT, HH_OP_COALESCE,
    HH_OP_CALL = 0x40 /* u8 function, u8 argc */,
};
enum {
    HH_FN_ABS = 0, HH_FN_MIN, HH_FN_MAX, HH_FN_ROUND, HH_FN_INT, HH_FN_FLOAT, HH_FN_FLOOR, HH_FN_CEIL,
    HH_FN_SQRT, HH_FN_EXP, HH_FN_LN, HH_FN_LOG10, HH_FN_POW, HH_FN_SIN, HH_FN_COS, HH_FN_SIGN,
    HH_FN_CLAMP, HH_FN_BETWEEN, HH_FN_BIT, HH_FN_SIGNED, HH_FN_POLY, HH_FN_TABLE, HH_FN_THERMISTOR,
    HH_FN_COUNT
};
#define HH_EXPR_STACK 32

typedef struct {
    const double *consts; uint16_t n_const;
    const float *ch; uint16_t n_ch;         /* channel values, NaN = none */
    const uint8_t *raw; uint8_t raw_len;    /* the channel's bytes as read */
    double x;                               /* raw as one unsigned number */
    double t;
} hh_expr_env_t;

/* Runs prog[0..len). Returns the value, or NaN for no value / a bad program. */
double hh_expr_eval(const uint8_t *prog, size_t len, const hh_expr_env_t *env);
/* 0 if the program is well formed for these limits (stack, indexes, ends in END). */
int    hh_expr_check(const uint8_t *prog, size_t len, uint16_t n_const, uint16_t n_ch);

/* ---- HANDHELD.MAP, HANDHELD.CFG ------------------------------------------ */

#define HH_MAP_MAGIC   0x504D4848u      /* "HHMP" */
#define HH_MAP_SCHEMA  1
#define HH_MAP_MAX_CORE 32768           /* the part the handheld loads */

typedef struct HH_PACKED {
    uint32_t magic;
    uint16_t schema, hdr_len;
    uint32_t core_len;          /* header + sections: what the handheld reads */
    uint32_t core_crc;          /* crc32 of those bytes with this field zero */
    char     map_hash[12];      /* AddressMap.version_hash */
    uint8_t  map_sha256[32];    /* of the map's TOML text */
    uint32_t src_len;           /* TOML text after the core, then its crc32; 0 = none */
} hh_map_hdr_t;                 /* 64 bytes */

typedef struct HH_PACKED { uint8_t type, rsv; uint16_t count; uint32_t len; } hh_map_sec_t;
enum { HH_MS_INFO = 1, HH_MS_PROTO = 2, HH_MS_CONST = 3, HH_MS_CHAN = 4, HH_MS_PROG = 5, HH_MS_ALARM = 6,
       HH_MS_STALL = 7 };

typedef struct HH_PACKED {
    char    version[48];
    char    variant[12];
    int16_t ecu_id_addr;        /* -1 = none */
    uint8_t ecu_id_len, rsv;
} hh_map_info_t;                /* 64 bytes */

typedef struct HH_PACKED {
    uint32_t baud;
    uint8_t  cmd_read, request_len;
    int16_t  reply_header;
    uint8_t  reply_len_extra, max_read_len, adaptive_timing, retries;
    uint8_t  init_len, init_seq[16], rsv[3];
    double   init_delay_s, reply_timeout_s, inter_request_delay_s, min_reply_timeout_s,
             inter_byte_timeout_s, max_inter_request_delay_s, turnaround_s, rate_hz[3];
} hh_map_proto_t;               /* 112 bytes */

enum { HH_KIND_VALUE = 0, HH_KIND_FLAG = 1, HH_KIND_MATH = 2 };

typedef struct HH_PACKED {
    char    name[16], label[20], units[8];
    float   lo, hi, warn_lo, warn_hi, alarm_lo, alarm_hi;
    uint8_t decimals, verified, rate_class, kind;
    uint8_t addr, length, little_endian, rsv;
    uint16_t prog_off, prog_len;
} hh_map_chan_t;                /* 80 bytes */

#define HH_AF_LATCHING 0x01
#define HH_AF_LOW      0x02
#define HH_AF_HIGH     0x04
#define HH_AF_RATE_LOW 0x08
#define HH_AF_RATE_HIGH 0x10
#define HH_AF_MARKER   0x20
#define HH_AF_CAPTURE  0x40
#define HH_AF_TILE     0x80

typedef struct HH_PACKED {
    char    name[16], label[24];
    int16_t channel;            /* -1 = none */
    uint8_t severity;           /* 1 warn, 2 critical */
    uint8_t flags;
    uint8_t priority, rsv[3];
    float   low, high, hysteresis, hold_s, clear_hold_s, rate_low, rate_high, rate_window_ms;
    uint16_t when_off, when_len, clear_off, clear_len, gate_off, gate_len;
} hh_map_alarm_t;               /* 92 bytes */

typedef struct HH_PACKED {
    float   rpm_run, rpm_zero, link_down_s, pre_s, post_s;
    int16_t rpm_channel;        /* -1 = no stall trigger */
    uint16_t rsv;
} hh_map_stall_t;               /* 24 bytes */

/* A loaded map. Points into the buffer it was parsed from: keep that alive. */
typedef struct {
    hh_map_hdr_t hdr;
    hh_map_info_t info;
    hh_framing_t framing;
    const double *consts; uint16_t n_const;     /* may be unaligned: read with memcpy */
    const uint8_t *chan; uint16_t n_ch;         /* hh_map_chan_t records */
    const uint8_t *prog; uint32_t prog_len;
    const uint8_t *alarm; uint16_t n_alarm;     /* hh_map_alarm_t records */
    hh_map_stall_t stall;
} hh_map_t;

/* 0 ok. <0: -1 too short / magic, -2 schema, -3 crc, -4 a section is wrong,
 * -5 a program is wrong, -6 too many channels or alarms. */
int  hh_map_parse(hh_map_t *m, const uint8_t *buf, size_t len);
void hh_map_chan(const hh_map_t *m, int i, hh_map_chan_t *out);
void hh_map_alarm(const hh_map_t *m, int i, hh_map_alarm_t *out);

enum { HH_CFG_BACKEND_AUTO = 0, HH_CFG_BACKEND_USB = 1, HH_CFG_BACKEND_UART = 2 };

typedef struct {
    int32_t backend;            /* auto: USB cable if one enumerates, else stay on USB and wait */
    int32_t gps_baud;           /* 0 = no GPS */
    int32_t iso_fallback;       /* 1: try ISO 9141-2 when Honda reads stay unanswered */
    int32_t iso_after_s;        /* ... for this long */
    int32_t drive_gap_s;        /* ECU silent this long ends the drive */
    int32_t sync_ms;            /* fsync period */
    int32_t block_ms;           /* a log block is closed after this long at the latest */
    int32_t prealloc_mb;        /* drive file space taken at the start; 0 = none */
    int32_t stall_pre_s, stall_post_s;      /* -1 = the map's */
    int32_t capture_files;      /* 1: also write CAPTURES/Cnnnnnn.HHD at a stall */
    int32_t sleep_after_s;      /* deep sleep this long after the drive ended; 0 = never */
    int32_t sleep_probe_s;      /* while asleep, wake this often and ask the ECU */
    int32_t usb_latency_ms;     /* FTDI latency timer */
    int32_t utc_offset_min;     /* for the clock on screen only; files are UTC */
    int32_t serial;             /* 1: the binary link to the laptop on the USB-C port (SERIAL.md); 0 = off */
    int32_t serial_baud;        /* its baud rate */
    int32_t rotate;             /* display: 0 or 180 degrees (180 = picture upside down, for mounting the other way up) */
} hh_config_t;

void   hh_config_default(hh_config_t *c);
/* key = value lines, # comments. Bad lines are skipped; returns how many. */
int    hh_config_parse(hh_config_t *c, const char *text, size_t len);
size_t hh_config_dump(const hh_config_t *c, char *out, size_t cap);

/* ---- alarms (alarms/engine.py) -------------------------------------------- */

enum { HH_AL_IDLE = 0, HH_AL_PENDING, HH_AL_ACTIVE, HH_AL_LATCHED };
enum { HH_ALEV_RAISED = 1, HH_ALEV_CLEARED, HH_ALEV_ACK };

#define HH_SLOPE_N 48
typedef struct { double t[HH_SLOPE_N], x[HH_SLOPE_N]; uint8_t head, n; double w; } hh_slope_t;
void hh_slope_reset(hh_slope_t *s);
/* Least-squares slope over the window, units per second; NaN = not enough data. */
double hh_slope_update(hh_slope_t *s, double t, double x);

typedef struct {
    uint8_t state, acknowledged;
    double  since, raised_at, clear_since;      /* clear_since < 0 = not set */
    float   value;
    uint32_t count;
} hh_alarm_state_t;

typedef struct { uint8_t kind, index, severity; double t; float value; } hh_alarm_event_t;

typedef struct {
    const hh_map_t *map;
    hh_alarm_state_t st[HH_MAX_ALARMS];
    hh_slope_t *slope[HH_MAX_ALARMS];   /* only for rules with a rate limit */
    hh_slope_t slope_pool[4];
    uint8_t n, n_slope;
} hh_alarms_t;

void hh_alarms_init(hh_alarms_t *a, const hh_map_t *map);
/* One sample. values: NaN = none. Writes up to max events; returns the count. */
int  hh_alarms_update(hh_alarms_t *a, const float *values, uint16_t n_values, double t,
                      hh_alarm_event_t *ev, int max);
int  hh_alarms_ack(hh_alarms_t *a, double t, hh_alarm_event_t *ev, int max);
/* Most important active alarm (priority, then oldest), or -1. */
int  hh_alarms_top(const hh_alarms_t *a);
/* Per channel 0 / 1 warn / 2 critical: the worst active rule that colours its tile. */
void hh_alarms_levels(const hh_alarms_t *a, uint8_t *level, uint16_t n_ch);

/* ---- stall trigger (triggers/presets.py: stall_rules) -------------------- */

enum { HH_STALL_NONE = 0, HH_STALL_RPM = 1, HH_STALL_LINK = 2, HH_STALL_ALARM = 3 };

typedef struct {
    double rpm_run, rpm_zero, link_down_s, pre_s, post_s;
    uint8_t armed, capturing, last_reason;
    double link_false_since;        /* < 0 = link is up */
    uint8_t rpm_low;                /* "rpm < rpm_zero" was true at the last feed */
    double t_trigger, t_start, t_end;
    uint32_t count, fired_in_window;
} hh_stall_t;

void hh_stall_init(hh_stall_t *s, const hh_map_stall_t *cfg);
/* Feed what is known now: rpm (NaN = unknown) and whether the ECU answers.
 * Returns the reason if a rule fired at t, else 0. */
int  hh_stall_feed(hh_stall_t *s, double t, double rpm, int link);
int  hh_stall_fire(hh_stall_t *s, double t, int reason);    /* by hand (an alarm with "capture") */
/* 1 once: the open window's post time is over (t_trigger, t_start, t_end are final). */
int  hh_stall_window_done(hh_stall_t *s, double t);

/* ---- byte ring: the last pre_s seconds of reads, in external RAM ---------- */

typedef struct { uint8_t *buf; uint32_t cap, head, tail, used; } hh_ring_t;
void hh_ring_init(hh_ring_t *r, uint8_t *buf, uint32_t cap);
/* Items are length-prefixed; the oldest are dropped to make room. */
void hh_ring_push(hh_ring_t *r, const void *item, uint16_t len);
uint16_t hh_ring_peek(const hh_ring_t *r, uint32_t *pos, void *out, uint16_t max);  /* 0 = end; *pos starts at r->tail */
void hh_ring_pop(hh_ring_t *r);
void hh_ring_clear(hh_ring_t *r);

/* ---- GPS (bbcore's parser, with HDOP) ------------------------------------- */

typedef struct HH_PACKED {
    uint64_t t_us;
    int32_t  lat_e7, lon_e7;
    uint32_t utc;           /* unix seconds, 0 = no date yet */
    uint16_t speed_cms;     /* cm/s */
    uint16_t course_cdeg;   /* 0.01 degree */
    uint8_t  fix, sats;
    uint16_t hdop_c;        /* HDOP x 100, 0 = unknown (reserved in the black box's record) */
} hh_gps_t;                 /* 28 bytes, the black box's bb_gps_t */

typedef struct {
    char line[96]; uint8_t n; hh_gps_t fix;
    uint32_t good, bad;     /* sentences that passed / failed the checksum or framing */
    int32_t gga_tod;        /* seconds of the UTC day in the last GGA that had a fix (no date in GGA) */
} hh_nmea_t;
/* 1 when an RMC sentence completed a fix update; 2 when a GGA with a fix and a time did (gga_tod); else 0. */
int hh_nmea_feed(hh_nmea_t *g, char c);

/* ---- PPS: the wall clock from the GPS pulse ---------------------------------- */

/* The receiver's pulse rises at the start of a UTC second and the sentences
 * that follow carry that second. Pairing the two gives an exact fact, "device
 * time T was second S"; a small filter keeps the offset (and the crystal's
 * drift) from those, so unix = device time + offset is good to tens of
 * microseconds while the pulse is there. Portable: the port only calls
 * hh_core_pps_edge() with the time its interrupt saw the edge (hh_port.c). */
enum { HH_PPS_NONE = 0, HH_PPS_PAIR, HH_PPS_STALE, HH_PPS_OUTLIER, HH_PPS_RELOCK };
#define HH_PPS_HOLD_US  10000000LL      /* no pair for this long: back to NMEA-only time */

typedef struct {
    int64_t last_good_us;               /* the last edge that fits the 1 s chain */
    int64_t pend_us;                    /* a good edge not yet paired */
    int64_t ref_us, last_pair_us;       /* the model is exact at ref_us; the last accepted pair */
    double  off_us, drift;              /* unix us - device us at ref_us; its change per us */
    double  jitter2, resid_us;          /* mean square of the residuals; the last one */
    uint8_t have_edge, pending, n_ok, locked, bad_run, bad_pairs;
    uint16_t n_pairs;                   /* accepted since the model was (re)started, saturating */
    uint32_t edges, glitches, gaps, missed, resets, pairs, stale, outliers, relocks, unpaired;
} hh_pps_t;

void    hh_pps_init(hh_pps_t *p);
/* One rising edge at device time t_us. 1 if it fits the 1 s +- 1 ms chain and so can be paired with a sentence;
 * 0 if it is noise, or the first of a chain (not believed until the next one comes a second later). */
int     hh_pps_edge(hh_pps_t *p, int64_t t_us);
/* A sentence with UTC second utc_s (unix seconds) arrived at t_rx_us: pair it with the pending edge.
 * Returns a HH_PPS_* code; HH_PPS_PAIR means the offset moved. */
int     hh_pps_time(hh_pps_t *p, int64_t t_rx_us, int64_t utc_s);
int     hh_pps_active(const hh_pps_t *p, int64_t t_us);     /* locked, and a pair came within HH_PPS_HOLD_US */
int64_t hh_pps_unix_us(const hh_pps_t *p, int64_t t_us);
double  hh_pps_jitter_us(const hh_pps_t *p);                /* rms of the residuals */
uint32_t hh_pps_err_us(const hh_pps_t *p, int64_t t_us);    /* jitter plus 2 us per second since the last pair */

/* ---- drive and capture files (FORMAT.md) ---------------------------------- */

#define HH_FILE_MAGIC   0x46444848u     /* "HHDF" */
#define HH_BLOCK_MAGIC  0x4B424848u     /* "HHBK" */
#define HH_FILE_VERSION 1

enum { HH_FK_DRIVE = 1, HH_FK_CAPTURE = 2 };
#define HH_HF_TIME_VALID 0x01
#define HH_HF_TIME_GPS   0x02
#define HH_HF_PREALLOC   0x04
#define HH_HF_BUILTIN_MAP 0x08

typedef struct HH_PACKED {
    uint32_t magic;
    uint16_t version, hdr_len;
    uint32_t file_no, boot_no;
    uint8_t  kind, flags, backend, link_mode;   /* link_mode: 0 Honda reads, 1 ISO 9141-2 only */
    uint32_t nonce;             /* mixed into every block CRC of this file */
    uint64_t t0_us;             /* device time of file second 0 */
    int64_t  unix_us_at_t0;     /* wall clock then; 0 = not known yet (see TIME records) */
    char     map_hash[12];
    uint8_t  map_sha[8];        /* first 8 bytes of the map text's sha256 */
    char     fw[12], device[12];
    uint8_t  ecu_id[10], ecu_id_len, rsv0;
    uint32_t drive_no;          /* capture: the drive it was cut from, 0 = none was open */
    uint8_t  reason, rsv1[3];   /* capture: what fired */
    uint64_t trigger_t_us;      /* capture */
    uint32_t prealloc;          /* bytes taken for the file at creation */
    uint8_t  rsv2[8];
    uint32_t crc;               /* crc32 of everything before it */
} hh_file_hdr_t;                /* 128 bytes */

typedef struct HH_PACKED {
    uint32_t magic;
    uint8_t  type, flags;
    uint16_t count;             /* records in the payload */
    uint32_t payload_len;
    uint32_t seq;               /* order of writing, from 0 */
    uint64_t t0_us;             /* time base of the records */
} hh_block_hdr_t;               /* 24 bytes; payload; crc32(nonce, header, payload) */

enum { HH_B_READS = 1, HH_B_GPS = 2, HH_B_EVENTS = 3, HH_B_CONFIG = 4, HH_B_ISO = 5, HH_B_NAMES = 6,
       HH_B_CAPTURE = 7, HH_B_TIME = 8, HH_B_END = 0x7F };

#define HH_BLOCK_PAYLOAD 480    /* a block is closed before it grows past this */

typedef struct HH_PACKED { uint32_t dt_us; uint8_t addr, len; uint16_t latency_us; } hh_rec_read_t;   /* + len data bytes */
typedef struct HH_PACKED { uint32_t dt_us; uint8_t mode, pid, len, flags; } hh_rec_iso_t;             /* + len data bytes */
typedef struct HH_PACKED { uint64_t t_us; uint8_t code, src; uint16_t aux; union { int32_t i; float f; } v; } hh_rec_event_t;  /* 16 */
typedef struct HH_PACKED { uint64_t t_trigger_us, t_start_us, t_end_us; uint8_t reason, complete; uint16_t rsv;
                           uint32_t n_fired; } hh_rec_capture_t;    /* 32 */
/* source: 1 GPS (NMEA), 2 the clock as it was, 3 set by the laptop, 4 GPS + PPS (err_us and drift_ppb are only set for 4) */
enum { HH_TIME_GPS = 1, HH_TIME_CLOCK = 2, HH_TIME_HOST = 3, HH_TIME_PPS = 4 };
typedef struct HH_PACKED { uint64_t t_us; int64_t unix_us; uint8_t source, rsv; uint16_t err_us; int32_t drift_ppb; } hh_rec_time_t;   /* 24 */
typedef struct HH_PACKED { uint32_t n_blocks, n_reads, status, reason; uint64_t t_end_us; } hh_rec_end_t;   /* 24 */

enum { HH_EV_MARKER = 1, HH_EV_LINK_UP, HH_EV_LINK_DOWN, HH_EV_ALARM_RAISED, HH_EV_ALARM_CLEARED,
       HH_EV_ALARM_ACK, HH_EV_STALL, HH_EV_PORT_LOST, HH_EV_PORT_BACK, HH_EV_ISO_SESSION, HH_EV_DTC,
       HH_EV_LOG_DROPPED, HH_EV_DRIVE_START, HH_EV_DTC_NONE };
enum { HH_END_LINK_LOST = 1, HH_END_STOPPED = 2, HH_END_SLEEP = 3 };

/* File I/O supplied by the platform. Paths use '/' and 8.3 names. */
typedef struct {
    void *ctx;
    void *(*open)(void *ctx, const char *path, const char *mode);      /* "wb" */
    int   (*write)(void *ctx, void *fh, const void *data, size_t len); /* 0 ok */
    int   (*sync)(void *ctx, void *fh);
    int   (*close)(void *ctx, void *fh);
    /* Take len bytes for the file now and leave the write position at 0. The
     * bytes keep whatever the card held. May be NULL. 0 ok. */
    int   (*preallocate)(void *ctx, void *fh, uint32_t len);
    /* Cut the file to len bytes (a clean end after preallocation). May be NULL. */
    int   (*truncate)(void *ctx, void *fh, uint32_t len);
} hh_io_t;

/* Builds blocks of one type. `out` gets header, payload and CRC ready to write. */
typedef struct {
    uint8_t  type;
    uint16_t count;
    uint32_t len;               /* payload bytes so far */
    uint64_t t0_us;
    double   opened_at;
    uint8_t  payload[HH_BLOCK_PAYLOAD + 80];
} hh_blk_t;

size_t hh_block_encode(uint32_t nonce, uint8_t type, uint16_t count, uint32_t seq, uint64_t t0_us,
                       const void *payload, uint32_t len, uint8_t *out);    /* out >= len + 28 */
void   hh_file_hdr_seal(hh_file_hdr_t *h);

/* ---- ISO 9141-2 (hondalog/obd2.py: Iso9141Link, read-only services) ------- */

#define HH_ISO_BAUD 10400

typedef struct {
    hh_transport_t *tr;
    hh_clock_t clk;
    uint8_t  up, kb1, kb2;
    double   last_activity, next_tx;
    uint32_t requests, blocked;
} hh_iso_t;

void hh_iso_init(hh_iso_t *iso, hh_transport_t *tr, const hh_clock_t *clk);
/* 1 only for 68 6A F1 01 <pid> <sum> and 68 6A F1 03 <sum>: nothing that
 * clears or writes can be framed. */
int  hh_iso_allowed(const uint8_t *frame, size_t len);
int  hh_iso_slow_init(hh_iso_t *iso);                   /* 5-baud init, about 2.6 s. HH_OK / HH_E_INIT / HH_E_PORT */
void hh_iso_close(hh_iso_t *iso, uint32_t honda_baud);
/* Mode 01. Returns the number of data bytes written (0 = no answer), <0 = error. */
int  hh_iso_read_pid(hh_iso_t *iso, uint8_t pid, uint8_t *data, size_t max);
/* Mode 03. Returns the number of codes (raw 2 bytes each, high byte first), <0 = error. */
int  hh_iso_read_dtcs(hh_iso_t *iso, uint16_t *codes, int max);
/* Cuts a run of replies (48 6B xx ... sum) into payloads; calls fn for each. Returns how many. */
int  hh_iso_split(const uint8_t *buf, size_t n, void (*fn)(void *ctx, const uint8_t *payload, size_t len), void *ctx);
int  hh_iso_pid_len(uint8_t pid);                       /* data bytes of a Mode 01 PID, 0 = unknown */

/* ---- the engine ------------------------------------------------------------ */

typedef struct hh_core hh_core_t;

/* The tap: everything the engine learns, as it learns it, for a listener that
 * is not the card (hh_serial.c streams it to the laptop). Called with the
 * engine's lock held, on whichever task produced the record: it must only
 * copy. The records are the payloads of SERIAL.md's frames. */
enum { HH_TAP_READ = 1, HH_TAP_TICK, HH_TAP_LINK, HH_TAP_GPS, HH_TAP_EVENT, HH_TAP_ISO };
typedef struct HH_PACKED { uint64_t t_us; uint8_t addr, len; uint16_t latency_us; } hh_tap_read_t;    /* + len data bytes */
typedef struct HH_PACKED { uint64_t t_us; uint16_t latency_us, hz_c; uint32_t n; } hh_tap_tick_t;     /* one fast cycle done; hz_c = Hz x 100 */
typedef struct HH_PACKED { uint64_t t_us; uint8_t state, up, backend, iso; } hh_tap_link_t;           /* state: HH_LINK_* */
typedef struct HH_PACKED { uint64_t t_us; uint8_t mode, pid, len, flags; } hh_tap_iso_t;              /* + len data bytes */
/* HH_TAP_GPS carries an hh_gps_t, HH_TAP_EVENT an hh_rec_event_t. */
typedef void (*hh_tap_fn)(void *ctx, uint8_t kind, const void *rec, size_t len, const void *data, size_t data_len);

/* Who the engine is, for a hello. */
typedef struct {
    char    map_hash[12];
    uint8_t map_sha[8];
    char    device[12];
    uint8_t ecu_id[10], ecu_id_len;
    uint8_t link_state, backend, link_mode, builtin_map, time_valid, sd_ok, n_ch;
    uint32_t boot_no;
    uint8_t time_source;                /* HH_TS_* */
} hh_core_info_t;

typedef struct {
    hh_clock_t clk;
    hh_transport_t *transport;          /* opened and closed by the engine */
    hh_io_t io;
    void (*lock)(void *ctx);            /* guards the snapshot and the log queue; may be NULL */
    void (*unlock)(void *ctx);
    void *lock_ctx;
    void (*set_time)(void *ctx, int64_t unix_us);   /* GPS gave the time: set the system clock. May be NULL */
    int64_t (*get_time)(void *ctx);                 /* wall clock in unix us, 0 = not set. May be NULL */
    void *time_ctx;
    const char *device;                 /* short id */
    uint32_t boot_no;
    uint32_t last_drive_no, last_capture_no;        /* highest numbers already on the card */
    uint32_t nonce_seed;                /* anything that differs from boot to boot */
    uint8_t *ring_mem; uint32_t ring_size;          /* pre-trigger reads (external RAM) */
    uint8_t *queue_mem; uint32_t queue_size;        /* blocks waiting for the card */
    uint8_t builtin_map;                /* the map is the compiled-in one */
    hh_tap_fn tap;                      /* may be NULL */
    void *tap_ctx;
} hh_platform_t;

size_t     hh_core_size(void);
/* Builds the engine in mem (hh_core_size() bytes). cfg_text is logged into each drive. */
hh_core_t *hh_core_init(void *mem, const hh_map_t *map, const hh_config_t *cfg, const hh_platform_t *plat);
/* Link task: one exchange (or one wait) per call. Never returns an error:
 * every failure is a state. */
void hh_core_step(hh_core_t *c);
/* GPS task. t_rx_us: device time (esp_timer us) the bytes were read; hh_core_gps_bytes takes it from the clock. */
void hh_core_gps_bytes(hh_core_t *c, const uint8_t *data, size_t len);
void hh_core_gps_bytes_at(hh_core_t *c, const uint8_t *data, size_t len, int64_t t_rx_us);
/* GPS task: a rising edge of the pulse, device time t_us (call it for every edge that came before the bytes
 * just fed, in order, before feeding them). */
void hh_core_pps_edge(hh_core_t *c, int64_t t_us);
/* Storage task: writes what is queued. Returns 1 if it did work, 0 if idle. */
int  hh_core_storage_step(hh_core_t *c);
void hh_core_set_storage(hh_core_t *c, int sd_ok, float free_mb);
/* The pulse clock, for the diagnostics line. */
typedef struct {
    uint8_t source, locked;             /* HH_TS_*, filter locked */
    uint32_t err_us;
    int64_t offset_us;                  /* unix us - device us, now */
    double drift_ppm, jitter_us, resid_us;
    uint32_t edges, glitches, missed, pairs, stale, outliers, relocks;
} hh_pps_info_t;
void hh_core_pps_info(hh_core_t *c, hh_pps_info_t *out);
/* Any task. */
void hh_core_snapshot(hh_core_t *c, hh_snapshot_t *out);
void hh_core_mark(hh_core_t *c);
void hh_core_mark_at(hh_core_t *c, int64_t t_us);      /* device us; out of range = now */
void hh_core_ack(hh_core_t *c);
void hh_core_request_dtc(hh_core_t *c);
const hh_channel_info_t *hh_core_channels(const hh_core_t *c, uint16_t *n);
/* Seconds since the ECU last answered (0 while it does), for the sleep decision. */
double hh_core_idle_s(hh_core_t *c);
/* End the drive now (before sleep or power down). */
void hh_core_stop(hh_core_t *c, int reason);
const hh_link_stats_t *hh_core_link_stats(const hh_core_t *c);
const hh_plan_t *hh_core_plan(const hh_core_t *c);
/* A listener for everything the engine learns (see hh_tap_fn); NULL = none. Set it before the tasks run. */
void hh_core_set_tap(hh_core_t *c, hh_tap_fn tap, void *ctx);
/* For the laptop link (any task). None of these can put a byte on the K-line. */
void hh_core_info(hh_core_t *c, hh_core_info_t *out);
/* The laptop's clock, when the GPS has not set one: 0 taken, 1 ignored (GPS time is in use). */
int  hh_core_set_time(hh_core_t *c, int64_t unix_us);
/* on = 0: end the drive now and start no other until on = 1 (or a restart).
 * on = 1: drives start again, at once if the ECU is answering. */
void hh_core_drive(hh_core_t *c, int on);
/* 1 while DRIVES/Dnnnnnn.HHD (kind HH_FK_DRIVE) or CAPTURES/Cnnnnnn.HHD is open for writing or queued to be. */
int  hh_core_file_busy(hh_core_t *c, uint8_t kind, uint32_t file_no);

#ifdef __cplusplus
}
#endif
#endif
