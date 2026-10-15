import { describe, expect, it } from "vitest";

import { sidebarSections } from "./threads";
import type { ThreadSummary } from "./types";

const thread = (id: string, pinned: boolean, runs = 1): ThreadSummary =>
  ({ id, title: id, pinned, runs, updated_at: "", last_status: "completed" });

describe("sidebarSections", () => {
  it("lists pinned conversations apart, keeps the order and hides empty ones", () => {
    const { pinned, recent } = sidebarSections([thread("a", false), thread("b", true), thread("c", false, 0),
      thread("d", false), thread("e", true)]);
    expect(pinned.map((t) => t.id)).toEqual(["b", "e"]);
    expect(recent.map((t) => t.id)).toEqual(["a", "d"]);
  });
});
