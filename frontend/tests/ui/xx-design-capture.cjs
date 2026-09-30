const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { installFixture } = require('./xx-fixture.cjs');
async function main() {
  const output = path.resolve(process.argv[2]); fs.mkdirSync(output, { recursive: true });
  const root = path.resolve(__dirname, '../../out');
  const mime = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.woff': 'font/woff', '.png': 'image/png', '.svg': 'image/svg+xml', '.json': 'application/json' };
  const server = http.createServer((req, res) => {
    let relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    if (relative === '/') relative = '/index.html';
    let file = path.resolve(root, '.' + relative);
    if (!file.startsWith(root + path.sep)) { res.writeHead(403); res.end(); return; }
    if (!path.extname(file)) file += '.html';
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream'); fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(3124, '127.0.0.1', resolve));
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const report = { synthetic_local_ipc: true, screenshots: [], side_effects: false };
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await installFixture(page);
    await page.goto('http://127.0.0.1:3124/meeting-details?id=qa-meeting', { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { name: 'Project Willow · Friday catch-up', exact: true }).waitFor({ timeout: 30000 });
    await page.screenshot({ path: path.join(output, '01-current-meeting.png'), fullPage: true });
    report.screenshots.push('01-current-meeting.png');
    await page.goto('http://127.0.0.1:3124/', { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Local transcription profile', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(output, '02-current-home.png'), fullPage: true });
    report.screenshots.push('02-current-home.png');
    const reference = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await reference.goto('https://www.granola.ai/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await reference.waitForTimeout(1800);
    const rejectCookies = reference.getByRole('button', { name: 'Reject all', exact: true });
    if (await rejectCookies.isVisible()) await rejectCookies.click();
    await reference.waitForTimeout(500);
    await reference.screenshot({ path: path.join(output, '03-granola-public-home.png'), fullPage: false });
    report.screenshots.push('03-granola-public-home.png');
    const notes = reference.getByText('Northwind Sync', { exact: true });
    let visibleNote;
    for (let index = 0; index < await notes.count(); index++) {
      if (await notes.nth(index).isVisible()) { visibleNote = notes.nth(index); break; }
    }
    if (visibleNote) await visibleNote.scrollIntoViewIfNeeded();
    else await reference.evaluate(() => window.scrollTo(0, window.innerHeight * 1.2));
    await reference.waitForTimeout(800);
    if (await rejectCookies.isVisible()) await rejectCookies.click();
    await reference.waitForTimeout(500);
    await reference.screenshot({ path: path.join(output, '04-granola-public-product.png'), fullPage: false });
    report.screenshots.push('04-granola-public-product.png');
    report.reference_url = reference.url();
    fs.writeFileSync(path.join(output, 'capture-report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
