"use client";
import { useEffect, useRef, useState } from "react";

import { reportUrl } from "@/lib/api";

/**
 * The HTML report, served by the API with its own Content-Security-Policy, in a sandboxed frame: its scripts
 * run (for the charts) but cannot reach this page or the API. The report sends its height, so the frame fits
 * it with no scrollbar of its own; and when a citation is clicked, it sends the target's position, so this page
 * scrolls the conversation there.
 */
export function ReportFrame({ runId }: { runId: string }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(640);

  useEffect(() => {
    const onMessage = (message: MessageEvent) => {
      const el = frame.current;
      if (!el || message.source !== el.contentWindow) return;
      const value = Number(message.data?.height ?? message.data?.top);
      if (!Number.isFinite(value)) return;
      if (message.data?.type === "report-height") setHeight(Math.min(Math.max(value, 200), 40_000) + 4);
      if (message.data?.type === "report-scroll") {
        const scroller = el.closest(".scroll");
        if (!scroller) return;
        const frameTop = el.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
        scroller.scrollTo({ top: frameTop + value - 80, behavior: "smooth" });
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  return (
    <iframe
      ref={frame}
      className="report-frame"
      title="Report"
      src={reportUrl(runId)}
      sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"
      scrolling="no"
      style={{ height }}
    />
  );
}
