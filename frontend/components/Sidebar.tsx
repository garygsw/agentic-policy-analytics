import type { ThreadSummary } from "@/lib/types";

interface Props {
  threads: ThreadSummary[];
  activeId: string | null;
  open: boolean; // on narrow screens the list slides in
  onOpen: (id: string) => void;
  onNew: () => void;
}

/** Conversation history. Conversations whose first message was refused (no runs) are not listed. */
export function Sidebar({ threads, activeId, open, onOpen, onNew }: Props) {
  const shown = threads.filter((t) => t.runs > 0);
  return (
    <nav className={`sidebar${open ? " open" : ""}`} aria-label="Conversations">
      <div className="brand">Policy Data Analytics</div>
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
