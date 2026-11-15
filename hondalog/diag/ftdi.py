"""What is the USB serial adapter, and is its latency timer set right?

Everything is read from sysfs, so it needs no root and no extra library.

The FT232R holds received bytes for ``latency_timer`` milliseconds (16 by
default) before passing them to the PC unless its buffer fills first. At 9600
baud a reply never fills the buffer, so every request would wait up to 16 ms
for nothing: a 25 ms exchange becomes 40 ms. 1 ms is the minimum.

Genuine or clone: Linux's ftdi_sio drives both the same way, so this is for
the report only. A sure answer needs a USB-level test this module does not
do; it reports what the descriptors suggest and says so.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

FTDI_VID = 0x0403
UDEV_RULE_PATH = "/etc/udev/rules.d/99-hondalog.rules"
UDEV_LATENCY_RULE = ('ACTION=="add", SUBSYSTEM=="usb-serial", DRIVER=="ftdi_sio", '
                     'ATTR{latency_timer}="1"')

_FTDI_CHIPS = {0x0200: "FT232AM", 0x0400: "FT232BM", 0x0500: "FT2232C/D", 0x0600: "FT232R",
               0x0700: "FT2232H", 0x0800: "FT4232H", 0x0900: "FT232H", 0x1000: "FT-X series"}
_OTHER = {(0x1A86, 0x7523): "CH340", (0x1A86, 0x5523): "CH341", (0x10C4, 0xEA60): "CP210x",
          (0x067B, 0x2303): "PL2303"}
# Serial numbers that thousands of counterfeit FT232RL share.
_CLONE_SERIALS = {"A50285BI", "00000000", "A9007ZSO", "A600CVYX"}


@dataclass
class AdapterInfo:
    port: str
    real_port: str = ""
    tty: str = ""
    kind: str = "unknown"            # "usb-serial", "pty", "simulator", "unknown"
    driver: str = ""
    vid: int | None = None
    pid: int | None = None
    bcd_device: int | None = None
    manufacturer: str = ""
    product: str = ""
    serial: str = ""
    chip: str = ""
    is_ftdi: bool = False
    verdict: str = "unknown"         # "genuine-looking", "clone-suspected", "not-ftdi", "unknown"
    latency_ms: int | None = None
    latency_writable: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def usb_id(self) -> str:
        return f"{self.vid:04x}:{self.pid:04x}" if self.vid is not None and self.pid is not None else "?"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _port_dir(port: str, sysfs: str = "/sys") -> tuple[str, str]:
    """(tty name, sysfs directory of the usb-serial port) or ('', '')."""
    try:
        tty = os.path.basename(os.path.realpath(port))
    except OSError:
        return "", ""
    link = os.path.join(sysfs, "class", "tty", tty, "device")
    if not tty or not os.path.exists(link):
        return tty, ""
    return tty, os.path.realpath(link)


def read_latency_ms(port: str, sysfs: str = "/sys") -> int | None:
    """The FTDI latency timer in ms, or None if this port has none."""
    _, directory = _port_dir(port, sysfs)
    text = _read(os.path.join(directory, "latency_timer")) if directory else ""
    return int(text) if text.isdigit() else None


def write_latency_ms(port: str, ms: int = 1, sysfs: str = "/sys") -> bool:
    """Set the latency timer through sysfs. True if it now reads back ``ms``.

    The file is root-writable unless a udev rule changed that, so False is
    the normal answer for a plain user; pyserial's low-latency flag (which
    SerialTransport sets on open) reaches the same driver setting without root.
    """
    _, directory = _port_dir(port, sysfs)
    if not directory:
        return False
    try:
        with open(os.path.join(directory, "latency_timer"), "w", encoding="ascii") as fh:
            fh.write(str(ms))
    except OSError:
        return False
    return read_latency_ms(port, sysfs) == ms


def detect(port: str, sysfs: str = "/sys") -> AdapterInfo:
    """Describe the adapter behind ``port``."""
    info = AdapterInfo(port=port)
    if port.startswith("sim:"):
        info.kind, info.verdict = "simulator", "not-ftdi"
        info.notes.append("built-in simulator, no hardware involved")
        return info
    info.real_port = os.path.realpath(port)
    if info.real_port.startswith("/dev/pts/"):
        info.kind, info.verdict = "pty", "not-ftdi"
        info.notes.append("pseudo-terminal (hondalog-sim), no hardware involved")
        return info
    if not os.path.exists(port):
        info.notes.append(f"{port} does not exist: cable not plugged in, or no driver bound")
        return info
    info.tty, directory = _port_dir(port, sysfs)
    if not directory:
        info.notes.append("no sysfs entry for this port; cannot identify the adapter")
        return info
    info.kind = "usb-serial"
    info.driver = os.path.basename(os.path.realpath(os.path.join(directory, "driver")))
    # walk up from the port to the USB device (the one with idVendor)
    usb = directory
    for _ in range(4):
        if os.path.exists(os.path.join(usb, "idVendor")):
            break
        usb = os.path.dirname(usb)
    vid, pid, bcd = (_read(os.path.join(usb, n)) for n in ("idVendor", "idProduct", "bcdDevice"))
    try:
        info.vid, info.pid = int(vid, 16), int(pid, 16)
        info.bcd_device = int(bcd, 16) if bcd else None
    except ValueError:
        info.notes.append("could not read the USB ids")
    info.manufacturer = _read(os.path.join(usb, "manufacturer"))
    info.product = _read(os.path.join(usb, "product"))
    info.serial = _read(os.path.join(usb, "serial"))
    info.latency_ms = read_latency_ms(port, sysfs)
    info.latency_writable = os.access(os.path.join(directory, "latency_timer"), os.W_OK)
    _judge(info)
    return info


def _judge(info: AdapterInfo) -> None:
    notes = info.notes
    if info.vid != FTDI_VID:
        info.verdict = "not-ftdi"
        if info.vid is not None and info.pid is not None:
            info.chip = _OTHER.get((info.vid, info.pid), "")
        notes.append(f"not an FTDI chip ({info.chip or 'unknown chip'}, driver "
                     f"{info.driver or '?'}). It can still work as a K-line adapter; "
                     "there is no latency timer to tune.")
        return
    info.is_ftdi = True
    info.chip = _FTDI_CHIPS.get(info.bcd_device or 0, "FTDI (unknown type)")
    suspicious: list[str] = []
    if info.pid == 0x0000:
        suspicious.append("product id 0000: a counterfeit that an old Windows FTDI driver "
                          "zeroed. Works on Linux after: echo 0403 0000 | sudo tee "
                          "/sys/bus/usb-serial/drivers/ftdi_sio/new_id")
    if info.serial in _CLONE_SERIALS:
        suspicious.append(f"serial number {info.serial} is shared by many counterfeit FT232RL")
    if info.chip == "FT232R" and not info.serial:
        suspicious.append("no serial number; genuine FT232R chips carry one")
    if info.manufacturer and info.manufacturer.upper() != "FTDI":
        suspicious.append(f"manufacturer string is {info.manufacturer!r}, not 'FTDI'")
    if info.driver and info.driver != "ftdi_sio":
        notes.append(f"bound to driver {info.driver!r}, expected ftdi_sio")
    if suspicious:
        info.verdict = "clone-suspected"
        notes.extend(suspicious)
        notes.append("Clones are fine on Linux: ftdi_sio treats them like the real chip. "
                     "Some clones are sloppy at non-standard baud rates such as 10400 "
                     "(ISO 9141-2); if the ISO init fails with a clone, suspect that.")
    else:
        info.verdict = "genuine-looking"
        notes.append("Descriptors look like a genuine FTDI chip. Good clones copy them, "
                     "so this is not proof; it does not matter on Linux.")
    if info.latency_ms is None:
        notes.append("no latency_timer in sysfs (driver not ftdi_sio?)")
    elif info.latency_ms > 1:
        notes.append(f"latency_timer is {info.latency_ms} ms: every request waits up to that "
                     "long for nothing. hondalog lowers it to 1 ms when it opens the port; "
                     "the udev rule makes it permanent.")
    else:
        notes.append("latency_timer is 1 ms (best).")


def udev_rule() -> str:
    """The udev lines that name the cable and pin its latency timer to 1 ms."""
    return ("# hondalog: KKL 409.1 K-line cable (FTDI FT232R)\n"
            'SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", '
            'SYMLINK+="hondalog", GROUP="dialout", MODE="0660"\n'
            "# FTDI receive latency 16 ms -> 1 ms, every time the cable is plugged in\n"
            f"{UDEV_LATENCY_RULE}\n")


def udev_install_hint() -> str:
    return (f"sudo tee {UDEV_RULE_PATH} >/dev/null <<'EOF'\n{udev_rule()}EOF\n"
            "sudo udevadm control --reload-rules && sudo udevadm trigger")
