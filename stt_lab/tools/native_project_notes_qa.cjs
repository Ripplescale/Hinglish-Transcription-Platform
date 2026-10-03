/* Actual native IPC/storage checks against an explicitly disposable fixture.
 * Completed fictional checkpoints only: no capture, workers, weights or cloud.
 * Prepare with prepare_native_live_workflow.py, launch the new executable with
 * STTAPP_DATA_DIR=<fixture root> and a separate WebView profile/CDP port, then:
 * node native_project_notes_qa.cjs http://127.0.0.1:<port> <root>/fixture.json
 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');

async function main() {
  const [endpoint, fixturePath] = process.argv.slice(2);
  if (!endpoint || !fixturePath) throw Error('Pass a localhost CDP endpoint and a fresh disposable fixture.json');
  const endpointUrl = new URL(endpoint);
  assert(['127.0.0.1', 'localhost', '[::1]'].includes(endpointUrl.hostname), 'Only a localhost QA browser is allowed');
  const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
  const fixture = read(fixturePath);
  assert.equal(fixture.synthetic_checkpoint, true);
  assert.equal(fixture.model_inference, false);
  assert.equal(fixture.device_recording, false);
  const root = path.resolve(fixture.data_root);
  const pathKey = file => path.resolve(file).replace(/^\\\\\?\\/, '').toLowerCase();
  assert.equal(pathKey(path.dirname(fixturePath)), pathKey(root));
  const rootFile = (...parts) => {
    const target = path.resolve(root, ...parts);
    assert(target.startsWith(`${root}${path.sep}`), 'QA writes must stay in the disposable root');
    return target;
  };
  const reportPath = rootFile('native-project-notes-report.json');
  assert(!fs.existsSync(reportPath), 'Use a fresh QA fixture rather than reusing prior evidence');
  const config = read(rootFile('runtime.json'));
  assert.equal(config.synthetic_only, true);
  assert.equal(pathKey(config.synthetic_data_root), pathKey(root));
  const report = { started_at: new Date().toISOString(), data_root: root, mocked_ipc: false,
    synthetic_checkpoint: true, device_recording: false, model_inference: false, worker_processes: false, checks: [] };
  const browser = await chromium.connectOverCDP(endpoint);
  let verifiedRoot = false;
  const digest = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  const checked = name => { report.checks.push(name); console.log(name); };
  try {
    const page = browser.contexts().flatMap(context => context.pages()).find(item => /tauri|localhost/.test(item.url()));
    if (!page) throw Error('Native QA webview not found');
    await page.waitForFunction(() => typeof window.__TAURI_INTERNALS__?.invoke === 'function');
    const invoke = (command, args = {}) => page.evaluate(({ command, args }) => window.__TAURI_INTERNALS__.invoke(command, args), { command, args })
      .catch(error => { throw error instanceof Error ? error : Error(String(error)); });
    // Read-only proof precedes every mutation, including SQLite fixture seeding.
    assert.equal(pathKey(await invoke('get_database_directory')), pathKey(root));
    assert.equal(await invoke('get_active_capture'), null);
    verifiedRoot = true;
    const existing = await invoke('api_get_meetings');
    assert.equal(existing.length, 0, 'A fresh fictional database is required');
    const dbPath = rootFile('meeting_minutes.sqlite');
    assert(fs.existsSync(dbPath));
    const seed = String.raw`
import json, sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    columns = list(db.execute('PRAGMA table_info(meeting_notes)'))
    keys = [row[1] for row in columns if row[5]]
    assert keys == ['meeting_id'], keys
    for mid, title in [('qa-legacy-note','Fictional older meeting'),('qa-assigned-note','Fictional filed meeting')]:
        db.execute('INSERT INTO meetings(id,title,created_at,updated_at) VALUES(?,?,?,?)',(mid,title,'2026-10-01T10:00:00Z','2026-10-01T10:00:00Z'))
    db.execute('INSERT INTO meeting_notes(meeting_id,notes_markdown,notes_json,created_at,updated_at) VALUES(?,?,?,?,?)',('qa-legacy-note','Older handwritten note.\nMaya checked the room.',json.dumps({'fictional':True}),'2026-10-01T10:00:00Z','2026-10-01T10:00:00Z'))
    print(json.dumps({'meeting_notes_primary_key':keys,'seeded_meetings':2}))
`;
    report.schema = JSON.parse(execFileSync(config.python_executable, ['-c', seed, dbPath], { encoding: 'utf8', windowsHide: true }));
    const library = () => invoke('list_project_library');
    const workspace = id => invoke('load_transcript_workspace', { meetingId: id });
    const rows = id => invoke('api_get_meeting_transcripts', { meetingId: id, limit: 100, offset: 0 });
    const saveNote = (id, previous, notes, projectId = previous.project_id) => invoke('save_meeting_note', { meetingId: id, expectedRevision: previous.revision, notes, projectId });
    const initial = await library();
    assert.equal(initial.meetings.length, 2);
    assert.equal(new Set(initial.meetings.map(item => item.id)).size, 2);
    const legacyRow = initial.meetings.find(item => item.id === 'qa-legacy-note');
    assert.equal(legacyRow.project_id, null);
    assert.match(legacyRow.notes_preview, /Older handwritten note/);
    assert(!fs.existsSync(rootFile('workspaces', 'qa-legacy-note')), 'Library reads must not materialize or assign legacy notes');
    checked('Disposable native root proven; one-to-one legacy notes join; previews preserve unfiled legacy recordings without creating workspaces');

    const project = await invoke('create_project', { name: '  Willow research  ' });
    assert.equal(project.name, 'Willow research'); assert.equal(project.entry_count, 0);
    const reference = (id, canonical) => ({ id, kind: 'term', canonical, aliases: [], source: 'Fictional checked project note', verified: true, context: 'Workshop planning', unit: '', valid_from: '', valid_to: '' });
    const entries = [reference('willow-term', 'Willow'), reference('workshop-term', 'Workshop')];
    const relationships = [{ subject_id: 'willow-term', predicate: 'contains checked note about', object_id: 'workshop-term', source: 'Fictional project document', verified: true }];
    const evidence = await invoke('save_project_vault', { projectId: project.id, expectedRevision: project.revision, name: project.name, entries, relationships });
    const evidenceFile = rootFile('vaults', project.id, `revision-${String(evidence.revision).padStart(12, '0')}.json`);
    const evidenceHash = digest(evidenceFile);
    const renamed = await invoke('rename_project', { projectId: project.id, expectedRevision: evidence.revision, name: 'Willow studio' });
    assert.equal(renamed.name, 'Willow studio'); assert.equal(renamed.entry_count, 2);
    const renamedVault = await invoke('load_project_vault', { projectId: project.id });
    assert.deepEqual(renamedVault.entries, evidence.entries); assert.deepEqual(renamedVault.relationships, evidence.relationships);
    await assert.rejects(() => invoke('rename_project', { projectId: project.id, expectedRevision: evidence.revision, name: 'Stale rename' }), /changed in another window/);
    assert.equal(digest(evidenceFile), evidenceHash);
    const previousProject = initial.projects[0];
    assert(previousProject, 'Initial vault should remain available');
    const assigned = await saveNote('qa-assigned-note', await workspace('qa-assigned-note'), 'Previously assigned project remains assigned.', previousProject.id);
    assert.equal((await library()).meetings.find(item => item.id === 'qa-assigned-note').project_id, assigned.project_id);
    checked('Project create/rename persists and retains checked entries, relationships and prior revisions; existing saved project assignments survive');

    const source = await invoke('ensure_capture_meeting', { sessionDir: fixture.session_dir });
    const audio = await invoke('local_get_meeting_audio', { meetingId: source.meeting_id });
    assert.equal(pathKey(audio.path), pathKey(path.join(fixture.session_dir, 'audio.wav')));
    const audioHash = digest(audio.path);
    const writeJson = (file, value) => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, JSON.stringify(value, null, 2)); };
    const checkpoint = (id, role, profile, count = 2) => {
      const dir = rootFile('jobs', id);
      const request = { job_id: id, session_dir: fixture.session_dir, capture_session_id: fixture.session_id, profile, workflow_role: role, language_mode: 'hinglish', created_at: '2026-10-03T10:00:00Z' };
      const segments = Array.from({ length: count }, (_, index) => ({ id: `${id}-${index}`, text: `Fictional raw ${index + 1}: आज Willow workshop की बात हुई।`, source_track: index % 2 ? 'microphone' : 'system', start_seconds: index * 5, end_seconds: (index + 1) * 5, final: true }));
      writeJson(path.join(dir, 'request.json'), request);
      writeJson(path.join(dir, 'status.json'), { ...request, state: 'complete', segments, processed_audio_seconds: count * 5, available_audio_seconds: count * 5, backlog_seconds: 0, synthetic_checkpoint: true });
      return { id, segments };
    };
    const primary = checkpoint('qa-primary', 'live-final', 'trelis-20');
    await invoke('import_local_transcription', { jobId: primary.id, meetingId: source.meeting_id, primary: true });
    const originalRows = (await rows(source.meeting_id)).transcripts;
    const first = await workspace(source.meeting_id);
    const checkedWorkspace = await invoke('save_transcript_workspace', { meetingId: source.meeting_id, expectedRevision: first.revision,
      notes: 'Initial fictional note', projectId: project.id, profile: 'trelis-20', speakerNames: { 'speaker-one': 'Maya' },
      corrections: [{ segment_id: originalRows[0].id, original_text: originalRows[0].text, text: 'Fictional human-checked wording.', updated_at: '2026-10-03T10:00:00Z' }] });
    const workspaceFile = rootFile('workspaces', source.meeting_id, `revision-${String(checkedWorkspace.revision).padStart(12, '0')}.json`);
    const workspaceHash = digest(workspaceFile);
    const saved = await saveNote(source.meeting_id, checkedWorkspace, 'New notes; corrections are independent.', project.id);
    assert.equal(saved.revision, checkedWorkspace.revision + 1);
    assert.deepEqual(saved.corrections, checkedWorkspace.corrections); assert.deepEqual(saved.speaker_names, checkedWorkspace.speaker_names);
    assert.equal(saved.profile, 'trelis-20'); assert.equal(saved.source_job_id, primary.id);
    await assert.rejects(() => saveNote(source.meeting_id, checkedWorkspace, 'Stale notes'), /changed in another window/);
    await assert.rejects(() => saveNote(source.meeting_id, saved, 'Missing project must not save', 'missing-project'), /Project no longer exists/);
    await assert.rejects(() => saveNote('missing-meeting', { revision: 0, project_id: null }, 'Orphan note'), /Conversation no longer exists/);
    assert.equal((await workspace(source.meeting_id)).notes, saved.notes);
    assert.equal(digest(workspaceFile), workspaceHash); assert.deepEqual((await rows(source.meeting_id)).transcripts, originalRows);
    checked('Notes-only native save preserves raw transcripts, corrections, speaker names and immutable history; stale or missing-object writes are rejected');

    checkpoint(primary.id, 'live-final', 'trelis-20', 3);
    const [imported, concurrentSave] = await Promise.all([
      invoke('import_local_transcription', { jobId: primary.id, meetingId: source.meeting_id, primary: true }),
      saveNote(source.meeting_id, saved, 'Typing while transcript metadata updates.', project.id),
    ]);
    assert.equal(imported.imported_count, 1);
    const afterImport = await workspace(source.meeting_id);
    assert.equal(afterImport.revision, concurrentSave.revision); assert.equal(afterImport.notes, concurrentSave.notes);
    assert.deepEqual(afterImport.corrections, saved.corrections); assert.deepEqual(afterImport.speaker_names, saved.speaker_names);
    assert.equal(Object.keys(afterImport.segment_metadata).length, 3);
    const progressedRows = (await rows(source.meeting_id)).transcripts;
    assert.deepEqual(progressedRows.slice(0, originalRows.length), originalRows);
    assert.equal((await saveNote(source.meeting_id, afterImport, afterImport.notes, afterImport.project_id)).revision, afterImport.revision);
    checked('Concurrent transcript import and notes autosave keep both changes; progress metadata does not invalidate notes; unchanged autosave creates no revision');

    const versions = [];
    for (const [id, role, profile] of [['qa-fallback', 'fallback', 'apex-20'], ['qa-draft', 'live-draft', 'apex-20'], ['qa-independent', null, 'trelis-20']]) {
      checkpoint(id, role, profile);
      const importedVersion = await invoke('import_local_transcription', { jobId: id, meetingId: source.meeting_id, primary: false });
      versions.push({ role, meetingId: importedVersion.meeting_id });
    }
    const completeLibrary = await library();
    const ids = completeLibrary.meetings.map(meeting => meeting.id);
    assert.equal(ids.length, new Set(ids).size);
    assert(ids.includes(source.meeting_id)); assert(ids.includes('qa-legacy-note')); assert(ids.includes('qa-assigned-note'));
    for (const version of versions) assert.equal(ids.includes(version.meetingId), version.role === null);
    const primaryRow = completeLibrary.meetings.find(meeting => meeting.id === source.meeting_id);
    assert.equal(primaryRow.project_id, project.id); assert.equal(primaryRow.notes_preview, afterImport.notes);
    const migratedLegacy = await workspace('qa-legacy-note');
    assert.equal(migratedLegacy.project_id, null); assert.match(migratedLegacy.notes, /Older handwritten note/);
    assert.equal(JSON.parse(migratedLegacy.legacy_notes_json).fictional, true);
    assert.equal(digest(audio.path), audioHash); assert.equal(digest(workspaceFile), workspaceHash); assert.equal(digest(evidenceFile), evidenceHash);
    assert(!fs.existsSync(rootFile('synthetic-worker-starts.jsonl')), 'No model or synthetic worker may have been launched');
    assert.equal(await invoke('get_active_capture'), null);
    checked('Library groups automatic draft/fallback versions, retains independent comparisons and legacy notes; original audio, raw prefix and reference history stay unchanged');
    report.source_meeting_id = source.meeting_id; report.project_id = project.id; report.library = completeLibrary;
    report.state = 'passed';
  } catch (error) {
    report.state = 'failed'; report.error = String(error.stack || error); throw error;
  } finally {
    report.finished_at = new Date().toISOString();
    if (verifiedRoot) fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
