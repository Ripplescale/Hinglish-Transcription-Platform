/* Read-only app checks after the one-shot test has stopped and asynchronous
 * imports/metadata have settled. Never records, plays audio, or uses clipboard.
 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
async function main() {
  const [fixturePath] = process.argv.slice(2);
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  if (!/native-qa-/i.test(fixture.data_root)) throw Error('Isolated QA fixture required');
  const output = path.join(fixture.data_root, 'physical-capture');
  const report = JSON.parse(fs.readFileSync(path.join(output, 'native-capture-report.json'), 'utf8'));
  assert.equal(report.state, 'complete');
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9788');
  try {
    const page = browser.contexts().flatMap(context => context.pages()).find(page => /tauri|localhost/.test(page.url()));
    const invoke = (command, args = {}) => page.evaluate(({ command, args }) => window.__TAURI_INTERNALS__.invoke(command, args), { command, args });
    assert.equal(await invoke('get_active_capture'), null);
    const metadata = JSON.parse(fs.readFileSync(path.join(report.capture.session_dir, 'metadata.json'), 'utf8'));
    const result = JSON.parse(fs.readFileSync(path.join(fixture.data_root, 'jobs', report.transcription.job_id, 'status.json'), 'utf8'));
    const rows = await invoke('api_get_meeting_transcripts', { meetingId: report.meeting_id, limit: 100, offset: 0 });
    assert.equal(rows.transcripts.length, result.segments.filter(segment => segment.text.trim()).length);
    const workspace = await invoke('load_transcript_workspace', { meetingId: report.meeting_id });
    assert(workspace.speaker_metadata.turns.length > 0);
    await page.getByText(/Heard in this window:/).first().waitFor({ timeout: 10000 });
    await page.locator('audio').evaluateAll(items => items.forEach(audio => { audio.muted = true; audio.pause(); }));
    await page.screenshot({ path: path.join(output, 'after-capture-settled.png'), fullPage: true });
    report.initial_observation = { metadata_duration_seconds: report.duration_seconds, imported_segments: report.transcription.imported_segments };
    report.duration_seconds = metadata.duration_seconds;
    report.transcription.imported_segments = rows.transcripts.length;
    report.automatic_speakers.visible_in_ui = true;
    report.checks.push('Final metadata duration and all imported rows verified after asynchronous finalization; speaker candidates appeared in the UI');
    const microphone = result.segments.find(segment => segment.source_track === 'microphone');
    report.microphone_recognition_limit = { active_energy_seconds: microphone.audio_provenance.active_energy_seconds,
      runtime_stdout: microphone.recognition.provenance.runtime_stdout,
      raw_decoder_segments: microphone.recognition.provenance.unrequested_decoder_segments,
      conclusion: 'Quiet microphone input produced literal CLI text nan; not a parser conversion. Microphone speech accuracy was not established.' };
    report.settled_at = new Date().toISOString();
    fs.writeFileSync(path.join(output, 'native-capture-report-final.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ state: report.state, duration: report.duration_seconds,
      imported_segments: rows.transcripts.length, speakers_visible: true }));
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
