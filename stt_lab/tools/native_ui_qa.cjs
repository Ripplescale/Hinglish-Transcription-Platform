/* Real WebView2 UI checks over an isolated saved-audio fixture.
 * Run only after native_ipc_smoke.cjs completes; never captures devices or opens Claude.
 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const normalize = value => value.replace(/^\\\\\?\\/, '').replace(/\\/g, '/').replace(/\/$/, '').toLowerCase();

async function main() {
  const [endpoint = 'http://127.0.0.1:9788', fixturePath] = process.argv.slice(2);
  if (!fixturePath) throw new Error('Pass localhost CDP endpoint and isolated fixture.json');
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(endpoint)) throw new Error('Only a local debugging endpoint is allowed');
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  if (normalize(path.dirname(path.resolve(fixturePath))) !== normalize(fixture.data_root) || !/native-qa-/i.test(fixture.data_root)) {
    throw new Error('Refusing to edit a meeting outside the isolated native QA fixture');
  }
  const ipcReport = JSON.parse(fs.readFileSync(path.join(fixture.data_root, 'native-ipc-report.json'), 'utf8'));
  if (ipcReport.state !== 'complete' || !ipcReport.meeting_id) throw new Error('Wait for successful native IPC checks before navigating its webview');
  const output = path.join(fixture.data_root, 'ui-qa');
  fs.mkdirSync(output, { recursive: true });
  const report = { started_at: new Date().toISOString(), mocked_ipc: false, physical_device_capture: false,
    claude_opened: false, audio_muted: true, checks: [], meeting_id: ipcReport.meeting_id };
  const browser = await chromium.connectOverCDP(endpoint);
  const page = browser.contexts().flatMap(context => context.pages()).find(page => /tauri|localhost/.test(page.url()));
  if (!page) { await browser.close(); throw new Error('Native app webview was not found'); }
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const invoke = (command, args = {}) => page.evaluate(({ command, args }) => window.__TAURI_INTERNALS__.invoke(command, args), { command, args });
  const checked = name => { report.checks.push(name); console.log(name); };
  try {
    await page.waitForFunction(() => typeof window.__TAURI_INTERNALS__?.invoke === 'function');
    assert.equal(await invoke('get_active_capture'), null);
    const metadata = await invoke('api_get_meeting_metadata', { meetingId: report.meeting_id });
    assert.equal(normalize(metadata.folder_path), normalize(fixture.session_dir));
    report.initial_onboarding_status = await invoke('get_onboarding_status');
    await page.screenshot({ path: path.join(output, 'native-initial-state.png'), fullPage: true });
    if (!report.initial_onboarding_status?.completed) {
      await page.getByRole('heading', { name: 'Your local transcript workspace', exact: true }).waitFor({ timeout: 30000 });
      const setupText = await page.locator('main').innerText();
      assert(setupText.includes('Trelis') || setupText.includes('trelis'));
      assert(setupText.includes('Apex') || setupText.includes('apex'));
      assert.equal(await page.getByRole('button', { name: /Download (Whisper|Parakeet|summary|model)/i }).count(), 0);
      await page.screenshot({ path: path.join(output, 'native-onboarding.png'), fullPage: true });
      await page.getByRole('button', { name: 'Open workspace', exact: true }).click();
      await page.getByRole('heading', { name: 'Your local transcript workspace', exact: true }).waitFor({ state: 'hidden', timeout: 30000 });
      await page.waitForFunction(async () => (await window.__TAURI_INTERNALS__?.invoke('get_onboarding_status'))?.completed === true);
      checked('Fresh native onboarding reaches the workspace without retired model or cloud setup');
    } else checked('Native app reports completed onboarding; fresh onboarding already completed in this fixture');
    const initial = await invoke('load_transcript_workspace', { meetingId: report.meeting_id });
    const rows = await invoke('api_get_meeting_transcripts', { meetingId: report.meeting_id, limit: 100, offset: 0 });
    assert(rows.transcripts.length > 0);
    const first = rows.transcripts[0];
    const correction = `${first.text}\n[Native UI review check]`;
    const notes = `Native UI validation: notes saved through the actual editor.\n${new Date().toISOString()}`;
    await page.goto(new URL(`/meeting-details?id=${encodeURIComponent(report.meeting_id)}`, page.url()).href, { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { name: metadata.title, exact: true }).waitFor({ timeout: 45000 });
    const audio = page.locator('audio');
    await audio.waitFor({ state: 'attached' });
    await audio.evaluate(element => { element.muted = true; element.volume = 0; element.load(); });
    await page.waitForFunction(() => {
      const audio = document.querySelector('audio');
      return audio && audio.readyState >= 1 && Number.isFinite(audio.duration);
    }, null, { timeout: 20000 });
    const audioInfo = await audio.evaluate(element => ({ duration: element.duration, currentSrc: element.currentSrc, muted: element.muted, error: element.error?.code ?? null }));
    assert.equal(audioInfo.error, null);
    assert(Math.abs(audioInfo.duration-fixture.duration_seconds) < .1);
    assert(audioInfo.currentSrc.includes('asset.localhost') || audioInfo.currentSrc.startsWith('asset:'));
    await audio.evaluate(async element => { element.currentTime = 7.5; await element.play(); });
    await page.waitForFunction(() => { const audio = document.querySelector('audio'); return audio && !audio.paused && audio.currentTime > 7.5; }, null, { timeout: 10000 });
    report.playback = await audio.evaluate(element => { element.pause(); return { duration: element.duration, seek_position: element.currentTime, muted: element.muted, ready_state: element.readyState }; });
    checked('Actual local asset audio loads metadata and seeks/plays while muted');

    await page.getByRole('button', { name: 'Edit', exact: true }).first().click();
    await page.getByLabel('Correct transcript text').fill(correction);
    await page.getByRole('button', { name: 'Apply correction', exact: true }).click();
    const notesTab = page.getByRole('tab', { name: 'Notes', exact: true });
    if (await notesTab.isVisible()) await notesTab.click();
    await page.getByRole('textbox', { name: 'Meeting notes', exact: true }).fill(notes);
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await page.getByText(`Saved revision ${initial.revision+1}`, { exact: true }).waitFor({ timeout: 15000 });
    const saved = await invoke('load_transcript_workspace', { meetingId: report.meeting_id });
    assert.equal(saved.notes, notes);
    assert.equal(saved.corrections.find(item => item.segment_id === first.id)?.text, correction);
    assert.equal(saved.corrections.find(item => item.segment_id === first.id)?.original_text, first.text);
    const raw = await invoke('api_get_meeting_transcripts', { meetingId: report.meeting_id, limit: 100, offset: 0 });
    assert.equal(raw.transcripts.find(item => item.id === first.id)?.text, first.text);
    checked('Actual notes/correction controls persist revision while original SQLite recognition stays unchanged');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { name: metadata.title, exact: true }).waitFor();
    if (await notesTab.isVisible()) await notesTab.click();
    await page.waitForFunction(expected => document.querySelector('textarea[aria-label="Meeting notes"]')?.value === expected, notes);
    checked('Saved notes survive a real webview reload');
    const transcriptTab = page.getByRole('tab', { name: 'Transcript', exact: true });
    if (await transcriptTab.isVisible()) await transcriptTab.click();
    await page.getByText('More handoff options', { exact: true }).click();
    await page.getByRole('button', { name: 'Copy only', exact: true }).click();
    await page.getByText('Copied for Claude. Paste it yourself when ready.', { exact: true }).waitFor({ timeout: 15000 });
    // WebView2 denies clipboard reads here. Never fall back to reading the OS
    // clipboard: the user may be copying an unrelated secret concurrently.
    report.clipboard_export = { ui_copy_succeeded: true, contents_verified: false,
      limitation: 'WebView clipboard read permission is unavailable; export content has separate unit coverage.' };
    report.native_save_dialog = 'Not opened: native OS dialog requires a separate interactive check; clipboard export used the actual UI.';
    checked('Actual Copy only control reports success without opening Claude; clipboard content was not read');
    await page.screenshot({ path: path.join(output, 'native-workspace-final.png'), fullPage: true });
    report.page_errors = errors;
    assert.deepEqual(errors, []);
    report.state = 'complete';
  } catch (error) {
    report.state = 'failed'; report.error = String(error.stack || error);
    await page.screenshot({ path: path.join(output, 'native-ui-failure.png'), fullPage: true }).catch(() => {});
    throw error;
  } finally {
    await page.locator('audio').evaluateAll(elements => elements.forEach(element => { element.muted = true; element.pause(); })).catch(() => {});
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(output, 'native-ui-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
