import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { ActionLibrary } from "./ActionLibrary";
import { actionLabels } from "./model";
import { setLanguage } from "./i18n";

const definitions = Object.keys(actionLabels).reverse().map((type) => ({
  properties: { type: { const: type } },
}));
const render = (defs = definitions) => renderToStaticMarkup(
  <ActionLibrary definitions={defs} onAdd={() => {}} />,
);
afterEach(() => setLanguage("es"));

describe("Action library", () => {
  it("groups all schema actions by provider in a stable, human order", () => {
    const html = render();
    expect(html.match(/class="library-module"/g)).toHaveLength(8);
    expect(html).toContain('aria-label="Jira"');
    expect(html).toContain('aria-label="Bitbucket"');
    expect(html.indexOf("jira.issue.get")).toBeLessThan(html.indexOf("jira.issue.create"));
    expect(html.indexOf("jira.issue.link")).toBeLessThan(html.indexOf("bitbucket.pipeline"));
  });
  it("separates the readable action title from its technical identifier in both languages", () => {
    expect(render()).toContain("<strong>Clonar ticket</strong><small>jira.issue.clone</small>");
    setLanguage("en");
    const html = render();
    expect(html).toContain("<strong>Clone issue</strong><small>jira.issue.clone</small>");
    expect(html).toContain('aria-label="Add: Clone issue"');
  });
  it("does not invent actions or empty provider groups", () => {
    const html = render(definitions.filter((def) => def.properties.type.const === "bitbucket.pipeline"));
    expect(html.match(/class="library-module"/g)).toHaveLength(1);
    expect(html).not.toContain('aria-label="Jira"');
  });
});
