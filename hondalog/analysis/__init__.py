"""Offline analysis on logs. No Qt in here: the UI draws what these modules compute."""

from .compare import Comparison, Overlay, event_time, first_crossing
from .cursors import SpanStats, span_stats, value_at
from .expr import Expr, ExprError, compile_expr
from .filters import Filter, FilterSet
from .histogram import Bins, TableHistogram
from .lod import Envelope, LodPyramid, envelope
from .replay import Readout, ReplaySession
from .scatter import Fit, ScatterModel

__all__ = ["Bins", "Comparison", "Envelope", "Expr", "ExprError", "Filter", "FilterSet", "Fit",
           "LodPyramid", "Overlay", "Readout", "ReplaySession", "ScatterModel", "SpanStats",
           "TableHistogram", "compile_expr", "envelope", "event_time", "first_crossing",
           "span_stats", "value_at"]
