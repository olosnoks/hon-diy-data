/* hondalog handheld: startup.
 *
 *   core 0   hh-core: link, GPS, SD (started by hh_core_start)
 *   core 1   the UI task: LVGL, the display driver and its interrupt, the button
 *
 * The UI only ever reads hh_snapshot() and calls hh_mark() / hh_ack_alarm().
 */
#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "hh_data.h"
#include "port.h"

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK) ESP_LOGW("main", "no NVS (%s): the theme choice will not be kept", esp_err_to_name(err));

    hh_core_start();
    ui_port_start();
}
