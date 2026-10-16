import { describe, expect, it, vi, afterEach } from "vitest";
import { readFileSync } from "node:fs";
import {
  actionDefinitions,
  api,
  eventExclusionMessage,
  eventStatus,
  bindingMode,
  bindingValue,
  initialAction,
  parseRule,
  previousOutputs,
  contextCompatible,
  yaml,
} from "./model";

describe("Full v1 editor contract", () => {
  it("merges actual nested outputs but never future or mismatched steps", () => {
    const actions=[{type:'jira.issue.get'},{type:'jira.issue.edit'}];
    const suggestions=previousOutputs(actions,1,[
      {path:'_steps["0"].fields.parent.key',step:0,action_type:'jira.issue.get',types:['string'],example:'KAN-10'},
      {path:'_steps["1"].key',step:1,action_type:'jira.issue.edit',types:['string'],example:'KAN-99'},
      {path:'_steps["0"].forged',step:0,action_type:'bitbucket.pipeline',types:['string']},
    ]);
    expect(suggestions.find(s=>s.path==='_steps["0"].fields.parent.key')?.example).toBe('KAN-10');
    expect(suggestions.some(s=>s.path.includes('forged')||s.path.startsWith('_steps["1"]'))).toBe(false);
    expect(suggestions.find(s=>s.path==='_steps["0"].key')?.availability).toBe('declared');
  });
  it("invalidates historical context only when its prior prefix or trigger changes", () => {
    const rule=parseRule(readFileSync('../examples/all-v1-variations.yaml','utf8'));
    const basis={version:1,spec:structuredClone(rule)};
    expect(contextCompatible(rule,basis,1)).toBe(true);
    rule.actions[1]={type:'jira.issue.get',issue:{value:'KAN-4'}};
    expect(contextCompatible(rule,basis,1)).toBe(true);
    rule.actions[0]={type:'jira.issue.get',issue:{value:'KAN-4'}};
    expect(contextCompatible(rule,basis,1)).toBe(false);
    expect(contextCompatible(rule,null,1)).toBe(false);
  });
  it("distinguishes ignored service events from unknown actors and ordinary errors", () => {
    expect(eventExclusionMessage({execution_blocked_reason: "ignored_service_actor"})).toContain("cuenta del servicio");
    expect(eventExclusionMessage({last_error: "held_actor_unknown"})).toContain("Retenido");
    expect(eventExclusionMessage({last_error: "ordinary-error"})).toBeNull();
  });
  it("does not label anti-loop exclusions as processing failures", () => {
    for (const reason of ["ignored_service_actor", "ignored_jira_actor"])
      expect(eventStatus({last_error: reason, processed_at: "2026-10-16T20:07:00Z"})).toBe("ignored");
    expect(eventStatus({last_error: "held_actor_unknown"})).toBe("held");
    expect(eventStatus({execution_blocked_reason: "ignored_service_actor"})).toBe("ignored");
    expect(eventStatus({last_error: "unexpected_failure"})).toBe("failed");
    expect(eventStatus({processed_at: "2026-10-16T20:07:00Z"})).toBe("processed");
    expect(eventStatus({})).toBe("pending");
  });
  it("explains unsafe activation errors without exposing the raw rule", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({detail: "unsafe_write_scope: raw details"}), {status: 422})));
    await expect(api("/v1/rules/demo/activate", "POST", {})).rejects.toThrow("Acotá el proyecto o ticket");
    vi.unstubAllGlobals();
  });
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
