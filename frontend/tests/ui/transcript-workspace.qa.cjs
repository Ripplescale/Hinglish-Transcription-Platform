/* Synthetic IPC browser integration test: no real recordings or Claude launch. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
async function main() {
  const output = process.argv[2];
  if (!output) throw new Error('Pass an output directory for screenshots and QA JSON.');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => { errors.push(error.message); console.error('PAGE ERROR:', error.stack || error.message); });
    await page.addInitScript(() => {
      const calls = []; window.qaCalls = calls;
      let workspace = { version: 1, meeting_id: 'qa-meeting', revision: 0, notes: 'Existing manual notes', corrections: [], project_id: null, profile: 'trelis-15' };
      const segments = [{ id: 'first', text: 'सात notebooks. Mira is joining.', timestamp: '12:00', audio_start_time: 65, audio_end_time: 80 }, { id: 'second', text: 'Check the sample cards before the workshop.', timestamp: '12:01' }];
      window.qaClipboard = '';
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async text => { window.qaClipboard = text; } } });
      const callbacks = new Map(); let callbackId = 0;
      window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
      window.__TAURI_OS_PLUGIN_INTERNALS__ = { platform: 'windows', arch: 'x86_64', family: 'windows', type: 'windows', version: '10' };
      window.__TAURI_INTERNALS__ = {
        metadata: { currentWindow: { label: 'main' }, currentWebview: { label: 'main' } },
        convertFileSrc: p => p,
        transformCallback: fn => { callbacks.set(++callbackId, fn); return callbackId; },
        unregisterCallback: id => callbacks.delete(id),
        invoke: async (command, args = {}) => {
          calls.push({ command, args });
          if (command === 'get_onboarding_status') return { completed: true, current_step: 3, model_status: { parakeet: 'not_downloaded', summary: 'not_downloaded' } };
          if (command === 'api_get_meetings') return [{ id: 'qa-meeting', title: 'QA meeting' }];
          if (command === 'api_get_meeting_metadata') return { id: 'qa-meeting', title: 'QA meeting', created_at: '2026-09-30T12:00:00Z', updated_at: '2026-09-30T12:00:00Z' };
          if (command === 'api_get_meeting_transcripts') return { transcripts: segments.slice(args.offset, args.offset + args.limit), total_count: segments.length, has_more: args.offset + args.limit < segments.length };
          if (command === 'api_get_summary') return { status: 'idle', data: null };
          if (command === 'load_transcript_workspace') return workspace;
          if (command === 'save_transcript_workspace') { if (args.expectedRevision !== workspace.revision) throw 'Revision conflict'; workspace = { ...workspace, revision: workspace.revision + 1, notes: args.notes, corrections: args.corrections, project_id: args.projectId, profile: args.profile }; return workspace; }
          if (command === 'local_get_meeting_audio') return { path: null };
          if (command === 'get_local_stt_profiles') return ['apex-15', 'apex-20', 'apex-30', 'trelis-15', 'trelis-20'].map(id => ({ id, model: id.split('-')[0], chunk_seconds: +id.split('-')[1], available: false, reason: 'Synthetic QA profile', live_qualified: false }));
          if (command === 'list_local_transcription_jobs' || command === 'list_recoverable_captures') return [];
          if (command === 'open_claude_desktop') return { opened: true, requires_paste: true, path: null };
          if (command === 'api_get_transcript_config') return { provider: 'localWhisper', model: 'apex' };
          if (command === 'get_recording_state') return { is_recording: false, is_paused: false };
          if (command === 'check_first_launch') return false;
          if (command.startsWith('plugin:event|')) return 1;
          if (command.startsWith('plugin:store|load')) return 1;
          if (command.startsWith('plugin:store|get')) return [null, false];
          if (command.startsWith('plugin:store|has')) return false;
          if (command.startsWith('plugin:os|')) return 'windows';
          if (command === 'get_audio_devices') return [];
          return null;
        },
      };
    });
    await page.goto('http://localhost:3118/meeting-details?id=qa-meeting', { waitUntil: 'domcontentloaded', timeout: 120000 });
    try { await page.getByRole('heading', { name: 'QA meeting', exact: true }).waitFor({ timeout: 60000 }); }
    catch (error) { await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }); fs.writeFileSync(path.join(output, 'failure.txt'), await page.locator('body').innerText()); throw error; }
    await page.getByRole('button', { name: 'Edit', exact: true }).first().click();
    await page.getByLabel('Correct transcript text').fill('सात notebooks. Mira confirmed.');
    await page.getByRole('button', { name: 'Apply correction', exact: true }).click();
    await page.getByRole('textbox', { name: 'Meeting notes', exact: true }).fill('My checked notes for Claude.');
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await page.getByText('Saved revision 1', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Copy & open Claude', exact: true }).click();
    await page.waitForFunction(() => window.qaCalls.some(call => call.command === 'open_claude_desktop'));
    const evidence = await page.evaluate(() => ({ clipboard: window.qaClipboard, calls: window.qaCalls }));
    assert.ok(evidence.clipboard.includes('सात notebooks. Mira confirmed.'));
    assert.ok(evidence.clipboard.includes('Check the sample cards before the workshop.'));
    assert.ok(evidence.clipboard.includes('My checked notes for Claude.'));
    assert.ok(evidence.clipboard.includes('[time unavailable]'));
    assert.ok(!evidence.calls.some(call => /api_process_transcript|builtin_ai_download_model|init_analytics/.test(call.command)));
    assert.equal(evidence.calls.filter(call => call.command === 'open_claude_desktop').length, 1);
    await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 430, height: 932 });
    await page.getByRole('tab', { name: 'Notes', exact: true }).click();
    await page.getByRole('textbox', { name: 'Meeting notes', exact: true }).waitFor({ state: 'visible' });
    await page.screenshot({ path: path.join(output, 'mobile-notes.png'), fullPage: true });
    assert.deepEqual(errors, []);
    const report = { synthetic_ipc: true, passed: true, checks: ['native script preserved', 'correction and notes saved in revision', 'complete transcript copied for Claude', 'unknown timestamp preserved', 'no auto-summary/download/analytics', 'manual Claude open only after click', 'mobile notes accessible'], page_errors: errors };
    fs.writeFileSync(path.join(output, 'browser-validation.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
