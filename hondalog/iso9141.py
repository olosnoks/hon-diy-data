"""Stub: standard OBD2 PIDs over ISO 9141-2.

Not implemented. The Honda RAM reads in protocol.py are the point of this
tool; generic PIDs would only be a cross-check. What it would take:

  1. 5-baud init: send address 0x33 at 5 bit/s on the K-line. An FT232 cannot
     run at 5 baud, so bit-bang it with break on/off (pyserial ``break_condition``),
     200 ms per bit: start bit low, 0x33 LSB first, stop bit high.
  2. Switch to 10400 8N1 (not 9600), read sync 0x55 and key bytes 08 08,
     send back the inverted second key byte, read the inverted address 0xCC.
  3. Requests: 68 6A F1 <mode> <pid> <sum>, replies 48 6B <ecu> <mode+40> ...
     with a plain 8-bit sum as the checksum. Send something at least every 5 s
     or the ECU drops the session.

The two protocols cannot share the line at the same time: disconnect the
Honda poller first.
"""

from __future__ import annotations


class Iso9141Link:
    BAUD = 10400
    INIT_ADDRESS = 0x33

    def __init__(self, transport):
        self.transport = transport

    def slow_init(self) -> None:
        raise NotImplementedError("ISO 9141-2 5-baud init is not implemented yet")

    def read_pid(self, mode: int, pid: int) -> bytes:
        raise NotImplementedError("ISO 9141-2 PIDs are not implemented yet")
