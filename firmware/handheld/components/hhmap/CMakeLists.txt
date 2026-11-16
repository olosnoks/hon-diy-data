# hhmap: the offline road map (MAPFORMAT.md). Portable C99, also built for the
# host tests (../../test/host) and the PC simulator (../../sim).
idf_component_register(
    SRCS "hm_format.c" "hm_store.c" "hm_render.c"
    INCLUDE_DIRS "include"
)
