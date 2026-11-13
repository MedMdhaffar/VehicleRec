export function cropPath(runId, trackId, kind, crop) {
  const expected = `/api/t1/runs/${runId}/tracks/${trackId}/crops/${kind}/`;
  return crop?.status === "available" && crop.url === expected ? expected : null;
}

export function plateReasonLabel(plate) {
  const reasons = { no_plate_detected: "No plate detected", not_associated: "Not associated", crop_rejected: "Crop rejected" };
  return reasons[plate?.diagnostic?.reason] || "Unknown";
}

export function sourceFrame(frame) {
  return Number.isInteger(frame) && frame >= 0 ? `Source frame ${frame}` : "Source frame unknown";
}
