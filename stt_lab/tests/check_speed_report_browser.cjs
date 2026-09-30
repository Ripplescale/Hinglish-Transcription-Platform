/* Visual and local-link QA for the completed private speed report.
 * Usage: node check_speed_report_browser.cjs <report.html> <playwright-module>
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {pathToFileURL, fileURLToPath} = require('node:url');
const {chromium} = require(process.argv[3] || 'playwright');

(async () => {
  const report = path.resolve(process.argv[2]);
  const qa = path.join(path.dirname(report), 'qa');
  fs.mkdirSync(qa, {recursive:true});
  const browser = await chromium.launch({channel:'msedge', headless:true});
  const errors = [], remote = [], consoleErrors = [];
  const results = {report};
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1100}});
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {if (message.type()==='error') consoleErrors.push(message.text());});
    await page.route(/^https?:/, route => {remote.push(route.request().url()); return route.abort();});
    await page.goto(pathToFileURL(report).href);
    assert.match(await page.title(), /STT speed and conversion/);
    assert.ok(await page.locator('svg[role="img"]').count() >= 1);
    const tags = await page.locator('.tag').allTextContents();
    assert.ok(tags.length >= 1);
    assert.ok(tags.every(text => !text.startsWith('running')));
    const links = await page.locator('a').evaluateAll(elements => elements.map(el => el.href));
    assert.ok(links.length >= 3);
    for (const href of links) {
      assert.ok(href.startsWith('file:'), `Unexpected remote link: ${href}`);
      assert.ok(fs.statSync(fileURLToPath(href)).isFile(), `Missing link: ${href}`);
    }
    results.local_links_checked = links.length;
    results.run_states = tags;
    results.desktop_overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(results.desktop_overflow, false);
    await page.screenshot({path:path.join(qa, 'speed-desktop.png')});
    await page.locator('section.card').filter({has:page.locator('.tag')}).first().scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(qa, 'speed-replay.png')});
    await page.locator('summary').first().click();
    assert.ok(await page.locator('details[open]').count() >= 1);
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(() => window.scrollTo(0,0));
    results.mobile_overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(results.mobile_overflow, false);
    await page.screenshot({path:path.join(qa, 'speed-mobile.png')});
    assert.deepEqual(errors, []);
    assert.deepEqual(consoleErrors, []);
    assert.deepEqual(remote, []);
    results.page_errors = errors;
    results.console_errors = consoleErrors;
    results.remote_requests = remote;
    fs.writeFileSync(path.join(qa, 'speed-browser-qa.json'), JSON.stringify(results, null, 2));
    process.stdout.write(JSON.stringify(results, null, 2)+'\n');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode=1;});
