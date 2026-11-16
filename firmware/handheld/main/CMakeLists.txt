file(GLOB ui_srcs "ui/*.c" "ui/fonts/*.c")

# The data side. hh-core's component when it is in the tree, else the stub
# (fake data; see components/hhcore_stub).
if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/../components/hhcore/CMakeLists.txt")
    set(core hhcore)
else()
    set(core hhcore_stub)
endif()

set(reqs ${core} hhmap esp_lcd esp_driver_ledc esp_driver_gpio esp_timer nvs_flash)
# offline build (see ../CMakeLists.txt); otherwise main/idf_component.yml brings LVGL
idf_build_get_property(lvgl_name HH_LVGL_COMPONENT)
if(lvgl_name)
    list(APPEND reqs ${lvgl_name})
endif()

idf_component_register(
    SRCS "main.c" "port/port_esp32.c" "port/port_map_esp32.c" ${ui_srcs}
    INCLUDE_DIRS "ui" "port" "../include"
    REQUIRES ${reqs}
)

# idf.py -DHH_UI_PERF=1 build   (PlatformIO: the esp32s3-perf environment)
if(HH_UI_PERF)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE HH_UI_PERF=1)
endif()
