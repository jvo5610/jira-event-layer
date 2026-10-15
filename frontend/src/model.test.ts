import { describe, expect, it, vi, afterEach } from "vitest";
import { readFileSync } from "node:fs";
import {
  actionDefinitions,
  api,
  bindingMode,
  bindingValue,
  initialAction,
  parseRule,
  previousOutputs,
  yaml,
} from "./model";

describe("Full v1 editor contract", () => {
  it("round-trips every variation without dropping fields or typed values", () => {
    const rule = parseRule(
      readFileSync("../examples/all-v1-variations.yaml", "utf8"),
    );
    expect(parseRule(yaml(rule))).toEqual(rule);
    expect(new Set(rule.actions.map((a) => a.type)).size).toBe(8);
    expect(rule.actions.length).toBe(10);
  });
  it("uses the actual backend definitions to discover actions", () => {
    const schema = {
      $defs: {
        JiraGet: {
          properties: { type: { const: "jira.issue.get" }, issue: {} },
          required: ["type", "issue"],
        },
        Other: { properties: {} },
      },
    };
    const defs = actionDefinitions(schema);
    expect(defs.length).toBe(1);
    expect(initialAction(defs[0])).toEqual({
      type: "jira.issue.get",
      issue: { path: "issue.key" },
    });
  });
  it("preserves binding semantics and defaults", () => {
    for (const value of [
      "literal",
      "${issue.key}",
      { template: "${issue.key}" },
      { path: "issue.key", default: "fallback" },
      { value: [1, false, null] },
      { value: { id: "3" } },
      { value: null },
    ]) {
      expect(bindingMode(value)).toBeDefined();
      expect(bindingValue(value)).toBeDefined();
    }
    expect(bindingMode({ value: "${not_a_template}" })).toBe("value");
    expect(bindingMode({ path: null, value: "constant" })).toBe("value");
  });
  it("only offers actual previous results available to this action", () => {
    const actions = [
      { type: "jira.issue.create" },
      { type: "jira.issue.get" },
      { type: "bitbucket.pipeline" },
    ];
    expect(previousOutputs(actions, 1).map((x) => x.path)).toEqual([
      '_steps["0"].key',
      '_steps["0"].operation_id',
    ]);
    expect(previousOutputs(actions, 2)).toHaveLength(5);
    expect(previousOutputs(actions, 0)).toEqual([]);
  });
  it("rejects documents that are not complete v1 rules", () => {
    expect(() => parseRule("name: incomplete")).toThrow();
  });
});
afterEach(() => vi.unstubAllGlobals());
it("ingests with the real idempotency header and exact backend body", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("{}", { status: 202 }));
  vi.stubGlobal("fetch", fetch);
  await api("/v1/events", "POST", {
    source: "jira",
    event_type: "jira:issue_updated",
    payload: {},
    delivery_id: "delivery",
  });
  const [, options] = fetch.mock.calls[0];
  expect(options.headers["Idempotency-Key"]).toBe("delivery");
  expect(JSON.parse(options.body)).toEqual({
    source: "jira",
    event_type: "jira:issue_updated",
    payload: {},
  });
});
