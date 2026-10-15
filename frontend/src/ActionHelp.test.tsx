import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it } from "vitest";
import { ActionHelp, ActionHelpContent } from "./ActionHelp";
import { actionOutputKeys } from "./model";
import { setLanguage } from "./i18n";
afterEach(() => setLanguage("es"));
const render = (types: string[], index: number) => renderToStaticMarkup(
  <ActionHelpContent actions={types.map(type => ({type}))} index={index} />,
);
it("offers accessible contextual help for every supported action", () => {
  for (const type of Object.keys(actionOutputKeys)) {
    const html = renderToStaticMarkup(<ActionHelp actions={[{type}]} index={0} />);
    expect(html).toContain('aria-label="Ayuda de la acción"');
    for (const key of actionOutputKeys[type]) expect(html).toContain(`<code>${key}</code>`);
    expect(html).toContain("todavía no hay resultados anteriores");
  }
});
it("uses only previous actions and distinguishes event data, paths, templates and ADF", () => {
  const html = render(["jira.issue.create", "jira.issue.get", "jira.issue.edit", "jira.issue.get"], 2);
  expect(html).toContain('_steps[&quot;1&quot;].fields.summary');
  expect(html).toContain('Ticket: ${_steps[&quot;1&quot;].key}');
  expect(html).not.toContain('_steps[&quot;3&quot;]');
  expect(html).toContain("evento original");
  expect(html).toContain("ADF");
});
it("supports pipelines without inventing Jira keys and translates help", () => {
  setLanguage("en");
  const html = render(["bitbucket.pipeline", "jira.comment.add"], 1);
  expect(html).toContain("Use a previous result");
  expect(html).toContain('_steps[&quot;0&quot;].state');
  expect(html).not.toContain('_steps[&quot;0&quot;].key');
});
