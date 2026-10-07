// Synthetic popup QA. Never opens an audio device or starts a native recording.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

(async () => {
  const root = path.resolve(__dirname, '../../public');
  const server = http.createServer((req, res) => {
    const name = req.url.slice(1);
    if (!['meeting-prompt.html', 'meeting-prompt.js', 'meeting-prompt.css', 'oats-buddy.png'].includes(name)) { res.writeHead(404).end(); return; }
    res.setHeader('Content-Type', name.endsWith('.png') ? 'image/png' : name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html');
    res.setHeader('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'");
    res.end(fs.readFileSync(path.join(root, name)));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    for (const scenario of ['record', 'dismiss', 'escape', 'error', 'expired']) {
      const page = await browser.newPage({ viewport: { width: 370, height: 270 } });
      await page.addInitScript(({ scenario }) => {
        window.calls = [];
        window.__TAURI__ = { core: { invoke: async (command, args) => {
          window.calls.push({ command, args });
          if (command === 'meeting_prompt') return scenario === 'expired' ? null : { id: 7, app: 'Microsoft Teams' };
          if (command !== 'respond_to_meeting') throw Error('Unexpected command');
          if (scenario === 'error' && args.record) throw Error('Open oats and finish setup first');
        } } };
      }, { scenario });
      await page.goto(`http://127.0.0.1:${server.address().port}/meeting-prompt.html`);
      await page.waitForFunction(() => { const buddy = document.querySelector('.brand img'); return buddy?.complete && buddy.naturalWidth > 0; });
      if (scenario === 'expired') {
        await page.getByText('This call prompt has expired.').waitFor();
        assert.equal(await page.locator('#record').isDisabled(), true);
      } else {
        await page.getByRole('heading', { name: 'Microsoft Teams audio detected' }).waitFor();
        assert.equal(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight), true, 'Popup fits its window');
        if (scenario === 'record') {
          const output = path.resolve(__dirname, '../test-results/meeting-prompt.png');
          fs.mkdirSync(path.dirname(output), { recursive: true });
          await page.screenshot({ path: output });
          await page.locator('#record').click();
          await page.locator('#record').dispatchEvent('click');
        } else if (scenario === 'dismiss') await page.locator('#dismiss').click();
        else if (scenario === 'escape') await page.keyboard.press('Escape');
        else {
          await page.locator('#record').click();
          await page.getByText('Open oats and finish setup first').waitFor();
          await page.locator('#dismiss').click();
        }
        const calls = await page.evaluate(() => window.calls.filter(c => c.command === 'respond_to_meeting'));
        assert.equal(calls.length, scenario === 'error' ? 2 : 1);
        assert.deepEqual(calls[0].args, { id: 7, record: ['record', 'error'].includes(scenario) });
      }
      await page.close();
      console.log(`PASS: ${scenario}`);
    }
  } finally { await browser?.close(); server.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
