const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require(process.env.CONSOLE_TEST_PLAYWRIGHT);

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CONSOLE_TEST_CHROMIUM,
    headless: true, args: ["--no-sandbox"] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
    page.setDefaultTimeout(15000);
    const urls = [], logs = [], bodies = [];
    let privateBodies = 0;
    page.on("request", (request) => {
      urls.push(request.url());
      if (request.method() === "POST" && request.postData()?.includes("browser-private-secret")) privateBodies++;
    });
    page.on("console", (message) => logs.push(message.text()));
    page.on("response", (response) => {
      if (response.url().includes("/api/t1/runs/") && response.headers()["content-type"]?.includes("application/json")) {
        response.text().then((body) => bodies.push(body)).catch(() => {});
      }
    });
    const api = async () => page.evaluate(async () => (await (await fetch("/api/t1/runs/")).json()).runs);
    const waitRun = async (state) => page.waitForFunction(async (wanted) => {
      const runs = (await (await fetch("/api/t1/runs/")).json()).runs;
      return runs[0]?.status === wanted && runs[0]?.active_slot === false;
    }, state);
    const waitEnabled = async () => page.waitForFunction(() => !document.querySelector(".start-button").disabled);
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.getByLabel("MP4 file").setInputFiles({ name: "controlled.mp4", mimeType: "video/mp4", buffer: Buffer.from("controlled fixture") });
    try { await waitEnabled(); }
    catch (error) {
      console.error(JSON.stringify({ phase: "initial backend connection", logs, urls, bodies,
        backend: await page.locator(".backend-state").innerText() }));
      throw error;
    }
    await page.getByRole("button", { name: "Start run" }).click();
    await page.waitForFunction(() => !document.querySelector(".stop-button").disabled);
    assert.equal(await page.getByRole("button", { name: "Start run" }).isDisabled(), true);
    const duplicate = await page.evaluate(async () => (await fetch("/api/t1/runs/", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_kind: "rtsp", rtsp_uri: "rtsp://127.0.0.1:9/duplicate" }),
    })).status);
    assert.equal(duplicate, 409);
    await page.getByRole("button", { name: "Stop", exact: false }).click();
    await waitRun("stopped");await waitEnabled();
    await page.getByText("Resources released ✓", { exact: true }).waitFor();

    await page.getByRole("tab", { name: "RTSP camera" }).click();
    for (const index of [1, 2]) {
      await page.getByLabel("RTSP URI").fill(`rtsp://user:browser-private-secret@127.0.0.1:9/camera-${index}`);
      await page.getByRole("button", { name: "Start run" }).click();
      await page.waitForFunction(() => !document.querySelector(".stop-button").disabled);
      assert.equal(await page.getByLabel("RTSP URI").inputValue(), "");
      if (index === 1) {
        await page.waitForFunction(async () => {
          const run = (await (await fetch("/api/t1/runs/")).json()).runs[0];
          return run.status === "completed" && run.active_slot === true;
        });
        await page.waitForFunction(() => document.querySelector(".run-badge").textContent === "Completed");
        assert.equal(await page.getByRole("button", { name: "Start run" }).isDisabled(), true);
      }
      await page.getByRole("button", { name: "Stop", exact: false }).click();
      await waitRun(index === 1 ? "stopped" : "interrupted");await waitEnabled();
    }
    await page.getByText("Cleanup needs attention", { exact: true }).waitFor();
    const runs = await api();
    assert.equal(runs.length, 3);
    assert.equal(privateBodies, 2);
    assert.equal(urls.some((value) => value.includes("browser-private-secret") || value.includes("rtsp:")), false);
    assert.equal(bodies.some((value) => value.includes("browser-private-secret") || value.includes("rtsp://")), false);
    assert.equal(JSON.stringify(runs).includes("browser-private-secret"), false);
    assert.equal(logs.some((value) => value.includes("browser-private-secret")), false);
    assert.equal(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]).includes("browser-private-secret")), false);
    await page.screenshot({ path: process.env.CONSOLE_TEST_SCREENSHOT, fullPage: true });
    const result = { passed: true, runs: runs.map(({ status, active_slot, cleanup }) => ({ status, active_slot, cleanup })),
      repeatStart: duplicate, privatePostBodies: privateBodies, secretsInUrls: false, secretsInStatus: false,
      secretsInConsole: false, secretsInStorage: false, terminalWorkerKeptStartDisabled: true,
      hardExitCleanupAttentionVisible: true, source: "actual console API with controlled pipe workers; no inference" };
    fs.writeFileSync(process.env.CONSOLE_TEST_RESULT, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
