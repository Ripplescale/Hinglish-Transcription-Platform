/* Final read-only optimized-app UI smoke against the existing QA capture.
 * No device access, model launch, clipboard operation, or cloud handoff.
 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const normalize = value => value.replace(/^\\\\\?\\/, '').replace(/\\/g, '/').replace(/\/$/, '').toLowerCase();

async function main() {
  const [endpoint, fixturePath] = process.argv.slice(2);
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(endpoint)) throw Error('Local CDP endpoint required');
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  if (normalize(path.dirname(path.resolve(fixturePath))) !== normalize(fixture.data_root) || !/native-qa-/i.test(fixture.data_root)) throw Error('Isolated QA root required');
  const capture = JSON.parse(fs.readFileSync(path.join(fixture.data_root, 'physical-capture', 'native-capture-report-final.json'), 'utf8'));
  assert.equal(capture.state, 'complete');
  const output = path.join(fixture.data_root, 'release-ui-qa');
  fs.mkdirSync(output, { recursive: true });
  const report = { started_at: new Date().toISOString(), state: 'running', mocked_ipc: false,
    physical_device_capture: false, model_rerun: false, clipboard_accessed: false, claude_opened: false, checks: [] };
  const browser = await chromium.connectOverCDP(endpoint);
  const page = browser.contexts().flatMap(context => context.pages()).find(page => /tauri|localhost/.test(page.url()));
  if (!page) { await browser.close(); throw Error('Native app WebView not found'); }
  const invoke = (command, args = {}) => page.evaluate(({ command, args }) => window.__TAURI_INTERNALS__.invoke(command, args), { command, args });
  const preferences = () => page.evaluate(() => JSON.parse(localStorage.getItem('sttapp.local-workflow.v1') || '{}').preferences ?? null);
  const checked = message => { report.checks.push(message); console.log(message); };
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    assert.equal(await invoke('get_active_capture'), null);
    await page.waitForFunction(() => !!JSON.parse(localStorage.getItem('sttapp.local-workflow.v1') || '{}').preferences?.profile,
      null, { timeout: 15000 });
    const initialPreferences = await preferences();
    const before = await invoke('load_transcript_workspace', { meetingId: capture.meeting_id });
    const originalRows = await invoke('api_get_meeting_transcripts', { meetingId: capture.meeting_id, limit: 100, offset: 0 });
    assert.equal(before.profile, 'apex-20');
    assert.equal(before.source_job_id, capture.transcription.job_id);
    const metadata = await invoke('api_get_meeting_metadata', { meetingId: capture.meeting_id });
    assert.equal(normalize(metadata.folder_path), normalize(capture.capture.session_dir));
    await page.goto(new URL(`/meeting-details?id=${encodeURIComponent(capture.meeting_id)}`, page.url()).href, { waitUntil: 'domcontentloaded' });
    await page.getByText('Transcript: Apex · 20s', { exact: true }).waitFor({ timeout: 20000 });
    await page.getByRole('heading', { name: metadata.title, exact: true }).waitFor();
    assert.equal(await page.getByLabel('Local transcription profile', { exact: true }).inputValue(), initialPreferences.profile);
    checked('Optimized app identifies the existing Apex20 transcript separately from the unchanged next-run model preference');
    for (const row of originalRows.transcripts) await page.getByText(row.text, { exact: true }).waitFor();
    assert.equal(originalRows.transcripts.length, capture.transcription.imported_segments);
    await page.getByText(/Heard in this window:/).first().waitFor();
    checked('Existing transcript rows and speaker candidates render from native saved data');
    const audio = page.locator('audio');
    await audio.waitFor({ state: 'attached' });
    await audio.evaluate(element => { element.muted = true; element.volume = 0; element.pause(); element.load(); });
    await page.waitForFunction(() => {
      const element = document.querySelector('audio');
      return element && element.readyState >= 1 && Number.isFinite(element.duration);
    }, null, { timeout: 15000 });
    report.playback = await audio.evaluate(element => ({ duration: element.duration, src: element.currentSrc, muted: element.muted,
      paused: element.paused, error: element.error?.code ?? null }));
    assert.equal(report.playback.error, null);
    assert.equal(report.playback.paused, true);
    assert(Math.abs(report.playback.duration - capture.duration_seconds) < .001);
    assert(report.playback.src.includes('asset.localhost') || report.playback.src.startsWith('asset:'));
    await audio.evaluate(element => { element.currentTime = 5; });
    await page.waitForFunction(() => { const element = document.querySelector('audio'); return element && Math.abs(element.currentTime - 5) < .01 && element.paused; });
    checked('Saved audio metadata loads through the native asset protocol and seeks while paused and muted');
    await page.screenshot({ path: path.join(output, 'optimized-workspace.png'), fullPage: true });
    const after = await invoke('load_transcript_workspace', { meetingId: capture.meeting_id });
    assert.deepEqual(after, before);
    const finalRows = await invoke('api_get_meeting_transcripts', { meetingId: capture.meeting_id, limit: 100, offset: 0 });
    assert.deepEqual(finalRows, originalRows);
    assert.deepEqual(await preferences(), initialPreferences);
    assert.equal(await invoke('get_active_capture'), null);
    assert.deepEqual(errors, []);
    report.meeting_id = capture.meeting_id;
    report.segment_count = originalRows.transcripts.length;
    report.transcript_profile = before.profile;
    report.next_run_profile = initialPreferences.profile;
    report.page_errors = errors;
    checked('Notes, corrections, transcript data and recording preferences remain unchanged');
    report.state = 'complete';
  } catch (error) {
    report.state = 'failed'; report.error = String(error.stack || error);
    await page.screenshot({ path: path.join(output, 'optimized-ui-failure.png'), fullPage: true }).catch(() => {});
    throw error;
  } finally {
    await page.locator('audio').evaluateAll(items => items.forEach(element => { element.pause(); element.muted = true; })).catch(() => {});
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(output, 'optimized-ui-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
