import { afterEach, describe, expect, it } from "vitest";
import { t, setLanguage, locale, messages } from "./i18n";
import { relativeSuggestions } from "./model";
afterEach(() => setLanguage("es"));
describe("Language and field mapping", () => {
  it("switches UI copy, statuses and locale without changing DSL identifiers", () => {
    setLanguage("en");
    expect(t("Escenarios")).toBe("Rules");
    expect(t("succeeded")).toBe("Succeeded");
    expect(locale()).toBe("en-GB");
    expect(t("jira.issue.get")).toBe("jira.issue.get");
    expect(t("issue.fields.status.id")).toBe("issue.fields.status.id");
    setLanguage("es");
    expect(t("Escenarios")).toBe("Reglas");
    expect(t("succeeded")).toBe("Completada");
  });
  it("formats dynamic copy and uses short, accurate text in both languages", () => {
    setLanguage("en");
    expect(
      t("Versión {version} guardada. No se activó automáticamente.", {
        version: 3,
      }),
    ).toContain("Version 3");
    expect(t("Editor de reglas")).toBe("Rule editor");
    expect(Object.keys(messages).length).toBeGreaterThan(150);
  });
  it("offers relative paths for list conditions and current Jira guards", () => {
    const items = [
      { path: "changelog.items[0].field" },
      { path: "changelog.items[1].field" },
      { path: "changelog.items[0].toString" },
      { path: "issue.fields.status.name" },
    ];
    expect(
      relativeSuggestions(items, "changelog.items").map((x) => x.path),
    ).toEqual(["field", "toString"]);
    expect(relativeSuggestions(items, "issue").map((x) => x.path)).toEqual([
      "fields.status.name",
    ]);
  });
});
