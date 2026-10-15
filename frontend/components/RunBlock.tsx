"use client";
import { useState } from "react";
import Markdown from "react-markdown";

import { markdownUrl, pdfUrl } from "@/lib/api";
import { formatCost, formatElapsed, formatTokens } from "@/lib/format";
import { checkLabel, draftStage, elapsedSeconds, pendingOf, rejectedPlanOf, visibleSteps } from "@/lib/runState";
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
  onAnswer: (answer: string) => Promise<boolean>; // reply to a clarifying question; false: refused
  onPlan: (approved: boolean, feedback: string) => void; // approve, or ask for a plan revised by the feedback
  onRejectPlan: () => void; // turn the plan down: the run stops, and the plan can be reopened
  onCancel: () => void;
  latest: boolean; // the conversation's latest run: only its rejected plan can be reopened
}

/** One question and everything the agents did for it. */
export function RunBlock({ run, verbosity, now, onInspect, onAnswer, onPlan, onRejectPlan, onCancel, latest }: Props) {
  const status = displayedStatus(run);
  const working = status === "running";
  const [stepsOpen, setStepsOpen] = useState<boolean | null>(null); // null: open while working, closed after
  const open = stepsOpen ?? working;
  const steps = visibleSteps(run.view, verbosity);
  const pending = pendingOf(run.view);
  const rejectedPlan = rejectedPlanOf(run.view);
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
        <ClarifyingQuestion question={pending.question} choices={pending.choices ?? []} onAnswer={onAnswer}
          onCancel={onCancel} />
      )}
      {pending?.kind === "plan_approval" && (
        <PlanApproval plan={pending.plan} reasoning={pending.reasoning} onDecide={onPlan} onReject={onRejectPlan} />
      )}
      {rejectedPlan && (
        <RejectedPlan plan={rejectedPlan.plan} reasoning={rejectedPlan.reasoning} canReopen={latest}
          onDecide={onPlan} />
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
        <Answer runId={run.id} detail={run.detail} outcome={outcome} check={checkLabel(run.view, outcome)}
          flags={run.view.end?.flags ?? []} />
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

function Answer({ runId, detail, outcome, check, flags }: {
  runId: string; detail: RunDetail; outcome: string | null; check: string | null; flags: string[];
}) {
  const label = outcome ? OUTCOMES[outcome] : undefined;
  return (
    <div className="answer">
      <div className="answer-head">
        {label && <span className={`badge ${label.tone}`}>{label.label}</span>}
        {check && outcome === "answered" && (
          <span className={`badge ${flags.length ? "warn" : "ok"}`} title={flags.join("\n")}>{check}</span>
        )}
        {detail.has_report && (
          <span className="actions">
            <a href={markdownUrl(runId)}>Download Markdown</a>
            {detail.has_pdf && <a href={pdfUrl(runId)}>Download PDF</a>}
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

/** A clarifying question: pick one of the offered answers, or "Other" to write your own. */
function ClarifyingQuestion({ question, choices, onAnswer, onCancel }: {
  question: string; choices: string[]; onAnswer: (answer: string) => Promise<boolean>; onCancel: () => void;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const send = async (answer: string) => {
    const value = answer.trim();
    if (!value || sending) return;
    setSending(true);
    const accepted = await onAnswer(value);
    setSending(false);
    if (accepted) setText("");
  };
  return (
    <div className="clarify">
      <p className="clarify-question">{question}</p>
      <div className="choices" role="group" aria-label="Answers">
        {choices.map((choice, i) => (
          <button key={choice} type="button" className="choice" disabled={sending} onClick={() => void send(choice)}>
            <span className="choice-number">{i + 1}</span>
            <span>{choice}</span>
          </button>
        ))}
        <form className="choice other" onSubmit={(e) => { e.preventDefault(); void send(text); }}>
          <span className="choice-number">{choices.length + 1}</span>
          <input aria-label="Other answer" value={text} disabled={sending} autoFocus={choices.length === 0}
            placeholder={choices.length ? "Other: write your own answer" : "Write your answer"}
            onChange={(e) => setText(e.target.value)} />
          <button type="submit" className="primary" disabled={sending || !text.trim()}>Send</button>
        </form>
      </div>
      <div className="clarify-foot">
        <button type="button" className="link quiet-link" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

/** A proposed plan: approve it, reject it (the run stops; the plan can be reopened), or say what should change and
 * submit, which brings a revised plan back here for approval. */
function PlanApproval({ plan, reasoning, onDecide, onReject }: {
  plan: string; reasoning: string; onDecide: (approved: boolean, feedback: string) => void; onReject: () => void;
}) {
  const [feedback, setFeedback] = useState("");
  return (
    <div className="plan">
      <p className="plan-title">Approve this plan before any data is fetched?</p>
      <p>{plan}</p>
      {reasoning && <p className="muted small">{reasoning}</p>}
      <div className="row">
        <button className="primary" onClick={() => onDecide(true, "")}>Approve</button>
        <button onClick={onReject}>Reject</button>
      </div>
      <form className="plan-change" onSubmit={(e) => {
        e.preventDefault();
        if (feedback.trim()) onDecide(false, feedback.trim());
      }}>
        <textarea aria-label="What should change" placeholder="Or say what should change, to get a revised plan"
          value={feedback} onChange={(e) => setFeedback(e.target.value)} rows={2}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }} />
        {feedback.trim() && <button type="submit" className="primary">Submit</button>}
      </form>
    </div>
  );
}

/** A plan the user rejected. The run stopped there; the plan can be reviewed again and approved or revised, as long
 * as no newer question was asked in the conversation. */
function RejectedPlan({ plan, reasoning, canReopen, onDecide }: {
  plan: string; reasoning: string; canReopen: boolean; onDecide: (approved: boolean, feedback: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (open && canReopen) {
    return <PlanApproval plan={plan} reasoning={reasoning} onDecide={onDecide} onReject={() => setOpen(false)} />;
  }
  return (
    <div className="plan rejected">
      <span className="badge">Plan rejected</span>
      <span className="muted small">{canReopen ? "The analysis stopped here." : "A newer question was asked since."}</span>
      {canReopen && <button className="link" onClick={() => setOpen(true)}>Review plan</button>}
    </div>
  );
}
