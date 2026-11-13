// Read-only browser verification; no footage, camera endpoint or new run.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require(process.env.CONSOLE_TEST_PLAYWRIGHT);
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CONSOLE_TEST_CHROMIUM, headless: true, args: ["--no-sandbox"] });
  try {
    const page = await browser.newPage();
    let mutations = 0;
    page.on("request", request => { if (request.method() !== "GET") mutations++; });
    await page.goto("http://127.0.0.1:5173");
    await page.waitForFunction(() => document.querySelector(".backend-state")?.textContent.includes("online"));
    assert.ok((await page.getByLabel("Stack readiness").innerText()).includes("Full stack ready"));
    const control = page.getByLabel("Analyze every Nth frame");
    assert.equal(await control.inputValue(), "1");
    assert.equal(await control.getAttribute("max"), null);
    assert.equal(await page.getByLabel("Timeout", {exact:true}).inputValue(), "");
    assert.equal(await page.getByRole("switch").isChecked(), false);
    for (const invalid of ["0", "1.5", ""]) {
      await control.fill(invalid);
      assert.equal(await control.evaluate(node => node.checkValidity()), false);
    }
    await control.fill("9");
    assert.equal(await control.evaluate(node => node.checkValidity()), true);
    await page.getByRole("tab", {name:"RTSP camera"}).click();
    assert.equal(await control.inputValue(), "5");
    assert.equal(await page.getByLabel("RTSP URI").inputValue(), "");
    await page.getByRole("tab", {name:"Recorded MP4"}).click();
    assert.equal(await control.inputValue(), "9");
    await page.getByRole("combobox", {name:"Run history"}).selectOption("7c4521cb-f403-48b6-aa62-a57cfdf8337c");
    const counters = await page.getByLabel("Frame counters").innerText();
    assert.ok(counters.includes("Decoded: 21") && counters.includes("Analyzed: 21") && counters.includes("Full video complete"));
    const headers = await page.locator("th").allTextContents();
    assert.ok(headers.includes("PostgreSQL commit") && headers.includes("Redis DB1 ACK"));
    const preview=page.locator(".preview-stage img");
    await preview.waitFor();
    await page.waitForFunction(() => document.querySelector(".preview-stage img")?.naturalWidth > 0);
    assert.equal(mutations, 0);
    fs.writeFileSync(process.env.CONSOLE_TEST_OUTPUT, JSON.stringify({passed:true,defaults:{mp4:1,rtsp:5},invalidRejected:["0","1.5","empty"],noDefaultTimeout:true,manufacturerOff:true,separateCommitAndAck:true,historyAndPreview:true,mutations},null,2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error);process.exitCode=1; });
