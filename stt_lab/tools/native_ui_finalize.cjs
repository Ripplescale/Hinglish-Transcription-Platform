/* Completes screenshot evidence after the expected WebView clipboard-read denial.
 * No clipboard access, navigation, recording, or transcript edits.
 */
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
async function main() {
  const [fixturePath] = process.argv.slice(2);
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  if (!/native-qa-/i.test(fixture.data_root)) throw Error('Isolated fixture required');
  const output = path.join(fixture.data_root, 'ui-qa');
  const report = JSON.parse(fs.readFileSync(path.join(output, 'native-ui-report.json'), 'utf8'));
  if (report.state !== 'failed' || !report.error?.includes('readText') ||
      !report.error.includes('Read permission denied') || report.checks.length !== 4) {
    throw Error('Unexpected original QA state');
  }
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9788');
  try {
    const page = browser.contexts().flatMap(context => context.pages()).find(page => /tauri|localhost/.test(page.url()));
    await page.locator('audio').evaluateAll(items => items.forEach(audio => { audio.muted = true; audio.pause(); }));
    await page.screenshot({ path: path.join(output, 'native-workspace-final.png'), fullPage: true });
    report.checks.push('Actual Copy only control reported success without opening Claude; clipboard content verification unavailable');
    report.clipboard_export = { ui_copy_succeeded: true, contents_verified: false,
      limitation: 'WebView denied clipboard read permission. No OS clipboard fallback was attempted.' };
    report.native_save_dialog = 'Not opened; native OS Save dialog still requires a separate interactive check.';
    report.blocked_verification = report.error;
    delete report.error;
    report.state = 'complete_with_limitations';
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(output, 'native-ui-report-final.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ state: report.state, checks: report.checks.length, output }));
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
