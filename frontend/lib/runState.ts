// Turns a run's trace events into what the page shows: the steps, the live status line, the streamed draft,
// token and cost counters, the elapsed time and each agent's context window. Pure functions, no React, so the
// same events (live or replayed) always give the same view.
import type { ContextMessage, Pending, RunStatus, TraceEvent } from "./types";

export interface Usage {
  calls: number;
  input: number;
  output: number;
  cost: number;
}

export interface AgentContext {
  node: string;
  model: string;
  effort: string;
  inputTokens: number;
  window: number;
  breakdown: Record<string, number>;
  messages: ContextMessage[];
  calls: number; // LLM calls this agent made so far
}

export interface Draft {
  id: string;
  content: string;
}

export interface RunView {
  events: TraceEvent[];
  lastSeq: number;
  statusLine: string; // the latest one-line status
  statusNode: string; // the step that status belongs to
  drafts: Draft[]; // every report draft written, in order, each at its latest snapshot
  usage: Usage; // the whole run
  usageBefore: Usage; // earlier parts of a paused and resumed run (each part counts from zero)
  contexts: Record<string, AgentContext>; // the latest context window per agent
  end: TraceEvent | null; // the latest end event
  workedSeconds: number; // time spent working in finished parts, excluding pauses for the user
  partStart: number | null; // when the current working part began (epoch seconds); null while not working
}

const NO_USAGE: Usage = { calls: 0, input: 0, output: 0, cost: 0 };

export function emptyRun(): RunView {
  return {
    events: [], lastSeq: 0, statusLine: "", statusNode: "", drafts: [], usage: NO_USAGE, usageBefore: NO_USAGE,
    contexts: {}, end: null, workedSeconds: 0, partStart: null,
  };
}

function add(a: Usage, b: Usage): Usage {
  return { calls: a.calls + b.calls, input: a.input + b.input, output: a.output + b.output, cost: a.cost + b.cost };
}

export function applyEvent(view: RunView, e: TraceEvent): RunView {
  if (e.seq <= view.lastSeq) return view; // a reconnect or replay can repeat events
  const next: RunView = { ...view, events: [...view.events, e], lastSeq: e.seq };
  if (next.partStart === null && e.kind !== "end") next.partStart = e.ts;

  switch (e.kind) {
    case "status":
      next.statusLine = e.content;
      next.statusNode = e.node;
      break;
    case "draft": {
      const id = e.draft_id ?? "draft";
      const draft = { id, content: e.content };
      const known = next.drafts.some((d) => d.id === id);
      next.drafts = known ? next.drafts.map((d) => (d.id === id ? draft : d)) : [...next.drafts, draft];
      break;
    }
    case "usage":
      next.usage = add(next.usageBefore, {
        calls: e.llm_calls ?? 0, input: e.input_total ?? 0, output: e.output ?? 0, cost: e.cost_usd ?? 0,
      });
      break;
    case "context": {
      const previous = next.contexts[e.node];
      next.contexts = {
        ...next.contexts,
        [e.node]: {
          node: e.node, model: e.model ?? "", effort: e.effort ?? "", inputTokens: e.input_tokens ?? 0,
          window: e.window ?? 0, breakdown: e.breakdown ?? {}, messages: e.messages ?? [],
          calls: (previous?.calls ?? 0) + 1,
        },
      };
      break;
    }
    case "end":
      next.end = e;
      next.usageBefore = next.usage;
      if (next.partStart !== null) next.workedSeconds += Math.max(0, e.ts - next.partStart);
      next.partStart = null;
      break;
  }
  return next;
}

export function applyEvents(view: RunView, events: TraceEvent[]): RunView {
  return events.reduce(applyEvent, view);
}

/** The run's status as the events tell it; `known` is what the API last said (used before any end event). */
export function runStatus(view: RunView, known: RunStatus): RunStatus {
  const last = view.events[view.events.length - 1];
  if (view.end && last === view.end) return view.end.status ?? known;
  if (view.end) return "running"; // events after an end: the run was resumed
  return known;
}

/** What the run is waiting for the user to answer, if it is paused (and not resumed since). */
export function pendingOf(view: RunView): Pending | null {
  return runStatus(view, "running") === "awaiting_input" ? view.end?.pending ?? null : null;
}

/** Seconds spent working, excluding time the run waited for the user. */
export function elapsedSeconds(view: RunView, nowSeconds: number): number {
  const current = view.partStart === null ? 0 : Math.max(0, nowSeconds - view.partStart);
  return view.workedSeconds + current;
}

const STEP_KINDS = new Set(["action", "error", "reasoning", "observation", "thinking", "reply"]);

/** The events listed as steps at a verbosity (1 brief, 2 standard, 3 full). Status lines, counters, context
 * windows and drafts are shown elsewhere. */
export function visibleSteps(view: RunView, verbosity: number): TraceEvent[] {
  return view.events.filter((e) => STEP_KINDS.has(e.kind) && e.level <= verbosity);
}

/** The label next to a streamed draft while the run works on it. */
export function draftStage(view: RunView): string {
  if (view.statusNode === "validate") return "Checking numbers…";
  if (view.statusNode === "reviewer") return "Reviewing…";
  if (view.statusNode === "report_writer") return view.drafts.length > 1 ? "Revising…" : "Writing…";
  return "Checking numbers…";
}

/** The label on a finished report: was it accepted first time, revised, or only partly accepted? */
export function checkLabel(view: RunView, outcome: string | null): string | null {
  if (outcome === "answered") return view.drafts.length > 1 ? "Revised after check" : "✓ Checked";
  if (outcome === "partial") return "Partial: see the reviewer note";
  if (outcome === "fallback") return "Automated summary";
  return null;
}
