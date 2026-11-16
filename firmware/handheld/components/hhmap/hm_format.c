/* The MAP format: header, tiles, features. Layout: MAPFORMAT.md. */
#include <math.h>
#include <string.h>

#include "hhmap.h"

const char *hm_strerror(int err)
{
    switch (err) {
    case HM_OK: return "ok";
    case HM_ERR_NOFILE: return "no MAP folder";
    case HM_ERR_IO: return "read error";
    case HM_ERR_MAGIC: return "not a map file";
    case HM_ERR_VERSION: return "map format too new";
    case HM_ERR_CRC: return "checksum wrong";
    case HM_ERR_FORMAT: return "damaged";
    case HM_ERR_MISMATCH: return "MAP.IDX and MAP.DAT differ";
    case HM_ERR_NOMEM: return "out of memory";
    default: return "?";
    }
}

uint32_t hm_crc32(uint32_t crc, const void *data, size_t len)
{
    static uint32_t table[256];
    static int ready;
    if (!ready) {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            table[i] = c;
        }
        ready = 1;
    }
    const uint8_t *p = data;
    crc = ~crc;
    while (len--) crc = table[(crc ^ *p++) & 0xFF] ^ (crc >> 8);
    return ~crc;
}

static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static uint64_t rd64(const uint8_t *p) { return (uint64_t)rd32(p) | (uint64_t)rd32(p + 4) << 32; }
static double rdf64(const uint8_t *p) { uint64_t v = rd64(p); double d; memcpy(&d, &v, 8); return d; }
static float rdf32(const uint8_t *p) { uint32_t v = rd32(p); float f; memcpy(&f, &v, 4); return f; }

static void cstr(char *dst, const uint8_t *src, size_t n)
{
    memcpy(dst, src, n);
    dst[n - 1] = 0;
}

/* Header layout (little-endian, packed):
 *   0  char[8] "HHMAPIDX"   8 u16 version  10 u16 header length  12 u8 levels  13 u8 projection  14 u16 0
 *  16  f64 lat0 lon0 kx ky radius_m min_lat min_lon max_lat max_lon     (9 x 8)
 *  88  i64 build_unix   96 u32 build_id   100 char[20] date   120 char[96] sources   216 char[48] attribution
 * 264  4 x level { u32 tile_m, u16 cols, u16 rows, f64 origin_x, f64 origin_y, u32 index_off,
 *                  u32 n_tiles, f32 tol_m, u32 data_bytes }                             (40 each)
 * 424  u32 CRC-32 of bytes 0..423 */
int hm_header_parse(const uint8_t *b, size_t len, hm_header_t *h)
{
    memset(h, 0, sizeof(*h));
    if (len < 16 || memcmp(b, "HHMAPIDX", 8) != 0) return HM_ERR_MAGIC;
    h->version = rd16(b + 8);
    if (h->version != HM_VERSION) return HM_ERR_VERSION;
    if (rd16(b + 10) != HM_HDR_SIZE || len < HM_HDR_SIZE) return HM_ERR_FORMAT;
    if (hm_crc32(0, b, HM_HDR_SIZE - 4) != rd32(b + HM_HDR_SIZE - 4)) return HM_ERR_CRC;
    h->n_levels = b[12];
    if (h->n_levels < 1 || h->n_levels > HM_MAX_LEVELS || b[13] != 0) return HM_ERR_FORMAT;
    h->lat0 = rdf64(b + 16); h->lon0 = rdf64(b + 24); h->kx = rdf64(b + 32); h->ky = rdf64(b + 40);
    h->radius_m = rdf64(b + 48);
    for (int i = 0; i < 4; i++) h->bbox[i] = rdf64(b + 56 + 8 * i);
    h->build_unix = (int64_t)rd64(b + 88);
    h->build_id = rd32(b + 96);
    cstr(h->build_date, b + 100, 20);
    cstr(h->sources, b + 120, 96);
    cstr(h->attribution, b + 216, 48);
    if (!(h->kx > 1000 && h->ky > 1000)) return HM_ERR_FORMAT;
    for (int i = 0; i < h->n_levels; i++) {
        const uint8_t *p = b + 264 + 40 * i;
        hm_level_t *l = &h->level[i];
        l->tile_m = rd32(p); l->cols = rd16(p + 4); l->rows = rd16(p + 6);
        l->origin_x = rdf64(p + 8); l->origin_y = rdf64(p + 16);
        l->index_off = rd32(p + 24); l->n_tiles = rd32(p + 28); l->tol_m = rdf32(p + 32); l->data_bytes = rd32(p + 36);
        if (!l->tile_m || !l->cols || !l->rows || l->index_off < HM_HDR_SIZE) return HM_ERR_FORMAT;
    }
    return HM_OK;
}

void hm_project(const hm_header_t *h, double lat, double lon, double *x, double *y)
{
    *x = (lon - h->lon0) * h->kx;
    *y = (lat - h->lat0) * h->ky;
}

void hm_unproject(const hm_header_t *h, double x, double y, double *lat, double *lon)
{
    *lat = h->lat0 + y / h->ky;
    *lon = h->lon0 + x / h->kx;
}

/* ---- tiles ----------------------------------------------------------------------
 * header: "HT", u8 level, u8 version, u16 n_features, u16 n_strings, u32 body_len,
 *         u32 crc (of the header's first 12 bytes, then the body)
 * body:   n_strings x { u8 len, bytes }, then the features:
 *         u8 class, u8 flags (bits 0-1 geometry, bit 2 named, bit 3 ranked, bit 4 ref), u16 npts,
 *         [u16 name index], [u8 rank], area: u16 n_rings, n_rings x u16 ring length,
 *         npts x (int16 x, int16 y) */

int hm_tile_strings(const uint8_t *data, size_t len)
{
    if (len < HM_TILE_HDR || data[0] != 'H' || data[1] != 'T') return HM_ERR_MAGIC;
    return rd16(data + 6);
}

int hm_tile_parse(const uint8_t *data, size_t len, hm_tile_t *t, uint32_t *str_off, int max_str)
{
    memset(t, 0, sizeof(*t));
    if (len < HM_TILE_HDR || data[0] != 'H' || data[1] != 'T') return HM_ERR_MAGIC;
    if (data[3] != HM_VERSION) return HM_ERR_VERSION;
    t->level = data[2];
    t->n_features = rd16(data + 4);
    t->n_strings = rd16(data + 6);
    t->body_len = rd32(data + 8);
    if ((size_t)t->body_len + HM_TILE_HDR > len || t->n_strings > max_str) return HM_ERR_FORMAT;
    t->body = data + HM_TILE_HDR;
    if (hm_crc32(hm_crc32(0, data, 12), t->body, t->body_len) != rd32(data + 12)) return HM_ERR_CRC;

    /* walk the whole tile once: drawing then trusts the bounds */
    uint32_t pos = 0, end = t->body_len;
    const uint8_t *b = t->body;
    for (int i = 0; i < t->n_strings; i++) {
        if (pos >= end || pos + 1 + b[pos] > end) return HM_ERR_FORMAT;
        str_off[i] = pos;
        pos += 1u + b[pos];
    }
    t->str_off = str_off;
    for (int i = 0; i < t->n_features; i++) {
        if (pos + 4 > end) return HM_ERR_FORMAT;
        uint8_t flags = b[pos + 1];
        uint32_t npts = rd16(b + pos + 2);
        pos += 4;
        if ((flags & 3) > HM_POINT || !npts) return HM_ERR_FORMAT;
        if (flags & 4) {
            if (pos + 2 > end || rd16(b + pos) >= t->n_strings) return HM_ERR_FORMAT;
            pos += 2;
        }
        if (flags & 8) pos += 1;
        if ((flags & 3) == HM_AREA) {
            if (pos + 2 > end) return HM_ERR_FORMAT;
            uint32_t nr = rd16(b + pos), sum = 0;
            if (!nr || pos + 2 + 2 * nr > end) return HM_ERR_FORMAT;
            for (uint32_t k = 0; k < nr; k++) sum += rd16(b + pos + 2 + 2 * k);
            if (sum != npts) return HM_ERR_FORMAT;
            pos += 2 + 2 * nr;
        }
        if (pos + 4 * npts > end) return HM_ERR_FORMAT;
        pos += 4 * npts;
    }
    return pos == end ? HM_OK : HM_ERR_FORMAT;
}

void hm_iter_init(hm_iter_t *it, const hm_tile_t *t)
{
    it->t = t;
    it->left = t->n_features;
    it->pos = t->n_strings ? t->str_off[t->n_strings - 1] + 1u + t->body[t->str_off[t->n_strings - 1]] : 0;
}

int hm_iter_next(hm_iter_t *it, hm_feature_t *f)
{
    if (!it->left) return 0;
    it->left--;
    const uint8_t *b = it->t->body;
    uint32_t pos = it->pos;
    f->cls = b[pos];
    uint8_t flags = b[pos + 1];
    f->geom = flags & 3;
    f->npts = rd16(b + pos + 2);
    pos += 4;
    f->name = NULL;
    f->name_len = 0;
    f->rank = -1;
    f->ref = (flags & 16) != 0;
    if (flags & 4) {
        uint32_t so = it->t->str_off[rd16(b + pos)];
        f->name_len = b[so];
        f->name = (const char *)b + so + 1;
        pos += 2;
    }
    if (flags & 8) f->rank = b[pos++];
    f->n_rings = 1;
    f->ring_lens = NULL;
    if (f->geom == HM_AREA) {
        f->n_rings = rd16(b + pos);
        f->ring_lens = b + pos + 2;
        pos += 2u + 2u * f->n_rings;
    }
    f->pts = b + pos;
    it->pos = pos + 4u * f->npts;
    return 1;
}
