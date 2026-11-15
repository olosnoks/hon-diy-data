"""Track B: the black box.

An ESP32-S3 in the car records battery, ECU +B, fuel pump feed and IG1 at
5 kHz, and tach, ignition trigger and injector edges to the microsecond. When
the engine dies it saves the 30 s before and 10 s after. This package is the
laptop side. Firmware and wiring: ``firmware/blackbox/``.

What a UI needs, in one place:

Live data
    ``BlackboxReader(port)``: a thread with the same surface as
    ``hondalog.poller.Poller`` (``samples``, ``on_sample``, ``state``, ``hz``,
    ``errors``, ``last_error``, ``ready``, ``t0``, ``stop()``). It yields
    ``hondalog.poller.Sample`` with ``bb_*`` values. ``BB_CHANNELS`` /
    ``bb_channel(name)`` describe them as ``addressmap.Channel``.
    ``list_ports()`` offers ports; ``"bbsim:"`` is the built-in simulator.

Events and markers
    ``reader.events`` (queue of ``BBEvent``) or ``on_event=``. ``reader.mark(n)``
    stamps the app's marker into the box. ``reader.trigger()`` saves a capture
    now. ``reader.list_events()`` / ``reader.fetch_event(no, folder)`` pull
    captures off the SD card over USB.

Captures
    ``read_event(path)`` -> ``BlackboxEvent``. ``open_event(path)`` -> a
    ``logger.LogData`` with the matching ECU log merged in and aligned
    (``logger.read_log`` does this for ``.bbx`` files). ``diagnose(event)``
    -> ``Verdict`` with the order of events. ``export_csv(event, path)``.

Settings
    ``Config`` / ``Rule``: the trigger rules, as text. ``reader.get_config()``,
    ``reader.set_config(key, value)``, ``reader.save_config()``.

No car, no box
    ``SimDevice`` / ``Car``; ``hondalog-bbsim`` puts one on a pty.
"""

from .align import Alignment, estimate
from .channels import BB_CHANNELS, BB_NAMES, LIVE_NAMES, bb_channel
from .diagnose import Verdict, diagnose, timeline
from .eventfile import BBEventRecord, BlackboxEvent, EventFileError, export_csv, read_event, write_event
from .format import REASONS, reason_name, reason_text
from .importer import bundle, find_ecu_log, merge, open_event
from .reader import (BBEvent, BlackboxError, BlackboxReader, ClockSync, FileInfo, Hello,
                     list_ports, make_port)
from .sim import Car, CoreModel, SimDevice, SimPort
from .triggers import Config, ConfigError, Rule, TriggerEngine

EVENT_SUFFIX = ".bbx"

__all__ = [
    "Alignment", "BBEvent", "BBEventRecord", "BB_CHANNELS", "BB_NAMES", "BlackboxError",
    "BlackboxEvent", "BlackboxReader", "Car", "ClockSync", "Config", "ConfigError", "CoreModel",
    "EVENT_SUFFIX", "EventFileError", "FileInfo", "Hello", "LIVE_NAMES", "REASONS", "Rule",
    "SimDevice", "SimPort", "TriggerEngine", "Verdict", "bb_channel", "bundle", "diagnose",
    "estimate", "export_csv", "find_ecu_log", "list_ports", "make_port", "merge", "open_event",
    "read_event", "reason_name", "reason_text", "timeline", "write_event",
]
