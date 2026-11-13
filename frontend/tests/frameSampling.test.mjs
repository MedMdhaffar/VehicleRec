import assert from "node:assert/strict";
import test from "node:test";
import { samplingDefaults, validInterval, samplingLabel } from "../src/frameSampling.js";

test("source modes have independent defaults and no old eight-frame ceiling", () => {
  assert.deepEqual(samplingDefaults, { mp4: 1, rtsp: 5 });
  for (const value of [1, "5", 21, 1000]) assert.equal(validInterval(value), true);
  for (const value of ["", 0, -1, 1.5, NaN, Infinity, true, null, undefined]) assert.equal(validInterval(value), false);
});

test("history labels use the stored interval without inventing one for old runs", () => {
  assert.equal(samplingLabel({ sample_every: 5 }), "N=5");
  assert.equal(samplingLabel({ max_frames: 4 }), "historical");
});
