const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("files", {
  list: () => ipcRenderer.invoke("files:list"),
  path: () => ipcRenderer.invoke("files:path"),
  clear: () => ipcRenderer.invoke("files:clear"),
  open: (fileName) => ipcRenderer.invoke("files:open", fileName),
  onChanged: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("files:changed", listener);
    return () => ipcRenderer.removeListener("files:changed", listener);
  },
});

contextBridge.exposeInMainWorld("trackedFiles", {
  list: () => ipcRenderer.invoke("tracked-files:list"),
  saveExtraction: (fileId, extractedJson) => ipcRenderer.invoke("tracked-files:save-extraction", fileId, extractedJson),
  reject: (fileId) => ipcRenderer.invoke("tracked-files:reject", fileId),
  onChanged: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("tracked-files:changed", listener);
    return () => ipcRenderer.removeListener("tracked-files:changed", listener);
  },
});

contextBridge.exposeInMainWorld("imports", {
  start: () => ipcRenderer.invoke("imports:start"),
});

contextBridge.exposeInMainWorld("systemStatus", {
  get: () => ipcRenderer.invoke("system-status:get"),
  onChanged: (callback) => {
    const listener = (_, status) => callback(status);
    ipcRenderer.on("system-status:changed", listener);
    return () => ipcRenderer.removeListener("system-status:changed", listener);
  },
});

contextBridge.exposeInMainWorld("configuration", {
  get: () => ipcRenderer.invoke("configuration:get"),
  save: (update) => ipcRenderer.invoke("configuration:save", update),
  onOpenSettings: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("configuration:open-settings", listener);
    return () => ipcRenderer.removeListener("configuration:open-settings", listener);
  },
  onRunningChanged: (callback) => {
    const listener = (_, running) => callback(running);
    ipcRenderer.on("configuration:running-changed", listener);
    return () => ipcRenderer.removeListener("configuration:running-changed", listener);
  },
});
