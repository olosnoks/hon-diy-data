/* LVGL configuration for the PC simulator only. The firmware configures LVGL
 * through Kconfig (CONFIG_LV_* in ../sdkconfig.defaults); keep the two in step.
 * Everything not set here takes LVGL's default on both sides.
 */
#ifndef LV_CONF_H
#define LV_CONF_H

#define LV_COLOR_DEPTH 16                 /* RGB565, as the panel */

#define LV_USE_STDLIB_MALLOC   LV_STDLIB_CLIB
#define LV_USE_STDLIB_STRING   LV_STDLIB_CLIB
#define LV_USE_STDLIB_SPRINTF  LV_STDLIB_CLIB

#define LV_DEF_REFR_PERIOD 30
#define LV_DPI_DEF 128                    /* 480 px across 95 mm */
#define LV_USE_OS LV_OS_NONE

/* every colour and border comes from ui_theme.c */
#define LV_USE_THEME_DEFAULT 0
#define LV_USE_THEME_SIMPLE 0
#define LV_USE_THEME_MONO 0

#define LV_USE_LOG 1
#define LV_LOG_LEVEL LV_LOG_LEVEL_WARN
#define LV_LOG_PRINTF 1

#endif
