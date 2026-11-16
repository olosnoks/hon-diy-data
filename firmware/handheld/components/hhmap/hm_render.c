/* One map view into an RGB565 buffer.
 *
 *   water areas    scanline fill, even-odd (holes are islands)
 *   lines          anti-aliased "capsules": every segment is the set of pixels
 *                  within half the line width of it, with a half-pixel fringe;
 *                  joins and ends come out round
 *   points         small squares (places), a pump (fuel), octagon / diamond pins
 *
 * Drawing goes by layer across all visible tiles (water first, motorways
 * last), so a minor road in one tile never paints over a major one in the
 * next. Road refs and place names are not drawn here: they come back as
 * label candidates, and the UI places them with its own fonts.
 */
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "hhmap.h"

/* the drawing order; the same as LAYER in hondalog/handheld/osmmap.py */
static const uint8_t LAYERS[] = {
    HM_WATER, HM_RIVER, HM_CANAL, HM_RAIL, HM_SERVICE, HM_RESIDENTIAL, HM_TERTIARY, HM_LINK, HM_LINK_MAJOR,
    HM_SECONDARY, HM_PRIMARY, HM_TRUNK, HM_MOTORWAY, HM_SUBURB, HM_HAMLET, HM_VILLAGE, HM_TOWN, HM_CITY, HM_FUEL,
};
#define N_LAYERS ((int)(sizeof(LAYERS) / sizeof(LAYERS[0])))
#define MAXI(a, b) ((a) > (b) ? (a) : (b))
#define TRAIL_LAYER 13          /* the drive's trail goes on top of the roads, under the places */

/* line width in px per level (0 = closest) */
static const struct { uint8_t cls, w[HM_MAX_LEVELS]; } WIDTH[] = {
    {HM_MOTORWAY, {7, 6, 5, 4}}, {HM_TRUNK, {6, 5, 4, 3}}, {HM_PRIMARY, {5, 4, 4, 3}},
    {HM_SECONDARY, {4, 3, 3, 2}}, {HM_TERTIARY, {3, 3, 2, 0}}, {HM_LINK_MAJOR, {3, 3, 2, 0}},
    {HM_LINK, {3, 2, 0, 0}}, {HM_RESIDENTIAL, {2, 2, 0, 0}}, {HM_SERVICE, {1, 0, 0, 0}},
    {HM_RAIL, {2, 2, 2, 0}}, {HM_RIVER, {3, 2, 2, 2}}, {HM_CANAL, {2, 2, 0, 0}},
};

int hm_line_width(int cls, int level)
{
    if (level < 0 || level >= HM_MAX_LEVELS) return 0;
    for (size_t i = 0; i < sizeof(WIDTH) / sizeof(WIDTH[0]); i++)
        if (WIDTH[i].cls == cls) return WIDTH[i].w[level];
    return 0;
}

typedef struct { float x0, y0, x1, y1; } edge_t;          /* y0 < y1 */

typedef struct {
    uint32_t hash;
    char text[HM_LABEL_LEN];
    float score;
    int16_t x, y;
    uint8_t kind, cls;
    uint16_t prio;
} cand_t;

#define MAX_CAND 96

struct hm_renderer {
    hm_env_t env;
    edge_t *edges;
    int edges_cap;
    float *xs;
    int *active;
    int xs_cap;
    cand_t cand[MAX_CAND];
    int n_cand;
};

static void *ralloc(hm_renderer_t *r, size_t n) { return r->env.alloc ? r->env.alloc(n) : malloc(n); }
static void rfree(hm_renderer_t *r, void *p) { if (p) { if (r->env.free) r->env.free(p); else free(p); } }

hm_renderer_t *hm_renderer_new(const hm_env_t *env)
{
    hm_renderer_t tmp = {0};
    if (env) tmp.env = *env;
    hm_renderer_t *r = ralloc(&tmp, sizeof(*r));
    if (!r) return NULL;
    *r = tmp;
    return r;
}

void hm_renderer_free(hm_renderer_t *r)
{
    if (!r) return;
    rfree(r, r->edges);
    rfree(r, r->xs);
    rfree(r, r->active);
    hm_env_t env = r->env;
    if (env.free) env.free(r); else free(r);
}

static int grow(hm_renderer_t *r, int edges)
{
    if (edges <= r->edges_cap) return 0;
    int cap = r->edges_cap ? r->edges_cap : 1024;
    while (cap < edges) cap *= 2;
    edge_t *e = ralloc(r, (size_t)cap * sizeof(edge_t));
    float *xs = ralloc(r, (size_t)cap * sizeof(float));
    int *act = ralloc(r, (size_t)cap * sizeof(int));
    if (!e || !xs || !act) { rfree(r, e); rfree(r, xs); rfree(r, act); return -1; }
    rfree(r, r->edges); rfree(r, r->xs); rfree(r, r->active);
    r->edges = e; r->xs = xs; r->active = act;
    r->edges_cap = r->xs_cap = cap;
    return 0;
}

/* ---- pixels ------------------------------------------------------------------------- */

static inline uint16_t blend(uint16_t fg, uint16_t bg, uint32_t a)       /* a: 0..32 */
{
    uint32_t f = (fg | (uint32_t)fg << 16) & 0x07E0F81Fu, b = (bg | (uint32_t)bg << 16) & 0x07E0F81Fu;
    uint32_t m = ((((f - b) * a) >> 5) + b) & 0x07E0F81Fu;
    return (uint16_t)(m | m >> 16);
}

static void fill_rect(hm_canvas_t *c, int x0, int y0, int x1, int y1, uint16_t col)
{
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 >= c->w) x1 = c->w - 1;
    if (y1 >= c->h) y1 = c->h - 1;
    for (int y = y0; y <= y1; y++) {
        uint16_t *row = c->px + (size_t)y * c->w;
        for (int x = x0; x <= x1; x++) row[x] = col;
    }
}

/* All pixels within hw of segment AB, anti-aliased over the last half pixel. */
static void seg(hm_canvas_t *c, float ax, float ay, float bx, float by, float hw, uint16_t col)
{
    const float R = hw + 0.5f;
    float minx = fminf(ax, bx) - R, maxx = fmaxf(ax, bx) + R, miny = fminf(ay, by) - R, maxy = fmaxf(ay, by) + R;
    if (maxx < 0 || maxy < 0 || minx >= (float)c->w || miny >= (float)c->h) return;
    float dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
    float inner = hw > 0.5f ? (hw - 0.5f) * (hw - 0.5f) : 0;
    float corner[4][2] = {{0}};
    if (l2 > 1e-6f) {
        float l = sqrtf(l2), nx = -dy / l * R, ny = dx / l * R;
        corner[0][0] = ax + nx; corner[0][1] = ay + ny; corner[1][0] = bx + nx; corner[1][1] = by + ny;
        corner[2][0] = bx - nx; corner[2][1] = by - ny; corner[3][0] = ax - nx; corner[3][1] = ay - ny;
    }
    int y0 = miny < 0 ? 0 : (int)miny, y1 = maxy >= (float)c->h ? c->h - 1 : (int)maxy;
    for (int y = y0; y <= y1; y++) {
        float yc = (float)y + 0.5f, xl = 1e30f, xr = -1e30f;
        /* the row through the capsule: the two end discs and the band between them */
        const float ends[2][2] = {{ax, ay}, {bx, by}};
        for (int k = 0; k < 2; k++) {
            float d = yc - ends[k][1];
            if (d * d <= R * R) {
                float h = sqrtf(R * R - d * d);
                xl = fminf(xl, ends[k][0] - h);
                xr = fmaxf(xr, ends[k][0] + h);
            }
        }
        if (l2 > 1e-6f) {
            for (int k = 0; k < 4; k++) {
                const float *p = corner[k], *q = corner[(k + 1) & 3];
                if ((p[1] - yc) * (q[1] - yc) > 0 || p[1] == q[1]) continue;
                float x = p[0] + (yc - p[1]) * (q[0] - p[0]) / (q[1] - p[1]);
                xl = fminf(xl, x);
                xr = fmaxf(xr, x);
            }
        }
        if (xl > xr) continue;
        int x0 = xl < 0 ? 0 : (int)xl, x1 = xr >= (float)c->w ? c->w - 1 : (int)xr;
        uint16_t *row = c->px + (size_t)y * c->w;
        for (int x = x0; x <= x1; x++) {
            float px = (float)x + 0.5f, t = 0;
            if (l2 > 1e-6f) {
                t = ((px - ax) * dx + (yc - ay) * dy) / l2;
                t = t < 0 ? 0 : t > 1 ? 1 : t;
            }
            float ex = px - (ax + t * dx), ey = yc - (ay + t * dy), d2 = ex * ex + ey * ey;
            if (d2 <= inner) { row[x] = col; continue; }
            float cov = hw + 0.5f - sqrtf(d2);
            if (cov <= 0) continue;
            if (cov >= 1) row[x] = col;
            else row[x] = blend(col, row[x], (uint32_t)(cov * 32.0f + 0.5f));
        }
    }
}

/* Even-odd scanline fill of r->edges[0..n). */
static int cmp_edge(const void *a, const void *b)
{
    float d = ((const edge_t *)a)->y0 - ((const edge_t *)b)->y0;
    return d < 0 ? -1 : d > 0;
}

static int cmp_float(const void *a, const void *b)
{
    float d = *(const float *)a - *(const float *)b;
    return d < 0 ? -1 : d > 0;
}

static void fill_edges(hm_renderer_t *r, hm_canvas_t *c, int n, uint16_t col)
{
    if (!n) return;
    edge_t *e = r->edges;
    float ymin = e[0].y0, ymax = e[0].y1;
    for (int i = 1; i < n; i++) { ymin = fminf(ymin, e[i].y0); ymax = fmaxf(ymax, e[i].y1); }
    qsort(e, (size_t)n, sizeof(edge_t), cmp_edge);
    int y0 = (int)floorf(ymin), y1 = (int)ceilf(ymax);
    if (y0 < 0) y0 = 0;
    if (y1 > c->h - 1) y1 = c->h - 1;
    int next = 0, n_act = 0;
    for (int y = y0; y <= y1; y++) {
        float yc = (float)y + 0.5f;
        while (next < n && e[next].y0 <= yc) r->active[n_act++] = next++;
        int nx = 0;
        for (int k = 0; k < n_act;) {
            const edge_t *a = &e[r->active[k]];
            if (a->y1 <= yc) { r->active[k] = r->active[--n_act]; continue; }
            r->xs[nx++] = a->x0 + (yc - a->y0) * (a->x1 - a->x0) / (a->y1 - a->y0);
            k++;
        }
        if (nx < 2) continue;
        if (nx > 8) qsort(r->xs, (size_t)nx, sizeof(float), cmp_float);
        else
            for (int i = 1; i < nx; i++)
                for (int j = i; j > 0 && r->xs[j - 1] > r->xs[j]; j--) { float t = r->xs[j]; r->xs[j] = r->xs[j - 1]; r->xs[j - 1] = t; }
        uint16_t *row = c->px + (size_t)y * c->w;
        for (int i = 0; i + 1 < nx; i += 2) {
            int xa = (int)ceilf(r->xs[i] - 0.5f), xb = (int)ceilf(r->xs[i + 1] - 0.5f) - 1;
            if (xa < 0) xa = 0;
            if (xb > c->w - 1) xb = c->w - 1;
            for (int x = xa; x <= xb; x++) row[x] = col;
        }
    }
}

static int add_edge(hm_renderer_t *r, int n, float x0, float y0, float x1, float y1)
{
    if (y0 == y1) return n;
    if (n >= r->edges_cap && grow(r, n + 1)) return n;
    edge_t *e = &r->edges[n];
    if (y0 < y1) { e->x0 = x0; e->y0 = y0; e->x1 = x1; e->y1 = y1; }
    else { e->x0 = x1; e->y0 = y1; e->x1 = x0; e->y1 = y0; }
    return n + 1;
}

/* a regular polygon (n corners) of circumradius rad, first corner at angle a0 */
static void ngon(hm_renderer_t *r, hm_canvas_t *c, float cx, float cy, float rad, int n, float a0, uint16_t col)
{
    if (grow(r, n)) return;
    int m = 0;
    for (int i = 0; i < n; i++) {
        float a = a0 + 6.2831853f * (float)i / (float)n, b = a0 + 6.2831853f * (float)(i + 1) / (float)n;
        m = add_edge(r, m, cx + rad * cosf(a), cy + rad * sinf(a), cx + rad * cosf(b), cy + rad * sinf(b));
    }
    fill_edges(r, c, m, col);
}

/* ---- labels ------------------------------------------------------------------------ */

static uint32_t fnv(const char *s, int n)
{
    uint32_t h = 2166136261u;
    for (int i = 0; i < n; i++) h = (h ^ (uint8_t)s[i]) * 16777619u;
    return h;
}

static void candidate(hm_renderer_t *r, const char *s, int n, float score, float x, float y, uint8_t kind,
                      uint8_t cls, uint16_t prio)
{
    if (!s || !n) return;
    if (n > HM_LABEL_LEN - 1) {
        n = HM_LABEL_LEN - 1;
        while (n > 0 && ((uint8_t)s[n] & 0xC0) == 0x80) n--;      /* not inside a UTF-8 character */
    }
    uint32_t h = fnv(s, n);
    cand_t *slot = NULL;
    for (int i = 0; i < r->n_cand; i++)
        if (r->cand[i].hash == h && !strncmp(r->cand[i].text, s, (size_t)n) && !r->cand[i].text[n]) { slot = &r->cand[i]; break; }
    if (!slot) {
        if (r->n_cand < MAX_CAND) slot = &r->cand[r->n_cand++];
        else {                                         /* replace the weakest */
            slot = &r->cand[0];
            for (int i = 1; i < MAX_CAND; i++) if (r->cand[i].prio < slot->prio) slot = &r->cand[i];
            if (slot->prio >= prio) return;
        }
        memset(slot, 0, sizeof(*slot));
        slot->hash = h;
        memcpy(slot->text, s, (size_t)n);
        slot->score = -1;
    }
    if (score <= slot->score) return;
    slot->score = score;
    slot->x = (int16_t)lrintf(x);
    slot->y = (int16_t)lrintf(y);
    slot->kind = kind;
    slot->cls = cls;
    slot->prio = prio;
}

static uint16_t road_prio(int cls)
{
    switch (cls) {
    case HM_MOTORWAY: return 560;
    case HM_TRUNK: return 550;
    case HM_PRIMARY: return 540;
    case HM_SECONDARY: return 530;
    default: return 450;
    }
}

static uint16_t place_prio(int cls, int rank)
{
    if (rank < 0) rank = 0;
    if (cls == HM_CITY || cls == HM_TOWN) return (uint16_t)(600 + rank);
    if (cls == HM_VILLAGE) return (uint16_t)(300 + rank);
    return (uint16_t)(200 + rank);
}

static int cmp_cand(const void *a, const void *b)
{
    const cand_t *x = a, *y = b;
    return (int)y->prio - (int)x->prio;
}

/* ---- features ---------------------------------------------------------------------- */

typedef struct { float ox, oy, k; } xform_t;           /* screen = (ox + qx k, oy - qy k) */

static void draw_line(hm_canvas_t *c, const hm_feature_t *f, const xform_t *t, float hw, uint16_t col, int dash)
{
    float px = t->ox + (float)hm_px(f->pts, 0) * t->k, py = t->oy - (float)hm_py(f->pts, 0) * t->k;
    float phase = 0;                                        /* dashes: 6 px on, 4 off */
    for (int i = 1; i < f->npts; i++) {
        float x = t->ox + (float)hm_px(f->pts, i) * t->k, y = t->oy - (float)hm_py(f->pts, i) * t->k;
        if (!dash) {
            seg(c, px, py, x, y, hw, col);
        } else {
            float dx = x - px, dy = y - py, len = sqrtf(dx * dx + dy * dy), s = 0;
            while (s < len) {
                float in = fmodf(phase, 10.0f), step = in < 6 ? 6 - in : 10 - in;
                if (s + step > len) step = len - s;
                if (in < 6) seg(c, px + dx * s / len, py + dy * s / len, px + dx * (s + step) / len,
                                py + dy * (s + step) / len, hw, col);
                s += step;
                phase += step;
            }
        }
        px = x;
        py = y;
    }
}

/* A road's label goes somewhere along it on the screen, clear of the centre
 * (where the car is) and of the UI's overlays: points every 16 px along the
 * line are tried; the longer the road on the screen the better, and a point
 * about 90 px from the car is better than one at the edge. */
static void road_label(hm_renderer_t *r, hm_canvas_t *c, const hm_view_t *v, const hm_feature_t *f, const xform_t *t)
{
    if (!f->name) return;
    uint8_t kind = f->ref ? HM_LABEL_ROAD_REF : HM_LABEL_ROAD_NAME;
    const float clear = 44, step = 16;
    float x0 = (float)MAXI(16, v->inset[0]), y0 = (float)MAXI(14, v->inset[1]);
    float x1 = (float)c->w - (float)MAXI(16, v->inset[2]), y1 = (float)c->h - (float)MAXI(14, v->inset[3]);
    float total = 0;
    for (int i = 1; i < f->npts; i++) {
        float dx = (float)(hm_px(f->pts, i) - hm_px(f->pts, i - 1)) * t->k, dy = (float)(hm_py(f->pts, i) - hm_py(f->pts, i - 1)) * t->k;
        total += sqrtf(dx * dx + dy * dy);
    }
    if (total < 20) return;
    float len_score = fminf(total, 240), carry = step / 2;
    for (int i = 1; i < f->npts; i++) {
        float ax = t->ox + (float)hm_px(f->pts, i - 1) * t->k, ay = t->oy - (float)hm_py(f->pts, i - 1) * t->k;
        float bx = t->ox + (float)hm_px(f->pts, i) * t->k, by = t->oy - (float)hm_py(f->pts, i) * t->k;
        float len = sqrtf((bx - ax) * (bx - ax) + (by - ay) * (by - ay)), at = carry;
        for (; at < len; at += step) {
            float u = at / len, mx = ax + (bx - ax) * u, my = ay + (by - ay) * u;
            if (mx < x0 || my < y0 || mx > x1 || my > y1) continue;
            float dx = mx - (float)c->w / 2, dy = my - (float)c->h / 2, d = sqrtf(dx * dx + dy * dy);
            if (d < clear) continue;
            candidate(r, f->name, f->name_len, len_score - 0.1f * fabsf(d - 90), mx, my, kind, f->cls, road_prio(f->cls));
        }
        carry = at - len;
    }
}

static int draw_area(hm_renderer_t *r, hm_canvas_t *c, const hm_feature_t *f, const xform_t *t, uint16_t col)
{
    if (grow(r, f->npts)) return -1;
    int n = 0, at = 0;
    float minx = 1e30f, maxx = -1e30f, miny = 1e30f, maxy = -1e30f;
    for (int k = 0; k < f->n_rings; k++) {
        int len = hm_u16(f->ring_lens, k);
        for (int i = 0; i < len; i++) {
            int j = at + (i + 1) % len;
            float x0 = t->ox + (float)hm_px(f->pts, at + i) * t->k, y0 = t->oy - (float)hm_py(f->pts, at + i) * t->k;
            float x1 = t->ox + (float)hm_px(f->pts, j) * t->k, y1 = t->oy - (float)hm_py(f->pts, j) * t->k;
            minx = fminf(minx, x0); maxx = fmaxf(maxx, x0); miny = fminf(miny, y0); maxy = fmaxf(maxy, y0);
            n = add_edge(r, n, x0, y0, x1, y1);
        }
        at += len;
    }
    if (maxx < 0 || maxy < 0 || minx >= (float)c->w || miny >= (float)c->h) return 0;
    fill_edges(r, c, n, col);
    return 0;
}

/* a petrol pump, 9 x 11 */
static const uint16_t PUMP[11] = {
    0x0FC, 0x084, 0x094, 0x0FA, 0x0FA, 0x0FA, 0x0FA, 0x0F4, 0x0F4, 0x0F4, 0x1FF,
};

static void draw_pump(hm_canvas_t *c, int x0, int y0, uint16_t fg, uint16_t bg)
{
    fill_rect(c, x0 - 1, y0 - 1, x0 + 9, y0 + 11, bg);
    for (int y = 0; y < 11; y++)
        for (int x = 0; x < 9; x++)
            if (PUMP[y] >> (8 - x) & 1) {
                int px = x0 + x, py = y0 + y;
                if (px >= 0 && py >= 0 && px < c->w && py < c->h) c->px[(size_t)py * c->w + px] = fg;
            }
}

static void draw_point(hm_renderer_t *r, hm_canvas_t *c, const hm_feature_t *f, const xform_t *t,
                       const hm_palette_t *pal, int level)
{
    float x = t->ox + (float)hm_px(f->pts, 0) * t->k, y = t->oy - (float)hm_py(f->pts, 0) * t->k;
    if (x < -20 || y < -20 || x > (float)c->w + 20 || y > (float)c->h + 20) return;
    int ix = (int)lrintf(x), iy = (int)lrintf(y);
    if (f->cls == HM_FUEL) {
        if (level <= 1) draw_pump(c, ix - 4, iy - 5, pal->fuel, pal->bg);
        return;
    }
    int s = f->cls == HM_CITY ? 4 : f->cls == HM_TOWN ? 3 : f->cls == HM_SUBURB ? 0 : 2;
    if (s) {
        fill_rect(c, ix - s - 2, iy - s - 2, ix + s + 1, iy + s + 1, pal->bg);
        fill_rect(c, ix - s, iy - s, ix + s - 1, iy + s - 1, pal->place);
    }
    if (f->name && x >= 0 && y >= 0 && x < (float)c->w && y < (float)c->h)
        candidate(r, f->name, f->name_len, (float)(f->rank + 1), x, y, HM_LABEL_PLACE, f->cls, place_prio(f->cls, f->rank));
}

static int is_road(int cls) { return cls >= HM_MOTORWAY && cls <= HM_LINK; }

/* ---- the view ------------------------------------------------------------------------ */

typedef struct { int c0, c1, r0, r1; } span_t;

static span_t view_span(const hm_header_t *h, const hm_view_t *v, int w, int hpx, int margin)
{
    const hm_level_t *l = &h->level[v->level];
    double hw = (w / 2.0 + 12) * v->mpp, hh = (hpx / 2.0 + 12) * v->mpp, s = l->tile_m;
    span_t sp = {(int)floor((v->cx - hw - l->origin_x) / s) - margin, (int)floor((v->cx + hw - l->origin_x) / s) + margin,
                 (int)floor((v->cy - hh - l->origin_y) / s) - margin, (int)floor((v->cy + hh - l->origin_y) / s) + margin};
    if (sp.c0 < 0) sp.c0 = 0;
    if (sp.r0 < 0) sp.r0 = 0;
    if (sp.c1 > l->cols - 1) sp.c1 = l->cols - 1;
    if (sp.r1 > l->rows - 1) sp.r1 = l->rows - 1;
    return sp;
}

void hm_view_tiles(const hm_header_t *h, const hm_view_t *v, int w, int hpx, int margin,
                   void (*fn)(void *ctx, int level, int col, int row), void *ctx)
{
    if (v->level < 0 || v->level >= h->n_levels) return;
    span_t sp = view_span(h, v, w, hpx, margin);
    for (int row = sp.r0; row <= sp.r1; row++)
        for (int col = sp.c0; col <= sp.c1; col++) fn(ctx, v->level, col, row);
}

#define MAX_VIEW_TILES 36

/* The drive's trail: breadcrumbs, a dot every 10 px along it, accent on a bg
 * ring, so the road it was driven on still shows between them. */
static void draw_trail(hm_canvas_t *c, const hm_view_t *v)
{
    float hx = (float)c->w / 2, hy = (float)c->h / 2, carry = 0;
    for (int i = 1; i < v->n_trail; i++) {
        float ax = hx + (float)((v->trail[i - 1].x - v->cx) / v->mpp), ay = hy - (float)((v->trail[i - 1].y - v->cy) / v->mpp);
        float bx = hx + (float)((v->trail[i].x - v->cx) / v->mpp), by = hy - (float)((v->trail[i].y - v->cy) / v->mpp);
        float len = sqrtf((bx - ax) * (bx - ax) + (by - ay) * (by - ay)), at = carry;
        if (len > 400) { carry = 0; continue; }                    /* a jump in the fixes: no crumbs across it */
        for (; at < len; at += 10) {
            float x = ax + (bx - ax) * at / len, y = ay + (by - ay) * at / len;
            seg(c, x, y, x, y, 3.5f, v->pal->bg);
            seg(c, x, y, x, y, 2.0f, v->pal->trail);
        }
        carry = at - len;
    }
}

static void draw_pins(hm_renderer_t *r, hm_canvas_t *c, const hm_view_t *v)
{
    float hx = (float)c->w / 2, hy = (float)c->h / 2;
    for (int i = 0; i < v->n_pins; i++) {
        float x = hx + (float)((v->pins[i].x - v->cx) / v->mpp), y = hy - (float)((v->pins[i].y - v->cy) / v->mpp);
        if (x < -12 || y < -12 || x > (float)c->w + 12 || y > (float)c->h + 12) continue;
        if (v->pins[i].kind == HM_PIN_STALL) {                    /* octagon, as on every alarm */
            ngon(r, c, x, y, 10.5f, 8, 0.3927f, v->pal->bg);
            ngon(r, c, x, y, 8.0f, 8, 0.3927f, v->pal->stall);
        } else {                                                  /* diamond */
            ngon(r, c, x, y, 10.0f, 4, 0, v->pal->bg);
            ngon(r, c, x, y, 7.5f, 4, 0, v->pal->mark);
        }
    }
}

int hm_render(hm_renderer_t *r, hm_store_t *s, const hm_view_t *v, hm_canvas_t *c, hm_render_out_t *out)
{
    memset(out, 0, sizeof(*out));
    r->n_cand = 0;
    const hm_palette_t *pal = v->pal;
    for (int i = 0; i < c->w * c->h; i++) c->px[i] = pal->bg;
    const hm_header_t *h = s ? hm_store_header(s) : NULL;
    int64_t t0 = r->env.micros ? r->env.micros() : 0;

    const hm_tile_t *tiles[MAX_VIEW_TILES];
    xform_t xf[MAX_VIEW_TILES];
    int n = 0;
    if (h && v->level >= 0 && v->level < h->n_levels) {
        hm_store_frame(s);
        const hm_level_t *l = &h->level[v->level];
        span_t sp = view_span(h, v, c->w, c->h, 0);
        double unit = (double)l->tile_m / HM_Q;
        for (int row = sp.r0; row <= sp.r1; row++)
            for (int col = sp.c0; col <= sp.c1 && n < MAX_VIEW_TILES; col++) {
                int err;
                out->tiles++;
                const hm_tile_t *t = hm_store_get(s, v->level, col, row, &err);
                if (err) out->errors++;
                if (!t) continue;
                double tcx = l->origin_x + (col + 0.5) * l->tile_m, tcy = l->origin_y + (row + 0.5) * l->tile_m;
                xf[n].ox = (float)((tcx - v->cx) / v->mpp) + (float)c->w / 2;
                xf[n].oy = (float)c->h / 2 - (float)((tcy - v->cy) / v->mpp);
                xf[n].k = (float)(unit / v->mpp);
                tiles[n++] = t;
            }
    }
    int64_t t1 = r->env.micros ? r->env.micros() : 0;
    out->tiles_drawn = n;

    for (int li = 0; li < N_LAYERS; li++) {
        int cls = LAYERS[li];
        if (li == TRAIL_LAYER) draw_trail(c, v);
        int w = hm_line_width(cls, v->level);
        for (int i = 0; i < n; i++) {
            hm_iter_t it;
            hm_feature_t f;
            hm_iter_init(&it, tiles[i]);
            while (hm_iter_next(&it, &f)) {
                if (f.cls != cls) continue;
                out->features++;
                if (f.geom == HM_AREA) {
                    if (draw_area(r, c, &f, &xf[i], pal->water)) out->errors++;
                } else if (f.geom == HM_POINT) {
                    draw_point(r, c, &f, &xf[i], pal, v->level);
                } else if (w > 0 && f.npts >= 2) {
                    uint16_t col = cls == HM_RAIL ? pal->rail : (cls == HM_RIVER || cls == HM_CANAL) ? pal->water : pal->road;
                    draw_line(c, &f, &xf[i], (float)w / 2, col, cls == HM_RAIL);
                    if (cls == HM_MOTORWAY && w >= 6) draw_line(c, &f, &xf[i], (float)(w - 4) / 2, pal->bg, 0);
                    if (is_road(cls)) road_label(r, c, v, &f, &xf[i]);
                }
            }
        }
    }
    draw_pins(r, c, v);

    qsort(r->cand, (size_t)r->n_cand, sizeof(cand_t), cmp_cand);
    for (int i = 0; i < r->n_cand && out->n_labels < HM_MAX_LABELS; i++) {
        hm_label_t *lb = &out->labels[out->n_labels++];
        memcpy(lb->text, r->cand[i].text, HM_LABEL_LEN);
        lb->x = r->cand[i].x; lb->y = r->cand[i].y;
        lb->kind = r->cand[i].kind; lb->cls = r->cand[i].cls; lb->prio = r->cand[i].prio;
    }
    if (r->env.micros) {
        int64_t t2 = r->env.micros();
        out->us_load = (uint32_t)(t1 - t0);
        out->us_draw = (uint32_t)(t2 - t1);
    }
    return HM_OK;
}
