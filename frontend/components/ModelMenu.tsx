"use client";
import { useState } from "react";

import type { OptionsResponse, RunOptions } from "@/lib/types";

import { Chevron, usePopover } from "./Popover";

const capitalise = (word: string) => word.charAt(0).toUpperCase() + word.slice(1);

/** The model menu beside the send button: the models, then an Effort section that opens a submenu. */
export function ModelMenu({ catalog, value, onChange }: {
  catalog: OptionsResponse; value: RunOptions; onChange: (value: RunOptions) => void;
}) {
  const { open, setOpen, ref } = usePopover();
  const [effortOpen, setEffortOpen] = useState(false);
  const model = catalog.models.find((m) => m.id === (value.model ?? catalog.defaults.model)) ?? catalog.models[0];
  const defaultEffort = catalog.defaults.reasoning_effort ?? "low";
  const effort = value.reasoning_effort ?? defaultEffort;

  const close = () => {
    setOpen(false);
    setEffortOpen(false);
  };

  return (
    <div className="menu-anchor" ref={ref}>
      <button type="button" className="pill" aria-haspopup="menu" aria-expanded={open}
        onClick={() => (open ? close() : setOpen(true))}>
        {model?.name} <span className="muted">· {capitalise(effort)}</span>
        <Chevron />
      </button>
      {open && (
        <div className="popover align-right" role="menu">
          {catalog.models.map((m) => (
            <button key={m.id} type="button" role="menuitemradio" aria-checked={m.id === model?.id}
              className="menu-item" onClick={() => { onChange({ ...value, model: m.id }); close(); }}>
              <span>
                <span className="item-title">{m.name}</span>
                <span className="item-desc">{m.description}. Falls back to {m.fallback_name}.</span>
              </span>
              {m.id === model?.id && <span className="tick">✓</span>}
            </button>
          ))}
          <div className="menu-divider" />
          <div className="submenu-anchor" onMouseEnter={() => setEffortOpen(true)}>
            <button type="button" className="menu-item" aria-haspopup="menu" aria-expanded={effortOpen}
              onClick={() => setEffortOpen(!effortOpen)}>
              <span className="item-title">Effort</span>
              <span className="muted">{capitalise(effort)} ›</span>
            </button>
            {effortOpen && (
              <div className="popover submenu" role="menu">
                <p className="menu-note">Higher effort means more thorough answers, but takes longer and costs more.</p>
                {catalog.efforts.map((level) => (
                  <button key={level} type="button" role="menuitemradio" aria-checked={level === effort}
                    className="menu-item"
                    onClick={() => { onChange({ ...value, reasoning_effort: level as RunOptions["reasoning_effort"] }); close(); }}>
                    <span>
                      {capitalise(level)}
                      {level === defaultEffort && <span className="tag">Default</span>}
                    </span>
                    {level === effort && <span className="tick">✓</span>}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
