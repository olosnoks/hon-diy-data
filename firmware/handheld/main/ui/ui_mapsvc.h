/* The map worker and its hand-off with the UI.
 *
 * The UI never touches the SD card or draws the map itself. It posts a view
 * (centre, zoom, theme, trail, pins) with mapsvc_submit() and, on later ticks,
 * picks up a finished picture with mapsvc_take(). The worker, mapsvc_work(),
 * runs in the port's map task: a low-priority task on core 0 on the board
 * (port_map_esp32.c), called in place by the simulator. It owns the tile
 * store (components/hhmap), its LRU cache in PSRAM, and two 480 x 216 RGB565
 * canvases: one on the screen, one being drawn.
 *
 * Only the latest request counts: a request posted while one is being drawn
 * replaces any other waiting one. A finished picture waits until the UI takes
 * it, and the worker draws nothing new until then, so the canvas on the screen
 * is never written to.
 */
#ifndef UI_MAPSVC_H
#define UI_MAPSVC_H

#include <stdbool.h>
#include <stdint.h>
#include "hhmap.h"

#define MAPSVC_W 480
#define MAPSVC_H 216
#define MAPSVC_TRAIL 512
#define MAPSVC_PINS 32
#define MAPSVC_CACHE_BYTES (1536u * 1024u)

typedef enum {
    MAPSVC_NONE = 0,        /* nothing drawn yet */
    MAPSVC_OK,              /* a map is on the canvas */
    MAPSVC_NO_MAP,          /* no MAP folder on the card (err: HM_ERR_NOFILE) */
    MAPSVC_BAD_MAP,         /* there is one, but it cannot be read (err says why) */
    MAPSVC_NO_MEMORY,       /* the canvases or the cache did not fit */
    MAPSVC_UPDATING,        /* MAP/ is being replaced over the USB cable: its files are closed */
} mapsvc_status_t;

typedef struct { float lat, lon; } mapsvc_ll_t;
typedef struct { float lat, lon; uint8_t kind; } mapsvc_pin_t;      /* kind: HM_PIN_STALL / HM_PIN_MARK */

typedef struct {
    double lat, lon;        /* view centre */
    float mpp;              /* metres per pixel */
    int level;
    hm_palette_t pal;
    uint8_t night;
    int16_t inset[4];       /* label anchors: clear of the overlays (hm_view_t.inset) */
    uint16_t n_trail, n_pins;
    mapsvc_ll_t trail[MAPSVC_TRAIL];   /* oldest first */
    mapsvc_pin_t pins[MAPSVC_PINS];
} mapsvc_req_t;

typedef struct {
    mapsvc_status_t status;
    int err;                /* HM_ERR_* behind NO_MAP / BAD_MAP */
    uint32_t seq;           /* of the request it answers */
    const uint16_t *px;     /* MAPSVC_W x MAPSVC_H; valid until the next mapsvc_take() */
    double lat, lon;        /* its centre */
    float mpp;
    int level;
    uint8_t night;
    double kx, ky;          /* metres per degree of longitude / latitude: the map's own projection */
    double radius_m, lat0, lon0;
    hm_render_out_t out;    /* labels, timing */
    hm_stats_t stats;       /* the store's counters after this render */
    uint32_t render_us;     /* worker time for this picture, card reads included */
} mapsvc_result_t;

float mapsvc_mpp(int level);                        /* metres per pixel each detail level is drawn at */

/* UI side ----------------------------------------------------------------------- */
void mapsvc_init(void);                             /* once; cheap until the first submit */
uint32_t mapsvc_submit(const mapsvc_req_t *req);    /* returns the request's sequence number */
const mapsvc_result_t *mapsvc_take(void);           /* a new picture, or NULL */
bool mapsvc_busy(void);                             /* a request is waiting or being drawn */
mapsvc_req_t *mapsvc_scratch(void);                 /* a request to fill in (big: PSRAM on the board) */
int mapsvc_hold(int on);                            /* the laptop link is replacing MAP/ files: on = close them and stay off (0 = done, -1 = the worker
                                                     * is still drawing; ask again), off = open again at the next picture. Any task but the worker's. */
void mapsvc_reopen(void);                           /* close the MAP folder and open it again (another card) */

/* Worker side ------------------------------------------------------------------- */
/* Draw the latest request if there is one, else warm the cache around the last
 * view. Returns true if there may be more to do right away. */
bool mapsvc_work(void);

#endif
