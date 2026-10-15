import { t, useLanguage, locale } from "./i18n";
import { useEffect, useState } from "react";
import { Plus, Trash2, Braces, ChevronDown } from "lucide-react";
import {
  Obj,
  bindingMode,
  bindingValue,
  labels,
  relativeSuggestions,
} from "./model";

export type Suggestion = {
  path: string;
  types: string[];
  values?: any[];
  origin?: string;
  example_state?: "present" | "empty" | "protected";
  example?: string | number | boolean;
  example_kind?: "value" | "object" | "array";
  example_count?: number;
  example_truncated?: boolean;
};
export function SuggestionDetails({ suggestion: s }: { suggestion: Suggestion }) {
  let preview = t("Sin ejemplo disponible");
  if (s.example_state === "empty") preview = t("Sin datos en la muestra");
  if (s.example_state === "protected") preview = t("Valor protegido");
  if (s.example_state === "present") {
    const value = s.example_kind === "object"
      ? t("Objeto con {count} campos", { count: s.example_count ?? 0 })
      : s.example_kind === "array"
        ? t("Lista con {count} elementos", { count: s.example_count ?? 0 })
        : JSON.stringify(s.example ?? null) + (s.example_truncated ? "…" : "");
    preview = t("Ejemplo: {value}", { value });
  }
  return (
    <>
      <span className="data-option-path">{s.path}</span>
      <small className="data-option-types">
        {s.origin ?? s.types.join(" · ")}
      </small>
      {!s.origin && <small className="data-option-example">{preview}</small>}
    </>
  );
}
export function filterSuggestions(suggestions: Suggestion[], search: string) {
  return suggestions
    .filter((s) => s.path.toLowerCase().includes(search.toLowerCase()))
    .slice(0, 60);
}
export function JsonInput({
  value,
  onChange,
  label = t("Valor JSON"),
  rows = 4,
}: {
  value: any;
  onChange: (v: any) => void;
  label?: string;
  rows?: number;
}) {
  const canonical = JSON.stringify(value, null, 2) ?? "null";
  const [text, setText] = useState(canonical);
  const [error, setError] = useState("");
  useEffect(() => {
    setText(canonical);
    setError("");
  }, [canonical]);
  return (
    <label className="field">
      {t(label)}
      <textarea
        className={"code " + (error ? "invalid" : "")}
        rows={rows}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          try {
            const v = JSON.parse(e.target.value);
            onChange(v);
            setError("");
          } catch {
            setError(t("JSON incompleto o inválido; aún no se aplicó."));
          }
        }}
      />
      {error && <small className="error">{error}</small>}
    </label>
  );
}

export function PathInput({
  value,
  onChange,
  suggestions,
  label = t("Campo / ruta"),
}: {
  value: string;
  onChange: (s: string) => void;
  suggestions: Suggestion[];
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const matches = filterSuggestions(suggestions, search);
  return (
    <div className="field path-field">
      <label>{t(label)}</label>
      <div className="input-action">
        <input
          aria-label={t(label)}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="issue.fields.status.name"
        />
        <button
          title={t("Elegir dato")}
          aria-label={t("Elegir dato")}
          onClick={() => setOpen(!open)}
        >
          <Braces size={16} />
        </button>
      </div>
      {open && (
        <div className="data-picker">
          <div className="picker-head">
            {t("Datos disponibles")}{" "}
            <button
              aria-label={t("Cerrar selector")}
              onClick={() => setOpen(false)}
            >
              ×
            </button>
          </div>
          <input
            aria-label={t("Buscar campo")}
            placeholder={t("Buscar un campo…")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {matches.map((s) => (
            <button
              className="data-option"
              key={s.path}
              onClick={() => {
                onChange(s.path);
                setOpen(false);
              }}
            >
              <SuggestionDetails suggestion={s} />
            </button>
          ))}
          {!matches.length && (
            <p className="muted">
              {t("Sin muestras para este campo. Podés escribir la ruta.") + " "}
            </p>
          )}
          <small className="muted">
            {t(
              "Campos observados, no un esquema obligatorio. Solo se cargan 60 coincidencias.",
            ) + " "}
          </small>
        </div>
      )}
    </div>
  );
}

export function BindingEditor({
  value,
  onChange,
  suggestions,
  typed = false,
  label,
  optional = false,
  onRemove,
}: {
  value: any;
  onChange: (v: any) => void;
  suggestions: Suggestion[];
  typed?: boolean;
  label: string;
  optional?: boolean;
  onRemove?: () => void;
}) {
  const mode = bindingMode(value);
  const current = bindingValue(value);
  const [picker, setPicker] = useState(false);
  return (
    <div className="binding">
      <div className="field-heading">
        <span>{t(label)}</span>
        {optional && (
          <button
            className="icon-button"
            title={t("Quitar valor opcional")}
            onClick={onRemove}
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>
      <div className="segmented">
        {[
          ["value", t("Valor fijo")],
          ["path", t("Dato")],
          ["template", t("Plantilla")],
        ].map(([key, title]) => (
          <button
            className={mode === key ? "selected" : ""}
            key={key}
            onClick={() =>
              onChange(
                key === "path"
                  ? { path: "issue.key" }
                  : key === "template"
                    ? { template: "${issue.key}" }
                    : { value: typed ? null : "" },
              )
            }
          >
            {t(title)}
          </button>
        ))}
      </div>
      {mode === "path" ? (
        <>
          <PathInput
            label={t("Ruta del dato")}
            value={current ?? ""}
            onChange={(path) => onChange({ ...value, path })}
            suggestions={suggestions}
          />
          {!typed && (
            <>
              <label className="check">
                <input
                  type="checkbox"
                  checked={value.default != null}
                  onChange={(e) => {
                    const next = { ...value };
                    if (e.target.checked) next.default = "";
                    else delete next.default;
                    onChange(next);
                  }}
                />{" "}
                {t("Usar valor si falta el dato") + " "}
              </label>
              {value.default != null && (
                <label className="field">
                  {t("Valor alternativo") + " "}
                  <input
                    value={value.default}
                    onChange={(e) =>
                      onChange({ ...value, default: e.target.value })
                    }
                  />
                </label>
              )}
            </>
          )}
        </>
      ) : mode === "template" ? (
        <>
          <textarea
            aria-label={t(label)}
            rows={3}
            value={current ?? ""}
            onChange={(e) => onChange({ template: e.target.value })}
            placeholder={"Ticket ${issue.key}"}
          />
          <button className="text-button" onClick={() => setPicker(!picker)}>
            <Braces size={14} /> {t("Insertar referencia") + " "}
          </button>
          {picker && (
            <PathInput
              value=""
              onChange={(path) => {
                onChange({ template: (current ?? "") + "${" + path + "}" });
                setPicker(false);
              }}
              suggestions={suggestions}
            />
          )}
          <small className="muted">
            {t("Una referencia completa conserva el tipo JSON. $$")}
            {"{"} {t("escapa una referencia literal.") + " "}
          </small>
        </>
      ) : typed ? (
        <TypedValue value={current} onChange={(v) => onChange({ value: v })} />
      ) : (
        <input
          aria-label={t(label)}
          value={current ?? ""}
          onChange={(e) => onChange({ value: e.target.value })}
        />
      )}
    </div>
  );
}

function TypedValue({
  value,
  onChange,
}: {
  value: any;
  onChange: (v: any) => void;
}) {
  const kind =
    value === null ? "null" : Array.isArray(value) ? "array" : typeof value;
  const defaults: Obj = {
    string: "",
    number: 0,
    boolean: true,
    array: [],
    object: {},
    null: null,
  };
  return (
    <>
      <label className="field">
        {t("Tipo del valor") + " "}
        <select
          aria-label={t("Tipo del valor")}
          value={kind}
          onChange={(e) => onChange(defaults[e.target.value])}
        >
          <option value="string">{t("Texto")}</option>
          <option value="number">{t("Número")}</option>
          <option value="boolean">{t("Booleano")}</option>
          <option value="array">{t("Lista JSON")}</option>
          <option value="object">{t("Objeto JSON")}</option>
          <option value="null">{t("Nulo")}</option>
        </select>
      </label>
      {kind === "string" ? (
        <textarea
          aria-label={t("Texto literal")}
          rows={3}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : kind === "boolean" ? (
        <select
          aria-label={t("Valor booleano")}
          value={String(value)}
          onChange={(e) => onChange(e.target.value === "true")}
        >
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      ) : kind === "null" ? (
        <small className="muted">
          {t('Se enviará null, no el texto "null".')}
        </small>
      ) : (
        <JsonInput label={t("Valor JSON")} value={value} onChange={onChange} />
      )}
    </>
  );
}

const operators: Record<string, string> = {
  eq: "es igual a",
  ne: "es distinto de",
  in: "está en la lista",
  contains: "contiene",
  exists: "existe",
  gt: "mayor que",
  gte: "mayor o igual",
  lt: "menor que",
  lte: "menor o igual",
};
const leaf = () => ({ path: "issue.key", op: "exists" });
export function FilterEditor({
  value,
  onChange,
  suggestions,
  depth = 0,
}: {
  value: Obj;
  onChange: (v: Obj) => void;
  suggestions: Suggestion[];
  depth?: number;
}) {
  const kind =
    "all" in value
      ? "all"
      : "any" in value
        ? "any"
        : "not" in value
          ? "not"
          : "some" in value
            ? "some"
            : "predicate";
  const changeKind = (k: string) =>
    onChange(
      k === "predicate"
        ? leaf()
        : k === "all" || k === "any"
          ? { [k]: [value] }
          : k === "not"
            ? { not: value }
            : { some: { path: "changelog.items", where: leaf() } },
    );
  if (depth > 12)
    return (
      <p className="error">{t("El filtro supera la profundidad permitida.")}</p>
    );
  return (
    <div className="filter-group">
      <select
        aria-label={t("Tipo de filtro")}
        value={kind}
        onChange={(e) => changeKind(e.target.value)}
      >
        <option value="predicate">{t("Comparar un campo")}</option>
        <option value="all">{t("TODAS las condiciones (all)")}</option>
        <option value="any">{t("ALGUNA condición (any)")}</option>
        <option value="not">{t("NO se cumple (not)")}</option>
        <option value="some">{t("ALGÚN elemento de una lista (some)")}</option>
      </select>
      {kind === "all" || kind === "any" ? (
        <>
          {value[kind].map((child: Obj, i: number) => (
            <div className="filter-child" key={i}>
              <FilterEditor
                value={child}
                onChange={(v) =>
                  onChange({
                    [kind]: value[kind].map((c: Obj, j: number) =>
                      j === i ? v : c,
                    ),
                  })
                }
                suggestions={suggestions}
                depth={depth + 1}
              />
              {value[kind].length > 1 && (
                <button
                  className="icon-button"
                  title={t("Quitar condición")}
                  onClick={() =>
                    onChange({
                      [kind]: value[kind].filter(
                        (_: unknown, j: number) => j !== i,
                      ),
                    })
                  }
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          ))}
          <button
            className="text-button"
            disabled={value[kind].length >= 32 || depth >= 12}
            onClick={() => onChange({ [kind]: [...value[kind], leaf()] })}
          >
            <Plus size={14} /> {t("Agregar condición") + " "}
          </button>
        </>
      ) : kind === "not" ? (
        <FilterEditor
          value={value.not}
          onChange={(v) => onChange({ not: v })}
          suggestions={suggestions}
          depth={depth + 1}
        />
      ) : kind === "some" ? (
        <>
          <PathInput
            label={t("Ruta de la lista")}
            value={value.some.path}
            onChange={(path) => onChange({ some: { ...value.some, path } })}
            suggestions={suggestions}
          />
          <small className="muted">
            {t(
              "Las rutas siguientes son relativas a cada elemento, no al evento.",
            ) + " "}
          </small>
          <FilterEditor
            value={value.some.where}
            onChange={(where) => onChange({ some: { ...value.some, where } })}
            suggestions={relativeSuggestions(suggestions, value.some.path)}
            depth={depth + 1}
          />
        </>
      ) : (
        <>
          <PathInput
            value={value.path ?? ""}
            onChange={(path) => onChange({ ...value, path })}
            suggestions={suggestions}
          />
          <select
            aria-label={t("Operador")}
            value={value.op}
            onChange={(e) => {
              const op = e.target.value;
              onChange({
                ...value,
                op,
                value:
                  op === "exists"
                    ? true
                    : op === "in"
                      ? []
                      : ["gt", "gte", "lt", "lte"].includes(op)
                        ? 0
                        : "",
              });
            }}
          >
            {Object.entries(operators).map(([key, title]) => (
              <option key={key} value={key}>
                {t(title)}
              </option>
            ))}
          </select>
          {value.op === "exists" ? (
            <select
              aria-label={t("Existencia")}
              value={String(value.value ?? true)}
              onChange={(e) =>
                onChange({ ...value, value: e.target.value === "true" })
              }
            >
              <option value="true">{t("Debe existir")}</option>
              <option value="false">{t("No debe existir")}</option>
            </select>
          ) : (
            <>
              <JsonInput
                label={t("Valor de comparación (JSON)")}
                rows={2}
                value={value.value}
                onChange={(v) => onChange({ ...value, value: v })}
              />
              {suggestions
                .find((s) => s.path === value.path)
                ?.values?.map((v) => (
                  <button
                    className="chip"
                    key={JSON.stringify(v)}
                    onClick={() =>
                      onChange({ ...value, value: value.op === "in" ? [v] : v })
                    }
                  >
                    {String(v)}
                  </button>
                ))}
            </>
          )}
        </>
      )}
    </div>
  );
}

function BindingMap({
  value,
  onChange,
  suggestions,
  typed,
  title,
}: {
  value: Obj;
  onChange: (v: Obj) => void;
  suggestions: Suggestion[];
  typed: boolean;
  title: string;
}) {
  const [key, setKey] = useState("");
  return (
    <section className="map-editor">
      <h4>{t(title)}</h4>
      {Object.entries(value ?? {}).map(([name, binding]) => (
        <BindingEditor
          key={name}
          label={name}
          value={binding}
          typed={typed}
          suggestions={suggestions}
          onChange={(v) => onChange({ ...value, [name]: v })}
          optional
          onRemove={() => {
            const next = { ...value };
            delete next[name];
            onChange(next);
          }}
        />
      ))}
      <div className="input-action">
        <input
          aria-label={t("Nuevo ") + title}
          placeholder={
            typed ? "summary / customfield_10090" : "NOMBRE_VARIABLE"
          }
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
        <button
          aria-label={t("Agregar ") + title}
          disabled={
            !key ||
            Object.hasOwn(value ?? {}, key) ||
            Object.keys(value ?? {}).length >= (typed ? 30 : 50)
          }
          onClick={() => {
            onChange({ ...value, [key]: { value: "" } });
            setKey("");
          }}
        >
          <Plus size={17} />
        </button>
      </div>
      {typed && (
        <small className="muted">
          {t(
            "summary, description, labels, priority, assignee, duedate o customfield_N. Los campos deben estar habilitados en la instalación.",
          ) + " "}
        </small>
      )}
    </section>
  );
}

export function ActionEditor({
  action,
  definition,
  onChange,
  suggestions,
}: {
  action: Obj;
  definition: Obj;
  onChange: (v: Obj) => void;
  suggestions: Suggestion[];
}) {
  const set = (key: string, value: any) =>
    onChange({ ...action, [key]: value });
  return (
    <>
      {Object.entries(definition.properties)
        .sort(
          ([a], [b]) =>
            Number(
              ["fields", "variables", "require", "copy_fields"].includes(a),
            ) -
            Number(
              ["fields", "variables", "require", "copy_fields"].includes(b),
            ),
        )
        .map(([key, property]) => {
          const p = property as Obj;
          if (key === "type") return null;
          if (key === "fields" || key === "variables")
            return (
              <BindingMap
                key={key}
                title={
                  key === "fields"
                    ? t("Campos Jira")
                    : t("Variables del pipeline")
                }
                value={action[key] ?? {}}
                onChange={(v) => set(key, v)}
                suggestions={suggestions}
                typed={key === "fields"}
              />
            );
          if (key === "require")
            return (
              <section key={key}>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={action.require != null}
                    onChange={(e) => {
                      if (e.target.checked)
                        set("require", {
                          path: "fields.status.id",
                          op: "exists",
                        });
                      else {
                        const next = { ...action };
                        delete next.require;
                        onChange(next);
                      }
                    }}
                  />{" "}
                  {t("Verificar el ticket consultado") + " "}
                </label>
                {action.require && (
                  <>
                    <small className="muted">
                      {t(
                        "Este filtro se aplica a la respuesta actual de Jira, no al evento original.",
                      ) + " "}
                    </small>
                    <FilterEditor
                      value={action.require}
                      onChange={(v) => set("require", v)}
                      suggestions={relativeSuggestions(suggestions, "issue")}
                    />
                  </>
                )}
              </section>
            );
          if (key === "copy_fields")
            return (
              <JsonInput
                key={key}
                label={t(labels[key])}
                value={action[key] ?? ["summary", "description"]}
                onChange={(v) => set(key, v)}
              />
            );
          const optional = !definition.required?.includes(key);
          const stringType =
            p.type === "string" ||
            (p.anyOf?.some((v: Obj) => v.type === "string" && !v.$ref) &&
              ["from_status_id"].includes(key));
          if (stringType)
            return (
              <label className="field" key={key}>
                {t(labels[key]) ?? key}
                {!optional && " *"}
                <input
                  value={action[key] ?? ""}
                  onChange={(e) => {
                    if (optional && !e.target.value) {
                      const next = { ...action };
                      delete next[key];
                      onChange(next);
                    } else set(key, e.target.value);
                  }}
                />
                {p.pattern && (
                  <small className="muted">
                    {key.includes("_id")
                      ? t("Identificador numérico de Jira")
                      : p.pattern}
                  </small>
                )}
              </label>
            );
          if (optional && action[key] == null)
            return (
              <button
                className="text-button"
                key={key}
                onClick={() => set(key, { path: "issue.key" })}
              >
                <Plus size={14} /> {t(labels[key]) ?? key}{" "}
                {t("(opcional)") + " "}
              </button>
            );
          return (
            <BindingEditor
              key={key}
              label={(t(labels[key]) ?? key) + (!optional ? " *" : "")}
              value={action[key]}
              onChange={(v) => set(key, v)}
              suggestions={suggestions}
              optional={optional}
              onRemove={() => {
                const next = { ...action };
                delete next[key];
                onChange(next);
              }}
            />
          );
        })}
      <details>
        <summary>
          <Braces size={14} /> {t("Configuración JSON completa")}{" "}
          <ChevronDown size={12} />
        </summary>
        <JsonInput
          label={t("Acción completa (edición avanzada)")}
          value={action}
          onChange={onChange}
          rows={12}
        />
      </details>
    </>
  );
}
