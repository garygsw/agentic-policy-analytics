"use client";
import { useEffect, useRef, useState, type CSSProperties, type RefObject } from "react";

import { sidebarSections } from "@/lib/threads";
import type { ThreadSummary } from "@/lib/types";

import { usePopover } from "./Popover";

interface Actions {
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onPin: (id: string, pinned: boolean) => void;
  onDelete: (id: string) => void;
}

interface Props extends Actions {
  threads: ThreadSummary[]; // one page, already filtered by the search
  hasMore: boolean; // more conversations than are listed
  onMore: () => void;
  search: string;
  onSearch: (text: string) => void;
  searchBox: RefObject<HTMLInputElement | null>; // focused by the page's ⌘K shortcut
  activeId: string | null;
  open: boolean; // on narrow screens the list slides in
  onNew: () => void;
  onHide: () => void; // collapse the sidebar (wide screens)
}

/** Conversation history: a search box, pinned conversations, then recent ones, a page at a time. Each has a menu
 * to pin, rename or delete it. */
export function Sidebar({ threads, hasMore, onMore, search, onSearch, searchBox, activeId, open, onNew, onHide,
  ...actions }: Props) {
  const { pinned, recent } = sidebarSections(threads);
  const searching = search.trim() !== "";
  const mac = useIsMac();
  const item = (t: ThreadSummary) => <ThreadItem key={t.id} thread={t} active={t.id === activeId} {...actions} />;
  return (
    <nav className={`sidebar${open ? " open" : ""}`} aria-label="Conversations">
      <div className="brand">
        <span>Policy Data Analytics</span>
        <button className="icon hide-sidebar" onClick={onHide} aria-label="Hide sidebar" title="Hide sidebar">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><rect x="1.5" y="2.5" width="13" height="11" rx="2" fill="none" stroke="currentColor" strokeWidth="1.3" /><line x1="6" y1="2.5" x2="6" y2="13.5" stroke="currentColor" strokeWidth="1.3" /></svg>
        </button>
      </div>
      <button className="new" onClick={onNew} title="New conversation">
        <svg className="new-icon" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="9" fill="currentColor" /><path d="M10 6v8M6 10h8" stroke="#fff" strokeWidth="1.7" strokeLinecap="round" /></svg>
        <span className="new-label">New</span>
        <kbd>{mac ? "⌘⇧O" : "Ctrl+Shift+O"}</kbd>
      </button>
      <label className="search">
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.4" /><path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
        <input ref={searchBox} type="search" aria-label="Search conversations" placeholder="Search" value={search}
          onChange={(e) => onSearch(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape" && search) {
              e.preventDefault(); // clears the search; it should not also stop a running analysis
              onSearch("");
            }
          }} />
        {!search && <kbd>{mac ? "⌘K" : "Ctrl+K"}</kbd>}
      </label>
      {searching ? (
        <>
          <div className="section-label">Results</div>
          {pinned.length + recent.length === 0 && <p className="muted small">No conversation matches “{search.trim()}”.</p>}
          <ul>{[...pinned, ...recent].map(item)}</ul>
        </>
      ) : (
        <>
          {pinned.length > 0 && (
            <>
              <div className="section-label">Pinned</div>
              <ul>{pinned.map(item)}</ul>
            </>
          )}
          <div className="section-label">Recent</div>
          {recent.length === 0 && (
            <p className="muted small">{pinned.length ? "Nothing else yet." : "No conversations yet."}</p>
          )}
          <ul>{recent.map(item)}</ul>
        </>
      )}
      {hasMore && <button className="view-more" onClick={onMore}>View more</button>}
    </nav>
  );
}

/** Whether to write shortcuts the Mac way (⌘) or with Ctrl; the page accepts both keys everywhere. Worked out
 * after the first render, so the server's HTML and the browser's first render agree. */
function useIsMac(): boolean {
  const [mac, setMac] = useState(false);
  useEffect(() => {
    setMac(/Mac|iPhone|iPad/.test(navigator.userAgent));
  }, []);
  return mac;
}

const MENU_WIDTH = 190;
const MENU_HEIGHT = 150; // about three items and a divider: open upward when there is less room below
const SHORTCUTS: Record<string, "pin" | "rename" | "delete"> = { p: "pin", r: "rename", d: "delete" };

function ThreadItem({ thread, active, onOpen, onRename, onPin, onDelete }: Actions & {
  thread: ThreadSummary; active: boolean;
}) {
  const [mode, setMode] = useState<"view" | "rename" | "delete">("view");
  const { open, setOpen, ref } = usePopover();
  const [place, setPlace] = useState<CSSProperties>({});
  const more = useRef<HTMLButtonElement>(null);
  const title = thread.title || "Untitled";

  const act = (action: "pin" | "rename" | "delete") => {
    setOpen(false);
    if (action === "pin") onPin(thread.id, !thread.pinned);
    else setMode(action);
  };

  const openMenu = () => {
    // The menu sits at a fixed point on the screen, so the scrolling sidebar never clips it.
    const r = more.current!.getBoundingClientRect();
    const left = Math.max(8, r.right - MENU_WIDTH);
    setPlace(window.innerHeight - r.bottom > MENU_HEIGHT ? { top: r.bottom + 4, left }
      : { bottom: window.innerHeight - r.top + 4, left });
    setOpen(true);
  };

  // While the menu is open: P, R and D choose an item; scrolling closes it (it would no longer line up).
  const latest = useRef(act);
  latest.current = act;
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      const action = SHORTCUTS[e.key.toLowerCase()];
      if (action && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        latest.current(action);
      }
    };
    const onScroll = () => setOpen(false);
    document.addEventListener("keydown", onKey);
    document.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("scroll", onScroll, true);
    };
  }, [open, setOpen]);

  if (mode === "rename") {
    return (
      <li>
        <RenameBox initial={thread.title} onDone={(name) => {
          setMode("view");
          if (name && name !== thread.title) onRename(thread.id, name);
        }} />
      </li>
    );
  }
  if (mode === "delete") {
    return (
      <li className="confirm" role="alertdialog" aria-label="Delete conversation">
        <p>Delete “{title}”? It will be removed from your history.</p>
        <div className="row">
          <button className="danger" onClick={() => { setMode("view"); onDelete(thread.id); }}>Delete</button>
          <button autoFocus onClick={() => setMode("view")}>Cancel</button>
        </div>
      </li>
    );
  }
  return (
    <li>
      <div className={`thread-row${active ? " active" : ""}${open ? " menu-open" : ""}`} ref={ref}>
        <button className="open-thread" onClick={() => onOpen(thread.id)} title={title}>
          <span className={`dot ${thread.last_status ?? ""}`} aria-label={thread.last_status ?? ""} />
          <span className="title">{title}</span>
        </button>
        <button ref={more} className="more" aria-label={`More actions for ${title}`} aria-haspopup="menu"
          aria-expanded={open} onClick={() => (open ? setOpen(false) : openMenu())}>
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="3.5" r="1.3" fill="currentColor" /><circle cx="8" cy="8" r="1.3" fill="currentColor" /><circle cx="8" cy="12.5" r="1.3" fill="currentColor" /></svg>
        </button>
        {open && (
          <div className="popover thread-menu" role="menu" style={place}>
            <button type="button" role="menuitem" className="menu-item" onClick={() => act("pin")}>
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M10 2.5l3.5 3.5M11.5 4L8.5 7 5.5 6.5 4 8l4 4 1.5-1.5L9 7.5l3-3M6 10l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></svg>
              <span className="item-title">{thread.pinned ? "Unpin" : "Pin"}</span><kbd>P</kbd>
            </button>
            <button type="button" role="menuitem" className="menu-item" onClick={() => act("rename")}>
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M11 2.5L13.5 5 5.5 13H3v-2.5zM9.5 4L12 6.5" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /></svg>
              <span className="item-title">Rename</span><kbd>R</kbd>
            </button>
            <div className="menu-divider" />
            <button type="button" role="menuitem" className="menu-item danger" onClick={() => act("delete")}>
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 4.5h11M6 4.5V3h4v1.5M4 4.5l.7 9h6.6l.7-9M6.8 7v4.5M9.2 7v4.5" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></svg>
              <span className="item-title">Delete</span><kbd>D</kbd>
            </button>
          </div>
        )}
      </div>
    </li>
  );
}

/** Edits a conversation's name in place: Enter or clicking away saves, Escape keeps the old name. */
function RenameBox({ initial, onDone }: { initial: string; onDone: (name: string | null) => void }) {
  const [value, setValue] = useState(initial);
  const finished = useRef(false);
  const finish = (name: string | null) => {
    if (finished.current) return; // Enter, then the blur as the box goes away: save once
    finished.current = true;
    onDone(name === null ? null : name.trim());
  };
  return (
    <input className="rename" aria-label="Conversation name" value={value} maxLength={100} autoFocus
      onFocus={(e) => e.currentTarget.select()}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => finish(value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.nativeEvent.isComposing) {
          e.preventDefault();
          finish(value);
        } else if (e.key === "Escape") {
          e.preventDefault(); // handled here: it should not also stop a running analysis
          finish(null);
        }
      }} />
  );
}
