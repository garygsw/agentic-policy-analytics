import type { ThreadSummary } from "@/lib/types";

interface Props {
  threads: ThreadSummary[];
  activeId: string | null;
  open: boolean; // on narrow screens the list slides in
  onOpen: (id: string) => void;
  onNew: () => void;
  onHide: () => void; // collapse the sidebar (wide screens)
}

/** Conversation history. Conversations whose first message was refused (no runs) are not listed. */
export function Sidebar({ threads, activeId, open, onOpen, onNew, onHide }: Props) {
  const shown = threads.filter((t) => t.runs > 0);
  return (
    <nav className={`sidebar${open ? " open" : ""}`} aria-label="Conversations">
      <div className="brand">
        <span>Policy Data Analytics</span>
        <button className="icon hide-sidebar" onClick={onHide} aria-label="Hide sidebar" title="Hide sidebar">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><rect x="1.5" y="2.5" width="13" height="11" rx="2" fill="none" stroke="currentColor" strokeWidth="1.3" /><line x1="6" y1="2.5" x2="6" y2="13.5" stroke="currentColor" strokeWidth="1.3" /></svg>
        </button>
      </div>
      <button className="new" onClick={onNew}>+ New analysis</button>
      <div className="section-label">Recent</div>
      {shown.length === 0 && <p className="muted small">No conversations yet.</p>}
      <ul>
        {shown.map((t) => (
          <li key={t.id}>
            <button className={t.id === activeId ? "active" : ""} onClick={() => onOpen(t.id)} title={t.title}>
              <span className={`dot ${t.last_status ?? ""}`} aria-label={t.last_status ?? ""} />
              <span className="title">{t.title || "Untitled"}</span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
