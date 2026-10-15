"use client";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import { pageOf, THREADS_PAGE } from "./layout";
import { applyEvent, applyEvents, emptyRun, runStatus, type RunView } from "./runState";
import type { RunDetail, RunOptions, RunStatus, ThreadSummary, TraceEvent } from "./types";

export interface RunEntry {
  id: string;
  question: string;
  status: RunStatus; // as the API last reported it
  live: boolean; // the page is following its event stream
  view: RunView;
  detail: RunDetail | null; // the answer and LLM call log, loaded when the run ends or pauses
}

const FINISHED = new Set<RunStatus>(["completed", "failed", "cancelled", "rejected"]);

export function displayedStatus(run: RunEntry): RunStatus {
  if (run.live) return "running";
  if (FINISHED.has(run.status)) return run.status;
  return runStatus(run.view, run.status);
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The open conversation, its runs, and the actions on them. */
export function useConversation() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [openTitle, setOpenTitle] = useState<string | null>(null); // the open conversation may not be listed
  const [runs, setRuns] = useState<RunEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearchText] = useState(""); // the sidebar's search: words in titles or questions
  const [limit, setLimit] = useState(THREADS_PAGE); // how many conversations are listed ("View more" adds a page)
  const [hasMore, setHasMore] = useState(false);
  const listing = useRef({ search, limit });
  const latestList = useRef(0);

  const refreshThreads = useCallback(() => {
    const ticket = ++latestList.current; // a slower, older reply must not replace a newer list
    const { search, limit } = listing.current;
    api.threads(search, limit + 1)
      .then((rows) => {
        if (ticket !== latestList.current) return;
        const { shown, more } = pageOf(rows, limit);
        setThreads(shown);
        setHasMore(more);
      })
      .catch((e) => setError(messageOf(e)));
  }, []);
  useEffect(() => {
    listing.current = { search, limit };
    const timer = setTimeout(refreshThreads, search ? 200 : 0); // wait for a pause in typing
    return () => clearTimeout(timer);
  }, [search, limit, refreshThreads]);

  const setSearch = useCallback((text: string) => {
    setSearchText(text);
    setLimit(THREADS_PAGE); // a new search starts from its first page
  }, []);
  const showMore = useCallback(() => setLimit((current) => current + THREADS_PAGE), []);

  const update = useCallback((id: string, change: (run: RunEntry) => RunEntry) => {
    setRuns((previous) => previous.map((run) => (run.id === id ? change(run) : run)));
  }, []);

  const loadDetail = useCallback((id: string) => {
    api.run(id)
      .then((detail) => update(id, (run) => ({ ...run, detail, status: detail.status })))
      .catch((e) => setError(messageOf(e)));
  }, [update]);

  const onEvent = useCallback((id: string, event: TraceEvent) => {
    update(id, (run) => ({ ...run, view: applyEvent(run.view, event), live: event.kind === "end" ? false : run.live }));
    if (event.kind === "end") {
      loadDetail(id);
      refreshThreads();
    }
  }, [update, loadDetail, refreshThreads]);

  const openThread = useCallback(async (id: string) => {
    setError(null);
    try {
      const thread = await api.thread(id);
      const entries = await Promise.all(thread.runs.map(async (summary): Promise<RunEntry> => {
        const [events, detail] = await Promise.all([api.events(summary.id), api.run(summary.id)]);
        return {
          id: summary.id, question: summary.question, status: detail.status, live: detail.status === "running",
          view: applyEvents(emptyRun(), events), detail,
        };
      }));
      setThreadId(id);
      setOpenTitle(thread.title);
      setRuns(entries);
    } catch (e) {
      setError(messageOf(e));
    }
  }, []);

  const newThread = useCallback(() => {
    setThreadId(null);
    setOpenTitle(null);
    setRuns([]);
    setError(null);
  }, []);

  /** Rename or pin a conversation in the history list. */
  const updateThread = useCallback(async (id: string, change: { title?: string; pinned?: boolean }) => {
    setError(null);
    try {
      const updated = await api.updateThread(id, change);
      if (id === threadId) setOpenTitle(updated.title);
    } catch (e) {
      setError(messageOf(e));
    }
    refreshThreads();
  }, [threadId, refreshThreads]);

  /** Delete a conversation (the backend keeps it and can restore it); if it is open, the page starts a new one. */
  const deleteThread = useCallback(async (id: string) => {
    setError(null);
    try {
      await api.deleteThread(id);
      if (id === threadId) newThread();
    } catch (e) {
      setError(messageOf(e));
    }
    refreshThreads();
  }, [threadId, newThread, refreshThreads]);

  /** Ask a question; returns whether it was accepted (a refused message stays in the box to edit). */
  const ask = useCallback(async (text: string, options: RunOptions, planApproval: boolean) => {
    setError(null);
    let id = threadId;
    try {
      id = id ?? (await api.createThread()).id;
      const { run_id } = await api.send(id, text, options, planApproval);
      const entry: RunEntry = { id: run_id, question: text, status: "running", live: true, view: emptyRun(), detail: null };
      setRuns((previous) => [...(id === threadId ? previous : []), entry]);
      return true;
    } catch (e) {
      setError(messageOf(e));
      return false;
    } finally {
      if (id !== threadId) setThreadId(id); // a new conversation is kept even if its first message was refused
      refreshThreads();
    }
  }, [threadId, refreshThreads]);

  const resumed = useCallback((id: string) => {
    update(id, (run) => ({ ...run, live: true, status: "running" }));
  }, [update]);

  const answer = useCallback(async (runId: string, text: string, options: RunOptions) => {
    setError(null);
    try {
      await api.answer(runId, text, options);
      resumed(runId);
      return true;
    } catch (e) {
      setError(messageOf(e));
      return false;
    }
  }, [resumed]);

  const decidePlan = useCallback(async (runId: string, approved: boolean, feedback: string, options: RunOptions) => {
    setError(null);
    try {
      await api.decidePlan(runId, approved, feedback, options);
      resumed(runId);
    } catch (e) {
      setError(messageOf(e));
    }
  }, [resumed]);

  /** Turn a proposed plan down: the run stops, keeping the plan so it can be reopened. */
  const rejectPlan = useCallback(async (runId: string) => {
    setError(null);
    try {
      await api.rejectPlan(runId);
      const events = await api.events(runId);
      update(runId, (run) => ({ ...run, status: "rejected", view: applyEvents(run.view, events) }));
      loadDetail(runId);
    } catch (e) {
      setError(messageOf(e));
    }
  }, [update, loadDetail]);

  const cancel = useCallback(async (runId: string) => {
    try {
      const result = await api.cancel(runId);
      if (result.status === "cancelled") {
        // A paused run is closed at once; its end event is fetched with the rest.
        const events = await api.events(runId);
        update(runId, (run) => ({ ...run, view: applyEvents(run.view, events) }));
        loadDetail(runId);
        refreshThreads();
      }
    } catch (e) {
      setError(messageOf(e));
    }
  }, [update, loadDetail, refreshThreads]);

  const title = threads.find((t) => t.id === threadId)?.title ?? openTitle;

  return {
    title, threads, hasMore, showMore, threadId, runs, error, setError, search, setSearch, openThread, newThread,
    updateThread, deleteThread, ask, answer, decidePlan, rejectPlan, cancel, onEvent,
  };
}
