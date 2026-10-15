import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { filterSuggestions, SuggestionDetails, type Suggestion } from "./Forms";
import { setLanguage } from "./i18n";

afterEach(() => setLanguage("es"));
const render = (overrides: Partial<Suggestion> = {}) => renderToStaticMarkup(
  <SuggestionDetails suggestion={{path: "issue.fields.status.name", types: ["null", "string"], ...overrides}} />,
);
describe("Observed field examples", () => {
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
