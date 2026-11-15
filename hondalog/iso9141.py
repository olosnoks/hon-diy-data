"""Compatibility shim: the ISO 9141-2 code lives in ``hondalog.obd2``."""

from .obd2 import ISO_BAUD, INIT_ADDRESS, Iso9141Link, Obd2Session

__all__ = ["ISO_BAUD", "INIT_ADDRESS", "Iso9141Link", "Obd2Session"]
