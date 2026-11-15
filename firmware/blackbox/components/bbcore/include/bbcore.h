/* bbcore: the portable part of the hondalog black box.
 *
 * Everything in here is plain C99 with no hardware or OS calls, so the same
 * code runs in the firmware and in the host tests (test/host). The firmware
 * supplies time, ADC conversions, edges and file I/O through small callback
 * tables; see bb_engine_* at the bottom.
 *
 * All on-disk and on-wire structures are little-endian and packed. The
 * Python side (hondalog/blackbox/format.py) mirrors them byte for byte.
 */
#ifndef BBCORE_H
#define BBCORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BB_FW_VERSION    "0.1.0"
#define BB_FILE_VERSION  1
#define BB_PROTO_VERSION 1
#define BB_SAMPLE_HZ     1000u
#define BB_TICK_US       1000u          /* one sample record per tick */

#define BB_PACKED __attribute__((packed))

/* ---- channels ----------------------------------------------------------- */

enum { BB_A_VBAT, BB_A_ECUB, BB_A_PUMP, BB_A_IG1, BB_A_ALT, BB_N_ANALOG };
#define BB_N_RAILS 4            /* the first four get min/max per millisecond */
enum { BB_E_TACH, BB_E_IGN, BB_E_INJ, BB_N_EDGE };

/* bb_sample_t.dig bits */
#define BB_DIG_TACH      0x01   /* signal level, 1 = active */
#define BB_DIG_IGN       0x02
#define BB_DIG_INJ       0x04
#define BB_DIG_BUTTON    0x08
#define BB_DIG_IG1_ON    0x10   /* debounced */
#define BB_DIG_RUNNING   0x20   /* engine seen running (trigger armed) */
#define BB_DIG_CAPTURING 0x40
#define BB_DIG_GAP       0x80   /* record was filled in, the ADC fell behind */

typedef struct BB_PACKED {
    uint16_t mv[BB_N_ANALOG];   /* car-side millivolts, mean over the millisecond */
    uint8_t  min_dv[BB_N_RAILS]; /* lowest / highest 5 kHz reading in the millisecond, */
    uint8_t  max_dv[BB_N_RAILS]; /* in 0.1 V steps (min rounds down, max rounds up)    */
    uint16_t rpm_tach;
    uint16_t rpm_ign;
    uint16_t inj_pw_us;         /* last injector pulse width, saturates at 65535 */
    uint8_t  inj_duty;          /* 0.5 % steps, 200 = 100 % */
    uint8_t  dig;
    uint16_t rsv;
} bb_sample_t;                  /* 28 bytes, one per millisecond */

/* An edge is one 64-bit word: bits 0..55 time in us, 56..57 channel,
 * bit 60 the new level (1 = active). */
typedef uint64_t bb_edge_t;
#define BB_EDGE_T_MASK  0x00FFFFFFFFFFFFFFull
#define BB_EDGE(t, ch, lvl) (((uint64_t)(t) & BB_EDGE_T_MASK) | ((uint64_t)(ch) << 56) | ((uint64_t)((lvl) ? 1 : 0) << 60))
#define BB_EDGE_T(e)    ((e) & BB_EDGE_T_MASK)
#define BB_EDGE_CH(e)   ((unsigned)(((e) >> 56) & 3))
#define BB_EDGE_LVL(e)  ((unsigned)(((e) >> 60) & 1))

/* ---- events and reason codes ------------------------------------------- */

enum {
    BB_R_NONE = 0,
    /* trigger reasons: these can start an event file */
    BB_R_TACH_STOP = 1,     /* tach pulses stopped, IG1 still on */
    BB_R_IGN_STOP  = 2,     /* ignition trigger pulses stopped, IG1 still on */
    BB_R_IG1_DROP  = 3,     /* IG1 fell while the engine was running */
    BB_R_ECUB_DROP = 4,     /* ECU +B fell while IG1 on: main relay signature */
    BB_R_PUMP_DROP = 5,     /* fuel pump feed fell while running, IG1 on */
    BB_R_VBAT_DIP  = 6,     /* battery below threshold while running */
    BB_R_BUTTON    = 7,     /* marker button */
    BB_R_HOST      = 8,     /* trigger command over USB */
    BB_R_BROWNOUT  = 9,     /* logger supply is collapsing, flush now */
    BB_R_INJ_STOP  = 10,    /* injector pulses stopped while the tach keeps going */
    BB_R_ECUB_GLITCH = 11,  /* ECU +B falling faster than a set slope */
    /* 12..31: free for user rules */
    BB_R_MAX_TRIGGER = 31,
    /* plain events: recorded, never start a file */
    BB_EV_HOST_MARK = 32,   /* value = marker number from the laptop */
    BB_EV_TIME_SYNC = 33,
    BB_EV_RUNNING   = 34,   /* engine seen running */
    BB_EV_IG1_ON    = 35,
    BB_EV_IG1_OFF   = 36,
    BB_EV_OVERRUN   = 37,   /* ring overwritten before it was saved */
};

typedef struct BB_PACKED {
    uint64_t t_us;
    uint8_t  code;
    uint8_t  src;           /* analog/edge channel the value came from, or 0xFF */
    uint16_t rsv;
    int32_t  value;         /* mV, rpm or marker number, depends on code */
} bb_event_t;               /* 16 bytes */

typedef struct BB_PACKED {
    uint64_t t_us;
    int32_t  lat_e7, lon_e7;
    uint32_t utc;           /* unix seconds, 0 = no date yet */
    uint16_t speed_cms;     /* cm/s */
    uint16_t course_cdeg;   /* 0.01 degree */
    uint8_t  fix, sats;
    uint16_t rsv;
} bb_gps_t;                 /* 28 bytes */

/* ---- checksums, framing ------------------------------------------------- */

uint32_t bb_crc32(uint32_t crc, const void *data, size_t len);   /* IEEE, start with 0 */
uint16_t bb_crc16(const void *data, size_t len);                 /* CCITT-FALSE */
size_t bb_cobs_encode(const uint8_t *in, size_t len, uint8_t *out);     /* out >= len + len/254 + 1 */
size_t bb_cobs_decode(const uint8_t *in, size_t len, uint8_t *out);     /* 0 = malformed */

/* USB frame: COBS( ver, type, seq, payload..., crc16 lo, crc16 hi ) 0x00 */
#define BB_FRAME_MAX_PAYLOAD 512
#define BB_FRAME_MAX_WIRE    (BB_FRAME_MAX_PAYLOAD + 5 + 4 + 2)

enum {
    /* device -> host */
    BB_F_HELLO = 0x01, BB_F_LIVE = 0x02, BB_F_EVENT = 0x03, BB_F_TIME_RSP = 0x05,
    BB_F_TEXT = 0x0A, BB_F_FILE_INFO = 0x0B, BB_F_FILE_DATA = 0x0C,
    /* host -> device */
    BB_F_GET_HELLO = 0x10, BB_F_TIME_REQ = 0x11, BB_F_TIME_SET = 0x12, BB_F_MARK = 0x13,
    BB_F_TRIGGER = 0x14, BB_F_SELFTEST = 0x15, BB_F_GET_CONFIG = 0x16, BB_F_SET_CONFIG = 0x17,
    BB_F_SAVE_CONFIG = 0x18, BB_F_LIST_FILES = 0x19, BB_F_READ_FILE = 0x1A, BB_F_LIVE_RATE = 0x1B,
};

size_t bb_frame_encode(uint8_t type, uint8_t seq, const void *payload, size_t len, uint8_t *wire);

typedef struct {
    uint8_t  buf[BB_FRAME_MAX_WIRE];
    uint16_t n;
    uint8_t  overflow;
    uint32_t bad;           /* frames dropped: CRC, COBS, version or length */
} bb_deframer_t;

/* Feed one received byte. Returns 1 when a good frame is ready: *type, *seq,
 * *payload (points into d->buf, valid until the next call) and *len are set. */
int bb_deframe(bb_deframer_t *d, uint8_t byte, uint8_t *type, uint8_t *seq,
               const uint8_t **payload, size_t *len);

typedef struct BB_PACKED {
    uint8_t  proto, file_ver;
    char     fw[12], device[12];
    uint16_t sample_hz, live_hz;
    uint32_t boot_no, event_count;
    uint8_t  n_analog, n_edge;
} bb_hello_t;

#define BB_ST_SD_OK      0x01
#define BB_ST_CAPTURING  0x02
#define BB_ST_TIME_VALID 0x04
#define BB_ST_RUNNING    0x08
#define BB_ST_IG1_ON     0x10
#define BB_ST_GPS_FIX    0x20
#define BB_ST_SELFTEST_FAIL 0x40

typedef struct BB_PACKED {
    uint64_t    t_us;
    bb_sample_t now;
    uint16_t    mv_min[BB_N_ANALOG];    /* lowest since the previous live frame */
    uint32_t    event_count;
    uint8_t     state;                  /* BB_ST_* */
    uint8_t     last_reason;
    uint16_t    rsv;
} bb_live_t;                            /* 54 bytes */

typedef struct BB_PACKED { bb_event_t ev; uint32_t event_no; uint8_t started_file; } bb_event_msg_t;
typedef struct BB_PACKED { int64_t host_tx_us; uint64_t dev_rx_us, dev_tx_us; } bb_time_rsp_t;
typedef struct BB_PACKED { uint32_t event_no, size; uint8_t reason, complete; } bb_file_info_t;
typedef struct BB_PACKED { uint32_t event_no, offset; uint16_t len; } bb_read_file_t;
#define BB_FILE_CHUNK 480

/* ---- configuration ------------------------------------------------------ */

/* A trigger rule: "<source> <op> <level> for <hold_ms>, while <qual>".
 * The rule arms once the qualifiers hold and the source is on the good side
 * of the level by at least `hyst`; it fires once when the condition has held
 * for hold_ms samples in a row, and must arm again before it can refire. */
enum {  /* sources */
    BB_S_VBAT, BB_S_ECUB, BB_S_PUMP, BB_S_IG1, BB_S_ALT,    /* mV: "<" tests the ms minimum, ">" the maximum */
    BB_S_SLEW_VBAT, BB_S_SLEW_ECUB, BB_S_SLEW_PUMP, BB_S_SLEW_IG1, BB_S_SLEW_ALT,   /* mV per ms, signed */
    BB_S_TACH_AGE, BB_S_IGN_AGE, BB_S_INJ_AGE,              /* ms since the last edge */
    BB_S_RPM_TACH, BB_S_RPM_IGN, BB_S_INJ_PW, BB_S_INJ_DUTY, /* rpm, us, 0.5 % */
    BB_S_BUTTON,                                            /* 0 / 1 */
    BB_N_SOURCES
};
enum { BB_OP_BELOW, BB_OP_ABOVE };
#define BB_Q_IG1         0x01   /* IG1 debounced on and above ig1_on_mv right now */
#define BB_Q_RUNNING     0x02   /* tach has shown run_rpm for run_ms */
#define BB_Q_IGN_RUNNING 0x04   /* same for the ignition trigger input */
#define BB_Q_TACH_ALIVE  0x08   /* a tach edge within pulse_timeout_ms */
enum { BB_ACT_OFF, BB_ACT_MARK, BB_ACT_CAPTURE };  /* MARK: record the event only */
#define BB_MAX_RULES 32         /* slot number = reason code; 0 and 8 are not rules */

typedef struct {
    uint8_t  action, source, op, qual;
    int32_t  level, hyst;
    uint16_t hold_ms;
} bb_rule_t;

typedef struct {
    int32_t pre_s, post_s;
    int32_t scale_milli[BB_N_ANALOG];   /* car mV = pin mV * scale / 1000 + offset */
    int32_t offset_mv[BB_N_ANALOG];
    int32_t invert[BB_N_EDGE];          /* 1: GPIO low = signal active (opto) */
    int32_t tach_ppr, ign_ppr;          /* pulses per crank revolution */
    int32_t edge_min_us;                /* shorter periods are glitches */
    int32_t run_rpm, run_ms;            /* "running" = above run_rpm for run_ms */
    int32_t pulse_timeout_ms;           /* no edge for this long = rpm 0 */
    int32_t ig1_on_mv, ig1_off_mv, ig1_debounce_ms;
    int32_t holdoff_s;                  /* minimum gap between event files */
    bb_rule_t rule[BB_MAX_RULES];       /* indexed by reason code */
    int32_t host_mark_triggers;
    int32_t live_hz, summary_s, csv_export;
    int32_t sleep_after_s, gps_baud, alt_enable;
    int32_t edge_ring;                  /* edges kept in RAM */
} bb_config_t;

void bb_config_defaults(bb_config_t *c);
/* Set one key. 0 ok, -1 unknown key, -2 bad value. */
int  bb_config_set(bb_config_t *c, const char *key, const char *value);
extern const char *const bb_source_names[BB_N_SOURCES];
/* Parse "key = value" lines ('#' comments). Returns the number of bad lines;
 * the first bad line number goes to *bad_line (may be NULL). */
int  bb_config_parse(bb_config_t *c, const char *text, size_t len, int *bad_line);
/* Write the config as text. Returns the length (always < cap). */
size_t bb_config_dump(const bb_config_t *c, char *out, size_t cap);

/* ---- edge timing -------------------------------------------------------- */

typedef struct {
    uint64_t last_act_us;       /* last accepted active-going edge */
    uint32_t period_us;         /* between the last two of those, 0 = unknown */
    uint32_t pw_us;             /* last active pulse width */
    uint8_t  level, seen;
} bb_edge_ch_t;

void     bb_edge_feed(bb_edge_ch_t *c, uint64_t t_us, int level, uint32_t min_us);
uint32_t bb_edge_rpm(const bb_edge_ch_t *c, uint64_t now_us, uint32_t ppr, uint32_t timeout_us);
uint32_t bb_edge_duty200(const bb_edge_ch_t *c, uint64_t now_us, uint32_t timeout_us);
/* Turn a 32-bit capture count into 64-bit time using a nearby 64-bit hint. */
uint64_t bb_extend32(uint64_t hint, uint32_t low);

/* ---- trigger logic ------------------------------------------------------ */

typedef struct { uint16_t cnt; uint8_t state; } bb_debounce_t;

typedef struct {
    bb_debounce_t ig1;
    uint16_t run_cnt, ign_run_cnt;
    uint8_t  running, ign_running;      /* tach / ignition trigger seen alive */
    uint8_t  qual;                      /* BB_Q_* as of the previous step */
    uint8_t  have_prev;
    uint16_t prev_mv[BB_N_ANALOG];
    struct { uint16_t cnt; uint8_t armed; } rule[BB_MAX_RULES];
} bb_trigger_t;

typedef struct {
    const bb_sample_t *s;       /* mv and rpm filled in */
    uint16_t min_mv[BB_N_ANALOG], max_mv[BB_N_ANALOG];  /* exact, this millisecond */
    uint64_t t_us;
    uint32_t age_ms[BB_N_EDGE]; /* time since the last edge on each pulse input */
    int      button;
} bb_trig_in_t;

/* Run one 1 ms step. Fired events (triggers and plain) are written to out[],
 * at most BB_TRIG_MAX_OUT. Returns how many. A trigger event carries its
 * rule's action in ev.rsv (BB_ACT_*). The debounced state bits
 * (BB_DIG_IG1_ON, BB_DIG_RUNNING) come back through *dig. */
#define BB_TRIG_MAX_OUT 12
int bb_trigger_step(bb_trigger_t *tr, const bb_config_t *cfg, const bb_trig_in_t *in,
                    bb_event_t *out, uint8_t *dig);

/* ---- ADC resampler ------------------------------------------------------ */

typedef struct { uint8_t ch; uint16_t mv; } bb_conv_t;      /* one conversion, pin mV */

typedef struct {
    uint64_t end_us;            /* estimated time of the last conversion seen */
    uint64_t next_tick_us;      /* time of the next record to emit */
    uint32_t period_q8;         /* us per conversion, 24.8 fixed point */
    uint32_t acc[BB_N_ANALOG];
    uint16_t n[BB_N_ANALOG];
    uint16_t lo[BB_N_ANALOG], hi[BB_N_ANALOG];
    uint16_t last[BB_N_ANALOG];
    uint8_t  started;
} bb_resamp_t;

/* ---- event file --------------------------------------------------------- */

#define BB_FILE_MAGIC  0x58424248u      /* "HBBX" */
#define BB_BLOCK_MAGIC 0x4B4C4242u      /* "BBLK" */

enum { BB_B_SAMPLES = 1, BB_B_EDGES = 2, BB_B_EVENTS = 3, BB_B_CONFIG = 4, BB_B_GPS = 5,
       BB_B_INDEX = 0x7E, BB_B_END = 0x7F };

#define BB_HF_TIME_VALID 0x01
#define BB_HF_TIME_GPS   0x02

typedef struct BB_PACKED {
    uint32_t magic;
    uint16_t version, hdr_len;
    uint32_t event_no, boot_no;
    uint8_t  reason, n_analog, n_edge, flags;
    uint32_t sample_hz, pre_ms, post_ms;
    uint64_t trigger_t_us;
    int64_t  unix_us_at_t0;     /* wall clock at t_us = 0; 0 = unknown */
    int32_t  trigger_value;
    char     fw[12], device[12];
    uint8_t  tach_ppr, ign_ppr;
    uint16_t rsv;
    uint32_t crc;               /* crc32 of everything before it */
} bb_file_hdr_t;                /* 84 bytes */

typedef struct BB_PACKED {
    uint32_t magic;
    uint8_t  type, flags;
    uint16_t count;             /* records in the payload */
    uint32_t payload_len;
    uint32_t seq;               /* order of writing */
    uint64_t t0_us;             /* time of the first record */
} bb_block_hdr_t;               /* 24 bytes; payload; crc32(header + payload) */

typedef struct BB_PACKED {
    uint32_t offset;
    uint8_t  type, flags;
    uint16_t count;
    uint64_t t0_us;
} bb_index_t;                   /* 16 bytes */

#define BB_END_COMPLETE 1
typedef struct BB_PACKED { uint32_t index_offset, n_blocks, status, n_samples; } bb_end_t;

#define BB_BLOCK_SAMPLES 256
#define BB_BLOCK_EDGES   512
#define BB_MAX_INDEX     1024
#define BB_SCRATCH       (sizeof(bb_block_hdr_t) + BB_BLOCK_SAMPLES * sizeof(bb_sample_t) + 4)

/* File I/O supplied by the platform. Paths use '/' and 8.3 names. */
typedef struct {
    void *ctx;
    void *(*open)(void *ctx, const char *path, const char *mode);   /* "wb", "rb", "ab" */
    int   (*write)(void *ctx, void *fh, const void *data, size_t len);  /* 0 ok */
    int   (*read)(void *ctx, void *fh, void *data, size_t len);     /* bytes read, <0 error */
    int   (*seek)(void *ctx, void *fh, uint32_t offset);
    long  (*size)(void *ctx, void *fh);
    int   (*sync)(void *ctx, void *fh);
    int   (*close)(void *ctx, void *fh);
} bb_io_t;

/* ---- summary, GPS, LED -------------------------------------------------- */

size_t bb_csv_header(char *out, size_t cap);
size_t bb_csv_row(char *out, size_t cap, uint64_t t_us, const bb_sample_t *s);

/* Feed GPS bytes. Returns 1 when an RMC sentence completed a fix update. */
typedef struct { char line[96]; uint8_t n; bb_gps_t fix; } bb_nmea_t;
int bb_nmea_feed(bb_nmea_t *g, char c);

enum { BB_LED_BOOT, BB_LED_IDLE, BB_LED_RUNNING, BB_LED_CAPTURE, BB_LED_WAITING, BB_LED_NO_SD, BB_LED_FAULT };
int bb_led_pattern(int state, uint32_t t_ms);       /* 1 = LED on */

/* ---- the engine: everything above wired together ------------------------ */

typedef struct bb_engine bb_engine_t;

typedef struct {
    bb_io_t io;
    /* The engine calls unlock/lock around slow file I/O in bb_engine_storage_step
     * and bb_engine_rx. May be NULL on a single-threaded host. */
    void (*lock)(void *ctx);
    void (*unlock)(void *ctx);
    void *lock_ctx;
    /* Send one encoded frame to the laptop. May drop when not connected. */
    void (*tx)(void *ctx, const uint8_t *wire, size_t len);
    void *tx_ctx;
    uint64_t (*now_us)(void *ctx);
    void *now_ctx;
    void (*selftest)(void *ctx);        /* run the platform self-test (may be NULL) */
    void *selftest_ctx;
    uint32_t adc_conv_hz;               /* conversions per second, all channels together */
    const char *device;                 /* short id, e.g. last MAC bytes */
    uint32_t boot_no;
    uint32_t last_event_no;             /* highest EVnnnnnn.BBX already on the card */
    int sd_ok;
} bb_platform_t;

/* Memory needed for the given config (rings + engine). */
size_t bb_engine_mem_size(const bb_config_t *cfg);
/* Build the engine inside mem. If mem is too small pre_s and the edge ring
 * are shrunk to fit (check bb_engine_config()->pre_s). NULL if hopeless. */
bb_engine_t *bb_engine_init(void *mem, size_t mem_size, const bb_config_t *cfg,
                            const bb_platform_t *plat);
const bb_config_t *bb_engine_config(const bb_engine_t *e);

/* Acquisition side. Call with the lock held, edges before the chunk they
 * precede. */
void bb_engine_edge(bb_engine_t *e, unsigned ch, int gpio_level, uint64_t t_us);
void bb_engine_adc_chunk(bb_engine_t *e, uint64_t t_arrival_us, const bb_conv_t *conv, size_t n);
void bb_engine_button(bb_engine_t *e, int pressed);
void bb_engine_gps(bb_engine_t *e, const bb_gps_t *fix);

/* Storage task: call in a loop with the lock held. Returns 1 if it did work
 * (call again soon), 0 if idle. */
int  bb_engine_storage_step(bb_engine_t *e);
/* Link task, lock held: bytes from the laptop, and the periodic live frame
 * (call at >= live_hz; it paces itself). */
void bb_engine_rx(bb_engine_t *e, const uint8_t *data, size_t len);
void bb_engine_link_poll(bb_engine_t *e);
void bb_engine_text(bb_engine_t *e, const char *text);

typedef struct {
    uint64_t t_us;
    bb_sample_t now;
    uint32_t event_count, samples, edges, overruns, io_errors;
    uint8_t  state, last_reason, capturing, sd_ok;
    uint32_t captures_this_boot;
    uint32_t ig1_off_ms;        /* how long IG1 has been off (0 while on) */
    int64_t  unix_us_at_t0;
} bb_status_t;
void bb_engine_status(const bb_engine_t *e, bb_status_t *st);
void bb_engine_set_sd(bb_engine_t *e, int ok);
void bb_engine_set_selftest_failed(bb_engine_t *e, int failed);
/* Known-answer checks of the core itself. 0 = pass, else the failing step. */
int  bb_core_selftest(void);

#ifdef __cplusplus
}
#endif
#endif
