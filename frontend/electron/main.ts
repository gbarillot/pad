import { app, BrowserWindow, Menu, ipcMain, shell } from "electron";
import Database from "better-sqlite3";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

interface FileRecord {
  id: string;
  name: string;
  path: string;
  extension: string;
  sizeBytes: number;
  status: string;
  confidence: number | null;
  error: string | null;
  extractedJson: unknown | null;
  createdAt: string;
  updatedAt: string;
}

interface FileDbRow {
  id: string;
  name: string;
  status: string;
  confidence: number | null;
  error: string | null;
  extracted_json: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface LegacyUploadDbRow {
  id: string;
  stored_filename: string;
  status: string;
  extracted_json: string | null;
  confidence: number | null;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface LegacyUploadEventDbRow {
  id: number;
  upload_id: string;
  status: string;
  error: string | null;
  created_at: string | null;
}

interface ConfigurationRecord {
  filesDirectoryPath: string;
  ollamaUrl: string;
  redcapUrl: string | null;
  redcapToken: string | null;
  redcapRecordIdField: string;
  redcapFirstNameField: string;
  redcapLastNameField: string;
  manualMode: boolean;
  minConfidence: number;
  autoCleanup: boolean;
  running: boolean;
  promptDefault: string | null;
  promptRetry: string | null;
}

interface ConfigurationDbRow {
  files_directory_path: string;
  ollama_url: string | null;
  redcap_url: string | null;
  redcap_token: string | null;
  redcap_record_id_field: string | null;
  redcap_first_name_field: string | null;
  redcap_last_name_field: string | null;
  manual_mode: number;
  min_confidence: number;
  auto_cleanup: number;
  running: number;
  prompt_default: string | null;
  prompt_retry: string | null;
}

interface ConfigurationUpdate {
  filesDirectoryPath?: string;
  ollamaUrl?: string;
  redcapUrl?: string | null;
  redcapToken?: string | null;
  redcapRecordIdField?: string;
  redcapFirstNameField?: string;
  redcapLastNameField?: string;
  manualMode?: boolean;
  minConfidence?: number;
  autoCleanup?: boolean;
  running?: boolean;
  promptDefault?: string | null;
  promptRetry?: string | null;
}

interface ImportStartResult {
  configuration: ConfigurationRecord;
  insertedCount: number;
  ignoredCount: number;
}

interface SystemStatus {
  filesAccess: boolean;
  database: boolean;
  model: boolean;
  network: boolean;
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const defaultFilesDirectoryName = "fichiers_pad";
const defaultFilesDirectoryPath = `~/Desktop/${defaultFilesDirectoryName}`;
const defaultOllamaUrl = "http://localhost:11434";
const defaultRedcapRecordIdField = "pat";
const defaultRedcapFirstNameField = "prenom";
const defaultRedcapLastNameField = "nom";
const legacyDefaultRedcapRecordIdField = "record_id";
const legacyDefaultRedcapFirstNameField = "patient_first_name";
const legacyDefaultRedcapLastNameField = "patient_last_name";
const databaseFileName = "pad.sqlite3";
const legacyDatabaseFileName = "pad_development.sqlite3";
let filesWatcher: fs.FSWatcher | null = null;
let filesWatcherDebounce: NodeJS.Timeout | null = null;
let fileEventsPollingInterval: NodeJS.Timeout | null = null;
let configurationPollingInterval: NodeJS.Timeout | null = null;
let lastSeenFileEventId = 0;
let lastSeenRunning: boolean | null = null;
let latestSystemStatus: SystemStatus | null = null;
let workerProcess: ChildProcessWithoutNullStreams | null = null;
const fileEventsPollingIntervalMs = 500;
const configurationPollingIntervalMs = 500;
const statusCheckTimeoutMs = 2500;
const expectedRedcapApiError = "The requested method is not implemented.";

app.setName("PAD");

function getFilesDirectoryPath() {
  return expandHomePath(getConfiguration().filesDirectoryPath);
}

function getTrackedFilesDirectoryPath() {
  return getFilesDirectoryPath();
}

function getDatabasePath() {
  const databasePath = path.join(app.getPath("userData"), databaseFileName);
  ensureDefaultDatabaseFile(databasePath);
  return databasePath;
}

function ensureDefaultDatabaseFile(databasePath: string) {
  if (fs.existsSync(databasePath)) {
    return;
  }

  fs.mkdirSync(path.dirname(databasePath), { recursive: true });
  const sourceDatabasePath = findLegacyDatabasePath() ?? findSeedDatabasePath();
  if (!sourceDatabasePath || path.resolve(sourceDatabasePath) === path.resolve(databasePath)) {
    return;
  }

  fs.copyFileSync(sourceDatabasePath, databasePath);
}

function findLegacyDatabasePath() {
  const candidates = [
    path.resolve(app.getAppPath(), "..", "..", "persistence", "data", legacyDatabaseFileName),
    path.resolve(app.getAppPath(), "..", "persistence", "data", legacyDatabaseFileName),
    path.resolve(process.cwd(), "..", "..", "persistence", "data", legacyDatabaseFileName),
    path.resolve(process.cwd(), "..", "persistence", "data", legacyDatabaseFileName),
    path.resolve(process.cwd(), "persistence", "data", legacyDatabaseFileName),
  ];

  return candidates.find((candidate) => fs.existsSync(candidate)) ?? null;
}

function findSeedDatabasePath() {
  const candidates = [
    path.join(process.resourcesPath, "seed", databaseFileName),
    path.resolve(app.getAppPath(), "resources", "seed", databaseFileName),
    path.resolve(process.cwd(), "resources", "seed", databaseFileName),
  ];

  return candidates.find((candidate) => fs.existsSync(candidate)) ?? null;
}

function expandHomePath(value: string) {
  if (value === "~") {
    return os.homedir();
  }

  if (value.startsWith("~/")) {
    return path.join(os.homedir(), value.slice(2));
  }

  return value;
}

function openDatabaseConnection() {
  const databasePath = getDatabasePath();
  fs.mkdirSync(path.dirname(databasePath), { recursive: true });
  const db = new Database(databasePath);
  db.pragma("busy_timeout = 30000");
  return db;
}

function createDatabaseConnection() {
  const db = openDatabaseConnection();
  ensureConfigurationSchema(db);
  ensureFilesSchema(db);
  return db;
}

type SQLiteDatabase = ReturnType<typeof createDatabaseConnection>;

function ensureConfigurationSchema(db: SQLiteDatabase) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS configuration (
      files_directory_path TEXT NOT NULL DEFAULT '${defaultFilesDirectoryPath}',
      ollama_url TEXT NOT NULL DEFAULT '${defaultOllamaUrl}',
      redcap_url TEXT DEFAULT NULL,
      redcap_token TEXT DEFAULT NULL,
      redcap_record_id_field TEXT NOT NULL DEFAULT '${defaultRedcapRecordIdField}',
      redcap_first_name_field TEXT NOT NULL DEFAULT '${defaultRedcapFirstNameField}',
      redcap_last_name_field TEXT NOT NULL DEFAULT '${defaultRedcapLastNameField}',
      manual_mode INTEGER NOT NULL DEFAULT 0 CHECK (manual_mode IN (0, 1)),
      min_confidence REAL NOT NULL DEFAULT 0.90,
      auto_cleanup INTEGER NOT NULL DEFAULT 1 CHECK (auto_cleanup IN (0, 1)),
      running INTEGER NOT NULL DEFAULT 0 CHECK (running IN (0, 1)),
      prompt_default TEXT DEFAULT NULL,
      prompt_retry TEXT DEFAULT NULL
    );
    DELETE FROM configuration
    WHERE rowid NOT IN (
      SELECT rowid FROM configuration ORDER BY rowid LIMIT 1
    );
    CREATE TRIGGER IF NOT EXISTS configuration_single_row
    BEFORE INSERT ON configuration
    WHEN (SELECT COUNT(*) FROM configuration) >= 1
    BEGIN
      SELECT RAISE(ABORT, 'configuration table can contain only one row');
    END;
  `);

  const existingColumns = new Set(
    (db.prepare("PRAGMA table_info(configuration)").all() as Array<{ name: string }>).map((column) => column.name),
  );
  if (!existingColumns.has("auto_cleanup")) {
    db.prepare("ALTER TABLE configuration ADD COLUMN auto_cleanup INTEGER NOT NULL DEFAULT 1").run();
  }
  if (!existingColumns.has("ollama_url")) {
    db.prepare(`ALTER TABLE configuration ADD COLUMN ollama_url TEXT NOT NULL DEFAULT '${defaultOllamaUrl}'`).run();
  }
  if (!existingColumns.has("redcap_record_id_field")) {
    db.prepare(`ALTER TABLE configuration ADD COLUMN redcap_record_id_field TEXT NOT NULL DEFAULT '${defaultRedcapRecordIdField}'`).run();
  }
  if (!existingColumns.has("redcap_first_name_field")) {
    db.prepare(`ALTER TABLE configuration ADD COLUMN redcap_first_name_field TEXT NOT NULL DEFAULT '${defaultRedcapFirstNameField}'`).run();
  }
  if (!existingColumns.has("redcap_last_name_field")) {
    db.prepare(`ALTER TABLE configuration ADD COLUMN redcap_last_name_field TEXT NOT NULL DEFAULT '${defaultRedcapLastNameField}'`).run();
  }
  db.prepare(`
    UPDATE configuration
    SET redcap_record_id_field = ?
    WHERE redcap_record_id_field IS NULL
       OR redcap_record_id_field = ''
       OR redcap_record_id_field = ?
  `).run(defaultRedcapRecordIdField, legacyDefaultRedcapRecordIdField);
  db.prepare(`
    UPDATE configuration
    SET redcap_first_name_field = ?
    WHERE redcap_first_name_field IS NULL
       OR redcap_first_name_field = ''
       OR redcap_first_name_field = ?
  `).run(defaultRedcapFirstNameField, legacyDefaultRedcapFirstNameField);
  db.prepare(`
    UPDATE configuration
    SET redcap_last_name_field = ?
    WHERE redcap_last_name_field IS NULL
       OR redcap_last_name_field = ''
       OR redcap_last_name_field = ?
  `).run(defaultRedcapLastNameField, legacyDefaultRedcapLastNameField);
  if (!existingColumns.has("running")) {
    db.prepare("ALTER TABLE configuration ADD COLUMN running INTEGER NOT NULL DEFAULT 0").run();
  }
  if (!existingColumns.has("prompt_default")) {
    db.prepare("ALTER TABLE configuration ADD COLUMN prompt_default TEXT").run();
  }
  if (!existingColumns.has("prompt_retry")) {
    db.prepare("ALTER TABLE configuration ADD COLUMN prompt_retry TEXT").run();
  }

  const row = db.prepare("SELECT rowid FROM configuration LIMIT 1").get();
  if (!row) {
    db.prepare("INSERT INTO configuration DEFAULT VALUES").run();
  }
}

function ensureFilesSchema(db: SQLiteDatabase) {
  migrateLegacyUploadsSchema(db);
  db.exec(`
    CREATE TABLE IF NOT EXISTS files (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL UNIQUE,
      status TEXT NOT NULL DEFAULT 'todo',
      extracted_json TEXT DEFAULT NULL,
      confidence REAL DEFAULT NULL,
      error TEXT DEFAULT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS file_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      file_id TEXT NOT NULL,
      status TEXT NOT NULL,
      error TEXT DEFAULT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS ix_file_events_file_id ON file_events (file_id);
    CREATE TRIGGER IF NOT EXISTS file_events_after_insert
    AFTER INSERT ON files
    BEGIN
      INSERT INTO file_events (file_id, status, error, created_at)
      VALUES (NEW.id, NEW.status, NEW.error, CURRENT_TIMESTAMP);
    END;
    CREATE TRIGGER IF NOT EXISTS file_events_after_status_update
    AFTER UPDATE OF status, error ON files
    WHEN OLD.status IS NOT NEW.status OR OLD.error IS NOT NEW.error
    BEGIN
      INSERT INTO file_events (file_id, status, error, created_at)
      VALUES (NEW.id, NEW.status, NEW.error, CURRENT_TIMESTAMP);
    END;
    CREATE TRIGGER IF NOT EXISTS files_updated_at_after_status_update
    AFTER UPDATE OF status, error ON files
    WHEN OLD.status IS NOT NEW.status OR OLD.error IS NOT NEW.error
    BEGIN
      UPDATE files SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
    END;
  `);

  const existingColumns = new Set(
    (db.prepare("PRAGMA table_info(files)").all() as Array<{ name: string }>).map((column) => column.name),
  );
  if (!existingColumns.has("extracted_json")) {
    db.prepare("ALTER TABLE files ADD COLUMN extracted_json TEXT").run();
  }
  if (!existingColumns.has("confidence")) {
    db.prepare("ALTER TABLE files ADD COLUMN confidence REAL").run();
  }
}

function migrateLegacyUploadsSchema(db: SQLiteDatabase) {
  if (!tableExists(db, "uploads")) {
    return;
  }

  db.exec(`
    DROP TRIGGER IF EXISTS upload_events_after_insert;
    DROP TRIGGER IF EXISTS upload_events_after_status_update;
    DROP TRIGGER IF EXISTS uploads_updated_at_after_status_update;
    CREATE TABLE IF NOT EXISTS files (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL UNIQUE,
      status TEXT NOT NULL DEFAULT 'todo',
      extracted_json TEXT DEFAULT NULL,
      confidence REAL DEFAULT NULL,
      error TEXT DEFAULT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS file_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      file_id TEXT NOT NULL,
      status TEXT NOT NULL,
      error TEXT DEFAULT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS ix_file_events_file_id ON file_events (file_id);
  `);

  const existingUploadColumns = new Set(
    (db.prepare("PRAGMA table_info(uploads)").all() as Array<{ name: string }>).map((column) => column.name),
  );
  if (!existingUploadColumns.has("extracted_json")) {
    db.prepare("ALTER TABLE uploads ADD COLUMN extracted_json TEXT").run();
  }
  if (!existingUploadColumns.has("confidence")) {
    db.prepare("ALTER TABLE uploads ADD COLUMN confidence REAL").run();
  }

  const legacyUploads = db.prepare(`
    SELECT id,
           stored_filename,
           status,
           extracted_json,
           confidence,
           error,
           created_at,
           updated_at
    FROM uploads
  `).all() as LegacyUploadDbRow[];
  const insertFile = db.prepare(`
    INSERT INTO files (id, name, status, extracted_json, confidence, error, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
      name = excluded.name,
      status = excluded.status,
      extracted_json = excluded.extracted_json,
      confidence = excluded.confidence,
      error = excluded.error,
      created_at = COALESCE(files.created_at, excluded.created_at),
      updated_at = excluded.updated_at
  `);
  const legacyUploadIdToFileId = new Map<string, string>();

  for (const upload of legacyUploads) {
    const fileId = fileIdForName(upload.stored_filename);
    legacyUploadIdToFileId.set(upload.id, fileId);
    insertFile.run(
      fileId,
      upload.stored_filename,
      upload.status,
      upload.extracted_json,
      upload.confidence,
      upload.error,
      upload.created_at,
      upload.updated_at,
    );
  }

  if (tableExists(db, "upload_events")) {
    const legacyEvents = db.prepare(`
      SELECT id, upload_id, status, error, created_at
      FROM upload_events
      ORDER BY id
    `).all() as LegacyUploadEventDbRow[];
    const insertFileEvent = db.prepare(`
      INSERT OR IGNORE INTO file_events (id, file_id, status, error, created_at)
      VALUES (?, ?, ?, ?, ?)
    `);

    for (const event of legacyEvents) {
      const fileId = legacyUploadIdToFileId.get(event.upload_id);
      if (!fileId) {
        continue;
      }
      insertFileEvent.run(event.id, fileId, event.status, event.error, event.created_at);
    }

    db.prepare("DROP TABLE upload_events").run();
  }

  db.prepare("DROP TABLE uploads").run();
}

function tableExists(db: SQLiteDatabase, tableName: string) {
  const row = db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?").get(tableName);
  return Boolean(row);
}

function getConfiguration(): ConfigurationRecord {
  const db = createDatabaseConnection();
  try {
    return readConfiguration(db);
  } finally {
    db.close();
  }
}

function saveConfiguration(_: unknown, update: ConfigurationUpdate): ConfigurationRecord {
  const currentConfiguration = getConfiguration();
  const minConfidence = hasConfigurationValue(update, "minConfidence") && Number.isFinite(update.minConfidence)
    ? update.minConfidence
    : currentConfiguration.minConfidence;
  const nextConfiguration = {
    filesDirectoryPath: hasConfigurationValue(update, "filesDirectoryPath") ? update.filesDirectoryPath : currentConfiguration.filesDirectoryPath,
    ollamaUrl: hasConfigurationValue(update, "ollamaUrl") ? update.ollamaUrl : currentConfiguration.ollamaUrl,
    redcapUrl: hasConfigurationValue(update, "redcapUrl") ? update.redcapUrl : currentConfiguration.redcapUrl,
    redcapToken: hasConfigurationValue(update, "redcapToken") ? update.redcapToken : currentConfiguration.redcapToken,
    redcapRecordIdField: hasConfigurationValue(update, "redcapRecordIdField") ? update.redcapRecordIdField : currentConfiguration.redcapRecordIdField,
    redcapFirstNameField: hasConfigurationValue(update, "redcapFirstNameField") ? update.redcapFirstNameField : currentConfiguration.redcapFirstNameField,
    redcapLastNameField: hasConfigurationValue(update, "redcapLastNameField") ? update.redcapLastNameField : currentConfiguration.redcapLastNameField,
    manualMode: hasConfigurationValue(update, "manualMode") ? update.manualMode : currentConfiguration.manualMode,
    minConfidence,
    autoCleanup: hasConfigurationValue(update, "autoCleanup") ? update.autoCleanup : currentConfiguration.autoCleanup,
    running: hasConfigurationValue(update, "running") ? update.running : currentConfiguration.running,
    promptDefault: hasConfigurationValue(update, "promptDefault") ? update.promptDefault : currentConfiguration.promptDefault,
    promptRetry: hasConfigurationValue(update, "promptRetry") ? update.promptRetry : currentConfiguration.promptRetry,
  };
  const db = createDatabaseConnection();

  try {
    db.prepare(
      `
      UPDATE configuration
      SET files_directory_path = ?,
          ollama_url = ?,
          redcap_url = ?,
          redcap_token = ?,
          redcap_record_id_field = ?,
          redcap_first_name_field = ?,
          redcap_last_name_field = ?,
          manual_mode = ?,
          min_confidence = ?,
          auto_cleanup = ?,
          running = ?,
          prompt_default = ?,
          prompt_retry = ?
      WHERE rowid = (SELECT rowid FROM configuration LIMIT 1)
      `,
    ).run(
      nextConfiguration.filesDirectoryPath || defaultFilesDirectoryPath,
      nextConfiguration.ollamaUrl || defaultOllamaUrl,
      nextConfiguration.redcapUrl || null,
      nextConfiguration.redcapToken || null,
      nextConfiguration.redcapRecordIdField || defaultRedcapRecordIdField,
      nextConfiguration.redcapFirstNameField || defaultRedcapFirstNameField,
      nextConfiguration.redcapLastNameField || defaultRedcapLastNameField,
      nextConfiguration.manualMode ? 1 : 0,
      nextConfiguration.minConfidence,
      nextConfiguration.autoCleanup ? 1 : 0,
      nextConfiguration.running ? 1 : 0,
      nextConfiguration.promptDefault || null,
      nextConfiguration.promptRetry || null,
    );
  } finally {
    db.close();
  }

  restartFilesWatcher();
  notifyFilesChanged();
  void refreshSystemStatus();

  return getConfiguration();
}

function hasConfigurationValue<Key extends keyof ConfigurationUpdate>(update: ConfigurationUpdate, key: Key) {
  return Object.prototype.hasOwnProperty.call(update, key);
}

async function listFiles() {
  const folderPath = getFilesDirectoryPath();
  const folderStat = await fs.promises.stat(folderPath).catch((error: NodeJS.ErrnoException) => {
    if (error.code === "ENOENT") {
      throw new Error(`Folder not found: ${folderPath}`);
    }

    throw error;
  });

  if (!folderStat.isDirectory()) {
    throw new Error(`Path is not a folder: ${folderPath}`);
  }

  const entries = await fs.promises.readdir(folderPath, { withFileTypes: true });
  const files = await Promise.all(
    entries
      .filter((entry) => entry.isFile() && !entry.name.startsWith(".") && path.extname(entry.name).toLowerCase() === ".pdf")
      .map(async (entry): Promise<FileRecord> => {
        const filePath = path.join(folderPath, entry.name);
        const stat = await fs.promises.stat(filePath);

        return {
          id: filePath,
          name: entry.name,
          path: filePath,
          extension: path.extname(entry.name).replace(/^\./, "").toUpperCase() || "FILE",
          sizeBytes: stat.size,
          status: "raw",
          confidence: null,
          error: null,
          extractedJson: null,
          createdAt: stat.birthtime.toISOString(),
          updatedAt: stat.mtime.toISOString(),
        };
      }),
  );

  return {
    folderPath,
    files: files.sort((left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime()),
  };
}

async function openFile(_: unknown, fileName: string) {
  if (typeof fileName !== "string" || !fileName.trim()) {
    throw new Error("Invalid file name.");
  }

  const filesDirectoryPath = getFilesDirectoryPath();
  const filePath = path.resolve(filesDirectoryPath, fileName);
  const relativePath = path.relative(filesDirectoryPath, filePath);
  if (relativePath.startsWith("..") || path.isAbsolute(relativePath)) {
    throw new Error("File is outside the configured folder.");
  }

  const stat = await fs.promises.stat(filePath).catch((error: NodeJS.ErrnoException) => {
    if (error.code === "ENOENT") {
      throw new Error(`File not found: ${fileName}`);
    }

    throw error;
  });
  if (!stat.isFile()) {
    throw new Error(`Path is not a file: ${fileName}`);
  }

  const error = await shell.openPath(filePath);
  if (error) {
    throw new Error(error);
  }
}

async function listTrackedFiles() {
  const db = createDatabaseConnection();
  const trackedFilesDirectoryPath = getTrackedFilesDirectoryPath();

  try {
    const rows = db.prepare(`
      SELECT id,
             name,
             status,
             confidence,
             error,
             extracted_json,
             created_at,
             updated_at
      FROM files
      ORDER BY COALESCE(updated_at, created_at) DESC
    `).all() as FileDbRow[];

    return Promise.all(rows.map(async (row): Promise<FileRecord> => {
      const filePath = path.join(trackedFilesDirectoryPath, row.name);
      const stat = await fs.promises.stat(filePath).catch(() => null);
      return {
        id: `file:${row.id}`,
        name: row.name,
        path: filePath,
        extension: path.extname(row.name).replace(/^\./, "").toUpperCase() || "FILE",
        sizeBytes: stat?.size ?? 0,
        status: row.status,
        confidence: row.confidence,
        error: row.error,
        extractedJson: parseExtractedJson(row.extracted_json),
        createdAt: row.created_at || new Date(0).toISOString(),
        updatedAt: row.updated_at || stat?.mtime.toISOString() || row.created_at || new Date(0).toISOString(),
      };
    }));
  } finally {
    db.close();
  }
}

function parseExtractedJson(value: string | null) {
  if (!value) {
    return null;
  }

  try {
    return JSON.parse(value) as unknown;
  } catch {
    return null;
  }
}

function saveFileExtraction(_: unknown, fileId: string, extractedJson: unknown) {
  const db = createDatabaseConnection();
  const normalizedFileId = fileId.replace(/^file:/, "");

  try {
    db.transaction(() => {
      db.prepare(`
        UPDATE files
        SET status = 'ready', extracted_json = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
      `).run(JSON.stringify(extractedJson), normalizedFileId);
      markWorkerRunning(db);
    })();
  } finally {
    db.close();
  }

  lastSeenRunning = true;
  notifyConfigurationRunningChanged(true);
  notifyTrackedFilesChanged();
  startWorkerDaemon();
}

function rejectFile(_: unknown, fileId: string) {
  const db = createDatabaseConnection();
  const normalizedFileId = fileId.replace(/^file:/, "");

  try {
    db.prepare(`
      UPDATE files
      SET status = 'failed', error = 'rejected_by_user', updated_at = CURRENT_TIMESTAMP
      WHERE id = ?
    `).run(normalizedFileId);
    notifyTrackedFilesChanged();
  } finally {
    db.close();
  }
}

async function startImport(): Promise<ImportStartResult> {
  const { files } = await listFiles();
  startWorkerDaemon();
  const db = createDatabaseConnection();

  try {
    const result = db.transaction((fileRecords: FileRecord[]) => {
      const updateRunning = db.prepare("UPDATE configuration SET running = 1 WHERE rowid = (SELECT rowid FROM configuration LIMIT 1)");
      const upsertFile = db.prepare(`
        INSERT INTO files (
          id,
          name,
          status
        ) VALUES (?, ?, 'todo')
        ON CONFLICT(id) DO UPDATE SET
          name = excluded.name,
          status = 'todo',
          extracted_json = NULL,
          confidence = NULL,
          error = NULL,
          updated_at = CURRENT_TIMESTAMP
      `);

      updateRunning.run();
      let insertedCount = 0;
      let ignoredCount = 0;

      for (const file of fileRecords) {
        const info = upsertFile.run(fileIdForName(file.name), file.name);
        if (info.changes > 0) {
          insertedCount += 1;
        } else {
          ignoredCount += 1;
        }
      }

      return { insertedCount, ignoredCount };
    })(files);

    lastSeenRunning = true;
    notifyConfigurationRunningChanged(true);
    notifyTrackedFilesChanged();

    return {
      configuration: readConfiguration(db),
      ...result,
    };
  } finally {
    db.close();
  }
}

function markWorkerRunning(db: SQLiteDatabase) {
  db.prepare("UPDATE configuration SET running = 1 WHERE rowid = (SELECT rowid FROM configuration LIMIT 1)").run();
}

function hasPendingWorkerFiles(db: SQLiteDatabase) {
  const row = db.prepare(`
    SELECT 1
    FROM files
    WHERE status IN ('todo', 'ready', 'extracting', 'processing', 'saving', 'registering', 'transferring', 'recording')
    LIMIT 1
  `).get();
  return Boolean(row);
}

function startWorkerDaemonForPendingFiles() {
  const db = createDatabaseConnection();
  let shouldStart = false;

  try {
    shouldStart = hasPendingWorkerFiles(db);
    if (shouldStart) {
      markWorkerRunning(db);
    }
  } finally {
    db.close();
  }

  if (!shouldStart) {
    return;
  }

  lastSeenRunning = true;
  notifyConfigurationRunningChanged(true);
  startWorkerDaemon();
}

async function clearFilesAndDatabase() {
  const filesDirectoryPath = getFilesDirectoryPath();
  const db = createDatabaseConnection();

  try {
    db.transaction(() => {
      db.prepare("DELETE FROM file_events").run();
      db.prepare("DELETE FROM files").run();
    })();
  } finally {
    db.close();
  }

  const entries = await fs.promises.readdir(filesDirectoryPath, { withFileTypes: true }).catch((error: NodeJS.ErrnoException) => {
    if (error.code === "ENOENT") {
      return [];
    }

    throw error;
  });
  await Promise.all(
    entries
      .filter((entry) => entry.isFile() || entry.isSymbolicLink())
      .map((entry) => fs.promises.unlink(path.join(filesDirectoryPath, entry.name)).catch((error: NodeJS.ErrnoException) => {
        if (error.code !== "ENOENT") {
          throw error;
        }
      })),
  );

  notifyTrackedFilesChanged();
  notifyFilesChanged();
}

function fileIdForName(fileName: string) {
  return createHash("sha1").update(fileName).digest("hex");
}

function startWorkerDaemon() {
  if (workerProcess && !workerProcess.killed && workerProcess.exitCode === null) {
    return;
  }

  const workerDirectory = findWorkerDirectory();
  if (!workerDirectory) {
    throw new Error("Worker directory not found. Set PAD_WORKER_DIR or run the app from the project checkout.");
  }

  workerProcess = spawn("uv", ["run", "python", "-m", "app.daemon"], {
    cwd: workerDirectory,
    env: workerEnvironment(),
  });
  workerProcess.stdout.on("data", (chunk) => {
    console.log(`[worker] ${chunk.toString().trimEnd()}`);
  });
  workerProcess.stderr.on("data", (chunk) => {
    console.error(`[worker] ${chunk.toString().trimEnd()}`);
  });
  workerProcess.on("error", (error) => {
    console.error("worker failed to start", error);
    workerProcess = null;
    void saveConfiguration(null, { running: false });
  });
  workerProcess.on("exit", (code, signal) => {
    if (code !== 0 && signal !== "SIGTERM") {
      console.error(`worker exited unexpectedly with code ${code ?? "null"} and signal ${signal ?? "null"}`);
    }
    workerProcess = null;
    void saveConfiguration(null, { running: false });
  });
}

function workerEnvironment(): NodeJS.ProcessEnv {
  const databasePath = getDatabasePath();
  const configuration = getConfiguration();
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    DATABASE_URL: databaseUrlFromPath(databasePath),
    FILES_DIR: expandHomePath(configuration.filesDirectoryPath),
    PAD_DISABLE_ENV_FILE: "true",
    OLLAMA_BASE_URL: configuration.ollamaUrl || defaultOllamaUrl,
    OCR_LANGUAGE: "fra+eng",
    POLL_INTERVAL: "1.0",
    REDCAP_RECORD_ID_FIELD: configuration.redcapRecordIdField || defaultRedcapRecordIdField,
    REDCAP_FIRST_NAME_FIELD: configuration.redcapFirstNameField || defaultRedcapFirstNameField,
    REDCAP_LAST_NAME_FIELD: configuration.redcapLastNameField || defaultRedcapLastNameField,
    REDCAP_VERIFY_SSL: "true",
  };

  if (configuration.redcapUrl) {
    env.REDCAP_API_URL = configuration.redcapUrl;
  } else {
    delete env.REDCAP_API_URL;
  }
  if (configuration.redcapToken) {
    env.REDCAP_TOKEN = configuration.redcapToken;
  } else {
    delete env.REDCAP_TOKEN;
  }

  return env;
}

function databaseUrlFromPath(databasePath: string) {
  return `sqlite:///${databasePath}`;
}

function stopWorkerDaemon() {
  if (!workerProcess || workerProcess.killed || workerProcess.exitCode !== null) {
    workerProcess = null;
    return;
  }

  workerProcess.kill("SIGTERM");
  workerProcess = null;
}

function findWorkerDirectory() {
  const configuredWorkerDirectory = process.env.PAD_WORKER_DIR;
  const candidates = [
    configuredWorkerDirectory ? expandHomePath(configuredWorkerDirectory) : null,
    path.resolve(app.getAppPath(), "..", "worker"),
    path.resolve(app.getAppPath(), "..", "..", "worker"),
    path.resolve(process.cwd(), "..", "worker"),
    path.resolve(process.cwd(), "worker"),
  ];

  for (const candidate of candidates) {
    if (!candidate) {
      continue;
    }
    if (fs.existsSync(path.join(candidate, "app", "daemon.py")) && fs.existsSync(path.join(candidate, "pyproject.toml"))) {
      return candidate;
    }
  }

  return null;
}

async function getSystemStatus() {
  if (!latestSystemStatus) {
    latestSystemStatus = await checkSystemStatus();
  }
  return latestSystemStatus;
}

async function refreshSystemStatus() {
  latestSystemStatus = await checkSystemStatus();
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send("system-status:changed", latestSystemStatus);
  }
  return latestSystemStatus;
}

async function checkSystemStatus(): Promise<SystemStatus> {
  const databasePath = getDatabasePath();
  const databaseExists = fs.existsSync(databasePath);
  const configuration = databaseExists ? getConfiguration() : null;
  const filesDirectoryPath = expandHomePath(configuration?.filesDirectoryPath || defaultFilesDirectoryPath);
  const redcapUrl = configuration?.redcapUrl || "";

  const [filesAccess, model, network] = await Promise.all([
    isDirectory(filesDirectoryPath),
    isOllamaResponding(configuration?.ollamaUrl || defaultOllamaUrl),
    isRedcapApiResponding(redcapUrl),
  ]);

  return {
    filesAccess,
    database: databaseExists,
    model,
    network,
  };
}

async function isDirectory(directoryPath: string) {
  try {
    return (await fs.promises.stat(directoryPath)).isDirectory();
  } catch {
    return false;
  }
}

async function isOllamaResponding(baseUrl: string) {
  return isUrlResponding(`${baseUrl.replace(/\/$/, "")}/api/tags`, { requireOk: true });
}

async function isRedcapApiResponding(url: string) {
  const responseText = await requestText(url, {
    rejectUnauthorized: redcapVerifySsl(),
  });
  return responseText?.includes(`<error>${expectedRedcapApiError}</error>`) ?? false;
}

async function isUrlResponding(
  url: string,
  { healthyStatuses = [], requireOk = false }: { healthyStatuses?: number[]; requireOk?: boolean } = {},
) {
  if (!url.trim()) {
    return false;
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), statusCheckTimeoutMs);
  try {
    const response = await fetch(url, {
      method: "GET",
      signal: controller.signal,
    });
    return healthyStatuses.includes(response.status) || (requireOk ? response.ok : true);
  } catch {
    return false;
  } finally {
    clearTimeout(timeout);
  }
}

function requestText(
  url: string,
  { redirectCount = 0, rejectUnauthorized = true }: { redirectCount?: number; rejectUnauthorized?: boolean } = {},
) {
  return new Promise<string | null>((resolve) => {
    if (!url.trim()) {
      resolve(null);
      return;
    }

    let parsedUrl: URL;
    try {
      parsedUrl = new URL(url);
    } catch {
      resolve(null);
      return;
    }

    const requestModule = parsedUrl.protocol === "https:" ? https : http;
    const request = requestModule.get(
      parsedUrl,
      {
        rejectUnauthorized,
        timeout: statusCheckTimeoutMs,
      },
      (response) => {
        const location = response.headers.location;
        if (location && response.statusCode && [301, 302, 303, 307, 308].includes(response.statusCode) && redirectCount < 3) {
          response.resume();
          resolve(
            requestText(new URL(location, parsedUrl).toString(), {
              redirectCount: redirectCount + 1,
              rejectUnauthorized,
            }),
          );
          return;
        }

        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk: string) => {
          body += chunk;
        });
        response.on("end", () => {
          resolve(body);
        });
      },
    );

    request.on("timeout", () => {
      request.destroy();
      resolve(null);
    });
    request.on("error", () => {
      resolve(null);
    });
  });
}

function redcapVerifySsl() {
  return true;
}

function notifyTrackedFilesChanged() {
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send("tracked-files:changed");
  }
}

function readConfigurationRunning(db: SQLiteDatabase) {
  const row = db.prepare("SELECT running FROM configuration LIMIT 1").get() as { running: number } | undefined;
  return Boolean(row?.running);
}

function readConfiguration(db: SQLiteDatabase): ConfigurationRecord {
  const row = db.prepare("SELECT files_directory_path, ollama_url, redcap_url, redcap_token, redcap_record_id_field, redcap_first_name_field, redcap_last_name_field, manual_mode, min_confidence, auto_cleanup, running, prompt_default, prompt_retry FROM configuration LIMIT 1").get() as ConfigurationDbRow;

  return {
    filesDirectoryPath: row.files_directory_path,
    ollamaUrl: row.ollama_url || defaultOllamaUrl,
    redcapUrl: row.redcap_url,
    redcapToken: row.redcap_token,
    redcapRecordIdField: row.redcap_record_id_field || defaultRedcapRecordIdField,
    redcapFirstNameField: row.redcap_first_name_field || defaultRedcapFirstNameField,
    redcapLastNameField: row.redcap_last_name_field || defaultRedcapLastNameField,
    manualMode: Boolean(row.manual_mode),
    minConfidence: row.min_confidence,
    autoCleanup: Boolean(row.auto_cleanup),
    running: Boolean(row.running),
    promptDefault: row.prompt_default,
    promptRetry: row.prompt_retry,
  };
}

function notifyConfigurationRunningChanged(running: boolean) {
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send("configuration:running-changed", running);
  }
}

function initializeLastSeenRunning() {
  const db = createDatabaseConnection();
  try {
    lastSeenRunning = readConfigurationRunning(db);
  } finally {
    db.close();
  }
}

function pollConfigurationRunning() {
  const db = openDatabaseConnection();
  try {
    const running = readConfigurationRunning(db);
    if (lastSeenRunning !== running) {
      lastSeenRunning = running;
      notifyConfigurationRunningChanged(running);
    }
  } finally {
    db.close();
  }
}

function startConfigurationPolling() {
  if (configurationPollingInterval) {
    return;
  }

  initializeLastSeenRunning();
  configurationPollingInterval = setInterval(() => {
    try {
      pollConfigurationRunning();
    } catch (error) {
      console.error("configuration polling failed", error);
    }
  }, configurationPollingIntervalMs);
}

function initializeLastSeenFileEventId() {
  const db = createDatabaseConnection();
  try {
    const row = db.prepare("SELECT COALESCE(MAX(id), 0) AS id FROM file_events").get() as { id: number };
    lastSeenFileEventId = row.id;
  } finally {
    db.close();
  }
}

function pollFileEvents() {
  const db = openDatabaseConnection();
  try {
    const row = db.prepare("SELECT COALESCE(MAX(id), 0) AS id FROM file_events WHERE id > ?").get(lastSeenFileEventId) as { id: number };
    if (row.id > lastSeenFileEventId) {
      lastSeenFileEventId = row.id;
      notifyTrackedFilesChanged();
    }
  } finally {
    db.close();
  }
}

function startFileEventsPolling() {
  if (fileEventsPollingInterval) {
    return;
  }

  initializeLastSeenFileEventId();
  fileEventsPollingInterval = setInterval(() => {
    try {
      pollFileEvents();
    } catch (error) {
      console.error("file event polling failed", error);
    }
  }, fileEventsPollingIntervalMs);
}

function notifyFilesChanged() {
  if (filesWatcherDebounce) {
    clearTimeout(filesWatcherDebounce);
  }

  filesWatcherDebounce = setTimeout(() => {
    for (const window of BrowserWindow.getAllWindows()) {
      window.webContents.send("files:changed");
    }
  }, 50);
}

function startFilesWatcher() {
  if (filesWatcher) {
    return;
  }

  const folderPath = getFilesDirectoryPath();
  try {
    filesWatcher = fs.watch(folderPath, notifyFilesChanged);
    filesWatcher.on("error", () => {
      filesWatcher?.close();
      filesWatcher = null;
    });
  } catch {
    filesWatcher = null;
  }
}

function restartFilesWatcher() {
  filesWatcher?.close();
  filesWatcher = null;
  startFilesWatcher();
}

function openSettings() {
  const targetWindow = BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0];
  targetWindow?.webContents.send("configuration:open-settings");
}

function createApplicationMenu() {
  const menu = Menu.buildFromTemplate([
    ...(process.platform === "darwin"
      ? [
          {
            label: app.name,
            submenu: [
              { role: "about" as const },
              { type: "separator" as const },
              {
                label: "Configuration",
                accelerator: "Command+,",
                click: openSettings,
              },
              { type: "separator" as const },
              { role: "hide" as const },
              { role: "hideOthers" as const },
              { role: "unhide" as const },
              { type: "separator" as const },
              { role: "quit" as const },
            ],
          },
        ]
      : []),
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "selectAll" },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    {
      label: "Window",
      submenu: [
        { role: "minimize" },
        { role: "zoom" },
        ...(process.platform === "darwin"
          ? [{ type: "separator" as const }, { role: "front" as const }]
          : [{ role: "close" as const }]),
      ],
    },
  ]);
  Menu.setApplicationMenu(menu);
}

function createWindow() {
  const preloadPath = path.join(__dirname, "preload.cjs");
  const window = new BrowserWindow({
    width: 1400,
    height: 1000,
    minWidth: 1200,
    minHeight: 600,
    title: "PAD",
    backgroundColor: "#edf3f9",
    titleBarStyle: "hiddenInset",
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    void window.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    void window.loadFile(path.join(app.getAppPath(), "dist", "index.html"));
  }
}

ipcMain.handle("files:list", listFiles);
ipcMain.handle("files:path", getFilesDirectoryPath);
ipcMain.handle("files:clear", clearFilesAndDatabase);
ipcMain.handle("files:open", openFile);
ipcMain.handle("tracked-files:list", listTrackedFiles);
ipcMain.handle("tracked-files:save-extraction", saveFileExtraction);
ipcMain.handle("tracked-files:reject", rejectFile);
ipcMain.handle("imports:start", startImport);
ipcMain.handle("configuration:get", getConfiguration);
ipcMain.handle("configuration:save", saveConfiguration);
ipcMain.handle("system-status:get", getSystemStatus);

app.whenReady().then(async () => {
  getConfiguration();
  await refreshSystemStatus();
  createApplicationMenu();
  createWindow();
  startFilesWatcher();
  startFileEventsPolling();
  startConfigurationPolling();
  try {
    startWorkerDaemonForPendingFiles();
  } catch (error) {
    console.error("worker pending-file startup failed", error);
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
      startFilesWatcher();
      startFileEventsPolling();
      startConfigurationPolling();
      try {
        startWorkerDaemonForPendingFiles();
      } catch (error) {
        console.error("worker pending-file startup failed", error);
      }
    }
  });
});

app.on("before-quit", () => {
  stopWorkerDaemon();
});

app.on("window-all-closed", () => {
  stopWorkerDaemon();
  if (fileEventsPollingInterval) {
    clearInterval(fileEventsPollingInterval);
    fileEventsPollingInterval = null;
  }
  if (configurationPollingInterval) {
    clearInterval(configurationPollingInterval);
    configurationPollingInterval = null;
  }

  if (process.platform !== "darwin") {
    app.quit();
  }
});
