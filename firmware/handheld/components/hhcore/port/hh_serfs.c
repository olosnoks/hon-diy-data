/* The card as hh_serial's commands see it, through stdio and dirent under a
 * root folder: "/sd" on the handheld (ESP-IDF's FAT VFS), a temporary folder
 * in the host tests. Runs on the task that calls hh_serial_rx().
 *
 * A drive or capture file that is open for writing is not read: its length
 * on the card is the preallocated one and its tail is not ours yet. The
 * listing flags it and READ_FILE answers "busy".
 *
 * A pushed HANDHELD.MAP or HANDHELD.CFG goes to HANDHELD.TMP first. Only
 * when its length and CRC are right, and a map loads, does it take the
 * place of the old file, which is kept as HANDHELD.MBK / HANDHELD.CBK. The
 * handheld reads both files once, at start: the new one is used from the
 * next power-up.
 */
#include <ctype.h>
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "hh_serial.h"

#define TMP_NAME "HANDHELD.TMP"

static void path_of(const hh_serfs_t *fs, uint8_t kind, uint32_t no, char *out, size_t cap)
{
    switch (kind) {
    case HH_SER_F_DRIVE: snprintf(out, cap, "%s/DRIVES/D%06lu.HHD", fs->root, (unsigned long)(no % 1000000u)); break;
    case HH_SER_F_CAPTURE: snprintf(out, cap, "%s/CAPTURES/C%06lu.HHD", fs->root, (unsigned long)(no % 1000000u)); break;
    case HH_SER_F_MAP: snprintf(out, cap, "%s/HANDHELD.MAP", fs->root); break;
    case HH_SER_F_CFG: snprintf(out, cap, "%s/HANDHELD.CFG", fs->root); break;
    default: out[0] = 0; break;
    }
}

static int busy(const hh_serfs_t *fs, uint8_t kind, uint32_t no)
{
    if (!fs->core || (kind != HH_SER_F_DRIVE && kind != HH_SER_F_CAPTURE)) return 0;
    return hh_core_file_busy(fs->core, kind == HH_SER_F_DRIVE ? HH_FK_DRIVE : HH_FK_CAPTURE, no);
}

static void drop_handle(hh_serfs_t *fs)
{
    if (fs->fh) fclose(fs->fh);
    fs->fh = NULL; fs->kind = 0;
}

/* Dnnnnnn.HHD -> nnnnnn, or -1 */
static long number_of(const char *name, char letter)
{
    if (toupper((unsigned char)name[0]) != letter) return -1;
    char *end;
    long n = strtol(name + 1, &end, 10);
    if (end == name + 1 || n <= 0) return -1;
    if (end[0] != '.') return -1;
    if (toupper((unsigned char)end[1]) != 'H' || toupper((unsigned char)end[2]) != 'H'
        || toupper((unsigned char)end[3]) != 'D' || end[4]) return -1;
    return n;
}

static int fs_list(void *ctx, uint8_t kind, uint32_t after_no, hh_ser_file_info_t *out, int max, int *more)
{
    hh_serfs_t *fs = ctx;
    char path[280];
    struct stat st;
    int n = 0;
    *more = 0;
    if (kind == HH_SER_F_MAP || kind == HH_SER_F_CFG) {
        path_of(fs, kind, 0, path, sizeof(path));
        if (after_no >= 1 || max < 1 || stat(path, &st) != 0) return 0;
        memset(&out[0], 0, sizeof(out[0]));
        out[0].kind = kind; out[0].file_no = 1; out[0].size = (uint32_t)st.st_size;
        return 1;
    }
    if (kind != HH_SER_F_DRIVE && kind != HH_SER_F_CAPTURE) return HH_SER_E_ARG;
    char letter = kind == HH_SER_F_DRIVE ? 'D' : 'C';
    snprintf(path, sizeof(path), "%s/%s", fs->root, kind == HH_SER_F_DRIVE ? "DRIVES" : "CAPTURES");
    DIR *d = opendir(path);
    if (!d) return 0;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        long no = number_of(e->d_name, letter);
        if (no < 0 || (uint32_t)no <= after_no) continue;
        /* keep the max lowest numbers, in order */
        int at = n;
        while (at > 0 && out[at - 1].file_no > (uint32_t)no) at--;
        if (n == max) {
            *more = 1;
            if (at == max) continue;
            n--;
        }
        memmove(&out[at + 1], &out[at], (size_t)(n - at) * sizeof(out[0]));
        memset(&out[at], 0, sizeof(out[0]));
        out[at].kind = kind; out[at].file_no = (uint32_t)no;
        n++;
    }
    closedir(d);
    for (int i = 0; i < n; i++) {
        path_of(fs, kind, out[i].file_no, path, sizeof(path));
        out[i].size = stat(path, &st) == 0 ? (uint32_t)st.st_size : 0;
        if (busy(fs, kind, out[i].file_no)) out[i].flags |= HH_SER_FI_OPEN;
    }
    return n;
}

static int fs_read(void *ctx, uint8_t kind, uint32_t file_no, uint32_t offset, uint8_t *buf, uint16_t len, uint32_t *size)
{
    hh_serfs_t *fs = ctx;
    char path[280];
    if (kind < HH_SER_F_DRIVE || kind > HH_SER_F_CFG) return HH_SER_E_ARG;
    if (busy(fs, kind, file_no)) {
        if (fs->kind == kind && fs->file_no == file_no) drop_handle(fs);
        return HH_SER_E_BUSY;
    }
    if (!fs->fh || fs->kind != kind || fs->file_no != file_no) {
        drop_handle(fs);
        path_of(fs, kind, file_no, path, sizeof(path));
        FILE *f = fopen(path, "rb");
        if (!f) return HH_SER_E_ARG;
        if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return HH_SER_E_IO; }
        long end = ftell(f);
        if (end < 0) { fclose(f); return HH_SER_E_IO; }
        fs->fh = f; fs->kind = kind; fs->file_no = file_no; fs->size = (uint32_t)end;
    }
    *size = fs->size;
    if (offset >= fs->size) return 0;
    if (fseek(fs->fh, (long)offset, SEEK_SET) != 0) { drop_handle(fs); return HH_SER_E_IO; }
    size_t n = fread(buf, 1, len, fs->fh);
    if (n == 0 && ferror((FILE *)fs->fh)) { drop_handle(fs); return HH_SER_E_IO; }
    return (int)n;
}

static int fs_put_begin(void *ctx, uint8_t kind, uint32_t size)
{
    hh_serfs_t *fs = ctx;
    char path[280];
    (void)kind; (void)size;
    if (fs->put) { fclose(fs->put); fs->put = NULL; }
    snprintf(path, sizeof(path), "%s/" TMP_NAME, fs->root);
    fs->put = fopen(path, "wb");
    return fs->put ? HH_SER_OK : HH_SER_E_IO;
}

static int fs_put_data(void *ctx, uint32_t offset, const uint8_t *data, uint16_t len)
{
    hh_serfs_t *fs = ctx;
    (void)offset;                       /* hh_serial only ever passes the next byte */
    if (!fs->put) return HH_SER_E_STATE;
    if (fwrite(data, 1, len, fs->put) != len) { fclose(fs->put); fs->put = NULL; return HH_SER_E_IO; }
    return HH_SER_OK;
}

static int fs_put_end(void *ctx, uint8_t kind, uint32_t size, uint32_t crc, uint32_t *value)
{
    hh_serfs_t *fs = ctx;
    char tmp[280], final[280], backup[280];
    int rc = HH_SER_OK;
    *value = 0;
    snprintf(tmp, sizeof(tmp), "%s/" TMP_NAME, fs->root);
    if (!fs->put) return HH_SER_E_STATE;
    int bad = fclose(fs->put) != 0;
    fs->put = NULL;
    if (bad) { remove(tmp); return HH_SER_E_IO; }

    /* read it back from the card: what counts is what is there */
    FILE *f = fopen(tmp, "rb");
    uint8_t chunk[256];
    uint32_t got = 0, sum = 0;
    size_t n;
    if (!f) { remove(tmp); return HH_SER_E_IO; }
    while ((n = fread(chunk, 1, sizeof(chunk), f)) > 0) { sum = hh_crc32(sum, chunk, n); got += (uint32_t)n; }
    if (got != size || sum != crc) rc = HH_SER_E_CRC;
    if (rc == HH_SER_OK && kind == HH_SER_F_MAP) {
        hh_map_hdr_t hdr;
        hh_map_t *m = malloc(sizeof(*m));
        uint8_t *core = NULL;
        int prc = -1;
        rewind(f);
        if (m && fread(&hdr, 1, sizeof(hdr), f) == sizeof(hdr) && hdr.magic == HH_MAP_MAGIC
            && hdr.core_len >= sizeof(hdr) && hdr.core_len <= HH_MAP_MAX_CORE && hdr.core_len <= size
            && (core = malloc(hdr.core_len)) != NULL) {
            memcpy(core, &hdr, sizeof(hdr));
            if (fread(core + sizeof(hdr), 1, hdr.core_len - sizeof(hdr), f) == hdr.core_len - sizeof(hdr))
                prc = hh_map_parse(m, core, hdr.core_len);
        }
        free(core); free(m);
        if (prc != 0) { rc = HH_SER_E_REFUSED; *value = (uint32_t)-prc; }  /* the handheld would not load it */
    } else if (rc == HH_SER_OK && kind == HH_SER_F_CFG) {
        char *text = malloc(size + 1);
        hh_config_t cfg;
        rewind(f);
        if (!text || fread(text, 1, size, f) != size) rc = HH_SER_E_IO;
        else {
            hh_config_default(&cfg);
            *value = (uint32_t)hh_config_parse(&cfg, text, size);          /* lines it will skip */
        }
        free(text);
    }
    fclose(f);
    if (rc != HH_SER_OK) { remove(tmp); return rc; }

    if (fs->fh && fs->kind == kind) drop_handle(fs);
    path_of(fs, kind, 0, final, sizeof(final));
    snprintf(backup, sizeof(backup), "%s/HANDHELD.%s", fs->root, kind == HH_SER_F_MAP ? "MBK" : "CBK");
    remove(backup);
    rename(final, backup);              /* fails when there was none: fine */
    if (rename(tmp, final) != 0) { rename(backup, final); remove(tmp); return HH_SER_E_IO; }
    return HH_SER_OK;
}

void hh_serfs_init(hh_serfs_t *fs, const char *root, hh_core_t *core, hh_serial_fs_t *ops)
{
    memset(fs, 0, sizeof(*fs));
    snprintf(fs->root, sizeof(fs->root), "%s", root);
    fs->core = core;
    ops->ctx = fs;
    ops->list = fs_list; ops->read = fs_read;
    ops->put_begin = fs_put_begin; ops->put_data = fs_put_data; ops->put_end = fs_put_end;
}
