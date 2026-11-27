/**
 * Units: conversion tables (hondalog/units.py) and the UI's display units
 * (hondalog/ui/units.py). Storage stays metric, in the address map's units;
 * only what is drawn is converted. Default preset: US (°F, mph, mi, gal/h,
 * AFR; MAP stays kPa as in the Python app).
 */

export {
  STOICH_PETROL, TABLE, QUANTITY_NAMES, UnitPrefs, canonical, choices, convert, convertSpan, factors, quantity,
} from './units';
export type { Quantity, UnitPrefsInit } from './units';
export {
  DEFAULT_PRESET, DisplayUnits, METRIC, PRESETS, QUANTITIES, U, US, formatValue,
} from './display';
export type { PresetName, UnitSettings, UnitSpec } from './display';
