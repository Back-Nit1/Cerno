import { describe, expect, it } from "vitest";
import { countByStatus, progressSteps } from "./session-view";
import type { Verdict } from "@/types";

describe("progressSteps", () => {
  it("marks all stages pending except the first while capturing", () => {
    expect(progressSteps("capturing").map((s) => s.state)).toEqual(["active", "pending", "pending", "pending"]);
  });

  it("marks every stage done once complete", () => {
    expect(progressSteps("complete").every((s) => s.state === "done")).toBe(true);
  });
});

describe("countByStatus", () => {
  it("tallies verdicts by status, defaulting missing statuses to zero", () => {
    const verdicts = [{ status: "VERIFIED" }, { status: "VIOLATED" }, { status: "VERIFIED" }] as Verdict[];
    expect(countByStatus(verdicts)).toEqual({ VERIFIED: 2, LIKELY: 0, UNCERTAIN: 0, VIOLATED: 1 });
  });
});
