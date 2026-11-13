// Real console proxy acceptance. MP4 mode opts into existing local CV models.
// No camera, screenshot, footage copy, raw plate text or production DB writes.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { chromium } = require(process.env.CONSOLE_TEST_PLAYWRIGHT);

(async () => {
  const result = { passed: false, mode: process.env.CONSOLE_PROXY_MODE || "health",
    browserUrl: "http://127.0.0.1:5173/", directApiBlockedForSingleTunnelSimulation: true,
    requests: [], failures: [], observations: [] };
  const browser = await chromium.launch({ executablePath: process.env.CONSOLE_TEST_CHROMIUM,
    headless: true, args: ["--no-sandbox"] });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(20000);
    // Model a browser whose only forwarded VM port is 5173.
    await page.route("http://127.0.0.1:8002/**", (route) => route.abort("connectionrefused"));
    page.on("request", (request) => {
      if (request.url().includes("/api/t1/runs/")) result.requests.push({ method: request.method(), url: request.url() });
    });
    page.on("requestfailed", (request) => {
      if (request.url().includes("/api/t1/runs/")) result.failures.push({ url: request.url(), error: request.failure()?.errorText });
    });
    await page.goto(result.browserUrl);
    await page.waitForFunction(() => document.querySelector(".backend-state")?.textContent.includes("online"), null, { timeout: 5000 });
    // React StrictMode deliberately aborts the first mount's GET in development.
    assert.equal(result.failures.filter((failure) => failure.error !== "net::ERR_ABORTED").length, 0);
    assert.ok(result.requests.length > 0);
    assert.ok(result.requests.every(({ url }) => new URL(url).origin === new URL(result.browserUrl).origin));
    const list = await page.request.get("http://127.0.0.1:5173/api/t1/runs/");
    result.listHttpStatus = list.status();
    assert.equal(list.status(), 200);
    if (result.mode === "mp4") {
      assert.equal(await page.getByRole("switch").isChecked(), false);
      assert.equal(await page.getByLabel("Analyze every Nth frame").inputValue(), "1");
      await page.getByLabel("MP4 file").setInputFiles(process.env.CONSOLE_TEST_MP4);
      await page.waitForFunction(() => !document.querySelector(".start-button").disabled);
      const started = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/api/t1/runs/"));
      await page.getByRole("button", { name: "Start run" }).click();
      const response = await started;
      result.startHttpStatus = response.status();
      assert.equal(response.status(), 202);
      const start = await response.json();
      result.runId = start.run_id;
      result.source = path.relative(process.cwd(), process.env.CONSOLE_TEST_MP4);
      result.manufacturerEnabled = start.manufacturer_enabled;
      assert.equal(start.manufacturer_enabled, false);
      let final;
      let signature;
      const deadline = Date.now() + 1800000;
      while (Date.now() < deadline) {
        const response = await page.request.get(`http://127.0.0.1:5173/api/t1/runs/${result.runId}/`);
        assert.equal(response.status(), 200);
        const status = await response.json();
        const sample = { status: status.status, frames: status.frames, previewRevision: status.preview_revision,
          activeSlot: status.active_slot, errorCode: status.error_code };
        if (JSON.stringify(sample) !== signature) {
          result.observations.push(sample);signature = JSON.stringify(sample);
          console.log(JSON.stringify(sample));
        }
        if (["completed", "stopped", "failed", "interrupted"].includes(status.status) && !status.active_slot) { final = status;break; }
        await page.waitForTimeout(250);
      }
      assert.ok(final, "Run did not settle within 300 seconds");
      result.final = { status: final.status, frames: final.frames, sampleEvery: final.sample_every, endReason: final.end_reason, fullVideoComplete: final.full_video_complete,
        previewRevision: final.preview_revision, activeSlot: final.active_slot, cleanup: final.cleanup,
        delivery: final.delivery, counters: final.counters,
        rows: final.rows.map((row) => ({ trackId: row.track_id, type: row.vehicle_type,
          colour: row.colour, plateDecision: row.plate, manufacturer: row.manufacturer.status })) };
      assert.equal(final.status, "completed");
      assert.equal(final.full_video_complete, true);
      assert.equal(final.end_reason, "eof");
      assert.equal(final.analyzed_frames, final.decoded_frames);
      assert.equal(final.counters.manufacturer_calls, 0);
      assert.ok(Object.values(final.cleanup).every((value) => value === true));
      assert.equal(final.delivery.postgres_commit, process.env.CONSOLE_TEST_DB1 === "1" ? "committed" : "not_configured");
      assert.equal(final.delivery.redis_ack, process.env.CONSOLE_TEST_DB1 === "1" ? "acknowledged" : "not_attempted");
      if (process.env.CONSOLE_TEST_DB1 === "1") {
        const progress = JSON.parse(fs.readFileSync(path.join(process.cwd(), "vehicle/media/t1-console", result.runId, "db1-progress.json")));
        const source = fs.readFileSync(process.env.CONSOLE_TEST_MP4);
        const sourceSha256 = crypto.createHash("sha256").update(source).digest("hex");
        assert.equal(progress.status, "completed");
        assert.equal(progress.evidence.length, final.rows.length);
        assert.ok(progress.evidence.every((entry) => entry.exact_entry_pending && entry.ack_reply === 1 &&
          entry.source_kind === "file" && entry.before_ack.source_media.sha256 === sourceSha256 && entry.before_ack.source_media.bytes === source.length));
        result.databaseEvidence = progress;
        result.uploadSha256 = sourceSha256;
      }
      assert.ok(final.rows.length > 0);
      assert.ok(final.rows.every((row) => row.manufacturer.status === "disabled"));
      if (process.env.CONSOLE_TEST_CROPS === "1") {
        result.cropEvidence = final.rows.map((row) => ({ trackId: row.track_id,
          vehicle: row.vehicle_crop, plate: row.plate.crop, diagnostic: row.plate.diagnostic }));
        assert.ok(final.rows.every((row) => row.vehicle_crop?.status === "available"));
        assert.ok(final.rows.some((row) => row.plate.crop?.status === "available"), "Known-good MP4 must retain a selected plate crop");
        for (const row of final.rows) {
          for (const [kind, crop] of [["vehicle", row.vehicle_crop], ["plate", row.plate.crop]]) {
            if (crop?.status !== "available") continue;
            assert.equal(crop.url, `/api/t1/runs/${result.runId}/tracks/${row.track_id}/crops/${kind}/`);
            assert.ok(Number.isInteger(crop.frame));
            const image = await page.request.get(new URL(crop.url, result.browserUrl).href);
            assert.equal(image.status(), 200);
            assert.ok(image.headers()["content-type"].startsWith("image/png"));
            const displayed = page.locator(`tr[data-track-id="${row.track_id}"] .track-crop.${kind} img`);
            await displayed.waitFor();await displayed.scrollIntoViewIfNeeded();
            await page.waitForFunction((selector) => {
              const image = document.querySelector(selector);
              return image && image.complete && image.naturalWidth > 0;
            }, `tr[data-track-id="${row.track_id}"] .track-crop.${kind} img`);
          }
        }
        result.cropImagesLoadedInBrowser = true;
      }
      await page.waitForFunction((runId) => {
        const image = document.querySelector(".preview-stage img");
        return image?.src.includes(runId) && image.complete && image.naturalWidth > 0;
      }, result.runId);
      result.previewLoadedInBrowser = true;
      if (process.env.CONSOLE_TEST_SCREENSHOT) {
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: process.env.CONSOLE_TEST_SCREENSHOT, fullPage: true });
      }
      await page.waitForFunction(() => !document.querySelector(".start-button").disabled);
      result.freshStartEnabled = true;
      const owner = JSON.parse(fs.readFileSync(path.join(process.cwd(), "vehicle/media/t1-console", result.runId, "owner.json"), "utf8"));
      try {
        const stat = fs.readFileSync(`/proc/${owner.pid}/stat`, "utf8").split(")").at(-1).trim().split(/\s+/);
        result.originalWorkerExited = stat[19] !== owner.start_ticks || stat[0] === "Z";
      } catch (error) { if (error.code !== "ENOENT") throw error;result.originalWorkerExited = true; }
      assert.equal(result.originalWorkerExited, true);
      assert.equal(result.failures.filter((failure) => failure.error !== "net::ERR_ABORTED").length, 0);
      assert.ok(result.requests.every(({ url }) => new URL(url).origin === new URL(result.browserUrl).origin));
    }
    result.passed = true;
  } catch (error) {
    result.error = { name: error.name, message: error.message };
    throw error;
  } finally {
    fs.writeFileSync(process.env.CONSOLE_TEST_RESULT, JSON.stringify(result, null, 2));
    await browser.close();
  }
  console.log(JSON.stringify({ passed: result.passed, mode: result.mode, runId: result.runId,
    startHttpStatus: result.startHttpStatus, listHttpStatus: result.listHttpStatus,
    finalStatus: result.final?.status, frames: result.final?.frames,
    networkFailures: result.failures.filter((failure) => failure.error !== "net::ERR_ABORTED").length }));
})().catch((error) => { console.error(error);process.exitCode = 1; });
