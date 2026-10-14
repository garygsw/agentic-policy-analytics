import type { OptionsResponse, RunOptions } from "@/lib/types";

import { SCOPE_LABELS, TRIAGE_LABELS } from "./labels";

interface Props {
  catalog: OptionsResponse;
  value: RunOptions;
  onChange: (value: RunOptions) => void;
  planApproval: boolean;
  onPlanApproval: (on: boolean) => void;
}

/** Per-request settings: model, reasoning effort, enabled tools, triage, web scope and plan approval. */
export function OptionsBar({ catalog, value, onChange, planApproval, onPlanApproval }: Props) {
  const set = (patch: Partial<RunOptions>) => onChange({ ...value, ...patch });
  const model = value.model ?? catalog.defaults.model ?? catalog.models[0]?.id;
  const effort = value.reasoning_effort ?? catalog.defaults.reasoning_effort;
  const optional = catalog.tools.filter((t) => !t.required);
  const enabled = value.tools ?? optional.map((t) => t.name); // null means every tool
  const fallback = catalog.models.find((m) => m.id === model)?.fallback;

  const toggleTool = (name: string) => {
    const next = enabled.includes(name) ? enabled.filter((n) => n !== name) : [...enabled, name];
    set({ tools: next.length === optional.length ? null : next });
  };

  return (
    <div className="options">
      <select aria-label="Model" value={model} onChange={(e) => set({ model: e.target.value })}
        title={fallback ? `Falls back to ${fallback} if this provider fails` : undefined}>
        {catalog.models.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
      </select>

      <div className="segmented" role="group" aria-label="Reasoning effort">
        {catalog.efforts.map((level) => (
          <button key={level} type="button" aria-pressed={effort === level}
            onClick={() => set({ reasoning_effort: level as RunOptions["reasoning_effort"] })}>
            {level}
          </button>
        ))}
      </div>

      <details className="menu">
        <summary>Tools {enabled.length}/{optional.length}</summary>
        <div className="menu-body">
          {optional.map((t) => (
            <label key={t.name} className="check">
              <input type="checkbox" checked={enabled.includes(t.name)} onChange={() => toggleTool(t.name)} />
              <span><b>{t.name}</b><small>{t.description}</small></span>
            </label>
          ))}
          <p className="muted small">Always on: {catalog.tools.filter((t) => t.required).map((t) => t.name).join(", ")}</p>
        </div>
      </details>

      <select aria-label="Triage" value={value.triage} onChange={(e) => set({ triage: e.target.value as RunOptions["triage"] })}>
        {catalog.triage.map((t) => <option key={t} value={t}>{TRIAGE_LABELS[t] ?? t}</option>)}
      </select>

      <select aria-label="Web search scope" value={value.web_scope}
        onChange={(e) => set({ web_scope: e.target.value as RunOptions["web_scope"] })}>
        {catalog.web_scopes.map((s) => <option key={s} value={s}>{SCOPE_LABELS[s] ?? s}</option>)}
      </select>

      <label className="check inline" title="Pause for your approval before any data is fetched">
        <input type="checkbox" checked={planApproval} onChange={(e) => onPlanApproval(e.target.checked)} />
        Approve plan
      </label>
    </div>
  );
}
