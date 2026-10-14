"use client";
import { useState } from "react";
import Markdown from "react-markdown";

import { markdownUrl, reportUrl } from "@/lib/api";
import { formatCost, formatElapsed, formatTokens } from "@/lib/format";
import { checkLabel, draftStage, elapsedSeconds, pendingOf, visibleSteps } from "@/lib/runState";
import type { RunDetail } from "@/lib/types";
import { displayedStatus, type RunEntry } from "@/lib/useConversation";

import { OUTCOMES } from "./labels";
import { ReportFrame } from "./ReportFrame";
import { Steps } from "./Steps";

interface Props {
  run: RunEntry;
  verbosity: number;
  now: number;
  onInspect: () => void;
  onPlan: (approved: boolean, feedback: string) => void;
}

/** One question and everything the agents did for it. */
export function RunBlock({ run, verbosity, now, onInspect, onPlan }: Props) {
  const status = displayedStatus(run);
  const working = status === "running";
  const [stepsOpen, setStepsOpen] = useState<boolean | null>(null); // null: open while working, closed after
  const open = stepsOpen ?? working;
  const steps = visibleSteps(run.view, verbosity);
  const pending = pendingOf(run.view);
  const draft = run.view.drafts.at(-1);
  const usage = run.view.usage;
  const finished = status === "completed" || status === "failed" || status === "cancelled";
  // A run stopped or failed part-way has no outcome; its status says what happened.
  const outcome = run.detail?.outcome ?? run.view.end?.outcome ?? (finished && status !== "completed" ? status : null);

  return (
    <section className="run">
      <div className="bubble user">{run.question}</div>

      {steps.length > 0 && (
        <div className="trace">
          <button className="trace-toggle" onClick={() => setStepsOpen(!open)} aria-expanded={open}>
            {open ? "▾" : "▸"} {steps.length} step{steps.length === 1 ? "" : "s"}
          </button>
          {open && <Steps steps={steps} />}
        </div>
      )}

      {pending?.kind === "clarification" && (
        <div className="bubble assistant">
          <p>{pending.question}</p>
          <p className="hint">Reply in the box below.</p>
        </div>
      )}
      {pending?.kind === "plan_approval" && (
        <PlanApproval plan={pending.plan} reasoning={pending.reasoning} onDecide={onPlan} />
      )}

      {working && draft && (
        <div className="draft">
          <div className="draft-head">
            <span className="badge pulse">{draftStage(run.view)}</span>
            {run.view.drafts.length > 1 && <span className="muted small">draft {run.view.drafts.length}</span>}
          </div>
          <div className="markdown"><Markdown>{draft.content}</Markdown></div>
        </div>
      )}

      {finished && run.detail && (
        <Answer runId={run.id} detail={run.detail} outcome={outcome} check={checkLabel(run.view, outcome)} />
      )}

      <div className="run-meta">
        <span>{formatElapsed(elapsedSeconds(run.view, now))}</span>
        <span>{usage.calls} LLM call{usage.calls === 1 ? "" : "s"}</span>
        <span>↑ {formatTokens(usage.input)} ↓ {formatTokens(usage.output)} tokens</span>
        <span>{formatCost(usage.cost)}</span>
        <button className="link" onClick={onInspect}>Inspect agents</button>
      </div>
    </section>
  );
}

function Answer({ runId, detail, outcome, check }: {
  runId: string; detail: RunDetail; outcome: string | null; check: string | null;
}) {
  const label = outcome ? OUTCOMES[outcome] : undefined;
  return (
    <div className="answer">
      <div className="answer-head">
        {label && <span className={`badge ${label.tone}`}>{label.label}</span>}
        {check && outcome === "answered" && <span className="badge ok">{check}</span>}
        {detail.has_report && (
          <span className="actions">
            <a href={reportUrl(runId)} target="_blank" rel="noopener noreferrer">Open report ↗</a>
            <a href={markdownUrl(runId)}>Download Markdown</a>
          </span>
        )}
      </div>
      {detail.status === "failed" && <p className="error-text">The run failed. Please try again.</p>}
      {detail.has_report ? (
        <ReportFrame runId={runId} />
      ) : (
        detail.answer && <div className="bubble assistant markdown"><Markdown>{detail.answer}</Markdown></div>
      )}
    </div>
  );
}

function PlanApproval({ plan, reasoning, onDecide }: {
  plan: string; reasoning: string; onDecide: (approved: boolean, feedback: string) => void;
}) {
  const [feedback, setFeedback] = useState("");
  return (
    <div className="plan">
      <p className="plan-title">Approve this plan before any data is fetched?</p>
      <p>{plan}</p>
      {reasoning && <p className="muted small">{reasoning}</p>}
      <textarea
        aria-label="What should change"
        placeholder="Optional: what should change?"
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
        rows={2}
      />
      <div className="row">
        <button className="primary" onClick={() => onDecide(true, "")}>Approve</button>
        <button onClick={() => onDecide(false, feedback.trim())}>{feedback.trim() ? "Revise the plan" : "Reject"}</button>
      </div>
    </div>
  );
}
