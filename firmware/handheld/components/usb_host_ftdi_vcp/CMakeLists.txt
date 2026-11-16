# Vendored copy: REQUIRES added (upstream gets it from idf_component.yml, which is not vendored).
idf_component_register(SRCS "usb_host_ftdi_vcp.c"
                    INCLUDE_DIRS "include"
                    REQUIRES usb_host_cdc_acm)
