import type { TraceEvent } from "@/lib/types";

import { nodeLabel } from "./labels";

const PREVIEW_CHARS = 220;

/** Short text inline; long text (or a tool result with full detail) folds into an expandable block. */
function Fold({ text, detail }: { text: string; detail?: string }) {
  if (text.length <= PREVIEW_CHARS && !detail) return <span className="step-text">{text}</span>;
  const preview = text.length > PREVIEW_CHARS ? `${text.slice(0, PREVIEW_CHARS)}…` : text;
  return (
    <details className="fold">
      <summary>{preview}</summary>
      <pre>{detail ?? text}</pre>
    </details>
  );
}

function Step({ event: e }: { event: TraceEvent }) {
  switch (e.kind) {
    case "action":
      return (
        <li className="step action">
          <span className="marker">⏺</span>
          <span className="who">{nodeLabel(e.node)}</span>
          <Fold text={e.content} />
        </li>
      );
    case "observation":
      return (
        <li className="step observation">
          <span className="marker">⎿</span>
          <Fold text={e.content} detail={e.detail} />
        </li>
      );
    case "reasoning":
      return (
        <li className="step reasoning">
          <span className="marker">∴</span>
          <span className="who">{nodeLabel(e.node)}</span>
          <Fold text={e.content} />
        </li>
      );
    case "thinking":
      return (
        <li className="step thinking">
          <details className="fold">
            <summary>Thinking · {nodeLabel(e.node)}</summary>
            <pre>{e.content}</pre>
          </details>
        </li>
      );
    case "error":
      return (
        <li className="step error">
          <span className="marker">⚠</span>
          <span className="who">{nodeLabel(e.node)}</span>
          <Fold text={e.content} />
        </li>
      );
    case "reply":
      return (
        <li className="step reply">
          <span className="marker">›</span>
          <span className="who">You</span>
          <span className="step-text">{e.content}</span>
        </li>
      );
    default:
      return null;
  }
}

export function Steps({ steps }: { steps: TraceEvent[] }) {
  if (steps.length === 0) return null;
  return (
    <ol className="steps">
      {steps.map((e) => <Step key={e.seq} event={e} />)}
    </ol>
  );
}
