/**
 * The CYD black box (Sunton ESP32-4827S043N handheld, CH340 1a86:7523): everything the
 * laptop says to it and reads from it. Ported from hondalog/handheld/.
 *
 *   serial_proto   binary frames (COBS + CRC16), HELLO, STATS, relay frames, events, GPS
 *   hl_client      the HL text session: hello, stat, ls, get, put with resume, cfg, rm, mark, time, reboot
 *   link           request/answer: list/fetch files, push HANDHELD.MAP/CFG, MAP/ upload, set-time
 *   live           the live stream (hello, raw reads, GPS, events, heartbeat, clock exchange)
 *   companion      the handheld next to the KKL cable, for its GPS
 *   relay          the laptop's ECU reads to the handheld
 *   mapfile        HANDHELD.MAP, byte-identical to the Python's
 *   drivefile      drive and capture files (FORMAT.md); importer, sync
 *   flash          firmware with esptool-js
 */

export * as format from './format';
export * as proto from './serial_proto';
export type { Hello, Stats, GpsRecord, EventRecord, Frame } from './serial_proto';
export { Deframer, frameEncode, parseHello, toCydHello, helloText } from './serial_proto';
export * from './port';
export { HlClient, HlError, HlTxOnly, Cancelled, REASONS, reasonText, mapFolderFiles, MapFolderError } from './hl_client';
export * as hl from './hl_proto';
export { CydClient, ClockSync, OneWayClock, Opener, UsedLength, MemorySink, entryName, pushMapTo, PREFIX } from './link';
export type { FileEntry, PartSink } from './link';
export { CydLink } from './live';
export type { CydEvent, LinkHooks, LinkStateName } from './live';
export { HandheldCompanion, findHandheld, samePort, specPort, normPort, isHandheldPort, GPS_FRESH_S } from './companion';
export type { PortSource, Fix, CompanionOptions } from './companion';
export { Relay, linkFields, TOO_OLD, TX_ONLY, MAP_DIFFERS } from './relay';
export type { RelayPoller, RelayTarget } from './relay';
export { resolveMap, fromContract, versionHash, isVerified, MapError } from './hhmap';
export type { HhMap, HhChannel, HhAlarm, HhMath, HhFraming } from './hhmap';
export { buildMap, buildMapFromContract, buildMapFromToml, readMap, mapOfFile, cArray, exportFiles, DEFAULT_CFG, MAP_NAME, CFG_NAME } from './mapfile';
export type { ExportReport, MapFile, StallOptions } from './mapfile';
export { parse as parseExpr, references, ExprError } from './expr';
export type { Node as ExprNode } from './expr';
export { parseDriveFile, driveId, epochUnix, seconds, tLastUs } from './drivefile';
export type { DriveFile } from './drivefile';
export { importCard, importFile, importDrive, findMap, resultText } from './importer';
export type { ImportStore, ImportDrive, ImportResult, ImportOptions } from './importer';
export { sync, MemoryMirror, MirrorPart, mirrorFiles, mirrorName } from './sync';
export type { MirrorStore, SyncReport } from './sync';
export { flash, checkImage, parseLine, esptoolLoaderFactory, FlashError, CHIP_NAME, DEFAULT_BAUD, FALLBACK_BAUD } from './flash';
export type { FlashProgress, FlashDevice, LoaderFactory, FlashOptions } from './flash';
export { pushMap, pushText, setTime, setTimeText } from './pushmap';
export type { PushResult } from './pushmap';
