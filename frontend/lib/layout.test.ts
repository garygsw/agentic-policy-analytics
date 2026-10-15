import { describe, expect, it } from "vitest";

import { threadsPath } from "./api";
import { clampSidebarWidth, pageOf, SIDEBAR_WIDTH } from "./layout";

describe("sidebar layout", () => {
  it("keeps the width between its limits, and resets a broken saved value", () => {
    expect(clampSidebarWidth(100)).toBe(SIDEBAR_WIDTH.min);
    expect(clampSidebarWidth(900)).toBe(SIDEBAR_WIDTH.max);
    expect(clampSidebarWidth(300.4)).toBe(300);
    expect(clampSidebarWidth(Number.NaN)).toBe(SIDEBAR_WIDTH.initial);
  });

  it("shows a page and says whether more rows exist", () => {
    expect(pageOf([1, 2, 3], 2)).toEqual({ shown: [1, 2], more: true });
    expect(pageOf([1, 2], 2)).toEqual({ shown: [1, 2], more: false });
  });

  it("asks the API for one page of conversations with runs", () => {
    expect(threadsPath("", 16)).toBe("/api/threads?limit=16&with_runs=true");
    expect(threadsPath(" COE premiums ", 16)).toBe("/api/threads?limit=16&with_runs=true&q=COE+premiums");
  });
});
