// Native-model RTSP sampling/preview/Stop through the single-port console.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.CONSOLE_TEST_PLAYWRIGHT);
const origin = `http://127.0.0.1:${process.env.WAYCON_WEB_PORT || "5173"}`;
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CONSOLE_TEST_CHROMIUM, headless: true, args: ["--no-sandbox"] });
  const result = { passed: false, source: "owned_loopback", realCamera: "not_tested", runs: [], requests: [] };
  const uri = fs.readFileSync(process.env.CONSOLE_TEST_RTSP_FILE, "utf8").trim();
  try {
    const page = await browser.newPage();
    await page.route(`http://127.0.0.1:${process.env.WAYCON_API_PORT || "8002"}/**`, route => route.abort("connectionrefused"));
    page.on("request", request => result.requests.push({ method: request.method(), url: request.url() }));
    await page.goto(origin);
    await page.waitForFunction(() => document.querySelector(".backend-state")?.textContent.includes("online"));
    assert.ok((await page.getByLabel("Stack readiness").innerText()).includes("Full stack ready"));
    await page.getByRole("tab", { name: "RTSP camera" }).click();
    assert.equal(await page.getByLabel("Analyze every Nth frame").inputValue(), "5");
    for (const every of (process.env.CONSOLE_TEST_RTSP_MODE === "stop" ? [5] : [1, 5])) {
      await page.getByLabel("RTSP transport").selectOption("udp");
      assert.equal(await page.getByLabel("RTSP URI").inputValue(), "");
      await page.getByLabel("RTSP URI").fill(uri);
      await page.getByLabel("Analyze every Nth frame").fill(String(every));
      const started = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/api/t1/runs/"));
      await page.getByRole("button", { name: "Start run" }).click();
      const response = await started;assert.equal(response.status(), 202);
      const runId = (await response.json()).run_id;
      assert.equal(await page.getByLabel("RTSP URI").inputValue(), "");
      const record = { runId, sampleEvery: every, stopRequested: false, previewLoaded: false };
      result.runs.push(record);
      const deadline = Date.now()+360000;
      let final;
      let firstRevision;
      while (Date.now() < deadline) {
        const status = await (await page.request.get(`${origin}/api/t1/runs/${runId}/`)).json();
        assert.equal(JSON.stringify(status).includes(uri), false);
        const image = page.locator(".preview-stage img");
        if (status.preview_revision > 0 && await image.count()) {
          record.previewLoaded = await image.evaluate(node => node.complete && node.naturalWidth > 0);
          firstRevision ??= status.preview_revision;
          if (firstRevision !== status.preview_revision) record.livePreviewAdvanced = true;
          if (status.analyzed_frames >= 2 && record.previewLoaded && !record.stopRequested) {
            await page.getByRole("button", { name: /Stop$/ }).click();record.stopRequested = true;
          }
        }
        if (!status.active_slot) { final=status;break; }
        await page.waitForTimeout(150);
      }
      assert.ok(final);assert.equal(final.status, "stopped");assert.equal(final.end_reason,"controlled_stop");
      assert.equal(final.full_video_complete,false);assert.ok(record.stopRequested && record.previewLoaded && record.livePreviewAdvanced);
      assert.ok(Object.values(final.cleanup).every(value => value === true));
      const directory=path.join(process.env.T1_CONSOLE_RUN_ROOT || path.join(process.cwd(), "vehicle/media/t1-console"), runId);
      const evidence=fs.readFileSync(path.join(directory,"analyzed-source.jsonl"),"utf8").trim().split("\n").map(JSON.parse);
      const indexes=evidence.map(entry=>entry.frame_index);
      assert.equal(indexes[0],0);assert.ok(indexes.every(index=>index % every===0));
      assert.equal(new Set(indexes).size,indexes.length);assert.ok(evidence.every(entry=>entry.connection_id===runId));
      const runtime=JSON.parse(fs.readFileSync(path.join(directory,"native-runtime.json")));
      assert.deepEqual(runtime.vehicle.parameter_devices,[process.env.T1_CONSOLE_DEVICE || "cuda:0"]);
      if (process.env.WAYCON_EXPECTED_GPU) assert.ok(runtime.vehicle.gpu_name.includes(process.env.WAYCON_EXPECTED_GPU));
      assert.equal(runtime.initial_tracking.frame_id,0);assert.equal(runtime.initial_tracking.tracked,0);assert.equal(runtime.initial_tracking.lost,0);
      assert.equal(fs.existsSync(JSON.parse(fs.readFileSync(path.join(directory,"config.json"))).source),false);
      record.final={ status:final.status,end_reason:final.end_reason,decoded_frames:final.decoded_frames,analyzed_frames:final.analyzed_frames,
        sampling_skips:final.sampling_skips,overload_drops:final.overload_drops,delivery:final.delivery,cleanup:final.cleanup };
      record.analyzedSourceIndexes=indexes;record.receipts=evidence;record.initialTracking=runtime.initial_tracking;
      record.privateCameraRemoved=true;
      await page.waitForFunction(() => !document.querySelector(".start-button").disabled);
      console.log(JSON.stringify({ runId,every,indexes,status:final.status,decoded:final.decoded_frames,drops:final.overload_drops }));
    }
    assert.equal(new Set(result.runs.map(run=>run.runId)).size,result.runs.length);
    const storage=await page.evaluate(()=>JSON.stringify({local:{...localStorage},session:{...sessionStorage}}));
    assert.equal(storage.includes(uri),false);assert.equal(JSON.stringify(result.requests).includes(uri),false);
    assert.ok(result.requests.every(request=>new URL(request.url).origin===origin));
    result.privacyPassed=true;result.passed=true;
  } finally {
    fs.writeFileSync(path.join(process.env.CONSOLE_TEST_OUTPUT,"browser-rtsp.json"),JSON.stringify(result,null,2));
    await browser.close();
  }
})().catch(error=>{console.error(error.name+": owned RTSP sampling acceptance failed");process.exitCode=1;});
