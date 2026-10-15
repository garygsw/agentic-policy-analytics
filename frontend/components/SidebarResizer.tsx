"use client";
import { useRef } from "react";

import { clampSidebarWidth, SIDEBAR_WIDTH } from "@/lib/layout";

interface Props {
  width: number;
  onResize: (width: number) => void; // while dragging
  onResizeEnd: (width: number) => void; // the width to remember
}

/** The sidebar's right edge: drag it to change the width, double-click to reset it, or focus it and use the arrow
 * keys. Wide screens only; narrow screens slide the sidebar over the page instead. */
export function SidebarResizer({ width, onResize, onResizeEnd }: Props) {
  const drag = useRef<{ startX: number; startWidth: number; width: number } | null>(null);
  return (
    <div className="sidebar-resizer" role="separator" aria-orientation="vertical" aria-label="Resize the sidebar"
      aria-valuemin={SIDEBAR_WIDTH.min} aria-valuemax={SIDEBAR_WIDTH.max} aria-valuenow={width} tabIndex={0}
      title="Drag to resize; double-click to reset"
      onPointerDown={(e) => {
        e.preventDefault(); // no text selection while dragging
        e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { startX: e.clientX, startWidth: width, width };
        document.documentElement.classList.add("resizing");
      }}
      onPointerMove={(e) => {
        if (!drag.current) return;
        drag.current.width = clampSidebarWidth(drag.current.startWidth + e.clientX - drag.current.startX);
        onResize(drag.current.width);
      }}
      onPointerUp={() => {
        if (!drag.current) return;
        onResizeEnd(drag.current.width);
        drag.current = null;
        document.documentElement.classList.remove("resizing");
      }}
      onDoubleClick={() => onResizeEnd(SIDEBAR_WIDTH.initial)}
      onKeyDown={(e) => {
        const step = e.key === "ArrowLeft" ? -16 : e.key === "ArrowRight" ? 16 : 0;
        if (!step) return;
        e.preventDefault();
        onResizeEnd(clampSidebarWidth(width + step));
      }} />
  );
}
