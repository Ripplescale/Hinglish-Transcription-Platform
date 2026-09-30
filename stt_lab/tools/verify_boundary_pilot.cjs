/* Verify the actual generated private HTML with local headless Edge. */
const fs=require('fs');const path=require('path');const {pathToFileURL}=require('url');
const {chromium}=require('playwright');const assert=require('assert');
(async()=>{
 const input=path.resolve(process.argv[2]);const output=path.resolve(process.argv[3]);
 fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true,args:['--mute-audio']});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
  const page=await context.newPage();const errors=[];const external=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/i.test(r.url()))external.push(r.url())});
  await page.goto(pathToFileURL(input).href);await page.waitForFunction(()=>[...document.querySelectorAll('audio')].every(a=>Number.isFinite(a.duration)));
  assert.equal(await page.locator('.minute').count(),3);
  const data=await page.locator('#data').textContent();const manifest=JSON.parse(data);
  for(const section of manifest.samples){for(const profile of manifest.profiles){
   const scope=page.locator('#'+section.id);await scope.locator('.profile').first().selectOption(profile);
   assert.equal(await scope.locator('.transcript').first().textContent(),section.drafts[profile+':aligned'].text);
  }}
  assert.deepEqual(await page.locator('audio').evaluateAll(xs=>xs.map(x=>Math.round(x.duration))),[60,60,60]);
  await page.locator('audio').first().evaluate(async a=>{a.currentTime=12;await a.play()});
  await page.waitForFunction(()=>document.querySelector('audio').currentTime>12.1);await page.locator('audio').first().evaluate(a=>a.pause());
  const first=page.locator('.minute').first();await first.locator('.reference').fill('Checked reference test');await first.locator('.notes').fill('Names and quantities');await first.locator('.reviewed').check();
  await first.locator('.reference').fill('Edited checked reference test');assert.equal(await first.locator('.reviewed').isChecked(),false);
  await first.locator('.reviewed').check();await first.locator('.assessment').first().selectOption('looks_complete');
  const downloadPromise=page.waitForEvent('download');await page.locator('#export').click();const download=await downloadPromise;
  const saved=path.join(output,'roundtrip.json');await download.saveAs(saved);const savedData=JSON.parse(fs.readFileSync(saved,'utf8'));
  assert.equal(savedData.reviews[manifest.samples[0].id].reviewed,true);assert.deepEqual(savedData.binding,manifest.binding);
  await first.locator('.reference').fill('Replace me');await page.locator('#import').setInputFiles(saved);
  await page.waitForFunction(()=>document.getElementById('status').textContent==='Matching corrections imported');
  assert.equal(await first.locator('.reference').inputValue(),'Edited checked reference test');
  const wrong=structuredClone(savedData);wrong.binding.manifest_sha256='bad';const wrongPath=path.join(output,'wrong-binding.json');fs.writeFileSync(wrongPath,JSON.stringify(wrong));
  await page.locator('#import').setInputFiles(wrongPath);await page.waitForFunction(()=>document.getElementById('status').textContent.includes('different audio'));
  assert.equal(await first.locator('.reference').inputValue(),'Edited checked reference test');
  await first.locator('.profile').first().selectOption('trelis:15');await first.locator('.profile').nth(1).selectOption('trelis:20');
  await page.screenshot({path:path.join(output,'desktop.png'),fullPage:false});await first.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,'desktop-review.png'),fullPage:false});
  await page.setViewportSize({width:390,height:844});await first.scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(output,'mobile.png'),fullPage:false});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  fs.writeFileSync(path.join(output,'validation.json'),JSON.stringify({passed:true,drafts_verified:15,audio_minutes:3,playback:true,edit_invalidates_review:true,export_import_roundtrip:true,binding_rejection:true,mobile_no_overflow:true,page_errors:errors,external_requests:external},null,2)+'\n');
  console.log(JSON.stringify({passed:true,output}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
