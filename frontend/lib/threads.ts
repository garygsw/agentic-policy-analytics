import type { ThreadSummary } from "./types";

/** The history list: pinned conversations first, then the rest (newest first, as the API orders them).
 * Conversations whose first message was refused (no runs) are not listed. */
export function sidebarSections(threads: ThreadSummary[]): { pinned: ThreadSummary[]; recent: ThreadSummary[] } {
  const shown = threads.filter((t) => t.runs > 0);
  return { pinned: shown.filter((t) => t.pinned), recent: shown.filter((t) => !t.pinned) };
}
