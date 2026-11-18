// Local HTML -> PDF + reproducible browser screenshots. No remote writes.
const fs = require('fs');
const path = require('path');
const os = require('os');
const {pathToFileURL} = require('url');
const candidates = [process.env.PLAYWRIGHT_MODULE, 'playwright-core',
  path.join(os.homedir(), '.agents/skills/github-issue-tracker/scripts/node_modules/playwright-core')].filter(Boolean);
let chromium;
for (const candidate of candidates) {
  try { chromium = require(candidate).chromium; break; } catch (e) { if (e.code !== 'MODULE_NOT_FOUND') throw e; }
}
if (!chromium) throw new Error('Set PLAYWRIGHT_MODULE to an installed Playwright module');
(async () => {
  const file = path.resolve(process.argv[2]);
  const folder = path.dirname(file);
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1100}, deviceScaleFactor: 1});
    await page.goto(pathToFileURL(file).href);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({path: path.join(folder, 'desktop.png'), fullPage: true});
    await page.pdf({path: path.join(folder, 'report.pdf'), preferCSSPageSize: true, printBackground: true});
    const checks = [];
    for (const [name, width, height] of [['desktop', 1440, 1100], ['narrow', 390, 844]]) {
      await page.setViewportSize({width, height});
      await page.screenshot({path: path.join(folder, `${name}-top.png`)});
      for (const section of ['New', 'Handling', 'Handled']) {
        await page.locator(`#${section}`).evaluate(e => scrollTo(0, e.offsetTop));
        await page.screenshot({path: path.join(folder, `${name}-${section}.png`)});
      }
      checks.push(await page.evaluate(() => ({width: innerWidth, documentWidth: document.documentElement.scrollWidth,
        sections: [...document.querySelectorAll('.group')].map(e => ({name: e.id, rows: e.querySelectorAll('.case-row').length})),
        overflowCells: [...document.querySelectorAll('.cell, .mini td')].filter(e => e.scrollWidth > e.clientWidth + 1).length})));
      await page.evaluate(() => scrollTo(0, 0));
    }
    fs.writeFileSync(path.join(folder, 'browser-check.json'), JSON.stringify(checks, null, 2));
    console.log(JSON.stringify({pdf: path.join(folder, 'report.pdf'), checks}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
