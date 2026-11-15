/* STUB COPY of the hh-core data API, for building and simulating the UI before
 * components/hhcore lands. The declarations below are the agreed interface,
 * verbatim. When components/hhcore/ exists this whole component switches
 * itself off (see CMakeLists.txt) and that component's hh_data.h is used.
 */
#ifndef HH_DATA_H
#define HH_DATA_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HH_MAX_CH 48
typedef struct { char name[16]; char label[20]; char units[8]; float lo, hi, warn_lo, warn_hi, alarm_lo, alarm_hi; uint8_t decimals, verified, rate_class; } hh_channel_info_t;
typedef struct {
  uint32_t seq; int64_t t_us;
  uint16_t n_ch; float value[HH_MAX_CH]; uint8_t fresh[HH_MAX_CH]; uint8_t alarm[HH_MAX_CH]; /* 0 ok 1 warn 2 alarm */
  struct { uint8_t state; /*0 off 1 connecting 2 ok 3 iso_only 4 error*/ uint8_t backend; float hz_fast; uint32_t reads, errors; char ecu_id[12]; } link;
  struct { uint8_t fix; uint8_t sats; double lat, lon; float speed_kmh, heading, hdop; } gps;
  struct { uint8_t sd_ok, recording; uint32_t drive_no, bytes; float free_mb; } log;
  struct { uint8_t armed, triggered; uint32_t count; char last_reason[32]; } stall;
  int16_t top_alarm_ch; uint8_t top_alarm_level;
} hh_snapshot_t;
const hh_channel_info_t *hh_channels(uint16_t *n);
void hh_snapshot(hh_snapshot_t *out);
void hh_mark(void);
void hh_ack_alarm(void);
void hh_core_start(void);

#ifdef __cplusplus
}
#endif

#endif
