/* Real Tauri IPC and saved-audio inference. No device recording or cloud handoff. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function main() {
  const [endpoint, fixturePath] = process.argv.slice(2);
  if (!endpoint || !fixturePath) throw new Error('Pass localhost CDP endpoint and fixture.json');
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  const report = { started_at: new Date().toISOString(), device_recording: false, mocked_ipc: false, checks: [] };
  const browser = await chromium.connectOverCDP(endpoint);
  const page = browser.contexts().flatMap(context => context.pages()).find(page => /tauri|localhost/.test(page.url()));
  if (!page) throw new Error('Native app webview was not found');
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const invoke = (command, args = {}) => page.evaluate(({ command, args }) => window.__TAURI_INTERNALS__.invoke(command, args), { command, args })
    .catch(error => { throw error instanceof Error ? error : new Error(String(error)); });
  const checked = name => { report.checks.push(name); console.log(name); };
  try {
    await page.waitForFunction(() => typeof window.__TAURI_INTERNALS__?.invoke === 'function');
    const profiles = await invoke('get_local_stt_profiles');
    assert.deepEqual(profiles.map(item => item.id).sort(), ['apex-20', 'trelis-10', 'trelis-20', 'trelis-5']);
    assert(profiles.every(item => item.available));
    assert.equal(await invoke('get_active_capture'), null);
    checked('Real IPC: selected models available, no active device capture');
    const meeting = await invoke('ensure_capture_meeting', { sessionDir: fixture.session_dir });
    const same = await invoke('ensure_capture_meeting', { sessionDir: fixture.session_dir });
    assert.equal(meeting.meeting_id, same.meeting_id);
    report.meeting_id = meeting.meeting_id;
    checked('Capture-to-meeting linking is idempotent');

    const workspace = await invoke('load_transcript_workspace', { meetingId: meeting.meeting_id });
    const saved = await invoke('save_transcript_workspace', { meetingId: meeting.meeting_id,
      expectedRevision: workspace.revision, notes: 'Native QA note: preserve this while transcript updates.',
      corrections: [], projectId: null, profile: null, speakerNames: {} });
    const waitComplete = async job => {
      const deadline = Date.now() + 180000;
      while (Date.now() < deadline) {
        const status = await invoke('get_local_transcription_status', { jobId: job.job_id });
        if (status.state === 'complete') return status;
        if (['failed', 'stopped'].includes(status.state)) throw new Error(status.error || status.state);
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      throw new Error('Saved-audio model job timed out');
    };
    const job = await invoke('start_local_transcription', { sessionDir: fixture.session_dir, profile: 'apex-20', languageMode: 'hinglish', projectId: null });
    report.job_id = job.job_id;
    const result = await waitComplete(job);
    assert(result.segments.some(segment => segment.text.trim()));
    const imported = await invoke('import_local_transcription', { jobId: job.job_id, meetingId: meeting.meeting_id, primary: true });
    assert.equal(imported.meeting_id, meeting.meeting_id);
    const duplicate = await invoke('import_local_transcription', { jobId: job.job_id, meetingId: meeting.meeting_id, primary: true });
    assert.equal(duplicate.imported_count, 0);
    const updated = await invoke('load_transcript_workspace', { meetingId: meeting.meeting_id });
    assert.equal(updated.revision, saved.revision);
    assert.equal(updated.notes, saved.notes);
    checked('Real Apex worker, final tail, primary import, deduplication and note revision isolation');

    const second = await invoke('start_local_transcription', { sessionDir: fixture.session_dir, profile: 'apex-20', languageMode: 'hinglish', projectId: null });
    await waitComplete(second);
    await assert.rejects(() => invoke('import_local_transcription', { jobId: second.job_id, meetingId: meeting.meeting_id, primary: true }), /already has a transcript/);
    const alternate = await invoke('import_local_transcription', { jobId: second.job_id, meetingId: meeting.meeting_id, primary: false });
    assert.notEqual(alternate.meeting_id, meeting.meeting_id);
    await invoke('import_local_transcription', { jobId: job.job_id, meetingId: alternate.meeting_id, primary: false });
    const rebound = await invoke('load_transcript_workspace', { meetingId: meeting.meeting_id });
    assert.equal(rebound.source_meeting_id, meeting.meeting_id);
    checked('Alternate versions cannot overwrite primary text or change its source binding');

    const rows = await invoke('api_get_meeting_transcripts', { meetingId: meeting.meeting_id, limit: 100, offset: 0 });
    assert(rows.transcripts.length > 0);
    const first = rows.transcripts[0];
    const reviewed = await invoke('save_transcript_workspace', { meetingId: meeting.meeting_id,
      expectedRevision: saved.revision, notes: saved.notes, corrections: [{ segment_id: first.id, original_text: first.text,
        text: `${first.text} [QA correction]`, updated_at: new Date().toISOString() }], projectId: null, profile: 'apex-20', speakerNames: {} });
    await assert.rejects(() => invoke('save_transcript_workspace', { meetingId: meeting.meeting_id,
      expectedRevision: saved.revision, notes: 'stale', corrections: [], projectId: null, profile: 'apex-20', speakerNames: {} }), /changed in another window/);
    assert.equal(reviewed.corrections[0].original_text, first.text);
    checked('Corrections preserve original text; stale user saves are rejected');

    const audio = await invoke('local_get_meeting_audio', { meetingId: meeting.meeting_id });
    assert(audio.path);
    await page.evaluate(id => { window.location.href = `/meeting-details?id=${encodeURIComponent(id)}`; }, meeting.meeting_id);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2500);
    await page.screenshot({ path: path.join(fixture.data_root, 'native-workspace.png'), fullPage: true });
    report.audio_path = audio.path;
    report.segment_count = rows.transcripts.length;
    report.page_errors = errors;
    report.state = 'complete';
  } catch (error) {
    report.state = 'failed'; report.error = String(error.stack || error); throw error;
  } finally {
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(fixture.data_root, 'native-ipc-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
