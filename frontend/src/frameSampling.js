export const samplingDefaults = { mp4: 1, rtsp: 5 };

export function validInterval(value) {
  return ["string", "number"].includes(typeof value) && value !== "" && Number.isSafeInteger(Number(value)) && Number(value) >= 1;
}

export function samplingLabel(run) {
  return run?.sample_every ? `N=${run.sample_every}` : "historical";
}
