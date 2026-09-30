/* Real native recovery and final-tail inference from an isolated saved-audio
 * fixture. Never captures devices, changes the clipboard, or opens Claude.
 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const normalize = value => value.replace(/^\\\\\?\\/, '').replace(/\\/g, '/').replace(/\/$/, '').toLowerCase();
const sha = filename => crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex');

async function main() {
  const [endpoint, fixturePath] = process.argv.slice(2);
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(endpoint)) throw Error('Local CDP endpoint required');
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  if (normalize(path.dirname(path.resolve(fixturePath))) !== normalize(fixture.data_root) ||
      !/native-qa-/i.test(fixture.data_root) ||
      normalize(path.dirname(fixture.session_dir)) !== normalize(path.join(fixture.data_root, 'recordings'))) {
    throw Error('Isolated recovery fixture required');
  }
  const report = { started_at: new Date().toISOString(), state: 'running', device_recording: false,
    mocked_ipc: false, clipboard_accessed: false, checks: [], session_id: fixture.session_id };
  const verifySources = () => {
    for (const [relative, hash] of Object.entries(fixture.source_files)) {
      const filename = path.resolve(fixture.session_dir, relative);
      assert(normalize(filename).startsWith(normalize(fixture.session_dir) + '/'));
      assert.equal(sha(filename), hash, `Source changed: ${relative}`);
    }
    assert.equal(sha(path.join(fixture.session_dir, 'timeline.jsonl')), fixture.journal_sha256);
  };
  const browser = await chromium.connectOverCDP(endpoint);
  const page = browser.contexts().flatMap(context => context.pages()).find(page => /tauri|localhost/.test(page.url()));
  if (!page) { await browser.close(); throw Error('Native WebView not found'); }
  const invoke = (command, args = {}) => page.evaluate(({ command, args }) => window.__TAURI_INTERNALS__.invoke(command, args), { command, args });
  const checked = text => { report.checks.push(text); console.log(text); };
  let job;
  try {
    assert.equal(await invoke('get_active_capture'), null);
    verifySources();
    assert(!fs.existsSync(path.join(fixture.session_dir, 'audio.wav')), 'Use a fresh interrupted fixture');
    const journal = fs.readFileSync(path.join(fixture.session_dir, 'timeline.jsonl'), 'utf8');
    assert(!journal.includes('capture_finalized'));
    const meeting = await invoke('ensure_capture_meeting', { sessionDir: fixture.session_dir });
    report.meeting_id = meeting.meeting_id;
    const recoverable = await invoke('list_recoverable_captures');
    assert(recoverable.some(item => item.session_id === fixture.session_id));
    checked('Interrupted capture remains discoverable after its meeting has already been linked');

    const recovered = await invoke('recover_local_capture', { sessionDir: fixture.session_dir });
    assert.equal(recovered.capture.session_id, fixture.session_id);
    assert.equal(recovered.verification.verified, true);
    assert.equal(recovered.verification.source_journal_sha256, fixture.journal_sha256);
    assert(Math.abs(recovered.verification.duration_seconds - fixture.duration_seconds) < .0001);
    verifySources();
    const metadata = JSON.parse(fs.readFileSync(path.join(fixture.session_dir, 'metadata.json'), 'utf8'));
    assert.equal(metadata.status, 'recovered');
    const wav = fs.readFileSync(path.join(fixture.session_dir, 'audio.wav'));
    assert.equal(wav.toString('ascii', 0, 4), 'RIFF');
    assert.equal(wav.toString('ascii', 8, 12), 'WAVE');
    assert.equal(wav.toString('ascii', 36, 40), 'data');
    assert.equal(wav.length, 44 + wav.readUInt32LE(40));
    const duration = wav.readUInt32LE(40) / wav.readUInt32LE(28);
    assert(Math.abs(duration - fixture.duration_seconds) < .0001);
    const audio = await invoke('local_get_meeting_audio', { meetingId: meeting.meeting_id });
    assert.equal(normalize(audio.path), normalize(path.join(fixture.session_dir, 'audio.wav')));
    const remaining = await invoke('list_recoverable_captures');
    assert(!remaining.some(item => item.session_id === fixture.session_id));
    report.recovery = { verified: true, duration_seconds: duration, source_files_unchanged: Object.keys(fixture.source_files).length,
      source_journal_sha256: fixture.journal_sha256, audio_sha256: sha(path.join(fixture.session_dir, 'audio.wav')) };
    checked('Recovery creates verified 17.3-second playback without changing source chunks or the journal');

    job = await invoke('start_local_transcription', { sessionDir: fixture.session_dir, profile: 'apex-20', languageMode: 'hinglish', projectId: null });
    report.job_id = job.job_id;
    let result;
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      result = await invoke('get_local_transcription_status', { jobId: job.job_id });
      if (result.state === 'complete') break;
      if (['failed', 'stopped'].includes(result.state)) throw Error(result.error || result.state);
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    assert.equal(result.state, 'complete', 'Recovered final tail must not wait forever for a full 20-second chunk');
    assert.equal(result.capture_finalized, true);
    assert.equal(result.backlog_seconds, 0);
    assert(Math.abs(result.processed_audio_seconds - fixture.duration_seconds) < .0001);
    assert(Math.abs(result.available_audio_seconds - fixture.duration_seconds) < .0001);
    assert.equal(result.segments.length, 1);
    assert.equal(result.segments[0].start_seconds, 0);
    assert(Math.abs(result.segments[0].end_seconds - fixture.duration_seconds) < .0001);
    assert(result.segments[0].text.trim().length > 0);
    const imported = await invoke('import_local_transcription', { jobId: job.job_id, meetingId: meeting.meeting_id, primary: true });
    assert.equal(imported.meeting_id, meeting.meeting_id);
    assert(imported.imported_count > 0);
    verifySources();
    report.transcription = { state: result.state, profile: result.profile, capture_finalized: result.capture_finalized,
      available_audio_seconds: result.available_audio_seconds, processed_audio_seconds: result.processed_audio_seconds,
      backlog_seconds: result.backlog_seconds, segment_count: result.segments.length,
      final_segment_end_seconds: result.segments[0].end_seconds, transcript_character_count: result.segments[0].text.length };
    checked('Real Apex completes and imports the recovered 17.3-second final tail with zero backlog');
    report.state = 'complete';
  } catch (error) {
    report.state = 'failed'; report.error = String(error.stack || error);
    if (job) await invoke('stop_local_transcription', { jobId: job.job_id }).catch(() => {});
    throw error;
  } finally {
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(fixture.data_root, 'recovery-native-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
