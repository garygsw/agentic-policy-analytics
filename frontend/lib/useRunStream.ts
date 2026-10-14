"use client";
import { useEffect, useRef } from "react";

import { streamUrl } from "./api";
import type { TraceEvent } from "./types";

/**
 * Follows a run over its WebSocket while `follow` is true. The server first replays the events after
 * `after`, then sends live ones, and closes once the run ends or pauses. If the connection drops before
 * that, this reconnects from the last event received, so nothing is missed or shown twice.
 */
export function useRunStream(runId: string, after: number, follow: boolean, onEvent: (e: TraceEvent) => void) {
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  });

  useEffect(() => {
    if (!follow) return;
    let last = after; // read when following starts; later events advance it
    let socket: WebSocket | null = null;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      let ended = false;
      socket = new WebSocket(streamUrl(runId, last));
      socket.onmessage = (message) => {
        const event = JSON.parse(message.data) as TraceEvent;
        if (typeof event.seq !== "number") return; // e.g. "unknown run"
        last = Math.max(last, event.seq);
        if (event.kind === "end") ended = true;
        handler.current(event);
      };
      socket.onclose = () => {
        if (!stopped && !ended) retry = setTimeout(connect, 1000);
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      socket?.close();
    };
    // `after` is deliberately not a dependency: it is only the starting point when following begins.
  }, [runId, follow]);
}
