"""Qt user interface. PySide6 must win over any PyQt that apt pulled in."""

import os

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
