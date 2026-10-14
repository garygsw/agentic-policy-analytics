"use client";
import { useEffect, useRef, useState } from "react";

import { reportUrl } from "@/lib/api";

/**
 * The HTML report, served by the API with its own Content-Security-Policy, in a sandboxed frame: its scripts
 * run (for the charts) but cannot reach this page or the API. The report sends its height, so the frame fits
 * it without a second scrollbar.
 */
export function ReportFrame({ runId }: { runId: string }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(640);

  useEffect(() => {
    const onMessage = (message: MessageEvent) => {
      if (message.source !== frame.current?.contentWindow || message.data?.type !== "report-height") return;
      const value = Number(message.data.height);
      if (Number.isFinite(value)) setHeight(Math.min(Math.max(value, 200), 40_000));
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
      style={{ height }}
    />
  );
}
