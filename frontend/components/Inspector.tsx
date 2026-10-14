"use client";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { formatCost, formatTokens, percent } from "@/lib/format";
import type { AgentContext } from "@/lib/runState";
import type { LLMCall } from "@/lib/types";
import type { RunEntry } from "@/lib/useConversation";

import { nodeLabel } from "./labels";

// What can fill a context window, in the order shown (see backend/app/llm/ledger.py).
const CATEGORIES = ["system", "user", "assistant", "tool_results", "retrieved", "tool_schemas"];
const CATEGORY_LABELS: Record<string, string> = {
  system: "System prompt", user: "Task and question", assistant: "Agent's own messages",
  tool_results: "Tool results", retrieved: "Retrieved passages", tool_schemas: "Tool definitions",
};

/** A side panel showing each agent's context window and the run's LLM call log. */
export function Inspector({ run, onClose }: { run: RunEntry; onClose: () => void }) {
  const [tab, setTab] = useState<"context" | "calls">("context");
  return (
    <aside className="inspector" aria-label="Run inspector">
      <header className="inspector-head">
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "context"} onClick={() => setTab("context")}>Context windows</button>
          <button role="tab" aria-selected={tab === "calls"} onClick={() => setTab("calls")}>LLM calls</button>
        </div>
        <button className="icon" onClick={onClose} aria-label="Close inspector">✕</button>
      </header>
      <div className="inspector-body">
        {tab === "context" ? <Contexts contexts={Object.values(run.view.contexts)} /> : <Calls run={run} />}
      </div>
    </aside>
  );
}

function Contexts({ contexts }: { contexts: AgentContext[] }) {
  if (contexts.length === 0) return <p className="muted">No LLM calls yet.</p>;
  return (
    <>
      <p className="muted small">The latest call of each agent: what was sent to the model, by category.</p>
      {contexts.map((c) => <ContextCard key={c.node} context={c} />)}
    </>
  );
}

function ContextCard({ context: c }: { context: AgentContext }) {
  const total = CATEGORIES.reduce((sum, k) => sum + (c.breakdown[k] ?? 0), 0) || 1;
  return (
    <section className="context-card">
      <h3>
        {nodeLabel(c.node)}
        <span className="muted small"> · {c.model.replace(/^\w+:/, "")}{c.effort ? ` · ${c.effort} effort` : ""} · {c.calls} call{c.calls === 1 ? "" : "s"}</span>
      </h3>
      <p className="small">
        {formatTokens(c.inputTokens)} input tokens of a {formatTokens(c.window)} window ({percent(c.inputTokens, c.window)})
      </p>
      <div className="stack" aria-hidden>
        {CATEGORIES.map((k) => (c.breakdown[k] ? (
          <span key={k} className={`seg ${k}`} style={{ width: `${(c.breakdown[k] / total) * 100}%` }} />
        ) : null))}
      </div>
      <ul className="legend">
        {CATEGORIES.filter((k) => c.breakdown[k]).map((k) => (
          <li key={k}><span className={`swatch ${k}`} />{CATEGORY_LABELS[k]} <span className="muted">{formatTokens(c.breakdown[k])}</span></li>
        ))}
      </ul>
      <details className="fold">
        <summary>{c.messages.length} message{c.messages.length === 1 ? "" : "s"} sent</summary>
        {c.messages.map((m, i) => (
          <details key={i} className="message">
            <summary>
              <span className={`swatch ${m.category}`} />{CATEGORY_LABELS[m.category] ?? m.category}
              {m.name ? ` · ${m.name}` : ""} <span className="muted">· {formatTokens(m.tokens)} tokens</span>
            </summary>
            <pre>{m.content}{m.truncated ? `\n… ${m.truncated.toLocaleString()} more characters not shown` : ""}</pre>
          </details>
        ))}
      </details>
    </section>
  );
}

function Calls({ run }: { run: RunEntry }) {
  const [calls, setCalls] = useState<LLMCall[]>(run.detail?.llm_calls ?? []);
  const counted = run.view.usage.calls;
  useEffect(() => {
    // Fetched again whenever another call is counted, so a running run's log stays current.
    api.run(run.id).then((detail) => setCalls(detail.llm_calls)).catch(() => undefined);
  }, [run.id, counted]);

  if (calls.length === 0) return <p className="muted">No LLM calls recorded yet.</p>;
  const sum = (pick: (c: LLMCall) => number) => calls.reduce((total, c) => total + pick(c), 0);
  return (
    <div className="table-wrap">
      <p className="muted small">
        Input tokens are split as the provider reports them: new (uncached), read from its prompt cache, or written
        to it. Failed calls are marked ✕ (hover for the error).
      </p>
      <table className="calls">
        <thead>
          <tr><th>Agent</th><th>Model</th><th>Uncached in</th><th>Cache read</th><th>Cache write</th><th>Output</th><th>Time</th><th>Cost</th><th>Tools called</th></tr>
        </thead>
        <tbody>
          {calls.map((c) => (
            <tr key={c.id} className={c.ok ? "" : "failed"} title={c.error ?? undefined}>
              <td>{nodeLabel(c.agent ?? "")}</td>
              <td>{c.model}{c.effort ? <span className="muted"> · {c.effort}</span> : null}{c.ok ? "" : " ✕"}</td>
              <td>{formatTokens(c.input_uncached)}</td>
              <td>{formatTokens(c.cache_read)}</td>
              <td>{formatTokens(c.cache_write)}</td>
              <td>{formatTokens(c.output)}</td>
              <td>{(c.latency_ms / 1000).toFixed(1)}s</td>
              <td>{c.cost_usd === null ? "—" : formatCost(c.cost_usd)}</td>
              <td className="tools">{c.tool_calls.join(", ")}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td>{calls.length} calls</td><td />
            <td>{formatTokens(sum((c) => c.input_uncached))}</td>
            <td>{formatTokens(sum((c) => c.cache_read))}</td>
            <td>{formatTokens(sum((c) => c.cache_write))}</td>
            <td>{formatTokens(sum((c) => c.output))}</td>
            <td>{(sum((c) => c.latency_ms) / 1000).toFixed(0)}s</td>
            <td>{formatCost(sum((c) => c.cost_usd ?? 0))}</td>
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
