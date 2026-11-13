// Start through the real browser, then request launcher-owned shutdown.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const { chromium } = require(process.env.CONSOLE_TEST_PLAYWRIGHT);

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CONSOLE_TEST_CHROMIUM,
    headless: true, args: ["--no-sandbox"] });
  const result = { passed: false };
  try {
    const page = await browser.newPage();
    await page.goto("http://127.0.0.1:5173/");
    await page.waitForFunction(() => document.querySelector(".backend-state")?.textContent.includes("online"));
    await page.getByLabel("MP4 file").setInputFiles(path.join(process.cwd(), "vehicle/license_plate_service/run_video_pipeline/test_video.mp4"));
    const started = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/api/t1/runs/"));
    await page.getByRole("button", { name: "Start run" }).click();
    const response = await started;
    assert.equal(response.status(), 202);
    const run = await response.json();
    assert.equal(run.active_slot, true);
    result.runId = run.run_id;
    const stopped = await promisify(execFile)("bash", ["scripts/run_vehicle_console.sh", "--stop"], { timeout: 360000 });
    result.shutdownMessage = stopped.stderr.trim();
    const directory = path.join(process.cwd(), "vehicle/media/t1-console", run.run_id);
    const final = JSON.parse(fs.readFileSync(path.join(directory, "status.json")));
    assert.equal(final.status, "stopped");
    assert.equal(final.stop_requested, true);
    assert.ok(fs.existsSync(path.join(directory, "stop")));
    assert.ok(Object.values(final.cleanup).every((value) => value === true));
    const owner = JSON.parse(fs.readFileSync(path.join(directory, "owner.json")));
    let alive = false;
    try {
      const stat = fs.readFileSync(`/proc/${owner.pid}/stat`, "utf8").split(")").at(-1).trim().split(/\s+/);
      alive = stat[0] !== "Z" && stat[19] === owner.start_ticks;
    } catch (error) { if (error.code !== "ENOENT") throw error; }
    assert.equal(alive, false);
    result.final = final;
    result.originalWorkerExited = true;
    result.passed = true;
  } finally {
    fs.writeFileSync(path.join(process.env.CONSOLE_TEST_OUTPUT, "owned-shutdown.json"), JSON.stringify(result, null, 2));
    await browser.close();
  }
  console.log(JSON.stringify({ passed: result.passed, runId: result.runId, status: result.final?.status }));
})().catch((error) => { console.error(error.name+": owned shutdown acceptance failed");process.exitCode = 1; });
