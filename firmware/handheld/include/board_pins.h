/*
 * hondalog handheld: board pins and panel timing.
 * Board: Sunton ESP32-4827S043N (ESP32-S3-WROOM-1 N16R8, 4.3" 480x272 RGB
 * panel, no touch). See ../HARDWARE.md for wiring, sources and what is
 * still unverified. Nothing here has been run on the board.
 *
 * Sources, cited per pin as [A]..[F]:
 *  [A] rzeldent/platformio-espressif32-sunton, esp32-4827S043N.json
 *  [B] esp3d.io, hardware/esp32-s3/sunton-43-4827
 *  [C] espboards.dev, cyd-esp32-4827s043 (schematic-derived net names)
 *  [D] moononournation/Arduino_GFX discussion 587 (ESP32S3-4827S043)
 *  [E] forum.squareline.io thread 933 (LovyanGFX config, 480x272 Sunton)
 *  [F] ESP32-S3 datasheet (USB pads, SPI2 IOMUX, strapping pins)
 *
 * Names are stable: other code includes this file. Change values, not names.
 */
#pragma once

/* ---- LCD: 16-bit parallel RGB565, DE mode, no SPI init ------------------ */
#define HH_LCD_H_RES            480
#define HH_LCD_V_RES            272
#define HH_LCD_BITS_PER_PIXEL   16
#define HH_LCD_DATA_WIDTH       16

#define HH_LCD_PIN_HSYNC        39   /* [A][B][C][D][E] */
#define HH_LCD_PIN_VSYNC        41   /* [A][B][C][D][E] */
#define HH_LCD_PIN_DE           40   /* [A][B][C][D][E] */
#define HH_LCD_PIN_PCLK         42   /* [A][B][C][D][E] */
#define HH_LCD_PIN_DISP         (-1) /* [A] no display-enable pin */

/*
 * Colour pins. [B][C][D][E] agree on the mapping below. [A] names the same
 * GPIOs with red and blue exchanged (R0..R4 = 8,3,46,9,1 and B0..B4 =
 * 45,48,47,21,14). If red and blue come out swapped on the glass, exchange
 * the R and B groups here. Green is the same in every source.
 */
#define HH_LCD_PIN_R0           45   /* [B][C][D][E]; strapping pin (VDD_SPI) [F] */
#define HH_LCD_PIN_R1           48   /* [B][C][D][E] */
#define HH_LCD_PIN_R2           47   /* [B][C][D][E] */
#define HH_LCD_PIN_R3           21   /* [B][C][D][E] */
#define HH_LCD_PIN_R4           14   /* [B][C][D][E] */
#define HH_LCD_PIN_G0           5    /* [A][B][C][D][E] */
#define HH_LCD_PIN_G1           6    /* [A][B][C][D][E] */
#define HH_LCD_PIN_G2           7    /* [A][B][C][D][E] */
#define HH_LCD_PIN_G3           15   /* [A][B][C][D][E] */
#define HH_LCD_PIN_G4           16   /* [A][B][C][D][E] */
#define HH_LCD_PIN_G5           4    /* [A][B][C][D][E] */
#define HH_LCD_PIN_B0           8    /* [B][C][D][E] */
#define HH_LCD_PIN_B1           3    /* [B][C][D][E]; strapping pin (JTAG select) [F] */
#define HH_LCD_PIN_B2           46   /* [B][C][D][E]; strapping pin (boot mode) [F] */
#define HH_LCD_PIN_B3           9    /* [B][C][D][E] */
#define HH_LCD_PIN_B4           1    /* [B][C][D][E] */

/* For esp_lcd_rgb_panel_config_t.data_gpio_nums: D0..D15 = B0-4, G0-5, R0-4. */
#define HH_LCD_DATA_PINS { \
    HH_LCD_PIN_B0, HH_LCD_PIN_B1, HH_LCD_PIN_B2, HH_LCD_PIN_B3, HH_LCD_PIN_B4, \
    HH_LCD_PIN_G0, HH_LCD_PIN_G1, HH_LCD_PIN_G2, HH_LCD_PIN_G3, HH_LCD_PIN_G4, \
    HH_LCD_PIN_G5, \
    HH_LCD_PIN_R0, HH_LCD_PIN_R1, HH_LCD_PIN_R2, HH_LCD_PIN_R3, HH_LCD_PIN_R4 }

/*
 * Panel timing, as esp_lcd_rgb_timing_t wants it. Values are [A] (written for
 * esp_lcd). 535 x 296 total at 8 MHz = 50.5 frames/s.
 * Other sources: [D] 8 MHz, h front/pulse/back 1/1/43, v 3/1/12.
 *                [E] 12 MHz (16 MHz noted), same porches as [A], pclk_idle_high 1.
 * All agree on back porches 43 and 12 and on pclk_active_neg = 1.
 */
#define HH_LCD_PCLK_HZ          (8 * 1000 * 1000)  /* [A][D]; [E] uses 12 MHz */
#define HH_LCD_HSYNC_PULSE      4    /* [A][E]; [D] 1 */
#define HH_LCD_HSYNC_BACK       43   /* [A][D][E] */
#define HH_LCD_HSYNC_FRONT      8    /* [A][E]; [D] 1 */
#define HH_LCD_VSYNC_PULSE      4    /* [A][E]; [D] 1 */
#define HH_LCD_VSYNC_BACK       12   /* [A][D][E] */
#define HH_LCD_VSYNC_FRONT      8    /* [A][E]; [D] 3 */
#define HH_LCD_HSYNC_IDLE_LOW   1    /* [A] */
#define HH_LCD_VSYNC_IDLE_LOW   1    /* [A] */
#define HH_LCD_DE_IDLE_HIGH     0    /* [A] */
#define HH_LCD_PCLK_ACTIVE_NEG  1    /* [A][D][E] */
#define HH_LCD_PCLK_IDLE_HIGH   0    /* [A]; [E] has 1 */
#define HH_LCD_FB_IN_PSRAM      1    /* [A] frame buffer in the 8 MB octal PSRAM */
#define HH_LCD_PSRAM_ALIGN      64   /* [A] */
#define HH_LCD_SRAM_ALIGN       4    /* [A] */

/* ---- Backlight ----------------------------------------------------------- */
#define HH_BL_PIN               2    /* [A][B][C][D][E] BL_CTR, enable of the boost LED driver, active high */
#define HH_BL_ACTIVE_HIGH       1    /* [C][D] */
#define HH_BL_PWM_HZ            1000 /* guess, not from a source; see HARDWARE.md "Backlight" */
#define HH_BL_DUTY_MIN_8BIT     10   /* [D] useful analogWrite range reported as 10..130 of 255 */
#define HH_BL_DUTY_MAX_8BIT     130  /* [D] above this one user saw no more brightness; check */

/* ---- microSD, SPI2 (these are the SPI2 IOMUX pins [F]) ------------------- */
#define HH_SD_SPI_HOST          1    /* SPI2_HOST */
#define HH_SD_CS                10   /* [A][B][C] */
#define HH_SD_MOSI              11   /* [A][B][C]; also on P2 */
#define HH_SD_SCK               12   /* [A][B][C]; also on P2 */
#define HH_SD_MISO              13   /* [A][B][C]; also on P2 */
#define HH_PIN_SD_CS            HH_SD_CS
#define HH_PIN_SD_MOSI          HH_SD_MOSI
#define HH_PIN_SD_SCK           HH_SD_SCK
#define HH_PIN_SD_MISO          HH_SD_MISO

/* ---- Console / flashing: USB-C -> CH340C -> UART0 ------------------------ */
#define HH_PIN_UART0_TX         43   /* [B][C] U0TXD, 100 ohm to the CH340C; P1 */
#define HH_PIN_UART0_RX         44   /* [B][C] U0RXD, 100 ohm to the CH340C; P1 */

/* ---- Buttons ------------------------------------------------------------- */
#define HH_BTN_BOOT             0    /* [B][C] BOOT button to GND. Strapping pin: read only after boot [F] */
#define HH_BTN_BOOT_ACTIVE_LOW  1
#define HH_BTN_2                17   /* [C] the one header pin nothing on the board uses (P3, P4). Optional second button to GND, internal pull-up */

/* ---- GPS: NEO-6M on UART1, plugs into P4 (GND, 3V3, IO17, IO18) ---------- */
#define HH_GPS_UART_NUM         1
#define HH_GPS_BAUD             9600
#define HH_GPS_RX               18   /* [C] P4/P3. GPS TX -> here. Free on the N board: touch IRQ only on R; on C it needs R17, not fitted */
#define HH_GPS_TX               (-1) /* not wired. If the GPS must be configured, use 17 and give up HH_BTN_2 */
#define HH_GPS_PPS              17   /* GPS PPS (one pulse per second) on P3, next to the data pin. The firmware
                                      * watches both: if the data shows up on this pin instead (P3 wired the other
                                      * way round), it swaps the two at run time. Takes the place of HH_BTN_2. */
#undef  HH_BTN_2

/* Variant: GPS on header P1 (UART0, next to the USB-C) instead of P4.
 * Build with -DHH_GPS_P1=1 and sdkconfig.gps_p1 (console off). GPIO44 is also
 * the CH340C's TX through 100 ohm, so the GPS and the CH340 share the line:
 * works only if the GPS module drives it hard enough. Watch GPS OK/BAD on the
 * Link screen. Flashing over USB-C still works (esptool resets into the ROM). */
#if defined(HH_GPS_P1) && HH_GPS_P1
#undef  HH_GPS_UART_NUM
#undef  HH_GPS_RX
#undef  HH_GPS_TX
#define HH_GPS_UART_NUM         1    /* UART1 routed to GPIO44 through the GPIO matrix, so a pulse counter can watch the pin too */
#define HH_GPS_RX               44   /* P1 pin 3, RX0 */
#undef  HH_GPS_PPS
#define HH_GPS_PPS              (-1) /* no PPS on P1 */
#define HH_BTN_2                17   /* P3 is free in this build: the optional second button again */
#define HH_GPS_TX               (-1) /* P1 pin 2 (TX0) left idle: nothing is sent to the GPS */
#endif

/* ---- Laptop link (SERIAL.md): binary frames through the CH340C ------------ */
/* Default build: UART0, both directions, shared with (and replacing) the
 * console. GPS on P1: GPIO44 belongs to the GPS (UART1 at 9600 baud), and a
 * UART has one baud rate for both directions, so the link keeps UART0 with
 * only its TX on GPIO43: the handheld talks, it cannot listen. */
#define HH_SERIAL_UART_NUM      0
#define HH_SERIAL_TX            HH_PIN_UART0_TX
#define HH_SERIAL_RX            HH_PIN_UART0_RX
#define HH_SERIAL_TX_ONLY       0
#define HH_SERIAL_BAUD          921600  /* HANDHELD.CFG serial_baud; the CH340C's nearest rate is 923077 (+0.16 %) */
#if defined(HH_GPS_P1) && HH_GPS_P1
#undef  HH_SERIAL_UART_NUM
#undef  HH_SERIAL_RX
#undef  HH_SERIAL_TX_ONLY
#define HH_SERIAL_UART_NUM      0    /* TX only on its own pin (GPIO43); the GPS has UART1 at 9600 */
#define HH_SERIAL_RX            (-1)
#define HH_SERIAL_TX_ONLY       1
#endif

/* ---- K-line plan A: KKL cable on the S3's own USB (host, full speed) ----- */
#define HH_USBH_DM              19   /* [F] USB D-, fixed pad. [C] P3 (and P2 pin 1). Touch SDA on the C board only */
#define HH_USBH_DP              20   /* [F] USB D+, fixed pad. [C] P3. Touch SCL on the C board only */
#define HH_USBH_FTDI_VID        0x0403
#define HH_USBH_FTDI_PID        0x6001  /* FT232R, the owner's KKL cable */

/* ---- K-line plan B: L9637D on UART2, same two header pins as plan A ------ */
/* Plan A and plan B cannot both be fitted: they share GPIO19/20.            */
#define HH_KLINE_UART_NUM       2
#define HH_KLINE_BAUD           9600
#define HH_KLINE_TX             19   /* -> L9637D TX (pin 4) */
#define HH_KLINE_RX             20   /* <- L9637D RX (pin 1) */

/* ---- Not available ------------------------------------------------------- */
/* 35, 36, 37: octal PSRAM inside the module [F]. 26..32: flash.             */
/* 38: touch chip select / reset on R and C boards [C]; not on a header.     */
/* No speaker or amplifier on this board ([A] lists none for the 4827S043).  */
#define HH_PIN_SPEAKER          (-1)
