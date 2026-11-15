/* Hardware abstraction for the black box.
 *
 * main.c and bbcore never include a vendor header; everything the hardware
 * does goes through these functions. To port (RP2040 / Pico 2): write
 * hal_<chip>.c implementing this file, add it to main/CMakeLists.txt.
 * Notes for that port are in HARDWARE.md ("Porting").
 */
#ifndef BB_HAL_H
#define BB_HAL_H

#include <stddef.h>
#include <stdint.h>

#include "bbcore.h"

/* Pin roles. The numbers live in the HAL implementation (pins table there). */

typedef struct { uint64_t t_us; uint8_t ch; uint8_t gpio_level; } hal_edge_t;

/* Clock, memory, identity */
void        hal_init(void);                     /* clocks, GPIO, LED, NVS */
uint64_t    hal_now_us(void);                   /* monotonic, same clock as edge times */
void       *hal_big_alloc(size_t size);         /* external RAM if there is any */
size_t      hal_big_free(void);                 /* largest block hal_big_alloc could give */
const char *hal_device_id(void);                /* short, stable, printable */
uint32_t    hal_next_boot_no(void);             /* persistent counter, incremented per call */

/* Analog: continuous DMA conversion of the BB_N_ANALOG channels in turn.
 * hal_adc_read blocks until data is there (or timeout_ms), fills conv[] with
 * calibrated pin millivolts and returns the count; *t_us is the time the
 * last conversion in the batch finished, as well as the HAL knows it. */
int      hal_adc_start(uint32_t per_channel_hz);        /* 0 ok */
uint32_t hal_adc_conv_hz(void);                         /* total conversions per second */
int      hal_adc_read(bb_conv_t *conv, size_t max, uint64_t *t_us, uint32_t timeout_ms);
uint32_t hal_adc_overruns(void);

/* Pulse inputs: hardware-latched timestamps of both edges on BB_N_EDGE pins.
 * hal_edges_read drains what the ISR queued, oldest first. */
int      hal_edges_start(void);
size_t   hal_edges_read(hal_edge_t *out, size_t max);
uint32_t hal_edges_dropped(void);                       /* queue overflows, should stay 0 */
uint32_t hal_edges_resolution_hz(void);

int  hal_button(void);                                  /* 1 = pressed */
void hal_led(int on);

/* Storage: mount the card, give bbcore its file functions. */
int  hal_storage_mount(bb_io_t *io);                    /* 0 ok */
void hal_storage_unmount(void);
int  hal_storage_mkdir(const char *path);
uint32_t hal_storage_last_event_no(void);               /* highest EVnnnnnn.BBX present */
int  hal_storage_read_text(const char *path, char *out, size_t cap);   /* length, <0 = none */
int  hal_storage_write_text(const char *path, const char *text, size_t len);
int  hal_storage_selftest(void);                        /* write, read back, delete; 0 ok */

/* USB CDC link to the laptop */
int  hal_usb_start(void);
int  hal_usb_read(uint8_t *buf, size_t max, uint32_t timeout_ms);
void hal_usb_write(const uint8_t *data, size_t len);    /* never blocks; drops if no host */

/* Optional GPS on a UART (RX only) */
int  hal_gps_start(uint32_t baud);
int  hal_gps_read(uint8_t *buf, size_t max);

/* Tasks and one mutex */
void hal_task_start(void (*fn)(void *), const char *name, int priority, size_t stack, int core);
void hal_sleep_ms(uint32_t ms);
void hal_lock(void *ctx);
void hal_unlock(void *ctx);

/* Power down until IG1 comes back or the button is pressed. Does not return. */
void hal_deep_sleep(void);

void hal_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

#endif
