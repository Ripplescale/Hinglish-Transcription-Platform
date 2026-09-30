/* Headless local review QA. Synthetic edits go only into qa/synthetic-*.json.
 * Usage: node check_minute_review_browser.cjs <review.html> <playwright-module>
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const {chromium} = require(process.argv[3] || 'playwright');

(async () => {
  const report = path.resolve(process.argv[2]);
  const qa = path.join(path.dirname(report), 'qa-html');
  fs.mkdirSync(qa, {recursive:true});
  const browser = await chromium.launch({channel:'msedge', headless:true, args:['--mute-audio']});
  const errors = [], consoleErrors = [], remote = [];
  const results = {};
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1100}, acceptDownloads:true});
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {if (message.type() === 'error') consoleErrors.push(message.text());});
    page.on('dialog', dialog => dialog.accept());
    await page.route(/^https?:/, route => {remote.push(route.request().url()); return route.abort();});
    await page.goto(pathToFileURL(report).href);
    const seed = await page.locator('#review-data').textContent().then(JSON.parse);
    assert.equal(await page.locator('.excerpt').count(),12);
    assert.equal(await page.locator('.draft').count(),24);
    assert.equal(await page.locator('#progress').textContent(),'0 / 12 checked');
    const first = page.locator('.excerpt').first();
    const reference = first.locator('[data-field="reference_text"]');
    const listened = first.locator('[data-field="listened"]');
    const checked = first.locator('[data-field="review_completed"]');
    async function download(name) {
      const promise = page.waitForEvent('download');
      await page.locator('#save-review').click();
      const saved = await promise;
      const target = path.join(qa,name);
      await saved.saveAs(target);
      return JSON.parse(fs.readFileSync(target,'utf8'));
    }
    const payload = '</textarea><script>window.injected=true</script> Synthetic QA only: कल meeting है';
    await reference.fill(payload);
    await checked.check();
    let saved = await download('synthetic-pending.json');
    assert.equal(saved.samples[0].human_reviewed,false);
    assert.equal(saved.samples[0].reference_text,payload);
    await listened.check();
    saved = await download('synthetic-checked.json');
    assert.equal(saved.samples[0].human_reviewed,true);
    assert.equal(saved.human_reviewed,false);
    assert.equal(await page.evaluate(() => window.injected),undefined);
    const bad = structuredClone(saved);
    bad.screening_sha256='incorrect-hash';
    await page.locator('#import-review').setInputFiles({name:'wrong-input.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(bad))});
    await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Import refused:'));
    assert.equal(await reference.inputValue(),payload);
    const wrongAudio = structuredClone(saved);
    wrongAudio.samples[0].audio_sha256 = '0'.repeat(64);
    await page.locator('#import-review').setInputFiles({name:'wrong-audio.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(wrongAudio))});
    await page.waitForFunction(() => document.getElementById('status').textContent.includes('audio identity'));
    assert.equal(await reference.inputValue(),payload);
    const dishonest = structuredClone(saved);
    dishonest.samples[0].listened=false;
    await page.locator('#import-review').setInputFiles({name:'inconsistent-review.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(dishonest))});
    await page.waitForFunction(() => document.getElementById('status').textContent.includes('flags'));
    await reference.fill('Temporary change, not a user reference');
    await page.locator('#import-review').setInputFiles(path.join(qa,'synthetic-checked.json'));
    await page.waitForFunction(() => document.getElementById('status').textContent === 'Imported local review JSON.');
    assert.equal(await reference.inputValue(),payload);
    assert.equal(await page.locator('#progress').textContent(),'1 / 12 checked');
    await first.locator('[data-field="no_speech"]').check();
    assert.match(await first.locator('.state').textContent(),/^Pending:/);
    await reference.fill('');
    assert.equal(await page.locator('#progress').textContent(),'1 / 12 checked');
    await page.locator('#import-review').setInputFiles({name:'blank-template.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(seed))});
    await page.waitForFunction(() => document.getElementById('progress').textContent === '0 / 12 checked');
    await page.evaluate(() => document.querySelectorAll('audio').forEach(audio => {audio.preload='metadata';audio.load();}));
    await page.waitForFunction(() => [...document.querySelectorAll('audio')].every(audio => Number.isFinite(audio.duration)));
    const durations = await page.locator('audio').evaluateAll(elements => elements.map(audio => audio.duration));
    assert.equal(durations.length,12);
    assert.equal(durations.filter(value => value===60).length,12);
    assert.equal(await page.locator('[data-model=trelis]').count(),24);
    const original = JSON.parse(fs.readFileSync(path.join(path.dirname(report),'results','trelis.json'),'utf8'));
    for(const [id,result] of Object.entries(original.chunks)) {
      assert.equal(await page.locator('[data-model=trelis][data-chunk="' + id + '"]').textContent(),result.text);
    }
    const apex = JSON.parse(fs.readFileSync(path.join(path.dirname(report),'results','apex.json'),'utf8'));
    for(const [id,result] of Object.entries(apex.chunks)) {
      assert.equal(await page.locator('[data-model=apex][data-chunk="' + id + '"]').textContent(),result.text);
    }
    assert.match(await page.locator('[data-model=trelis]').first().textContent(),/[\u0900-\u097f]/);
    assert.equal(await page.locator('audio').evaluateAll(a=>a.every(x=>x.src.startsWith('data:audio/wav;base64,'))),true);
    await first.locator('[data-speed]').selectOption('0.75');
    assert.equal(await first.locator('audio').evaluate(a=>a.playbackRate),0.75);
    assert.equal(await page.locator('#status').textContent(),'Imported local review JSON.');
    await first.locator('[data-seek="5"]').click();
    assert.equal(await first.locator('audio').evaluate(a=>a.currentTime),5);
    await first.locator('audio').evaluate(a=>a.play());
    await page.waitForFunction(()=>document.querySelector('audio').currentTime>5.1);
    await first.locator('audio').evaluate(a=>a.pause());
    await first.locator('[data-seek="-5"]').click();
    assert.ok(await first.locator('audio').evaluate(a=>a.currentTime)<1);
    results.literal_apex_and_trelis_text_preserved=true;
    results.embedded_audio_playback_and_controls=true;
    results.audio_durations=durations;
    results.desktop_overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(results.desktop_overflow,false);
    await page.evaluate(() => window.scrollTo(0,0));
    await page.screenshot({path:path.join(qa,'desktop-preview.png')});
    await first.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(qa,'excerpt-preview.png')});
    await first.screenshot({path:path.join(qa,'first-minute-full.png')});
    await page.locator('.excerpt').last().screenshot({path:path.join(qa,'last-minute-full.png')});
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(() => window.scrollTo(0,0));
    results.mobile_overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(results.mobile_overflow,false);
    await page.screenshot({path:path.join(qa,'mobile-preview.png')});
    await first.screenshot({path:path.join(qa,'mobile-minute-full.png')});
    results.export_import_round_trip=true;
    results.review_flags_enforced=true;
    results.wrong_manifest_import_rejected=true;
    results.wrong_audio_import_rejected=true;
    results.inconsistent_review_flags_import_rejected=true;
    results.imported_markup_remained_text=true;
    results.actual_reference_template_unchanged=true;
    results.page_errors=errors;
    results.console_errors=consoleErrors;
    results.remote_requests=remote;
    assert.deepEqual(errors,[]);
    assert.deepEqual(consoleErrors,[]);
    assert.deepEqual(remote,[]);
    results.ok=true;
    fs.writeFileSync(path.join(qa,'browser-validation.json'),JSON.stringify(results,null,2)+'\n');
    console.log(JSON.stringify(results));
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
