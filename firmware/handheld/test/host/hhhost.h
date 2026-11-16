/* Host harness for hhcore: the engine with stdio files under a folder, and
 * the K-line and the clock supplied by the caller (the C simulator in
 * hhsim.c, or the Python tests through ctypes). */
#ifndef HHHOST_H
#define HHHOST_H

#include "hhcore.h"

typedef struct {
    int    (*open)(void);
    void   (*close)(void);
    int    (*write)(const uint8_t *data, size_t len);
    int    (*read_some)(uint8_t *buf, size_t max, double timeout_s);
    void   (*flush_input)(void);
    int    (*set_baud)(uint32_t baud);
    int    (*set_break)(int on);
    double (*now)(void);
    void   (*sleep)(double seconds);
    double usb_latency_s;
} hhhost_cb_t;

typedef struct hhhost hhhost_t;

/* map == NULL: the built-in default map. cfg_text may be NULL. */
hhhost_t *hhhost_new(const char *sd_root, const uint8_t *map, size_t map_len, const char *cfg_text,
                     const hhhost_cb_t *cb, uint32_t boot_no, uint32_t last_drive_no);
void hhhost_free(hhhost_t *h);
hh_core_t *hhhost_core(hhhost_t *h);
/* n engine steps, the storage drained after each. */
void hhhost_run(hhhost_t *h, int steps);
/* Steps until the clock reaches t. */
void hhhost_run_until(hhhost_t *h, double t);
void hhhost_gps(hhhost_t *h, const char *nmea);
void hhhost_snapshot(hhhost_t *h, hh_snapshot_t *out);
/* After this many more bytes every file write is lost (power cut). <0 = no limit. */
void hhhost_set_write_budget(hhhost_t *h, long bytes);
/* Preallocated space is filled with the bytes of this file (an older drive's leftovers). */
void hhhost_set_stale(hhhost_t *h, const char *path);
int64_t hhhost_time_set(const hhhost_t *h);         /* what the engine last set the clock to, 0 = never */
uint32_t hhhost_syncs(const hhhost_t *h);
size_t hhhost_default_map(const uint8_t **out);

#endif
