/* One explicitly approved physical capture only. Parent must announce readiness
 * and release all model work before --approved-one-shot is passed.
 * --prepare only synthesizes a local signal WAV: it never plays or records audio.
 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const normalize = value => value.replace(/^\\\\\?\\/, '').replace(/\\/g, '/').replace(/\/$/, '').toLowerCase();
const storageKey = 'sttapp.local-workflow.v1';
function waveStats(filename) {
  const data = fs.readFileSync(filename);
  assert.equal(data.toString('ascii', 0, 4), 'RIFF');
  assert.equal(data.toString('ascii', 36, 40), 'data');
  let peak = 0, squares = 0;
  for (let i = 44; i + 1 < data.length; i += 2) {
    const sample = data.readInt16LE(i) / 32768;
    peak = Math.max(peak, Math.abs(sample)); squares += sample * sample;
  }
  return { duration_seconds: data.readUInt32LE(40) / data.readUInt32LE(28),
    peak, rms: Math.sqrt(squares / ((data.length - 44) / 2)), bytes: data.length };
}
async function main() {
  const [endpoint, fixturePath, mode] = process.argv.slice(2);
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(endpoint)) throw Error('Local CDP endpoint required');
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  if (normalize(path.dirname(path.resolve(fixturePath))) !== normalize(fixture.data_root) || !/native-qa-/i.test(fixture.data_root)) throw Error('Isolated QA root required');
  const output = path.join(fixture.data_root, 'physical-capture');
  fs.mkdirSync(output, { recursive: true });
  const signal = path.join(output, 'local-test-signal.wav');
  if (mode === '--prepare') {
    execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', path.join(__dirname, 'prepare_native_signal.ps1'), '-WavePath', signal], { windowsHide: true, stdio: 'pipe' });
    console.log('Offline signal prepared. No audio was played or recorded.'); return;
  }
  if (mode !== '--approved-one-shot') throw Error('Explicitly dispatched one-shot approval flag required');
  assert(fs.existsSync(signal), 'Prepare the signal before dispatch');
  const attempt = path.join(output, 'one-shot-attempt.json');
  fs.writeFileSync(attempt, JSON.stringify({ dispatched_at: new Date().toISOString() }), { flag: 'wx' });
  const report = { started_at: new Date().toISOString(), state: 'running', mocked_ipc: false,
    physical_device_capture: true, clipboard_accessed: false, cloud_upload: false, target_seconds: 20, checks: [] };
  const browser = await chromium.connectOverCDP(endpoint);
  const page = browser.contexts().flatMap(context => context.pages()).find(page => /tauri|localhost/.test(page.url()));
  const invoke = (command, args = {}) => page.evaluate(({ command, args }) => window.__TAURI_INTERNALS__.invoke(command, args), { command, args });
  const workflowState = () => page.evaluate(key => JSON.parse(localStorage.getItem(key) || '{}'), storageKey);
  const checked = message => { report.checks.push(message); console.log(message); };
  let capture, signalProcess, emergencyTimer, uiStopTimer, oldPreferences, started = false, stopRequested = false;
  const forceStop = async () => {
    if (!started || !capture) return;
    try {
      const active = await invoke('get_active_capture');
      if (active && active.session_id === capture.session_id) {
        report.emergency_stop_used = true;
        await invoke('stop_recording', { args: { save_path: path.join(output, 'emergency-stop.wav') } });
      }
    } catch (error) { report.emergency_stop_error = String(error); }
  };
  const uiStop = async () => {
    if (stopRequested) return;
    stopRequested = true;
    try {
      report.ui_stop_at = new Date().toISOString();
      await page.locator('main button:has(svg.lucide-square)').click({ timeout: 500 });
    } catch { await forceStop(); }
  };
  try {
    assert.equal(await invoke('get_active_capture'), null, 'Never interfere with an existing recording');
    oldPreferences = (await workflowState()).preferences;
    await page.goto(new URL('/', page.url()).href, { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Local transcription profile', { exact: true }).selectOption('apex-20');
    await page.getByLabel('Transcription timing', { exact: true }).selectOption('during-recording');
    await page.getByLabel('Recording language', { exact: true }).selectOption('hinglish');
    assert.equal((await workflowState()).preferences.profile, 'apex-20');
    assert.equal((await workflowState()).preferences.timing, 'during-recording');
    await page.screenshot({ path: path.join(output, 'before-capture.png'), fullPage: true });
    signalProcess = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', path.join(__dirname, 'play_native_signal.ps1'), '-WavePath', signal], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('Signal player did not become ready')), 15000);
      signalProcess.once('error', reject);
      signalProcess.stdout.on('data', chunk => { if (chunk.toString().includes('READY')) { clearTimeout(timer); resolve(); } });
    });
    // Only the real recording button initiates hardware capture.
    started = true;
    report.start_clicked_at = new Date().toISOString();
    await page.locator('main button.w-12:has(svg.lucide-mic)').click({ timeout: 1500 });
    const startDeadline = Date.now() + 120000;
    while (Date.now() < startDeadline) {
      capture = await invoke('get_active_capture');
      if (capture) break;
      await pause(100);
    }
    assert(capture, 'Recording did not start; no automatic retry is permitted');
    report.capture = capture; report.active_observed_at = new Date().toISOString();
    assert(normalize(capture.session_dir).startsWith(normalize(path.join(fixture.data_root, 'recordings')) + '/'));
    // Budget one second for UI stop scheduling. The independent timer invokes
    // native stop if the UI button fails; native duration polling may stop sooner.
    uiStopTimer = setTimeout(() => { void uiStop(); }, 19000);
    emergencyTimer = setTimeout(() => { void forceStop(); }, 20000);
    signalProcess.stdin.end('PLAY\n');
    checked('Actual recording controls started default microphone/system capture; generated signal now plays locally');
    while (await invoke('get_active_capture')) {
      const state = await invoke('get_recording_state');
      const elapsed = state.recording_duration ?? state.active_duration ?? 0;
      if (elapsed >= 19) await uiStop();
      const run = (await workflowState()).runs?.find(item => item.capture.session_id === capture.session_id);
      if (run?.job?.job_id) report.worker_started_during_capture = { job_id: run.job.job_id, state: run.job.state, recording_duration_seconds: elapsed };
      if (Date.now() - Date.parse(report.active_observed_at) > 20500) { await forceStop(); break; }
      await pause(150);
    }
    signalProcess.kill();
    clearTimeout(uiStopTimer); clearTimeout(emergencyTimer);
    assert.equal(await invoke('get_active_capture'), null);
    report.stopped_observed_at = new Date().toISOString();
    const source = capture.session_dir.replace(/^\\\\\?\\/, '');
    // Stop releases the capture before file finalization may finish. Waiting
    // for durable output does not keep either input device recording.
    const finalizeDeadline = Date.now() + 15000;
    while (Date.now() < finalizeDeadline) {
      if (fs.existsSync(path.join(source, 'metadata.json')) && fs.existsSync(path.join(source, 'audio.wav')) &&
          fs.readFileSync(path.join(source, 'timeline.jsonl'), 'utf8').includes('capture_finalized')) break;
      await pause(100);
    }
    const journal = fs.readFileSync(path.join(source, 'timeline.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line));
    assert(journal.some(row => row.kind === 'capture_finalized'));
    const metadata = JSON.parse(fs.readFileSync(path.join(source, 'metadata.json'), 'utf8'));
    report.duration_seconds = metadata.duration_seconds;
    report.track_audio = {};
    for (const track of ['microphone', 'system']) {
      assert(journal.some(row => row.kind === 'audio_chunk' && row.track === track), `Missing ${track} source chunks`);
      report.track_audio[track] = waveStats(path.join(source, `${track}.wav`));
      assert(report.track_audio[track].bytes > 44);
    }
    report.playback_audio = waveStats(path.join(source, 'audio.wav'));
    assert(report.track_audio.system.rms > 0.0001, 'System track should contain the generated signal');
    assert(report.playback_audio.duration_seconds <= 20.5, 'Capture exceeded the bounded test duration');
    checked('Microphone and system tracks finalized independently with a non-silent system signal and playable recording');
    const deadline = Date.now() + 240000;
    let run, result, rows;
    while (Date.now() < deadline) {
      run = (await workflowState()).runs?.find(item => item.capture.session_id === capture.session_id);
      if (run?.job?.job_id) {
        result = await invoke('get_local_transcription_status', { jobId: run.job.job_id });
        if (['failed', 'stopped'].includes(result.state)) throw Error(result.error || result.state);
        if (run.transcriptMeetingId) rows = await invoke('api_get_meeting_transcripts', { meetingId: run.transcriptMeetingId, limit: 100, offset: 0 });
        if (result.state === 'complete' && rows?.transcripts?.length) break;
      }
      await pause(1000);
    }
    assert.equal(result?.state, 'complete');
    assert.equal(result.backlog_seconds, 0);
    assert(rows?.transcripts.length > 0);
    assert(report.worker_started_during_capture, 'Live preview should start its worker while recording');
    report.meeting_id = run.transcriptMeetingId;
    report.transcription = { job_id: run.job.job_id, state: result.state, profile: result.profile,
      segment_count: result.segments.length, imported_segments: rows.transcripts.length, backlog_seconds: result.backlog_seconds };
    assert(page.url().includes(encodeURIComponent(run.transcriptMeetingId)), 'Stop should automatically open the saved meeting');
    checked('Meeting creation, live-worker startup, completed transcription and transcript import all occurred automatically');
    const speakerSetup = await invoke('get_speaker_setup_status');
    if (speakerSetup.available && speakerSetup.enabled !== false) {
      const speakerDeadline = Date.now() + 180000;
      while (Date.now() < speakerDeadline) {
        run = (await workflowState()).runs?.find(item => item.capture.session_id === capture.session_id);
        if (run?.speakerJob?.job_id) {
          const status = await invoke('get_speaker_job_status', { jobId: run.speakerJob.job_id });
          report.automatic_speakers = { job_id: status.job_id, state: status.state, error: status.error,
            speaker_count: status.result?.speakers?.length, turn_count: status.result?.turns?.length };
          if (['complete', 'failed', 'stopped'].includes(status.state)) break;
        }
        await pause(1000);
      }
      assert.equal(report.automatic_speakers?.state, 'complete', 'Configured post-call speakers should run automatically');
      checked('Configured speaker identification started automatically after transcription and completed');
    }
    await page.screenshot({ path: path.join(output, 'after-capture.png'), fullPage: true });
    report.state = 'complete';
  } catch (error) {
    report.state = 'failed'; report.error = String(error.stack || error);
    await page.screenshot({ path: path.join(output, 'capture-failure.png'), fullPage: true }).catch(() => {});
    throw error;
  } finally {
    clearTimeout(uiStopTimer); clearTimeout(emergencyTimer);
    if (signalProcess && !signalProcess.killed) signalProcess.kill();
    await forceStop();
    if (oldPreferences && !await invoke('get_active_capture').catch(() => true)) {
      try {
        await page.goto(new URL('/', page.url()).href, { waitUntil: 'domcontentloaded' });
        await page.getByLabel('Local transcription profile', { exact: true }).selectOption(oldPreferences.profile);
        await page.getByLabel('Recording language', { exact: true }).selectOption(oldPreferences.languageMode);
        const timing = oldPreferences.profile === 'trelis-20' ? 'after-recording' : oldPreferences.timing;
        if (oldPreferences.profile === 'apex-20') await page.getByLabel('Transcription timing', { exact: true }).selectOption(timing);
        await page.waitForFunction(({ key, expected }) => {
          const actual = JSON.parse(localStorage.getItem(key) || '{}').preferences;
          return actual?.profile === expected.profile && actual?.languageMode === expected.languageMode && actual?.timing === expected.timing;
        }, { key: storageKey, expected: { ...oldPreferences, timing } }, { timeout: 5000 });
        report.preferences_restored = true;
        if (report.meeting_id) await page.goto(new URL(`/meeting-details?id=${encodeURIComponent(report.meeting_id)}`, page.url()).href, { waitUntil: 'domcontentloaded' });
      } catch (error) { report.preferences_restored = false; report.preference_restore_error = String(error); }
    }
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(output, 'native-capture-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
