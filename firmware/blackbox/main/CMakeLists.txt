idf_component_register(
    SRCS "main.c" "hal/hal_esp32s3.c"
    INCLUDE_DIRS "hal"
    REQUIRES bbcore driver esp_adc esp_timer fatfs nvs_flash sdmmc vfs
)
