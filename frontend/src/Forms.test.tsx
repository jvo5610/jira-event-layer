import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { ActionEditor, enteredBinding, isIssueTargetSuggestion, SingleValueEditor, filterSuggestions, SuggestionDetails, type Suggestion } from "./Forms";
import { setLanguage } from "./i18n";

afterEach(() => setLanguage("es"));
const render = (overrides: Partial<Suggestion> = {}) => renderToStaticMarkup(
  <SuggestionDetails suggestion={{path: "issue.fields.status.name", types: ["null", "string"], ...overrides}} />,
);
describe("Observed field examples", () => {
  it("suggests issue-key values, not observed dates, status names or structured outputs as edit targets", () => {
    expect(isIssueTargetSuggestion({path:'issue.fields.parent.key',types:['null']})).toBe(true);
    expect(isIssueTargetSuggestion({path:'issue.fields.customfield_10090',types:['string'],example_state:'present',example_kind:'value',example:'OPS-42'})).toBe(true);
    for (const example of ['Done','2026-10-15',false,123])
      expect(isIssueTargetSuggestion({path:'issue.fields.x',types:['string'],example_state:'present',example_kind:'value',example})).toBe(false);
    expect(isIssueTargetSuggestion({path:'_steps["0"].fields',types:[],availability:'declared'})).toBe(false);
    expect(isIssueTargetSuggestion({path:'_steps["0"].key',types:[],availability:'declared'})).toBe(true);
  });
  it("uses one issue input and a data picker instead of requiring a binding mode", () => {
    const html = renderToStaticMarkup(<SingleValueEditor label="Ticket a editar" target value={{path:'issue.fields.parent.key'}} suggestions={[]} onChange={() => {}} />);
    expect(html).toContain('aria-label="Ticket a editar"');
    expect(html).toContain('issue.fields.parent.key');
    expect(html.toLowerCase()).toContain('readonly');
    expect(html).toContain('Elegir dato para Ticket a editar');
    expect(html).toContain('Quitar dato seleccionado');
    expect(html).not.toContain('class="segmented"');
    expect(enteredBinding('OPS-42')).toEqual({value:'OPS-42'});
    expect(enteredBinding('${issue.fields.parent.key}')).toEqual({template:'${issue.fields.parent.key}'});
  });
  it("adds Jira fields through a select and immediately shows their value editor", () => {
    const html = renderToStaticMarkup(<ActionEditor action={{type:'jira.issue.edit',issue:{value:'KAN-45'},fields:{description:{value:'New text'}}}}
      definition={{properties:{issue:{},fields:{}},required:['issue','fields']}} suggestions={[]} onChange={() => {}} />);
    expect(html).toContain('Campos a modificar');
    expect(html).toContain('aria-label="description"');
    expect(html).toContain('Reemplaza el contenido actual');
    expect(html).toContain('aria-label="Agregar campo"');
    expect(html).not.toContain('class="segmented"');
  });
  it("shows actual prior values and distinguishes unobserved output contracts", () => {
    expect(render({origin:'1 · Consultar ticket',example_state:'present',example_kind:'value',example:'KAN-45'})).toContain('Ejemplo: &quot;KAN-45&quot;');
    expect(render({origin:'1 · Consultar ticket',availability:'declared'})).toContain('todavía no hay un valor observado');
  });
  it("preserves raw paths, observed types and JSON example types", () => {
    for (const value of ["En curso", 0, false]) {
      const html = render({example_state: "present", example_kind: "value", example: value});
      const example = value === "En curso" ? "&quot;En curso&quot;" : String(value);
      expect(html).toContain(`Ejemplo: ${example}`);
      expect(html).toContain("null · string");
      expect(html).toContain("issue.fields.status.name");
      expect(html).not.toContain("Texto · puede venir vacío");
    }
  });
  it("distinguishes absent examples from protected data and unavailable samples", () => {
    expect(render({example_state: "empty"})).toContain("Sin datos en la muestra");
    expect(render({example_state: "protected"})).toContain("Valor protegido");
    expect(render()).toContain("Sin ejemplo disponible");
  });
  it("shows bounded structure, truncation and translated copy", () => {
    expect(render({example_state:"present", example_kind:"array", example_count:3})).toContain("Lista con 3 elementos");
    expect(render({example_state:"present", example_kind:"object", example_count:2})).toContain("Objeto con 2 campos");
    setLanguage("en");
    expect(render({example_state:"present", example_kind:"value", example:"Preview", example_truncated:true})).toContain("Example: &quot;Preview&quot;…");
    expect(render({example_state:"empty"})).toContain("No data in the sample");
  });
  it("does not relabel or translate data paths and searches only raw keys", () => {
    const suggestion: Suggestion = {path:"issue.fields.duedate",types:["null","string"],example_state:"present",example_kind:"value",example:"2026-10-23"};
    const spanish = renderToStaticMarkup(<SuggestionDetails suggestion={suggestion} />);
    setLanguage("en");
    const english = renderToStaticMarkup(<SuggestionDetails suggestion={suggestion} />);
    for (const html of [spanish, english]) {
      expect(html).toContain("issue.fields.duedate");
      expect(html).toContain("null · string");
      expect(html).not.toContain("Fecha de vencimiento");
      expect(html).not.toContain("Due date");
      expect(html.match(/issue.fields.duedate/g)).toHaveLength(1);
    }
    expect(filterSuggestions([suggestion], "DUEDATE")).toEqual([suggestion]);
    expect(filterSuggestions([suggestion], "Fecha de vencimiento")).toEqual([]);
    expect(filterSuggestions([suggestion], "Due date")).toEqual([]);
  });
});
