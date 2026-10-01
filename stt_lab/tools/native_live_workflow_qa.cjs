/* Native IPC integration against a prepared, disposable live-workflow fixture.
 * Fictional checkpoints only. Never starts device capture or loads ASR weights. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

async function main() {
  const [endpoint, fixturePath] = process.argv.slice(2);
  if (!endpoint || !fixturePath) throw new Error('Pass localhost CDP endpoint and live fixture.json');
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  assert.equal(fixture.live_workflow_fixture, true);
  assert.equal(fixture.synthetic_checkpoint, true);
  assert.equal(fixture.model_inference, false);
  assert.equal(fixture.device_recording, false);
  const root = path.resolve(fixture.data_root);
  assert.equal(path.dirname(path.resolve(fixturePath)), root);
  const report = { started_at: new Date().toISOString(), device_recording: false,
    mocked_ipc: false, model_inference: false, synthetic_checkpoint: true, checks: [] };
  const browser = await chromium.connectOverCDP(endpoint);
  const page = browser.contexts().flatMap(context => context.pages()).find(item => /tauri|localhost/.test(item.url()));
  if (!page) throw new Error('Native app webview not found');
  const invoke = (command, args = {}) => page.evaluate(({ command, args }) => window.__TAURI_INTERNALS__.invoke(command, args), { command, args })
    .catch(error => { throw error instanceof Error ? error : new Error(String(error)); });
  const checked = name => { report.checks.push(name); console.log(name); };
  const digest = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  const pathKey = file => path.resolve(file).replace(/^\\\\\?\\/, '').toLowerCase();
  const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
  const write = (file, value) => { fs.writeFileSync(`${file}.tmp`, JSON.stringify(value, null, 2)); fs.renameSync(`${file}.tmp`, file); };
  const jobRoot = job => path.join(root, 'jobs', job.job_id);
  const rows = id => invoke('api_get_meeting_transcripts', { meetingId: id, limit: 100, offset: 0 });
  const workspace = id => invoke('load_transcript_workspace', { meetingId: id });
  const save = (id, previous, overrides = {}) => invoke('save_transcript_workspace', { meetingId: id,
    expectedRevision: previous.revision, notes: previous.notes || '', corrections: previous.corrections || [],
    projectId: null, profile: previous.profile || null, speakerNames: previous.speaker_names || {}, ...overrides });
  const startArgs = role => ({ sessionDir: fixture.live_session.session_dir,
    profile: role === 'fallback' ? 'apex-20' : 'trelis-20', languageMode: 'hinglish',
    projectId: null, workflowRole: role });
  const waitState = async (job, wanted) => {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      const status = await invoke('get_local_transcription_status', { jobId: job.job_id });
      if (wanted.includes(status.state)) return status;
      if (['failed', 'stopped'].includes(status.state)) throw new Error(status.error || status.state);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error(`Synthetic worker did not reach ${wanted.join('/')}`);
  };
  let live;
  try {
    await page.waitForFunction(() => typeof window.__TAURI_INTERNALS__?.invoke === 'function');
    assert.equal(await invoke('get_active_capture'), null);
    const meeting = await invoke('ensure_capture_meeting', { sessionDir: fixture.live_session.session_dir });
    const audio = await invoke('local_get_meeting_audio', { meetingId: meeting.meeting_id });
    assert.equal(pathKey(audio.path), pathKey(path.join(fixture.live_session.session_dir, 'audio.wav')));
    const audioHash = digest(audio.path);
    const configPath = path.join(root, 'runtime.json');
    const originalConfig = read(configPath);
    assert.equal(originalConfig.synthetic_only, true);
    assert.equal(pathKey(originalConfig.synthetic_data_root), pathKey(root));
    checked('Isolated fictional root confirmed through native saved-audio lookup; no capture devices');

    await assert.rejects(() => invoke('start_local_transcription', { ...startArgs('live-final'), profile: 'apex-20' }), /Use Trelis 20s/);
    await assert.rejects(() => invoke('start_local_transcription', { ...startArgs('fallback'), profile: 'trelis-20' }), /Use Trelis 20s/);
    await assert.rejects(() => invoke('start_local_transcription', startArgs('final')), /Finalize or recover/);
    const [first, duplicate] = await Promise.all([
      invoke('start_local_transcription', startArgs('live-final')),
      invoke('start_local_transcription', startArgs('live-final')),
    ]);
    live = first;
    assert.equal(first.job_id, duplicate.job_id);
    const partial = await waitState(live, ['waiting_for_audio']);
    assert.equal(partial.workflow_role, 'live-final');
    assert.equal(partial.runtime_marker, 'original');
    assert.equal(partial.segments.length, 1);
    const snapshotPath = path.join(jobRoot(live), 'runtime-snapshot.json');
    const snapshotHash = digest(snapshotPath);
    assert.deepEqual(read(snapshotPath), originalConfig);
    const imported = await invoke('import_local_transcription', { jobId: live.job_id, meetingId: meeting.meeting_id, primary: true });
    assert.equal(imported.meeting_id, meeting.meeting_id);
    const partialRows = (await rows(meeting.meeting_id)).transcripts;
    assert.deepEqual(partialRows.map(row => row.text), partial.segments.map(row => row.text));
    const saved = await save(meeting.meeting_id, await workspace(meeting.meeting_id), {
      notes: 'Fictional reviewer notes belong to this live transcript.', profile: 'trelis-20',
      corrections: [{ segment_id: partialRows[0].id, original_text: partialRows[0].text,
        text: 'Fictional corrected wording: 18 pencils.', updated_at: new Date().toISOString() }],
    });
    checked('Live Trelis accepts unfinished source, deduplicates starts, imports incremental primary and retains original-script raw text');

    await invoke('stop_local_transcription', { jobId: live.job_id });
    const stopped = await waitState(live, ['stopped']);
    assert.deepEqual(stopped.segments, partial.segments);
    const nextWorker = path.join(root, 'synthetic_worker_v2.py');
    const nextRegistry = path.join(root, 'synthetic_registry_v2.json');
    fs.copyFileSync(originalConfig.worker_script, nextWorker);
    fs.copyFileSync(originalConfig.registry_path, nextRegistry);
    const replacement = structuredClone(originalConfig);
    replacement.worker_script = nextWorker;
    replacement.registry_path = nextRegistry;
    replacement.synthetic_runtime_marker = 'replacement';
    replacement.models.trelis.backend = 'transformers';
    replacement.models.trelis.device = 'cpu';
    write(configPath, replacement);
    await assert.rejects(() => invoke('start_local_transcription', { ...startArgs('live-final'), sessionDir: fixture.session_dir }), /local GPU runtime/);
    replacement.models.trelis.backend = 'openvino';
    replacement.models.trelis.device = 'GPU';
    write(configPath, replacement);
    checked('Stopped live job retains raw prefix; fresh live job rejects CPU-only setup');

    const [fallback, duplicateFallback] = await Promise.all([
      invoke('start_local_transcription', startArgs('fallback')),
      invoke('start_local_transcription', startArgs('fallback')),
    ]);
    assert.equal(fallback.job_id, duplicateFallback.job_id);
    const fallbackStatus = await waitState(fallback, ['complete']);
    assert.equal(fallbackStatus.runtime_marker, 'replacement');
    assert.equal(fallbackStatus.workflow_role, 'fallback');
    const fallbackImport = await invoke('import_local_transcription', { jobId: fallback.job_id, meetingId: meeting.meeting_id, primary: false });
    assert.notEqual(fallbackImport.meeting_id, meeting.meeting_id);
    const fallbackRows = (await rows(fallbackImport.meeting_id)).transcripts;
    const fallbackSaved = await save(fallbackImport.meeting_id, await workspace(fallbackImport.meeting_id), {
      notes: 'Fictional fallback review.', profile: 'apex-20', corrections: [{ segment_id: fallbackRows[0].id,
        original_text: fallbackRows[0].text, text: 'Separate fallback correction.', updated_at: new Date().toISOString() }],
    });
    assert.deepEqual(await invoke('get_local_transcript_groups'), [{ meeting_id: fallbackImport.meeting_id,
      source_meeting_id: meeting.meeting_id, workflow_role: 'fallback' }]);
    assert.deepEqual((await rows(meeting.meeting_id)).transcripts, partialRows);
    checked('Apex fallback uses newly configured runtime, imports a grouped child and keeps separate corrections');

    await invoke('resume_local_transcription', { jobId: live.job_id });
    const resumed = await waitState(live, ['waiting_for_audio']);
    assert.equal(resumed.runtime_marker, 'original');
    assert.deepEqual(resumed.segments, partial.segments);
    assert.equal(digest(snapshotPath), snapshotHash);
    fs.appendFileSync(path.join(fixture.live_session.session_dir, 'timeline.jsonl'), `${JSON.stringify({ kind: 'capture_finalized', synthetic_only: true })}\n`);
    const complete = await waitState(live, ['complete']);
    assert.equal(complete.runtime_marker, 'original');
    assert.equal(complete.segments.length, 2);
    assert.deepEqual(complete.segments.slice(0, 1), partial.segments);
    const finishedImport = await invoke('import_local_transcription', { jobId: live.job_id, meetingId: meeting.meeting_id, primary: true });
    assert.equal(finishedImport.imported_count, 1);
    assert.equal((await invoke('import_local_transcription', { jobId: live.job_id, meetingId: meeting.meeting_id, primary: true })).imported_count, 0);
    assert.deepEqual((await rows(meeting.meeting_id)).transcripts.map(row => row.text), complete.segments.map(row => row.text));
    const current = await workspace(meeting.meeting_id);
    assert.equal(current.notes, saved.notes);
    assert.equal(current.revision, saved.revision);
    assert.deepEqual(current.corrections, saved.corrections);
    assert.equal(current.workflow_role, 'live-final');
    const currentFallback = await workspace(fallbackImport.meeting_id);
    assert.deepEqual(currentFallback.corrections, fallbackSaved.corrections);
    assert.equal(currentFallback.notes, fallbackSaved.notes);
    assert.deepEqual((await rows(fallbackImport.meeting_id)).transcripts, fallbackRows);
    checked('Resume keeps original snapshot despite changed worker/registry defaults; final append preserves raw prefix, notes and correction revision');

    const layers = await invoke('get_local_transcript_layers', { meetingId: fallbackImport.meeting_id });
    assert.equal(layers.source_meeting_id, meeting.meeting_id);
    assert.deepEqual(layers.layers.map(layer => layer.workflow_role).sort(), ['fallback', 'live-final']);
    assert.equal(layers.layers.find(layer => layer.workflow_role === 'live-final').primary, true);
    assert.equal(layers.layers.find(layer => layer.workflow_role === 'fallback').primary, false);
    const jobs = await invoke('list_local_transcription_jobs', { sessionDir: fixture.live_session.session_dir });
    assert.equal(jobs.length, 2);
    assert.equal(jobs.filter(job => job.workflow_role === 'final').length, 0);
    assert.equal((await invoke('start_local_transcription', startArgs('live-final'))).job_id, live.job_id);
    const launches = fs.readFileSync(path.join(root, 'synthetic-worker-starts.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(launches.length, 3);
    const liveLaunches = launches.filter(item => item.job_id === live.job_id);
    assert.equal(liveLaunches.length, 2);
    assert(liveLaunches.every(item => item.runtime_marker === 'original' && pathKey(item.worker_script) === pathKey(originalConfig.worker_script)
      && pathKey(item.registry_path) === pathKey(originalConfig.registry_path) && pathKey(item.config_path) === pathKey(snapshotPath)));
    assert.equal(digest(audio.path), audioHash);
    assert.equal(digest(snapshotPath), snapshotHash);
    assert.equal(await invoke('get_active_capture'), null);
    report.layers = layers; report.launches = launches; report.state = 'complete';
    checked('Layer navigation resolves both versions; no duplicate after-call pass; three expected launches only; original audio unchanged');
  } catch (error) {
    report.state = 'failed'; report.error = String(error.stack || error); throw error;
  } finally {
    if (live) {
      const status = await invoke('get_local_transcription_status', { jobId: live.job_id }).catch(() => null);
      if (status && !['complete', 'stopped', 'failed'].includes(status.state)) {
        await invoke('stop_local_transcription', { jobId: live.job_id }).catch(() => {});
      }
    }
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(root, 'native-live-workflow-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
