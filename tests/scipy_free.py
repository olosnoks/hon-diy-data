"""Bilinear table lookup for the auto-tune convergence test (no scipy)."""

import numpy as np


def bilinear_lookup(table, row, col):
    r0 = np.clip(np.floor(row).astype(int), 0, table.shape[0] - 2)
    c0 = np.clip(np.floor(col).astype(int), 0, table.shape[1] - 2)
    wr, wc = row - r0, col - c0
    return (table[r0, c0] * (1 - wr) * (1 - wc) + table[r0, c0 + 1] * (1 - wr) * wc
            + table[r0 + 1, c0] * wr * (1 - wc) + table[r0 + 1, c0 + 1] * wr * wc)
