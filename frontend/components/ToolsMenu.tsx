"use client";
import type { OptionsResponse, RunOptions } from "@/lib/types";

import { TOOL_NAMES } from "./labels";
import { Switch, usePopover } from "./Popover";

interface Props {
  catalog: OptionsResponse;
  value: RunOptions;
  onChange: (value: RunOptions) => void;
}

/** The + menu: a switch for each optional tool. */
export function ToolsMenu({ catalog, value, onChange }: Props) {
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
      <button type="button" className="plus" aria-label="Tools" aria-haspopup="menu"
        aria-expanded={open} onClick={() => setOpen(!open)}>
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5v11M2.5 8h11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
        {switchedOff > 0 && <span className="off-count">{switchedOff} off</span>}
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
        </div>
      )}
    </div>
  );
}
