// Read-only native startup check. No recording, worker, clipboard, or export calls.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function main() {
  const [endpoint, expectedRoot, reportPath] = process.argv.slice(2);
  if (!endpoint || !expectedRoot || !reportPath) throw new Error('Pass CDP endpoint, expected data root, and report path.');
  const browser = await chromium.connectOverCDP(endpoint, { timeout: 20000 });
  try {
    const page = browser.contexts().flatMap(context => context.pages()).find(page => /tauri|localhost/.test(page.url()));
    if (!page) throw new Error('Installed app WebView was not found.');
    await page.waitForFunction(() => typeof window.__TAURI_INTERNALS__?.invoke === 'function');
    const report = await page.evaluate(async () => {
      const invoke = window.__TAURI_INTERNALS__.invoke;
      return {
        checked_at: new Date().toISOString(),
        active_capture: await invoke('get_active_capture'),
        data_root: await invoke('get_database_directory'),
        profiles: await invoke('get_local_stt_profiles'),
        speaker_setup: await invoke('get_speaker_setup_status'),
      };
    });
    assert.equal(report.active_capture, null, 'A recording is active; do not close the app.');
    assert.equal(path.resolve(report.data_root).toLowerCase(), path.resolve(expectedRoot).toLowerCase());
    assert.deepEqual(report.profiles.map(profile => profile.id).sort(), ['apex-20', 'trelis-20']);
    assert(report.profiles.every(profile => profile.available), 'A selected model is unavailable.');
    assert.equal(report.speaker_setup.available, true);
    assert.equal(report.speaker_setup.enabled, true);
    report.state = 'passed';
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
    process.stdout.write(JSON.stringify(report));
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
