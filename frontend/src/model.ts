import { dump, load } from "js-yaml";
import { t } from "./i18n";

export type Obj = Record<string, any>;
export function eventExclusionMessage(event: Obj): string | null {
  const reason = event.execution_blocked_reason ?? event.last_error;
  if (["ignored_service_actor", "ignored_jira_actor"].includes(reason))
    return t("Ignorado: lo generó una cuenta del servicio. No puede disparar flujos ni ejecutarse manualmente.");
  if (reason === "held_actor_unknown")
    return t("Retenido: no se pudo identificar quién generó el evento. No se ejecutará por seguridad.");
  return null;
}
export type Rule = {
  apiVersion: string;
  kind: string;
  name: string;
  description?: string;
  trigger: { source: string; event: string };
  when: Obj;
  actions: Obj[];
};
export const actionLabels: Record<string, string> = {
  "jira.issue.get": "Consultar ticket",
  "jira.issue.create": "Crear ticket",
  "jira.issue.clone": "Clonar ticket",
  "jira.issue.edit": "Editar ticket",
  "jira.issue.transition": "Cambiar estado",
  "jira.comment.add": "Agregar comentario",
  "jira.issue.link": "Vincular tickets",
  "bitbucket.pipeline": "Ejecutar pipeline",
};
export const labels: Record<string, string> = {
  issue: "Ticket",
  source: "Ticket de origen",
  project: "Proyecto",
  issue_type_id: "ID del tipo de ticket",
  parent: "Ticket padre",
  to_status_id: "ID del estado destino",
  from_status_id: "ID del estado de origen",
  text: "Comentario",
  inward: "Ticket de entrada",
  outward: "Ticket de salida",
  link_type_id: "ID del tipo de vínculo",
  workspace: "Workspace",
  repository: "Repositorio",
  branch: "Rama",
  pipeline: "Pipeline personalizado",
  copy_fields: "Campos a copiar",
};
export function newRule(): Rule {
  return {
    apiVersion: "automation/v1",
    kind: "Rule",
    name: "nuevo-flujo",
    description: "",
    trigger: { source: "jira", event: "jira:issue_updated" },
    when: { path: "issue.key", op: "exists" },
    actions: [],
  };
}
export function parseRule(text: string): Rule {
  const value = load(text, { json: false }) as Rule;
  if (
    !value ||
    typeof value !== "object" ||
    value.apiVersion !== "automation/v1" ||
    value.kind !== "Rule" ||
    !value.trigger ||
    !Array.isArray(value.actions) ||
    !value.when
  )
    throw new Error("Se esperaba una regla automation/v1 completa.");
  return value;
}
export const yaml = (value: unknown) =>
  dump(value, { noRefs: true, lineWidth: 120, sortKeys: false });
export function actionDefinitions(schema: Obj): Obj[] {
  return Object.values(schema.$defs ?? {}).filter(
    (d: any) => d?.properties?.type?.const in actionLabels,
  ) as Obj[];
}
export function initialAction(definition: Obj): Obj {
  const action: Obj = { type: definition.properties.type.const };
  for (const [key, field] of Object.entries(definition.properties) as [
    string,
    Obj,
  ][]) {
    if (key === "type") continue;
    if ("default" in field && field.default !== null)
      action[key] = structuredClone(field.default);
    else if (key === "fields" || key === "variables") action[key] = {};
    else if (key === "copy_fields") action[key] = ["summary", "description"];
    else if (definition.required?.includes(key))
      action[key] = field.type === "string" ? "" : { path: "issue.key" };
  }
  return action;
}
export function bindingMode(value: any): string {
  if (typeof value === "string")
    return value.includes("${") ? "template" : "value";
  if (value && typeof value === "object" && !Array.isArray(value)) {
    if ("template" in value) return "template";
    if ("path" in value && value.path !== null) return "path";
  }
  return "value";
}
export function bindingValue(value: any, mode = bindingMode(value)): any {
  if (mode === "template")
    return typeof value === "string" ? value : value.template;
  if (mode === "path") return value.path;
  return value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    "value" in value
    ? value.value
    : value;
}
export function hasStepReference(value: unknown): boolean {
  return JSON.stringify(value).includes("_steps");
}
export function relativeSuggestions<T extends { path: string }>(
  items: T[],
  root: string,
): T[] {
  return items
    .flatMap((item) => {
      if (!item.path.startsWith(root)) return [];
      let suffix = item.path.slice(root.length);
      if (suffix.startsWith(".")) suffix = suffix.slice(1);
      else if (/^\[\d+\]/.test(suffix))
        suffix = suffix.replace(/^\[\d+\]\.?/, "");
      else return [];
      return [{ ...item, path: suffix }];
    })
    .filter(
      (item, index, all) =>
        all.findIndex((other) => other.path === item.path) === index,
    );
}
export const actionOutputKeys: Record<string, string[]> = {
  "jira.issue.create": ["key", "operation_id"],
  "jira.issue.clone": ["key", "operation_id"],
  "jira.issue.get": ["key", "fields", "guard_matched"],
  "jira.issue.edit": ["key", "updated_fields", "no_change"],
  "jira.issue.transition": ["key", "status_id", "no_change"],
  "jira.comment.add": ["key", "comment_id", "operation_id"],
  "jira.issue.link": ["inward", "outward", "link_type_id"],
  "bitbucket.pipeline": ["state", "result"],
};
export function previousOutputs(
  actions: Obj[],
  index: number,
  observed: Obj[] = [],
): { path: string; types: string[]; origin: string; availability: "declared" | "observed"; [key: string]: any }[] {
  const declared = actions.slice(0, index).flatMap((a, i) =>
    (actionOutputKeys[a.type] ?? []).map((key) => ({
      path: `_steps["${i}"].${key}`,
      types: [],
      origin: `${i + 1} · ${t(actionLabels[a.type])}`,
      availability: "declared",
    })),
  );
  const actual = observed.filter(s => Number.isInteger(s.step) && s.step >= 0 && s.step < index
    && actions[s.step]?.type === s.action_type
    && s.path.startsWith(`_steps["${s.step}"].`)).map((s): Obj => ({ ...s,
      availability: "observed", origin: `${s.step + 1} · ${t(actionLabels[s.action_type])}` }));
  const byPath = new Map<string, any>(declared.map(s => [s.path, s]));
  for (const item of actual) byPath.set(item.path, item);
  return [...byPath.values()];
}

export function contextCompatible(rule: Rule, basis: Obj | null, index: number): boolean {
  return Boolean(basis && index >= 0 && rule.name === basis.spec.name
    && JSON.stringify(rule.trigger) === JSON.stringify(basis.spec.trigger)
    && JSON.stringify(rule.when) === JSON.stringify(basis.spec.when)
    && JSON.stringify(rule.actions.slice(0, index)) === JSON.stringify(basis.spec.actions.slice(0, index)));
}
export async function api(
  path: string,
  method = "GET",
  body?: unknown,
  raw = false,
  headers: Record<string, string> = {},
): Promise<any> {
  if (
    path === "/v1/events" &&
    method === "POST" &&
    body &&
    typeof body === "object" &&
    "delivery_id" in body
  ) {
    const { delivery_id, ...payload } = body as Obj;
    body = payload;
    headers = { ...headers, "Idempotency-Key": String(delivery_id) };
  }
  const response = await fetch("/api" + path, {
    method,
    headers: {
      ...(body === undefined
        ? {}
        : { "Content-Type": raw ? "application/yaml" : "application/json" }),
      ...headers,
    },
    body:
      body === undefined
        ? undefined
        : raw
          ? (body as string)
          : JSON.stringify(body),
    signal: AbortSignal.timeout(20000),
  });
  const text = await response.text();
  let result: any;
  try {
    result = JSON.parse(text);
  } catch {
    result = text;
  }
  if (!response.ok) {
    const detail = typeof result?.detail === "string" ? result.detail : JSON.stringify(result?.detail ?? result);
    const code = detail.split(":")[0];
    const safetyMessages: Record<string, string> = {
      unsafe_write_scope: "Acotá el proyecto o ticket con eq/in en todas las ramas del filtro antes de activar.",
      unsafe_change_scope: "En issue_updated, filtrá el campo cambiado y su valor anterior o nuevo dentro del mismo some.where.",
      unsafe_transition: "Cada cambio de estado necesita IDs de origen y destino distintos.",
      unsafe_action_target: "Usá una clave Jira o una sola referencia que contenga esa clave. No se permiten pasos futuros, templates parciales ni fallbacks.",
    };
    throw new Error(safetyMessages[code] ? `${t(safetyMessages[code])} [${code}]` : detail);
  }
  return result;
}
