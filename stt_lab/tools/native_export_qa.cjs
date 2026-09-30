/* Actual native Save dialogs, controlled only through target-process UIA. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const execute = promisify(execFile);
const ts = require(path.resolve(__dirname, '../../frontend/node_modules/typescript'));
const source = fs.readFileSync(path.resolve(__dirname, '../../frontend/src/lib/transcript-workspace.ts'), 'utf8');
const exported = {};
new Function('exports', ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText)(exported);
async function main() {
  const [endpoint, fixturePath, processId] = process.argv.slice(2);
  if (!/^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(endpoint) || !/^\d+$/.test(processId)) throw Error('Local endpoint and approved process ID required');
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  if (!/native-qa-/i.test(fixture.data_root)) throw Error('Isolated QA root required');
  const reviewed = JSON.parse(fs.readFileSync(path.join(fixture.data_root, 'ui-qa', 'native-ui-report-final.json'), 'utf8'));
  const output = path.join(fixture.data_root, 'export-qa'); fs.mkdirSync(output, { recursive: true });
  const report = { started_at: new Date().toISOString(), state: 'running', mocked_ipc: false, clipboard_accessed: false,
    process_id: Number(processId), meeting_id: reviewed.meeting_id, exports: [] };
  const browser = await chromium.connectOverCDP(endpoint);
  const page = browser.contexts().flatMap(context => context.pages()).find(page => /tauri|localhost/.test(page.url()));
  const invoke = (command, args = {}) => page.evaluate(({ command, args }) => window.__TAURI_INTERNALS__.invoke(command, args), { command, args });
  try {
    assert.equal(await invoke('get_active_capture'), null);
    const workspace = await invoke('load_transcript_workspace', { meetingId: reviewed.meeting_id });
    const metadata = await invoke('api_get_meeting_metadata', { meetingId: reviewed.meeting_id });
    const rows = await invoke('api_get_meeting_transcripts', { meetingId: reviewed.meeting_id, limit: 100, offset: 0 });
    assert(workspace.corrections.length > 0 && workspace.notes.length > 0);
    await page.goto(new URL(`/meeting-details?id=${encodeURIComponent(reviewed.meeting_id)}`, page.url()).href, { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { name: metadata.title, exact: true }).waitFor();
    for (const format of ['txt', 'md', 'json']) {
      const filename = path.join(output, `reviewed-native-${Date.now()}.${format}`);
      await page.getByRole('button', { name: `Export ${format.toUpperCase()}`, exact: true }).click();
      const dialog = await execute('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', path.join(__dirname, 'native_save_dialog.ps1'),
        '-AppProcessId', processId, '-TargetPath', filename], { windowsHide: true, timeout: 20000 });
      const deadline = Date.now() + 10000;
      while (!fs.existsSync(filename) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 100));
      assert(fs.existsSync(filename), 'Native export did not create the selected file');
      const actual = fs.readFileSync(filename, 'utf8');
      const expected = exported.makeTranscriptExport({ id: reviewed.meeting_id, title: metadata.title, created_at: metadata.created_at }, rows.transcripts, workspace, format);
      assert.equal(actual, expected, 'Export content differs from saved transcript/corrections/metadata');
      report.exports.push({ format, path: filename, character_count: actual.length, native_dialog: JSON.parse(dialog.stdout.trim()), content_verified: true });
      console.log(`Native ${format.toUpperCase()} Save dialog and complete corrected export passed`);
    }
    assert.deepEqual(await invoke('load_transcript_workspace', { meetingId: reviewed.meeting_id }), workspace);
    report.state = 'complete';
  } catch (error) { report.state = 'failed'; report.error = String(error.stack || error); throw error; }
  finally {
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(output, 'native-export-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
