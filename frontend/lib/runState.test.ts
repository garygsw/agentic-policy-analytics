import { describe, expect, it } from "vitest";

import { formatCost, formatElapsed, formatTokens, percent } from "./format";
import {
  applyEvents, checkLabel, draftStage, elapsedSeconds, emptyRun, pendingOf, rejectedPlanOf, runStatus, visibleSteps,
} from "./runState";
import type { TraceEvent } from "./types";

let seq = 0;
function ev(kind: string, extra: Partial<TraceEvent> = {}): TraceEvent {
  seq += 1;
  return { seq, node: "coordinator", kind, level: 1, content: "", ts: 1000 + seq, ...extra };
}

describe("run view", () => {
  it("ignores events it has already seen, so a reconnect can replay safely", () => {
    seq = 0;
    const events = [ev("status", { content: "Planning" }), ev("action", { content: "extract" })];
    const view = applyEvents(applyEvents(emptyRun(), events), events);
    expect(view.events).toHaveLength(2);
    expect(view.statusLine).toBe("Planning");
  });

  it("adds usage across the parts of a paused and resumed run", () => {
    seq = 0;
    const view = applyEvents(emptyRun(), [
      ev("usage", { llm_calls: 1, input_total: 500, output: 100, cost_usd: 0.01 }),
      ev("usage", { llm_calls: 2, input_total: 900, output: 150, cost_usd: 0.02 }), // running totals
      ev("end", { status: "awaiting_input", pending: { kind: "clarification", question: "Which area?" } }),
      ev("reply", { node: "user", content: "employment" }),
      ev("usage", { llm_calls: 1, input_total: 300, output: 50, cost_usd: 0.005 }), // a new part counts from zero
    ]);
    expect(view.usage).toEqual({ calls: 3, input: 1200, output: 200, cost: 0.025 });
  });

  it("keeps the latest snapshot of each draft and counts revisions", () => {
    seq = 0;
    const view = applyEvents(emptyRun(), [
      ev("draft", { draft_id: "a", content: "# Title" }),
      ev("draft", { draft_id: "a", content: "# Title\n\nSummary" }),
      ev("status", { node: "validate", content: "Checking every number" }),
    ]);
    expect(view.drafts).toEqual([{ id: "a", content: "# Title\n\nSummary" }]);
    expect(draftStage(view)).toBe("Checking numbers…");
    const revised = applyEvents(view, [ev("status", { node: "report_writer" }), ev("draft", { draft_id: "b", content: "# Fixed" })]);
    expect(revised.drafts.map((d) => d.id)).toEqual(["a", "b"]);
    expect(draftStage(revised)).toBe("Revising…");
    expect(checkLabel(revised, "answered")).toBe("Revised after check");
    expect(checkLabel(view, "answered")).toBe("✓ Checked");
    expect(checkLabel(view, "partial")).toMatch(/Partial/); // older runs
    const flagged = applyEvents(view, [ev("end", { status: "completed", outcome: "answered", flags: ["finding 2: 14 does not match"] })]);
    expect(checkLabel(flagged, "answered")).toBe("⚠ 1 flagged");
  });

  it("follows the status through a pause and a resume", () => {
    seq = 0;
    const paused = applyEvents(emptyRun(), [
      ev("status"), ev("end", { status: "awaiting_input", pending: { kind: "clarification", question: "Which area?" } }),
    ]);
    expect(runStatus(paused, "running")).toBe("awaiting_input");
    expect(pendingOf(paused)).toEqual({ kind: "clarification", question: "Which area?" });
    const resumed = applyEvents(paused, [ev("reply", { node: "user", content: "jobs" })]);
    expect(runStatus(resumed, "awaiting_input")).toBe("running");
    expect(pendingOf(resumed)).toBeNull();
    expect(runStatus(emptyRun(), "running")).toBe("running");
  });

  it("keeps a rejected plan to reopen, until the run carries on", () => {
    seq = 0;
    const plan = { kind: "plan_approval" as const, plan: "Load ICT employment", reasoning: "" };
    const paused = applyEvents(emptyRun(), [ev("end", { status: "awaiting_input", pending: plan })]);
    expect(rejectedPlanOf(paused)).toBeNull();
    const rejected = applyEvents(paused, [ev("reply", { node: "user", content: "Plan rejected" }),
      ev("end", { status: "rejected", pending: plan })]);
    expect(runStatus(rejected, "awaiting_input")).toBe("rejected");
    expect(pendingOf(rejected)).toBeNull(); // no longer waiting: the chat bar is free
    expect(rejectedPlanOf(rejected)).toEqual(plan);
    const approved = applyEvents(rejected, [ev("reply", { node: "user", content: "Plan approved" })]);
    expect(rejectedPlanOf(approved)).toBeNull();
    expect(runStatus(approved, "rejected")).toBe("running");
  });

  it("counts working time only, not the time spent waiting for the user", () => {
    seq = 0;
    const view = applyEvents(emptyRun(), [
      ev("status", { ts: 100 }), ev("end", { ts: 130, status: "awaiting_input" }), // 30 s working
      ev("reply", { ts: 400 }), ev("status", { ts: 410 }), // resumed after a 270 s wait
    ]);
    expect(elapsedSeconds(view, 420)).toBe(30 + 20);
  });

  it("lists more steps at higher verbosity, never status lines or counters", () => {
    seq = 0;
    const view = applyEvents(emptyRun(), [
      ev("status"), ev("action"), ev("usage"), ev("reasoning", { level: 2 }), ev("thinking", { level: 3 }),
      ev("context", { level: 3, node: "extraction", input_tokens: 1200, window: 1_000_000 }),
    ]);
    expect(visibleSteps(view, 1).map((e) => e.kind)).toEqual(["action"]);
    expect(visibleSteps(view, 2).map((e) => e.kind)).toEqual(["action", "reasoning"]);
    expect(visibleSteps(view, 3).map((e) => e.kind)).toEqual(["action", "reasoning", "thinking"]);
    expect(view.contexts.extraction).toMatchObject({ inputTokens: 1200, calls: 1 });
  });
});

describe("formatting", () => {
  it("formats counters compactly", () => {
    expect([formatTokens(950), formatTokens(1234), formatTokens(60_509), formatTokens(1_250_000)])
      .toEqual(["950", "1.2k", "61k", "1.25M"]);
    expect([formatCost(0), formatCost(0.00002), formatCost(0.0028), formatCost(0.1336)])
      .toEqual(["$0", "<$0.0001", "$0.0028", "$0.13"]);
    expect([formatElapsed(7), formatElapsed(89), formatElapsed(3723)]).toEqual(["0:07", "1:29", "1:02:03"]);
    expect([percent(504, 1_050_000), percent(60_000, 1_000_000)]).toEqual(["<0.1%", "6.0%"]);
  });
});
