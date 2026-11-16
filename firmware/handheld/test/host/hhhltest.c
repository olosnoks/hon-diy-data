/* Host tests of the HL text session (components/hhcore/hh_hl.c): line parsing, base64, CRC32, PUT with resume and a
 * bad checksum, the path rules, the 30 s timeout, and that a session leaves no frames in the stream. The laptop's own
 * client is run against the same C by tests/test_hh_hl.py; this is the C on its own, `make` runs it. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "hhhost.h"
#include "hhsim.h"

static int failures, checks;
static const char *current = "";

#define CHECK(cond) do { checks++; if (!(cond)) { failures++; \
    printf("  FAIL %s:%d [%s] %s\n", __FILE__, __LINE__, current, #cond); } } while (0)
#define RUN(name) do { current = #name; int before = failures; name(); \
    printf("%-34s %s\n", #name, failures == before ? "ok" : "FAILED"); } while (0)
#define TEST(name) static void name(void)

static char TMP[200];
static int tmp_n;
static const char *fresh_dir(void)
{
    static char path[256];
    snprintf(path, sizeof(path), "%s/sd%d", TMP, tmp_n++);
    mkdir(path, 0777);
    return path;
}

/* what the UART has sent since the last look, as text (frames are binary: they show up as junk, which the tests
 * that care look for by their sizes) */
static char out[1 << 20];
static size_t out_n;

static void pull(hhhost_t *h)
{
    uint8_t buf[2048];
    size_t n;
    while ((n = hhhost_serial_poll(h, buf, sizeof(buf))) != 0) {
        for (size_t i = 0; i < n && out_n < sizeof(out) - 1; i++) if (buf[i]) out[out_n++] = (char)buf[i];   /* the 0x00 separators are not text */
    }
    out[out_n] = 0;
}

static void clear(void) { out_n = 0; out[0] = 0; }

static void send_line(hhhost_t *h, const char *line)
{
    char buf[8192];
    int n = snprintf(buf, sizeof(buf), "%s\n", line);
    hhhost_serial_rx(h, (const uint8_t *)buf, (size_t)n);
    pull(h);
}

/* the reply to a command: what came since */
static const char *ask(hhhost_t *h, const char *line)
{
    clear();
    send_line(h, line);
    return out;
}

static int has(const char *text, const char *what) { return strstr(text, what) != NULL; }

static void run(hhhost_t *h, double until)
{
    while (SIM.now < until) { hhhost_run(h, 1); hhhost_serial_tick(h); pull(h); }
}

static hhhost_t *fresh(const char *cfg)
{
    hhsim_reset();
    hhhost_t *h = hhhost_new(fresh_dir(), NULL, 0, cfg, hhsim_callbacks(), 1, 0);
    hhhost_serial(h, 0, 0);
    run(h, 1.0);
    return h;
}

/* ---- base64 and CRC32, as the laptop writes them ------------------------------------ */

static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static size_t enc64(const uint8_t *in, size_t len, char *o)
{
    size_t w = 0;
    for (size_t i = 0; i < len; i += 3) {
        uint32_t v = (uint32_t)in[i] << 16 | (i + 1 < len ? (uint32_t)in[i + 1] << 8 : 0) | (i + 2 < len ? in[i + 2] : 0);
        o[w++] = B64[(v >> 18) & 63]; o[w++] = B64[(v >> 12) & 63];
        o[w++] = i + 1 < len ? B64[(v >> 6) & 63] : '='; o[w++] = i + 2 < len ? B64[v & 63] : '=';
    }
    o[w] = 0;
    return w;
}

static void data_line(char *line, size_t cap, uint32_t at, const uint8_t *d, size_t n)
{
    int w = snprintf(line, cap, "HL DATA @%u ", (unsigned)at);
    enc64(d, n, line + w);
}

TEST(test_crc32_matches_the_standard)
{
    CHECK(hh_crc32(0, "123456789", 9) == 0xCBF43926u);
    uint32_t crc = hh_crc32(0, "1234", 4);
    CHECK(hh_crc32(crc, "56789", 5) == 0xCBF43926u);
}

/* ---- the session ----------------------------------------------------------------------- */

TEST(test_hello_and_line_parsing)
{
    hhhost_t *h = fresh(NULL);
    CHECK(has(ask(h, "HL STAT"), "HL ERR nosession"));
    CHECK(has(ask(h, "HL HELLO"), "HL HELLO fw="));
    CHECK(has(out, "model=cyd-4827s043n") && has(out, "chunk=2048") && has(out, "win=3") && has(out, "variant=default"));
    CHECK(has(ask(h, "HL stat"), "HL STAT {"));                       /* the command's case does not matter */
    CHECK(has(ask(h, "HL    MARK   "), "HL OK"));                      /* spaces around */
    CHECK(has(ask(h, "HL FROB"), "HL ERR badcmd"));
    CHECK(has(ask(h, "HL TIME x"), "HL ERR args"));
    CHECK(has(ask(h, "HL TIME -5"), "HL ERR args"));
    CHECK(has(ask(h, "HL TIME 1700000000000"), "HL OK"));
    clear();
    static const char mixed[] = "console words\nmore\n\n\nHL MARK\n";
    hhhost_serial_rx(h, (const uint8_t *)mixed, sizeof(mixed) - 1);      /* console lines and blank lines */
    pull(h);
    CHECK(has(out, "HL OK") && !has(out, "badcmd"));
    CHECK(has(ask(h, "HL RM"), "HL ERR path") && has(ask(h, "HL GET"), "HL ERR path"));
    CHECK(has(ask(h, "HL BYE"), "HL OK bye"));
    CHECK(has(ask(h, "HL MARK"), "HL ERR nosession"));
    hhhost_free(h);
}

TEST(test_a_session_pauses_the_stream)
{
    hhhost_t *h = fresh(NULL);
    ask(h, "HL HELLO");
    uint32_t before = hhhost_serial_stat(h, "tx_frames");
    clear();
    run(h, SIM.now + 5.0);
    CHECK(hhhost_serial_stat(h, "tx_frames") == before);
    CHECK(out_n == 0);                                                  /* nothing at all: no hello, no stats */
    ask(h, "HL BYE");
    run(h, SIM.now + 3.0);
    CHECK(hhhost_serial_stat(h, "tx_frames") > before);                 /* the stream is back */
    hhhost_free(h);
}

TEST(test_timeout)
{
    hhhost_t *h = fresh(NULL);
    ask(h, "HL HELLO");
    clear();
    run(h, SIM.now + 29.0);
    CHECK(!has(out, "HL TIMEOUT"));
    ask(h, "HL STAT");                                                  /* STAT does not keep the session alive */
    run(h, SIM.now + 2.0);
    CHECK(has(out, "HL TIMEOUT"));
    CHECK(has(ask(h, "HL STAT"), "HL ERR nosession"));
    ask(h, "HL HELLO");
    clear();
    for (int i = 0; i < 10; i++) { run(h, SIM.now + 10.0); ask(h, "HL MARK"); }   /* a command every 10 s: it stays */
    CHECK(!has(out, "HL TIMEOUT") && has(out, "HL OK"));
    hhhost_free(h);
}

/* ---- PUT -------------------------------------------------------------------------------- */

static void make_file(const char *path, const uint8_t *d, size_t n) { FILE *f = fopen(path, "wb"); fwrite(d, 1, n, f); fclose(f); }

TEST(test_put_with_resume_and_a_bad_checksum)
{
    char root[256];
    hhsim_reset();
    snprintf(root, sizeof(root), "%s", fresh_dir());
    char sub[300];
    snprintf(sub, sizeof(sub), "%s/MAP", root);
    mkdir(sub, 0777);
    hhhost_t *h = hhhost_new(root, NULL, 0, NULL, hhsim_callbacks(), 1, 0);
    hhhost_serial(h, 0, 0);
    run(h, 0.5);
    ask(h, "HL HELLO");

    static uint8_t data[7000];
    for (size_t i = 0; i < sizeof(data); i++) data[i] = (uint8_t)(i * 7 + (i >> 8));
    char cmd[200], line[8192];
    uint32_t crc = hh_crc32(0, data, sizeof(data));

    /* a bad checksum: everything arrives, nothing is kept */
    snprintf(cmd, sizeof(cmd), "HL PUT /MAP/T.DAT %zu %08x", sizeof(data), crc ^ 1u);
    CHECK(has(ask(h, cmd), "HL READY 0"));
    clear();
    for (size_t at = 0; at < sizeof(data); at += 2048) {
        size_t n = sizeof(data) - at > 2048 ? 2048 : sizeof(data) - at;
        data_line(line, sizeof(line), (uint32_t)at, data + at, n);
        send_line(h, line);
    }
    CHECK(has(out, "HL ACK 2048") && has(out, "HL ACK 4096") && has(out, "HL ACK 6144") && has(out, "HL ERR crc"));
    snprintf(sub, sizeof(sub), "%s/MAP/T.DAT", root);
    CHECK(access(sub, F_OK) != 0);

    /* a good one, cut off after two chunks, and carried on after a new HELLO */
    snprintf(cmd, sizeof(cmd), "HL PUT /MAP/T.DAT %zu %08x", sizeof(data), crc);
    CHECK(has(ask(h, cmd), "HL READY 0"));
    for (size_t at = 0; at < 4096; at += 2048) { data_line(line, sizeof(line), (uint32_t)at, data + at, 2048); send_line(h, line); }
    ask(h, "HL HELLO");
    snprintf(cmd, sizeof(cmd), "HL STATUS /MAP/T.DAT");
    snprintf(line, sizeof(line), "HL PART 4096 %zu %08x", sizeof(data), crc);
    CHECK(has(ask(h, cmd), line));
    snprintf(cmd, sizeof(cmd), "HL PUT /MAP/T.DAT %zu %08x 4096", sizeof(data), crc);
    CHECK(has(ask(h, cmd), "HL READY 4096"));
    clear();
    data_line(line, sizeof(line), 4096, data + 4096, 2048);
    send_line(h, line);
    data_line(line, sizeof(line), 6144, data + 6144, sizeof(data) - 6144);
    send_line(h, line);
    CHECK(has(out, "HL ACK 6144") && has(out, "HL ACK 7000") && has(out, "HL OK"));
    FILE *f = fopen(sub, "rb");
    static uint8_t back[8000];
    size_t got = f ? fread(back, 1, sizeof(back), f) : 0;
    if (f) fclose(f);
    CHECK(got == sizeof(data) && memcmp(back, data, got) == 0);

    /* the failures that skip the DATA lines still on the wire: one answer, not one per line */
    snprintf(cmd, sizeof(cmd), "HL PUT /MAP/U.DAT 5000 %08x", crc);
    ask(h, cmd);
    clear();
    send_line(h, "HL DATA @0 !!!!");
    send_line(h, "HL DATA @0 AAAA");
    send_line(h, "HL DATA @0 AAAA");
    CHECK(has(out, "HL ERR b64") && !has(out, "HL ACK") && !has(strstr(out, "b64") + 3, "HL ERR"));
    CHECK(has(ask(h, "HL STATUS /MAP/U.DAT"), "HL PART"));                /* a command ends the skipping */
    snprintf(cmd, sizeof(cmd), "HL PUT /MAP/U.DAT 5000 %08x", crc);
    ask(h, cmd);
    CHECK(has(ask(h, "HL DATA @10 AAAA"), "HL ERR offset"));
    ask(h, cmd);
    CHECK(has(ask(h, "HL DATA @0 AAA"), "HL ERR b64"));                   /* not a multiple of 4 */
    ask(h, cmd);
    CHECK(has(ask(h, "HL DATA @0 AA=A"), "HL ERR b64"));                  /* '=' in the middle */
    hhhost_free(h);
}

TEST(test_path_rules)
{
    hhhost_t *h = fresh(NULL);
    ask(h, "HL HELLO");
    static const char *const bad[] = {"../x", "/../x", "/MAP/../x", "/MAP//X.DAT", "/MAP\\X.DAT", "MAP/X.DAT", "/a b",
                                      "/MAP/LONGNAMES.EXTRA", "/.", "/..", "/MAP/...", "/%s"};
    char cmd[160];
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        snprintf(cmd, sizeof(cmd), "HL GET %s", bad[i]);
        CHECK(has(ask(h, cmd), "HL ERR path"));
        snprintf(cmd, sizeof(cmd), "HL LS %s", bad[i]);
        CHECK(has(ask(h, cmd), "HL ERR path"));
    }
    char longp[100];
    memset(longp, 'A', sizeof(longp));
    longp[0] = '/'; longp[90] = 0;
    snprintf(cmd, sizeof(cmd), "HL GET %s", longp);
    CHECK(has(ask(h, cmd), "HL ERR path"));
    /* writes and deletes */
    CHECK(has(ask(h, "HL PUT /OTHER.TXT 3 00000000"), "HL ERR denied"));
    CHECK(has(ask(h, "HL PUT /DRIVES/D000002.HHD 3 00000000"), "HL ERR denied"));
    CHECK(has(ask(h, "HL PUT /MAP/UPLOAD.TMP 3 00000000"), "HL ERR denied"));
    CHECK(has(ask(h, "HL PUT /MAP/LONGNAME9.D 3 00000000"), "HL ERR denied"));
    CHECK(has(ask(h, "HL PUT /MAP/a/b.dat 3 00000000"), "HL ERR denied"));
    CHECK(has(ask(h, "HL PUT /HANDHELD.CFG 9000 00000000"), "HL ERR size"));
    CHECK(has(ask(h, "HL PUT /MAP/X.DAT 0 00000000"), "HL ERR size"));
    CHECK(has(ask(h, "HL PUT /MAP/X.DAT abc 00000000"), "HL ERR args"));
    CHECK(has(ask(h, "HL PUT /MAP/X.DAT 5"), "HL ERR args"));
    CHECK(has(ask(h, "HL RM /OTHER.TXT"), "HL ERR denied"));
    CHECK(has(ask(h, "HL RM /CAPTURES/C000001.HHD"), "HL ERR denied"));
    CHECK(has(ask(h, "HL RM /MAP/NONE.BIN"), "HL ERR nofile"));
    /* the K-line is out of reach: no command touches the link's transport (the read-only guard counts what it refused) */
    CHECK(hhhost_serial_stat(h, "blocked") == 0);
    hhhost_free(h);
}

TEST(test_ls_get_and_the_config)
{
    char root[256];
    hhsim_reset();
    snprintf(root, sizeof(root), "%s", fresh_dir());
    char p[300];
    snprintf(p, sizeof(p), "%s/MAP", root);
    mkdir(p, 0777);
    static uint8_t data[5000];
    for (size_t i = 0; i < sizeof(data); i++) data[i] = (uint8_t)(i * 3);
    snprintf(p, sizeof(p), "%s/MAP/A.BIN", root);
    make_file(p, data, sizeof(data));
    snprintf(p, sizeof(p), "%s/HANDHELD.CFG", root);
    make_file(p, (const uint8_t *)"rotate = 180\n", 13);
    hhhost_t *h = hhhost_new(root, NULL, 0, NULL, hhsim_callbacks(), 1, 0);
    hhhost_serial(h, 0, 0);
    run(h, 0.5);
    ask(h, "HL HELLO");
    CHECK(has(ask(h, "HL LS /MAP"), "HL F 5000 ") && has(out, " /MAP/A.BIN") && has(out, "HL END"));
    CHECK(has(ask(h, "HL LS /"), "HL D /MAP") && has(out, "HL D /DRIVES") && has(out, " /HANDHELD.CFG"));
    CHECK(has(ask(h, "HL LS /NOWHERE"), "HL ERR nofile"));
    char want[64];
    snprintf(want, sizeof(want), "HL END %08x", (unsigned)hh_crc32(0, data, sizeof(data)));
    ask(h, "HL GET /MAP/A.BIN");
    run(h, SIM.now + 0.2);
    CHECK(has(out, "HL BEGIN 5000") && has(out, want));
    CHECK(has(ask(h, "HL CFG GET"), "HL BEGIN 13") && has(out, "HL END"));
    CHECK(has(ask(h, "HL GET /MAP/NONE"), "HL ERR nofile"));
    CHECK(has(ask(h, "HL STATUS /MAP/A.BIN"), "HL PART 0 0 00000000"));
    hhhost_free(h);
}

TEST(test_tx_only_never_answers)
{
    hhsim_reset();
    hhhost_t *h = hhhost_new(fresh_dir(), NULL, 0, NULL, hhsim_callbacks(), 1, 0);
    hhhost_serial(h, 1, 0);
    run(h, 0.5);
    clear();
    send_line(h, "HL HELLO");
    send_line(h, "HL MARK");
    run(h, SIM.now + 1.0);
    CHECK(!has(out, "HL "));
    CHECK(hhhost_serial_stat(h, "rx_frames") == 0);
    hhhost_free(h);
}

int main(void)
{
    const char *base = getenv("TMPDIR");
    snprintf(TMP, sizeof(TMP), "%s/hhhltest.XXXXXX", base && *base ? base : "/tmp");
    if (!mkdtemp(TMP)) { perror("mkdtemp"); return 2; }
    RUN(test_crc32_matches_the_standard);
    RUN(test_hello_and_line_parsing);
    RUN(test_a_session_pauses_the_stream);
    RUN(test_timeout);
    RUN(test_put_with_resume_and_a_bad_checksum);
    RUN(test_path_rules);
    RUN(test_ls_get_and_the_config);
    RUN(test_tx_only_never_answers);
    printf("%d checks, %d failed (files left in %s)\n", checks, failures, TMP);
    return failures ? 1 : 0;
}
