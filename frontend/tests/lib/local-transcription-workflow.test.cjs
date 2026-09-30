const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../src/lib/local-transcription-workflow.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS } });
const context = { exports: {}, require };
vm.runInNewContext(compiled.outputText, context);
const { LocalWorkflow, readPreferences, pathKey } = context.exports;
const clone = value => JSON.parse(JSON.stringify(value));
const capture = { session_id: 'session-one', session_dir: 'C:\\STTApp\\recordings\\one' };
const second = { session_id: 'session-two', session_dir: 'C:\\STTApp\\recordings\\two' };
const third = { session_id: 'session-three', session_dir: 'C:\\STTApp\\recordings\\three' };
const segment = (id = 'synthetic-one', text = 'मीरा ने seven notebooks कहा') => ({ id, text, source_track: 'system', start_seconds: 0, end_seconds: 20 });

function fixture(previous) {
  const calls = [], imports = [], stops = [], jobs = new Map(), speakers = new Map(), meetings = new Map(), bindings = new Map();
  const importFailures = new Set(), startFailures = new Set();
  let nextJob = 0, nextSpeaker = 0, stopFailures = 0, speakersEnabled = false, startGate;
  const dependencies = {
    invoke: async (command, args = {}) => {
      calls.push({ command, args: clone(args) });
      if (command === 'ensure_capture_meeting') {
        const key = pathKey(args.sessionDir);
        if (!meetings.has(key)) meetings.set(key, `meeting-${meetings.size + 1}`);
        return { meeting_id: meetings.get(key) };
      }
      if (command === 'start_local_transcription') {
        if (startFailures.has(args.workflowRole)) throw new Error('synthetic model unavailable');
        let job = args.workflowRole && [...jobs.values()].find(item => item.session_dir === args.sessionDir && item.workflow_role === args.workflowRole);
        if (!job) {
          job = { job_id: `job-${++nextJob}`, session_dir: args.sessionDir, profile: args.profile, workflow_role: args.workflowRole, state: 'running', segments: [] };
          jobs.set(job.job_id, job);
        }
        if (startGate) await startGate;
        return clone(job);
      }
      if (command === 'get_local_transcription_status') {
        assert.ok(jobs.has(args.jobId), `unknown synthetic job ${args.jobId}`);
        return clone(jobs.get(args.jobId));
      }
      if (command === 'list_local_transcription_jobs') return clone([...jobs.values()].filter(job => pathKey(job.session_dir) === pathKey(args.sessionDir)));
      if (command === 'stop_local_transcription') {
        if (stopFailures > 0) { stopFailures--; throw new Error('synthetic stop request failed'); }
        // Acknowledgment deliberately does not change status: Python may still be saving/exiting.
        return { job_id: args.jobId, state: 'stop_requested' };
      }
      if (command === 'resume_local_transcription') {
        const job = jobs.get(args.jobId); assert.ok(['failed', 'stopped'].includes(job.state));
        job.state = 'running'; return { job_id: args.jobId, state: 'running' };
      }
      if (command === 'import_local_transcription') {
        if (importFailures.has(args.jobId)) throw new Error('synthetic import failure');
        const id = args.primary ? args.meetingId : `meeting-local-${args.jobId}`;
        assert.ok(!bindings.has(id) || bindings.get(id) === args.jobId, 'a new job must not overwrite an occupied workspace');
        bindings.set(id, args.jobId);
        return { meeting_id: id };
      }
      if (command === 'get_speaker_setup_status') return { available: speakersEnabled, enabled: speakersEnabled };
      if (command === 'start_speaker_identification') {
        const job = { job_id: `speaker-${++nextSpeaker}`, state: 'diarizing', transcriptionJobId: args.transcriptionJobId };
        speakers.set(job.job_id, job); return clone(job);
      }
      if (command === 'get_speaker_job_status') return clone(speakers.get(args.jobId));
      if (command === 'retry_speaker_identification') { speakers.get(args.jobId).state = 'diarizing'; return clone(speakers.get(args.jobId)); }
      throw new Error(command);
    },
    changed: () => {}, imported: id => imports.push(id), stopped: (id, error) => stops.push({ id, error }),
  };
  const result = {
    workflow: new LocalWorkflow(dependencies, previous), calls, imports, stops, jobs, speakers, meetings, bindings,
    count: command => calls.filter(call => call.command === command).length,
    runs: (session = capture) => result.workflow.state.runs.filter(run => run.capture.session_id === session.session_id),
    draft: (session = capture) => result.runs(session).find(run => run.role === 'live-draft'),
    final: (session = capture) => result.runs(session).find(run => run.role === 'final'),
    status: (run, values) => Object.assign(jobs.get(run.job.job_id), clone(values)),
    failImport: (run, fail = true) => fail ? importFailures.add(run.job.job_id) : importFailures.delete(run.job.job_id),
    failStart: (role, fail = true) => fail ? startFailures.add(role) : startFailures.delete(role), failStops: count => { stopFailures = count; },
    enableSpeakers: () => { speakersEnabled = true; result.workflow.state.speakerSetup = undefined; },
    gateStarts: promise => { startGate = promise; },
    reload: () => {
      const saved = clone(result.workflow.state);
      for (const run of saved.runs) if (run.job) delete run.job.segments;
      result.workflow = new LocalWorkflow(dependencies, saved); return result.workflow;
    },
  };
  return result;
}
async function finishDraft(f, values = {}) {
  f.status(f.draft(), { state: 'stopped', ...values }); await f.workflow.tick();
}
async function startFinal(f) {
  await f.workflow.captureStarted(capture); await f.workflow.captureStopped(capture); await finishDraft(f);
  assert.ok(f.final().job); return f.final();
}

test('new recording defaults migrate profile/timing while preserving language and legacy run settings', () => {
  const preferences = readPreferences({ profile: 'trelis-15', timing: 'after-recording', languageMode: 'english' });
  assert.equal(preferences.profile, 'apex-20'); assert.equal(preferences.timing, 'during-recording'); assert.equal(preferences.languageMode, 'english');
  assert.equal(pathKey('\\\\?\\C:\\STTApp\\recordings\\one'), pathKey(capture.session_dir));
  const old = { capture, recording: false, primary: true, preferences: { profile: 'trelis-20', timing: 'after-recording', languageMode: 'hinglish' } };
  const f = fixture({ preferences, runs: [old] });
  assert.equal(f.workflow.state.runs[0].preferences.profile, 'trelis-20'); assert.equal(f.workflow.state.runs[0].role, undefined);
});

test('fresh capture creates one base meeting and distinct draft/final records but only starts Apex', async () => {
  const f = fixture(); await f.workflow.captureStarted(capture);
  assert.equal(f.runs().length, 2); assert.equal(f.count('ensure_capture_meeting'), 1); assert.equal(f.count('start_local_transcription'), 1);
  assert.equal(f.draft().preferences.profile, 'apex-20'); assert.equal(f.draft().primary, false);
  assert.equal(f.final().preferences.profile, 'trelis-20'); assert.equal(f.final().primary, true);
  assert.equal(f.final().job, undefined); assert.equal(f.final().recording, true);
  assert.equal(f.draft().meetingId, f.final().meetingId);
  assert.equal(f.calls.find(call => call.command === 'start_local_transcription').args.workflowRole, 'live-draft');
});

test('duplicate events request one graceful handoff, import the final draft checkpoint, then start distinct Final', async () => {
  const f = fixture(); await Promise.all([f.workflow.captureStarted(capture), f.workflow.captureStarted(capture)]);
  const raw = segment(); f.status(f.draft(), { segments: [raw] }); await f.workflow.tick();
  const draftId = f.draft().job.job_id, childId = f.draft().transcriptMeetingId;
  await Promise.all([f.workflow.captureStopped(capture), f.workflow.captureStopped(capture)]);
  assert.equal(f.count('stop_local_transcription'), 1); assert.equal(f.count('start_local_transcription'), 1);
  assert.equal(f.stops.length, 1); assert.equal(f.stops[0].id, f.final().meetingId); assert.notEqual(f.stops[0].id, childId);
  assert.equal(f.draft().job.state, 'running'); assert.equal(f.draft().recording, false); assert.equal(f.final().recording, false);
  await finishDraft(f, { segments: [raw, segment('synthetic-tail', 'The workshop is ready.')] });
  assert.equal(f.count('start_local_transcription'), 2); assert.equal(f.final().job.profile, 'trelis-20');
  assert.notEqual(f.final().job.job_id, draftId); assert.equal(f.draft().job.job_id, draftId); assert.equal(f.draft().job.segments[0].text, raw.text);
  const importAt = f.calls.findLastIndex(call => call.command === 'import_local_transcription' && call.args.jobId === draftId);
  const finalAt = f.calls.findIndex(call => call.command === 'start_local_transcription' && call.args.workflowRole === 'final');
  assert.ok(importAt < finalAt); assert.equal(f.draft().importedCount, 2); assert.equal(f.draft().importedState, 'stopped');
  f.status(f.final(), { segments: [segment('final-own-id', 'मीरा ने सात notebooks कहे')] }); await f.workflow.tick();
  assert.equal(f.final().transcriptMeetingId, f.final().meetingId); assert.notEqual(f.final().transcriptMeetingId, childId);
  await f.workflow.captureStarted(capture); await f.workflow.captureStopped(capture);
  assert.equal(f.runs().length, 2); assert.equal(f.count('start_local_transcription'), 2); assert.equal(f.stops.length, 1);
});

test('a terminal checkpoint masked as running does not release the model slot early', async () => {
  const f = fixture(); await f.workflow.captureStarted(capture); await f.workflow.captureStopped(capture);
  f.status(f.draft(), { state: 'running', checkpoint_state: 'stopped' }); await f.workflow.tick();
  assert.equal(f.final().job, undefined);
  await finishDraft(f); assert.ok(f.final().job);
});

test('failed draft import blocks handoff and explicit retry repairs import without resuming Apex', async () => {
  const f = fixture(); await f.workflow.captureStarted(capture); await f.workflow.captureStopped(capture);
  f.failImport(f.draft()); await finishDraft(f, { segments: [segment()] });
  assert.equal(f.final().job, undefined); assert.match(f.draft().error, /import failure/);
  await f.workflow.tick(); assert.equal(f.final().job, undefined);
  f.failImport(f.draft(), false); await f.workflow.retry(f.draft());
  assert.ok(f.final().job); assert.equal(f.count('resume_local_transcription'), 0);
});

test('Apex start failure never blocks saved capture or the Final stage', async () => {
  const f = fixture(); f.failStart('live-draft'); await f.workflow.captureStarted(capture);
  assert.equal(f.draft().recording, true); assert.match(f.draft().error, /unavailable/);
  await f.workflow.captureStopped(capture);
  assert.equal(f.draft().draftSkipped, true); assert.equal(f.draft().job, undefined); assert.ok(f.final().job);
  assert.equal(f.final().job.profile, 'trelis-20'); assert.equal(f.stops.length, 1);
});

test('failed Apex with an empty saved checkpoint can hand off to Final', async () => {
  const f = fixture(); await f.workflow.captureStarted(capture);
  f.status(f.draft(), { state: 'failed', segments: [], error: 'synthetic decoder failure' });
  await f.workflow.captureStopped(capture);
  assert.equal(f.draft().importedState, 'failed'); assert.equal(f.draft().importedCount, 0); assert.ok(f.final().job);
});

test('an eligible Final gets the free worker before another unstarted live Draft', async () => {
  const f = fixture(); await f.workflow.captureStarted(capture); await f.workflow.captureStopped(capture);
  await f.workflow.captureStarted(second); assert.equal(f.draft(second).job, undefined);
  await finishDraft(f);
  assert.ok(f.final().job); assert.equal(f.draft(second).job, undefined);
  assert.equal(f.calls.filter(call => call.command === 'start_local_transcription')[1].args.workflowRole, 'final');
});

test('missed live Draft stays visibly skipped after stop and is never backfilled', async () => {
  const f = fixture(); await startFinal(f); await f.workflow.captureStarted(second); await f.workflow.captureStopped(second);
  assert.equal(f.draft(second).draftSkipped, true); assert.equal(f.draft(second).job, undefined);
  await f.workflow.captureStarted(third);
  f.status(f.final(), { state: 'complete' }); await f.workflow.tick();
  assert.ok(f.final(second).job); assert.equal(f.draft(second).job, undefined); assert.equal(f.draft(third).job, undefined);
  assert.ok(!f.calls.some(call => call.command === 'start_local_transcription' && call.args.sessionDir === second.session_dir && call.args.workflowRole === 'live-draft'));
});

test('user pause during capture persists without resuming Draft, and Final still runs after stop', async () => {
  const f = fixture(); await f.workflow.captureStarted(capture); await f.workflow.stopWorker(f.draft());
  await finishDraft(f); await f.workflow.tick();
  assert.equal(f.draft().pausedByUser, true); assert.equal(f.draft().recording, true); assert.equal(f.final().job, undefined);
  assert.equal(f.count('resume_local_transcription'), 0); assert.equal(f.count('stop_recording'), 0);
  await f.workflow.captureStopped(capture); assert.ok(f.final().job);
});

test('pausing a queued Final persists across reload until explicit Retry', async () => {
  const f = fixture(); await f.workflow.captureStarted(capture); await f.workflow.stopWorker(f.final());
  await f.workflow.captureStopped(capture); await finishDraft(f);
  assert.equal(f.final().job, undefined); assert.equal(f.final().pausedByUser, true);
  f.reload(); await f.workflow.refreshAfterReload(null); assert.equal(f.final().job, undefined);
  await f.workflow.retry(f.final()); assert.ok(f.final().job); assert.equal(f.count('resume_local_transcription'), 0);
});

test('paused Final does not resume automatically after reload; Retry uses the same job', async () => {
  const f = fixture(); const final = await startFinal(f); const id = final.job.job_id;
  await f.workflow.stopWorker(final); f.status(final, { state: 'stopped' }); await f.workflow.tick();
  f.reload(); await f.workflow.refreshAfterReload(null);
  assert.equal(f.final().pausedByUser, true); assert.equal(f.count('resume_local_transcription'), 0);
  await f.workflow.retry(f.final()); assert.equal(f.count('resume_local_transcription'), 1); assert.equal(f.final().job.job_id, id);
});

test('failed Final requires explicit retry and preserves its identity', async () => {
  const f = fixture(); const final = await startFinal(f); const id = final.job.job_id;
  f.status(final, { state: 'failed', error: 'synthetic inference failure' }); await f.workflow.tick(); await f.workflow.tick();
  assert.equal(f.count('resume_local_transcription'), 0);
  await f.workflow.retry(final); assert.equal(f.count('resume_local_transcription'), 1); assert.equal(final.job.job_id, id);
});

test('a duplicate stop cannot clear a failed Final start and silently retry it', async () => {
  const f = fixture(); f.failStart('final');
  await f.workflow.captureStarted(capture); await f.workflow.captureStopped(capture); await finishDraft(f);
  assert.equal(f.final().job, undefined); assert.match(f.final().error, /unavailable/);
  const attempts = () => f.calls.filter(call => call.command === 'start_local_transcription' && call.args.workflowRole === 'final').length;
  await f.workflow.captureStopped(capture); await f.workflow.tick(); assert.equal(attempts(), 1);
  f.failStart('final', false); await f.workflow.retry(f.final()); assert.equal(attempts(), 2); assert.ok(f.final().job);
});

test('reload during handoff hydrates saved text and does not repeat stop or start', async () => {
  const f = fixture(); await f.workflow.captureStarted(capture); await f.workflow.captureStopped(capture);
  f.status(f.draft(), { state: 'stopped', segments: [segment()] }); f.reload(); await f.workflow.refreshAfterReload(null);
  assert.equal(f.count('stop_local_transcription'), 1); assert.equal(f.count('start_local_transcription'), 2);
  assert.equal(f.draft().job.segments.length, 1); assert.ok(f.final().job);
  await f.workflow.captureStopped(capture); assert.equal(f.stops.length, 1); assert.equal(f.count('start_local_transcription'), 2);
});

test('interrupted capture requires audio recovery before automatic Final', async () => {
  const f = fixture(); await f.workflow.captureStarted(capture);
  f.status(f.draft(), { state: 'failed', error: 'parent restarted' }); f.reload(); await f.workflow.refreshAfterReload(null);
  assert.ok(f.runs().every(run => run.needsRecovery)); assert.equal(f.final().job, undefined);
  await f.workflow.captureStopped(capture);
  assert.ok(f.runs().every(run => !run.needsRecovery)); assert.ok(f.final().job);
});

test('missing browser pointers rediscover native role jobs without starting or resuming a duplicate', async () => {
  const f = fixture(); await f.workflow.captureStarted(capture);
  const id = f.draft().job.job_id;
  f.status(f.draft(), { state: 'stopped', segments: [segment()] });
  f.workflow.state.runs = []; await f.workflow.refreshAfterReload(capture);
  assert.equal(f.runs().length, 2); assert.equal(f.draft().job.job_id, id);
  assert.equal(f.count('start_local_transcription'), 1); assert.equal(f.count('resume_local_transcription'), 0);
  assert.equal(f.final().job, undefined);
});

test('native legacy jobs stay roleless when browser pointers are missing', async () => {
  const f = fixture(); const job = { job_id: 'old-native-job', session_dir: capture.session_dir, profile: 'apex-20', state: 'stopped', segments: [segment()] };
  f.jobs.set(job.job_id, job); await f.workflow.refreshAfterReload(capture);
  assert.equal(f.runs().length, 1); assert.equal(f.runs()[0].role, undefined);
  assert.equal(f.count('start_local_transcription'), 0); assert.equal(f.count('resume_local_transcription'), 0);
  await f.workflow.captureStopped(capture); assert.equal(f.runs().length, 1); assert.equal(f.count('start_local_transcription'), 0);
});

test('failed graceful stop remains visible, is not flooded, and can be retried once explicitly', async () => {
  const f = fixture(); await f.workflow.captureStarted(capture); f.failStops(1); await f.workflow.captureStopped(capture);
  f.status(f.draft(), { segments: [segment()] }); await f.workflow.tick(); await f.workflow.tick();
  assert.equal(f.count('stop_local_transcription'), 1); assert.match(f.draft().error, /stop request failed/); assert.equal(f.final().job, undefined);
  await f.workflow.retry(f.draft()); assert.equal(f.count('stop_local_transcription'), 2);
  await finishDraft(f); assert.ok(f.final().job);
});

test('speakers run only for completed Final, never Draft, and failed speakers do not loop', async () => {
  const f = fixture(); f.enableSpeakers(); await f.workflow.captureStarted(capture);
  f.status(f.draft(), { state: 'complete', segments: [segment()] }); await f.workflow.captureStopped(capture);
  assert.equal(f.count('start_speaker_identification'), 0);
  f.status(f.final(), { state: 'complete', segments: [segment('final-segment')] }); await f.workflow.tick(); await f.workflow.tick();
  assert.equal(f.count('start_speaker_identification'), 1);
  const speakerCall = f.calls.find(call => call.command === 'start_speaker_identification');
  assert.equal(speakerCall.args.transcriptionJobId, f.final().job.job_id);
  f.speakers.get(f.final().speakerJob.job_id).state = 'failed'; await f.workflow.tick(); await f.workflow.tick();
  assert.equal(f.count('start_speaker_identification'), 1);
});

test('stopping while native start is in flight never mistakes the started Draft for skipped', async () => {
  const f = fixture(); let release; f.gateStarts(new Promise(resolve => { release = resolve; }));
  const started = f.workflow.captureStarted(capture);
  for (let i = 0; i < 20 && !f.count('start_local_transcription'); i++) await Promise.resolve();
  assert.equal(f.count('start_local_transcription'), 1);
  await f.workflow.captureStopped(capture); assert.notEqual(f.draft().draftSkipped, true); assert.equal(f.final().job, undefined);
  release(); await started; f.gateStarts(undefined); await f.workflow.tick();
  assert.ok(f.draft().job); assert.equal(f.count('stop_local_transcription'), 1); assert.equal(f.final().job, undefined);
  await finishDraft(f); assert.ok(f.final().job);
});

test('legacy roleless captures remain single-model and never gain an automatic Final', async () => {
  const oldPreferences = { profile: 'apex-20', languageMode: 'english', timing: 'during-recording' };
  const legacy = { capture, meetingId: 'legacy-meeting', recording: true, primary: true, preferences: oldPreferences };
  const f = fixture({ runs: [legacy] }); await f.workflow.captureStarted(capture); await f.workflow.captureStopped(capture);
  assert.equal(f.runs().length, 1); assert.equal(f.runs()[0].role, undefined); assert.equal(f.runs()[0].preferences.languageMode, 'english');
  assert.equal(f.count('start_local_transcription'), 1); assert.equal(f.count('stop_local_transcription'), 0);
  assert.equal(f.calls.find(call => call.command === 'start_local_transcription').args.workflowRole, undefined);
});

test('manual reruns retain the selected model and separate workspace without creating a pair', async () => {
  const f = fixture();
  const job = { job_id: 'manual-job', session_dir: capture.session_dir, profile: 'trelis-20', state: 'complete', segments: [segment()] };
  f.jobs.set(job.job_id, job); await f.workflow.adopt(job, 'base-meeting', false);
  const run = f.workflow.state.runs[0];
  assert.equal(run.role, undefined); assert.equal(run.preferences.profile, 'trelis-20'); assert.equal(run.transcriptMeetingId, 'meeting-local-manual-job');
  assert.equal(f.workflow.state.runs.length, 1); assert.equal(f.count('start_local_transcription'), 0);
});

test('manual versions cannot hijack a paired capture when a duplicate stop arrives', async () => {
  const f = fixture(); await startFinal(f);
  f.status(f.final(), { state: 'complete' }); await f.workflow.tick();
  const manual = { job_id: 'manual-copy', session_dir: capture.session_dir, capture_session_id: capture.session_id,
    profile: 'apex-20', state: 'complete', segments: [segment('manual-segment')] };
  f.jobs.set(manual.job_id, manual); await f.workflow.adopt(manual, f.final().meetingId, false);
  await f.workflow.captureStopped(capture);
  assert.equal(f.count('start_local_transcription'), 2); assert.equal(f.stops.length, 1);
  assert.equal(f.draft().captureEnded, true); assert.equal(f.final().captureEnded, true);
  assert.equal(f.workflow.state.runs.find(run => run.job?.job_id === manual.job_id).role, undefined);
});
