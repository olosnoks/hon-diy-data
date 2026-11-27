"""Minimal stacked-panel PNG plots with pyqtgraph (no matplotlib in the venv)."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters
from PySide6 import QtWidgets, QtCore

from PySide6 import QtGui
_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
_fid = QtGui.QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\arial.ttf')
if _fid < 0:
    _fid = QtGui.QFontDatabase.addApplicationFont(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'hondalog', 'data', 'fonts', 'Inter-Medium.otf'))
if _fid >= 0:
    _app.setFont(QtGui.QFont(QtGui.QFontDatabase.applicationFontFamilies(_fid)[0], 8))
pg.setConfigOptions(background='w', foreground='k', antialias=True)
COLORS = ['#1f77b4', '#d62728', '#2ca02c', '#ff7f0e', '#9467bd', '#8c564b', '#e377c2', '#17becf']


class Fig:
    def __init__(self, n, title='', width=1100, height_per=190, link=True):
        self.w = pg.GraphicsLayoutWidget()
        self.w.resize(width, height_per * n + 40)
        if title:
            self.w.addLabel(title, row=0, col=0)
        self.axes = []
        for i in range(n):
            p = self.w.addPlot(row=i + 1, col=0)
            p.showGrid(x=True, y=True, alpha=0.25)
            p.addLegend(offset=(5, 5), labelTextSize='7pt')
            if self.axes and link:
                p.setXLink(self.axes[0])
            self.axes.append(p)
        self._ci = [0] * n

    def line(self, i, x, y, label=None, step=False, color=None, width=1.5):
        p = self.axes[i]
        col = color or COLORS[self._ci[i] % len(COLORS)]
        self._ci[i] += 1
        x = np.asarray(x, float); y = np.asarray(y, float)
        if step and len(x) > 1:
            xs = np.repeat(x, 2)[1:]; ys = np.repeat(y, 2)[:-1]
            x, y = xs, ys
        p.plot(x, y, pen=pg.mkPen(col, width=width), name=label)

    def scatter(self, i, x, y, label=None, color=None, size=4):
        col = color or COLORS[self._ci[i] % len(COLORS)]
        self._ci[i] += 1
        self.axes[i].plot(np.asarray(x, float), np.asarray(y, float), pen=None, symbol='o', symbolSize=size,
                          symbolBrush=col, symbolPen=None, name=label)

    def vline(self, x, color='r'):
        for p in self.axes:
            p.addItem(pg.InfiniteLine(pos=x, angle=90, pen=pg.mkPen(color, width=1, style=QtCore.Qt.DashLine)))

    def span(self, x0, x1):
        for p in self.axes:
            r = pg.LinearRegionItem(values=(x0, x1), movable=False, brush=pg.mkBrush(150, 150, 150, 70))
            p.addItem(r)

    def labels(self, i, y=None, x=None):
        if y: self.axes[i].setLabel('left', y)
        if x: self.axes[i].setLabel('bottom', x)

    def save(self, path):
        self.w.show()
        _app.processEvents()
        ex = pg.exporters.ImageExporter(self.w.scene())
        ex.parameters()['width'] = self.w.width()
        ex.export(path)
        self.w.close()
