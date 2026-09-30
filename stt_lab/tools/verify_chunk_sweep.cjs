/* Browser verification for a generated local chunk-size review.
 * Usage: node verify_chunk_sweep.cjs <review.html> <new-private-QA-directory>
 * Uses synthetic correction text only; never rewrites the review or study.
 */
'use strict';
const fs=require('fs');
const path=require('path');
const assert=require('assert/strict');
const {createHash}=require('crypto');
const {pathToFileURL}=require('url');
const {chromium}=require('playwright');

async function main(){
  const [input,out]=process.argv.slice(2);
  if(!input||!out)throw new Error('Provide the HTML and a new private QA directory.');
  const html=path.resolve(input),dir=path.resolve(out);
  if(dir.split(path.sep).some(part=>part.toLowerCase().startsWith('onedrive')))
    throw new Error('Keep QA output outside OneDrive.');
  if(fs.existsSync(dir))throw new Error('Choose a new QA directory.');
  fs.mkdirSync(dir,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true,args:['--mute-audio']});
  const page=await browser.newPage({viewport:{width:1280,height:1100},deviceScaleFactor:1});
  const errors=[],external=[];
  page.on('pageerror',error=>errors.push(String(error)));
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
  await page.route(/^https?:/,route=>{external.push(route.request().url());route.abort();});
  const report={kind:'chunk_size_review_browser_qa',input:html,synthetic_corrections_only:true};
  try{
    await page.goto(pathToFileURL(html).href,{waitUntil:'load'});
    const data=await page.locator('#study-data').evaluate(node=>JSON.parse(node.textContent));
    assert.equal(await page.locator('#sample-select option').count(),data.focused_samples.length);
    assert.equal(await page.locator('#sample-select').inputValue(),data.focused_samples[0]);
    const first=data.focused_samples[0];
    await page.screenshot({path:path.join(dir,'desktop-summary.png')});
    await page.locator('#sample-title').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(dir,'desktop-review.png')});
    await page.selectOption('#queue-select','all');
    let literalChecks=0,audioChecks=0;
    for(const sample of data.samples){
      await page.selectOption('#sample-select',sample.id);
      await page.waitForFunction(()=>Number.isFinite(document.getElementById('audio').duration));
      const actual=await page.locator('#audio').evaluate(audio=>audio.duration);
      assert.ok(Math.abs(actual-sample.duration_seconds)<0.01,'Audio duration mismatch');
      audioChecks++;
      for(const model of ['apex','trelis']){
        for(const size of data.sizes){
          await page.selectOption('#'+model+'-size',String(size));
          assert.equal(await page.locator('#'+model+'-text').textContent(),sample.drafts[model][String(size)].text);
          literalChecks++;
        }
      }
    }
    await page.selectOption('#sample-select',first);
    await page.selectOption('#speed','0.75');
    assert.equal(await page.locator('#audio').evaluate(audio=>audio.playbackRate),0.75);
    await page.locator('#audio').evaluate(async audio=>{await audio.play();});
    await page.waitForTimeout(300);
    assert.equal(await page.locator('#audio').evaluate(audio=>audio.paused),false);
    await page.locator('#audio').evaluate(audio=>audio.pause());
    await page.locator('#audio').evaluate(audio=>audio.currentTime=0);
    await page.click('#forward');
    const seek=await page.locator('#audio').evaluate(audio=>audio.currentTime);
    assert.ok(Math.abs(seek-Math.min(5,data.samples.find(s=>s.id===first).duration_seconds))<0.1);
    await page.click('#back');
    assert.ok(await page.locator('#audio').evaluate(audio=>audio.currentTime)<0.1);
    const correction='QA ONLY — यह परीक्षण है. English + Hindi <script>alert(1)</script> & "quotes".';
    const notes='QA ONLY: named entities and boundary notes.';
    await page.fill('#correction',correction);
    await page.fill('#notes',notes);
    await page.check('#reviewed');
    await page.selectOption('#trelis-size','10');
    await page.selectOption('#trelis-assessment','missing_speech');
    await page.selectOption('#sample-select',data.samples.find(s=>s.id!==first).id);
    await page.selectOption('#sample-select',first);
    assert.equal(await page.locator('#correction').inputValue(),correction);
    assert.equal(await page.locator('#reviewed').isChecked(),true);
    assert.equal(await page.locator('#trelis-assessment').inputValue(),'missing_speech');
    const downloadPromise=page.waitForEvent('download');
    await page.click('#save');
    const download=await downloadPromise;
    const saved=path.join(dir,'synthetic-review-roundtrip.json');
    await download.saveAs(saved);
    const exported=JSON.parse(fs.readFileSync(saved,'utf8'));
    assert.deepEqual(exported.binding,data.binding);
    assert.equal(exported.reviews[first].reference,correction);
    assert.equal(exported.reviews[first].reviewed,true);
    assert.equal(exported.reviews[first].draft_checks['trelis:10'],'missing_speech');
    await page.reload({waitUntil:'load'});
    assert.equal(await page.locator('#correction').inputValue(),'');
    assert.equal(await page.locator('#reviewed').isChecked(),false);
    await page.locator('#import').setInputFiles(saved);
    await page.waitForFunction(()=>document.getElementById('save-status').textContent.startsWith('Loaded matching'));
    assert.equal(await page.locator('#correction').inputValue(),correction);
    assert.equal(await page.locator('#reviewed').isChecked(),true);
    await page.selectOption('#trelis-size','10');
    assert.equal(await page.locator('#trelis-assessment').inputValue(),'missing_speech');
    await page.fill('#correction',correction+' edited');
    assert.equal(await page.locator('#reviewed').isChecked(),false);
    // Reload only after explicitly dismissing the unsaved-changes dialog.
    page.once('dialog',dialog=>dialog.accept());
    await page.reload({waitUntil:'load'});
    const rejected=structuredClone(exported);
    rejected.binding.model_results_sha256.trelis='0'.repeat(64);
    await page.locator('#import').setInputFiles({name:'wrong-study.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(rejected))});
    await page.waitForFunction(()=>document.getElementById('save-status').textContent.startsWith('Could not load:'));
    assert.equal(await page.locator('#correction').inputValue(),'');
    assert.equal(await page.locator('#reviewed').isChecked(),false);
    // Reference-only imports merge a subset and cannot approve model drafts.
    const referenceIds=['section-10','section-02'];
    const protectedId=data.samples.find(sample=>!referenceIds.includes(sample.id)).id;
    const protectedReview=structuredClone(exported);
    protectedReview.reviews[protectedId]={reference:'QA ONLY: preserve this other passage.',notes:'Preserve these notes.',reviewed:true,draft_checks:{'apex:20':'looks_complete'},reference_provenance:null};
    await page.locator('#import').setInputFiles({name:'existing-full-review.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(protectedReview))});
    await page.waitForFunction(()=>document.getElementById('save-status').textContent.startsWith('Loaded matching'));
    const referencePayload={kind:'chunk_size_reference_check',version:1,binding:{manifest_sha256:data.binding.manifest_sha256,audio_sha256:{}},reviews:{}};
    for(const [i,id] of referenceIds.entries()){
      const sample=data.samples.find(sample=>sample.id===id),context={};
      referencePayload.binding.audio_sha256[id]=data.binding.audio_sha256[id];
      for(const [model,size] of [['apex',30],['trelis',15]])context[model]={window_seconds:size,text_sha256:createHash('sha256').update(sample.drafts[model][String(size)].text).digest('hex'),result_snapshot_sha256:(model==='apex'?'4':'5').repeat(64)};
      referencePayload.reviews[id]={reference:'QA ONLY reference '+i+' — जाँच.',notes:'Imported reference notes '+i,reviewed:i===0,draft_context:context,draft_checks:{'apex:30':'looks_complete','trelis:15':'looks_complete'}};
    }
    await page.locator('#import').setInputFiles({name:'interim-references.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(referencePayload))});
    await page.waitForFunction(()=>document.getElementById('save-status').textContent.startsWith('Imported 2 references.'));
    const mergedDownloadPromise=page.waitForEvent('download');await page.click('#save');
    const mergedFile=path.join(dir,'synthetic-merged-reference-review.json');await (await mergedDownloadPromise).saveAs(mergedFile);
    const merged=JSON.parse(fs.readFileSync(mergedFile,'utf8'));
    for(const sample of data.samples){
      const id=sample.id;
      if(!referenceIds.includes(id))assert.deepEqual(merged.reviews[id],protectedReview.reviews[id]);
      else{
        const source=referencePayload.reviews[id],actual=merged.reviews[id];
        assert.equal(actual.reference,source.reference);assert.equal(actual.notes,source.notes);assert.equal(actual.reviewed,source.reviewed);
        assert.deepEqual(actual.draft_checks,protectedReview.reviews[id].draft_checks);
        assert.deepEqual(actual.reference_provenance,{kind:'chunk_size_reference_check',manifest_sha256:data.binding.manifest_sha256,audio_sha256:data.binding.audio_sha256[id],draft_context:source.draft_context,edited_in_full_review:false});
      }
    }
    // Full-review reimport must preserve the reference's original snapshot hashes.
    await page.reload({waitUntil:'load'});
    await page.locator('#import').setInputFiles(mergedFile);
    await page.waitForFunction(()=>document.getElementById('save-status').textContent.startsWith('Loaded matching'));
    assert.equal(await page.locator('#correction').inputValue(),referencePayload.reviews[first].reference);
    const provenanceDownloadPromise=page.waitForEvent('download');await page.click('#save');
    const provenanceFile=path.join(dir,'synthetic-provenance-roundtrip.json');await (await provenanceDownloadPromise).saveAs(provenanceFile);
    const provenanceRoundtrip=JSON.parse(fs.readFileSync(provenanceFile,'utf8'));
    assert.deepEqual(provenanceRoundtrip.reviews,merged.reviews);
    const wrongReference=structuredClone(referencePayload);wrongReference.binding.audio_sha256['section-10']='0'.repeat(64);
    await page.locator('#import').setInputFiles({name:'wrong-reference-audio.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(wrongReference))});
    await page.waitForFunction(()=>document.getElementById('save-status').textContent.startsWith('Could not load:'));
    assert.equal(await page.locator('#correction').inputValue(),referencePayload.reviews[first].reference);
    assert.equal(await page.locator('#reviewed').isChecked(),true);
    await page.reload({waitUntil:'load'});
    await page.setViewportSize({width:390,height:900});
    await page.screenshot({path:path.join(dir,'mobile-summary.png')});
    await page.locator('#sample-title').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(dir,'mobile-player.png')});
    await page.locator('#trelis-size').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(dir,'mobile-trelis.png')});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'Horizontal overflow');
    assert.deepEqual(errors,[]);
    assert.deepEqual(external,[]);
    Object.assign(report,{passed:true,samples:data.samples.length,literal_drafts_verified:literalChecks,audio_durations_verified:audioChecks,playback_verified:true,seek_verified:true,corrections_roundtrip_verified:true,wrong_binding_rejected:true,editing_clears_reviewed:true,reference_only_import_verified:true,other_passages_preserved:true,no_model_approvals_transferred:true,reference_audio_mismatch_rejected:true,reference_provenance_roundtrip_verified:true,page_errors:errors,external_requests:external});
    fs.writeFileSync(path.join(dir,'browser-validation.json'),JSON.stringify(report,null,2)+'\n');
    process.stdout.write(JSON.stringify(report,null,2)+'\n');
  }finally{await browser.close();}
}
main().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
