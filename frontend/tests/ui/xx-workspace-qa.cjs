/* Isolated, fictional UI regression. No capture, model run, clipboard or cloud. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { installFixture } = require('./xx-fixture.cjs');

async function main() {
  const output = path.resolve(process.argv[2] || 'frontend/test-artifacts/xx-redesign');
  fs.mkdirSync(output, { recursive: true });
  const root = path.resolve(__dirname, '../../out');
  const mime = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.woff': 'font/woff', '.png': 'image/png', '.svg': 'image/svg+xml' };
  const server = http.createServer((request, response) => {
    const requestUrl = new URL(request.url, 'http://localhost');
    const relative = decodeURIComponent(requestUrl.pathname);
    let file = path.resolve(root, '.' + (relative === '/' ? '/index.html' : relative));
    if (!file.startsWith(root + path.sep)) { response.writeHead(403); response.end(); return; }
    if (!path.extname(file)) file += '.html';
    if (requestUrl.searchParams.has('_rsc') && file.endsWith('.html')) file = file.replace(/\.html$/, '.txt');
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { response.writeHead(404); response.end(); return; }
    response.setHeader('Content-Type', requestUrl.searchParams.has('_rsc') ? 'text/x-component' : mime[path.extname(file)] || 'application/octet-stream'); fs.createReadStream(file).pipe(response);
  });
  await new Promise(resolve => server.listen(3125, '127.0.0.1', resolve));
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const report = { fictional_fixture: true, native_ipc: false, side_effects: false, checks: [], screenshots: [], page_errors: [] };
  let page;
  try {
    page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    report.rsc = [];
    page.on('response', response => { if (response.url().includes('_rsc') || response.url().includes('.txt')) report.rsc.push({ url: response.url(), status: response.status(), type: response.headers()['content-type'] }); });
    page.on('pageerror', error => report.page_errors.push(error.message));
    await installFixture(page);
    await page.goto('http://127.0.0.1:3125/meeting-details?id=qa-meeting');
    await page.getByRole('heading', { name: 'Project Willow · Friday catch-up', exact: true }).waitFor();
    const sidebar = page.getByRole('complementary', { name: 'Workspace navigation' });
    await sidebar.getByText("I listen and I don't judge", { exact: true }).waitFor();
    await sidebar.getByRole('button', { name: 'Project Willow · Friday catch-up', exact: true }).waitFor();
    assert.equal(await sidebar.getByRole('button', { name: 'Project Willow · Friday catch-up', exact: true }).getAttribute('aria-current'), 'page');
    assert.equal(await sidebar.getByRole('button', { name: 'Project Willow · Live draft', exact: true }).count(), 0);
    report.checks.push('Direct meeting URL selects the correct conversation');
    await page.keyboard.press('Control+k');
    const search = page.getByRole('textbox', { name: 'Search conversations' });
    assert.equal(await search.evaluate(element => element === document.activeElement), true);
    await search.fill('Willow');
    await page.waitForTimeout(400);
    assert.equal(await sidebar.getByRole('button', { name: 'Planning for next month', exact: true }).count(), 0);
    await page.getByRole('button', { name: 'Clear search', exact: true }).click();
    await sidebar.getByRole('button', { name: 'Options for Project Willow · Friday catch-up', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Rename', exact: true }).click();
    await page.getByRole('textbox', { name: 'Conversation title', exact: true }).fill('Project Willow · Workshop plans');
    await page.getByRole('button', { name: 'Save title', exact: true }).click();
    await page.getByRole('heading', { name: 'Project Willow · Workshop plans', exact: true }).waitFor();
    await sidebar.getByRole('button', { name: 'Options for Project Willow · Workshop plans', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Rename', exact: true }).click();
    await page.getByRole('textbox', { name: 'Conversation title', exact: true }).fill('Project Willow · Friday catch-up');
    await page.getByRole('button', { name: 'Save title', exact: true }).click();
    await page.getByRole('heading', { name: 'Project Willow · Friday catch-up', exact: true }).waitFor();
    report.checks.push('Ctrl+K title search and rename synchronize sidebar and meeting header');
    await page.waitForTimeout(4500);
    for (const [width, height] of [[1440, 900], [1366, 768], [1280, 720], [1024, 768]]) {
      await page.setViewportSize({ width, height });
      await page.waitForTimeout(200);
      const layout = await page.evaluate(() => {
        const box = selector => { const rect = document.querySelector(selector).getBoundingClientRect(); return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, bottom: rect.bottom, right: rect.right }; };
        return { width: innerWidth, height: innerHeight, scrollWidth: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight, notes: box('textarea[aria-label="Meeting notes"]'), audio: box('audio'), transcript: box('[role="region"][aria-label="Transcript"]') };
      });
      assert.ok(layout.scrollWidth <= width + 1, `Horizontal overflow at ${width}`);
      assert.ok(layout.scrollHeight <= height + 1, `Page scroll at ${height}`);
      assert.ok(layout.notes.height > height - 300, `Notes too short at ${width}`);
      assert.ok(layout.audio.bottom <= height && layout.audio.right <= width, 'Persistent playback visible');
      assert.ok(layout.transcript.width >= 330, 'Readable transcript width');
      const screenshot = `meeting-${width}x${height}.png`;
      await page.screenshot({ path: path.join(output, screenshot), fullPage: true }); report.screenshots.push(screenshot);
      report.checks.push({ viewport: `${width}x${height}`, layout });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.waitForFunction(() => document.querySelector('audio')?.readyState >= 1);
    await page.locator('audio').evaluate(audio => { audio.muted = true; });
    await page.getByRole('button', { name: '00:20', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('audio').currentTime >= 20);
    await page.locator('audio').evaluate(audio => { audio.pause(); });
    report.checks.push('Saved audio metadata loads and a timestamp seeks the muted synthetic track');
    await page.getByRole('button', { name: 'Export', exact: true }).click();
    for (const name of ['Export TXT', 'Export MD', 'Export JSON', 'Copy & open Claude', 'Copy only', 'Open in Claude Cowork']) assert.equal(await page.getByRole('menuitem', { name, exact: true }).count(), 1);
    await page.keyboard.press('Escape');
    report.checks.push('All exports and manual handoffs discoverable without invoking them');

    const notes = page.getByRole('textbox', { name: 'Meeting notes', exact: true });
    const originalNotes = await notes.inputValue();
    await notes.fill(originalNotes + '\n\nChecked the fictional workshop plan.');
    await page.keyboard.press('Control+s');
    await page.waitForFunction(() => window.qaWorkspace().revision === 4);
    assert.match((await page.evaluate(() => window.qaWorkspace())).notes, /Checked the fictional/);
    await page.getByRole('button', { name: 'Edit', exact: true }).first().click();
    await page.getByRole('textbox', { name: 'Correct transcript text' }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Edit', exact: true }).nth(1).isDisabled(), true);
    await page.getByRole('textbox', { name: 'Correct transcript text' }).fill('Welcome to our fictional Willow workshop. आज drawing से शुरू करते हैं.');
    await page.getByRole('button', { name: 'Apply correction', exact: true }).click();
    await page.keyboard.press('Control+s');
    await page.waitForFunction(() => window.qaWorkspace().revision === 5);
    assert.equal((await page.evaluate(() => window.qaWorkspace())).corrections.length, 1);
    report.checks.push('Ctrl+S saves notes and applied corrections, keeping original recognition');

    await page.getByRole('button', { name: 'Tools', exact: true }).click();
    await page.getByText('Transcription & versions', { exact: true }).click();
    assert.equal(await page.getByLabel('Local transcription profile').locator('option').count(), 2);
    await page.getByLabel('Local transcription profile').selectOption('apex-20');
    await page.getByLabel('Recording language').selectOption('english');
    await page.setViewportSize({ width: 1024, height: 768 });
    const tools = await page.getByRole('dialog', { name: 'Meeting tools', exact: true }).boundingBox();
    assert.ok(tools && tools.x >= 0 && tools.x + tools.width <= 1024 && tools.y >= 0 && tools.y + tools.height <= 768, 'Tools fits the smallest laptop');
    await page.screenshot({ path: path.join(output, 'tools-1024x768.png') }); report.screenshots.push('tools-1024x768.png');
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.locator('summary').filter({ hasText: /^Speakers/ }).click();
    const speaker = page.getByLabel('Name for speaker-0001');
    await speaker.fill('Maya (host)');
    await page.keyboard.press('Escape');
    await page.keyboard.press('Control+s');
    await page.waitForFunction(() => window.qaWorkspace().speaker_names['speaker-0001'] === 'Maya (host)');
    report.checks.push('Two retained model options and speaker name edits preserved');

    await page.getByRole('button', { name: 'Tools', exact: true }).click();
    await page.getByText('Project vault · optional', { exact: true }).click();
    await page.getByRole('button', { name: 'Add reference', exact: true }).click();
    await page.getByLabel('Name or value', { exact: true }).fill('Unfinished fictional reference');
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Tools', exact: true }).click();
    assert.equal(await page.getByLabel('Name or value', { exact: true }).inputValue(), 'Unfinished fictional reference');
    await page.keyboard.press('Escape');
    report.checks.push('Closing Tools preserves a reference draft');

    const finalBefore = await page.evaluate(() => window.qaWorkspace());
    await page.getByRole('navigation', { name: 'Transcript versions' }).getByRole('button', { name: 'Live draft', exact: true }).click();
    await page.getByRole('heading', { name: 'Notes for this draft', exact: true }).waitFor();
    assert.equal(await sidebar.getByRole('button', { name: 'Project Willow · Friday catch-up', exact: true }).getAttribute('aria-current'), 'page');
    assert.match(await page.getByRole('region', { name: 'Transcript', exact: true }).innerText(), /Transcript: Apex/);
    await notes.fill('My fictional draft-layer note.');
    await page.getByRole('button', { name: 'Edit', exact: true }).first().click();
    await page.getByRole('textbox', { name: 'Correct transcript text' }).fill('A separately corrected Roman draft.');
    await page.getByRole('button', { name: 'Apply correction', exact: true }).click();
    await page.keyboard.press('Control+s');
    await page.waitForFunction(() => window.qaWorkspace('qa-draft').revision === 1);
    assert.equal((await page.evaluate(() => window.qaWorkspace())).notes, finalBefore.notes);
    assert.deepEqual((await page.evaluate(() => window.qaWorkspace())).corrections, finalBefore.corrections);
    await page.getByRole('navigation', { name: 'Transcript versions' }).getByRole('button', { name: 'Final transcript', exact: true }).click();
    await page.getByRole('heading', { name: 'My notes', exact: true }).waitFor();
    assert.equal(await notes.inputValue(), finalBefore.notes);
    report.checks.push('Draft and final switch safely and retain independent notes, corrections and model provenance');

    await notes.fill('Unsaved fictional review notes');
    await sidebar.getByRole('button', { name: 'Home', exact: true }).click();
    await page.getByRole('dialog', { name: 'Keep your changes?' }).waitFor();
    await page.getByRole('button', { name: 'Stay here', exact: true }).click();
    assert.equal(await notes.inputValue(), 'Unsaved fictional review notes');
    await page.keyboard.press('Control+s');
    await page.waitForFunction(() => window.qaWorkspace().notes === 'Unsaved fictional review notes');
    await sidebar.getByRole('button', { name: 'Home', exact: true }).click();
    await page.waitForURL('**/');
    await page.waitForTimeout(4500);
    await page.locator('summary').filter({ hasText: /^Recording options/ }).click();
    assert.equal(await page.getByLabel('Local transcription profile').count(), 0);
    assert.equal(await page.getByLabel('Transcription timing').count(), 0);
    assert.equal(await page.getByLabel('Recording language').count(), 1);
    assert.equal(await page.getByLabel('Recording language').inputValue(), 'hinglish', 'Manual version settings do not change the next recording');
    await page.getByText('Trelis live transcript', { exact: false }).first().waitFor();
    await page.locator('summary').filter({ hasText: /^Recording options/ }).click();
    report.checks.push('New recordings use Trelis live with explicit Apex fallback and language as the only option');
    for (const [width, height] of [[1440,900], [1366,768], [1280,720], [1024,768]]) {
      await page.setViewportSize({ width, height });
      const start = await page.getByRole('button', { name: 'Start recording', exact: true }).boundingBox();
      assert.ok(start && start.y + start.height <= height && start.x + start.width <= width, 'Start control visible without recording');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const homeShot = `home-${width}x${height}.png`;
      await page.screenshot({ path: path.join(output, homeShot), fullPage: true }); report.screenshots.push(homeShot);
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await sidebar.getByRole('button', { name: 'Project Willow · Friday catch-up', exact: true }).click();
    await page.getByRole('heading', { name: 'Project Willow · Friday catch-up', exact: true }).waitFor();
    await notes.fill('Back-navigation draft');
    await page.getByText('Unsaved changes', { exact: true }).waitFor();
    report.beforeBack = await page.evaluate(() => ({ boot: window.qaBoot, href: location.href, index: window.navigation?.currentEntry?.index, entries: window.navigation?.entries().map(entry => ({index:entry.index,url:entry.url})), state: history.state, dirty: document.body.innerText.includes('Unsaved changes') }));
    await page.evaluate(() => history.back());
    await page.getByRole('dialog', { name: 'Keep your changes?' }).waitFor({ timeout: 6000 });
    await page.getByRole('button', { name: 'Stay here', exact: true }).click();
    assert.equal(await notes.inputValue(), 'Back-navigation draft');
    assert.match(page.url(), /meeting-details/);
    await page.evaluate(() => history.back());
    await page.getByRole('dialog', { name: 'Keep your changes?' }).waitFor();
    await page.getByRole('button', { name: 'Leave without saving', exact: true }).click();
    await page.waitForURL('**/');
    report.checks.push('Sidebar and browser Back preserve unsaved edits until explicit leave');
    assert.deepEqual(report.page_errors, []);
    const forbidden = await page.evaluate(() => window.qaCalls.filter(call => /start_.*(recording|transcription|identification)|open_claude|plugin:dialog/.test(call.command)));
    assert.deepEqual(forbidden, []);
    report.passed = true;
  } catch (error) { report.failure = error.stack || String(error); if (page) { report.debug = await page.evaluate(() => ({ boot: window.qaBoot, href: location.href, index: window.navigation?.currentEntry?.index, text: document.body.innerText.slice(-3500) })); await page.screenshot({ path: path.join(output, 'failure.png') }); } throw error; }
  finally {
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    await browser.close(); await new Promise(resolve => server.close(resolve));
  }
  console.log(JSON.stringify(report));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
