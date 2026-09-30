/* Compare the rendered readback against the saved analysis; no reference edits. */
const fs=require('fs');const path=require('path');const {pathToFileURL}=require('url');
const {chromium}=require('playwright');const assert=require('assert');
(async()=>{
 const input=path.resolve(process.argv[2]),analysisPath=path.resolve(process.argv[3]),output=path.resolve(process.argv[4]);
 const analysis=JSON.parse(fs.readFileSync(analysisPath,'utf8'));
 fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true,args:['--mute-audio']});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1050}}),page=await context.newPage();
  const errors=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(/^https?:/i.test(r.url()))external.push(r.url())});
  await page.goto(pathToFileURL(input).href);
  assert.equal(await page.locator('#left-profile').inputValue(),'trelis:15');
  assert.equal(await page.locator('#right-profile').inputValue(),'trelis:20');
  const embedded=JSON.parse(await page.locator('#study-data').textContent());
  assert.deepEqual(embedded.source,analysis.source);
  assert.equal(embedded.samples.length,3);
  let drafts=0,refs=0,notes=0;const durations=[];
  for(const sample of analysis.samples){
   await page.locator('#sample-select').selectOption(sample.id);
   assert.equal(await page.locator('#reference-text').textContent(),sample.reference);refs++;
   assert.equal(await page.locator('#notes-text').textContent(),sample.notes||'No notes supplied for this minute.');notes++;
   assert.equal(await page.locator('#reference-status').textContent(),sample.reviewed?'Marked reviewed in your uploaded file':'Not marked reviewed in your uploaded file');
   const saved=embedded.samples.find(s=>s.id===sample.id);
   assert.deepEqual(saved.draft_checks,sample.draft_checks);
   assert.deepEqual(saved.reference_provenance,sample.reference_provenance);
   for(const [profile,draft] of Object.entries(sample.drafts)){
    for(const side of ['left','right']){
     await page.locator('#'+side+'-profile').selectOption(profile);
     assert.equal(await page.locator('#'+side+'-text').textContent(),draft.text);
     if(draft.metrics&&draft.metrics.wer!==null)assert.ok((await page.locator('#'+side+'-metric').textContent()).includes((draft.metrics.wer*100).toFixed(1)+'%'));
     else assert.ok((await page.locator('#'+side+'-metric').textContent()).includes('No compatible'));
    }
    drafts++;
   }
   await page.waitForFunction(()=>Number.isFinite(document.getElementById('audio').duration));
   const seconds=await page.locator('#audio').evaluate(a=>a.duration);assert.ok(Math.abs(seconds-sample.duration_seconds)<.01);durations.push(seconds);
   await page.locator('#audio').evaluate(async a=>{a.currentTime=12;await a.play()});
   await page.waitForFunction(()=>document.getElementById('audio').currentTime>12.1);
   await page.locator('#audio').evaluate(a=>a.pause());
  }
  assert.equal(drafts,15);assert.equal(refs,3);assert.equal(notes,3);
  await page.locator('#sample-select').selectOption(analysis.samples[0].id);
  await page.locator('#left-profile').selectOption('trelis:15');await page.locator('#right-profile').selectOption('trelis:20');
  await page.screenshot({path:path.join(output,'desktop.png'),fullPage:false});
  await page.locator('#reference-text').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'desktop-reference.png'),fullPage:false});
  await page.locator('#left-profile').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'desktop-drafts.png'),fullPage:false});
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>scrollTo(0,0));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(output,'mobile.png'),fullPage:false});
  await page.locator('#reference-text').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'mobile-reference.png'),fullPage:false});
  await page.locator('#left-profile').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(output,'mobile-draft.png'),fullPage:false});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const result={passed:true,drafts_verified:drafts,checked_references_verified:refs,notes_verified:notes,audio_durations:durations,playback_all_three:true,default_profiles:['trelis:15','trelis:20'],mobile_no_overflow:true,page_errors:errors,external_requests:external};
  fs.writeFileSync(path.join(output,'validation.json'),JSON.stringify(result,null,2)+'\n');
  console.log(JSON.stringify(result));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
