"use client";
import { usePopover } from "./Popover";

export type Mode = "auto" | "plan";

const MODES: { id: Mode; name: string; description: string }[] = [
  { id: "auto", name: "Auto", description: "Run straight through to the report" },
  { id: "plan", name: "Plan", description: "Show the plan and wait for your approval before any data is fetched" },
];

/** How a question runs: straight through (Auto, the default), or pausing for the user to approve the plan. */
export function ModeMenu({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  const { open, setOpen, ref } = usePopover();
  const current = MODES.find((m) => m.id === mode) ?? MODES[0];
  return (
    <div className="menu-anchor" ref={ref}>
      <button type="button" className={`pill${mode === "plan" ? " plan" : ""}`} aria-haspopup="menu"
        aria-expanded={open} aria-label={`Mode: ${current.name}`} onClick={() => setOpen(!open)}>
        {current.name} <span aria-hidden>⌄</span>
      </button>
      {open && (
        <div className="popover align-left narrow" role="menu">
          {MODES.map((m) => (
            <button key={m.id} type="button" role="menuitemradio" aria-checked={m.id === mode} className="menu-item"
              onClick={() => { onChange(m.id); setOpen(false); }}>
              <span>
                <span className="item-title">{m.name}</span>
                <span className="item-desc">{m.description}</span>
              </span>
              {m.id === mode && <span className="tick">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
