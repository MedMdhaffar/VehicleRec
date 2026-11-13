const terminal = new Set(["completed", "stopped", "failed", "interrupted", "partial"]);
const cleanupKeys = ["decoder_closed", "models_closed", "redis_removed", "private_camera_removed", "native_children_exited"];

export function holdsActiveSlot(run) {
  return run && (run.active_slot ?? !terminal.has(run.status));
}

export function cleanupLabel(run) {
  if (!run) return "";
  if (run.cleanup && cleanupKeys.every((key) => run.cleanup[key] === true)) return "Resources released ✓";
  if (terminal.has(run.status) || run.cleanup) return "Cleanup needs attention";
  return "";
}
