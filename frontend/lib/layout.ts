/** Sidebar width on wide screens: dragged by its edge, remembered in the browser. */
export const SIDEBAR_WIDTH = { min: 200, max: 480, initial: 264 };

export function clampSidebarWidth(width: number): number {
  if (!Number.isFinite(width)) return SIDEBAR_WIDTH.initial;
  return Math.round(Math.min(SIDEBAR_WIDTH.max, Math.max(SIDEBAR_WIDTH.min, width)));
}

/** How many conversations the sidebar lists at first, and adds each time "View more" is clicked. */
export const THREADS_PAGE = 15;

/** One page of the list, asked for with one extra row: the extra row only tells whether there are more. */
export function pageOf<T>(rows: T[], limit: number): { shown: T[]; more: boolean } {
  return { shown: rows.slice(0, limit), more: rows.length > limit };
}
