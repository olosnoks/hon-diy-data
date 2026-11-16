/* hh_serial: the handheld's link to the laptop over the USB-C port (SERIAL.md).
 *
 * Portable C99 like the rest of hhcore: no UART and no OS in here. The
 * platform gives it a lock, drains hh_serial_poll() into the UART from a
 * low-priority task, and (two-way builds only) feeds what the UART receives
 * to hh_serial_rx().
 *
 *   engine --tap--> hh_serial_tap() --+
 *   any task ----> hh_serial_push() --+--> ring (oldest dropped when full)
 *   ESP_LOG -----> hh_serial_log() ---+         |
 *                                               v
 *                              hh_serial_poll(): COBS + CRC16 frames --> UART TX
 *
 *   UART RX --> hh_serial_rx(): frames --> commands (mark, files, clock ...)
 *
 * The frame is the black box's (firmware/blackbox/PROTOCOL.md):
 *   COBS( ver type seq payload[0..512] crc16_lo crc16_hi ) 0x00
 * with its own type numbers, so one deframer on the laptop reads both.
 *
 * Nothing in this file can reach the K-line: it has no transport. A command
 * asks the engine for something the engine already offers the button
 * (marker, acknowledge, trouble codes), or touches the card or the clock.
 */
#ifndef HH_SERIAL_H
#define HH_SERIAL_H

#include "hhcore.h"

#ifdef __cplusplus
extern "C" {
#endif

#define HH_SER_PROTO        1       /* the frame's version byte, shared with the black box */
#define HH_SER_REV          1       /* revision of the frame set below, in HELLO */
#define HH_SER_MAX_PAYLOAD  512
#define HH_SER_CHUNK        480     /* most file bytes in one frame */
#define HH_SER_RAW_MAX      (3 + HH_SER_MAX_PAYLOAD + 2)
#define HH_SER_FRAME_MAX    (HH_SER_RAW_MAX + HH_SER_RAW_MAX / 254 + 2)    /* encoded, with the 0x00 */
#define HH_SER_HELLO_S      2.0
#define HH_SER_STATS_S      1.0
#define HH_SER_HOST_S       3.0     /* a frame from the laptop this recently = "attached" */
#define HH_SER_LIST_PAGE    24

/* handheld -> laptop */
enum { HH_SF_HELLO = 0x40, HH_SF_READ = 0x41, HH_SF_TICK = 0x42, HH_SF_LINK = 0x43, HH_SF_GPS = 0x44,
       HH_SF_EVENT = 0x45, HH_SF_STATS = 0x46, HH_SF_LOG = 0x47, HH_SF_ISO = 0x48, HH_SF_TIME_RSP = 0x49,
       HH_SF_ACK = 0x4A, HH_SF_FILE_INFO = 0x4B, HH_SF_FILE_DATA = 0x4C, HH_SF_MAPUP = 0x4D };
/* laptop -> handheld (two-way builds only) */
enum { HH_SC_GET_HELLO = 0x60, HH_SC_TIME_REQ = 0x61, HH_SC_TIME_SET = 0x62, HH_SC_MARK = 0x63,
       HH_SC_ACK_ALARM = 0x64, HH_SC_DRIVE = 0x65, HH_SC_LIST_FILES = 0x66, HH_SC_READ_FILE = 0x67,
       HH_SC_PUT_BEGIN = 0x68, HH_SC_PUT_DATA = 0x69, HH_SC_PUT_END = 0x6A, HH_SC_DTC = 0x6B,
       HH_SC_MAPUP_OPEN = 0x6C, HH_SC_MAPUP_WRITE = 0x6D, HH_SC_MAPUP_STATUS = 0x6E, HH_SC_MAPUP_COMMIT = 0x6F };

enum { HH_SER_VARIANT_DEFAULT = 0, HH_SER_VARIANT_GPS_P1 = 1 };
#define HH_SER_CAP_RX       0x0001  /* commands are heard (two-way). Clear = tx-only */
#define HH_SER_CAP_FILES    0x0002  /* LIST_FILES / READ_FILE */
#define HH_SER_CAP_PUT      0x0004  /* PUT_* (HANDHELD.MAP, HANDHELD.CFG) */
#define HH_SER_CAP_LOG      0x0008  /* ESP_LOG lines arrive as LOG frames */
#define HH_SER_CAP_MAPUP    0x0010  /* MAPUP_* (files into the card's MAP/ folder) */

/* file kinds on the wire */
enum { HH_SER_F_DRIVE = 1, HH_SER_F_CAPTURE = 2, HH_SER_F_MAP = 3, HH_SER_F_CFG = 4 };
/* ACK status */
enum { HH_SER_OK = 0, HH_SER_IGNORED = 1, HH_SER_E_ARG = -1, HH_SER_E_IO = -2, HH_SER_E_CRC = -3,
       HH_SER_E_STATE = -4, HH_SER_E_REFUSED = -5, HH_SER_E_BUSY = -6, HH_SER_E_UNSUPPORTED = -7,
       HH_SER_E_OFFSET = -8 };

typedef struct HH_PACKED {
    char     magic[4];              /* "HHSL" */
    uint8_t  rev;                   /* HH_SER_REV */
    uint8_t  variant;               /* HH_SER_VARIANT_* */
    uint16_t caps;                  /* HH_SER_CAP_* */
    char     fw[12], device[12], map_hash[12];
    uint8_t  map_sha[8];            /* first 8 bytes of the map text's sha256 */
    uint8_t  ecu_id[10], ecu_id_len;
    uint8_t  link_state;            /* HH_LINK_* */
    uint8_t  backend, link_mode;    /* as in a drive file's header */
    uint8_t  n_ch;
    uint8_t  flags;                 /* 1 built-in map, 2 clock set, 4 clock from GPS, 8 card ok */
    uint32_t boot_no, baud;
    uint64_t t_us;                  /* device time now */
    int64_t  unix_us;               /* wall clock now, 0 = not known */
    uint16_t hello_ms, max_payload;
} hh_ser_hello_t;                   /* 96 bytes */

#define HH_SER_ST_SD_OK     0x01
#define HH_SER_ST_RECORDING 0x02
#define HH_SER_ST_GPS_FIX   0x04
#define HH_SER_ST_TIME      0x08
#define HH_SER_ST_ARMED     0x10
#define HH_SER_ST_CAPTURING 0x20
#define HH_SER_ST_HOST      0x40
#define HH_SER_ST_BUILTIN   0x80

typedef struct HH_PACKED {
    uint64_t t_us;
    int64_t  unix_us;
    uint32_t reads, errors, timeouts, checksum_failures, retries, reconnects;   /* the K-line, since boot */
    uint16_t hz_c;                  /* fast cycle rate x 100 */
    uint8_t  link_state, flags;     /* HH_LINK_*, HH_SER_ST_* */
    uint32_t drive_no, log_bytes, free_mb, markers, stall_count, nmea_good, nmea_bad;
    uint32_t tx_frames, tx_dropped, tx_bytes, rx_frames, rx_bad, ring_peak;     /* this link, since boot */
} hh_ser_stats_t;                   /* 96 bytes */

typedef struct HH_PACKED { int64_t host_tx_us; uint64_t dev_rx_us, dev_tx_us; } hh_ser_time_rsp_t;     /* 24 */
typedef struct HH_PACKED { uint8_t cmd, seq; int16_t status; uint32_t value; } hh_ser_ack_t;           /* 8 */
#define HH_SER_FI_OPEN 0x01         /* being written: cannot be read yet */
#define HH_SER_FI_MORE 0x02         /* on the end record: ask again from the last number */
typedef struct HH_PACKED { uint8_t kind, flags; uint16_t rsv; uint32_t file_no, size; } hh_ser_file_info_t;   /* 12 */
#define HH_SER_FD_EOF  0x01
#define HH_SER_FD_BUSY 0x02
typedef struct HH_PACKED { uint8_t kind, flags; uint16_t len; uint32_t file_no, offset, crc32; } hh_ser_file_data_t; /* 16 + data */
typedef struct HH_PACKED { uint8_t kind, rsv[3]; uint32_t after_no; } hh_ser_list_t;                    /* 8 */
typedef struct HH_PACKED { uint8_t kind, rsv; uint16_t len; uint32_t file_no, offset; } hh_ser_read_file_t;   /* 12 */
typedef struct HH_PACKED { uint8_t kind, rsv[3]; uint32_t size, crc32; } hh_ser_put_begin_t;            /* 12 */

/* Files into MAP/ (SERIAL.md, "Map upload"). The name is "MAP/" and an 8.3 name. */
#define HH_SER_MAPUP_CHUNK      (HH_SER_MAX_PAYLOAD - 8)    /* most file bytes in one WRITE */
#define HH_SER_MAPUP_NAME_MAX   16
#define HH_SER_MAPUP_SIZE_MAX   (128u * 1024u * 1024u)
#define HH_SER_MAPUP_IDLE_S     10.0    /* no MAPUP frame this long: the map reader gets its files back; the upload can still be carried on */
#define HH_SER_MAPUP_TMP        "UPLOAD.TMP"    /* in MAP/; a name of that kind is refused */
#define HH_SER_MAPUP_FINAL     0x01     /* COMMIT: the last file; let the map reader reopen MAP/ at once */
#define HH_SER_MAPUP_ABORT     0x02     /* COMMIT: drop the file being written, and let go of MAP/ */
typedef struct HH_PACKED { uint8_t flags, name_len; uint16_t rsv; uint32_t size, crc32; } hh_ser_mapup_open_t;   /* 12 + name */
typedef struct HH_PACKED { uint32_t offset, crc32; } hh_ser_mapup_write_t;                                      /* 8 + data */
typedef struct HH_PACKED { uint8_t flags, rsv[3]; } hh_ser_mapup_commit_t;                                      /* 4 */
#define HH_SER_MU_NONE   0      /* hh_ser_mapup_info_t.state: no upload */
#define HH_SER_MU_OPEN   1      /*   one is open, the map reader is held off */
#define HH_SER_MU_PAUSED 2      /*   one is waiting for its next chunk; the map reader has its files */
/* the answer to MAPUP_STATUS (before its ACK): which file, so that a laptop that starts again knows where it stopped */
typedef struct HH_PACKED { uint8_t state, rsv1; uint16_t rsv2; uint32_t got, size, crc32; char name[HH_SER_MAPUP_NAME_MAX]; } hh_ser_mapup_info_t;  /* 32 */

/* The card, as the commands need it. Every pointer may be NULL (no card). */
typedef struct {
    void *ctx;
    /* Up to max files of this kind with a number above after_no, lowest first. Returns the count; *more = 1 if there are others. */
    int (*list)(void *ctx, uint8_t kind, uint32_t after_no, hh_ser_file_info_t *out, int max, int *more);
    /* Up to len bytes at offset. Returns the count (0 = end), HH_SER_E_ARG = no such file, HH_SER_E_BUSY, HH_SER_E_IO. */
    int (*read)(void *ctx, uint8_t kind, uint32_t file_no, uint32_t offset, uint8_t *buf, uint16_t len, uint32_t *size);
    int (*put_begin)(void *ctx, uint8_t kind, uint32_t size);
    int (*put_data)(void *ctx, uint32_t offset, const uint8_t *data, uint16_t len);
    /* Check the temporary file (length, crc32, and that a map loads) and move it into place. *value: lines skipped (cfg). */
    int (*put_end)(void *ctx, uint8_t kind, uint32_t size, uint32_t crc, uint32_t *value);
    /* MAP/ uploads: name is "MAP/xxxxxxxx.xxx", already checked. open: keep = 0 starts MAP/UPLOAD.TMP
     * (replacing any left over); keep > 0 opens the one there again, which must hold at least that many
     * bytes. write puts data at offset (always the next byte). pause closes the file and leaves it.
     * commit reads the temporary file back from the card, checks its length and CRC32 and only then
     * renames it over name; any failure removes it. abort closes and removes it. */
    int (*mapup_open)(void *ctx, const char *name, uint32_t size, uint32_t keep);
    int (*mapup_write)(void *ctx, uint32_t offset, const uint8_t *data, uint16_t len);
    int (*mapup_commit)(void *ctx, const char *name, uint32_t size, uint32_t crc);
    void (*mapup_pause)(void *ctx);
    void (*mapup_abort)(void *ctx);
} hh_serial_fs_t;

typedef struct {
    hh_core_t *core;                /* NULL until hh_serial_attach */
    hh_clock_t clk;
    void (*lock)(void *ctx);        /* guards the ring; may be NULL */
    void (*unlock)(void *ctx);
    void *lock_ctx;
    void (*wake)(void *ctx);        /* a frame is waiting: the TX task may stop sleeping. May be NULL */
    hh_serial_fs_t fs;
    /* The map reader keeps MAP/MAP.IDX and MAP/MAP.DAT open. Called from the command task when a
     * MAP/ upload starts (on = 1: close them and stay off the folder; returns 0 once they are closed,
     * non-zero if that did not happen in time) and when it ends (on = 0: may open them again). May be NULL. */
    int (*map_hold)(int on);
    hh_ring_t ring;
    uint8_t  item[2 + HH_SER_MAX_PAYLOAD];  /* a frame being queued (under the lock): off the producers' stacks */
    uint8_t  seq, variant;
    uint16_t caps;
    uint32_t baud;
    double   next_hello, next_stats, last_rx;   /* last_rx < 0 = never */
    uint32_t tx_frames, tx_dropped, tx_bytes, rx_frames, rx_bad, ring_peak;
    /* receive */
    uint8_t  rx[HH_SER_FRAME_MAX];
    uint16_t rx_n;
    uint8_t  rx_skip;               /* an over-long run: drop up to the next 0x00 */
    /* a file being pushed */
    uint8_t  put_kind;              /* 0 = none */
    uint32_t put_size, put_crc, put_got;
    /* a MAP/ file being uploaded */
    uint8_t  mapup_active, mapup_held;      /* an upload exists / its file is open and the map reader has been told to keep off */
    char     mapup_name[HH_SER_MAPUP_NAME_MAX + 1];
    uint32_t mapup_size, mapup_crc, mapup_got;
    double   mapup_t;                       /* time of the last MAPUP frame */
} hh_serial_t;

/* mem/size: the ring (16 KB or more). caps without HH_SER_CAP_RX = tx-only. */
void hh_serial_init(hh_serial_t *s, const hh_clock_t *clk, uint8_t *mem, uint32_t size, uint8_t variant,
                    uint16_t caps, uint32_t baud);
void hh_serial_attach(hh_serial_t *s, hh_core_t *core);
/* The engine's tap (hh_platform_t.tap, with tap_ctx = the hh_serial_t). */
void hh_serial_tap(void *ctx, uint8_t kind, const void *rec, size_t len, const void *data, size_t data_len);
/* One frame into the ring, from any task. When there is no room the oldest
 * frames go, and are counted. Returns 0, or -1 if it can never fit. */
int  hh_serial_push(hh_serial_t *s, uint8_t type, const void *head, size_t head_len, const void *data, size_t data_len);
/* A log line (or several), cut into LOG frames. */
void hh_serial_log(hh_serial_t *s, const char *text, size_t len);
/* TX task: whole encoded frames into out (cap >= HH_SER_FRAME_MAX), as many
 * as fit. Also where HELLO and STATS come from when they are due. Returns the bytes written. */
size_t hh_serial_poll(hh_serial_t *s, uint8_t *out, size_t cap);
/* RX task (two-way builds): bytes from the laptop. */
void hh_serial_rx(hh_serial_t *s, const uint8_t *data, size_t len);
/* Command task, after each read and each timeout: a MAP/ upload that has gone quiet for HH_SER_MAPUP_IDLE_S
 * gives the map reader its files back. The temporary file stays, and the next chunk carries on. */
void hh_serial_tick(hh_serial_t *s);
/* 1 while the laptop has been heard within HH_SER_HOST_S. */
int  hh_serial_host_attached(const hh_serial_t *s);

uint16_t hh_crc16(const uint8_t *data, size_t len);                     /* CRC-16/CCITT-FALSE */
size_t   hh_cobs_encode(const uint8_t *in, size_t len, uint8_t *out);   /* out >= len + len / 254 + 1 */
int      hh_cobs_decode(const uint8_t *in, size_t len, uint8_t *out);   /* bytes, or -1; out >= len */
/* ver type seq payload crc, encoded, then 0x00. Returns the bytes written (<= HH_SER_FRAME_MAX). */
size_t   hh_ser_frame(uint8_t type, uint8_t seq, const uint8_t *payload, size_t len, uint8_t *out);

/* The card through stdio under a root folder ("/sd"): port/hh_serfs.c. */
typedef struct { char root[200]; hh_core_t *core; void *fh; uint8_t kind; uint32_t file_no, size; void *put; void *mup; } hh_serfs_t;
void hh_serfs_init(hh_serfs_t *fs, const char *root, hh_core_t *core, hh_serial_fs_t *ops);

#ifdef __cplusplus
}
#endif
#endif
