import assert from "node:assert/strict";
import test from "node:test";
import { cropPath, plateReasonLabel, sourceFrame } from "../src/cropEvidence.js";

test("thumbnail URLs must belong to the displayed run, track and crop kind", () => {
  const crop = { status: "available", url: "/api/t1/runs/run-a/tracks/2/crops/vehicle/" };
  assert.equal(cropPath("run-a", 2, "vehicle", crop), crop.url);
  assert.equal(cropPath("run-b", 2, "vehicle", crop), null);
  assert.equal(cropPath("run-a", 3, "vehicle", crop), null);
  assert.equal(cropPath("run-a", 2, "plate", crop), null);
  assert.equal(cropPath("run-a", 2, "vehicle", { ...crop, status: "unavailable" }), null);
});

test("missing plate crops retain evidence reasons instead of implying no detection", () => {
  for (const [reason, expected] of Object.entries({ no_plate_detected: "No plate detected", not_associated: "Not associated", crop_rejected: "Crop rejected" })) {
    assert.equal(plateReasonLabel({ diagnostic: { reason } }), expected);
  }
  assert.equal(plateReasonLabel({ status: "omitted" }), "Unknown");
  assert.equal(plateReasonLabel({ diagnostic: { reason: "failure_before_model" } }), "Unknown");
  assert.equal(sourceFrame(0), "Source frame 0");
  assert.equal(sourceFrame(undefined), "Source frame unknown");
});
