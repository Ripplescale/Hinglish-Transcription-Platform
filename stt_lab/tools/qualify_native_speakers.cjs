/* Real native speaker IPC on an existing isolated saved-audio fixture. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const normalize = value => fs.realpathSync.native(value.replace(/^\\\\\?\\/, '')).toLowerCase();
const read = value => JSON.parse(fs.readFileSync(value, 'utf8').replace(/^\uFEFF/, ''));

async function main() {
  const [endpoint, fixturePath, configuredRuntime] = process.argv.slice(2);
  assert(endpoint && fixturePath && configuredRuntime, 'Pass local CDP endpoint, fixture.json and activated speaker-runtime.json');
  assert(['127.0.0.1', 'localhost'].includes(new URL(endpoint).hostname), 'CDP must stay local');
  const fixture = read(fixturePath);
  assert(path.basename(fixture.data_root).startsWith('native-qa-'), 'Use the isolated QA root');
  const prior = read(path.join(fixture.data_root, 'native-ipc-report.json'));
  assert.equal(prior.state, 'complete');
  const config = read(configuredRuntime);
  assert.equal(config.validation.model_validated, true);
  assert.equal(config.network_inference, false);
  const report = { started_at: new Date().toISOString(), fixture: fixturePath,
    meeting_id: prior.meeting_id, transcription_job_id: prior.job_id,
    model_revision: config.model_revision, device_recording: false, mocked_ipc: false,
    deployment_not_accuracy: true, performance_qualification: false, checks: [] };
  const browser = await chromium.connectOverCDP(endpoint);
  const page = browser.contexts().flatMap(context => context.pages()).find(candidate => /tauri|localhost/.test(candidate.url()));
  assert(page, 'Native app WebView must exist');
  const invoke = (command, args = {}) => page.evaluate(({ command, args }) => window.__TAURI_INTERNALS__.invoke(command, args), { command, args });
  const check = message => { report.checks.push(message); console.log(message); };
  try {
    assert.equal(await invoke('get_active_capture'), null);
    const audio = await invoke('local_get_meeting_audio', { meetingId: prior.meeting_id });
    assert.equal(normalize(audio.path), normalize(path.join(fixture.session_dir, 'audio.wav')));
    const beforeRows = await invoke('api_get_meeting_transcripts', { meetingId: prior.meeting_id, limit: 1000, offset: 0 });
    assert(beforeRows.transcripts.length > 0);
    const beforeWorkspace = await invoke('load_transcript_workspace', { meetingId: prior.meeting_id });
    const audioHash = hash(fs.readFileSync(audio.path));
    const statusPath = path.join(fixture.data_root, 'jobs', prior.job_id, 'status.json');
    const transcriptionHash = hash(fs.readFileSync(statusPath));
    const beforeTextHash = hash(JSON.stringify(beforeRows.transcripts));
    check('Native IPC targets the original isolated saved-audio meeting; no active capture');

    fs.copyFileSync(configuredRuntime, path.join(fixture.data_root, 'speaker-runtime.json'));
    const setup = await invoke('get_speaker_setup_status');
    assert.equal(setup.available, true);
    assert.equal(setup.enabled, true);
    assert.equal(setup.live_available, false);
    check('Native app recognizes the validated local post-call speaker runtime');
    let job = await invoke('start_speaker_identification', { transcriptionJobId: prior.job_id, meetingId: prior.meeting_id });
    if (['failed', 'stopped'].includes(job.state)) {
      job = await invoke('retry_speaker_identification', { jobId: job.job_id });
      report.retried_previous_interruption = true;
    }
    report.speaker_job_id = job.job_id;
    const deadline = Date.now() + 300000;
    let result;
    while (Date.now() < deadline) {
      const status = await invoke('get_speaker_job_status', { jobId: job.job_id });
      if (status.state === 'complete') { result = status; break; }
      if (['failed', 'stopped'].includes(status.state)) throw new Error(status.error || status.state);
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    assert(result, 'Native speaker worker must finish within the bounded deployment check');
    assert.equal(result.result.model_revision, config.model_revision);
    assert.equal(result.result.session_id, fixture.session_id);
    assert(result.result.turns.length > 0, 'Saved speech should produce at least one acoustic turn');
    assert(result.result.speakers.length > 0);
    assert(Object.keys(result.result.segment_assignments).length > 0);
    assert.equal(result.result.reconciliation.word_alignment_available, false);
    for (const assignment of Object.values(result.result.segment_assignments)) {
      assert.equal(assignment.assignment, 'window_candidates_only');
      assert.equal(assignment.speaker_id, null);
    }
    check('Real offline Community-1 worker returns turns, stable speaker IDs and honest window-level assignments');

    const afterRows = await invoke('api_get_meeting_transcripts', { meetingId: prior.meeting_id, limit: 1000, offset: 0 });
    const afterWorkspace = await invoke('load_transcript_workspace', { meetingId: prior.meeting_id });
    assert.equal(hash(JSON.stringify(afterRows.transcripts)), beforeTextHash);
    assert.equal(hash(fs.readFileSync(audio.path)), audioHash);
    assert.equal(hash(fs.readFileSync(statusPath)), transcriptionHash);
    for (const field of ['revision', 'notes', 'corrections', 'speaker_names']) {
      assert.deepEqual(afterWorkspace[field], beforeWorkspace[field], `Speaker processing must preserve ${field}`);
    }
    assert.equal(afterWorkspace.speaker_job_id, job.job_id);
    assert.deepEqual(afterWorkspace.speaker_metadata, result.result);
    const revisions = path.join(fixture.data_root, 'workspaces', prior.meeting_id, 'speaker-metadata');
    const revisionFiles = fs.readdirSync(revisions).sort();
    await invoke('get_speaker_job_status', { jobId: job.job_id });
    assert.deepEqual(fs.readdirSync(revisions).sort(), revisionFiles);
    check('Speaker metadata persists separately; audio, ASR, corrections, manual names and note revision remain unchanged');
    check('Repeated status retrieval does not create duplicate speaker revisions');
    report.source_identity = result.source_identity;
    report.audio_sha256 = audioHash;
    report.transcription_status_sha256 = transcriptionHash;
    report.asr_rows_sha256 = beforeTextHash;
    report.turn_count = result.result.turns.length;
    report.speaker_count = result.result.speakers.length;
    report.segment_assignment_count = Object.keys(result.result.segment_assignments).length;
    report.result = result.result;
    report.state = 'complete';
  } catch (error) {
    report.state = 'failed'; report.error = String(error.stack || error);
    process.exitCode = 1;
  } finally {
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(fixture.data_root, 'speaker-native-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
  console.log(JSON.stringify({ state: report.state, speaker_count: report.speaker_count, turn_count: report.turn_count, error: report.error }));
}
main().catch(error => { console.error(String(error)); process.exitCode = 1; });
