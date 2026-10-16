"use client";
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";

import { Composer } from "@/components/Composer";
import { Inspector } from "@/components/Inspector";
import { DETAIL_LABELS, EXAMPLES } from "@/components/labels";
import { ModeMenu, type Mode } from "@/components/ModeMenu";
import { ModelMenu } from "@/components/ModelMenu";
import { RunBlock } from "@/components/RunBlock";
import { Sidebar } from "@/components/Sidebar";
import { SidebarResizer } from "@/components/SidebarResizer";
import { StatusLine } from "@/components/StatusLine";
import { ToolsMenu } from "@/components/ToolsMenu";
import { api } from "@/lib/api";
import { useNow, useStoredState } from "@/lib/hooks";
import { clampSidebarWidth, SIDEBAR_WIDTH } from "@/lib/layout";
import { pendingOf } from "@/lib/runState";
import type { OptionsResponse, RunOptions, TraceEvent } from "@/lib/types";
import { displayedStatus, useConversation, type RunEntry } from "@/lib/useConversation";
import { useRunStream } from "@/lib/useRunStream";

const DEFAULT_OPTIONS: RunOptions = { model: null, reasoning_effort: null, tools: null };

/** Keeps saved options valid against what the server offers now (a model or tool may have been removed). */
function validOptions(saved: RunOptions, catalog: OptionsResponse): RunOptions {
  const tools = new Set(catalog.tools.map((t) => t.name));
  return {
    model: catalog.models.some((m) => m.id === saved.model) ? saved.model : null,
    reasoning_effort: saved.reasoning_effort ?? null,
    tools: saved.tools ? saved.tools.filter((t) => tools.has(t)) : null,
  }; // settings saved by older versions (web scope, triage) are dropped: the API refuses them
}

/** Follows one live run's event stream (a component, so each live run gets its own connection). */
function Follower({ run, onEvent }: { run: RunEntry; onEvent: (id: string, e: TraceEvent) => void }) {
  useRunStream(run.id, run.view.lastSeq, true, (e) => onEvent(run.id, e));
  return null;
}

export default function Home() {
  const conversation = useConversation();
  const { runs, error, cancel, setError } = conversation;
  const [catalog, setCatalog] = useState<OptionsResponse | null>(null);
  const [storedOptions, setOptions] = useStoredState<RunOptions>("run-options", DEFAULT_OPTIONS);
  const [verbosity, setVerbosity] = useStoredState<number>("detail", 1); // Normal by default
  const [mode, setMode] = useStoredState<Mode>("mode", "auto"); // Plan pauses for approval of the plan
  const [text, setText] = useState("");
  const [inspecting, setInspecting] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false); // narrow screens: the sidebar slides over the page
  const [sidebarHidden, setSidebarHidden] = useStoredState<boolean>("sidebar-hidden", false); // wide screens
  const [savedWidth, saveWidth] = useStoredState<number>("sidebar-width", SIDEBAR_WIDTH.initial);
  const [dragWidth, setDragWidth] = useState<number | null>(null); // while the edge is being dragged
  const sidebarWidth = dragWidth ?? clampSidebarWidth(savedWidth);
  const searchBox = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.options().then(setCatalog).catch((e) => setError(e.message));
  }, [setError]);

  const options = catalog ? validOptions(storedOptions, catalog) : DEFAULT_OPTIONS; // unchecked until it loads
  const active = runs.findLast((r) => ["running", "awaiting_input"].includes(displayedStatus(r)));
  const running = active && displayedStatus(active) === "running" ? active : undefined;
  const pending = active ? pendingOf(active.view) : null;
  const now = useNow(Boolean(running));
  const inspected = runs.find((r) => r.id === inspecting);

  // Keep the newest output in view while the user is at the bottom; leave them be if they scrolled up.
  const scroller = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const lastRun = runs.at(-1);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && atBottom.current) el.scrollTop = el.scrollHeight;
  }, [runs.length, lastRun?.view.lastSeq, lastRun?.detail]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "o") { // New, as in other chat apps
        e.preventDefault();
        conversation.newThread();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "k") { // search the history
        e.preventDefault();
        if (window.matchMedia("(max-width: 860px)").matches) setSidebarOpen(true);
        else setSidebarHidden(false);
        setTimeout(() => searchBox.current?.select(), 0); // once a hidden sidebar is shown again
        return;
      }
      if (e.key !== "Escape" || e.defaultPrevented) return; // an Escape that closed a menu does nothing else
      if (inspecting) setInspecting(null);
      else if (running) void cancel(running.id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [inspecting, running, cancel, conversation.newThread, setSidebarHidden]);

  const submit = (value: string) => conversation.ask(value, options, mode === "plan");

  // While the agents wait on the user, the answer is given in the run (choices, plan buttons), not here.
  const placeholder = running ? "The agents are working…"
    : pending?.kind === "clarification" ? "Answer the question above, or cancel it"
      : pending?.kind === "plan_approval" ? "Approve or reject the plan above"
        : runs.length ? "Ask a follow-up question…" : "Ask about Singapore's public data…";
  const title = conversation.title;

  return (
    <div className={`app${sidebarHidden ? " sidebar-hidden" : ""}`}
      style={{ "--sidebar-width": `${sidebarWidth}px` } as CSSProperties}>
      <Sidebar
        threads={conversation.threads}
        hasMore={conversation.hasMore}
        onMore={conversation.showMore}
        searchBox={searchBox}
        search={conversation.search}
        onSearch={conversation.setSearch}
        activeId={conversation.threadId}
        open={sidebarOpen}
        onOpen={(id) => { setSidebarOpen(false); void conversation.openThread(id); }}
        onNew={() => { setSidebarOpen(false); conversation.newThread(); }}
        onRename={(id, name) => void conversation.updateThread(id, { title: name })}
        onPin={(id, pinned) => void conversation.updateThread(id, { pinned })}
        onDelete={(id) => void conversation.deleteThread(id)}
        onHide={() => setSidebarHidden(true)}
      />
      {sidebarOpen && <div className="backdrop" onClick={() => setSidebarOpen(false)} aria-hidden />}
      <main className="main">
        {!sidebarHidden && (
          <SidebarResizer width={sidebarWidth} onResize={setDragWidth}
            onResizeEnd={(width) => { saveWidth(width); setDragWidth(null); }} />
        )}
        <header className="topbar">
          <button className="icon menu-button" aria-label="Show conversations" title="Show conversations"
            onClick={() => (window.matchMedia("(max-width: 860px)").matches
              ? setSidebarOpen(!sidebarOpen) : setSidebarHidden(false))}>
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><rect x="1.5" y="2.5" width="13" height="11" rx="2" fill="none" stroke="currentColor" strokeWidth="1.3" /><line x1="6" y1="2.5" x2="6" y2="13.5" stroke="currentColor" strokeWidth="1.3" /></svg>
          </button>
          <span className="thread-title">{title || "New analysis"}</span>
          <label className="verbosity">
            Detail
            <select value={verbosity} onChange={(e) => setVerbosity(Number(e.target.value))}>
              {(catalog?.verbosity ?? [{ level: 1, name: "normal", shows: "" }]).map((v) => (
                <option key={v.level} value={v.level} title={v.shows}>{DETAIL_LABELS[v.level] ?? v.name}</option>
              ))}
            </select>
          </label>
        </header>

        <div className="scroll" ref={scroller}
          onScroll={(e) => {
            const el = e.currentTarget;
            atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          }}>
          <div className="column">
            {runs.length === 0 ? (
              <div className="empty">
                <h1>Ask about Singapore&apos;s public data</h1>
                <p className="muted">
                  Agents find official datasets and documents, compute the statistics, and write a report in which
                  every number is checked against the computation and cited.
                </p>
                <div className="examples">
                  {EXAMPLES.map((q) => <button key={q} onClick={() => setText(q)}>{q}</button>)}
                </div>
              </div>
            ) : (
              runs.map((run) => (
                <RunBlock key={run.id} run={run} verbosity={verbosity} now={now}
                  onInspect={() => setInspecting(run.id)}
                  onAnswer={(answer) => conversation.answer(run.id, answer, options)}
                  onPlan={(approved, feedback) => void conversation.decidePlan(run.id, approved, feedback, options)}
                  onRejectPlan={() => void conversation.rejectPlan(run.id)}
                  onCancel={() => void cancel(run.id)} latest={run.id === runs.at(-1)?.id} />
              ))
            )}
          </div>
        </div>

        <footer className="bottom">
          <div className="column">
            {error && (
              <div className="alert" role="alert">
                <span>{error}</span>
                <button className="icon" onClick={() => setError(null)} aria-label="Dismiss">✕</button>
              </div>
            )}
            {running && <StatusLine view={running.view} now={now} onStop={() => void cancel(running.id)} />}
            <Composer text={text} onText={setText} placeholder={placeholder} onSubmit={submit}
              disabled={Boolean(running) || Boolean(pending)}
              left={catalog && (
                <>
                  <ToolsMenu catalog={catalog} value={options} onChange={setOptions} />
                  <ModeMenu mode={mode} onChange={setMode} />
                </>
              )}
              right={catalog && <ModelMenu catalog={catalog} value={options} onChange={setOptions} />} />
          </div>
        </footer>
      </main>

      {runs.filter((r) => r.live).map((r) => <Follower key={r.id} run={r} onEvent={conversation.onEvent} />)}
      {inspected && <Inspector run={inspected} onClose={() => setInspecting(null)} />}
    </div>
  );
}
