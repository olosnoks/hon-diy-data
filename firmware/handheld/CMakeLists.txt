cmake_minimum_required(VERSION 3.16)

# Offline build: LVGL from a local checkout instead of the component registry.
#   git clone --depth 1 -b v9.2.2 https://github.com/lvgl/lvgl
#   IDF_COMPONENT_MANAGER=0 idf.py -DHH_LVGL_DIR=/path/to/lvgl build
if(HH_LVGL_DIR)
    list(APPEND EXTRA_COMPONENT_DIRS "${HH_LVGL_DIR}")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# GPS on P1 (UART0) instead of P4:
#   idf.py -B build-p1 -DHH_GPS_P1=1 -DSDKCONFIG=build-p1/sdkconfig \
#          -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.gps_p1" build
if(HH_GPS_P1)
    idf_build_set_property(COMPILE_DEFINITIONS "HH_GPS_P1=1" APPEND)
endif()

if(HH_LVGL_DIR)
    # the component is named after its directory; main/CMakeLists.txt requires it by that name
    get_filename_component(hh_lvgl_name "${HH_LVGL_DIR}" NAME)
    idf_build_set_property(HH_LVGL_COMPONENT "${hh_lvgl_name}")
endif()

project(hondalog_handheld)
