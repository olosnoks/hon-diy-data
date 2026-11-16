/* MAP.IDX + MAP.DAT through stdio, and an LRU cache of checked tiles.
 *
 * A tile is read with one fseek + fread of the 8-byte index entry and one of
 * the tile itself, checked (CRC and bounds) once, and kept until the cache
 * needs the room. Empty tiles are remembered too, so open country costs one
 * index read per tile, once. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hhmap.h"

#define MAX_ENTRIES 512

typedef struct {
    int16_t level, col, row;            /* level -1 = free slot */
    uint32_t used, frame;
    size_t bytes;                       /* 0 = an empty tile */
    uint8_t *data;                      /* tile bytes, then the string offsets */
    hm_tile_t tile;
} entry_t;

struct hm_store {
    FILE *idx, *dat;
    hm_header_t hdr;
    hm_env_t env;
    size_t budget, used_bytes;
    uint32_t clock, frame;
    hm_stats_t stats;
    entry_t e[MAX_ENTRIES];
};

static void *env_alloc(const hm_env_t *env, size_t n) { return env->alloc ? env->alloc(n) : malloc(n); }
static void env_free(const hm_env_t *env, void *p) { if (p) { if (env->free) env->free(p); else free(p); } }
static int64_t env_us(const hm_env_t *env) { return env->micros ? env->micros() : 0; }

static FILE *open_in(const char *dir, const char *name)
{
    char path[160];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    return fopen(path, "rb");
}

static int read_at(FILE *f, uint32_t off, void *buf, size_t n)
{
    if (fseek(f, (long)off, SEEK_SET) != 0) return HM_ERR_IO;
    return fread(buf, 1, n, f) == n ? HM_OK : HM_ERR_IO;
}

int hm_store_open(hm_store_t **out, const char *dir, size_t cache_bytes, const hm_env_t *env)
{
    static const hm_env_t none = {0};
    *out = NULL;
    if (!env) env = &none;
    hm_store_t *s = env_alloc(env, sizeof(*s));
    if (!s) return HM_ERR_NOMEM;
    memset(s, 0, sizeof(*s));
    s->env = *env;
    s->budget = cache_bytes;
    for (int i = 0; i < MAX_ENTRIES; i++) s->e[i].level = -1;

    int err = HM_ERR_NOFILE;
    uint8_t hb[HM_HDR_SIZE], db[HM_DAT_HDR_SIZE];
    s->idx = open_in(dir, "MAP.IDX");
    s->dat = open_in(dir, "MAP.DAT");
    if (!s->idx || !s->dat) goto fail;
    err = HM_ERR_FORMAT;
    if (fread(hb, 1, sizeof(hb), s->idx) < 16) goto fail;
    if ((err = hm_header_parse(hb, sizeof(hb), &s->hdr)) != HM_OK) goto fail;
    err = HM_ERR_MAGIC;
    if (fread(db, 1, sizeof(db), s->dat) != sizeof(db) || memcmp(db, "HHMAPDAT", 8) != 0) goto fail;
    err = HM_ERR_VERSION;
    if ((db[8] | db[9] << 8) != HM_VERSION) goto fail;
    err = HM_ERR_MISMATCH;
    if (((uint32_t)db[12] | (uint32_t)db[13] << 8 | (uint32_t)db[14] << 16 | (uint32_t)db[15] << 24) != s->hdr.build_id) goto fail;
    *out = s;
    return HM_OK;
fail:
    if (s->idx) fclose(s->idx);
    if (s->dat) fclose(s->dat);
    env_free(env, s);
    return err;
}

void hm_store_close(hm_store_t *s)
{
    if (!s) return;
    for (int i = 0; i < MAX_ENTRIES; i++) env_free(&s->env, s->e[i].data);
    fclose(s->idx);
    fclose(s->dat);
    hm_env_t env = s->env;
    env_free(&env, s);
}

const hm_header_t *hm_store_header(const hm_store_t *s) { return &s->hdr; }
const hm_stats_t *hm_store_stats(const hm_store_t *s) { return &s->stats; }
void hm_store_frame(hm_store_t *s) { s->frame++; }

static entry_t *find(const hm_store_t *s, int level, int col, int row)
{
    for (int i = 0; i < MAX_ENTRIES; i++) {
        const entry_t *e = &s->e[i];
        if (e->level == level && e->col == col && e->row == row) return (entry_t *)e;
    }
    return NULL;
}

int hm_store_cached(const hm_store_t *s, int level, int col, int row) { return find(s, level, col, row) != NULL; }

static void drop(hm_store_t *s, entry_t *e)
{
    env_free(&s->env, e->data);
    s->used_bytes -= e->bytes;
    s->stats.cache_bytes = (uint32_t)s->used_bytes;
    s->stats.cache_entries--;
    memset(e, 0, sizeof(*e));
    e->level = -1;
}

/* A free slot with room for ``bytes``: evict least recently used tiles that
 * the current frame has not touched. NULL if the frame alone fills the cache. */
static entry_t *make_room(hm_store_t *s, size_t bytes)
{
    for (;;) {
        entry_t *free_slot = NULL, *old = NULL;
        for (int i = 0; i < MAX_ENTRIES; i++) {
            entry_t *e = &s->e[i];
            if (e->level < 0) { if (!free_slot) free_slot = e; continue; }
            if (e->frame == s->frame) continue;
            if (!old || e->used < old->used) old = e;
        }
        if (free_slot && s->used_bytes + bytes <= s->budget) return free_slot;
        if (!old) return NULL;
        drop(s, old);
        s->stats.evictions++;
    }
}

const hm_tile_t *hm_store_get(hm_store_t *s, int level, int col, int row, int *err)
{
    *err = HM_OK;
    if (level < 0 || level >= s->hdr.n_levels) return NULL;
    const hm_level_t *l = &s->hdr.level[level];
    if (col < 0 || row < 0 || col >= l->cols || row >= l->rows) return NULL;

    entry_t *e = find(s, level, col, row);
    if (e) {
        e->used = ++s->clock;
        e->frame = s->frame;
        if (e->bytes) s->stats.hits++;
        return e->bytes ? &e->tile : NULL;
    }

    int64_t t0 = env_us(&s->env);
    uint8_t ent[8];
    uint32_t off = 0, len = 0;
    *err = read_at(s->idx, l->index_off + ((uint32_t)row * l->cols + (uint32_t)col) * 8u, ent, 8);
    if (*err) { s->stats.io_errors++; return NULL; }
    off = (uint32_t)ent[0] | (uint32_t)ent[1] << 8 | (uint32_t)ent[2] << 16 | (uint32_t)ent[3] << 24;
    len = (uint32_t)ent[4] | (uint32_t)ent[5] << 8 | (uint32_t)ent[6] << 16 | (uint32_t)ent[7] << 24;

    if (!len) {                                     /* remember the empty tile */
        e = make_room(s, 0);
        if (e) {
            e->level = (int16_t)level; e->col = (int16_t)col; e->row = (int16_t)row;
            e->used = ++s->clock; e->frame = s->frame; e->bytes = 0; e->data = NULL;
            s->stats.cache_entries++;
        }
        s->stats.empty++;
        return NULL;
    }
    if (len < HM_TILE_HDR || len > 4u << 20) { *err = HM_ERR_FORMAT; s->stats.crc_errors++; return NULL; }

    uint8_t head[HM_TILE_HDR];
    if ((*err = read_at(s->dat, off, head, sizeof(head))) != HM_OK) { s->stats.io_errors++; return NULL; }
    int n_str = hm_tile_strings(head, sizeof(head));
    if (n_str < 0) { *err = n_str; s->stats.crc_errors++; return NULL; }
    size_t str_at = (len + 3u) & ~3u, bytes = str_at + (size_t)n_str * sizeof(uint32_t);
    e = make_room(s, bytes);
    uint8_t *data = e ? env_alloc(&s->env, bytes ? bytes : 1) : NULL;
    if (!data) { *err = HM_ERR_NOMEM; return NULL; }
    memcpy(data, head, sizeof(head));
    if (fread(data + sizeof(head), 1, len - sizeof(head), s->dat) != len - sizeof(head)) {
        env_free(&s->env, data);
        *err = HM_ERR_IO;
        s->stats.io_errors++;
        return NULL;
    }
    uint32_t us = (uint32_t)(env_us(&s->env) - t0);
    s->stats.read_us += us;
    if (us > s->stats.read_us_max) s->stats.read_us_max = us;
    s->stats.bytes_read += len;
    s->stats.misses++;

    *err = hm_tile_parse(data, len, &e->tile, (uint32_t *)(void *)(data + str_at), n_str);
    if (*err) {
        env_free(&s->env, data);
        memset(&e->tile, 0, sizeof(e->tile));
        s->stats.crc_errors++;
        return NULL;
    }
    e->level = (int16_t)level; e->col = (int16_t)col; e->row = (int16_t)row;
    e->used = ++s->clock; e->frame = s->frame;
    e->bytes = bytes; e->data = data;
    s->used_bytes += bytes;
    s->stats.cache_bytes = (uint32_t)s->used_bytes;
    s->stats.cache_entries++;
    return &e->tile;
}
