/* Unit tests of hhcore on the host. `make` builds and runs this; it exits
 * non-zero if any check fails. The Python suite (tests/test_hh_*.py) adds
 * what needs the laptop code: planner, scheduler and formula parity with the
 * Python, and reading these files with the Python parser and importer. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "hhhost.h"
#include "hhsim.h"

static int failures, checks;
static const char *current = "";

#define CHECK(cond) do { checks++; if (!(cond)) { failures++; \
    printf("  FAIL %s:%d [%s] %s\n", __FILE__, __LINE__, current, #cond); } } while (0)
#define NEAR(a, b, eps) (fabs((double)(a) - (double)(b)) <= (eps))
#define TEST(name) static void name(void)
#define RUN(name) do { current = #name; int before = failures; name(); \
    printf("%-34s %s\n", #name, failures == before ? "ok" : "FAILED"); } while (0)

static char TMP[200];
static int tmp_n;
static const char *fresh_dir(void)
{
    static char path[256];
    snprintf(path, sizeof(path), "%s/sd%d", TMP, tmp_n++);
    mkdir(path, 0777);
    return path;
}

static hh_map_t MAP;
static int ch_index(const char *name)
{
    for (int i = 0; i < MAP.n_ch; i++) {
        hh_map_chan_t c;
        hh_map_chan(&MAP, i, &c);
        if (strcmp(c.name, name) == 0) return i;
    }
    return -1;
}
static int alarm_index(const char *name)
{
    for (int i = 0; i < MAP.n_alarm; i++) {
        hh_map_alarm_t a;
        hh_map_alarm(&MAP, i, &a);
        if (strcmp(a.name, name) == 0) return i;
    }
    return -1;
}

/* ---- a small reader of drive files, by the rules of FORMAT.md ------------- */

typedef struct {
    hh_file_hdr_t hdr;
    int ok, blocks, reads, events, gps, captures, ended, bad, stall_events, markers, times, iso, alarms;
    uint32_t last_seq;
    long used;
} scan_t;

static void scan(const char *path, scan_t *s)
{
    memset(s, 0, sizeof(*s));
    FILE *f = fopen(path, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *d = malloc((size_t)size + 1);
    if (fread(d, 1, (size_t)size, f) != (size_t)size) { fclose(f); free(d); return; }
    fclose(f);
    if (size < (long)sizeof(s->hdr)) { free(d); return; }
    memcpy(&s->hdr, d, sizeof(s->hdr));
    if (s->hdr.magic != HH_FILE_MAGIC || s->hdr.crc != hh_crc32(0, d, offsetof(hh_file_hdr_t, crc))) { free(d); return; }
    s->ok = 1;
    long pos = sizeof(s->hdr);
    int64_t last = -1;
    while (pos + 28 <= size && !s->ended) {
        hh_block_hdr_t b;
        memcpy(&b, d + pos, sizeof(b));
        int good = b.magic == HH_BLOCK_MAGIC && b.payload_len <= 4096 && pos + 28 + (long)b.payload_len <= size
                   && (int64_t)b.seq > last;
        if (good) {
            uint32_t crc = hh_crc32(0, &s->hdr.nonce, 4), want;
            crc = hh_crc32(crc, d + pos, 24 + b.payload_len);
            memcpy(&want, d + pos + 24 + b.payload_len, 4);
            good = crc == want;
        }
        if (!good) { s->bad++; pos++; continue; }
        const uint8_t *p = d + pos + 24;
        last = b.seq; s->last_seq = b.seq; s->blocks++;
        if (b.type == HH_B_READS) s->reads += b.count;
        if (b.type == HH_B_ISO) s->iso += b.count;
        if (b.type == HH_B_GPS) s->gps += b.count;
        if (b.type == HH_B_CAPTURE) s->captures += b.count;
        if (b.type == HH_B_TIME) s->times += b.count;
        if (b.type == HH_B_END) s->ended = 1;
        if (b.type == HH_B_EVENTS) {
            for (uint32_t i = 0; i + 16 <= b.payload_len; i += 16) {
                hh_rec_event_t e;
                memcpy(&e, p + i, sizeof(e));
                s->events++;
                if (e.code == HH_EV_STALL) s->stall_events++;
                if (e.code == HH_EV_MARKER) s->markers++;
                if (e.code == HH_EV_ALARM_RAISED) s->alarms++;
            }
        }
        pos += 28 + (long)b.payload_len;
        s->used = pos;
    }
    free(d);
}

static const char *drive_path(const char *root, const char *dir, char letter, int no)
{
    static char path[320];
    snprintf(path, sizeof(path), "%s/%s/%c%06d.HHD", root, dir, letter, no);
    return path;
}

/* ---- protocol --------------------------------------------------------------- */

TEST(test_framing)
{
    hh_framing_t f;
    uint8_t req[5], reply[32], data[16], buf[64];
    hh_framing_default(&f);
    CHECK(hh_build_read_request(&f, 0x10, 4, req) == 5);
    CHECK(req[0] == 0x20 && req[1] == 0x05 && req[2] == 0x10 && req[3] == 0x04 && hh_verify(req, 5));
    CHECK(hh_checksum((const uint8_t *)"\x20\x05\x00\x10", 4) == 0xCB);
    const uint8_t payload[4] = {1, 2, 3, 4};
    size_t n = hh_build_reply(&f, payload, 4, reply);
    CHECK(n == 7 && reply[0] == 0 && reply[1] == 7 && hh_verify(reply, 7));

    /* echo, then the reply */
    memcpy(buf, req, 5); memcpy(buf + 5, reply, 7);
    CHECK(hh_parse_reply(&f, buf, 12, req, 5, 4, data) == HH_OK && memcmp(data, payload, 4) == 0);
    /* junk before the echo and between echo and reply */
    buf[0] = 0xFF; memcpy(buf + 1, req, 5); buf[6] = 0x55; memcpy(buf + 7, reply, 7);
    CHECK(hh_parse_reply(&f, buf, 14, req, 5, 4, data) == HH_OK);
    /* a cable that does not echo */
    CHECK(hh_parse_reply(&f, reply, 7, req, 5, 4, data) == HH_OK);
    /* nothing; echo only */
    CHECK(hh_parse_reply(&f, buf, 0, req, 5, 4, data) == HH_E_NO_REPLY);
    CHECK(hh_parse_reply(&f, req, 5, req, 5, 4, data) == HH_E_NO_REPLY);
    /* cut short */
    memcpy(buf, req, 5); memcpy(buf + 5, reply, 4);
    CHECK(hh_parse_reply(&f, buf, 9, req, 5, 4, data) == HH_E_SHORT);
    /* a flipped bit */
    memcpy(buf + 5, reply, 7); buf[8] ^= 1;
    CHECK(hh_parse_reply(&f, buf, 12, req, 5, 4, data) == HH_E_CHECKSUM);
    /* bytes that are no frame */
    memset(buf + 5, 0x77, 7);
    CHECK(hh_parse_reply(&f, buf, 12, req, 5, 4, data) == HH_E_BAD_FRAME);
    /* a damaged echo does not hide a good reply */
    memcpy(buf, req, 5); buf[2] ^= 0x40; memcpy(buf + 5, reply, 7);
    CHECK(hh_parse_reply(&f, buf, 12, req, 5, 4, data) == HH_OK);
}

TEST(test_read_only_guard)
{
    hh_framing_t f;
    uint8_t req[5];
    hh_framing_default(&f);
    hh_build_read_request(&f, 0, 16, req);
    CHECK(hh_frame_allowed(&f, req, 5));
    CHECK(hh_frame_allowed(&f, f.init_seq, f.init_len));
    req[0] = 0x21; req[4] = hh_checksum(req, 4);           /* another command, good checksum */
    CHECK(!hh_frame_allowed(&f, req, 5));
    hh_build_read_request(&f, 0, 16, req); req[4] ^= 1;     /* a read with a bad checksum */
    CHECK(!hh_frame_allowed(&f, req, 5));
    const uint8_t write6[6] = {0x20, 0x06, 0x00, 0x01, 0x55, 0x84};
    CHECK(!hh_frame_allowed(&f, write6, 6));
    /* ISO: Mode 01 and 03 only */
    const uint8_t m1[6] = {0x68, 0x6A, 0xF1, 0x01, 0x0C, 0xD0};
    const uint8_t m3[5] = {0x68, 0x6A, 0xF1, 0x03, 0xC6};
    const uint8_t m4[5] = {0x68, 0x6A, 0xF1, 0x04, 0xC7};
    const uint8_t m2[7] = {0x68, 0x6A, 0xF1, 0x02, 0x0C, 0x00, 0xD1};
    CHECK(hh_iso_allowed(m1, 6) && hh_iso_allowed(m3, 5));
    CHECK(!hh_iso_allowed(m4, 5) && !hh_iso_allowed(m2, 7));

    /* a whole session: the simulator saw nothing but reads and the wake-up bytes */
    hhsim_reset();
    hhhost_t *h = hhhost_new(fresh_dir(), NULL, 0, NULL, hhsim_callbacks(), 1, 0);
    hhhost_run(h, 200);
    CHECK(hh_core_link_stats(hhhost_core(h))->blocked == 0);
    CHECK(SIM.bad_frames_sent == 0 && SIM.mode4_seen == 0);
    hhhost_free(h);
}

/* ---- link with faults --------------------------------------------------------- */

static void *tr_ctx_dummy;
static int w_open(void *c) { (void)c; return hhsim_callbacks()->open(); }
static void w_close(void *c) { (void)c; hhsim_callbacks()->close(); }
static int w_write(void *c, const uint8_t *d, size_t n) { (void)c; return hhsim_callbacks()->write(d, n); }
static int w_read(void *c, uint8_t *b, size_t m, double t) { (void)c; return hhsim_callbacks()->read_some(b, m, t); }
static void w_flush(void *c) { (void)c; hhsim_callbacks()->flush_input(); }
static int w_baud(void *c, uint32_t b) { (void)c; return hhsim_callbacks()->set_baud(b); }
static int w_break(void *c, int on) { (void)c; return hhsim_callbacks()->set_break(on); }
static double w_now(void *c) { (void)c; return SIM.now; }
static void w_sleep(void *c, double s) { (void)c; if (s > 0) SIM.now += s; }

static void make_link(hh_link_t *l, hh_transport_t *tr)
{
    hh_framing_t f;
    hh_clock_t clk = {NULL, w_now, w_sleep};
    hh_framing_default(&f);
    memset(tr, 0, sizeof(*tr));
    tr->ctx = &tr_ctx_dummy; tr->open = w_open; tr->close = w_close; tr->write = w_write; tr->read_some = w_read;
    tr->flush_input = w_flush; tr->set_baud = w_baud; tr->set_break = w_break; tr->usb_latency_s = 0.001;
    hhsim_reset();
    tr->open(tr->ctx);
    hh_link_init(l, tr, &clk, &f);
}

static int data_matches(const hh_read_t *r)
{
    for (int i = 0; i < r->len; i++) if (r->data[i] != SIM.mem[(r->addr + i) & 0xFF]) return 0;
    return 1;
}

TEST(test_link_clean)
{
    hh_link_t l; hh_transport_t tr; hh_read_t r;
    make_link(&l, &tr);
    CHECK(hh_link_wake(&l) == HH_OK);
    for (int i = 0; i < 200; i++) {
        CHECK(hh_link_read_block(&l, (uint8_t)(i * 3), 16, -1, &r) == HH_OK);
        CHECK(data_matches(&r) && r.attempts == 1);
    }
    CHECK(l.stats.ok == 200 && l.stats.retries == 0 && l.echo_mode == 1);
    /* the timeout has come down from its 200 ms ceiling to near the measured overhead */
    CHECK(l.tm.response_timeout < 0.05 && l.tm.response_timeout >= 0.02);
    CHECK(NEAR(r.latency_s, 19 * (10.0 / 9600) + 0.0005, 1e-9));
    /* 200 reads of 16 bytes at 9600 baud: about 25 ms each on the wire plus think time */
    CHECK(SIM.now > 200 * 0.025 && SIM.now < 200 * 0.035 + 0.4);
}

TEST(test_link_each_fault)
{
    hh_link_t l; hh_transport_t tr; hh_read_t r;
    make_link(&l, &tr);
    CHECK(hh_link_read_block(&l, 0x00, 8, -1, &r) == HH_OK);

    SIM.drop_next = 1;                              /* silence once: the retry gets it */
    CHECK(hh_link_read_block(&l, 0x10, 8, -1, &r) == HH_OK && r.attempts == 2 && data_matches(&r));
    CHECK(l.stats.timeouts == 1 && l.stats.retries == 1);

    SIM.corrupt_next = 1;
    CHECK(hh_link_read_block(&l, 0x20, 8, -1, &r) == HH_OK && r.attempts == 2 && data_matches(&r));
    CHECK(l.stats.checksum_failures == 1);

    SIM.truncate_next = 1;
    CHECK(hh_link_read_block(&l, 0x30, 8, -1, &r) == HH_OK && r.attempts == 2 && data_matches(&r));
    CHECK(l.stats.short_replies == 1);

    SIM.junk_before_next = 1;                       /* junk in front of a good frame: no retry needed */
    CHECK(hh_link_read_block(&l, 0x40, 8, -1, &r) == HH_OK && r.attempts == 1 && data_matches(&r));
    CHECK(l.stats.junk_bytes > 0);

    SIM.junk_after_next = 1;                        /* junk after it must not reach the next exchange */
    CHECK(hh_link_read_block(&l, 0x50, 8, -1, &r) == HH_OK && data_matches(&r));
    CHECK(hh_link_read_block(&l, 0x58, 8, -1, &r) == HH_OK && data_matches(&r));

    /* A reply that is merely late (after the timeout) must not be taken for
     * the answer to the next, different request. Honda replies carry no address. */
    SIM.late_next_s = 0.12;
    CHECK(hh_link_read_block(&l, 0x60, 8, 0, &r) == HH_E_NO_REPLY);
    CHECK(hh_link_read_block(&l, 0xA0, 8, 0, &r) == HH_OK && r.addr == 0xA0 && data_matches(&r));

    /* failed for good after the retries */
    SIM.powered = 0;
    CHECK(hh_link_read_block(&l, 0x00, 8, -1, &r) == HH_E_NO_REPLY);
    SIM.powered = 1;
    CHECK(hh_link_read_block(&l, 0x00, 8, -1, &r) == HH_OK);

    /* cable pulled */
    SIM.port_ok = 0;
    CHECK(hh_link_read_block(&l, 0x00, 8, -1, &r) == HH_E_PORT);
    CHECK(SIM.bad_frames_sent == 0 && l.stats.blocked == 0);
}

TEST(test_link_no_echo_cable)
{
    hh_link_t l; hh_transport_t tr; hh_read_t r;
    make_link(&l, &tr);
    SIM.echo = 0;
    for (int i = 0; i < 20; i++) CHECK(hh_link_read_block(&l, (uint8_t)i, 4, -1, &r) == HH_OK && data_matches(&r));
    CHECK(l.echo_mode == 0 && l.stats.echo_mismatches == 0);
}

TEST(test_link_random_faults)
{
    hh_link_t l; hh_transport_t tr; hh_read_t r;
    int ok = 0, failed = 0;
    make_link(&l, &tr);
    SIM.p_drop = 0.05; SIM.p_corrupt = 0.05; SIM.p_truncate = 0.05; SIM.p_junk = 0.05;
    for (int i = 0; i < 5000; i++) {
        uint8_t addr = (uint8_t)(i * 13), len = (uint8_t)(1 + i % 16);
        if ((unsigned)addr + len > 256) len = (uint8_t)(256 - addr);
        if (i % 97 == 0) SIM.mem[addr] ^= 0x5A;     /* the data changes under it, too */
        int rc = hh_link_read_block(&l, addr, len, -1, &r);
        if (rc == HH_OK) { ok++; CHECK(r.addr == addr && r.len == len && data_matches(&r)); }
        else { failed++; CHECK(rc != HH_E_PORT && rc != HH_E_BLOCKED); }
    }
    /* A wrong answer is never accepted (the CHECKs above); most reads survive one retry. */
    CHECK(ok > 4700 && failed < 300);
    CHECK(l.stats.timeouts > 100 && l.stats.checksum_failures > 50 && l.stats.resyncs > 100);
    CHECK(SIM.bad_frames_sent == 0);
    /* the gap backed off no further than its ceiling, and recovers on a clean line */
    CHECK(l.tm.gap <= 0.05 + 1e-12);
    SIM.p_drop = SIM.p_corrupt = SIM.p_truncate = SIM.p_junk = 0;
    for (int i = 0; i < 200; i++) hh_link_read_block(&l, 0, 8, -1, &r);
    CHECK(NEAR(l.tm.gap, 0.002, 1e-9) && l.tm.response_timeout < 0.06);
}

/* ---- planner, scheduler --------------------------------------------------------- */

TEST(test_planner)
{
    hh_plan_ch_t ch[HH_MAX_CH];
    hh_plan_t plan;
    hh_cost_t cost;
    int n = 0;
    for (int i = 0; i < MAP.n_ch; i++) {
        hh_map_chan_t c;
        hh_map_chan(&MAP, i, &c);
        if (c.kind == HH_KIND_MATH) continue;
        ch[n].addr = c.addr; ch[n].length = c.length; ch[n].rate_hz = MAP.framing.rate_hz[c.rate_class];
        n++;
    }
    hh_cost_from_framing(&cost, &MAP.framing, -1, -1);
    CHECK(hh_plan_build(&plan, ch, n, MAP.framing.max_read_len, &cost) == HH_OK);
    CHECK(plan.n_blocks >= 3 && plan.n_blocks <= n);
    int covered = 0;
    for (int b = 0; b < plan.n_blocks; b++) {
        const hh_block_t *blk = &plan.blocks[b];
        CHECK(blk->length >= 1 && blk->length <= MAP.framing.max_read_len);
        if (b) CHECK(blk->addr >= plan.blocks[b - 1].addr + plan.blocks[b - 1].length);
        for (int k = 0; k < blk->count; k++) {
            const hh_plan_ch_t *c = &ch[plan.order[blk->first + k]];
            CHECK(c->addr >= blk->addr && c->addr + c->length <= blk->addr + blk->length);
            CHECK(c->rate_hz <= blk->rate_hz);
            covered++;
        }
    }
    CHECK(covered == n);
    /* The default map asks for a little more than 9600 baud carries (the
     * laptop's planner says 1.09 too): the fast class then runs at 18 Hz. */
    CHECK(NEAR(hh_plan_bus_load(&plan, &cost), 1.0916, 0.001));

    /* two far-apart fast bytes are two requests; two near ones are one */
    hh_plan_ch_t far2[2] = {{0x00, 1, 20.0}, {0x40, 1, 20.0}}, near2[2] = {{0x00, 1, 20.0}, {0x05, 1, 20.0}};
    CHECK(hh_plan_build(&plan, far2, 2, 16, &cost) == HH_OK && plan.n_blocks == 2);
    CHECK(hh_plan_build(&plan, near2, 2, 16, &cost) == HH_OK && plan.n_blocks == 1 && plan.blocks[0].length == 6);
    /* a slow byte far from a fast block is not dragged along at the fast rate */
    hh_plan_ch_t mixed[3] = {{0x00, 2, 20.0}, {0x02, 1, 20.0}, {0x0F, 1, 1.0}};
    CHECK(hh_plan_build(&plan, mixed, 3, 16, &cost) == HH_OK && plan.n_blocks == 2);
    CHECK(plan.blocks[0].length == 3 && plan.blocks[0].rate_hz == 20.0 && plan.blocks[1].rate_hz == 1.0);
}

TEST(test_scheduler)
{
    hh_plan_ch_t ch[4] = {{0x00, 2, 20.0}, {0x40, 2, 20.0}, {0x80, 1, 5.0}, {0xC0, 1, 1.0}};
    hh_plan_t plan;
    hh_sched_t s;
    hh_cost_t cost = {10.0 / 9600, 5, 3, 0.002, 0.002};
    int reads[4] = {0}, low_in_a_row = 0, worst_low_run = 0;
    CHECK(hh_plan_build(&plan, ch, 4, 16, &cost) == HH_OK && plan.n_blocks == 4);
    hh_sched_init(&s, &plan);
    double t = 0;
    for (int i = 0; i < 4; i++) {           /* never-read blocks first, the fast ones before the others */
        int pick = hh_sched_next(&s, t);
        t += hh_cost_request(&cost, plan.blocks[pick].length);
        hh_sched_done(&s, pick, t);
        if (i < 2) CHECK(s.top[pick]);
    }
    double t_start = t;
    while (t < t_start + 20.0) {
        int pick = hh_sched_next(&s, t);
        t += hh_cost_request(&cost, plan.blocks[pick].length);
        hh_sched_done(&s, pick, t);
        reads[pick]++;
        low_in_a_row = s.top[pick] ? 0 : low_in_a_row + 1;
        if (low_in_a_row > worst_low_run) worst_low_run = low_in_a_row;
    }
    /* slower classes close to their targets, the spare time to the fast class */
    CHECK(NEAR(reads[2] / 20.0, 5.0, 0.3) && NEAR(reads[3] / 20.0, 1.0, 0.1));
    CHECK(reads[0] / 20.0 > 20.0 && reads[1] / 20.0 > 20.0 && abs(reads[0] - reads[1]) <= 2);
    CHECK(worst_low_run == 1);
    /* a failing block is pushed back, the others carry on */
    int picks0 = 0;
    for (int i = 0; i < 200; i++) {
        int pick = hh_sched_next(&s, t);
        t += 0.02;
        if (pick == 0) { hh_sched_failed(&s, 0, t); picks0++; } else hh_sched_done(&s, pick, t);
    }
    CHECK(picks0 > 20 && picks0 < 110);
}

/* ---- expressions ----------------------------------------------------------------- */

TEST(test_expr)
{
    const double consts[2] = {0.716, 1e300};
    const float chv[2] = {3000.0f, NAN};
    const uint8_t raw[2] = {0x0C, 0x80};
    hh_expr_env_t env = {consts, 2, chv, 2, raw, 2, 3200.0, 1.5};
    const uint8_t rpm[] = {HH_OP_X, HH_OP_IMM, 4, HH_OP_DIV, HH_OP_END};
    const uint8_t map_[] = {HH_OP_X, HH_OP_CONST, 0, 0, HH_OP_MUL, HH_OP_IMM, 5, HH_OP_SUB, HH_OP_END};
    const uint8_t bit3[] = {HH_OP_BYTE, 0, HH_OP_IMM, 3, HH_OP_CALL, HH_FN_BIT, 2, HH_OP_END};
    const uint8_t div0[] = {HH_OP_X, HH_OP_IMM, 0, HH_OP_DIV, HH_OP_END};
    const uint8_t gate[] = {HH_OP_CH, 0, HH_OP_IMM, 200, HH_OP_GT, HH_OP_END};
    const uint8_t unknown[] = {HH_OP_CH, 1, HH_OP_IMM, 1, HH_OP_ADD, HH_OP_END};
    const uint8_t zero_and_unknown[] = {HH_OP_IMM, 0, HH_OP_CH, 1, HH_OP_AND, HH_OP_END};
    const uint8_t unknown_and_zero[] = {HH_OP_CH, 1, HH_OP_IMM, 0, HH_OP_AND, HH_OP_END};
    const uint8_t select[] = {HH_OP_IMM, 1, HH_OP_IMM, 7, HH_OP_CH, 1, HH_OP_SELECT, HH_OP_END};
    const uint8_t valid[] = {HH_OP_CH, 1, HH_OP_VALID, HH_OP_END};
    const uint8_t overflow[] = {HH_OP_CONST, 1, 0, HH_OP_CONST, 1, 0, HH_OP_MUL, HH_OP_END};
    const uint8_t no_end[] = {HH_OP_X};
    const uint8_t underflow[] = {HH_OP_ADD, HH_OP_END};
    const uint8_t bad_const[] = {HH_OP_CONST, 9, 0, HH_OP_END};
    const uint8_t poly[] = {HH_OP_X, HH_OP_IMM, 1, HH_OP_IMM, 2, HH_OP_IMM, 3, HH_OP_CALL, HH_FN_POLY, 4, HH_OP_END};
    CHECK(hh_expr_eval(rpm, sizeof(rpm), &env) == 800.0);
    CHECK(NEAR(hh_expr_eval(map_, sizeof(map_), &env), 3200 * 0.716 - 5, 1e-9));
    CHECK(hh_expr_eval(bit3, sizeof(bit3), &env) == 1.0);
    CHECK(isnan(hh_expr_eval(div0, sizeof(div0), &env)));
    CHECK(hh_expr_eval(gate, sizeof(gate), &env) == 1.0);
    CHECK(isnan(hh_expr_eval(unknown, sizeof(unknown), &env)));
    CHECK(hh_expr_eval(zero_and_unknown, sizeof(zero_and_unknown), &env) == 0.0);
    CHECK(isnan(hh_expr_eval(unknown_and_zero, sizeof(unknown_and_zero), &env)));
    CHECK(hh_expr_eval(select, sizeof(select), &env) == 7.0);
    CHECK(hh_expr_eval(valid, sizeof(valid), &env) == 0.0);
    CHECK(isnan(hh_expr_eval(overflow, sizeof(overflow), &env)));
    env.x = 2.0;
    CHECK(hh_expr_eval(poly, sizeof(poly), &env) == 1 + 2 * 2 + 3 * 4);
    /* a damaged program is refused by the loader and harmless if run anyway */
    CHECK(hh_expr_check(rpm, sizeof(rpm), 2, 2) == 0 && hh_expr_check(poly, sizeof(poly), 2, 2) == 0);
    CHECK(hh_expr_check(no_end, sizeof(no_end), 2, 2) != 0 && isnan(hh_expr_eval(no_end, sizeof(no_end), &env)));
    CHECK(hh_expr_check(underflow, 2, 2, 2) != 0 && isnan(hh_expr_eval(underflow, 2, &env)));
    CHECK(hh_expr_check(bad_const, 4, 2, 2) != 0 && isnan(hh_expr_eval(bad_const, 4, &env)));
    CHECK(hh_expr_check(gate, sizeof(gate), 2, 0) != 0);
}

/* ---- map and config --------------------------------------------------------------- */

TEST(test_map)
{
    const uint8_t *def;
    size_t len = hhhost_default_map(&def);
    hh_map_t m;
    CHECK(MAP.n_ch >= 22 && MAP.n_alarm >= 5 && MAP.framing.baud == 9600);
    CHECK(ch_index("rpm") == 0 && MAP.stall.rpm_channel == 0);
    CHECK(MAP.info.ecu_id_addr == 0x76 && MAP.info.ecu_id_len == 10);
    hh_map_chan_t c;
    hh_map_chan(&MAP, ch_index("ect"), &c);
    CHECK(c.addr == 0x10 && c.length == 1 && c.rate_class == 2 && NEAR(c.alarm_hi, 105, 1e-6) && isnan(c.warn_hi));
    hh_map_chan(&MAP, ch_index("batt"), &c);
    CHECK(NEAR(c.warn_lo, 12.0, 1e-6) && NEAR(c.warn_hi, 15.2, 1e-5) && isnan(c.alarm_lo));
    /* every damaged copy is refused */
    uint8_t *copy = malloc(len);
    int refused = 0;
    for (size_t i = 0; i < len; i += 7) {
        memcpy(copy, def, len);
        copy[i] ^= 0x04;
        if (hh_map_parse(&m, copy, len) != 0) refused++;
    }
    CHECK(refused == (int)((len + 6) / 7));
    CHECK(hh_map_parse(&m, def, len - 1) != 0 && hh_map_parse(&m, def, 10) != 0);
    free(copy);
}

TEST(test_config)
{
    hh_config_t c;
    char text[600];
    hh_config_default(&c);
    CHECK(c.drive_gap_s == 120 && c.sync_ms == 1000 && c.backend == HH_CFG_BACKEND_AUTO);
    const char *cfg = "# comment\nbackend = uart   # the L9637D\r\n drive_gap_s=60\nsync_ms = 5\nnonsense = 1\ngps_baud = x\n\n";
    CHECK(hh_config_parse(&c, cfg, strlen(cfg)) == 3);     /* out of range, unknown key, not a number */
    CHECK(c.backend == HH_CFG_BACKEND_UART && c.drive_gap_s == 60 && c.sync_ms == 1000 && c.gps_baud == 9600);
    size_t n = hh_config_dump(&c, text, sizeof(text));
    hh_config_t back;
    hh_config_default(&back);
    CHECK(n > 100 && hh_config_parse(&back, text, n) == 0 && memcmp(&back, &c, sizeof(c)) == 0);
}

/* ---- alarms ------------------------------------------------------------------------- */

TEST(test_alarm_hysteresis)
{
    /* ect_high: high 105, hysteresis 3, hold 2 s, latching, critical */
    hh_alarms_t a;
    hh_alarm_event_t ev[4];
    float v[HH_MAX_CH];
    uint8_t level[HH_MAX_CH];
    int ect = ch_index("ect"), rule = alarm_index("ect_high");
    CHECK(ect >= 0 && rule >= 0);
    for (int i = 0; i < HH_MAX_CH; i++) v[i] = NAN;
    hh_alarms_init(&a, &MAP);
    double t = 0;
#define FEED(val, dt, n_expected) do { v[ect] = (val); t += (dt); \
        CHECK(hh_alarms_update(&a, v, MAP.n_ch, t, ev, 4) == (n_expected)); } while (0)
    FEED(100, 0.1, 0);
    CHECK(a.st[rule].state == HH_AL_IDLE);
    FEED(106, 0.1, 0);                      /* over the limit: pending, not raised */
    CHECK(a.st[rule].state == HH_AL_PENDING);
    FEED(104, 0.5, 0);                      /* back under before the hold time: forgotten */
    CHECK(a.st[rule].state == HH_AL_IDLE);
    FEED(106, 0.1, 0);
    FEED(107, 1.0, 0);
    CHECK(a.st[rule].state == HH_AL_PENDING);
    FEED(107, 1.0, 1);                      /* held for 2 s: raised */
    CHECK(ev[0].kind == HH_ALEV_RAISED && ev[0].index == rule && ev[0].severity == 2 && NEAR(ev[0].value, 107, 1e-6));
    CHECK(hh_alarms_top(&a) == rule);
    hh_alarms_levels(&a, level, MAP.n_ch);
    CHECK(level[ect] == 2);
    /* sitting on the limit does not chatter: inside the 3 degree band it stays up */
    for (int i = 0; i < 20; i++) FEED(i & 1 ? 104.5f : 105.5f, 0.1, 0);
    CHECK(a.st[rule].state == HH_AL_ACTIVE && a.st[rule].count == 1);
    FEED(103, 0.1, 0);
    CHECK(a.st[rule].state == HH_AL_ACTIVE);
    /* a dropped link (no value) neither raises nor clears */
    FEED(NAN, 5.0, 0);
    CHECK(a.st[rule].state == HH_AL_ACTIVE);
    /* below the band: it would clear, but it latches until acknowledged */
    FEED(101, 0.1, 0);
    CHECK(a.st[rule].state == HH_AL_LATCHED && hh_alarms_top(&a) == rule);
    CHECK(hh_alarms_ack(&a, t, ev, 4) == 2 && ev[0].kind == HH_ALEV_ACK && ev[1].kind == HH_ALEV_CLEARED);
    CHECK(a.st[rule].state == HH_AL_IDLE && hh_alarms_top(&a) == -1);
    /* acknowledged while still hot: stays up, clears by itself when it cools */
    FEED(110, 0.1, 0);
    FEED(110, 2.0, 1);
    CHECK(hh_alarms_ack(&a, t, ev, 4) == 1 && a.st[rule].state == HH_AL_ACTIVE && a.st[rule].acknowledged);
    FEED(100, 0.1, 1);
    CHECK(ev[0].kind == HH_ALEV_CLEARED && a.st[rule].state == HH_AL_IDLE);
#undef FEED
}

TEST(test_alarm_gate_and_when)
{
    /* batt_low: low 12.0, hold 3 s, hysteresis 0.3, gate [rpm] > 400 */
    hh_alarms_t a;
    hh_alarm_event_t ev[4];
    float v[HH_MAX_CH];
    int batt = ch_index("batt"), rpm = ch_index("rpm"), rule = alarm_index("batt_low");
    int lean = alarm_index("lean_under_load"), o2 = ch_index("o2"), tps = ch_index("tps");
    CHECK(rule >= 0 && lean >= 0);
    for (int i = 0; i < HH_MAX_CH; i++) v[i] = NAN;
    hh_alarms_init(&a, &MAP);
    v[batt] = 11.5f; v[rpm] = 0;            /* engine off: the gate keeps it quiet */
    for (int i = 0; i < 50; i++) CHECK(hh_alarms_update(&a, v, MAP.n_ch, i * 0.1, ev, 4) == 0);
    CHECK(a.st[rule].state == HH_AL_IDLE);
    v[rpm] = 800;
    int raised = 0;
    for (int i = 50; i < 90; i++) raised += hh_alarms_update(&a, v, MAP.n_ch, i * 0.1, ev, 4);
    CHECK(raised == 1 && a.st[rule].state == HH_AL_ACTIVE);
    v[batt] = 12.2f;                        /* inside the band: stays */
    CHECK(hh_alarms_update(&a, v, MAP.n_ch, 9.1, ev, 4) == 0 && a.st[rule].state == HH_AL_ACTIVE);
    v[batt] = 12.4f;
    CHECK(hh_alarms_update(&a, v, MAP.n_ch, 9.2, ev, 4) == 1 && ev[0].kind == HH_ALEV_CLEARED);
    /* a `when` rule: [o2] < 0.2 && [tps] > 60 && [rpm] > 3000 for 0.5 s */
    v[o2] = 0.1f; v[tps] = 80; v[rpm] = 4000;
    CHECK(hh_alarms_update(&a, v, MAP.n_ch, 10.0, ev, 4) == 0);
    CHECK(hh_alarms_update(&a, v, MAP.n_ch, 10.6, ev, 4) == 1 && ev[0].index == lean);
    v[tps] = NAN;                           /* unknown input: hold */
    CHECK(hh_alarms_update(&a, v, MAP.n_ch, 12.0, ev, 4) == 0 && a.st[lean].state == HH_AL_ACTIVE);
    v[tps] = 10;                            /* clear_hold_s = 1 */
    CHECK(hh_alarms_update(&a, v, MAP.n_ch, 12.1, ev, 4) == 0);
    CHECK(hh_alarms_update(&a, v, MAP.n_ch, 13.2, ev, 4) == 1 && ev[0].kind == HH_ALEV_CLEARED);
}

TEST(test_slope)
{
    hh_slope_t s;
    s.w = 0.5;
    hh_slope_reset(&s);
    CHECK(isnan(hh_slope_update(&s, 0.0, 1000)));
    double r = 0;
    for (int i = 1; i <= 20; i++) r = hh_slope_update(&s, i * 0.05, 1000 - 400 * i * 0.05);
    CHECK(NEAR(r, -400.0, 1e-6));
    CHECK(isnan(hh_slope_update(&s, 0.0, 5)));      /* time went back: starts again */
}

/* ---- stall trigger ------------------------------------------------------------------- */

TEST(test_stall_trigger)
{
    hh_stall_t s;
    hh_map_stall_t cfg = MAP.stall;
    CHECK(cfg.rpm_run == 400 && cfg.rpm_zero == 50 && cfg.pre_s == 30 && cfg.post_s == 10);
    hh_stall_init(&s, &cfg);
    /* cranking and key-on: not armed, rpm 0 is not a stall */
    CHECK(hh_stall_feed(&s, 1.0, 0, 1) == 0 && !s.armed);
    CHECK(hh_stall_feed(&s, 2.0, 250, 1) == 0 && !s.armed);
    CHECK(hh_stall_feed(&s, 3.0, 800, 1) == 0 && s.armed);
    CHECK(hh_stall_feed(&s, 4.0, 300, 1) == 0);             /* low idle is not a stall */
    CHECK(hh_stall_feed(&s, 5.0, NAN, 1) == 0);             /* a read without rpm in it */
    CHECK(hh_stall_feed(&s, 6.0, 20, 1) == HH_STALL_RPM);
    CHECK(s.capturing && s.count == 1 && s.t_trigger == 6.0 && s.t_start == -24.0 && s.t_end == 16.0 && !s.armed);
    /* the key goes off after the stall: no second capture */
    CHECK(hh_stall_feed(&s, 7.0, 0, 1) == 0);
    CHECK(hh_stall_feed(&s, 8.0, NAN, 0) == 0 && hh_stall_feed(&s, 12.0, NAN, 0) == 0);
    CHECK(!hh_stall_window_done(&s, 15.9) && hh_stall_window_done(&s, 16.0) && !hh_stall_window_done(&s, 17.0));
    CHECK(s.count == 1 && !s.capturing);
    /* restart; this time the ECU loses power while running */
    CHECK(hh_stall_feed(&s, 30.0, NAN, 1) == 0 && hh_stall_feed(&s, 31.0, 900, 1) == 0 && s.armed);
    CHECK(hh_stall_feed(&s, 32.0, NAN, 0) == 0);            /* link just dropped */
    CHECK(hh_stall_feed(&s, 32.5, NAN, 0) == 0);            /* not yet a second */
    CHECK(hh_stall_feed(&s, 33.0, NAN, 0) == HH_STALL_LINK && s.last_reason == HH_STALL_LINK && s.count == 2);
    CHECK(hh_stall_feed(&s, 34.0, NAN, 0) == 0);
    /* a blip of silence shorter than link_down_s is nothing */
    hh_stall_init(&s, &cfg);
    hh_stall_feed(&s, 1.0, 900, 1);
    CHECK(hh_stall_feed(&s, 2.0, NAN, 0) == 0 && hh_stall_feed(&s, 2.4, 900, 1) == 0);
    CHECK(hh_stall_feed(&s, 3.2, NAN, 0) == 0 && s.armed && s.count == 0);
}

TEST(test_ring)
{
    uint8_t mem[64], out[16];
    hh_ring_t r;
    hh_ring_init(&r, mem, sizeof(mem));
    for (int i = 0; i < 100; i++) {
        uint8_t item[7] = {(uint8_t)i, 1, 2, 3, 4, 5, (uint8_t)~i};
        hh_ring_push(&r, item, (uint16_t)(3 + i % 5));
    }
    uint32_t pos = r.tail;
    int n = 0, first = -1, last = -1;
    uint16_t len;
    while ((len = hh_ring_peek(&r, &pos, out, sizeof(out))) != 0) {
        if (first < 0) first = out[0];
        CHECK(len == 3 + out[0] % 5 && (last < 0 || out[0] == last + 1));
        last = out[0];
        n++;
    }
    CHECK(last == 99 && n >= 7 && first == 100 - n);    /* the newest are kept, in order */
    hh_ring_pop(&r);
    pos = r.tail;
    CHECK(hh_ring_peek(&r, &pos, out, sizeof(out)) && out[0] == first + 1);
}

/* ---- GPS -------------------------------------------------------------------------------- */

TEST(test_nmea)
{
    hh_nmea_t g;
    memset(&g, 0, sizeof(g));
    const char *gga = "$GPGGA,123519,3217.100,N,09007.200,W,1,08,0.9,95.4,M,-29.0,M,,*48\r\n";
    const char *rmc = "$GPRMC,123519,A,3217.100,N,09007.200,W,022.4,084.4,151126,003.1,W*7F\r\n";
    int got = 0;
    for (const char *p = gga; *p; p++) got += hh_nmea_feed(&g, *p);
    CHECK(got == 2 && g.fix.sats == 8 && g.fix.hdop_c == 90);      /* a GGA with a fix and a time says so (2) */
    CHECK(g.gga_tod == 12 * 3600 + 35 * 60 + 19);
    got = 0;
    for (const char *p = rmc; *p; p++) got += hh_nmea_feed(&g, *p);
    CHECK(got == 1 && g.fix.fix == 1);
    CHECK(NEAR(g.fix.lat_e7 / 1e7, 32 + 17.1 / 60, 1e-6) && NEAR(g.fix.lon_e7 / 1e7, -(90 + 7.2 / 60), 1e-6));
    CHECK(NEAR(g.fix.speed_cms, 22.4 * 51.44, 2) && g.fix.course_cdeg == 8440);
    CHECK(g.fix.utc == 1794746119u);                    /* 2026-11-15 12:35:19 UTC */
    /* a damaged sentence is dropped */
    const char *bad = "$GPRMC,123520,A,3217.100,N,09007.200,W,099.9,084.4,151126,003.1,W*7F\r\n";
    got = 0;
    for (const char *p = bad; *p; p++) got += hh_nmea_feed(&g, *p);
    CHECK(got == 0 && NEAR(g.fix.speed_cms, 22.4 * 51.44, 2));
}

/* ---- the engine, end to end ---------------------------------------------------------------- */

TEST(test_engine_drive)
{
    hh_snapshot_t s;
    const char *root = fresh_dir();
    hhsim_reset();
    hhhost_t *h = hhhost_new(root, NULL, 0, "prealloc_mb = 1\n", hhsim_callbacks(), 7, 0);
    hhhost_run_until(h, 5.0);
    hhhost_snapshot(h, &s);
    CHECK(s.link.state == HH_LINK_OK && s.link.backend == HH_BACKEND_USB);
    CHECK(strcmp(s.link.ecu_id, "37820-P2P-") == 0);
    CHECK(s.n_ch == MAP.n_ch && s.seq > 50 && s.link.reads > 150 && s.link.errors == 0);
    CHECK(s.link.hz_fast > 17.0f && s.link.hz_fast < 21.0f);     /* 18 Hz: what the planner promises for this map */
    CHECK(NEAR(s.value[ch_index("rpm")], 800, 0.01) && NEAR(s.value[ch_index("batt")], 144 / 10.45, 0.001));
    CHECK(s.fresh[ch_index("rpm")] == 1);
    CHECK(NEAR(s.value[ch_index("inj_duty")], s.value[ch_index("inj")] * 800 / 1200, 0.01));
    CHECK(s.log.recording && s.log.drive_no == 1 && s.log.bytes > 2000 && s.log.sd_ok);
    CHECK(s.stall.armed && !s.stall.triggered && s.top_alarm_ch == -1);
    /* GPS: position, and the clock set once */
    hhhost_gps(h, "$GPGGA,123519,3217.100,N,09007.200,W,1,08,0.9,95.4,M,-29.0,M,,*48\r\n");
    hhhost_gps(h, "$GPRMC,123519,A,3217.100,N,09007.200,W,022.4,084.4,151126,003.1,W*7F\r\n");
    hhhost_snapshot(h, &s);
    CHECK(s.gps.fix && s.gps.sats == 8 && NEAR(s.gps.lat, 32.285, 1e-4) && NEAR(s.gps.speed_kmh, 41.5, 0.2));
    CHECK(NEAR(s.gps.hdop, 0.9, 1e-6) && s.time_valid && hhhost_time_set(h) == 1794746119000000LL);
    hh_core_mark(hhhost_core(h));
    hhhost_run_until(h, 8.0);

    /* the engine dies with the ECU still powered: "rpm dropped to 0" */
    hhsim_set_rpm(0);
    hhhost_run_until(h, 9.0);
    hhhost_snapshot(h, &s);
    CHECK(s.stall.triggered && s.stall.count == 1 && strcmp(s.stall.last_reason, "rpm dropped to 0") == 0);
    /* key off: silence. No second capture; after the post time the window closes */
    SIM.powered = 0;
    hhhost_run_until(h, 25.0);
    hhhost_snapshot(h, &s);
    CHECK(s.link.state == HH_LINK_OFF && s.stall.count == 1 && !s.stall.triggered && s.log.recording);
    CHECK(isnan(s.value[ch_index("rpm")]) && s.fresh[ch_index("rpm")] == 0 && s.link.hz_fast == 0);
    /* 120 s of silence ends the drive */
    hhhost_run_until(h, 8.5 + 119.0);
    hhhost_snapshot(h, &s);
    CHECK(s.log.recording);
    hhhost_run_until(h, 8.5 + 125.0);
    hhhost_snapshot(h, &s);
    CHECK(!s.log.recording && s.standby);
    CHECK(hh_core_idle_s(hhhost_core(h)) > 120.0);

    scan_t d;
    scan(drive_path(root, "DRIVES", 'D', 1), &d);
    CHECK(d.ok && d.ended && d.bad == 0 && d.hdr.kind == HH_FK_DRIVE && d.hdr.boot_no == 7 && d.hdr.file_no == 1);
    CHECK(memcmp(d.hdr.map_hash, MAP.hdr.map_hash, 12) == 0 && d.hdr.ecu_id_len == 10);
    CHECK(d.reads > 400 && d.gps == 1 && d.markers == 1 && d.stall_events == 1 && d.captures == 1 && d.times >= 1);
    CHECK((d.hdr.flags & HH_HF_PREALLOC) && d.hdr.prealloc == (1u << 20));
    struct stat st;
    CHECK(stat(drive_path(root, "DRIVES", 'D', 1), &st) == 0 && st.st_size == d.used);    /* cut to length at the end */
    /* the capture file: the seconds before the stall, written at once, then the post time */
    scan_t c;
    scan(drive_path(root, "CAPTURES", 'C', 1), &c);
    CHECK(c.ok && c.ended && c.hdr.kind == HH_FK_CAPTURE && c.hdr.drive_no == 1 && c.hdr.reason == HH_STALL_RPM);
    CHECK(c.reads > 400 && c.captures == 1 && c.hdr.trigger_t_us > 8000000 && c.hdr.trigger_t_us < 9000000);

    /* key on again: a new drive, by itself */
    SIM.powered = 1;
    hhsim_set_rpm(850);
    hhhost_run_until(h, 140.0);
    hhhost_snapshot(h, &s);
    CHECK(s.link.state == HH_LINK_OK && s.log.recording && s.log.drive_no == 2 && !s.standby);
    CHECK(hh_core_link_stats(hhhost_core(h))->blocked == 0 && SIM.bad_frames_sent == 0);
    hhhost_free(h);
    scan(drive_path(root, "DRIVES", 'D', 2), &d);
    CHECK(d.ok && d.ended && d.reads > 100 && d.stall_events == 0);
}

TEST(test_engine_link_loss_and_unplug)
{
    hh_snapshot_t s;
    const char *root = fresh_dir();
    hhsim_reset();
    hhsim_set_rpm(2500);
    hhhost_t *h = hhhost_new(root, NULL, 0, "iso_fallback = 0\nprealloc_mb = 0\n", hhsim_callbacks(), 1, 41);
    hhhost_run_until(h, 40.0);
    /* main relay drops out at speed: the ECU is gone mid-drive */
    SIM.powered = 0;
    double t_dead = SIM.now;
    hhhost_run_until(h, t_dead + 0.9);
    hhhost_snapshot(h, &s);
    CHECK(s.stall.count == 0);
    hhhost_run_until(h, t_dead + 1.6);
    hhhost_snapshot(h, &s);
    CHECK(s.stall.count == 1 && s.stall.triggered && strncmp(s.stall.last_reason, "link lost", 9) == 0);
    CHECK(s.link.state == HH_LINK_OFF && s.log.drive_no == 42);
    /* it comes back after 5 s: same drive, polling resumes, armed again once it runs */
    SIM.powered = 1;
    hhhost_run_until(h, t_dead + 6.0);
    hhhost_snapshot(h, &s);
    CHECK(s.link.state == HH_LINK_OK && s.log.drive_no == 42 && s.stall.armed && NEAR(s.value[0], 2500, 0.01));
    hhhost_run_until(h, t_dead + 20.0);

    /* the cable is pulled: PORT_LOST, reopened when it is back */
    SIM.port_ok = 0;
    uint32_t opens = SIM.opens;
    hhhost_run_until(h, SIM.now + 10.0);
    hhhost_snapshot(h, &s);
    CHECK(s.link.state == HH_LINK_ERROR && strstr(s.status, "USB") != NULL);
    CHECK(SIM.opens > opens + 3 && SIM.opens < opens + 12);     /* retried, with a growing pause */
    CHECK(s.stall.count == 2);                                  /* a running engine and a dead link: captured */
    SIM.port_ok = 1;
    hhhost_run_until(h, SIM.now + 5.0);
    hhhost_snapshot(h, &s);
    CHECK(s.link.state == HH_LINK_OK && s.log.drive_no == 42);
    CHECK(hh_core_link_stats(hhhost_core(h))->reconnects == 1);
    hhhost_free(h);

    scan_t d, c;
    scan(drive_path(root, "DRIVES", 'D', 42), &d);
    CHECK(d.ok && d.ended && d.stall_events == 2 && d.captures == 2 && d.bad == 0);
    CHECK(!(d.hdr.flags & HH_HF_PREALLOC));
    scan(drive_path(root, "CAPTURES", 'C', 1), &c);
    /* 30 s before the trigger at about 60 reads a second */
    CHECK(c.ok && c.hdr.reason == HH_STALL_LINK && c.reads > 1500 && c.reads < 2600);
}

TEST(test_engine_noisy_line)
{
    hh_snapshot_t s;
    hhsim_reset();
    SIM.p_drop = 0.03; SIM.p_corrupt = 0.03; SIM.p_truncate = 0.02; SIM.p_junk = 0.03;
    hhhost_t *h = hhhost_new(fresh_dir(), NULL, 0, NULL, hhsim_callbacks(), 1, 0);
    hhhost_run_until(h, 60.0);
    hhhost_snapshot(h, &s);
    /* a bad line costs speed, not the link, and never a false stall */
    CHECK(s.link.state == HH_LINK_OK && s.link.errors > 5 && s.link.hz_fast > 8.0f);
    CHECK(hh_core_link_stats(hhhost_core(h))->retries > 100);
    CHECK(s.stall.count == 0 && NEAR(s.value[0], 800, 0.01));
    CHECK(SIM.bad_frames_sent == 0);
    hhhost_free(h);
}

TEST(test_engine_power_cut)
{
    /* Power dies in the middle of a write, in a file whose preallocated space
     * holds an older drive's blocks. What is on the card must read back as a
     * clean prefix of this drive and nothing else. */
    const char *root = fresh_dir(), *old_root = fresh_dir();
    char old_path[320];
    hhsim_reset();
    hhhost_t *h = hhhost_new(old_root, NULL, 0, "prealloc_mb = 0\n", hhsim_callbacks(), 1, 0);
    hhhost_run_until(h, 60.0);
    hhhost_free(h);
    snprintf(old_path, sizeof(old_path), "%s", drive_path(old_root, "DRIVES", 'D', 1));
    scan_t old;
    scan(old_path, &old);
    CHECK(old.ok && old.reads > 3000);

    for (int cut = 0; cut < 6; cut++) {
        const char *r = cut ? fresh_dir() : root;
        hhsim_reset();
        h = hhhost_new(r, NULL, 0, "prealloc_mb = 1\n", hhsim_callbacks(), 2, 0);
        hhhost_set_stale(h, old_path);
        hhhost_run_until(h, 3.0);
        /* the preallocation and the header are written; now the budget runs out mid-block */
        hhhost_set_write_budget(h, 5000 + cut * 137);
        hhhost_run_until(h, 20.0);
        hhhost_free(h);
        scan_t d;
        scan(drive_path(r, "DRIVES", 'D', 1), &d);
        CHECK(d.ok && !d.ended && d.hdr.boot_no == 2);
        /* every block that checks out is this drive's: none of the 3000 stale reads got in */
        CHECK(d.reads > 150 && d.reads < 900 && d.blocks == (int)d.last_seq + 1);
        struct stat st;
        CHECK(stat(drive_path(r, "DRIVES", 'D', 1), &st) == 0 && st.st_size == (1 << 20));
    }
}

TEST(test_engine_sync_and_blocks)
{
    hhsim_reset();
    hhhost_t *h = hhhost_new(fresh_dir(), NULL, 0, "sync_ms = 1000\nblock_ms = 250\n", hhsim_callbacks(), 1, 0);
    hhhost_run_until(h, 31.0);
    uint32_t syncs = hhhost_syncs(h);
    CHECK(syncs >= 28 && syncs <= 40);          /* about once a second, plus the events */
    hhhost_free(h);
}

TEST(test_engine_iso_fallback)
{
    hh_snapshot_t s;
    const char *root = fresh_dir();
    hhsim_reset();
    SIM.honda = 0; SIM.iso = 1;                 /* an ECU that only speaks plain OBD2 */
    SIM.closed_loop = 1;
    SIM.dtc[0] = 0x0301; SIM.dtc[1] = 0x1456;
    hhsim_set_rpm(1500);
    hhhost_t *h = hhhost_new(root, NULL, 0, "iso_after_s = 4\n", hhsim_callbacks(), 1, 0);
    hhhost_run_until(h, 3.5);
    hhhost_snapshot(h, &s);
    CHECK(s.link.state == HH_LINK_OFF && SIM.iso_inits == 0);      /* Honda first */
    hhhost_run_until(h, 15.0);
    hhhost_snapshot(h, &s);
    CHECK(SIM.iso_inits == 1 && s.link.state == HH_LINK_ISO_ONLY);
    CHECK(NEAR(s.value[ch_index("rpm")], 1500, 0.01) && NEAR(s.value[ch_index("ect")], 90, 0.01));
    CHECK(NEAR(s.value[ch_index("tps")], 20.0, 0.01) && NEAR(s.value[ch_index("o2")], 0.45, 0.001));
    CHECK(isnan(s.value[ch_index("batt")]));                        /* no such PID: no value, not a guess */
    /* PID 01 bit 7 -> mil, PID 03 -> closed_loop (the map has both as Honda flags) */
    CHECK(s.value[ch_index("mil")] == 1.0f && s.value[ch_index("closed_loop")] == 1.0f);
    /* calc_load (04) and o2_post (15) are [[obd2]] channels: asked for and logged, decoded on the laptop */
    CHECK((SIM.pid_seen & (1u << (0x04 - 1))) && (SIM.pid_seen & (1u << (0x15 - 1))));
    CHECK(s.dtc.valid && s.dtc.n == 2 && s.dtc.code[0] == 0x0301 && s.dtc.code[1] == 0x1456);
    CHECK(s.log.recording && s.stall.armed);
    char text[6];
    hh_dtc_text(0x0301, text);
    CHECK(strcmp(text, "P0301") == 0);
    /* the stall trigger works on the OBD2 rpm too */
    hhsim_set_rpm(0);
    hhhost_run_until(h, 17.0);
    hhhost_snapshot(h, &s);
    CHECK(s.stall.count == 1);
    /* key off: the session dies, the handheld goes back to asking the Honda way */
    SIM.powered = 0;
    hhhost_run_until(h, 30.0);
    hhhost_snapshot(h, &s);
    CHECK(s.link.state == HH_LINK_OFF);
    CHECK(SIM.mode4_seen == 0 && SIM.bad_frames_sent == 0);
    hhhost_free(h);
    scan_t d;
    scan(drive_path(root, "DRIVES", 'D', 1), &d);
    CHECK(d.ok && d.hdr.link_mode == 1 && d.iso > 50 && d.reads == 0 && d.stall_events == 1);
}

/* The built-in map with another [protocol] baud rate, as the exporter would write it. */
static size_t map_with_baud(uint8_t *out, size_t cap, uint32_t baud)
{
    const uint8_t *def;
    size_t len = hhhost_default_map(&def);
    if (len > cap) return 0;
    memcpy(out, def, len);
    hh_map_hdr_t hdr;
    memcpy(&hdr, out, sizeof(hdr));
    for (size_t pos = sizeof(hdr); pos + sizeof(hh_map_sec_t) <= hdr.core_len;) {
        hh_map_sec_t sec;
        memcpy(&sec, out + pos, sizeof(sec));
        pos += sizeof(sec);
        if (sec.type == HH_MS_PROTO) memcpy(out + pos + offsetof(hh_map_proto_t, baud), &baud, sizeof(baud));
        pos += sec.len;
    }
    static const uint8_t zero[4] = {0, 0, 0, 0};
    size_t at = offsetof(hh_map_hdr_t, core_crc);
    uint32_t crc = hh_crc32(0, out, at);
    crc = hh_crc32(crc, zero, 4);
    crc = hh_crc32(crc, out + at + 4, hdr.core_len - at - 4);
    memcpy(out + at, &crc, 4);
    return len;
}

TEST(test_engine_p2p_10400_with_wake)
{
    /* What a stock 2000 Civic P2P does: Honda reads at 10400 baud, and only
     * after the wake-up bytes. The map says 10400; the handheld must open at
     * it, wake the ECU first, and go back to it after an ISO session. */
    static uint8_t map[256 * 1024];
    hh_snapshot_t s;
    size_t len = map_with_baud(map, sizeof(map), 10400);
    CHECK(len > 0);
    hhsim_reset();
    SIM.honda_baud = 10400; SIM.need_wake = 1; SIM.iso = 1;
    SIM.dtc[0] = 0x0420;
    hhsim_set_rpm(900);
    hhhost_t *h = hhhost_new(fresh_dir(), map, len, NULL, hhsim_callbacks(), 1, 0);
    hhhost_run_until(h, 5.0);
    hhhost_snapshot(h, &s);
    CHECK(s.link.state == HH_LINK_OK && NEAR(s.value[ch_index("rpm")], 900, 0.01) && s.link.hz_fast > 10.0f);
    CHECK(SIM.wakes >= 1 && SIM.honda_ignored == 0 && SIM.bad_frames_sent == 0);
    /* key off, then on: the ECU needs waking again; the probes send the wake-up bytes */
    SIM.powered = 0;
    hhhost_run_until(h, 10.0);
    SIM.powered = 1;
    uint32_t wakes = SIM.wakes;
    hhhost_run_until(h, 14.0);
    hhhost_snapshot(h, &s);
    CHECK(s.link.state == HH_LINK_OK && SIM.wakes > wakes && SIM.iso_inits == 0);
    /* trouble codes on request: 5-baud init, then back to 10400 and Honda reads */
    hh_core_request_dtc(hhhost_core(h));
    hhhost_run_until(h, 40.0);
    hhhost_snapshot(h, &s);
    CHECK(SIM.iso_inits == 1 && s.dtc.valid && s.dtc.n == 1 && s.dtc.code[0] == 0x0420);
    CHECK(s.link.state == HH_LINK_OK && s.link.hz_fast > 10.0f && s.log.drive_no == 1);
    CHECK(SIM.bad_frames_sent == 0 && SIM.mode4_seen == 0);
    hhhost_free(h);
}

TEST(test_engine_honda_seen_then_silent_no_iso)
{
    /* Honda reads worked, then the ECU went quiet (key off): never ISO, which
     * would hold the line for 5 s at key on. */
    hh_snapshot_t s;
    hhsim_reset();
    SIM.iso = 1;
    hhhost_t *h = hhhost_new(fresh_dir(), NULL, 0, "iso_after_s = 4\n", hhsim_callbacks(), 1, 0);
    hhhost_run_until(h, 5.0);
    hhhost_snapshot(h, &s);
    CHECK(s.link.state == HH_LINK_OK);
    SIM.powered = 0;
    hhhost_run_until(h, 60.0);
    hhhost_snapshot(h, &s);
    CHECK(s.link.state == HH_LINK_OFF && SIM.iso_inits == 0);
    /* key on: straight back to Honda reads */
    SIM.powered = 1;
    hhhost_run_until(h, 62.0);
    hhhost_snapshot(h, &s);
    CHECK(s.link.state == HH_LINK_OK && SIM.iso_inits == 0);
    hhhost_free(h);
}

TEST(test_engine_iso_default_wait)
{
    /* the default iso_after_s is 12: an OBD2-only ECU is tried after 12 s, not before */
    hh_snapshot_t s;
    hhsim_reset();
    SIM.honda = 0; SIM.iso = 1;
    hhhost_t *h = hhhost_new(fresh_dir(), NULL, 0, NULL, hhsim_callbacks(), 1, 0);
    hhhost_run_until(h, 11.5);
    CHECK(SIM.iso_inits == 0);
    hhhost_run_until(h, 20.0);
    hhhost_snapshot(h, &s);
    CHECK(SIM.iso_inits == 1 && s.link.state == HH_LINK_ISO_ONLY);
    hhhost_free(h);
}

TEST(test_engine_dtc_on_request)
{
    hh_snapshot_t s;
    hhsim_reset();
    SIM.iso = 1;
    SIM.dtc[0] = 0x0135;
    hhhost_t *h = hhhost_new(fresh_dir(), NULL, 0, NULL, hhsim_callbacks(), 1, 0);
    hhhost_run_until(h, 5.0);
    hh_core_request_dtc(hhhost_core(h));
    hhhost_run_until(h, 30.0);
    hhhost_snapshot(h, &s);
    CHECK(s.dtc.valid && s.dtc.n == 1 && s.dtc.code[0] == 0x0135);
    /* Honda polling is back, the pause was not logged as a stall or a new drive */
    CHECK(s.link.state == HH_LINK_OK && s.stall.count == 0 && s.log.drive_no == 1 && s.link.hz_fast > 15.0f);
    CHECK(SIM.mode4_seen == 0 && SIM.bad_frames_sent == 0);
    hhhost_free(h);
}

TEST(test_engine_alarm)
{
    hh_snapshot_t s;
    const char *root = fresh_dir();
    hhsim_reset();
    hhhost_t *h = hhhost_new(root, NULL, 0, NULL, hhsim_callbacks(), 1, 0);
    hhhost_run_until(h, 5.0);
    SIM.mem[0x17] = 115;                        /* 11.0 V with the engine running */
    hhhost_run_until(h, 12.0);
    hhhost_snapshot(h, &s);
    int batt = ch_index("batt");
    CHECK(s.top_alarm_ch == batt && s.top_alarm_level == 1 && s.alarm[batt] == 1);
    CHECK(strcmp(s.top_alarm_text, "Battery low") == 0 && !s.top_alarm_acked);
    hh_core_ack(hhhost_core(h));
    hhhost_snapshot(h, &s);
    CHECK(s.top_alarm_ch == batt && s.top_alarm_acked);
    SIM.mem[0x17] = 144;
    hhhost_run_until(h, 16.0);
    hhhost_snapshot(h, &s);
    CHECK(s.top_alarm_ch == -1 && s.top_alarm_level == 0 && s.alarm[batt] == 0 && s.top_alarm_text[0] == 0);
    hhhost_free(h);
    scan_t d;
    scan(drive_path(root, "DRIVES", 'D', 1), &d);
    CHECK(d.alarms == 1 && d.events >= 5);
}

TEST(test_engine_no_card)
{
    hh_snapshot_t s;
    hhsim_reset();
    hhhost_t *h = hhhost_new(fresh_dir(), NULL, 0, NULL, hhsim_callbacks(), 1, 0);
    hhhost_set_write_budget(h, 0);              /* the card will not open a file */
    hhhost_run_until(h, 20.0);
    hhhost_snapshot(h, &s);
    /* live data does not depend on the card */
    CHECK(s.link.state == HH_LINK_OK && s.link.hz_fast > 17.0f && !s.log.sd_ok && !s.log.recording);
    hhhost_free(h);
}

int main(void)
{
    const uint8_t *def;
    size_t len = hhhost_default_map(&def);
    const char *base = getenv("TMPDIR");
    snprintf(TMP, sizeof(TMP), "%s/hhtest.XXXXXX", base && *base ? base : "/tmp");
    if (!mkdtemp(TMP)) { perror("mkdtemp"); return 2; }
    if (hh_map_parse(&MAP, def, len) != 0) { printf("the built-in map does not load\n"); return 2; }
    if (sizeof(hh_map_hdr_t) != 64 || sizeof(hh_map_chan_t) != 80 || sizeof(hh_map_alarm_t) != 92
        || sizeof(hh_map_proto_t) != 112 || sizeof(hh_file_hdr_t) != 128 || sizeof(hh_block_hdr_t) != 24
        || sizeof(hh_gps_t) != 28 || sizeof(hh_rec_event_t) != 16 || sizeof(hh_rec_capture_t) != 32
        || sizeof(hh_rec_time_t) != 24 || sizeof(hh_rec_end_t) != 24 || sizeof(hh_map_info_t) != 64
        || sizeof(hh_map_stall_t) != 24) { printf("a packed layout has the wrong size\n"); return 2; }
    if (hh_crc32(0, "123456789", 9) != 0xCBF43926u) { printf("crc32 is wrong\n"); return 2; }

    RUN(test_framing);
    RUN(test_read_only_guard);
    RUN(test_link_clean);
    RUN(test_link_each_fault);
    RUN(test_link_no_echo_cable);
    RUN(test_link_random_faults);
    RUN(test_planner);
    RUN(test_scheduler);
    RUN(test_expr);
    RUN(test_map);
    RUN(test_config);
    RUN(test_alarm_hysteresis);
    RUN(test_alarm_gate_and_when);
    RUN(test_slope);
    RUN(test_stall_trigger);
    RUN(test_ring);
    RUN(test_nmea);
    RUN(test_engine_drive);
    RUN(test_engine_link_loss_and_unplug);
    RUN(test_engine_noisy_line);
    RUN(test_engine_power_cut);
    RUN(test_engine_sync_and_blocks);
    RUN(test_engine_iso_fallback);
    RUN(test_engine_p2p_10400_with_wake);
    RUN(test_engine_honda_seen_then_silent_no_iso);
    RUN(test_engine_iso_default_wait);
    RUN(test_engine_dtc_on_request);
    RUN(test_engine_alarm);
    RUN(test_engine_no_card);
    printf("%d checks, %d failed (files left in %s)\n", checks, failures, TMP);
    return failures ? 1 : 0;
}
