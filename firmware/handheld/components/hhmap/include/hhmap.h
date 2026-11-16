/* hhmap: the handheld's offline road map. Portable C99: the same code runs on
 * the board (a low-priority task on core 0, see main/port/port_map_esp32.c),
 * in the PC simulator, and in the host tests.
 *
 *   hm_header_* / hm_tile_* / hm_iter_*   the format (MAPFORMAT.md)
 *   hm_store_*                            MAP.IDX + MAP.DAT on the card, an LRU tile cache
 *   hm_render                             one view into an RGB565 buffer, plus label candidates
 *
 * Nothing here is thread-safe: one store and one renderer belong to one task.
 */
#ifndef HHMAP_H
#define HHMAP_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HM_VERSION      1
#define HM_MAX_LEVELS   4
#define HM_Q            32768           /* coordinate units per tile side */
#define HM_HDR_SIZE     428             /* MAP.IDX header, CRC included */
#define HM_DAT_HDR_SIZE 16
#define HM_TILE_HDR     16

/* class codes (MAPFORMAT.md "Classes") */
enum {
    HM_MOTORWAY = 1, HM_TRUNK = 2, HM_PRIMARY = 3, HM_SECONDARY = 4, HM_TERTIARY = 5, HM_RESIDENTIAL = 6,
    HM_SERVICE = 7, HM_LINK_MAJOR = 8, HM_LINK = 9,
    HM_RAIL = 16, HM_RIVER = 20, HM_CANAL = 21, HM_WATER = 24,
    HM_CITY = 32, HM_TOWN = 33, HM_VILLAGE = 34, HM_HAMLET = 35, HM_SUBURB = 36, HM_FUEL = 40,
};
enum { HM_LINE = 0, HM_AREA = 1, HM_POINT = 2 };

/* errors (negative) */
enum {
    HM_OK = 0, HM_ERR_NOFILE = -1, HM_ERR_IO = -2, HM_ERR_MAGIC = -3, HM_ERR_VERSION = -4, HM_ERR_CRC = -5,
    HM_ERR_FORMAT = -6, HM_ERR_MISMATCH = -7, HM_ERR_NOMEM = -8,
};
const char *hm_strerror(int err);

uint32_t hm_crc32(uint32_t crc, const void *data, size_t len);     /* zlib's CRC-32 */

/* ---- header (MAP.IDX) -------------------------------------------------------- */

typedef struct {
    uint32_t tile_m;
    uint16_t cols, rows;
    double origin_x, origin_y;          /* metres: south-west corner of tile (0, 0) */
    uint32_t index_off, n_tiles;
    float tol_m;
    uint32_t data_bytes;
} hm_level_t;

typedef struct {
    uint16_t version;
    uint8_t n_levels;
    double lat0, lon0, kx, ky;          /* x = (lon - lon0) * kx, y = (lat - lat0) * ky, metres */
    double radius_m;
    double bbox[4];                     /* min_lat, min_lon, max_lat, max_lon */
    int64_t build_unix;
    uint32_t build_id;
    char build_date[20], sources[96], attribution[48];
    hm_level_t level[HM_MAX_LEVELS];
} hm_header_t;

int hm_header_parse(const uint8_t *buf, size_t len, hm_header_t *h);
void hm_project(const hm_header_t *h, double lat, double lon, double *x, double *y);
void hm_unproject(const hm_header_t *h, double x, double y, double *lat, double *lon);

/* ---- tiles --------------------------------------------------------------------- */

typedef struct {
    uint8_t level;
    uint16_t n_features, n_strings;
    const uint8_t *body;
    uint32_t body_len;
    const uint32_t *str_off;            /* n_strings offsets into body (the length byte) */
} hm_tile_t;

typedef struct {
    uint8_t cls, geom;
    uint16_t npts;
    const char *name;                   /* not NUL-terminated; NULL if none */
    uint8_t name_len;
    int16_t rank;                       /* -1 = none */
    uint8_t ref;                        /* the name is a route number ("I-55"): a shield */
    uint16_t n_rings;
    const uint8_t *ring_lens;           /* n_rings little-endian uint16 */
    const uint8_t *pts;                 /* npts (x, y) little-endian int16 pairs */
} hm_feature_t;

typedef struct { const hm_tile_t *t; uint32_t pos; uint16_t left; } hm_iter_t;

/* Check a tile (magic, CRC, every feature's bounds) and fill ``t``. ``str_off``
 * must hold n_strings entries: hm_tile_strings() tells how many before parsing. */
int hm_tile_strings(const uint8_t *data, size_t len);
int hm_tile_parse(const uint8_t *data, size_t len, hm_tile_t *t, uint32_t *str_off, int max_str);
void hm_iter_init(hm_iter_t *it, const hm_tile_t *t);
int hm_iter_next(hm_iter_t *it, hm_feature_t *f);          /* 1 = got one, 0 = end */
static inline int16_t hm_px(const uint8_t *pts, int i) { return (int16_t)(pts[4 * i] | pts[4 * i + 1] << 8); }
static inline int16_t hm_py(const uint8_t *pts, int i) { return (int16_t)(pts[4 * i + 2] | pts[4 * i + 3] << 8); }
static inline uint16_t hm_u16(const uint8_t *p, int i) { return (uint16_t)(p[2 * i] | p[2 * i + 1] << 8); }

/* ---- the store: files on the card and the tile cache ------------------------------ */

typedef struct {
    void *(*alloc)(size_t n);           /* NULL = malloc; the board puts the cache in PSRAM */
    void (*free)(void *p);
    int64_t (*micros)(void);            /* NULL = no timing */
} hm_env_t;

typedef struct {
    uint32_t hits, misses, empty, bytes_read, crc_errors, io_errors, evictions;
    uint32_t read_us, read_us_max;      /* time in fseek + fread for one tile */
    uint32_t cache_bytes, cache_entries;
} hm_stats_t;

typedef struct hm_store hm_store_t;

/* ``dir``: the MAP folder ("/sd/MAP"). Returns HM_OK or an error; *out is NULL on error. */
int hm_store_open(hm_store_t **out, const char *dir, size_t cache_bytes, const hm_env_t *env);
void hm_store_close(hm_store_t *s);
const hm_header_t *hm_store_header(const hm_store_t *s);
/* Tiles fetched between hm_store_frame() calls are never evicted by each
 * other: a view's tiles stay valid until the next frame. */
void hm_store_frame(hm_store_t *s);
/* NULL and *err = HM_OK: no tile there (empty, or off the grid). */
const hm_tile_t *hm_store_get(hm_store_t *s, int level, int col, int row, int *err);
int hm_store_cached(const hm_store_t *s, int level, int col, int row);   /* 1 = would not touch the card */
const hm_stats_t *hm_store_stats(const hm_store_t *s);

/* ---- rendering -------------------------------------------------------------------- */

/* RGB565 as the display takes it (lv_color_t 16-bit, native endian). */
typedef struct {
    uint16_t bg, road, water, rail, trail, stall, mark, place, fuel;
} hm_palette_t;

typedef struct { float x, y; } hm_pt_t;           /* projected metres */
enum { HM_PIN_STALL = 1, HM_PIN_MARK = 2 };
typedef struct { float x, y; uint8_t kind; } hm_pin_t;

typedef struct {
    double cx, cy;                      /* view centre, metres */
    float mpp;                          /* metres per pixel */
    int level;                          /* detail level to draw */
    const hm_palette_t *pal;
    const hm_pt_t *trail; int n_trail;  /* oldest first */
    const hm_pin_t *pins; int n_pins;
    int16_t inset[4];                   /* left, top, right, bottom: label anchors stay this far (px)
                                           inside the edges, clear of the UI's own overlays */
} hm_view_t;

#define HM_LABEL_LEN 32
#define HM_MAX_LABELS 24
enum { HM_LABEL_ROAD_REF = 1, HM_LABEL_ROAD_NAME = 2, HM_LABEL_PLACE = 3 };
typedef struct {
    char text[HM_LABEL_LEN];
    int16_t x, y;                       /* anchor, canvas pixels: road = segment midpoint, place = its dot */
    uint8_t kind, cls;
    uint16_t prio;                      /* higher wins a collision */
} hm_label_t;

typedef struct {
    int tiles, tiles_drawn, features, errors;
    uint32_t us_load, us_draw;
    int n_labels;
    hm_label_t labels[HM_MAX_LABELS];   /* best first */
} hm_render_out_t;

typedef struct { uint16_t *px; int w, h; } hm_canvas_t;
typedef struct hm_renderer hm_renderer_t;

hm_renderer_t *hm_renderer_new(const hm_env_t *env);
void hm_renderer_free(hm_renderer_t *r);
/* Draw the view (background included). Returns HM_OK; tile errors are
 * counted in out->errors and the tile is left blank. */
int hm_render(hm_renderer_t *r, hm_store_t *s, const hm_view_t *v, hm_canvas_t *c, hm_render_out_t *out);
/* The tiles a view needs, for prefetching: calls fn for each (level, col, row). */
void hm_view_tiles(const hm_header_t *h, const hm_view_t *v, int w, int h_px, int margin_tiles,
                   void (*fn)(void *ctx, int level, int col, int row), void *ctx);

/* Line width in pixels for a class at a level, 0 = not drawn (the style table). */
int hm_line_width(int cls, int level);

#ifdef __cplusplus
}
#endif
#endif
