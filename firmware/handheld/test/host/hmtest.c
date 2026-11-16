/* Host tests for components/hhmap: the C reader against a MAP the Python
 * builder made (fixtures/tinymap, from tests/fixtures/osmmap/tiny.osm; the
 * facts to check are in its expect.txt), damage, the cache, and rendering.
 *
 *   make hmtest && build/hmtest          (run from test/host)
 */
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "hhmap.h"

#define FIX "fixtures/tinymap"

static int failures, checks;
#define CHECK(cond, ...) do { checks++; if (!(cond)) { failures++; printf("  FAIL %s:%d: ", __FILE__, __LINE__); \
    printf(__VA_ARGS__); printf("\n"); } } while (0)

static int64_t now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

static const hm_env_t ENV = {NULL, NULL, now_us};

/* ---- helpers ------------------------------------------------------------------------- */

static long file_size(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0 ? (long)st.st_size : -1;
}

static int copy_file(const char *from, const char *to)
{
    FILE *a = fopen(from, "rb"), *b = fopen(to, "wb");
    if (!a || !b) { if (a) fclose(a); if (b) fclose(b); return -1; }
    char buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), a)) > 0) fwrite(buf, 1, n, b);
    fclose(a);
    fclose(b);
    return 0;
}

static void poke(const char *path, long off, uint8_t xor_with)
{
    FILE *f = fopen(path, "r+b");
    if (!f) return;
    fseek(f, off, SEEK_SET);
    int c = fgetc(f);
    fseek(f, off, SEEK_SET);
    fputc(c ^ xor_with, f);
    fclose(f);
}

/* a scratch copy of the fixture: returns the MAP folder's path */
static const char *scratch_copy(char *dir, size_t n)
{
    static int k;
    snprintf(dir, n, "/tmp/hmtest-%d-%d", (int)getpid(), k++);
    mkdir(dir, 0777);
    char a[256], b[256];
    for (int i = 0; i < 2; i++) {
        const char *name = i ? "MAP.DAT" : "MAP.IDX";
        snprintf(a, sizeof(a), FIX "/MAP/%s", name);
        snprintf(b, sizeof(b), "%s/%s", dir, name);
        copy_file(a, b);
    }
    return dir;
}

/* ---- the reader against expect.txt ---------------------------------------------------------- */

static void test_crc(void)
{
    CHECK(hm_crc32(0, "123456789", 9) == 0xCBF43926u, "crc32 check value");
    CHECK(hm_crc32(hm_crc32(0, "1234", 4), "56789", 5) == 0xCBF43926u, "crc32 continues");
}

static void hex_to_str(const char *hex, char *out, size_t n)
{
    size_t i = 0;
    for (; hex[2 * i] && hex[2 * i + 1] && i + 1 < n; i++) {
        unsigned v;
        sscanf(hex + 2 * i, "%2x", &v);
        out[i] = (char)v;
    }
    out[i] = 0;
}

static void test_fixture(void)
{
    hm_store_t *s;
    int err = hm_store_open(&s, FIX "/MAP", 1 << 20, &ENV);
    CHECK(err == HM_OK, "open: %s", hm_strerror(err));
    if (err) return;
    const hm_header_t *h = hm_store_header(s);
    FILE *f = fopen(FIX "/expect.txt", "r");
    CHECK(f != NULL, "expect.txt");
    if (!f) { hm_store_close(s); return; }
    char line[512];
    int tiles = 0, feats = 0;
    const hm_tile_t *tile = NULL;
    hm_iter_t it;
    while (fgets(line, sizeof(line), f)) {
        char kind[8];
        if (sscanf(line, "%7s", kind) != 1) continue;
        if (!strcmp(kind, "header")) {
            double lat0, lon0, kx, ky, rad;
            unsigned id;
            int n;
            char date[32], attr_hex[128], attr[64];
            sscanf(line, "header %lf %lf %lf %lf %lf %u %d %31s %127s", &lat0, &lon0, &kx, &ky, &rad, &id, &n, date, attr_hex);
            hex_to_str(attr_hex, attr, sizeof(attr));
            CHECK(h->lat0 == lat0 && h->lon0 == lon0 && h->kx == kx && h->ky == ky && h->radius_m == rad,
                  "projection %f %f %f %f", h->lat0, h->lon0, h->kx, h->ky);
            CHECK(h->build_id == id && h->n_levels == n, "build id / levels");
            CHECK(!strcmp(h->build_date, date), "build date %s", h->build_date);
            CHECK(!strcmp(h->attribution, attr) && strstr(h->attribution, "OpenStreetMap contributors"),
                  "attribution \"%s\"", h->attribution);
        } else if (!strcmp(kind, "level")) {
            int i, tm, cols, rows, n;
            sscanf(line, "level %d %d %d %d %d", &i, &tm, &cols, &rows, &n);
            CHECK(h->level[i].tile_m == (uint32_t)tm && h->level[i].cols == cols && h->level[i].rows == rows &&
                  h->level[i].n_tiles == (uint32_t)n, "level %d", i);
        } else if (!strcmp(kind, "tile")) {
            int lv, c, r, n;
            sscanf(line, "tile %d %d %d %d", &lv, &c, &r, &n);
            tile = hm_store_get(s, lv, c, r, &err);
            CHECK(tile && !err && tile->n_features == n && tile->level == lv, "tile %d %d %d: %s", lv, c, r, hm_strerror(err));
            if (tile) hm_iter_init(&it, tile);
            tiles++;
        } else if (!strcmp(kind, "f") && tile) {
            int cls, npts, nrings, rank, ref, x, y, xl, yl;
            char name[64];
            sscanf(line, "f %d %d %d %d %d %63s %d %d %d %d", &cls, &npts, &nrings, &rank, &ref, name, &x, &y, &xl, &yl);
            for (char *p = name; *p; p++) if (*p == '_') *p = ' ';
            hm_feature_t ft;
            int got = hm_iter_next(&it, &ft);
            CHECK(got == 1, "feature missing");
            if (!got) continue;
            char have[64] = "-";
            if (ft.name) { memcpy(have, ft.name, ft.name_len); have[ft.name_len] = 0; }
            CHECK(ft.cls == cls && ft.npts == npts && ft.n_rings == nrings && ft.rank == rank && ft.ref == ref,
                  "feature %d: cls %d npts %d rings %d rank %d ref %d", cls, ft.cls, ft.npts, ft.n_rings, ft.rank, ft.ref);
            CHECK(!strcmp(have, name), "name %s, expected %s", have, name);
            CHECK(hm_px(ft.pts, 0) == x && hm_py(ft.pts, 0) == y, "first point");
            CHECK(hm_px(ft.pts, ft.npts - 1) == xl && hm_py(ft.pts, ft.npts - 1) == yl, "last point");
            CHECK(ft.geom == (cls >= 32 ? HM_POINT : cls >= 24 ? HM_AREA : HM_LINE), "geometry from class");
            feats++;
        }
    }
    fclose(f);
    CHECK(tiles >= 20 && feats >= 60, "checked %d tiles, %d features", tiles, feats);

    /* no tile there / off the grid: NULL, and no error */
    tile = hm_store_get(s, 0, 0, 0, &err);
    CHECK(!tile && err == HM_OK, "corner tile is empty");
    tile = hm_store_get(s, 0, 99, 0, &err);
    CHECK(!tile && err == HM_OK, "off the grid");
    tile = hm_store_get(s, 7, 0, 0, &err);
    CHECK(!tile && err == HM_OK, "no such level");

    /* a second pass is all cache */
    const hm_stats_t *st = hm_store_stats(s);
    uint32_t misses = st->misses;
    CHECK(hm_store_cached(s, 3, 0, 0), "level 3 tile cached");
    hm_store_get(s, 3, 0, 0, &err);
    hm_store_get(s, 0, 3, 3, &err);
    CHECK(st->misses == misses && st->hits >= 2, "hits from the cache");
    CHECK(st->crc_errors == 0 && st->io_errors == 0, "no errors");
    printf("  fixture: %d tiles, %d features; %u bytes read, cache %u bytes in %u entries\n", tiles, feats,
           (unsigned)st->bytes_read, (unsigned)st->cache_bytes, (unsigned)st->cache_entries);
    hm_store_close(s);
}

/* ---- damage --------------------------------------------------------------------------------- */

static void test_damage(void)
{
    hm_store_t *s;
    char dir[128], path[192];
    CHECK(hm_store_open(&s, "/nonexistent/MAP", 1 << 16, &ENV) == HM_ERR_NOFILE && !s, "no folder");

    scratch_copy(dir, sizeof(dir));
    snprintf(path, sizeof(path), "%s/MAP.IDX", dir);
    poke(path, 60, 0x01);                                 /* inside the header */
    CHECK(hm_store_open(&s, dir, 1 << 16, &ENV) == HM_ERR_CRC, "header damage");

    scratch_copy(dir, sizeof(dir));
    snprintf(path, sizeof(path), "%s/MAP.DAT", dir);
    poke(path, 12, 0x55);                                 /* the build id */
    CHECK(hm_store_open(&s, dir, 1 << 16, &ENV) == HM_ERR_MISMATCH, "a MAP.DAT from another build");

    scratch_copy(dir, sizeof(dir));
    snprintf(path, sizeof(path), "%s/MAP.DAT", dir);
    long size = file_size(path);
    poke(path, size - 3, 0x80);                           /* the last tile's points */
    int err = hm_store_open(&s, dir, 1 << 16, &ENV);
    CHECK(err == HM_OK, "data damage is found per tile, not at open");
    if (!err) {
        int bad = 0, good = 0;
        for (int lv = 0; lv < 4; lv++)
            for (int r = 0; r < 7; r++)
                for (int c = 0; c < 7; c++) {
                    const hm_tile_t *t = hm_store_get(s, lv, c, r, &err);
                    if (err == HM_ERR_CRC) bad++;
                    else if (t) good++;
                }
        CHECK(bad == 1 && good >= 20, "one damaged tile refused (%d), the rest read (%d)", bad, good);
        CHECK(hm_store_stats(s)->crc_errors == 1, "counted");
        hm_store_close(s);
    }

    /* every bit of a tile's body matters: flip each byte of one tile in turn */
    uint8_t buf[4096];
    FILE *f = fopen(FIX "/MAP/MAP.DAT", "rb");
    size_t n = f ? fread(buf, 1, sizeof(buf), f) : 0;
    if (f) fclose(f);
    size_t len = HM_TILE_HDR + (size_t)(buf[HM_DAT_HDR_SIZE + 8] | buf[HM_DAT_HDR_SIZE + 9] << 8);
    int caught = 0;
    uint32_t offs[64];
    for (size_t i = 0; i < len && HM_DAT_HDR_SIZE + len <= n; i++) {
        uint8_t t[4096];
        memcpy(t, buf + HM_DAT_HDR_SIZE, len);
        t[i] ^= 0x10;
        hm_tile_t tile;
        if (hm_tile_parse(t, len, &tile, offs, 64) != HM_OK) caught++;
    }
    CHECK(caught == (int)len, "a flipped bit anywhere in a tile is caught (%d of %d)", caught, (int)len);
}

/* ---- the cache ----------------------------------------------------------------------------- */

static size_t entry_bytes(int level, int col, int row)
{
    hm_store_t *s;
    int err;
    if (hm_store_open(&s, FIX "/MAP", 1 << 20, &ENV)) return 0;
    hm_store_get(s, level, col, row, &err);
    size_t n = hm_store_stats(s)->cache_bytes;
    hm_store_close(s);
    return n;
}

static void test_cache(void)
{
    /* room for one of two level-0 tiles: the frame's own tiles are never evicted */
    size_t a = entry_bytes(0, 3, 2), b = entry_bytes(0, 3, 3), big = entry_bytes(1, 0, 0);
    size_t budget = (a > b ? a : b) + 4;
    CHECK(a && b && a + b > budget && big > budget, "tile sizes %zu %zu %zu", a, b, big);
    hm_store_t *s;
    int err = hm_store_open(&s, FIX "/MAP", budget, &ENV);
    CHECK(err == HM_OK, "open");
    if (err) return;
    hm_store_frame(s);
    const hm_tile_t *t = hm_store_get(s, 1, 0, 0, &err);
    CHECK(!t && err == HM_ERR_NOMEM, "a tile bigger than the cache is refused (%s)", hm_strerror(err));
    const hm_tile_t *ta = hm_store_get(s, 0, 3, 2, &err);
    CHECK(ta && !err, "one fits");
    hm_iter_t it;
    hm_feature_t f;
    int first_cls = -1;
    if (ta) { hm_iter_init(&it, ta); hm_iter_next(&it, &f); first_cls = f.cls; }
    const hm_tile_t *tb = hm_store_get(s, 0, 3, 3, &err);
    CHECK(!tb && err == HM_ERR_NOMEM, "no room for the second while the first is in use this frame");
    if (ta) { hm_iter_init(&it, ta); hm_iter_next(&it, &f); CHECK(f.cls == first_cls, "the first is intact"); }
    hm_store_frame(s);
    tb = hm_store_get(s, 0, 3, 3, &err);
    CHECK(tb && !err && hm_store_stats(s)->evictions >= 1, "next frame: the first is evicted, the second read");
    CHECK(!hm_store_cached(s, 0, 3, 2), "the old one is gone");
    hm_store_close(s);
}

/* ---- rendering ------------------------------------------------------------------------------ */

#define W 480
#define H 216
static uint16_t px[W * H];
static const hm_palette_t PAL = {.bg = 0xFFFF, .road = 0x0000, .water = 0x4C78, .rail = 0x632C, .trail = 0x0A15,
                                 .stall = 0xB004, .mark = 0x0A15, .place = 0x0001, .fuel = 0x0002};

static uint16_t at(int x, int y) { return px[y * W + x]; }

static int count(int x0, int y0, int x1, int y1, uint16_t col)
{
    int n = 0;
    for (int y = y0; y <= y1; y++)
        for (int x = x0; x <= x1; x++) n += at(x, y) == col;
    return n;
}

static int has_label(const hm_render_out_t *o, const char *text, int kind)
{
    for (int i = 0; i < o->n_labels; i++)
        if (!strcmp(o->labels[i].text, text) && o->labels[i].kind == kind) return i + 1;
    return 0;
}

static void view_at(hm_view_t *v, const hm_header_t *h, double lat, double lon, int level, float mpp)
{
    memset(v, 0, sizeof(*v));
    hm_project(h, lat, lon, &v->cx, &v->cy);
    v->level = level;
    v->mpp = mpp;
    v->pal = &PAL;
}

static void test_render(void)
{
    hm_store_t *s;
    int err = hm_store_open(&s, FIX "/MAP", 1 << 20, &ENV);
    CHECK(err == HM_OK, "open");
    if (err) return;
    const hm_header_t *h = hm_store_header(s);
    hm_renderer_t *r = hm_renderer_new(&ENV);
    hm_canvas_t cv = {px, W, H};
    hm_render_out_t out;
    hm_view_t v;

    /* centre of town, closest level: I-55 runs north-south 0.005 deg east, about 118 px right */
    view_at(&v, h, 32.30, -90.18, 0, 4.0f);
    hm_render(r, s, &v, &cv, &out);
    CHECK(out.tiles_drawn >= 4 && out.errors == 0, "tiles %d drawn %d errors %d", out.tiles, out.tiles_drawn, out.errors);
    /* 7 px wide, so 7 or 8 pixels touched across, the middle three light again (the centre line) */
    int ink = 0, xm = -1, xl = -1, inner = 0;
    for (int x = 340; x < 380; x++)
        if (at(x, H / 2) != PAL.bg) { ink++; if (xm < 0) xm = x; xl = x; }
    for (int x = xm + 1; xm >= 0 && x < xl; x++) inner += at(x, H / 2) == PAL.bg;
    CHECK(ink >= 4 && ink <= 8 && xl - xm >= 6 && xl - xm <= 8, "motorway across the middle row: %d..%d", xm, xl);
    CHECK(xm >= 349 && xm <= 353, "motorway where I-55 is (x %d)", xm);
    CHECK(inner >= 1, "with a light centre line (%d px)", inner);
    CHECK(at(xm + 1, H / 2) == PAL.road, "solid black edge");
    CHECK(has_label(&out, "I-55", HM_LABEL_ROAD_REF), "I-55 shield candidate");
    CHECK(has_label(&out, "W Elm Ave", HM_LABEL_ROAD_NAME), "a street name on level 0");
    int city = has_label(&out, "Testville", HM_LABEL_PLACE);
    CHECK(city, "Testville place label");
    if (city) {
        const hm_label_t *lb = &out.labels[city - 1];
        CHECK(abs(lb->x - W / 2) <= 1 && abs(lb->y - H / 2) <= 1, "Testville anchored on its dot (%d, %d)", lb->x, lb->y);
        CHECK(at(W / 2, H / 2) == PAL.place, "the city's dot");
        CHECK(lb->prio > out.labels[has_label(&out, "I-55", HM_LABEL_ROAD_REF) - 1].prio, "a city outranks a road");
    }
    CHECK(count(0, 0, W - 1, H - 1, PAL.rail) > 20, "the railway");
    printf("  render level 0: %d tiles, %d features, load %u us, draw %u us, %d label candidates\n",
           out.tiles_drawn, out.features, (unsigned)out.us_load, (unsigned)out.us_draw, out.n_labels);

    view_at(&v, h, 32.30, -90.18, 1, 10.0f);
    hm_render(r, s, &v, &cv, &out);
    CHECK(has_label(&out, "US 49", HM_LABEL_ROAD_REF) && has_label(&out, "I-55", HM_LABEL_ROAD_REF), "shields on level 1");
    CHECK(!has_label(&out, "W Elm Ave", HM_LABEL_ROAD_NAME), "no street name on level 1");

    view_at(&v, h, 32.296, -90.181, 0, 4.0f);
    hm_render(r, s, &v, &cv, &out);
    CHECK(count(W / 2 - 6, H / 2 - 7, W / 2 + 6, H / 2 + 7, PAL.fuel) > 30, "the fuel pump at level 0");
    view_at(&v, h, 32.296, -90.181, 2, 25.0f);
    hm_render(r, s, &v, &cv, &out);
    CHECK(count(0, 0, W - 1, H - 1, PAL.fuel) == 0, "not at level 2");

    /* a lake fills; an island in a reservoir does not */
    view_at(&v, h, 32.29, -90.19, 0, 4.0f);
    hm_render(r, s, &v, &cv, &out);
    CHECK(at(W / 2, H / 2) == PAL.water && count(W / 2 - 40, H / 2 - 20, W / 2 + 40, H / 2 + 20, PAL.water) > 2500,
          "Test Lake is water");
    view_at(&v, h, 32.31, -90.17, 0, 4.0f);
    hm_render(r, s, &v, &cv, &out);
    CHECK(at(W / 2, H / 2) == PAL.bg, "the island is dry");
    CHECK(at(W / 2 + 25, H / 2) == PAL.water && at(W / 2, H / 2 - 25) == PAL.water, "the reservoir around it is water");
    CHECK(out.errors == 0, "no errors");

    /* the trail and pins go on top */
    view_at(&v, h, 32.30, -90.18, 1, 10.0f);
    hm_pt_t trail[3];
    hm_pin_t pins[2];
    for (int i = 0; i < 3; i++) { trail[i].x = (float)(v.cx - 1000 + 500 * i); trail[i].y = (float)(v.cy - 400); }
    pins[0] = (hm_pin_t){(float)v.cx - 600, (float)v.cy + 300, HM_PIN_STALL};
    pins[1] = (hm_pin_t){(float)v.cx + 600, (float)v.cy + 300, HM_PIN_MARK};
    v.trail = trail; v.n_trail = 3; v.pins = pins; v.n_pins = 2;
    hm_render(r, s, &v, &cv, &out);
    CHECK(count(W / 2 - 100, H / 2 + 38, W / 2, H / 2 + 42, PAL.trail) >= 20, "the trail's crumbs");
    CHECK(count(W / 2 - 100, H / 2 + 40, W / 2, H / 2 + 40, PAL.trail) <= 70, "with gaps between them");
    CHECK(at(W / 2 - 60, H / 2 - 30) == PAL.stall, "the stall pin");
    CHECK(at(W / 2 + 60, H / 2 - 30) == PAL.mark, "the marker pin");
    CHECK(at(W / 2 - 60 + 9, H / 2 - 30) == PAL.bg, "the stall pin's halo");

    /* each level renders */
    static const float mpp[4] = {4, 10, 25, 60};
    for (int lv = 0; lv < 4; lv++) {
        view_at(&v, h, 32.30, -90.18, lv, mpp[lv]);
        hm_render(r, s, &v, &cv, &out);
        int drawn = W * H - count(0, 0, W - 1, H - 1, PAL.bg);
        CHECK(out.errors == 0 && drawn > 100, "level %d draws (%d px of ink)", lv, drawn);
    }

    /* far outside the map: blank, no tiles, no errors */
    view_at(&v, h, 40.0, -100.0, 0, 4.0f);
    hm_render(r, s, &v, &cv, &out);
    CHECK(out.tiles_drawn == 0 && out.errors == 0 && count(0, 0, W - 1, H - 1, PAL.bg) == W * H, "nothing out there");
    /* no store at all (no card): a blank view */
    hm_render(r, NULL, &v, &cv, &out);
    CHECK(out.tiles_drawn == 0 && count(0, 0, W - 1, H - 1, PAL.bg) == W * H, "no store");

    hm_renderer_free(r);
    hm_store_close(s);
}

static void count_tile(void *ctx, int level, int col, int row) { (void)level; (void)col; (void)row; (*(int *)ctx)++; }

static void test_style_and_prefetch(void)
{
    static const int order[] = {HM_MOTORWAY, HM_TRUNK, HM_PRIMARY, HM_SECONDARY, HM_TERTIARY, HM_RESIDENTIAL, HM_SERVICE};
    for (int lv = 0; lv < 4; lv++)
        for (int i = 1; i < 7; i++)
            CHECK(hm_line_width(order[i - 1], lv) >= hm_line_width(order[i], lv), "width order at level %d", lv);
    CHECK(hm_line_width(HM_SERVICE, 0) == 1 && hm_line_width(HM_SERVICE, 1) == 0, "service roads only close up");
    CHECK(hm_line_width(HM_MOTORWAY, 3) >= 3, "motorways stay bold far out");

    hm_store_t *s;
    if (hm_store_open(&s, FIX "/MAP", 1 << 20, &ENV)) return;
    hm_view_t v;
    view_at(&v, hm_store_header(s), 32.30, -90.18, 0, 4.0f);
    int n0 = 0, n1 = 0;
    hm_view_tiles(hm_store_header(s), &v, W, H, 0, count_tile, &n0);
    hm_view_tiles(hm_store_header(s), &v, W, H, 1, count_tile, &n1);
    CHECK(n0 >= 4 && n0 <= 12 && n1 > n0, "view needs %d tiles, %d with a ring for prefetch", n0, n1);
    hm_store_close(s);
}

int main(void)
{
    printf("hhmap host tests\n");
    test_crc();
    test_fixture();
    test_damage();
    test_cache();
    test_render();
    test_style_and_prefetch();
    printf("%d checks, %s\n", checks, failures ? "FAILED" : "all passed");
    return failures != 0;
}
