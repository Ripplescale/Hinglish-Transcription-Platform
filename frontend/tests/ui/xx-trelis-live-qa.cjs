/* Fictional browser-only workflow. Native capture, model execution and cloud are disabled. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { installFixture } = require('./xx-fixture.cjs');

async function installLiveFixture(page) {
  await installFixture(page);
  await page.addInitScript(() => {
    const original = window.__TAURI_INTERNALS__.invoke;
    const transform = window.__TAURI_INTERNALS__.transformCallback;
    const callbacks = new Map(), listeners = new Map(), jobs = new Map();
    const imported = { primary: [], fallback: [] };
    let recording = false, listenerId = 0;
    const capture = { session_id: 'qa-session', session_dir: 'C:/STTApp/recordings/qa-session' };
    const fixtureText = {
      'live-final': [
        'Welcome back, everyone. आज Project Willow की workshop plan करते हैं. Maya, would you like to walk us through the new sketchbook idea?',
        'Sure. We could start with a short drawing exercise, then give everyone time to share. कोई perfect drawing नहीं चाहिए — just something that starts a conversation.',
        'For the practice run, let us prepare 24 sketchbooks and 36 pencils. Arjun, can you check the room booking for half past three?',
        'हाँ, मैं booking confirm कर दूँगा. The courtyard could work if the weather is nice; otherwise we have the small studio as a backup.',
        'Lovely. I will put together the invitation. अगले Friday तक draft ready हो जाएगा.',
      ],
      fallback: ['Welcome back. Aaj Project Willow ki workshop plan karte hain.', 'Maya, let us prepare 24 sketchbooks and 36 pencils.'],
    };
    window.qaWorkspace().workflow_role = 'live-final';
    window.qaWorkspace('qa-draft').workflow_role = 'fallback';
    window.qaWorkspace('qa-draft').notes = 'A separate fallback note, kept with this version.';
    window.qaLiveState = () => ({ recording, jobs: [...jobs.values()], imported });
    window.qaSetJob = (role, state, count) => {
      const job = jobs.get(role); if (!job) throw Error('Missing synthetic job');
      job.state = state;
      job.phase = 'transcribing';
      job.segments = fixtureText[role].slice(0, count).map((text, index) => ({ id: `${role}-${index}`, text,
        source_track: index % 2 ? 'microphone' : 'system', start_seconds: index * 20, end_seconds: (index + 1) * 20 }));
      job.processed_audio_seconds = count * 20; job.available_audio_seconds = count * 20;
      job.backlog_seconds = state === 'complete' ? 0 : 20;
    };
    window.qaEmit = (event, payload) => { for (const [id, listener] of listeners) if (listener.event === event) callbacks.get(listener.handler)?.({ event, id, payload }); };
    window.qaStart = () => { recording = true; window.qaEmit('recording-started', { capture }); };
    window.qaStop = () => { recording = false; window.qaEmit('recording-stopped', { capture, folder_path: capture.session_dir }); };
    window.__TAURI_INTERNALS__.transformCallback = fn => { const id = transform(fn); callbacks.set(id, fn); return id; };
    window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener(_event, id) { listeners.delete(id); } };
    window.__TAURI_INTERNALS__.invoke = async (command, args = {}) => {
      const handled = /^(plugin:event\||get_active_capture$|is_recording$|get_recording_state$|ensure_capture_meeting$|start_local_transcription$|get_local_transcription_status$|stop_local_transcription$|resume_local_transcription$|import_local_transcription$|list_local_transcription_jobs$|get_local_transcript_layers$|get_local_transcript_groups$|api_get_meetings$|api_get_meeting_transcripts$|get_speaker_setup_status$)/.test(command);
      if (!handled) return original(command, args);
      window.qaCalls.push({ command, args, synthetic: true });
      if (command === 'plugin:event|listen') { listeners.set(++listenerId, args); return listenerId; }
      if (command === 'plugin:event|unlisten') { listeners.delete(args.eventId); return null; }
      if (command.startsWith('plugin:event|')) return null;
      if (command === 'get_active_capture') return recording ? capture : null;
      if (command === 'is_recording') return recording;
      if (command === 'get_recording_state') return { is_recording: recording, is_paused: false, is_active: recording, recording_duration: recording ? 62 : 0, active_duration: recording ? 62 : 0 };
      if (command === 'get_speaker_setup_status') return { available: false, enabled: false };
      if (command === 'ensure_capture_meeting') return { meeting_id: 'qa-meeting' };
      if (command === 'start_local_transcription') {
        if (!['live-final', 'fallback'].includes(args.workflowRole)) throw Error('Unexpected synthetic role');
        if (jobs.has(args.workflowRole)) throw Error('Duplicate synthetic job');
        const job = { job_id: `qa-${args.workflowRole}`, session_dir: capture.session_dir, capture_session_id: capture.session_id,
          profile: args.profile, final_profile: args.finalProfile, workflow_role: args.workflowRole, state: 'running', phase: args.workflowRole === 'live-final' ? 'loading_model' : 'transcribing', segments: [] };
        jobs.set(args.workflowRole, job); return structuredClone(job);
      }
      if (command === 'list_local_transcription_jobs') return structuredClone([...jobs.values()]);
      const job = [...jobs.values()].find(item => item.job_id === args.jobId);
      if (command === 'get_local_transcription_status') return structuredClone(job);
      if (command === 'stop_local_transcription') { job.stop_requested = true; return { state: 'stop_requested' }; }
      if (command === 'resume_local_transcription') { job.state = 'running'; return { state: 'running' }; }
      if (command === 'import_local_transcription') {
        const layer = args.primary ? 'primary' : 'fallback';
        imported[layer] = job.segments.map(segment => ({ ...segment, audio_start_time: segment.start_seconds, audio_end_time: segment.end_seconds, timestamp: '15:00' }));
        const workspace = window.qaWorkspace(args.primary ? undefined : 'qa-draft');
        workspace.source_job_id = job.job_id; workspace.workflow_role = job.workflow_role; workspace.profile = job.profile;
        return { meeting_id: args.primary ? 'qa-meeting' : 'qa-draft' };
      }
      if (command === 'api_get_meeting_transcripts') {
        const rows = imported[args.meetingId === 'qa-draft' ? 'fallback' : 'primary'];
        return { transcripts: rows.slice(args.offset, args.offset + args.limit), total_count: rows.length, has_more: args.offset + args.limit < rows.length };
      }
      if (command === 'api_get_meetings') {
        const rows = await original(command, args);
        return rows.filter(row => row.id !== 'qa-draft' || jobs.has('fallback')).map(row => row.id === 'qa-draft' ? { ...row, title: 'Project Willow · Apex fallback' } : row);
      }
      if (command === 'get_local_transcript_groups') return jobs.has('fallback') ? [{ meeting_id: 'qa-draft', source_meeting_id: 'qa-meeting', workflow_role: 'fallback' }] : [];
      if (command === 'get_local_transcript_layers') return { source_meeting_id: 'qa-meeting', layers: [...jobs.values()].map(item => ({ meeting_id: item.workflow_role === 'fallback' ? 'qa-draft' : 'qa-meeting', title: 'Project Willow · Friday catch-up', profile: item.profile, workflow_role: item.workflow_role, state: item.state, job_id: item.job_id, primary: item.workflow_role !== 'fallback' })) };
    };
  });
}

async function main() {
  const output = path.resolve(process.argv[2] || 'frontend/test-artifacts/xx-trelis-live');
  const checkWarmup = process.argv.includes('--check-warmup');
  fs.mkdirSync(output, { recursive: true });
  const root = path.resolve(__dirname, '../../out');
  const mime = { '.html': 'text/html', '.txt': 'text/x-component', '.js': 'application/javascript', '.css': 'text/css', '.ttf': 'font/ttf', '.png': 'image/png', '.svg': 'image/svg+xml' };
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    let file = path.resolve(root, '.' + (url.pathname === '/' ? '/index.html' : decodeURIComponent(url.pathname)));
    if (!file.startsWith(root + path.sep)) { response.writeHead(403); return response.end(); }
    if (!path.extname(file)) file += '.html';
    if (url.searchParams.has('_rsc') && file.endsWith('.html')) file = file.replace(/\.html$/, '.txt');
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { response.writeHead(404); return response.end(); }
    response.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream'); fs.createReadStream(file).pipe(response);
  });
  await new Promise(resolve => server.listen(3126, '127.0.0.1', resolve));
  let browser, page;
  const report = { synthetic_ipc: true, native_execution: false, actual_recording: false, loading_notice_checked: checkWarmup, checks: [], page_errors: [], console_errors: [], screenshots: [] };
  const screenshot = async name => { await page.screenshot({ path: path.join(output, name), fullPage: true }); report.screenshots.push(name); };
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.on('pageerror', error => report.page_errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') report.console_errors.push(message.text()); });
    await installLiveFixture(page);
    await page.goto('http://127.0.0.1:3126/settings');
    await page.waitForFunction(() => window.qaCalls.some(call => call.command === 'get_active_capture'));
    await page.getByRole('tab', { name: 'Transcription', exact: true }).click();
    await page.getByText('Trelis live transcript', { exact: false }).first().waitFor();
    assert.equal(await page.getByLabel('Local transcription profile').count(), 0);
    assert.equal(await page.getByLabel('Trelis chunk length').inputValue(), 'trelis-10');
    assert.equal(await page.getByLabel('Trelis chunk length').locator('option').count(), 3);
    for (const profile of ['trelis-5', 'trelis-20', 'trelis-10']) {
      await page.getByLabel('Trelis chunk length').selectOption(profile);
      assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('sttapp.local-workflow.v1')).preferences.profile), profile);
    }
    await page.reload();
    await page.waitForFunction(() => window.qaCalls.some(call => call.command === 'get_active_capture'));
    await page.getByRole('tab', { name: 'Transcription', exact: true }).click();
    assert.equal(await page.getByLabel('Trelis chunk length').inputValue(), 'trelis-10');
    await screenshot('chunk-selector-1440x900.png');
    report.checks.push('5,10,20-second chunk choices persist; new settings default to10 and survive reload');
    await page.goto('http://127.0.0.1:3126/');
    await page.waitForFunction(() => window.qaCalls.some(call => call.command === 'get_active_capture'));
    await page.evaluate(() => window.qaStart());
    await page.waitForFunction(() => window.qaLiveState().jobs.length === 1);
    assert.equal((await page.evaluate(() => window.qaLiveState().jobs[0])).workflow_role, 'live-final');
    assert.equal((await page.evaluate(() => window.qaLiveState().jobs[0])).profile, 'trelis-10');
    await page.getByLabel('Recording options', { exact: true }).click();
    assert.equal(await page.getByLabel('Trelis chunk length').isDisabled(), true);
    if (checkWarmup) {
      await page.getByText('Loading Trelis. Your audio is being saved while the model gets ready; the first transcript can take about a minute.', { exact: true }).waitFor();
      await screenshot('model-loading-1440x900.png');
      report.checks.push('Model-loading status explains startup delay while preserving independent recording');
    }
    await page.evaluate(() => window.qaSetJob('live-final', 'running', 2));
    await page.getByRole('button', { name: 'Show transcript', exact: true }).click();
    await page.getByText('Trelis · 10-second windows plus processing · original Hindi + English script', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Use Apex fallback', exact: true }).waitFor();
    await screenshot('live-trelis-1440x900.png');
    await page.getByText('Trelis · 10-second windows plus processing · original Hindi + English script', { exact: true }).waitFor();
    report.checks.push('One primary Trelis10 live job starts from a synthetic recording event and displays its chosen duration and mixed-script text');
    await page.getByRole('button', { name: 'Use Apex fallback', exact: true }).click();
    await page.waitForFunction(() => window.qaCalls.some(call => call.command === 'stop_local_transcription'));
    await page.waitForTimeout(3200);
    assert.equal((await page.evaluate(() => window.qaLiveState().jobs)).length, 1);
    await page.evaluate(() => window.qaSetJob('live-final', 'stopped', 3));
    await page.waitForFunction(() => window.qaLiveState().jobs.length === 2);
    assert.equal((await page.evaluate(() => window.qaLiveState().jobs.find(job => job.workflow_role === 'fallback'))).final_profile, 'trelis-10');
    assert.equal((await page.evaluate(() => window.qaLiveState().imported.primary)).length, 3);
    await page.evaluate(() => window.qaSetJob('fallback', 'running', 2));
    await page.getByText('Welcome back. Aaj Project Willow ki workshop plan karte hain.', { exact: true }).waitFor();
    report.checks.push('Explicit fallback waits for Trelis exit and last checkpoint import before starting distinct Apex');
    await page.evaluate(() => window.qaStop());
    await page.waitForURL('**/meeting-details?id=qa-meeting');
    await page.getByRole('heading', { name: 'Project Willow · Friday catch-up', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Show transcript', exact: true }).click();
    await page.evaluate(() => window.qaSetJob('fallback', 'complete', 2));
    await page.getByRole('navigation', { name: 'Transcript versions' }).getByRole('button', { name: 'Apex fallback', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Tools', exact: true }).click();
    await page.getByText('Transcription & versions', { exact: true }).click();
    assert.equal(await page.getByLabel('Trelis chunk length').inputValue(), 'trelis-10');
    await page.getByLabel('Trelis chunk length').selectOption('trelis-5');
    await page.getByLabel('Local transcription profile').selectOption('apex-20');
    assert.equal(await page.getByLabel('Trelis chunk length').count(), 0);
    await page.getByLabel('Local transcription profile').selectOption('trelis-5');
    assert.equal(await page.getByLabel('Trelis chunk length').inputValue(), 'trelis-5');
    assert.equal((await page.evaluate(() => window.qaLiveState().jobs.find(job => job.workflow_role === 'live-final'))).profile, 'trelis-10');
    report.checks.push('Library reprocessing model and chunk choices stay independent of existing transcript and rememberTrelis5 after Apex selection');
    await page.getByRole('button', { name: 'Retry Trelis', exact: true }).click();
    await page.waitForFunction(() => window.qaCalls.some(call => call.command === 'resume_local_transcription'));
    await page.keyboard.press('Escape');
    await page.getByRole('navigation', { name: 'Transcript versions' }).getByRole('button', { name: /^Finishing transcript/ }).waitFor();
    await page.evaluate(() => window.qaSetJob('live-final', 'complete', 5));
    await page.getByRole('navigation', { name: 'Transcript versions' }).getByRole('button', { name: 'Final transcript', exact: true }).waitFor();
    report.checks.push('Stopping capture starts no second pass; explicit retry resumes original Trelis, labelled Final only after completion');
    const sidebar = page.getByRole('complementary', { name: 'Workspace navigation' });
    assert.equal(await sidebar.getByRole('button', { name: 'Project Willow · Apex fallback', exact: true }).count(), 0);
    await page.waitForTimeout(4500);
    for (const [width, height] of [[1440, 900], [1366, 768], [1280, 720], [1024, 768]]) {
      await page.setViewportSize({ width, height });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1 && document.documentElement.scrollHeight <= innerHeight + 1), true);
      const audio = await page.locator('audio').boundingBox(); assert.ok(audio && audio.y + audio.height <= height);
      await screenshot(`final-with-fallback-${width}x${height}.png`);
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    const primaryBefore = await page.evaluate(() => window.qaWorkspace());
    await page.getByRole('navigation', { name: 'Transcript versions' }).getByRole('button', { name: 'Apex fallback', exact: true }).click();
    await page.getByText('Notes for this fallback', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Show transcript', exact: true }).click();
    await page.getByRole('textbox', { name: 'Meeting notes', exact: true }).fill('A fictional note only for the fallback version.');
    await page.getByRole('button', { name: 'Edit', exact: true }).first().click();
    await page.getByRole('textbox', { name: 'Correct transcript text' }).fill('A corrected fictional fallback line.');
    await page.getByRole('button', { name: 'Apply correction', exact: true }).click();
    await page.keyboard.press('Control+s');
    await page.waitForFunction(() => window.qaWorkspace('qa-draft').revision === 1);
    assert.deepEqual(await page.evaluate(() => window.qaWorkspace()), primaryBefore);
    assert.equal((await page.evaluate(() => window.qaLiveState().imported.fallback[0])).text, 'Welcome back. Aaj Project Willow ki workshop plan karte hain.');
    await page.waitForTimeout(4500); await screenshot('fallback-notes-1440x900.png');
    report.checks.push('One sidebar conversation retains fallback layer; fallback notes/corrections do not overwrite Trelis or raw recognition');
    const calls = await page.evaluate(() => window.qaCalls);
    assert.equal(calls.filter(call => call.command === 'start_local_transcription').length, 2);
    assert.equal(calls.filter(call => call.command === 'resume_local_transcription').length, 1);
    assert.ok(!calls.some(call => /start_.*recording|open_claude|plugin:dialog|start_speaker_identification/.test(call.command)));
    assert.deepEqual(report.page_errors, []);
    report.passed = true;
  } catch (error) {
    report.failure = error.stack || String(error);
    if (page) { report.debug = await page.evaluate(() => ({ body: document.body.innerText, calls: window.qaCalls })); await screenshot('failure.png'); }
    throw error;
  } finally {
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    if (browser) await browser.close(); await new Promise(resolve => server.close(resolve));
  }
  console.log(JSON.stringify(report));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
