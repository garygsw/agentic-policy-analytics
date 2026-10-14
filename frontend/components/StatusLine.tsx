"use client";
import { useState } from "react";

import { formatCost, formatElapsed, formatTokens } from "@/lib/format";
import { elapsedSeconds, type RunView } from "@/lib/runState";

/** The live one-liner while a run works: what is happening now, time, tokens and cost. */
export function StatusLine({ view, now, onStop }: { view: RunView; now: number; onStop: () => void }) {
  const [stopping, setStopping] = useState(false); // a run stops once its current step (e.g. an LLM call) ends
  return (
    <div className="statusline" role="status" aria-live="polite">
      <span className="spinner" aria-hidden>✻</span>
      <span className="status-text">{stopping ? "Stopping after the current step" : view.statusLine || "Starting"}…</span>
      <span className="muted">
        ({formatElapsed(elapsedSeconds(view, now))} · ↑ {formatTokens(view.usage.input)} ↓ {formatTokens(view.usage.output)}
        {" "}tokens · {formatCost(view.usage.cost)}{stopping ? "" : " · esc to stop"})
      </span>
      <button className="stop" disabled={stopping} onClick={() => { setStopping(true); onStop(); }}>Stop</button>
    </div>
  );
}
