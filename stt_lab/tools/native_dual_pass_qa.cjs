/* Native IPC/storage integration with deterministic synthetic checkpoints.
 * Does not record devices or run speech models. Use only its isolated QA root. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

async function main() {
  const [endpoint, fixturePath] = process.argv.slice(2);
  if (!endpoint || !fixturePath) throw new Error('Pass localhost CDP endpoint and synthetic fixture.json');
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  assert.equal(fixture.synthetic_checkpoint, true);
  assert.equal(fixture.model_inference, false);
  const report = { started_at: new Date().toISOString(), device_recording: false, mocked_ipc: false,
    model_inference: false, synthetic_checkpoint: true, checks: [] };
  const browser = await chromium.connectOverCDP(endpoint);
  const page = browser.contexts().flatMap(context => context.pages()).find(item => /tauri|localhost/.test(item.url()));
  if (!page) throw new Error('Native app webview was not found');
  const invoke = (command, args = {}) => page.evaluate(({ command, args }) => window.__TAURI_INTERNALS__.invoke(command, args), { command, args })
    .catch(error => { throw error instanceof Error ? error : new Error(String(error)); });
  const checked = name => { report.checks.push(name); console.log(name); };
  const pathKey = value => { const normalized = path.resolve(value); return (normalized.startsWith('\\\\?\\') ? normalized.slice(4) : normalized).toLowerCase(); };
  const rows = id => invoke('api_get_meeting_transcripts', { meetingId: id, limit: 100, offset: 0 });
  const workspace = id => invoke('load_transcript_workspace', { meetingId: id });
  const save = (id, previous, overrides = {}) => invoke('save_transcript_workspace', { meetingId: id,
    expectedRevision: previous.revision, notes: previous.notes || '', corrections: previous.corrections || [],
    projectId: null, profile: previous.profile || null, speakerNames: previous.speaker_names || {}, ...overrides });
  const startArgs = role => ({ sessionDir: fixture.session_dir, profile: role === 'live-draft' ? 'apex-20' : 'trelis-20',
    languageMode: 'hinglish', projectId: null, workflowRole: role });
  const waitComplete = async job => {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      const status = await invoke('get_local_transcription_status', { jobId: job.job_id });
      if (status.state === 'complete') return status;
      if (['failed', 'stopped'].includes(status.state)) throw new Error(status.error || status.state);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('Synthetic worker timed out');
  };
  try {
    await page.waitForFunction(() => typeof window.__TAURI_INTERNALS__?.invoke === 'function');
    assert.equal(await invoke('get_active_capture'), null);
    const meeting = await invoke('ensure_capture_meeting', { sessionDir: fixture.session_dir });
    assert.equal((await invoke('ensure_capture_meeting', { sessionDir: fixture.session_dir })).meeting_id, meeting.meeting_id);
    report.source_meeting_id = meeting.meeting_id;
    // The source path confirms this app is attached to our disposable fixture.
    const audio = await invoke('local_get_meeting_audio', { meetingId: meeting.meeting_id });
    assert.equal(pathKey(audio.path), pathKey(path.join(fixture.session_dir, 'audio.wav')));
    const audioHash = crypto.createHash('sha256').update(fs.readFileSync(audio.path)).digest('hex');
    checked('Isolated data root confirmed through native saved-audio path; no device capture');

    await assert.rejects(() => invoke('start_local_transcription', { ...startArgs('final'),
      sessionDir: fixture.unfinished_session.session_dir }), /Finalize or recover/);
    await assert.rejects(() => invoke('start_local_transcription', { ...startArgs('final'), profile: 'apex-20' }), /Use Trelis 5s, 10s or 20s/);
    checked('Final pass rejects unfinished captures and swapped role/model combinations');

    const baseSaved = await save(meeting.meeting_id, await workspace(meeting.meeting_id), { notes: 'Source notes stay with the final transcript.' });
    const [draft, duplicateDraft] = await Promise.all([
      invoke('start_local_transcription', startArgs('live-draft')),
      invoke('start_local_transcription', startArgs('live-draft')),
    ]);
    assert.equal(draft.job_id, duplicateDraft.job_id);
    const draftStatus = await waitComplete(draft);
    assert.equal(draftStatus.workflow_role, 'live-draft');
    assert.equal(draftStatus.capture_session_id, fixture.session_id);
    assert.equal(draftStatus.language_mode, 'hinglish');
    const draftImport = await invoke('import_local_transcription', { jobId: draft.job_id, meetingId: meeting.meeting_id, primary: false });
    assert.notEqual(draftImport.meeting_id, meeting.meeting_id);
    assert.equal((await rows(meeting.meeting_id)).transcripts.length, 0);
    const draftRows = (await rows(draftImport.meeting_id)).transcripts;
    assert.deepEqual(draftRows.map(item => item.text), draftStatus.segments.map(item => item.text));
    const draftSaved = await save(draftImport.meeting_id, await workspace(draftImport.meeting_id), {
      notes: 'Draft-only review note.', profile: 'apex-20', corrections: [{ segment_id: draftRows[0].id,
        original_text: draftRows[0].text, text: 'Synthetic reviewer corrected draft.', updated_at: new Date().toISOString() }],
    });
    checked('Concurrent Draft starts deduplicate; raw draft imports as a separate child with independent corrections');

    const [final, duplicateFinal] = await Promise.all([
      invoke('start_local_transcription', startArgs('final')),
      invoke('start_local_transcription', startArgs('final')),
    ]);
    assert.equal(final.job_id, duplicateFinal.job_id);
    assert.notEqual(final.job_id, draft.job_id);
    const finalStatus = await waitComplete(final);
    assert.equal(finalStatus.workflow_role, 'final');
    const finalImport = await invoke('import_local_transcription', { jobId: final.job_id, meetingId: meeting.meeting_id, primary: true });
    assert.equal(finalImport.meeting_id, meeting.meeting_id);
    const finalRows = (await rows(meeting.meeting_id)).transcripts;
    assert.deepEqual(finalRows.map(item => item.text), finalStatus.segments.map(item => item.text));
    const afterFinal = await workspace(meeting.meeting_id);
    assert.equal(afterFinal.notes, baseSaved.notes);
    assert.equal(afterFinal.revision, baseSaved.revision);
    assert.equal(afterFinal.corrections.length, 0);
    assert.equal(afterFinal.workflow_role, 'final');
    assert.equal((await workspace(draftImport.meeting_id)).workflow_role, 'live-draft');
    const finalSaved = await save(meeting.meeting_id, afterFinal, { profile: 'trelis-20', corrections: [{
      segment_id: finalRows[0].id, original_text: finalRows[0].text,
      text: 'Synthetic reviewer corrected final.', updated_at: new Date().toISOString(),
    }] });
    assert.equal((await invoke('import_local_transcription', { jobId: final.job_id, meetingId: meeting.meeting_id, primary: true })).imported_count, 0);
    assert.deepEqual((await rows(meeting.meeting_id)).transcripts, finalRows);
    assert.deepEqual((await rows(draftImport.meeting_id)).transcripts, draftRows);
    const currentFinal = await workspace(meeting.meeting_id);
    const currentDraft = await workspace(draftImport.meeting_id);
    assert.deepEqual(currentFinal.corrections, finalSaved.corrections);
    assert.deepEqual(currentDraft.corrections, draftSaved.corrections);
    assert.equal(currentDraft.notes, draftSaved.notes);
    checked('Final becomes the primary; repeated import preserves both raw outputs, revisions, notes and per-pass corrections');

    const layers = await invoke('get_local_transcript_layers', { meetingId: meeting.meeting_id });
    const childLayers = await invoke('get_local_transcript_layers', { meetingId: draftImport.meeting_id });
    assert.deepEqual(childLayers, layers);
    assert.equal(layers.source_meeting_id, meeting.meeting_id);
    assert.equal(layers.layers.length, 2);
    assert.deepEqual(layers.layers.map(item => item.workflow_role).sort(), ['final', 'live-draft']);
    assert.equal(layers.layers.find(item => item.workflow_role === 'final').primary, true);
    assert.equal(layers.layers.find(item => item.workflow_role === 'live-draft').primary, false);
    assert.deepEqual(await invoke('get_local_transcript_groups'), [{ meeting_id: draftImport.meeting_id,
      source_meeting_id: meeting.meeting_id, workflow_role: 'live-draft' }]);
    report.layers = layers;
    checked('Recursive layer SQL resolves from either pass; sidebar groups only the Draft child');

    const manual = await invoke('start_local_transcription', { ...startArgs('final'), workflowRole: null });
    await waitComplete(manual);
    await assert.rejects(() => invoke('import_local_transcription', { jobId: manual.job_id,
      meetingId: meeting.meeting_id, primary: true }), /already has a transcript/);
    const comparison = await invoke('import_local_transcription', { jobId: manual.job_id, meetingId: draftImport.meeting_id, primary: false });
    assert.equal((await invoke('get_local_transcript_layers', { meetingId: comparison.meeting_id })).layers.length, 3);
    assert.equal((await invoke('get_local_transcript_groups')).length, 1);
    assert.equal((await invoke('start_local_transcription', startArgs('live-draft'))).job_id, draft.job_id);
    assert.equal((await invoke('start_local_transcription', startArgs('final'))).job_id, final.job_id);
    const launches = fs.readFileSync(path.join(fixture.data_root, 'synthetic-worker-starts.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(launches.length, 3);
    assert.equal(launches.filter(item => item.role === 'live-draft').length, 1);
    assert.equal(launches.filter(item => item.role === 'final').length, 1);
    checked('Occupied primary rejects replacement; nested comparison remains navigable; each automatic role launches only once');

    const deletion = await invoke('api_delete_meeting', { meetingId: meeting.meeting_id, authToken: null });
    assert.equal(deletion.status, 'success');
    for (const id of [meeting.meeting_id, draftImport.meeting_id, comparison.meeting_id]) {
      await assert.rejects(() => invoke('get_local_transcript_layers', { meetingId: id }), /Conversation not found/);
    }
    assert.deepEqual(await invoke('get_local_transcript_groups'), []);
    assert.equal(crypto.createHash('sha256').update(fs.readFileSync(audio.path)).digest('hex'), audioHash);
    assert(fs.existsSync(path.join(fixture.data_root, 'jobs', draft.job_id, 'status.json')));
    assert(fs.existsSync(path.join(fixture.data_root, 'jobs', final.job_id, 'status.json')));
    checked('Deleting the synthetic source removes all bound descendants and groups while retaining saved audio and raw job checkpoints');
    report.state = 'complete';
  } catch (error) {
    report.state = 'failed'; report.error = String(error.stack || error); throw error;
  } finally {
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(fixture.data_root, 'native-dual-pass-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
