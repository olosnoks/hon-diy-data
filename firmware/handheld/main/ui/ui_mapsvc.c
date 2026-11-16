#include <math.h>
#include <stddef.h>
#include <string.h>

#include "ui.h"
#include "ui_mapsvc.h"

/* metres per pixel of each detail level: the builder simplified level n for this scale */
static const float MPP[HM_MAX_LEVELS] = {4.0f, 10.0f, 25.0f, 60.0f};

#define REOPEN_MS 3000          /* no MAP folder: look again this often (only while asked to draw) */
#define PREFETCH_MAX 48
#define HOLD_WAIT_MS 3000       /* mapsvc_hold(1): most it waits for a picture in progress */

static struct {
    bool init;
    /* shared: under ui_port_map_lock() */
    mapsvc_req_t *pending;
    bool has_pending, drawing, reopen, hold;
    uint32_t seq, pending_seq;
    int shown, ready;                   /* canvas index, -1 = none */
    /* the worker's own */
    mapsvc_req_t *work;
    mapsvc_req_t *scratch;              /* the UI's */
    uint16_t *canvas[2];
    mapsvc_result_t res[2];
    hm_store_t *store;
    bool store_open;                    /* store != NULL, readable by other tasks (under the lock) */
    hm_renderer_t *rend;
    int store_err;
    bool tried;
    int64_t t_tried_us;
    hm_pt_t *trail_m;
    hm_pin_t pins_m[MAPSVC_PINS];
    struct { int8_t level; int16_t col, row; } pre[PREFETCH_MAX];
    int n_pre, i_pre;
} m;

static void *zalloc(size_t n)
{
    void *p = ui_port_map_alloc(n);
    if (p) memset(p, 0, n);
    return p;
}

static void *alloc_env(size_t n) { return ui_port_map_alloc(n); }
static void free_env(void *p) { ui_port_map_free(p); }
static const hm_env_t ENV = {alloc_env, free_env, ui_port_micros};

float mapsvc_mpp(int level) { return MPP[level < 0 ? 0 : level >= HM_MAX_LEVELS ? HM_MAX_LEVELS - 1 : level]; }

void mapsvc_init(void)
{
    if (m.init) return;
    m.shown = m.ready = -1;
    m.pending = zalloc(sizeof(mapsvc_req_t));
    m.work = zalloc(sizeof(mapsvc_req_t));
    m.scratch = zalloc(sizeof(mapsvc_req_t));
    m.init = m.pending && m.work && m.scratch;
}

mapsvc_req_t *mapsvc_scratch(void)
{
    mapsvc_init();
    return m.scratch;
}

uint32_t mapsvc_submit(const mapsvc_req_t *req)
{
    mapsvc_init();
    if (!m.init) return 0;
    size_t head = offsetof(mapsvc_req_t, trail);
    ui_port_map_lock();
    memcpy(m.pending, req, head);                          /* only what is used */
    memcpy(m.pending->trail, req->trail, req->n_trail * sizeof(req->trail[0]));
    memcpy(m.pending->pins, req->pins, req->n_pins * sizeof(req->pins[0]));
    m.has_pending = true;
    uint32_t seq = m.pending_seq = ++m.seq;
    ui_port_map_unlock();
    ui_port_map_kick();
    return seq;
}

const mapsvc_result_t *mapsvc_take(void)
{
    if (!m.init) return NULL;
    const mapsvc_result_t *r = NULL;
    ui_port_map_lock();
    if (m.ready >= 0) {
        m.shown = m.ready;
        m.ready = -1;
        r = &m.res[m.shown];
    }
    ui_port_map_unlock();
    if (r) ui_port_map_kick();                             /* the worker may draw the next one now */
    return r;
}

void mapsvc_reopen(void)
{
    if (!m.init) return;
    ui_port_map_lock();
    m.reopen = true;
    ui_port_map_unlock();
}

int mapsvc_hold(int on)
{
    mapsvc_init();
    if (!m.init) return 0;                                 /* no worker, no open files */
    ui_port_map_lock();
    m.hold = on != 0;
    ui_port_map_unlock();
    ui_port_map_kick();                                    /* the worker closes the files (and later reopens) */
    if (!on) return 0;
    for (int waited = 0; waited <= HOLD_WAIT_MS; waited += 10) {
        ui_port_map_lock();
        bool closed = !m.drawing && !m.store_open;
        ui_port_map_unlock();
        if (closed) return 0;
        ui_port_map_wait(10);
    }
    return -1;
}

bool mapsvc_busy(void)
{
    if (!m.init) return false;
    ui_port_map_lock();
    bool busy = m.has_pending || m.drawing;
    ui_port_map_unlock();
    return busy;
}

/* ---- the worker ---------------------------------------------------------------- */

static bool ensure_memory(void)
{
    for (int i = 0; i < 2; i++)
        if (!m.canvas[i] && !(m.canvas[i] = ui_port_map_alloc((size_t)MAPSVC_W * MAPSVC_H * 2))) return false;
    if (!m.trail_m && !(m.trail_m = ui_port_map_alloc(MAPSVC_TRAIL * sizeof(hm_pt_t)))) return false;
    if (!m.rend && !(m.rend = hm_renderer_new(&ENV))) return false;
    return true;
}

static void set_store(hm_store_t *s)
{
    ui_port_map_lock();
    m.store = s;
    m.store_open = s != NULL;
    ui_port_map_unlock();
}

/* A MAP/ upload has the folder: the worker's files are closed and stay closed. */
static bool held(void)
{
    ui_port_map_lock();
    bool h = m.hold;
    ui_port_map_unlock();
    if (h && m.store) {
        hm_store_close(m.store);
        set_store(NULL);
        m.tried = false;
        m.n_pre = m.i_pre = 0;
    }
    return h;
}

static void ensure_store(void)
{
    if (m.store || held()) return;
    int64_t now = ui_port_micros();
    if (m.tried && now - m.t_tried_us < (int64_t)REOPEN_MS * 1000) return;
    m.tried = true;
    m.t_tried_us = now;
    hm_store_t *s = NULL;
    m.store_err = hm_store_open(&s, ui_port_map_dir(), MAPSVC_CACHE_BYTES, &ENV);
    set_store(s);
}

static void add_pre(void *ctx, int level, int col, int row)
{
    (void)ctx;
    if (m.n_pre >= PREFETCH_MAX || hm_store_cached(m.store, level, col, row)) return;
    m.pre[m.n_pre].level = (int8_t)level;
    m.pre[m.n_pre].col = (int16_t)col;
    m.pre[m.n_pre].row = (int16_t)row;
    m.n_pre++;
}

/* After a picture: the ring of tiles around it, then the view one level
 * either side, so that driving on or a change of zoom finds them cached. */
static void plan_prefetch(const hm_view_t *v)
{
    m.n_pre = m.i_pre = 0;
    if (!m.store) return;
    const hm_header_t *h = hm_store_header(m.store);
    hm_view_tiles(h, v, MAPSVC_W, MAPSVC_H, 1, add_pre, NULL);
    for (int d = -1; d <= 1; d += 2) {
        hm_view_t o = *v;
        o.level = v->level + d;
        if (o.level < 0 || o.level >= h->n_levels) continue;
        o.mpp = MPP[o.level];
        hm_view_tiles(h, &o, MAPSVC_W, MAPSVC_H, 0, add_pre, NULL);
    }
}

static bool prefetch_one(void)
{
    if (!m.store || m.i_pre >= m.n_pre) return false;
    int err;
    hm_store_get(m.store, m.pre[m.i_pre].level, m.pre[m.i_pre].col, m.pre[m.i_pre].row, &err);
    m.i_pre++;
    if (err == HM_ERR_NOMEM) m.i_pre = m.n_pre;            /* the cache is full of this view: stop */
    return m.i_pre < m.n_pre;
}

bool mapsvc_work(void)
{
    if (!m.init) return false;
    held();                                                /* closes the files when an upload has asked for them */
    ui_port_map_lock();
    if (!m.has_pending || m.ready >= 0) {
        ui_port_map_unlock();
        return prefetch_one();
    }
    mapsvc_req_t *t = m.work;
    m.work = m.pending;
    m.pending = t;
    m.has_pending = false;
    m.drawing = true;
    uint32_t seq = m.pending_seq;
    int target = m.shown == 0 ? 1 : 0;
    bool reopen = m.reopen;
    m.reopen = false;
    ui_port_map_unlock();
    if (reopen) {
        if (m.store) hm_store_close(m.store);
        set_store(NULL);
        m.tried = false;
        m.n_pre = m.i_pre = 0;
    }

    int64_t t0 = ui_port_micros();
    const mapsvc_req_t *q = m.work;
    mapsvc_result_t *r = &m.res[target];
    memset(r, 0, sizeof(*r));
    r->seq = seq;
    r->lat = q->lat; r->lon = q->lon; r->mpp = q->mpp; r->level = q->level; r->night = q->night;

    if (!ensure_memory()) {
        r->status = MAPSVC_NO_MEMORY;
        r->err = HM_ERR_NOMEM;
    } else {
        ensure_store();
        hm_canvas_t cv = {m.canvas[target], MAPSVC_W, MAPSVC_H};
        r->px = cv.px;
        hm_view_t v = {0};
        v.mpp = q->mpp;
        v.level = q->level;
        v.pal = &q->pal;
        memcpy(v.inset, q->inset, sizeof(v.inset));
        if (m.store) {
            const hm_header_t *h = hm_store_header(m.store);
            hm_project(h, q->lat, q->lon, &v.cx, &v.cy);
            for (int i = 0; i < q->n_trail; i++) {
                double x, y;
                hm_project(h, q->trail[i].lat, q->trail[i].lon, &x, &y);
                m.trail_m[i].x = (float)x;
                m.trail_m[i].y = (float)y;
            }
            for (int i = 0; i < q->n_pins; i++) {
                double x, y;
                hm_project(h, q->pins[i].lat, q->pins[i].lon, &x, &y);
                m.pins_m[i] = (hm_pin_t){(float)x, (float)y, q->pins[i].kind};
            }
            v.trail = m.trail_m; v.n_trail = q->n_trail;
            v.pins = m.pins_m; v.n_pins = q->n_pins;
            r->kx = h->kx; r->ky = h->ky;
            r->radius_m = h->radius_m; r->lat0 = h->lat0; r->lon0 = h->lon0;
            r->status = MAPSVC_OK;
        } else {
            r->status = held() ? MAPSVC_UPDATING : m.store_err == HM_ERR_NOFILE ? MAPSVC_NO_MAP : MAPSVC_BAD_MAP;
            r->err = m.store_err;
        }
        hm_render(m.rend, m.store, &v, &cv, &r->out);
        if (m.store) {
            r->stats = *hm_store_stats(m.store);
            plan_prefetch(&v);
        }
    }
    r->render_us = (uint32_t)(ui_port_micros() - t0);

    ui_port_map_lock();
    m.ready = target;
    m.drawing = false;
    bool more = m.has_pending;
    ui_port_map_unlock();
    return more || m.n_pre > 0;
}
