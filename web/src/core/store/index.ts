/**
 * The drive database (agent #4): the same SQLite schema as the Python app's
 * hondalog.db, held in memory by sql.js and saved through a FileStore.
 */
export { SqliteDriveStore, driveInfo, toLoaded, type DriveStoreOptions } from './drivestore';
export { HondalogDb, DB_NAME, isDatabase, deleteDrives, deleteCaptures, vacuum, finishDrive, LogFormatError } from './db';
export { type FileStore, MemoryFileStore } from './filestore';
export { Store, DriveWriter, newDrive, annotate, refName, type DriveRef, type StoreOptions, type CaptureOptions } from './writer';
export { Log, align, readDrive, findRef, driveMeta, rateHz, type Series, type Table } from './reader';
export { type Column, column, StreamInfo, type StreamData, type LogEvent, type Dtype } from './model';
export { listEntries, search, matches, retentionPlan, applyRetention, deleteEntry, lookup, describe, KeepProtected, type DriveEntry, type RetentionPolicy } from './library';
export { writeLog, mergeWindow, extractToNewDb, importDatabase } from './transfer';
export { MIGRATIONS, SHARED_VIEWS, SLOTS, APPLICATION_ID, schemaVersion } from './schema';
export { loadSqlJs, setSqlJsWasm } from './sqljs';
export { APP_VERSION, APP_FORMAT } from './version';
export * from './export';
