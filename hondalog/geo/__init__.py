"""GPS: tracks, the Web Mercator projection, map tiles, stall locations.

    proj     unit coordinates, distances, the scale bar
    track    a drive's GPS fixes as a ``Track``; waypoints (markers, alarms, captures)
    tiles    raster tiles from an OpenStreetMap-style server, cached on disk
    stalls   every capture of the database with where it happened
    route    a plausible loop of streets in Pearl, MS for simulators and fixtures

No Qt in here.
"""

from .track import GAP_S, GPS_CHANNELS, GPS_STREAM, Position, Track, Waypoint, gps_columns

__all__ = ["GAP_S", "GPS_CHANNELS", "GPS_STREAM", "Position", "Track", "Waypoint", "gps_columns"]
