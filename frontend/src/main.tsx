import { t, useLanguage, locale, setLanguage } from "./i18n";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  Bell,
  Braces,
  Check,
  ChevronRight,
  CircleHelp,
  Clock3,
  Code2,
  Download,
  FileUp,
  GitBranch,
  Layers3,
  LayoutGrid,
  ListFilter,
  LoaderCircle,
  Plus,
  Radio,
  Save,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Upload,
  Workflow,
  X,
} from "lucide-react";
import {
  ActionEditor,
  FilterEditor,
  JsonInput,
  type Suggestion,
} from "./Forms";
import {
  Obj,
  Rule,
  actionDefinitions,
  actionLabels,
  api,
  eventExclusionMessage,
  hasStepReference,
  initialAction,
  newRule,
  parseRule,
  previousOutputs,
  yaml,
} from "./model";
import "./style.css";
import { ActionLibrary } from "./ActionLibrary";
import { Access } from "./Access";
import { ActionHelp } from "./ActionHelp";

const Canvas = lazy(() => import("./Canvas"));
const FieldCache = new Map<
  string,
  { at: number; fields: Suggestion[]; count: number }
>();
type Page = "rules" | "editor" | "events" | "runs" | "audit" | "settings" | "access";
const whenDate = (s: string) =>
  new Date(s).toLocaleString(locale(), {
    dateStyle: "short",
    timeStyle: "short",
  });
const short = (s: string) => s?.slice(0, 8) ?? "—";
function Status({ value }: { value: string }) {
  return <span className={"status " + value}>{t(value)}</span>;
}
function App() {
  const language = useLanguage();
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);
  const [connection, setConnection] = useState<{
    backend_url: string;
    auth_mode: "basic" | "local";
  } | null>(null);
  const [connectionError, setConnectionError] = useState(false);
  useEffect(() => {
    fetch("/runtime-config", { signal: AbortSignal.timeout(10000) })
      .then((r) => {
        if (!r.ok) throw new Error("Runtime configuration unavailable");
        return r.json();
      })
      .then((value) => {
        if (
          typeof value.backend_url !== "string" ||
          !["basic", "local"].includes(value.auth_mode)
        )
          throw new Error("Invalid runtime configuration");
        setConnection(value);
      })
      .catch(() => setConnectionError(true));
  }, []);
  const [page, setPage] = useState<Page>("rules");
  const [catalog, setCatalog] = useState<Obj | null>(null);
  const [rows, setRows] = useState<Obj[]>([]);
  const [rule, setRule] = useState<Rule>(newRule);
  const ruleRef = useRef(rule);
  ruleRef.current = rule;
  const [selected, setSelected] = useState("trigger");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(
    null,
  );
  const [detail, setDetail] = useState<Obj | null>(null);
  const [detailTitle, setDetailTitle] = useState("");
  const [fields, setFields] = useState<Suggestion[]>([]);
  const [sampleCount, setSampleCount] = useState(0);
  const [positions, setPositions] = useState<
    Record<string, { x: number; y: number }>
  >({});
  const [dirty, setDirty] = useState(false);
  const [versions, setVersions] = useState<Obj[]>([]);
  const [revision, setRevision] = useState<Obj | null>(null);
  const [activeVersion, setActiveVersion] = useState<number | null>(null);
  const [yamlOpen, setYamlOpen] = useState(false);
  const [yamlText, setYamlText] = useState("");
  const [tab, setTab] = useState("config");
  const [samples, setSamples] = useState<Obj[]>([]);
  const [eventIds, setEventIds] = useState<string[]>([]);
  const [evaluation, setEvaluation] = useState<Obj | null>(null);
  const [compareVersion, setCompareVersion] = useState("");
  const [search, setSearch] = useState("");
  const [more, setMore] = useState(false);
  const [eventsFilter, setEventsFilter] = useState("");
  const [importEvent, setImportEvent] = useState(false);
  const [eventPayload, setEventPayload] = useState<any>({});
  const [eventSource, setEventSource] = useState("jira");
  const [eventType, setEventType] = useState("jira:issue_updated");
  const [delivery, setDelivery] = useState("");
  const inputFile = useRef<HTMLInputElement>(null);
  const requestGeneration = useRef(0);
  const tell = (text: string, error = false) => setNotice({ text, error });
  const task = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      if (document.querySelector(".invalid"))
        throw new Error(
          t("Corregí los valores JSON inválidos antes de continuar."),
        );
      await fn();
    } catch (e) {
      setNotice({
        text: t(e instanceof Error ? e.message : String(e)),
        error: true,
      });
    } finally {
      setBusy(false);
    }
  }, []);
  useEffect(() => {
    void task(async () => {
      setCatalog(await api("/v1/catalog"));
    });
  }, [task]);
  const refresh = useCallback(
    async (current: Page, before?: string) => {
      if (current === "editor" || current === "access") return;
      const gen = ++requestGeneration.current;
      const endpoint =
        current === "settings"
          ? "/v1/catalog"
          : current === "audit"
            ? "/v1/audit"
            : `/v1/${current}`;
      const params = new URLSearchParams();
      if (before) params.set("before", before);
      if (current === "events" && eventsFilter)
        params.set("event_type", eventsFilter);
      const result = await api(endpoint + (params.size ? "?" + params : ""));
      if (gen !== requestGeneration.current) return;
      if (current === "settings") setCatalog(result);
      else {
        setRows((old) => (before ? [...old, ...result] : result));
        setMore(["events", "runs"].includes(current) && result.length === 50);
      }
    },
    [eventsFilter],
  );
  useEffect(() => {
    setRows([]);
    setSearch("");
    if (page !== "editor") void task(() => refresh(page));
  }, [page, refresh, task]);
  // Refresh only the visible executions screen, and never start concurrent polls.
  useEffect(() => {
    if (page !== "runs") return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible" && !busy && rows.length <= 50)
        void task(() => refresh("runs"));
    }, 10000);
    return () => clearInterval(timer);
  }, [page, busy, rows.length, refresh, task]);
  useEffect(() => {
    const stop = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", stop);
    return () => window.removeEventListener("beforeunload", stop);
  }, [dirty]);
  const navigate = (next: Page) => {
    if (
      dirty &&
      !window.confirm(t("Hay cambios sin guardar. ¿Querés salir del editor?"))
    )
      return;
    setDirty(false);
    setPage(next);
    setDetail(null);
  };
  const update = (next: Rule) => {
    setRule(next);
    setDirty(true);
    setRevision(null);
    setEvaluation(null);
  };
  const changeAction = (index: number, next: Obj) =>
    update({
      ...rule,
      actions: rule.actions.map((a, i) => (i === index ? next : a)),
    });
  const definitions = useMemo(
    () => actionDefinitions(catalog?.schema ?? {}),
    [catalog],
  );
  const actionIndex =
    selected.startsWith("a") && selected !== "add"
      ? Number(selected.slice(1))
      : -1;
  const action = rule.actions[actionIndex];
  const definition = definitions.find(
    (d) => d.properties.type.const === action?.type,
  );
  useEffect(() => {
    if (page !== "editor") return;
    let canceled = false;
    const key = JSON.stringify(rule.trigger);
    const cached = FieldCache.get(key);
    setFields([]);
    setSampleCount(0);
    setSamples([]);
    setEventIds([]);
    const load = async () => {
      let data = cached;
      if (!data || Date.now() - data.at > 60000) {
        const result = await api(
          "/v1/catalog/fields?" +
            new URLSearchParams({
              source: rule.trigger.source,
              event_type: rule.trigger.event,
            }),
        );
        data = {
          at: Date.now(),
          fields: result.fields,
          count: result.sample_count,
        };
        if (FieldCache.size > 30) FieldCache.clear();
        FieldCache.set(key, data);
      }
      if (!canceled) {
        setFields(data.fields);
        setSampleCount(data.count);
      }
      const examples = await api(
        "/v1/events?" +
          new URLSearchParams({
            source: rule.trigger.source,
            event_type: rule.trigger.event,
            limit: "20",
          }),
      );
      if (!canceled) setSamples(examples);
    };
    const timer = setTimeout(
      () =>
        void load().catch((e) => {
          if (!canceled) tell(e.message, true);
        }),
      300,
    );
    return () => {
      canceled = true;
      clearTimeout(timer);
    };
  }, [page, rule.trigger.source, rule.trigger.event]);
  const suggestions = useMemo(
    () => [...fields, ...previousOutputs(rule.actions, actionIndex)],
    [fields, rule.actions, actionIndex],
  );
  const loadRule = async (name: string, version?: number) => {
    const history = await api(`/v1/rules/${encodeURIComponent(name)}/versions`);
    const row = await api(
      `/v1/rules/${encodeURIComponent(name)}/versions/${version ?? history[0].version}`,
    );
    setRule(row.spec);
    setVersions(history);
    setRevision(row);
    setDirty(false);
    setSelected("trigger");
    setTab("config");
    setEvaluation(null);
    setPage("editor");
    setActiveVersion(rows.find((r) => r.name === name)?.active_version ?? null);
    try {
      setPositions(
        JSON.parse(localStorage.getItem("studio-layout:" + name) ?? "{}"),
      );
    } catch {
      setPositions({});
    }
  };
  const start = () => {
    if (dirty && !window.confirm(t("¿Descartar los cambios sin guardar?")))
      return;
    setRule(newRule());
    setVersions([]);
    setRevision(null);
    setActiveVersion(null);
    setPositions({});
    setDirty(false);
    setSelected("add");
    setEvaluation(null);
    setTab("config");
    setPage("editor");
  };
  const save = async () => {
    const result = await api("/v1/rules", "POST", yaml(rule), true);
    const changedWhileSaving =
      JSON.stringify(ruleRef.current) !== JSON.stringify(rule);
    setRevision(changedWhileSaving ? null : { ...result, spec: rule });
    setDirty(changedWhileSaving);
    setVersions(
      await api(`/v1/rules/${encodeURIComponent(rule.name)}/versions`),
    );
    tell(
      changedWhileSaving
        ? t(
            "Versión {version} guardada; hay cambios posteriores sin guardar.",
            { version: result.version },
          )
        : result.unchanged
          ? t("La definición ya estaba guardada.")
          : t("Versión {version} guardada. No se activó automáticamente.", {
              version: result.version,
            }),
    );
  };
  const activate = async () => {
    if (!revision || dirty)
      throw new Error(t("Guardá y validá la versión antes de activarla."));
    if (
      !window.confirm(
        t(
          "¿Activar {name}, versión {version}? Los próximos eventos coincidentes ejecutarán sus acciones.",
          { name: rule.name, version: revision.version },
        ),
      )
    )
      return;
    await api(`/v1/rules/${encodeURIComponent(rule.name)}/activate`, "POST", {
      version: revision.version,
      sha256: revision.sha256,
    });
    setActiveVersion(revision.version);
    tell(t("Versión activada."));
  };
  const disable = async (name = rule.name) => {
    if (
      !window.confirm(
        t(
          "Desactivar impide nuevas ejecuciones y marca como fallidas las pendientes. ¿Continuar?",
        ),
      )
    )
      return;
    await api(`/v1/rules/${encodeURIComponent(name)}/disable`, "POST", {});
    setActiveVersion(null);
    if (page === "rules") await refresh(page);
    tell(t("Regla desactivada."));
  };
  const inspect = async (kind: string, id: string) => {
    setDetail(await api(`/v1/${kind}/${id}`));
    setDetailTitle(
      kind === "events"
        ? t("Detalle del evento")
        : t("Detalle de la ejecución"),
    );
  };
  const evaluate = async (compare = false) => {
    if (!eventIds.length)
      throw new Error(t("Seleccioná al menos un evento guardado."));
    if (compare) {
      if (!compareVersion)
        throw new Error(t("Elegí una versión para comparar."));
      const other = await api(
        `/v1/rules/${encodeURIComponent(rule.name)}/versions/${compareVersion}`,
      );
      setEvaluation(
        await api("/v1/compare", "POST", {
          left_yaml: other.yaml,
          right_yaml: yaml(rule),
          event_ids: eventIds,
        }),
      );
    } else
      setEvaluation(
        await api("/v1/evaluate", "POST", {
          yaml: yaml(rule),
          event_ids: eventIds,
        }),
      );
  };
  const exportYaml = () => {
    const url = URL.createObjectURL(
      new Blob([yaml(rule)], { type: "application/yaml" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = rule.name + ".yaml";
    a.click();
    URL.revokeObjectURL(url);
  };
  const addAction = (def: Obj) => {
    if (rule.actions.length >= 10) return;
    update({ ...rule, actions: [...rule.actions, initialAction(def)] });
    setSelected("a" + rule.actions.length);
  };
  const removeAction = () => {
    if (
      hasStepReference(rule.actions) &&
      !window.confirm(
        t(
          "Hay referencias _steps por índice. Quitar este paso cambia los índices siguientes; deberás revisar esas referencias. ¿Continuar?",
        ),
      )
    )
      return;
    update({
      ...rule,
      actions: rule.actions.filter((_, i) => i !== actionIndex),
    });
    setSelected("add");
    setPositions({});
  };
  const shiftAction = (direction: number) => {
    const next = actionIndex + direction;
    if (next < 0 || next >= rule.actions.length) return;
    if (
      hasStepReference(rule.actions) &&
      !window.confirm(
        t(
          "Reordenar cambia los índices _steps. Revisá las referencias antes de activar. ¿Continuar?",
        ),
      )
    )
      return;
    const actions = [...rule.actions];
    [actions[next], actions[actionIndex]] = [
      actions[actionIndex],
      actions[next],
    ];
    update({ ...rule, actions });
    setSelected("a" + next);
    setPositions({});
  };
  const position = useCallback(
    (id: string, p: { x: number; y: number }) =>
      setPositions((old) => {
        const next = { ...old, [id]: p };
        try {
          localStorage.setItem(
            "studio-layout:" + rule.name,
            JSON.stringify(next),
          );
        } catch {
          /* Layout persistence is optional; never lose an edited rule. */
        }
        return next;
      }),
    [rule.name],
  );
  const visibleRows = rows.filter((row) =>
    JSON.stringify(row).toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button
          className="brand"
          onClick={() => navigate("rules")}
          aria-label={t("jira-relay-studio inicio")}
        >
          <span className="brand-symbol">
            <Workflow size={25} />
          </span>
          <span>
            jira-relay-studio
          </span>
        </button>
        <nav>
          {(
            [
              ["rules", Workflow, t("Escenarios")],
              ["events", Radio, t("Eventos")],
              ["runs", Activity, t("Ejecuciones")],
              ["audit", ShieldCheck, t("Auditoría")],
            ] as const
          ).map(([key, Icon, title]) => (
            <button
              key={key}
              className={
                page === key || (key === "rules" && page === "editor")
                  ? "active"
                  : ""
              }
              onClick={() => navigate(key)}
            >
              <Icon size={19} />
              {t(title)}
              {key === "rules" && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <button aria-label={t("Instalación")} onClick={() => navigate("settings")}>
            <Settings2 size={18} /> {t("Instalación") + " "}
          </button>
          <button onClick={() => navigate("access")}><ShieldCheck size={18} /> {t("Accesos")}</button>
          <div className="connection-info">
            {t("Backend")}
            <small>{connection?.backend_url ?? t("Sin verificar")}</small>
          </div>
          <div className="profile">
            <span className="avatar small">
              <ShieldCheck size={15} />
            </span>
            <div>
              {t("Acceso a la consola")}
              <small>
                {connection?.auth_mode === "basic"
                  ? t("Autenticado")
                  : connection?.auth_mode === "local"
                    ? t("Acceso local")
                    : t("Sin verificar")}
              </small>
            </div>
          </div>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="breadcrumbs">
            {t("Automatizaciones") + " "}
            <ChevronRight size={13} />{" "}
            <span>
              {page === "editor"
                ? t("Escenarios")
                : {
                    rules: t("Escenarios"),
                    events: t("Eventos"),
                    runs: t("Ejecuciones"),
                    audit: t("Auditoría"),
                    settings: t("Instalación"),
                    access: t("Accesos"),
                  }[page]}
            </span>
            {page === "editor" && (
              <>
                <ChevronRight size={13} />
                <strong>{rule.name}</strong>
              </>
            )}
          </div>
          <div className="topbar-right">
            <button
              className="icon-button"
              title={t("Contrato y ayuda")}
              onClick={() => navigate("settings")}
            >
              <CircleHelp size={18} />
            </button>
          </div>
        </header>
        {connectionError && (
          <div role="alert" className="notice error-notice">
            {t("No se pudo cargar la configuración de conexión.")}
          </div>
        )}
        {notice && (
          <div
            role="alert"
            className={"notice " + (notice.error ? "error-notice" : "")}
          >
            <span>
              {notice.error ? "⚠" : "✓"} {notice.text}
            </span>
            <button
              aria-label={t("Cerrar aviso")}
              onClick={() => setNotice(null)}
            >
              <X size={16} />
            </button>
          </div>
        )}
        {busy && <div className="busy-line" />}
        {page === "editor" ? (
          <>
            <div className="editor-toolbar">
              <div className="editor-heading">
                <button
                  className="icon-button"
                  title={t("Volver")}
                  onClick={() => navigate("rules")}
                >
                  <ArrowLeft size={19} />
                </button>
                <div>
                  <h2>{rule.name}</h2>
                  <span>
                    {dirty
                      ? t("Cambios sin guardar")
                      : revision
                        ? t("Versión") + " " + revision.version
                        : t("Nuevo escenario")}{" "}
                    ·{" "}
                    {activeVersion
                      ? t("Activa v") + activeVersion
                      : t("Inactivo")}
                  </span>
                </div>
              </div>
              <div className="toolbar-actions">
                <button
                  onClick={() => {
                    setYamlText(yaml(rule));
                    setYamlOpen(true);
                  }}
                >
                  <Code2 size={15} /> {t("YAML") + " "}
                </button>
                <button onClick={exportYaml} title={t("Exportar YAML")}>
                  <Download size={15} />
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void task(async () => {
                      const result = await api(
                        "/v1/rules/validate",
                        "POST",
                        yaml(rule),
                        true,
                      );
                      tell(t("Regla válida · ") + result.sha256.slice(0, 12));
                    })
                  }
                >
                  <Check size={15} /> {t("Validar") + " "}
                </button>
                <button disabled={busy} onClick={() => void task(save)}>
                  <Save size={15} /> {t("Guardar") + " "}
                </button>
                <button
                  className="primary"
                  disabled={busy || dirty || !revision}
                  onClick={() => void task(activate)}
                >
                  {t("Activar") + " "}
                  <ArrowRight size={15} />
                </button>
              </div>
            </div>
            <div className="editor-body">
              <div className="canvas-area">
                <div className="canvas-caption">
                  <span className="chip">
                    <GitBranch size={13} /> {t("Flujo secuencial") + " "}
                  </span>
                  <span>
                    {rule.actions.length} {t("/ 10 acciones")}
                  </span>
                </div>
                <Suspense
                  fallback={
                    <div className="loading">
                      <LoaderCircle className="spin" />{" "}
                      {t("Cargando editor…") + " "}
                    </div>
                  }
                >
                  <Canvas
                    rule={rule}
                    selected={selected}
                    onSelect={(id) => {
                      setSelected(id);
                      setTab("config");
                    }}
                    positions={positions}
                    onPosition={position}
                  />
                </Suspense>
                <div className="canvas-footer">
                  <div>
                    <span className="tiny-circle" /> {t("Escenario")}{" "}
                    {activeVersion ? "activo" : "inactivo"}
                  </div>
                  <span>
                    {t(
                      "Elegí un módulo para configurarlo · Arrastrá para organizar",
                    ) + " "}
                  </span>
                  <button
                    onClick={() => {
                      setTab("test");
                      setSelected("filter");
                    }}
                  >
                    <Search size={14} /> {t("Probar condiciones") + " "}
                  </button>
                </div>
              </div>
              <aside className="inspector">
                <div className="inspector-title">
                  <div className="eyebrow">{t("CONFIGURACIÓN DEL MÓDULO")}</div>
                  <h3>
                    {selected === "trigger"
                      ? t("Disparador")
                      : selected === "filter"
                        ? t("Condiciones")
                        : selected === "add"
                          ? t("Agregar una acción")
                          : (t(actionLabels[action?.type]) ?? t("Acción"))}
                  </h3>
                </div>
                <div className="inspector-tabs">
                  <button
                    className={tab === "config" ? "current" : ""}
                    onClick={() => setTab("config")}
                  >
                    {t("Configuración") + " "}
                  </button>
                  <button
                    className={tab === "test" ? "current" : ""}
                    onClick={() => setTab("test")}
                  >
                    {t("Prueba e historial") + " "}
                  </button>
                </div>
                <div className="inspector-content">
                  {tab === "test" ? (
                    <>
                      <div className="info-box">
                        <Search size={18} />
                        <div>
                          <strong>
                            {t("Evaluación sin efectos externos")}
                          </strong>
                          <p>
                            {t(
                              "Comprueba disparador y condiciones sobre eventos guardados. No ejecuta ni simula acciones, bindings o permisos del proveedor.",
                            ) + " "}
                          </p>
                        </div>
                      </div>
                      <h4>{t("Elegí eventos reales recibidos")}</h4>
                      {samples.length ? (
                        samples.map((s) => (
                          <label className="sample-row" key={s.id}>
                            <input
                              type="checkbox"
                              checked={eventIds.includes(s.id)}
                              onChange={(e) =>
                                setEventIds(
                                  e.target.checked
                                    ? [...eventIds, s.id]
                                    : eventIds.filter((id) => id !== s.id),
                                )
                              }
                            />
                            <span>
                              {s.event_type}
                              <small>
                                {short(s.id)} · {whenDate(s.received_at)}
                              </small>
                            </span>
                            <button
                              className="icon-button"
                              title={t("Inspeccionar evento")}
                              onClick={() =>
                                void task(() => inspect("events", s.id))
                              }
                            >
                              <Search size={14} />
                            </button>
                          </label>
                        ))
                      ) : (
                        <p className="empty-inline">
                          {t(
                            "Todavía no hay eventos de este disparador. Ingresalos desde Eventos o conectá el origen real.",
                          ) + " "}
                        </p>
                      )}
                      <button
                        className="primary full"
                        disabled={busy || !eventIds.length}
                        onClick={() => void task(() => evaluate())}
                      >
                        {t("Evaluar condiciones") + " "}
                      </button>
                      {versions.length > 0 && (
                        <>
                          <label className="field">
                            {t("Comparar con versión guardada") + " "}
                            <select
                              value={compareVersion}
                              onChange={(e) =>
                                setCompareVersion(e.target.value)
                              }
                            >
                              <option value="">
                                {t("Seleccionar versión")}
                              </option>
                              {versions.map((v) => (
                                <option key={v.version} value={v.version}>
                                  {t("Versión") + " "}
                                  {v.version}
                                </option>
                              ))}
                            </select>
                          </label>
                          <button
                            disabled={
                              busy || !eventIds.length || !compareVersion
                            }
                            onClick={() => void task(() => evaluate(true))}
                          >
                            {t("Comparar coincidencias") + " "}
                          </button>
                        </>
                      )}
                      {evaluation && (
                        <>
                          <h4>{t("Resultado · sin ejecución")}</h4>
                          <pre>{JSON.stringify(evaluation, null, 2)}</pre>
                        </>
                      )}
                      {versions.length > 0 && (
                        <section>
                          <h4>{t("Versiones")}</h4>
                          {versions.map((v) => (
                            <button
                              className="version-row"
                              key={v.version}
                              onClick={() => {
                                if (
                                  !dirty ||
                                  window.confirm(
                                    t(
                                      "¿Descartar cambios y abrir esta versión?",
                                    ),
                                  )
                                )
                                  void task(() =>
                                    loadRule(rule.name, v.version),
                                  );
                              }}
                            >
                              <span>
                                v{v.version}{" "}
                                {activeVersion === v.version && t("· activa")}
                              </span>
                              <small>{whenDate(v.created_at)}</small>
                            </button>
                          ))}
                        </section>
                      )}
                    </>
                  ) : selected === "trigger" ? (
                    <>
                      <label className="field">
                        {t("Nombre estable del escenario") + " "}
                        <input
                          value={rule.name}
                          onChange={(e) => {
                            setVersions([]);
                            setActiveVersion(null);
                            update({ ...rule, name: e.target.value });
                          }}
                        />
                        <small className="muted">
                          {t(
                            "Cambiar el nombre crea otra regla; no renombra la existente.",
                          ) + " "}
                        </small>
                      </label>
                      <label className="field">
                        {t("Descripción") + " "}
                        <textarea
                          rows={3}
                          value={rule.description ?? ""}
                          onChange={(e) =>
                            update({ ...rule, description: e.target.value })
                          }
                        />
                      </label>
                      <label className="field">
                        {t("Origen") + " "}
                        <select
                          value={rule.trigger.source}
                          onChange={(e) =>
                            update({
                              ...rule,
                              trigger: {
                                ...rule.trigger,
                                source: e.target.value,
                              },
                            })
                          }
                        >
                          <option value="jira">Jira</option>
                          <option value="test">
                            {t("Test (origen soportado por el backend)") + " "}
                          </option>
                        </select>
                      </label>
                      <label className="field">
                        {t("Evento") + " "}
                        <input
                          list="event-types"
                          value={rule.trigger.event}
                          onChange={(e) =>
                            update({
                              ...rule,
                              trigger: {
                                ...rule.trigger,
                                event: e.target.value,
                              },
                            })
                          }
                        />
                        <datalist id="event-types">
                          {[
                            ...new Set([
                              "jira:issue_created",
                              "jira:issue_updated",
                              "jira:issue_deleted",
                              ...(catalog?.events ?? [])
                                .filter(
                                  (e: Obj) => e.source === rule.trigger.source,
                                )
                                .map((e: Obj) => e.event_type),
                            ]),
                          ].map((s) => (
                            <option key={s} value={s} />
                          ))}
                        </datalist>
                        <small className="muted">
                          {t(
                            "Podés escribir cualquier evento soportado por tu origen, incluso antes de recibirlo.",
                          ) + " "}
                        </small>
                      </label>
                      <div className="info-box">
                        <Braces size={20} />
                        <div>
                          <strong>
                            {fields.length} {t("campos observados")}
                          </strong>
                          <p>
                            {t("Sobre") + " "}
                            {sampleCount}{" "}
                            {t(
                              "eventos recientes, máximo 100. Los campos faltantes no se inventan.",
                            ) + " "}
                          </p>
                        </div>
                      </div>
                      {activeVersion && (
                        <button
                          className="danger"
                          disabled={busy}
                          onClick={() => void task(() => disable())}
                        >
                          {t("Desactivar escenario") + " "}
                        </button>
                      )}
                    </>
                  ) : selected === "filter" ? (
                    <>
                      <p className="muted">
                        {t(
                          "El evento avanza solo cuando se cumple este filtro. Las rutas se evalúan sobre el payload original.",
                        ) + " "}
                      </p>
                      <p className="muted">{t("Para escribir: acotá el proyecto o ticket con eq/in en cada rama. En issue_updated, indicá el campo cambiado y su valor anterior o nuevo dentro del mismo some.where.")}</p>
                      <FilterEditor
                        value={rule.when}
                        onChange={(when) => update({ ...rule, when })}
                        suggestions={fields}
                      />
                    </>
                  ) : selected === "add" ? (
                    <>
                      <p className="muted">
                        {t(
                          "Todos los módulos ejecutables del contrato automation/v1. Sin routers o conexiones que el motor no soporte.",
                        ) + " "}
                      </p>
                      <ActionLibrary definitions={definitions} onAdd={addAction} />
                    </>
                  ) : action && definition ? (
                    <>
                      <div className="module-context">
                        <span className="chip">
                          {t("Paso") + " "}
                          {actionIndex + 1}
                        </span>
                        <code>_steps["{actionIndex}"]</code>
                      </div>
                      <div className="connector-warning">
                        {catalog?.connectors[
                          action.type.startsWith("bitbucket")
                            ? "bitbucket"
                            : "jira"
                        ]?.configured
                          ? t(
                              "Conector configurado; permisos y destinos se verifican al ejecutar.",
                            )
                          : t(
                              "Sin credenciales del proveedor. Podés editar; las acciones externas no podrán completarse.",
                            )}
                      </div>
                      <ActionHelp actions={rule.actions} index={actionIndex} />
                      <ActionEditor
                        action={action}
                        definition={definition}
                        onChange={(next) => changeAction(actionIndex, next)}
                        suggestions={suggestions}
                      />
                      <div className="action-controls">
                        <button
                          disabled={actionIndex === 0}
                          onClick={() => shiftAction(-1)}
                        >
                          {t("← Antes") + " "}
                        </button>
                        <button
                          disabled={actionIndex === rule.actions.length - 1}
                          onClick={() => shiftAction(1)}
                        >
                          {t("Después →") + " "}
                        </button>
                        <button
                          className="danger icon-button"
                          title={t("Quitar acción")}
                          onClick={removeAction}
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    </>
                  ) : (
                    <p>{t("Seleccioná un módulo del lienzo.")}</p>
                  )}
                </div>
              </aside>
            </div>
          </>
        ) : (
          <div className="page-content">
            <div className="page-heading">
              <div>
                <div className="eyebrow">{t("AUTOMATION WORKSPACE")}</div>
                <h1>
                  {
                    {
                      rules: t("Tus escenarios"),
                      events: t("Explorador de eventos"),
                      runs: t("Ejecuciones"),
                      audit: t("Registro de auditoría"),
                      settings: t("Tu instalación"),
                      access: t("Administración de accesos"),
                    }[page]
                  }
                </h1>
                <p>
                  {
                    {
                      rules: t(
                        "Conectá eventos, aplicá condiciones y automatizá el siguiente paso.",
                      ),
                      events: t(
                        "Los datos que recibe el motor. La fuente de tus próximas automatizaciones.",
                      ),
                      runs: t(
                        "Seguí cada paso, inspeccioná resultados y entendé los errores.",
                      ),
                      audit: t(
                        "Cambios de configuración y acciones de operación registradas.",
                      ),
                      settings: t(
                        "Idioma, conexión y conectores.",
                      ),
                      access: t("Credenciales, permisos y rotación."),
                    }[page]
                  }
                </p>
              </div>
              <div className="toolbar-actions">
                {page === "rules" ? (
                  <>
                    <button onClick={() => inputFile.current?.click()}>
                      <Upload size={16} /> {t("Importar YAML") + " "}
                    </button>
                    <button className="primary" onClick={start}>
                      <Plus size={17} /> {t("Crear escenario") + " "}
                    </button>
                  </>
                ) : page === "events" ? (
                  <button
                    className="primary"
                    onClick={() => {
                      setDelivery(crypto.randomUUID());
                      setImportEvent(true);
                    }}
                  >
                    <Plus size={16} /> {t("Ingresar evento") + " "}
                  </button>
                ) : null}
                <button
                  className="icon-button"
                  title={t("Actualizar")}
                  disabled={busy}
                  onClick={() => void task(() => refresh(page))}
                >
                  <Clock3 size={18} />
                </button>
              </div>
            </div>
            {page === "rules" && (
              <div className="overview">
                <div>
                  <span className="stat-icon violet">
                    <Workflow size={20} />
                  </span>
                  <div>
                    <strong>{rows.length}</strong>
                    <span>{t("Escenarios guardados")}</span>
                  </div>
                </div>
                <div>
                  <span className="stat-icon green">
                    <Activity size={20} />
                  </span>
                  <div>
                    <strong>
                      {rows.filter((r) => r.active_version).length}
                    </strong>
                    <span>{t("Escenarios activos")}</span>
                  </div>
                </div>
                <div>
                  <span className="stat-icon blue">
                    <Layers3 size={20} />
                  </span>
                  <div>
                    <strong>{definitions.length}</strong>
                    <span>{t("Tipos de acción disponibles")}</span>
                  </div>
                </div>
              </div>
            )}
            {page === "access" ? <Access /> : page === "settings" ? (
              <div className="settings-grid">
                <section className="card">
                  <h3>{t("Idioma")}</h3>
                  <select
                    aria-label={t("Idioma")}
                    value={language}
                    onChange={(e) => setLanguage(e.target.value as "es" | "en")}
                  >
                    <option value="es">Español</option>
                    <option value="en">English</option>
                  </select>
                </section>
                <section className="card">
                  <span className="stat-icon violet">
                    <ShieldCheck />
                  </span>
                  <h3>{t("Backend")}</h3>
                  <strong>{connection?.backend_url ?? t("Sin verificar")}</strong>
                  <p>
                    {t(
                      "La URL del backend se configura al iniciar el servicio. Guardar una regla no la activa.",
                    ) + " "}
                  </p>
                  <code>STUDIO_BACKEND_URL</code>
                </section>
                {Object.entries(catalog?.connectors ?? {}).map(([key, c]) => (
                  <section className="card" key={key}>
                    <h3>{key === "jira" ? "Jira" : "Bitbucket"}</h3>
                    <Status
                      value={
                        (c as Obj).configured === null
                          ? "unknown"
                          : (c as Obj).configured
                            ? "configured"
                            : "not_configured"
                      }
                    />
                    <dl className="connector-details">
                      {Object.entries(c as Obj)
                        .filter(([name]) => name !== "configured")
                        .map(([name, value]) => (
                          <div key={name}>
                            <dt>
                              {t(
                                (
                                  {
                                    projects: "Proyectos",
                                    fields: "Campos habilitados",
                                    repositories: "Repositorios",
                                  } as Record<string, string>
                                )[name] ?? name,
                              )}
                            </dt>
                            <dd>
                              {Array.isArray(value)
                                ? value.join(", ") || "—"
                                : String(value)}
                            </dd>
                          </div>
                        ))}
                    </dl>
                  </section>
                ))}
                <section className="card">
                  <h3>{t("Herramientas técnicas")}</h3>
                  <button
                    onClick={() => {
                      setDetail(catalog?.schema ?? {});
                      setDetailTitle("JSON Schema real del backend");
                    }}
                  >
                    <Braces size={16} /> {t("Contrato completo") + " "}
                  </button>
                  <button
                    onClick={() =>
                      void task(async () => {
                        setDetail({
                          metrics: await api("/metrics"),
                          readiness: await api("/readyz"),
                        });
                        setDetailTitle("Estado y métricas");
                      })
                    }
                  >
                    <Activity size={16} /> {t("Estado y métricas") + " "}
                  </button>
                  <p>
                    {t(
                      "Las credenciales del backend permanecen en el servidor. Los permisos dependen del token configurado.",
                    ) + " "}
                  </p>
                </section>
              </div>
            ) : (
              <>
                <div className="list-toolbar">
                  <div className="search">
                    <Search size={17} />
                    <input
                      aria-label={t("Buscar en la lista")}
                      placeholder={
                        page === "rules"
                          ? t("Buscar escenarios…")
                          : t("Buscar en esta página…")
                      }
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                  </div>
                  <div className="toolbar-actions">
                    {page === "events" && (
                      <select
                        aria-label={t("Filtrar tipo de evento")}
                        value={eventsFilter}
                        onChange={(e) => setEventsFilter(e.target.value)}
                      >
                        <option value="">{t("Todos los tipos")}</option>
                        {(catalog?.events ?? []).map((e: Obj) => (
                          <option
                            key={e.source + e.event_type}
                            value={e.event_type}
                          >
                            {e.event_type}
                          </option>
                        ))}
                      </select>
                    )}
                    <span className="muted">
                      {visibleRows.length} {t("elementos") + " "}
                    </span>
                  </div>
                </div>
                {page === "rules" ? (
                  <div className="scenario-grid">
                    {visibleRows.map((row) => (
                      <button
                        className="scenario-card"
                        key={row.name}
                        onClick={() => void task(() => loadRule(row.name))}
                      >
                        <div className="scenario-card-top">
                          <span className="app-icon">
                            <Layers3 size={24} />
                          </span>
                          <span
                            className={
                              "pill " + (row.active_version ? "on" : "")
                            }
                          >
                            {row.active_version ? t("Activo") : t("Inactivo")}
                          </span>
                        </div>
                        <h3>{row.name}</h3>
                        <p>
                          {t("Versión") + " "}
                          {row.latest_version}
                          {row.active_version
                            ? " · activa v" + row.active_version
                            : ""}
                        </p>
                        <div className="scenario-card-bottom">
                          <span>
                            <Workflow size={14} /> {t("Abrir escenario") + " "}
                          </span>
                          <ArrowRight size={17} />
                        </div>
                      </button>
                    ))}
                    <button
                      className="scenario-card create-card"
                      onClick={start}
                    >
                      <span className="create-circle">
                        <Plus size={25} />
                      </span>
                      <h3>{t("Un nuevo escenario")}</h3>
                      <p>
                        {t("Empezá con un evento.") + " "}
                        <br />
                        {t("Conectá lo que sigue.") + " "}
                      </p>
                    </button>
                  </div>
                ) : (
                  <div className="table-card">
                    <table>
                      <thead>
                        <tr>
                          {(page === "events"
                            ? [
                                t("Evento"),
                                t("Origen"),
                                t("Recibido"),
                                t("Estado"),
                                "",
                              ]
                            : page === "runs"
                              ? [
                                  "Escenario",
                                  t("Versión / paso"),
                                  t("Estado"),
                                  t("Creado"),
                                  "",
                                ]
                              : [
                                  t("Operación"),
                                  t("Destino"),
                                  t("Fecha"),
                                  t("Detalle"),
                                ]
                          ).map((s, i) => (
                            <th key={i}>{s}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {visibleRows.map((row, i) => (
                          <tr key={row.id ?? i}>
                            {page === "events" ? (
                              <>
                                <td>
                                  <strong>
                                    {row.issue_key
                                      ? row.issue_key +
                                        " · " +
                                        (row.issue_summary ?? "")
                                      : row.event_type}
                                  </strong>
                                  <small>
                                    {row.event_type} ·{" "}
                                    {row.issue_type ?? short(row.id)}
                                  </small>
                                </td>
                                <td>{row.source}</td>
                                <td>{whenDate(row.received_at)}</td>
                                <td>
                                  <Status
                                    value={
                                      row.last_error
                                        ? "failed"
                                        : row.processed_at
                                          ? "processed"
                                          : "pending"
                                    }
                                  />
                                </td>
                                <td>
                                  <button
                                    onClick={() =>
                                      void task(() => inspect("events", row.id))
                                    }
                                  >
                                    {t("Inspeccionar") + " "}
                                    <ChevronRight size={14} />
                                  </button>
                                </td>
                              </>
                            ) : page === "runs" ? (
                              <>
                                <td>
                                  <strong>{row.rule_name}</strong>
                                  <small>{short(row.id)}</small>
                                </td>
                                <td>
                                  v{row.version} {t("· índice") + " "}
                                  {row.step}
                                </td>
                                <td>
                                  <Status value={row.status} />
                                </td>
                                <td>{whenDate(row.created_at)}</td>
                                <td>
                                  <button
                                    onClick={() =>
                                      void task(() => inspect("runs", row.id))
                                    }
                                  >
                                    {t("Ver pasos") + " "}
                                    <ChevronRight size={14} />
                                  </button>
                                </td>
                              </>
                            ) : (
                              <>
                                <td>{row.operation}</td>
                                <td>{row.target}</td>
                                <td>{whenDate(row.created_at)}</td>
                                <td>
                                  <button
                                    onClick={() => {
                                      setDetail(row);
                                      setDetailTitle("Entrada de auditoría");
                                    }}
                                  >
                                    {t("Ver detalle") + " "}
                                  </button>
                                </td>
                              </>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {!visibleRows.length && (
                      <div className="empty-state">
                        <Radio size={32} />
                        <h3>
                          {busy ? "Cargando…" : t("Todavía no hay registros")}
                        </h3>
                        <p>
                          {page === "events"
                            ? t(
                                "Los eventos aparecerán cuando los ingreses a esta instalación.",
                              )
                            : t("Los resultados del motor aparecerán acá.")}
                        </p>
                      </div>
                    )}
                    {more && (
                      <button
                        className="load-more"
                        disabled={busy}
                        onClick={() =>
                          void task(() => refresh(page, rows.at(-1)?.id))
                        }
                      >
                        {t("Cargar 50 anteriores") + " "}
                      </button>
                    )}
                  </div>
                )}
              </>
            )}
            <div className="page-footnote">
              <ShieldCheck size={14} />{" "}
              {t(
                "Definiciones versionadas · Ejecución durable · Sin datos de producción en este entorno",
              ) + " "}
            </div>
          </div>
        )}
      </main>
      <input
        hidden
        ref={inputFile}
        type="file"
        accept=".yaml,.yml"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (!file) return;
          void task(async () => {
            if (file.size > 65536)
              throw new Error(t("El archivo supera 64 KiB."));
            if (
              dirty &&
              !window.confirm(t("¿Reemplazar los cambios del editor?"))
            )
              return;
            const text = await file.text();
            await api("/v1/rules/validate?targets=false", "POST", text, true);
            const parsed = parseRule(text);
            setRule(parsed);
            setRevision(null);
            setVersions([]);
            setActiveVersion(null);
            setPositions({});
            setSelected("trigger");
            setTab("config");
            setDirty(true);
            setPage("editor");
            tell(t("Archivo importado al editor. Aún no se guardó ni activó."));
          });
          e.target.value = "";
        }}
      />
      {yamlOpen && (
        <div className="modal-backdrop">
          <section className="modal wide">
            <div className="modal-title">
              <div>
                <h3>{t("Definición YAML")}</h3>
                <p>
                  {t(
                    "Importá, editá o exportá el contrato completo. No se modifica el motor.",
                  ) + " "}
                </p>
              </div>
              <button
                className="icon-button"
                onClick={() => setYamlOpen(false)}
              >
                <X />
              </button>
            </div>
            <textarea
              className="code yaml-editor"
              aria-label={t("Definición YAML")}
              disabled={busy}
              value={yamlText}
              onChange={(e) => setYamlText(e.target.value)}
            />
            <div className="modal-actions">
              <button onClick={() => setYamlOpen(false)}>
                {t("Cancelar")}
              </button>
              <button
                className="primary"
                disabled={busy}
                onClick={() =>
                  void task(async () => {
                    await api(
                      "/v1/rules/validate?targets=false",
                      "POST",
                      yamlText,
                      true,
                    );
                    const parsed = parseRule(yamlText);
                    if (parsed.name !== rule.name) {
                      setVersions([]);
                      setActiveVersion(null);
                    }
                    update(parsed);
                    setYamlOpen(false);
                    setSelected("trigger");
                    tell(
                      t(
                        "YAML válido aplicado al editor. Guardá para crear una versión.",
                      ),
                    );
                  })
                }
              >
                {t("Validar y aplicar al editor") + " "}
              </button>
            </div>
          </section>
        </div>
      )}
      {detail && (
        <div className="modal-backdrop">
          <section className="modal wide">
            <div className="modal-title">
              <div>
                <h3>{t(detailTitle)}</h3>
                <p>{detail.id ?? t("Información del backend")}</p>
              </div>
              <button
                className="icon-button"
                aria-label={t("Cerrar detalle")}
                onClick={() => setDetail(null)}
              >
                <X />
              </button>
            </div>
            {detailTitle === "Detalle del evento" && (
              <div className="info-box">
                <p>
                  {eventExclusionMessage(detail) ?? (detail.payload_redacted_at
                    ? t(
                        "El payload fue eliminado por retención y no puede ejecutarse.",
                      )
                    : t(
                        "Ejecutar evalúa las reglas activas para este evento; puede escribir en sistemas externos.",
                      ))}
                </p>
                <button
                  disabled={busy || !!detail.payload_redacted_at || !!eventExclusionMessage(detail)}
                  onClick={() =>
                    void task(async () => {
                      if (
                        !window.confirm(
                          t(
                            "¿Ejecutar este evento contra las reglas activas? Puede generar acciones reales.",
                          ),
                        )
                      )
                        return;
                      const result = await api(
                        `/v1/events/${detail.id}/execute`,
                        "POST",
                        {},
                      );
                      tell(JSON.stringify(result));
                      setDetail(await api(`/v1/events/${detail.id}`));
                    })
                  }
                >
                  {t("Ejecutar evento") + " "}
                </button>
              </div>
            )}
            {detailTitle === "Detalle de la ejecución" &&
              detail.status === "needs_review" && (
                <div className="info-box">
                  <div>
                    <strong>{t("Reconciliación de pipeline")}</strong>
                    <p>
                      {t(
                        "Adjuntá el UUID de un pipeline existente. Esto verifica su identidad; no vuelve a lanzarlo.",
                      ) + " "}
                    </p>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void task(async () => {
                          const id = window.prompt(
                            t("UUID del pipeline existente (sin llaves):"),
                          );
                          if (!id) return;
                          await api(`/v1/runs/${detail.id}/reconcile`, "POST", {
                            external_uuid: id.replace(/[{}]/g, ""),
                          });
                          setDetail(await api(`/v1/runs/${detail.id}`));
                          tell(t("Pipeline reconciliado."));
                        })
                      }
                    >
                      {t("Reconciliar pipeline") + " "}
                    </button>
                  </div>
                </div>
              )}
            {detail.log?.length > 0 && (
              <div className="step-timeline">
                {detail.log.map((entry: Obj) => (
                  <div key={entry.id}>
                    <span className="timeline-dot" />
                    <strong>
                      {t("Paso") + " "}
                      {entry.step + 1}
                    </strong>
                    <Status value={entry.status} />
                    <small>{whenDate(entry.created_at)}</small>
                  </div>
                ))}
              </div>
            )}
            <pre className="detail-json">{JSON.stringify(detail, null, 2)}</pre>
          </section>
        </div>
      )}
      {importEvent && (
        <div className="modal-backdrop">
          <section className="modal">
            <div className="modal-title">
              <div>
                <h3>{t("Ingresar un evento")}</h3>
                <p>
                  {t(
                    "Se guarda en el backend configurado y se procesa con sus reglas activas.",
                  ) + " "}
                </p>
              </div>
              <button
                className="icon-button"
                onClick={() => setImportEvent(false)}
              >
                <X />
              </button>
            </div>
            <label className="field">
              {t("Origen") + " "}
              <select
                value={eventSource}
                onChange={(e) => setEventSource(e.target.value)}
              >
                <option value="jira">jira</option>
                <option value="test">test</option>
              </select>
            </label>
            <label className="field">
              {t("Tipo de evento") + " "}
              <input
                value={eventType}
                onChange={(e) => setEventType(e.target.value)}
              />
            </label>
            <label className="field">
              {t("Identificador de entrega (idempotencia)") + " "}
              <input
                value={delivery}
                onChange={(e) => setDelivery(e.target.value)}
              />
            </label>
            <JsonInput
              label={t("Payload original (objeto JSON)")}
              value={eventPayload}
              onChange={setEventPayload}
              rows={12}
            />
            <button
              className="primary full"
              disabled={busy}
              onClick={() =>
                void task(async () => {
                  if (
                    !window.confirm(
                      t(
                        "¿Guardar y procesar este evento? Si coincide con una regla activa se ejecutarán sus acciones.",
                      ),
                    )
                  )
                    return;
                  await api("/v1/events", "POST", {
                    source: eventSource,
                    event_type: eventType,
                    delivery_id: delivery,
                    payload: eventPayload,
                  });
                  FieldCache.clear();
                  setImportEvent(false);
                  setCatalog(await api("/v1/catalog"));
                  await refresh("events");
                  tell(t("Evento recibido por el motor."));
                })
              }
            >
              {t("Guardar y procesar evento") + " "}
            </button>
          </section>
        </div>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
