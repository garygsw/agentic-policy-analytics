"use client";
import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
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

const FINISHED = new Set<RunStatus>(["completed", "failed", "cancelled"]);

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
  const [runs, setRuns] = useState<RunEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refreshThreads = useCallback(() => {
    api.threads().then(setThreads).catch((e) => setError(messageOf(e)));
  }, []);
  useEffect(refreshThreads, [refreshThreads]);

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
      setRuns(entries);
    } catch (e) {
      setError(messageOf(e));
    }
  }, []);

  const newThread = useCallback(() => {
    setThreadId(null);
    setRuns([]);
    setError(null);
  }, []);

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

  return {
    threads, threadId, runs, error, setError, openThread, newThread, ask, answer, decidePlan, cancel, onEvent,
  };
}
