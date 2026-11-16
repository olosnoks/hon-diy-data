# STUB. Fake data behind the hh-core API so the UI builds and runs alone.
# Switches itself off as soon as the real component is in the tree.
if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/../hhcore/CMakeLists.txt")
    idf_component_register()
else()
    idf_component_register(SRCS "hh_stub.c" INCLUDE_DIRS "include" REQUIRES esp_timer)
endif()
