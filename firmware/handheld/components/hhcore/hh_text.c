/* hhcore: small text helpers shared by the engine and the display side. */
#include "hhcore.h"

void hh_dtc_text(uint16_t code, char out[6])
{
    static const char system[] = "PCBU", hex[] = "0123456789ABCDEF";
    out[0] = system[(code >> 14) & 3];
    out[1] = (char)('0' + ((code >> 12) & 3));
    out[2] = hex[(code >> 8) & 15];
    out[3] = hex[(code >> 4) & 15];
    out[4] = hex[code & 15];
    out[5] = 0;
}
