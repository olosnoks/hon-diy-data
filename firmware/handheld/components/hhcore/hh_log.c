/* hhcore: the pieces of a drive or capture file (FORMAT.md). The engine
 * (hh_core.c) decides what goes in; this only frames it. */
#include <string.h>

#include "hhcore.h"

size_t hh_block_encode(uint32_t nonce, uint8_t type, uint16_t count, uint32_t seq, uint64_t t0_us,
                       const void *payload, uint32_t len, uint8_t *out)
{
    hh_block_hdr_t h;
    h.magic = HH_BLOCK_MAGIC;
    h.type = type; h.flags = 0; h.count = count;
    h.payload_len = len; h.seq = seq; h.t0_us = t0_us;
    memcpy(out, &h, sizeof(h));
    if (len) memcpy(out + sizeof(h), payload, len);
    /* The file's nonce goes into every block CRC: a block left on the card
     * by an older file that happens to lie in this file's preallocated space
     * does not check out. */
    uint32_t crc = hh_crc32(0, &nonce, 4);
    crc = hh_crc32(crc, out, sizeof(h) + len);
    memcpy(out + sizeof(h) + len, &crc, 4);
    return sizeof(h) + len + 4;
}

void hh_file_hdr_seal(hh_file_hdr_t *h)
{
    h->magic = HH_FILE_MAGIC;
    h->version = HH_FILE_VERSION;
    h->hdr_len = sizeof(*h);
    h->crc = hh_crc32(0, h, offsetof(hh_file_hdr_t, crc));
}
