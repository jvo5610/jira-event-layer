import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { SuggestionDetails, type Suggestion } from "./Forms";
import { setLanguage } from "./i18n";

afterEach(() => setLanguage("es"));
const render = (overrides: Partial<Suggestion> = {}) => renderToStaticMarkup(
  <SuggestionDetails suggestion={{path: "issue.fields.status.name", types: ["null", "string"], ...overrides}} />,
);
describe("Observed field examples", () => {
  it("shows populated values, including zero and false, without raw null type jargon", () => {
    for (const value of ["En curso", 0, false]) {
      const html = render({example_state: "present", example_kind: "value", example: value});
      expect(html).toContain(`Ejemplo: ${String(value)}`);
      expect(html).toContain("Texto · puede venir vacío");
      expect(html).not.toContain("null · string");
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
    expect(render({example_state:"present", example_kind:"value", example:"Preview", example_truncated:true})).toContain("Example: Preview…");
    expect(render({example_state:"empty"})).toContain("No data in the sample");
  });
});
