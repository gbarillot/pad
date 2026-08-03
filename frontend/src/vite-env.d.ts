/// <reference types="vite/client" />

type FileStatus = "raw" | string;

interface FileRecord {
  id: string;
  name: string;
  path: string;
  extension: string;
  sizeBytes: number;
  status: FileStatus;
  confidence: number | null;
  error: string | null;
  extractedJson: ExtractedJsonRecord | null;
  createdAt: string;
  updatedAt: string;
}

interface ExtractedJsonRecord {
  patient: {
    first_name: string | null;
    last_name: string | null;
    birth_date: string | null;
  };
  laboratory: {
    name: string | null;
  };
  analysis: {
    date: string | null;
    name: string | null;
    method: string | null;
    result: {
      target: string | null;
      value: string | null;
      operator: string | null;
      unit: string | null;
    };
    anteriority: {
      date: string | null;
      value: string | null;
      operator: string | null;
    } | null;
  };
  extraction: {
    confidence: number | null;
    warnings: string[];
  };
}

interface FileListResult {
  folderPath: string;
  files: FileRecord[];
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

type ConfigurationUpdate = Partial<ConfigurationRecord>;

interface Window {
  files: {
    list: () => Promise<FileListResult>;
    path: () => Promise<string>;
    clear: () => Promise<void>;
    open: (fileName: string) => Promise<void>;
    onChanged: (callback: () => void) => () => void;
  };
  trackedFiles: {
    list: () => Promise<FileRecord[]>;
    saveExtraction: (fileId: string, extractedJson: ExtractedJsonRecord) => Promise<void>;
    reject: (fileId: string) => Promise<void>;
    onChanged: (callback: () => void) => () => void;
  };
  imports: {
    start: () => Promise<ImportStartResult>;
  };
  systemStatus: {
    get: () => Promise<SystemStatus>;
    onChanged: (callback: (status: SystemStatus) => void) => () => void;
  };
  configuration: {
    get: () => Promise<ConfigurationRecord>;
    save: (update: ConfigurationUpdate) => Promise<ConfigurationRecord>;
    onOpenSettings: (callback: () => void) => () => void;
    onRunningChanged: (callback: (running: boolean) => void) => () => void;
  };
}
