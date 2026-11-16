# Vendored components

Copied from <https://github.com/espressif/esp-usb>, commit
`7d9431146f9078ebe4f0ac94e9a69e88cee64deb` (2026-11-13), directory
`host/class/cdc/`. Licence: Apache-2.0 (each folder has its `LICENSE`).
They are in the tree so that the firmware builds without the component
registry and always against the versions the K-line code was written for.

| folder | component | version |
|---|---|---|
| `usb_host_cdc_acm/` | `espressif/usb_host_cdc_acm` | 2.4.1 |
| `usb_host_ftdi_vcp/` | `espressif/usb_host_ftdi_vcp` | 2.1.1 |

Changes against upstream:

- `test_app/`, `host_test/` and `idf_component.yml` are not copied. Without
  the manifests the component manager has nothing to fetch for them.
- `usb_host_ftdi_vcp/CMakeLists.txt`: `REQUIRES usb_host_cdc_acm` added
  (upstream declares that dependency in the manifest).

Nothing else is touched. To update: copy the two folders again, re-apply
the two points above, rebuild, and run bring-up steps 9 to 13 of
`HARDWARE.md`.

`hhcore/` and `hhcore_stub/` are this project's own.
