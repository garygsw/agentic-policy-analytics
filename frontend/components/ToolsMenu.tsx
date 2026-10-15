"use client";
import type { OptionsResponse, RunOptions } from "@/lib/types";

import { TOOL_NAMES, TRIAGE_LABELS } from "./labels";
import { Switch, usePopover } from "./Popover";

interface Props {
  catalog: OptionsResponse;
  value: RunOptions;
  onChange: (value: RunOptions) => void;
  planApproval: boolean;
  onPlanApproval: (on: boolean) => void;
}

/** The + menu: a switch for each optional tool, and the less common settings (plan approval, triage). */
export function ToolsMenu({ catalog, value, onChange, planApproval, onPlanApproval }: Props) {
  const { open, setOpen, ref } = usePopover();
  const optional = catalog.tools.filter((t) => !t.required);
  const enabled = value.tools ?? optional.map((t) => t.name); // null means every tool
  const switchedOff = optional.length - enabled.length;

  const toggle = (name: string, on: boolean) => {
    const next = on ? [...enabled, name] : enabled.filter((n) => n !== name);
    onChange({ ...value, tools: next.length === optional.length ? null : next });
  };

  return (
    <div className="menu-anchor" ref={ref}>
      <button type="button" className="plus" aria-label="Tools and settings" aria-haspopup="menu"
        aria-expanded={open} onClick={() => setOpen(!open)}>
        +{switchedOff > 0 && <span className="off-count">{switchedOff} off</span>}
      </button>
      {open && (
        <div className="popover align-left" role="menu">
          <p className="menu-heading">Tools</p>
          {optional.map((t) => (
            <div key={t.name} className="menu-item">
              <span>
                <span className="item-title">{TOOL_NAMES[t.name] ?? t.name}</span>
                <span className="item-desc">{t.description}</span>
              </span>
              <Switch on={enabled.includes(t.name)} onChange={(on) => toggle(t.name, on)}
                label={TOOL_NAMES[t.name] ?? t.name} />
            </div>
          ))}
          <p className="menu-note">
            Always on: {catalog.tools.filter((t) => t.required).map((t) => TOOL_NAMES[t.name] ?? t.name).join(", ")}.
          </p>
          <div className="menu-divider" />
          <p className="menu-heading">Settings</p>
          <div className="menu-item">
            <span>
              <span className="item-title">Approve plan</span>
              <span className="item-desc">Pause for your approval before any data is fetched</span>
            </span>
            <Switch on={planApproval} onChange={onPlanApproval} label="Approve plan" />
          </div>
          <label className="menu-item stacked">
            <span>
              <span className="item-title">Triage</span>
              <span className="item-desc">How a question is checked for scope</span>
            </span>
            <select value={value.triage} onChange={(e) => onChange({ ...value, triage: e.target.value as RunOptions["triage"] })}>
              {catalog.triage.map((t) => <option key={t} value={t}>{TRIAGE_LABELS[t] ?? t}</option>)}
            </select>
          </label>
        </div>
      )}
    </div>
  );
}
