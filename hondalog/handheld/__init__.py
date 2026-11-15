"""Laptop side of the handheld (firmware/handheld): compile the address map
into the file the handheld reads, read its drive files, import them into the
database.

    hondalog-handheld export-map /media/sd        HANDHELD.MAP (+ HANDHELD.CFG)
    hondalog-handheld import /media/sd            every new drive into hondalog.db
    hondalog-handheld info /media/sd/DRIVES/D000007.HHD

File layouts: firmware/handheld/FORMAT.md. The C side is
firmware/handheld/components/hhcore.
"""

from .drivefile import DriveFile, read_drive_file
from .importer import ImportResult, import_card, import_file
from .mapfile import MapFile, build_map, export_map, read_map

__all__ = ["DriveFile", "ImportResult", "MapFile", "build_map", "export_map", "import_card",
           "import_file", "read_drive_file", "read_map"]
