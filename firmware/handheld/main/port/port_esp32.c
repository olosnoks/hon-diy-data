/* Board side of the UI: RGB panel, backlight, button, the LVGL task.
 *
 * Display path
 *   LVGL draws RGB565 straight into one of two full frame buffers in PSRAM
 *   (LV_DISPLAY_RENDER_MODE_DIRECT: only the changed areas are redrawn, and
 *   LVGL copies them to the other buffer before the next frame). The finished
 *   buffer is handed to esp_lcd, which shows it from the next frame on, so a
 *   half-drawn frame is never on the glass.
 *
 *   The LCD peripheral does not read PSRAM itself. esp_lcd copies the frame,
 *   HH_LCD_BOUNCE_LINES lines at a time, into two small DMA buffers in
 *   internal RAM ("bounce buffers") from the LCD interrupt. Reading PSRAM
 *   directly at pixel-clock rate is what makes these S3 RGB boards tear and
 *   drift when the CPU also uses the PSRAM bus.
 *
 * Everything here runs on core 1, the interrupt included (it is installed
 * from the UI task), which leaves core 0 to hh-core.
 */
#include <stdbool.h>
#include <stdint.h>

#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_rgb.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"

#include "lvgl.h"

#include "board_pins.h"
#include "port.h"
#include "ui.h"
#include "ui_perf.h"

/* Red and blue exchanged on the glass? Build with -DHH_LCD_SWAP_RB=1: one
 * public board file names the two pin groups the other way round. */
#ifndef HH_LCD_SWAP_RB
#define HH_LCD_SWAP_RB 0
#endif

/* Bounce buffer height in lines. esp_lcd wants the frame to be an even number
 * of bounce buffers: 272 lines = 16 x 17. Two buffers of 17 x 480 x 2 bytes
 * take 32.6 KB of internal RAM and give 16 refills a frame (808 a second).
 * 34 halves the interrupt rate for twice the RAM; 8, 4, 2 and 1 also divide. */
#ifndef HH_LCD_BOUNCE_LINES
#define HH_LCD_BOUNCE_LINES 17
#endif
_Static_assert(HH_LCD_V_RES % (2 * HH_LCD_BOUNCE_LINES) == 0,
               "the frame must be an even number of bounce buffers");
_Static_assert(HH_LCD_H_RES == 480 && HH_LCD_V_RES == 272, "the UI is laid out for 480 x 272");

/* Backlight, percent of the useful range in board_pins.h. */
#ifndef HH_BL_DAY_PCT
#define HH_BL_DAY_PCT 100
#endif
#ifndef HH_BL_NIGHT_PCT
#define HH_BL_NIGHT_PCT 25
#endif

#define UI_TASK_CORE   1
#define UI_TASK_PRIO   4
#define UI_TASK_STACK  (12 * 1024)
#define UI_TICK_MS     10

static const char *TAG = "ui";
static esp_lcd_panel_handle_t panel;
static SemaphoreHandle_t frame_done;

/* ---- backlight ------------------------------------------------------------ */

static void backlight_init(void)
{
    ledc_timer_config_t timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_8_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = HH_BL_PWM_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer));
    ledc_channel_config_t channel = {
        .gpio_num = HH_BL_PIN,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0,
        .hpoint = 0,
        .flags.output_invert = !HH_BL_ACTIVE_HIGH,
    };
    ESP_ERROR_CHECK(ledc_channel_config(&channel));
}

/* 0 = off; 1..100 maps onto the duty range the board notes call useful. */
static void backlight_set(int percent)
{
    uint32_t duty = 0;
    if (percent > 0) {
        if (percent > 100) percent = 100;
        duty = HH_BL_DUTY_MIN_8BIT + (uint32_t)(HH_BL_DUTY_MAX_8BIT - HH_BL_DUTY_MIN_8BIT) * (uint32_t)percent / 100u;
    }
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, duty);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
}

/* ---- theme choice in NVS --------------------------------------------------- */

static bool theme_load(void)
{
    nvs_handle_t h;
    uint8_t night = 0;
    if (nvs_open("hhui", NVS_READONLY, &h) == ESP_OK) {
        nvs_get_u8(h, "night", &night);
        nvs_close(h);
    }
    return night != 0;
}

void ui_port_theme_changed(bool night)
{
    backlight_set(night ? HH_BL_NIGHT_PCT : HH_BL_DAY_PCT);
    nvs_handle_t h;
    if (nvs_open("hhui", NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_u8(h, "night", night ? 1 : 0);
        nvs_commit(h);
        nvs_close(h);
    }
}

/* ---- clocks and CPU load --------------------------------------------------- */

int64_t ui_port_micros(void) { return esp_timer_get_time(); }

static uint32_t tick_ms(void) { return (uint32_t)(esp_timer_get_time() / 1000); }

void ui_port_cpu(int *core0, int *core1)
{
    *core0 = *core1 = -1;
#if CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS && CONFIG_FREERTOS_RUN_TIME_STATS_USING_ESP_TIMER && !CONFIG_FREERTOS_UNICORE
    /* idle-task run time is counted in esp_timer microseconds */
    static int64_t t_last;
    static uint32_t idle_last[2];
    int64_t now = esp_timer_get_time();
    uint32_t wall = (uint32_t)(now - t_last);
    int *out[2] = {core0, core1};
    for (int i = 0; i < 2; i++) {
        uint32_t idle = (uint32_t)ulTaskGetIdleRunTimeCounterForCore(i);
        if (t_last && wall) {
            uint32_t d = idle - idle_last[i];
            *out[i] = d >= wall ? 0 : (int)(100u - d * 100u / wall);
        }
        idle_last[i] = idle;
    }
    t_last = now;
#endif
}

/* ---- panel ------------------------------------------------------------------ */

/* Called from the LCD interrupt when the last bounce buffer of a frame has
 * been filled. From here on esp_lcd reads the buffer most recently passed to
 * esp_lcd_panel_draw_bitmap, and the other one is free to draw into. */
static bool IRAM_ATTR frame_finish_cb(esp_lcd_panel_handle_t p, const esp_lcd_rgb_panel_event_data_t *e, void *ctx)
{
    (void)p; (void)e; (void)ctx;
    BaseType_t woken = pdFALSE;
    xSemaphoreGiveFromISR(frame_done, &woken);
    return woken == pdTRUE;
}

static void panel_init(void **fb0, void **fb1)
{
    esp_lcd_rgb_panel_config_t cfg = {
        .clk_src = LCD_CLK_SRC_PLL160M,
        .timings = {
            .pclk_hz = HH_LCD_PCLK_HZ,
            .h_res = HH_LCD_H_RES,
            .v_res = HH_LCD_V_RES,
            .hsync_pulse_width = HH_LCD_HSYNC_PULSE,
            .hsync_back_porch = HH_LCD_HSYNC_BACK,
            .hsync_front_porch = HH_LCD_HSYNC_FRONT,
            .vsync_pulse_width = HH_LCD_VSYNC_PULSE,
            .vsync_back_porch = HH_LCD_VSYNC_BACK,
            .vsync_front_porch = HH_LCD_VSYNC_FRONT,
            .flags = {
                .hsync_idle_low = HH_LCD_HSYNC_IDLE_LOW,
                .vsync_idle_low = HH_LCD_VSYNC_IDLE_LOW,
                .de_idle_high = HH_LCD_DE_IDLE_HIGH,
                .pclk_active_neg = HH_LCD_PCLK_ACTIVE_NEG,
                .pclk_idle_high = HH_LCD_PCLK_IDLE_HIGH,
            },
        },
        .data_width = HH_LCD_DATA_WIDTH,
        .bits_per_pixel = HH_LCD_BITS_PER_PIXEL,
        .num_fbs = 2,
        .bounce_buffer_size_px = HH_LCD_BOUNCE_LINES * HH_LCD_H_RES,
        .psram_trans_align = HH_LCD_PSRAM_ALIGN,
        .hsync_gpio_num = HH_LCD_PIN_HSYNC,
        .vsync_gpio_num = HH_LCD_PIN_VSYNC,
        .de_gpio_num = HH_LCD_PIN_DE,
        .pclk_gpio_num = HH_LCD_PIN_PCLK,
        .disp_gpio_num = HH_LCD_PIN_DISP,
        /* D0..D15 of RGB565: five blue, six green, five red */
        .data_gpio_nums = {
#if HH_LCD_SWAP_RB
            HH_LCD_PIN_R0, HH_LCD_PIN_R1, HH_LCD_PIN_R2, HH_LCD_PIN_R3, HH_LCD_PIN_R4,
#else
            HH_LCD_PIN_B0, HH_LCD_PIN_B1, HH_LCD_PIN_B2, HH_LCD_PIN_B3, HH_LCD_PIN_B4,
#endif
            HH_LCD_PIN_G0, HH_LCD_PIN_G1, HH_LCD_PIN_G2, HH_LCD_PIN_G3, HH_LCD_PIN_G4, HH_LCD_PIN_G5,
#if HH_LCD_SWAP_RB
            HH_LCD_PIN_B0, HH_LCD_PIN_B1, HH_LCD_PIN_B2, HH_LCD_PIN_B3, HH_LCD_PIN_B4,
#else
            HH_LCD_PIN_R0, HH_LCD_PIN_R1, HH_LCD_PIN_R2, HH_LCD_PIN_R3, HH_LCD_PIN_R4,
#endif
        },
        .flags = {
            .fb_in_psram = HH_LCD_FB_IN_PSRAM,
        },
    };
    ESP_ERROR_CHECK(esp_lcd_new_rgb_panel(&cfg, &panel));
    esp_lcd_rgb_panel_event_callbacks_t cbs = {
        .on_bounce_frame_finish = frame_finish_cb,
    };
    ESP_ERROR_CHECK(esp_lcd_rgb_panel_register_event_callbacks(panel, &cbs, NULL));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    ESP_ERROR_CHECK(esp_lcd_rgb_panel_get_frame_buffer(panel, 2, fb0, fb1));
}

/* Direct mode: px is the whole frame buffer LVGL has just finished, area one
 * of the regions it redrew. Nothing is copied here. After the last region the
 * buffer becomes the displayed one; wait until esp_lcd has let go of the old
 * one before LVGL draws into it. */
static void flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px)
{
    ui_perf_flushed(area);
    if (lv_display_flush_is_last(disp)) {
        int64_t t0 = esp_timer_get_time();
        esp_lcd_panel_draw_bitmap(panel, 0, 0, HH_LCD_H_RES, HH_LCD_V_RES, px);
        /* Drop a stale "frame finished", then wait for the next one. Clearing
         * after the hand-over can cost one extra frame, never a torn one. */
        xSemaphoreTake(frame_done, 0);
        if (xSemaphoreTake(frame_done, pdMS_TO_TICKS(100)) != pdTRUE) ESP_LOGW(TAG, "no frame interrupt in 100 ms");
        ui_perf_waited((uint32_t)(esp_timer_get_time() - t0));
    }
    lv_display_flush_ready(disp);
}

/* ---- buttons ---------------------------------------------------------------- */

static void buttons_init(void)
{
    uint64_t mask = 1ULL << HH_BTN_BOOT;
#ifdef HH_BTN_2
    mask |= 1ULL << HH_BTN_2;
#endif
    gpio_config_t cfg = {
        .pin_bit_mask = mask,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&cfg);
}

static bool button_main(void) { return gpio_get_level(HH_BTN_BOOT) == (HH_BTN_BOOT_ACTIVE_LOW ? 0 : 1); }

/* Optional second button to GND: back one screen. Unfitted, the pull-up reads
 * as "not pressed" and the one-button UX is unchanged. */
static bool button_back(void)
{
#ifdef HH_BTN_2
    return gpio_get_level(HH_BTN_2) == 0;
#else
    return false;
#endif
}

/* ---- the task ----------------------------------------------------------------- */

static void ui_task(void *arg)
{
    (void)arg;
    void *fb0 = NULL, *fb1 = NULL;

    backlight_init();                       /* dark until the first frame is up */
    buttons_init();
    frame_done = xSemaphoreCreateBinary();
    panel_init(&fb0, &fb1);

    lv_init();
    lv_tick_set_cb(tick_ms);
    lv_display_t *disp = lv_display_create(HH_LCD_H_RES, HH_LCD_V_RES);
    lv_display_set_color_format(disp, LV_COLOR_FORMAT_RGB565);
    lv_display_set_buffers(disp, fb0, fb1, HH_LCD_H_RES * HH_LCD_V_RES * 2, LV_DISPLAY_RENDER_MODE_DIRECT);
    lv_display_set_flush_cb(disp, flush_cb);
    ui_perf_attach(disp);

    bool night = theme_load();
    ui_create(night);
    ui_tick(tick_ms(), false, false);
    lv_timer_handler();                     /* first frame */
    backlight_set(night ? HH_BL_NIGHT_PCT : HH_BL_DAY_PCT);
    ESP_LOGI(TAG, "UI up: %d x %d, bounce %d lines, %s", HH_LCD_H_RES, HH_LCD_V_RES, HH_LCD_BOUNCE_LINES,
             night ? "night" : "daylight");

    TickType_t wake = xTaskGetTickCount();
    for (;;) {
        ui_tick(tick_ms(), button_main(), button_back());
        lv_timer_handler();
        /* fixed 10 ms cadence; if a frame took longer, do not try to catch up */
        if (xTaskDelayUntil(&wake, pdMS_TO_TICKS(UI_TICK_MS)) == pdFALSE) wake = xTaskGetTickCount();
    }
}

void ui_port_start(void)
{
    xTaskCreatePinnedToCore(ui_task, "ui", UI_TASK_STACK, NULL, UI_TASK_PRIO, NULL, UI_TASK_CORE);
}
