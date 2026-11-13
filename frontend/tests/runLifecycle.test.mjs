import assert from "node:assert/strict";
import test from "node:test";
import { cleanupLabel, holdsActiveSlot } from "../src/runLifecycle.js";

test("terminal projection holds admission until the backend releases ownership", () => {
  assert.equal(holdsActiveSlot({ status: "completed", active_slot: true }), true);
  assert.equal(holdsActiveSlot({ status: "interrupted", active_slot: true }), true);
  assert.equal(holdsActiveSlot({ status: "completed", active_slot: false }), false);
  assert.equal(holdsActiveSlot({ status: "stopping" }), true);
});

test("unknown, empty and incomplete terminal cleanup needs attention", () => {
  for (const status of ["completed", "stopped", "failed", "interrupted", "partial"]) {
    for (const cleanup of [null, undefined, {}, { redis_removed: true }, { redis_removed: false }]) {
      assert.equal(cleanupLabel({ status, cleanup }), "Cleanup needs attention");
    }
  }
  assert.equal(cleanupLabel({ status: "starting", cleanup: null }), "");
  assert.equal(cleanupLabel(null), "");
});

test("resources released requires every cleanup guarantee to be explicitly true", () => {
  const cleanup = { decoder_closed: true, models_closed: true, redis_removed: true,
    private_camera_removed: true, native_children_exited: true };
  assert.equal(cleanupLabel({ status: "stopped", cleanup }), "Resources released ✓");
  assert.equal(cleanupLabel({ status: "stopped", cleanup: { ...cleanup, redis_removed: "unknown" } }), "Cleanup needs attention");
});
