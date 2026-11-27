/**
 * Compact tile metrics for F1 Live: on a short stage (a phone in landscape,
 * design height 640) three tile rows must fit a group, so the tiles shrink
 * (smaller value font, thinner sparkline, tighter padding).
 */
import { createContext, useContext } from 'react';

export const CompactContext = createContext(false);
export const useCompact = (): boolean => useContext(CompactContext);
