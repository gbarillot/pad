import { useEffect, useState, type ReactNode } from "react";
import { ArrowLeft, LoaderCircle, Play, Search, Shredder } from "lucide-react";

type LoadState = "idle" | "loading" | "success" | "error";
type Page = "files" | "configuration";
type StatusFilter = "all" | "processing" | "validation" | "recording" | "failures";
type ConfigurationTab = "business" | "technical" | "prompt";
const filePollingIntervalMs = 250;
const statusFilterStyles: Record<StatusFilter, { border: string; dot: string }> = {
  all: { border: "border-black", dot: "bg-black" },
  processing: { border: "border-purple-500", dot: "bg-purple-500" },
  validation: { border: "border-orange-500", dot: "bg-orange-500" },
  recording: { border: "border-blue-500", dot: "bg-blue-500" },
  failures: { border: "border-red-500", dot: "bg-red-500" },
};
const defaultSelectedFilters: StatusFilter[] = [];
const configurationTabs: Array<{ id: ConfigurationTab; label: string }> = [
  { id: "business", label: "Paramètres métier" },
  { id: "technical", label: "Paramètres techniques" },
  { id: "prompt", label: "Prompt" },
];

function App() {
  const [files, setFiles] = useState<FileRecord[]>([]);
  const [folderPath, setFolderPath] = useState("");
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [error, setError] = useState("");
  const [selectedFilters, setSelectedFilters] = useState<StatusFilter[]>(defaultSelectedFilters);
  const [searchTerm, setSearchTerm] = useState("");
  const [activePage, setActivePage] = useState<Page>("files");
  const [filesDirectoryPath, setFilesDirectoryPath] = useState("~/Desktop/fichiers_pad");
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [redcapApiUrl, setRedcapApiUrl] = useState("");
  const [redcapApiToken, setRedcapApiToken] = useState("");
  const [manualValidationRequired, setManualValidationRequired] = useState(false);
  const [autoCleanup, setAutoCleanup] = useState(true);
  const [minimumConfidenceScore, setMinimumConfidenceScore] = useState("0.9");
  const [promptDefault, setPromptDefault] = useState("");
  const [promptRetry, setPromptRetry] = useState("");
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [clearModalOpen, setClearModalOpen] = useState(false);
  const [clearInProgress, setClearInProgress] = useState(false);
  const [clearError, setClearError] = useState("");
  const [reviewFile, setReviewFile] = useState<FileRecord | null>(null);
  const [inspectFile, setInspectFile] = useState<FileRecord | null>(null);
  const [failureFile, setFailureFile] = useState<FileRecord | null>(null);
  const [running, setRunning] = useState(false);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);

  async function loadFiles({ silent = false }: { silent?: boolean } = {}) {
    if (!silent) {
      setLoadState("loading");
    }
    setError("");

    try {
      if (!window.files) {
        throw new Error("Electron preload is unavailable. Restart the dev app with npm run dev.");
      }

      const [result, trackedFiles] = await Promise.all([
        window.files.list(),
        window.trackedFiles?.list() ?? Promise.resolve([]),
      ]);
      setFiles(mergeFileSources(result.files, trackedFiles));
      setFolderPath(result.folderPath);
      setLoadState("success");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to read files.");
      setLoadState("error");
    }
  }

  useEffect(() => {
    void loadConfiguration();
    void loadSystemStatus();
    void window.files?.path().then(setFolderPath).catch(() => undefined);
    void loadFiles();

    const intervalId = window.setInterval(() => {
      void loadFiles({ silent: true });
    }, filePollingIntervalMs);
    const unsubscribe = window.files?.onChanged(() => {
      void loadFiles({ silent: true });
    }) ?? (() => undefined);
    const unsubscribeTrackedFiles = window.trackedFiles?.onChanged(() => {
      void loadFiles({ silent: true });
    }) ?? (() => undefined);
    const unsubscribeOpenSettings = window.configuration?.onOpenSettings(() => {
      setActivePage("configuration");
    }) ?? (() => undefined);
    const unsubscribeRunningChanged = window.configuration?.onRunningChanged((nextRunning) => {
      setRunning(nextRunning);
    }) ?? (() => undefined);
    const unsubscribeSystemStatus = window.systemStatus?.onChanged((nextSystemStatus) => {
      setSystemStatus(nextSystemStatus);
    }) ?? (() => undefined);

    return () => {
      window.clearInterval(intervalId);
      unsubscribe();
      unsubscribeTrackedFiles();
      unsubscribeOpenSettings();
      unsubscribeRunningChanged();
      unsubscribeSystemStatus();
    };
  }, []);

  async function loadSystemStatus() {
    if (!window.systemStatus) {
      return;
    }

    setSystemStatus(await window.systemStatus.get());
  }

  async function loadConfiguration() {
    if (!window.configuration) {
      return;
    }

    const configuration = await window.configuration.get();
    applyConfiguration(configuration);
  }

  function applyConfiguration(configuration: ConfigurationRecord) {
    setFilesDirectoryPath(configuration.filesDirectoryPath);
    setOllamaUrl(configuration.ollamaUrl);
    setRedcapApiUrl(configuration.redcapUrl ?? "");
    setRedcapApiToken(configuration.redcapToken ?? "");
    setManualValidationRequired(configuration.manualMode);
    setAutoCleanup(configuration.autoCleanup);
    setMinimumConfidenceScore(configuration.minConfidence.toString());
    setRunning(configuration.running);
    setPromptDefault(configuration.promptDefault ?? "");
    setPromptRetry(configuration.promptRetry ?? "");
  }

  async function saveConfiguration(update: ConfigurationUpdate) {
    if (!window.configuration) {
      return;
    }

    const configuration = await window.configuration.save(update);
    applyConfiguration(configuration);
    void window.files?.path().then(setFolderPath).catch(() => undefined);
    void loadFiles({ silent: true });
  }

  async function startImport() {
    if (!window.imports) {
      throw new Error("Electron import API is unavailable. Restart the app.");
    }

    const result = await window.imports.start();
    applyConfiguration(result.configuration);
    setImportModalOpen(false);
    void loadFiles({ silent: true });
  }

  async function clearFiles() {
    setClearError("");
    setClearInProgress(true);

    try {
      if (!window.files?.clear) {
        throw new Error("L'API de suppression n'est pas disponible. Redémarrez complètement l'application Electron.");
      }

      await window.files.clear();
      setClearModalOpen(false);
      void loadFiles({ silent: true });
    } catch (err) {
      setClearError(err instanceof Error ? err.message : "Impossible de vider les fichiers.");
    } finally {
      setClearInProgress(false);
    }
  }

  async function openFile(file: FileRecord) {
    try {
      await window.files.open(file.name);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Impossible d'ouvrir le fichier.");
    }
  }

  const statusFilters = getStatusFilters(files);
  const visibleFiles = files.filter((file) => matchesAnyStatusFilter(file, selectedFilters) && matchesSearch(file, searchTerm));

  function toggleStatusFilter(filter: StatusFilter) {
    if (filter === "all") {
      return;
    }

    setSelectedFilters((currentFilters) => {
      if (currentFilters.includes(filter)) {
        return currentFilters.filter((currentFilter) => currentFilter !== filter);
      }

      return [...currentFilters, filter];
    });
  }

  return (
    <main className="h-screen overflow-hidden bg-[#edf3f9] text-slate-900">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_top_left,rgba(51,136,216,0.22),transparent_34%),radial-gradient(circle_at_85%_10%,rgba(149,205,255,0.26),transparent_30%)]" />
      <section className="relative mx-auto flex h-full min-h-0 w-full max-w-7xl flex-col overflow-hidden px-6 py-8 sm:px-10 lg:px-12">
        <AppHeader status={systemStatus}>
          <SearchBar
            value={searchTerm}
            onChange={(value) => {
              setSearchTerm(value);
              setSelectedFilters(defaultSelectedFilters);
            }}
          />
        </AppHeader>

        {activePage === "files" ? (
          <>
            <section className="mb-6 shrink-0 flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-3">
                {statusFilters.map((filter) => {
                  const selected = filter.id === "all" || selectedFilters.includes(filter.id);

                  return (
                    <label
                      className={`inline-flex items-center gap-2 rounded-full border bg-white/40 px-3 py-2 text-left text-sm font-normal ${filter.id === "all" ? "cursor-default" : "cursor-pointer"} ${selected ? `${statusFilterStyles[filter.id].border} text-black` : "border-slate-200 text-slate-400"}`}
                      key={filter.id}
                    >
                      <input
                        checked={selected}
                        className="sr-only"
                        disabled={filter.id === "all"}
                        onChange={() => toggleStatusFilter(filter.id)}
                        type="checkbox"
                      />
                      <span className={`h-[10px] w-[10px] rounded-full ${statusFilterStyles[filter.id].dot}`} />
                      {filter.label} ({filter.count})
                    </label>
                  );
                })}
              </div>
              <div className="flex flex-wrap gap-3">
                {!running ? (
                  <button
                    className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white/60 px-5 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-white focus:outline-none focus:ring-4 focus:ring-slate-200"
                    onClick={() => setClearModalOpen(true)}
                    type="button"
                  >
                    <Shredder aria-hidden="true" className="h-4 w-4" strokeWidth={2.2} />
                    Vider
                  </button>
                ) : null}
                <button
                  className={`inline-flex items-center gap-2 rounded-md px-6 py-2.5 text-sm font-semibold text-white shadow-soft-blue transition focus:outline-none focus:ring-4 focus:ring-clinic-200 ${running ? "cursor-not-allowed bg-clinic-500/70" : "bg-clinic-600 hover:bg-clinic-700"}`}
                  disabled={running}
                  onClick={() => setImportModalOpen(true)}
                  type="button"
                >
                  {running ? (
                    <LoaderCircle aria-hidden="true" className="h-4 w-4 animate-spin" strokeWidth={2.2} />
                  ) : (
                    <Play aria-hidden="true" className="h-4 w-4 fill-current" strokeWidth={2.2} />
                  )}
                  {running ? "Import en cours" : "Importer les fichiers"}
                </button>
              </div>
            </section>

            <section className="min-h-0 flex-1 overflow-y-auto pb-8 pr-1">
              {loadState === "error" ? <ErrorState message={error} onRetry={loadFiles} /> : null}
              {loadState === "loading" && files.length === 0 ? <LoadingState /> : null}
              {loadState === "success" && files.length === 0 ? <EmptyState /> : null}
              {loadState === "success" && files.length > 0 && visibleFiles.length === 0 ? <FilteredEmptyState /> : null}
              {visibleFiles.length > 0 ? (
                <FilesTable
                  files={visibleFiles}
                  onFailure={(file) => {
                    setReviewFile(null);
                    setInspectFile(null);
                    setFailureFile(file);
                  }}
                  onInspect={(file) => {
                    setReviewFile(null);
                    setFailureFile(null);
                    setInspectFile(file);
                  }}
                  onOpen={openFile}
                  onReview={(file) => {
                    setInspectFile(null);
                    setFailureFile(null);
                    setReviewFile(file);
                  }}
                />
              ) : null}
            </section>
          </>
        ) : (
          <ConfigurationPage
            filesDirectoryPath={filesDirectoryPath}
            ollamaUrl={ollamaUrl}
            redcapApiToken={redcapApiToken}
            redcapApiUrl={redcapApiUrl}
            manualValidationRequired={manualValidationRequired}
            autoCleanup={autoCleanup}
            minimumConfidenceScore={minimumConfidenceScore}
            promptDefault={promptDefault}
            promptRetry={promptRetry}
            setFilesDirectoryPath={setFilesDirectoryPath}
            setOllamaUrl={setOllamaUrl}
            setRedcapApiToken={setRedcapApiToken}
            setRedcapApiUrl={setRedcapApiUrl}
            setManualValidationRequired={setManualValidationRequired}
            setAutoCleanup={setAutoCleanup}
            setMinimumConfidenceScore={setMinimumConfidenceScore}
            setPromptDefault={setPromptDefault}
            setPromptRetry={setPromptRetry}
            onBack={() => setActivePage("files")}
            saveConfiguration={saveConfiguration}
          />
        )}
      </section>
      <ImportConfirmationModal onClose={() => setImportModalOpen(false)} onConfirm={startImport} open={importModalOpen} />
      <ClearConfirmationModal
        error={clearError}
        loading={clearInProgress}
        onClose={() => {
          setClearError("");
          setClearModalOpen(false);
        }}
        onConfirm={clearFiles}
        open={clearModalOpen}
      />
      <ReviewDrawer
        file={failureFile}
        onClose={() => setFailureFile(null)}
        onSaved={() => {
          setFailureFile(null);
          void loadFiles({ silent: true });
        }}
        variant="replay"
      />
      <ReviewDrawer
        file={reviewFile}
        onClose={() => setReviewFile(null)}
        onSaved={() => {
          setReviewFile(null);
          void loadFiles({ silent: true });
        }}
      />
      <ReviewDrawer
        file={inspectFile}
        onClose={() => setInspectFile(null)}
        onSaved={() => undefined}
        readOnly
      />
    </main>
  );
}

function AppHeader({ children, status }: { children: ReactNode; status: SystemStatus | null }) {
  return (
    <header className="mb-8 flex flex-col gap-6 pt-[30px] md:flex-row md:items-start md:justify-between">
      <div>
        <h1 className="bg-gradient-to-r from-clinic-700 via-clinic-500 to-cyan-400 bg-clip-text text-4xl font-bold tracking-tight text-transparent drop-shadow-[0_10px_22px_rgba(51,136,216,0.22)] md:text-5xl">PAD</h1>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">
          Plateforme d'Automatisation Documentaire
        </p>
      </div>
      <div className="flex flex-col gap-4 md:items-end">
        <StatusIndicators status={status} />
        {children}
      </div>
    </header>
  );
}

function SearchBar({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <label className="relative block w-full md:w-80">
      <span className="sr-only">Rechercher un fichier</span>
      <Search
        aria-hidden="true"
        className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
        strokeWidth={2}
      />
      <input
        className="w-full rounded-2xl border-0 bg-[#edf3f9] py-3 pl-11 pr-4 text-sm font-medium text-slate-700 shadow-neo-inset outline-none placeholder:text-slate-400 focus:ring-4 focus:ring-clinic-200"
        onChange={(event) => onChange(event.target.value)}
        placeholder="Rechercher un fichier..."
        type="search"
        value={value}
      />
    </label>
  );
}

function StatusIndicators({ status }: { status: SystemStatus | null }) {
  const indicators = [
    { label: "Accès fichiers", ok: status?.filesAccess },
    { label: "AI Model", ok: status?.model },
    { label: "API", ok: status?.network },
  ];

  return (
    <div className="flex -translate-y-5 flex-wrap gap-2 md:justify-end">
      {indicators.map((indicator) => (
        <div className="inline-flex items-center gap-2 px-1 py-1 text-xs font-semibold text-slate-600" key={indicator.label}>
          <span className={`h-2.5 w-2.5 rounded-full ${statusIndicatorStyle(indicator.ok)}`} />
          {indicator.label}
        </div>
      ))}
    </div>
  );
}

function statusIndicatorStyle(ok: boolean | undefined) {
  if (ok === true) {
    return "bg-emerald-400 shadow-[0_0_0_3px_rgba(52,211,153,0.18),0_0_12px_rgba(16,185,129,0.8)]";
  }
  if (ok === false) {
    return "bg-rose-500 shadow-[0_0_0_3px_rgba(244,63,94,0.16),0_0_12px_rgba(244,63,94,0.65)]";
  }
  return "bg-slate-300 shadow-[0_0_0_3px_rgba(148,163,184,0.16)]";
}

function ReviewDrawer({
  file,
  onClose,
  onSaved,
  readOnly = false,
  variant = "review",
}: {
  file: FileRecord | null;
  onClose: () => void;
  onSaved: (file: FileRecord) => void;
  readOnly?: boolean;
  variant?: "review" | "replay";
}) {
  const [draft, setDraft] = useState<ExtractedJsonRecord>(() => normalizeExtractedJson(null));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    setDraft(normalizeExtractedJson(file?.extractedJson ?? null));
    setError("");
    setFieldErrors({});
  }, [file]);

  if (!file) {
    return null;
  }

  const replayMode = variant === "replay";
  const title = readOnly ? "Analyse" : replayMode ? "Rejouer" : "Validation";
  const primaryLabel = replayMode ? "Rejouer" : "Enregistrer";
  const savingLabel = replayMode ? "Rejeu..." : "Sauvegarde...";

  async function saveReview() {
    if (!window.trackedFiles || !file) {
      setError("La sauvegarde n'est pas disponible dans cette session. Fermez puis relancez PAD.");
      return;
    }

    const nextFieldErrors = validateReviewDraft(draft);
    if (Object.keys(nextFieldErrors).length > 0) {
      setFieldErrors(nextFieldErrors);
      return;
    }

    setSaving(true);
    setError("");
    setFieldErrors({});
    try {
      const extractedJson = extractedJsonForSave(draft);
      await window.trackedFiles.saveExtraction(file.id, extractedJson);
      onSaved({ ...file, extractedJson, status: "ready" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Impossible de sauvegarder les données extraites.");
    } finally {
      setSaving(false);
    }
  }

  async function rejectReview() {
    if (!window.trackedFiles || !file) {
      setError("La sauvegarde n'est pas disponible dans cette session. Fermez puis relancez PAD.");
      return;
    }

    setSaving(true);
    setError("");
    try {
      await window.trackedFiles.reject(file.id);
      onSaved({ ...file, status: "failed" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Impossible de rejeter le fichier.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <button
        aria-label={readOnly ? "Fermer l'analyse" : "Fermer la validation"}
        className="fixed inset-0 z-40 bg-slate-950/20"
        onClick={onClose}
        type="button"
      />
      <aside className="fixed right-0 top-0 z-50 h-screen w-full max-w-2xl overflow-y-auto bg-[#f5f9fd] p-8 shadow-[-20px_0_50px_rgba(15,23,42,0.18)]">
        <div className="mb-8 flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="text-2xl font-semibold text-slate-950">{title}</h2>
            <p className="mt-2 truncate text-sm text-slate-600">{file.name}</p>
          </div>
          <button className="rounded-full border border-slate-200 px-3 py-1 text-sm text-slate-500" onClick={onClose} type="button">
            Fermer
          </button>
        </div>

        {replayMode && file.error ? (
          <div className="mb-6 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm leading-6 text-rose-800">
            <p className="font-semibold">Erreur</p>
            <p className="mt-1 whitespace-pre-wrap">{file.error}</p>
          </div>
        ) : null}

        <div className="space-y-6">
          <ReviewSection title="Patient">
            <ReviewField label="Prénom *">
              <input className={reviewInputClassName(Boolean(fieldErrors.patientFirstName), readOnly)} onChange={(event) => setDraft((current) => ({ ...current, patient: { ...current.patient, first_name: emptyToNull(event.target.value) } }))} readOnly={readOnly} value={draft.patient.first_name ?? ""} />
              <ReviewFieldError message={fieldErrors.patientFirstName} />
            </ReviewField>
            <ReviewField label="Nom *">
              <input className={reviewInputClassName(Boolean(fieldErrors.patientLastName), readOnly)} onChange={(event) => setDraft((current) => ({ ...current, patient: { ...current.patient, last_name: emptyToNull(event.target.value) } }))} readOnly={readOnly} value={draft.patient.last_name ?? ""} />
              <ReviewFieldError message={fieldErrors.patientLastName} />
            </ReviewField>
            <ReviewField label="Date de naissance *">
              <input className={reviewInputClassName(Boolean(fieldErrors.patientBirthDate), readOnly)} onChange={(event) => setDraft((current) => ({ ...current, patient: { ...current.patient, birth_date: emptyToNull(event.target.value) } }))} readOnly={readOnly} type="date" value={dateInputValue(draft.patient.birth_date)} />
              <ReviewFieldError message={fieldErrors.patientBirthDate} />
            </ReviewField>
          </ReviewSection>

          <ReviewSection title="Laboratoire">
            <ReviewField label="Nom">
              <input className={reviewInputClassName(false, readOnly)} onChange={(event) => setDraft((current) => ({ ...current, laboratory: { ...current.laboratory, name: emptyToNull(event.target.value) } }))} readOnly={readOnly} value={draft.laboratory.name ?? ""} />
            </ReviewField>
          </ReviewSection>

          <ReviewSection title="Analyse">
            <ReviewField label="Date *">
              <input className={reviewInputClassName(Boolean(fieldErrors.analysisDate), readOnly)} onChange={(event) => setDraft((current) => ({ ...current, analysis: { ...current.analysis, date: emptyToNull(event.target.value) } }))} readOnly={readOnly} type="date" value={dateInputValue(draft.analysis.date)} />
              <ReviewFieldError message={fieldErrors.analysisDate} />
            </ReviewField>
            <ReviewField label="Nom">
              <input className={reviewInputClassName(false, readOnly)} onChange={(event) => setDraft((current) => ({ ...current, analysis: { ...current.analysis, name: emptyToNull(event.target.value) } }))} readOnly={readOnly} value={draft.analysis.name ?? ""} />
            </ReviewField>
            <ReviewField label="Méthode">
              <input className={reviewInputClassName(false, readOnly)} onChange={(event) => setDraft((current) => ({ ...current, analysis: { ...current.analysis, method: emptyToNull(event.target.value) } }))} readOnly={readOnly} value={draft.analysis.method ?? ""} />
            </ReviewField>
          </ReviewSection>

          <ReviewSection title="Résultat">
            <ReviewField label="Cible">
              <input className={reviewInputClassName(false, readOnly)} onChange={(event) => setDraft((current) => ({ ...current, analysis: { ...current.analysis, result: { ...current.analysis.result, target: emptyToNull(event.target.value) } } }))} readOnly={readOnly} value={draft.analysis.result.target ?? ""} />
            </ReviewField>
            <ReviewField label="Valeur *">
              <input className={reviewInputClassName(Boolean(fieldErrors.resultValue), readOnly)} onChange={(event) => setDraft((current) => ({ ...current, analysis: { ...current.analysis, result: { ...current.analysis.result, value: emptyToNull(event.target.value) } } }))} readOnly={readOnly} value={draft.analysis.result.value ?? ""} />
              <ReviewFieldError message={fieldErrors.resultValue} />
            </ReviewField>
            <ReviewField label="Opérateur">
              <select className={reviewInputClassName(false, readOnly)} disabled={readOnly} onChange={(event) => setDraft((current) => ({ ...current, analysis: { ...current.analysis, result: { ...current.analysis.result, operator: event.target.value } } }))} value={draft.analysis.result.operator ?? "="}>
                <option value="=">=</option>
                <option value="<">&lt;</option>
                <option value=">">&gt;</option>
              </select>
            </ReviewField>
            <ReviewField label="Unité">
              <input className={reviewInputClassName(false, readOnly)} onChange={(event) => setDraft((current) => ({ ...current, analysis: { ...current.analysis, result: { ...current.analysis.result, unit: emptyToNull(event.target.value) } } }))} readOnly={readOnly} value={draft.analysis.result.unit ?? ""} />
            </ReviewField>
          </ReviewSection>

          <ReviewSection title="Antériorité">
            <ReviewField label="Date">
              <input className={reviewInputClassName(false, readOnly)} onChange={(event) => setDraft((current) => ({ ...current, analysis: { ...current.analysis, anteriority: { ...emptyAnteriority(), ...(current.analysis.anteriority ?? {}), date: emptyToNull(event.target.value) } } }))} readOnly={readOnly} type="date" value={dateInputValue(draft.analysis.anteriority?.date ?? null)} />
            </ReviewField>
            <ReviewField label="Valeur">
              <input className={reviewInputClassName(false, readOnly)} onChange={(event) => setDraft((current) => ({ ...current, analysis: { ...current.analysis, anteriority: { ...emptyAnteriority(), ...(current.analysis.anteriority ?? {}), value: emptyToNull(event.target.value) } } }))} readOnly={readOnly} value={draft.analysis.anteriority?.value ?? ""} />
            </ReviewField>
            <ReviewField label="Opérateur">
              <input className={reviewInputClassName(false, readOnly)} onChange={(event) => setDraft((current) => ({ ...current, analysis: { ...current.analysis, anteriority: { ...emptyAnteriority(), ...(current.analysis.anteriority ?? {}), operator: emptyToNull(event.target.value) } } }))} readOnly={readOnly} value={draft.analysis.anteriority?.operator ?? ""} />
            </ReviewField>
          </ReviewSection>

        </div>

        {!readOnly ? (
          <>
            {error ? <p className="mt-6 rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</p> : null}
            <div className="mt-8 flex items-center justify-between gap-3 border-t border-slate-200 pt-5">
              {replayMode ? (
                <button className="rounded-md border border-slate-300 bg-transparent px-5 py-2.5 text-sm font-medium text-slate-600" disabled={saving} onClick={onClose} type="button">
                  Annuler
                </button>
              ) : (
                <button className="rounded-md bg-rose-600 px-5 py-2.5 text-sm font-semibold text-white shadow-[0_12px_24px_rgba(225,29,72,0.22)] transition hover:bg-rose-700 focus:outline-none focus:ring-4 focus:ring-rose-200 disabled:cursor-not-allowed disabled:bg-rose-500/70" disabled={saving} onClick={() => void rejectReview()} type="button">
                  Rejeter
                </button>
              )}
              <button className="inline-flex items-center gap-2 rounded-md bg-clinic-600 px-5 py-2.5 text-sm font-semibold text-white shadow-soft-blue transition hover:bg-clinic-700 focus:outline-none focus:ring-4 focus:ring-clinic-200 disabled:cursor-not-allowed disabled:bg-clinic-500/70" disabled={saving} onClick={() => void saveReview()} type="button">
                {saving ? <LoaderCircle aria-hidden="true" className="h-4 w-4 animate-spin" strokeWidth={2.2} /> : null}
                {saving ? savingLabel : primaryLabel}
              </button>
            </div>
          </>
        ) : null}
      </aside>
    </>
  );
}

function ReviewSection({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white/60 p-4">
      <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
    </section>
  );
}

function ReviewField({ children, label }: { children: ReactNode; label: string }) {
  return (
    <label className="block">
      <span className="mb-2 block text-sm font-medium text-slate-700">{label}</span>
      {children}
    </label>
  );
}

function ReviewFieldError({ message }: { message?: string }) {
  return message ? <span className="mt-2 block text-xs font-medium text-rose-600">{message}</span> : null;
}

function reviewInputClassName(hasError = false, readOnly = false) {
  return `w-full rounded-xl border px-4 py-3 text-sm text-slate-700 outline-none ${readOnly ? "border-slate-200 bg-white/50" : "bg-white/80"} ${hasError ? "border-rose-400 focus:border-rose-500" : "border-slate-200 focus:border-clinic-500"}`;
}

function validateReviewDraft(value: ExtractedJsonRecord) {
  const errors: Record<string, string> = {};

  if (!value.patient.first_name) {
    errors.patientFirstName = "Le prénom est obligatoire.";
  }
  if (!value.patient.last_name) {
    errors.patientLastName = "Le nom est obligatoire.";
  }
  if (!value.patient.birth_date) {
    errors.patientBirthDate = "La date de naissance est obligatoire.";
  }
  if (!value.analysis.date) {
    errors.analysisDate = "La date d'analyse est obligatoire.";
  }
  if (!value.analysis.result.value) {
    errors.resultValue = "La valeur du résultat est obligatoire.";
  }

  return errors;
}

function normalizeExtractedJson(value: ExtractedJsonRecord | null): ExtractedJsonRecord {
  const source = value ?? defaultExtractedJson();

  return {
    patient: {
      first_name: stringOrNull(source.patient?.first_name),
      last_name: stringOrNull(source.patient?.last_name),
      birth_date: dateOrNull(source.patient?.birth_date),
    },
    laboratory: {
      name: stringOrNull(source.laboratory?.name),
    },
    analysis: {
      date: dateOrNull(source.analysis?.date),
      name: stringOrNull(source.analysis?.name),
      method: stringOrNull(source.analysis?.method),
      result: {
        target: stringOrNull(source.analysis?.result?.target),
        value: stringOrNull(source.analysis?.result?.value),
        operator: stringOrNull(source.analysis?.result?.operator),
        unit: stringOrNull(source.analysis?.result?.unit),
      },
      anteriority: source.analysis?.anteriority ? {
        date: dateOrNull(source.analysis.anteriority.date),
        value: stringOrNull(source.analysis.anteriority.value),
        operator: stringOrNull(source.analysis.anteriority.operator),
      } : null,
    },
    extraction: {
      confidence: typeof source.extraction?.confidence === "number" ? source.extraction.confidence : null,
      warnings: Array.isArray(source.extraction?.warnings) ? source.extraction.warnings.filter((warning): warning is string => typeof warning === "string") : [],
    },
  };
}

function defaultExtractedJson(): ExtractedJsonRecord {
  return {
    patient: { first_name: null, last_name: null, birth_date: null },
    laboratory: { name: null },
    analysis: {
      date: null,
      name: null,
      method: null,
      result: { target: "HCG", value: null, operator: "=", unit: null },
      anteriority: null,
    },
    extraction: { confidence: null, warnings: [] },
  };
}

function extractedJsonForSave(value: ExtractedJsonRecord): ExtractedJsonRecord {
  const anteriority = value.analysis.anteriority;

  return {
    ...value,
    analysis: {
      ...value.analysis,
      result: {
        ...value.analysis.result,
        operator: value.analysis.result.operator ?? "=",
      },
      anteriority: anteriority && [anteriority.date, anteriority.value, anteriority.operator].some((field) => field !== null && field !== "")
        ? anteriority
        : null,
    },
    extraction: {
      ...value.extraction,
      confidence: 1.0,
    },
  };
}

function emptyAnteriority() {
  return { date: null, value: null, operator: null };
}

function emptyToNull(value: string) {
  const trimmedValue = value.trim();
  return trimmedValue ? trimmedValue : null;
}

function stringOrNull(value: unknown) {
  if (value === null || value === undefined) {
    return null;
  }
  return String(value);
}

function dateOrNull(value: unknown) {
  const stringValue = stringOrNull(value);
  return stringValue ? dateInputValue(stringValue) || stringValue : null;
}

function dateInputValue(value: string | null) {
  if (!value) {
    return "";
  }

  const normalizedValue = value.replace(/\//g, "-");
  return /^\d{4}-\d{2}-\d{2}$/.test(normalizedValue) ? normalizedValue : "";
}

function ConfigurationPage({
  filesDirectoryPath,
  ollamaUrl,
  redcapApiToken,
  redcapApiUrl,
  manualValidationRequired,
  autoCleanup,
  minimumConfidenceScore,
  promptDefault,
  promptRetry,
  setFilesDirectoryPath,
  setOllamaUrl,
  setRedcapApiToken,
  setRedcapApiUrl,
  setManualValidationRequired,
  setAutoCleanup,
  setMinimumConfidenceScore,
  setPromptDefault,
  setPromptRetry,
  onBack,
  saveConfiguration,
}: {
  filesDirectoryPath: string;
  ollamaUrl: string;
  redcapApiToken: string;
  redcapApiUrl: string;
  manualValidationRequired: boolean;
  autoCleanup: boolean;
  minimumConfidenceScore: string;
  promptDefault: string;
  promptRetry: string;
  setFilesDirectoryPath: (value: string) => void;
  setOllamaUrl: (value: string) => void;
  setRedcapApiToken: (value: string) => void;
  setRedcapApiUrl: (value: string) => void;
  setManualValidationRequired: (value: boolean) => void;
  setAutoCleanup: (value: boolean) => void;
  setMinimumConfidenceScore: (value: string) => void;
  setPromptDefault: (value: string) => void;
  setPromptRetry: (value: string) => void;
  onBack: () => void;
  saveConfiguration: (update: ConfigurationUpdate) => Promise<void>;
}) {
  const [activeTab, setActiveTab] = useState<ConfigurationTab>("business");

  function savePrompts() {
    void saveConfiguration({
      promptDefault: promptDefault || null,
      promptRetry: promptRetry || null,
    });
  }

  return (
    <section className="min-h-0 flex-1 overflow-hidden flex flex-col">
      <div className="mb-7 shrink-0">
        <div className="flex items-center gap-3">
          <button
            aria-label="Retour aux fichiers"
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white/60 text-slate-600 transition hover:bg-white hover:text-slate-950 focus:outline-none focus:ring-4 focus:ring-slate-200"
            onClick={onBack}
            type="button"
          >
            <ArrowLeft aria-hidden="true" className="h-5 w-5" strokeWidth={2.2} />
          </button>
          <h2 className="text-2xl font-semibold text-slate-950">Configuration</h2>
        </div>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
          Ajustez les règles métier, les connexions techniques et les prompts utilisés par PAD.
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pb-8 pr-1">
        <div aria-label="Sections de configuration" className="flex flex-wrap border-b border-slate-300" role="tablist">
          {configurationTabs.map((tab) => {
            const selected = activeTab === tab.id;

            return (
              <button
                aria-selected={selected}
                className={`-mb-px border px-5 py-3 text-sm font-semibold transition focus:outline-none focus-visible:ring-4 focus-visible:ring-clinic-200 ${selected ? "border-slate-300 border-b-[#f9fafb] bg-white/70 text-clinic-700" : "border-transparent bg-transparent text-slate-500 hover:border-slate-200 hover:bg-white/40 hover:text-slate-700"}`}
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                role="tab"
                type="button"
              >
                {tab.label}
              </button>
            );
          })}
        </div>

        <div className="rounded-b-md border-x border-b border-slate-300 bg-white/70 shadow-[0_18px_45px_rgba(15,23,42,0.08)]">
          <div className="h-5" aria-hidden="true" />
          <div className="p-5 sm:p-7">
          {activeTab === "technical" ? (
            <div className="space-y-6" role="tabpanel">
              <ConfigField label="Emplacement des fichiers">
                <input
                  className="w-full rounded-xl border border-slate-200 bg-white/70 px-4 py-3 text-sm text-slate-700 outline-none focus:border-clinic-500"
                  onBlur={() => void saveConfiguration({ filesDirectoryPath })}
                  onChange={(event) => setFilesDirectoryPath(event.target.value)}
                  value={filesDirectoryPath}
                />
              </ConfigField>
              <ConfigField label="URL Ollama">
                <input
                  className="w-full rounded-xl border border-slate-200 bg-white/70 px-4 py-3 text-sm text-slate-700 outline-none focus:border-clinic-500"
                  onBlur={() => void saveConfiguration({ ollamaUrl: ollamaUrl || "http://localhost:11434" })}
                  onChange={(event) => setOllamaUrl(event.target.value)}
                  placeholder="http://localhost:11434"
                  type="url"
                  value={ollamaUrl}
                />
              </ConfigField>
              <ConfigField label="URL de l'API Redcap">
                <input
                  className="w-full rounded-xl border border-slate-200 bg-white/70 px-4 py-3 text-sm text-slate-700 outline-none focus:border-clinic-500"
                  onBlur={() => void saveConfiguration({ redcapUrl: redcapApiUrl || null })}
                  onChange={(event) => setRedcapApiUrl(event.target.value)}
                  placeholder="https://..."
                  type="url"
                  value={redcapApiUrl}
                />
              </ConfigField>
              <ConfigField label="Token API">
                <input
                  className="w-full rounded-xl border border-slate-200 bg-white/70 px-4 py-3 text-sm text-slate-700 outline-none focus:border-clinic-500"
                  onBlur={() => void saveConfiguration({ redcapToken: redcapApiToken || null })}
                  onChange={(event) => setRedcapApiToken(event.target.value)}
                  placeholder="Token RedCap"
                  type="password"
                  value={redcapApiToken}
                />
              </ConfigField>
            </div>
          ) : null}

          {activeTab === "business" ? (
            <div className="space-y-4" role="tabpanel">
              <label className="block rounded-xl border border-slate-200 bg-white/50 p-4">
                <span className="flex items-center gap-3 text-sm font-medium text-slate-800">
                  <input
                    checked={manualValidationRequired}
                    className="h-4 w-4 rounded border-slate-300 text-clinic-600 focus:ring-clinic-500"
                    onChange={(event) => {
                      setManualValidationRequired(event.target.checked);
                      void saveConfiguration({ manualMode: event.target.checked });
                    }}
                    type="checkbox"
                  />
                  Validation manuelle requise
                </span>
                <span className="mt-3 block text-sm leading-6 text-slate-500">
                  Cochez cette case si vous voulez valider manuellement toutes les analyses avant de poursuivre le process
                </span>
              </label>
              <label className="block rounded-xl border border-slate-200 bg-white/50 p-4">
                <span className="flex items-center gap-3 text-sm font-medium text-slate-800">
                  <input
                    checked={autoCleanup}
                    className="h-4 w-4 rounded border-slate-300 text-clinic-600 focus:ring-clinic-500"
                    onChange={(event) => {
                      setAutoCleanup(event.target.checked);
                      void saveConfiguration({ autoCleanup: event.target.checked });
                    }}
                    type="checkbox"
                  />
                  Suppression automatique
                </span>
                <span className="mt-3 block text-sm leading-6 text-slate-500">
                  Supprimer automatiquement les fichiers dès que leur traitement est terminé.
                </span>
              </label>
              <label className="block rounded-xl border border-slate-200 bg-white/50 p-4">
                <span className="mb-3 block text-sm font-medium text-slate-800">Note minimum</span>
                <input
                  className="w-full rounded-xl border border-slate-200 bg-white/70 px-4 py-3 text-sm text-slate-700 outline-none focus:border-clinic-500"
                  min="0"
                  max="1"
                  onBlur={() => void saveConfiguration({ minConfidence: Number(minimumConfidenceScore) })}
                  onChange={(event) => setMinimumConfidenceScore(event.target.value)}
                  placeholder="0"
                  step="0.01"
                  type="number"
                  value={minimumConfidenceScore}
                />
                <span className="mt-3 block text-sm leading-6 text-slate-500">
                  Valeur en dessous de laquelle les documents sont considérés comme "à valider"
                </span>
              </label>
            </div>
          ) : null}

          {activeTab === "prompt" ? (
            <div className="space-y-6" role="tabpanel">
              <ConfigField label="Prompt par défaut">
                <textarea
                  className="min-h-56 w-full resize-y rounded-xl border border-slate-200 bg-white/70 px-4 py-3 font-mono text-sm leading-6 text-slate-700 outline-none focus:border-clinic-500"
                  onChange={(event) => setPromptDefault(event.target.value)}
                  placeholder="Prompt principal utilisé pour l'extraction LLM"
                  value={promptDefault}
                />
              </ConfigField>
              <ConfigField label="Prompt de retry">
                <textarea
                  className="min-h-56 w-full resize-y rounded-xl border border-slate-200 bg-white/70 px-4 py-3 font-mono text-sm leading-6 text-slate-700 outline-none focus:border-clinic-500"
                  onChange={(event) => setPromptRetry(event.target.value)}
                  placeholder="Prompt utilisé lors d'une nouvelle tentative"
                  value={promptRetry}
                />
              </ConfigField>
              <div className="flex justify-end">
                <button className="rounded-md bg-clinic-600 px-5 py-2.5 text-sm font-semibold text-white shadow-soft-blue transition hover:bg-clinic-700 focus:outline-none focus:ring-4 focus:ring-clinic-200" onClick={savePrompts} type="button">
                  Enregistrer
                </button>
              </div>
            </div>
          ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}

function ImportConfirmationModal({ onClose, onConfirm, open }: { onClose: () => void; onConfirm: () => Promise<void>; open: boolean }) {
  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/25 px-6">
      <section className="w-full max-w-xl bg-[#f5f9fd] shadow-[0_24px_80px_rgba(15,23,42,0.22)]">
        <div className="p-8">
          <p className="text-base leading-7 text-slate-800">
            L'import peut durer jusqu'à cinq minute par fichier. Durant toute la durée de l'opération, merci de ne pas ajouter/supprimer de fichier dans le dossier
          </p>
        </div>
        <div className="flex justify-end gap-3 border-t border-slate-200 px-8 py-5">
          <button className="rounded-md border border-slate-300 bg-transparent px-5 py-2.5 text-sm font-medium text-slate-600" onClick={onClose} type="button">
            Annuler
          </button>
          <button className="rounded-md bg-clinic-600 px-5 py-2.5 text-sm font-semibold text-white shadow-soft-blue transition hover:bg-clinic-700 focus:outline-none focus:ring-4 focus:ring-clinic-200" onClick={() => void onConfirm()} type="button">
            Commencer
          </button>
        </div>
      </section>
    </div>
  );
}

function ClearConfirmationModal({
  error,
  loading,
  onClose,
  onConfirm,
  open,
}: {
  error: string;
  loading: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void>;
  open: boolean;
}) {
  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/25 px-6">
      <section className="w-full max-w-xl bg-[#f5f9fd] shadow-[0_24px_80px_rgba(15,23,42,0.22)]">
        <div className="p-8">
          <p className="text-base leading-7 text-slate-800">
            Cette action va supprimer tous les fichiers du dossier configuré ainsi que les lignes de suivi associées.
          </p>
          {error ? <p className="mt-4 rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</p> : null}
        </div>
        <div className="flex justify-end gap-3 border-t border-slate-200 px-8 py-5">
          <button className="rounded-md border border-slate-300 bg-transparent px-5 py-2.5 text-sm font-medium text-slate-600" disabled={loading} onClick={onClose} type="button">
            Annuler
          </button>
          <button className="inline-flex items-center gap-2 rounded-md bg-clinic-600 px-5 py-2.5 text-sm font-semibold text-white shadow-soft-blue transition hover:bg-clinic-700 focus:outline-none focus:ring-4 focus:ring-clinic-200 disabled:cursor-not-allowed disabled:bg-clinic-500/70" disabled={loading} onClick={() => void onConfirm()} type="button">
            {loading ? <LoaderCircle aria-hidden="true" className="h-4 w-4 animate-spin" strokeWidth={2.2} /> : null}
            {loading ? "Suppression..." : "confirmer"}
          </button>
        </div>
      </section>
    </div>
  );
}

function ConfigField({ children, label }: { children: ReactNode; label: string }) {
  return (
    <label className="block">
      <span className="mb-2 block text-sm font-medium text-slate-700">{label}</span>
      {children}
    </label>
  );
}

function matchesSearch(file: FileRecord, searchTerm: string) {
  const normalizedSearchTerm = searchTerm.trim().toLowerCase();

  if (!normalizedSearchTerm) {
    return true;
  }

  return [file.name, file.path, file.extension].some((value) => value.toLowerCase().includes(normalizedSearchTerm));
}

function mergeFileSources(rawFiles: FileRecord[], trackedFiles: FileRecord[]) {
  const trackedFilesByName = new Map(trackedFiles.map((file) => [file.name, file]));
  const mergedFiles = rawFiles.map((rawFile) => {
    const trackedFile = trackedFilesByName.get(rawFile.name);

    if (!trackedFile) {
      return rawFile;
    }

    trackedFilesByName.delete(rawFile.name);
    return {
      ...rawFile,
      id: trackedFile.id,
      status: trackedFile.status,
      confidence: trackedFile.confidence,
      error: trackedFile.error,
      extractedJson: trackedFile.extractedJson,
      createdAt: trackedFile.createdAt || rawFile.createdAt,
      updatedAt: newestTimestamp(rawFile.updatedAt, trackedFile.updatedAt),
    };
  });

  return mergedFiles;
}

function newestTimestamp(left: string, right: string) {
  return new Date(left).getTime() > new Date(right).getTime() ? left : right;
}

function matchesAnyStatusFilter(file: FileRecord, selectedFilters: StatusFilter[]) {
  const activeFilters = selectedFilters.filter((filter) => filter !== "all");

  if (activeFilters.length === 0) {
    return true;
  }

  return activeFilters.some((filter) => matchesStatusFilter(file, filter));
}

function getStatusFilters(files: FileRecord[]) {
  return [
    { id: "all" as const, label: "Fichiers", count: files.length },
    { id: "processing" as const, label: "Analysés", count: files.filter((file) => matchesStatusFilter(file, "processing")).length },
    { id: "validation" as const, label: "A valider", count: files.filter((file) => matchesStatusFilter(file, "validation")).length },
    { id: "recording" as const, label: "Enregistrés", count: files.filter((file) => matchesStatusFilter(file, "recording")).length },
    { id: "failures" as const, label: "Échecs", count: files.filter((file) => matchesStatusFilter(file, "failures")).length },
  ];
}

function matchesStatusFilter(file: FileRecord, filter: StatusFilter) {
  const status = file.status.toLowerCase();

  if (filter === "all") {
    return true;
  }
  if (status === "raw") {
    return false;
  }
  if (filter === "processing") {
    return isFileAnalyzedStatus(status);
  }
  if (filter === "validation") {
    return status === "review";
  }
  if (filter === "recording") {
    return ["saved", "success"].includes(status);
  }
  if (filter === "failures") {
    return status === "failed";
  }

  return false;
}

function FilesTable({ files, onFailure, onInspect, onOpen, onReview }: { files: FileRecord[]; onFailure: (file: FileRecord) => void; onInspect: (file: FileRecord) => void; onOpen: (file: FileRecord) => void; onReview: (file: FileRecord) => void }) {
  return (
    <div className="space-y-3">
      {files.map((file) => {
        const statusPills = fileStatusPills(file.status);
        const confidenceLabel = formatConfidence(file.confidence);

        return (
          <article key={file.id} className="flex items-center justify-between gap-4 border-l-[5px] border-l-black bg-[#f7f8fa] p-4 shadow-[4px_4px_12px_rgba(151,165,185,0.14),-4px_-4px_12px_rgba(255,255,255,0.72)] transition-colors hover:bg-white">
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-center gap-2">
                <h3 className="min-w-0 truncate text-base font-semibold text-slate-950">
                  <button className="block max-w-full truncate text-left transition hover:text-clinic-700 hover:underline focus:outline-none focus:ring-2 focus:ring-clinic-300" onClick={() => void onOpen(file)} title="Ouvrir le fichier" type="button">
                    {file.name}
                  </button>
                </h3>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <span>{formatBytes(file.sizeBytes)}</span>
                {confidenceLabel ? <span className="font-bold text-slate-700">{confidenceLabel}</span> : null}
                {statusPills.map((statusPill) => (
                  statusPill.label === "Analysé" ? (
                    <button
                      className={`rounded-full px-2.5 py-1 font-semibold transition hover:ring-2 hover:ring-purple-200 focus:outline-none focus:ring-2 focus:ring-purple-300 ${statusPill.className}`}
                      key={statusPill.label}
                      onClick={() => onInspect(file)}
                      type="button"
                    >
                      {statusPill.label}
                    </button>
                  ) : statusPill.label === "A valider" ? (
                    <button
                      className={`rounded-full px-2.5 py-1 font-semibold transition hover:ring-2 hover:ring-orange-200 focus:outline-none focus:ring-2 focus:ring-orange-300 ${statusPill.className}`}
                      key={statusPill.label}
                      onClick={() => onReview(file)}
                      type="button"
                    >
                      {statusPill.label}
                    </button>
                  ) : statusPill.label === "Echec" ? (
                    <button
                      className={`rounded-full px-2.5 py-1 font-semibold transition hover:ring-2 hover:ring-rose-200 focus:outline-none focus:ring-2 focus:ring-rose-300 ${statusPill.className}`}
                      key={statusPill.label}
                      onClick={() => onFailure(file)}
                      type="button"
                    >
                      {statusPill.label}
                    </button>
                  ) : (
                    <span className={`rounded-full px-2.5 py-1 font-semibold ${statusPill.className}`} key={statusPill.label}>{statusPill.label}</span>
                  )
                ))}
              </div>
            </div>
            {isFileStatusInProgress(file.status) ? (
              <LoaderCircle aria-label="Traitement en cours" className="h-7 w-7 shrink-0 animate-spin text-clinic-600" strokeWidth={2.2} />
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

function isFileStatusInProgress(status: FileStatus) {
  return ["extracting", "processing", "saving", "registering", "transfer", "transferring", "recording"].includes(status.toLowerCase());
}

function fileStatusPills(status: FileStatus) {
  const normalizedStatus = status.toLowerCase();
  const pills: Array<{ label: string; className: string }> = [];

  if (isFileAnalyzedStatus(normalizedStatus)) {
    pills.push({ label: "Analysé", className: "bg-purple-100 text-purple-700" });
  }
  if (normalizedStatus === "review") {
    pills.push({ label: "A valider", className: "bg-orange-100 text-orange-700" });
  }
  if (["saved", "success"].includes(normalizedStatus)) {
    pills.push({ label: "Enregistré", className: "bg-blue-100 text-blue-700" });
  }
  if (normalizedStatus === "failed") {
    pills.push({ label: "Echec", className: "bg-rose-100 text-rose-700" });
  }

  return pills;
}

function isFileAnalyzedStatus(status: string) {
  return ["ready", "review", "failed", "transfer", "saved", "success"].includes(status);
}

function LoadingState() {
  return <StateShell title="Lecture du dossier local" message="Chargement des fichiers depuis ~/Desktop/fichiers_pad..." />;
}

function EmptyState() {
  return <StateShell showDecoration={false} title="Aucun fichier" message="Le dossier local est disponible, mais il ne contient aucun fichier." />;
}

function FilteredEmptyState() {
  return <StateShell showDecoration={false} title="Aucun résultat" message="Aucun fichier ne correspond à ces critères." />;
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-[1.5rem] bg-white/60 p-6 shadow-neo-inset">
      <h3 className="text-lg font-semibold text-rose-700">Impossible de lire les fichiers</h3>
      <p className="mt-2 text-sm text-slate-600">{message}</p>
      <button className="mt-5 rounded-2xl bg-white px-4 py-2 text-sm font-semibold text-clinic-700 shadow-neo transition hover:-translate-y-0.5" onClick={() => void onRetry()} type="button">
        Réessayer
      </button>
    </div>
  );
}

function StateShell({ title, message, showDecoration = true }: { title: string; message: string; showDecoration?: boolean }) {
  return (
    <div className="flex min-h-72 flex-col items-center justify-center rounded-[1.5rem] bg-white/50 p-8 text-center shadow-neo-inset">
      {showDecoration ? <div className="mb-5 h-16 w-16 rounded-full bg-clinic-100 shadow-neo-inset" /> : null}
      <h3 className="text-xl font-semibold text-slate-950">{title}</h3>
      <p className="mt-2 max-w-md text-sm leading-6 text-slate-600">{message}</p>
    </div>
  );
}

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }

  const units = ["B", "KB", "MB", "GB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** exponent).toFixed(exponent === 0 ? 0 : 1)} ${units[exponent]}`;
}

function formatConfidence(confidence: unknown) {
  if (typeof confidence !== "number" || !Number.isFinite(confidence)) {
    return null;
  }

  return confidence.toString();
}

export default App;
