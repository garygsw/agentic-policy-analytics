// Shapes returned by the API (backend/app/api). Only the fields the UI uses are listed.

/** One trace event. `level` 1 is brief, 2 standard, 3 full (see backend/app/graph/events.py). */
export interface TraceEvent {
  seq: number;
  node: string;
  kind: string;
  level: number;
  content: string;
  ts: number;
  detail?: string;
  // usage events: running totals for the current part of the run
  llm_calls?: number;
  input_total?: number;
  output?: number;
  cost_usd?: number;
  // context events: what filled an agent's context window for one call
  model?: string;
  effort?: string;
  input_tokens?: number;
  window?: number;
  breakdown?: Record<string, number>;
  messages?: ContextMessage[];
  // draft events: the report written so far (a snapshot)
  draft_id?: string;
  // end events
  status?: RunStatus;
  outcome?: string;
  pending?: Pending;
}

export interface ContextMessage {
  category: string;
  name: string | null;
  tokens: number;
  content: string;
  truncated?: number; // characters left out of `content`
}

export type RunStatus = "running" | "awaiting_input" | "completed" | "failed" | "cancelled";

export type Pending =
  | { kind: "clarification"; question: string }
  | { kind: "plan_approval"; plan: string; reasoning: string };

export interface ModelInfo {
  id: string;
  provider: string;
  fallback: string;
  context_window: number;
}

export interface ToolInfo {
  name: string;
  description: string;
  required: boolean;
}

export interface RunOptions {
  model: string | null;
  reasoning_effort: "low" | "medium" | "high" | null;
  tools: string[] | null; // null: every tool
  web_scope: "gov" | "any";
  triage: "local" | "jev" | "llm";
}

export interface OptionsResponse {
  models: ModelInfo[];
  tools: ToolInfo[];
  efforts: string[];
  triage: string[];
  web_scopes: string[];
  verbosity: { level: number; name: string; shows: string }[];
  defaults: RunOptions;
}

export interface ThreadSummary {
  id: string;
  title: string;
  updated_at: string;
  runs: number;
  last_status: RunStatus | null;
}

export interface RunSummary {
  id: string;
  question: string;
  status: RunStatus;
  outcome: string | null;
  pending: Pending | null;
  started_at: string;
}

export interface ThreadDetail {
  id: string;
  title: string;
  runs: RunSummary[];
}

export interface LLMCall {
  id: number;
  agent: string | null;
  provider: string;
  model: string;
  effort: string | null;
  ok: boolean;
  error: string | null;
  latency_ms: number;
  input_uncached: number;
  cache_read: number;
  cache_write: number;
  output: number;
  cost_usd: number | null;
  tool_calls: string[];
}

export interface RunDetail extends RunSummary {
  answer: string | null;
  has_report: boolean;
  error: string | null;
  llm_calls: LLMCall[];
}
