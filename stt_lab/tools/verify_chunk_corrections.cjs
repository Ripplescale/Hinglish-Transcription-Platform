/* Verify an immutable local correction readback against its source analysis.
 * Usage: node verify_chunk_corrections.cjs <report.html> <new-private-QA-dir> <analysis.json>
 */
'use strict';
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const {createHash}=require('crypto'),{pathToFileURL}=require('url');
const {chromium}=require('playwright');
const hash=value=>createHash('sha256').update(value).digest('hex');
function tc(value){const ms=Math.round(value*1000);return String(Math.floor(ms/60000)).padStart(2,'0')+':'+String(Math.floor(ms%60000/1000)).padStart(2,'0')+'.'+String(ms%1000).padStart(3,'0');}

async function main(){
  const [input,out,source]=process.argv.slice(2);
  if(!input||!out||!source)throw new Error('Provide HTML, a new private QA directory, and source analysis JSON.');
  const html=path.resolve(input),dir=path.resolve(out),analysis=JSON.parse(fs.readFileSync(source,'utf8'));
  if(dir.split(path.sep).some(part=>part.toLowerCase().startsWith('onedrive')))throw new Error('Keep QA output outside OneDrive.');
  if(fs.existsSync(dir))throw new Error('Choose a new QA directory.');
  fs.mkdirSync(dir,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true,args:['--mute-audio']});
  const page=await browser.newPage({viewport:{width:1280,height:1100},deviceScaleFactor:1});
  const errors=[],external=[];
  page.on('pageerror',error=>errors.push(String(error)));
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
  await page.route(/^https?:/,route=>{external.push(route.request().url());return route.abort();});
  const report={kind:'chunk_size_corrections_browser_qa',input:html,html_sha256:hash(fs.readFileSync(html)),analysis_sha256:hash(fs.readFileSync(source))};
  try{
    await page.goto(pathToFileURL(html).href,{waitUntil:'load'});
    const data=await page.locator('#study-data').evaluate(node=>JSON.parse(node.textContent));
    assert.deepEqual(data.source,analysis.source);
    assert.deepEqual(data.models,analysis.models);
    assert.deepEqual(data.sizes,analysis.sizes);
    assert.equal(data.trelis_romanization,false);
    assert.equal(data.release_qualified,false);
    assert.equal(await page.locator('#sample-select option').count(),analysis.samples.length);
    assert.equal(await page.locator('#apex-size').inputValue(),'15');
    assert.equal(await page.locator('#trelis-size').inputValue(),'15');
    assert.equal(await page.locator('textarea,input[type=file]').count(),0);
    await page.screenshot({path:path.join(dir,'desktop-summary.png')});
    let draftChecks=0,referenceChecks=0,audioChecks=0,assessmentChecks=0,tableCells=0;
    const tables=[
      ['#apex-results > .table-wrap table','apex','aggregate'],
      ['#trelis-results > .table-wrap table','trelis','aggregate'],
      ['#boundary-sensitivity table','apex','boundary_certain_aggregate'],
      ['#script-sensitivity table','trelis','script_stable_aggregate']
    ];
    for(const [selector,model,key] of tables){
      const actual=await page.locator(selector+' tbody tr').evaluateAll(rows=>rows.map(row=>Array.from(row.querySelectorAll('td'),cell=>cell.textContent)));
      const expected=analysis.models[model].rows.map(row=>{
        const m=row[key]||{},n=m.reference_words||0;
        return [row.size+'s',m.wer==null?'Not scored':(m.wer*100).toFixed(1)+'%',String(n),n?String(m.deletions||0):'—',n?String(m.substitutions||0):'—',n?String(m.insertions||0):'—'];
      });
      assert.deepEqual(actual,expected,'Displayed cells differ from source: '+model+' '+key);
      tableCells+=expected.length*6;
    }
    for(const expected of analysis.samples){
      const sample=data.samples.find(s=>s.id===expected.id);
      for(const key of ['reference','previous_reference','notes','draft_checks','reference_source','reference_kind','reference_scope_note','reviewed','reference_provenance'])assert.deepEqual(sample[key],expected[key],expected.id+' '+key);
      const audioBytes=Buffer.from(sample.audio_uri.split(',')[1],'base64');
      assert.equal(hash(audioBytes),expected.audio_sha256);
      await page.selectOption('#sample-select',expected.id);
      await page.waitForFunction(()=>{const a=document.getElementById('audio');return Number.isFinite(a.duration)&&a.currentSrc===a.src;});
      assert.ok(Math.abs(await page.locator('#audio').evaluate(a=>a.duration)-expected.duration_seconds)<0.01,'Audio duration mismatch');
      audioChecks++;
      assert.equal(await page.locator('#reference-text').textContent(),expected.reference||'');
      assert.equal(await page.locator('#notes-text').textContent(),expected.notes||'');
      assert.equal(await page.locator('#previous-reference').textContent(),expected.previous_reference||'');
      assert.equal(await page.locator('#previous-reference-details').evaluate(n=>n.open),false);
      assert.equal(await page.locator('#sample-source').textContent(),expected.call_title+' · '+tc(expected.source_start_seconds)+'–'+tc(expected.source_start_seconds+expected.duration_seconds)+' in decoded source audio');
      referenceChecks++;
      for(const model of ['apex','trelis'])for(const size of analysis.sizes){
        const draft=expected.drafts[model][String(size)];
        assert.equal(sample.drafts[model][String(size)].text,draft.text);
        await page.selectOption('#'+model+'-size',String(size));
        assert.equal(await page.locator('#'+model+'-text').textContent(),draft.text);
        assert.equal(await page.locator('#'+model+'-warning').isVisible(),Boolean(draft.script_surface_warning));
        const flag=expected.draft_checks?.[model+':'+size];
        const assessment=await page.locator('#'+model+'-assessment').textContent();
        const names={looks_complete:'Looks complete after listening',missing_speech:'Missing spoken content',unclear:'Unclear / needs another listen'};
        if(flag&&flag!=='not_checked')assert.equal(assessment,'Your '+size+'s draft check: '+names[flag]+'. Applies only to this draft.');
        else assert.equal(assessment,'Not assessed in this correction file.');
        assessmentChecks++;draftChecks++;
      }
    }
    const first=data.display_order[0];
    await page.selectOption('#sample-select',first);
    await page.selectOption('#apex-size','15');await page.selectOption('#trelis-size','15');
    await page.selectOption('#speed','0.75');
    assert.equal(await page.locator('#audio').evaluate(a=>a.playbackRate),0.75);
    await page.locator('#audio').evaluate(async a=>{await a.play();});
    await page.waitForTimeout(300);
    assert.equal(await page.locator('#audio').evaluate(a=>a.paused),false);
    await page.locator('#audio').evaluate(a=>{a.pause();a.currentTime=0;});
    await page.click('#forward');assert.ok(Math.abs(await page.locator('#audio').evaluate(a=>a.currentTime)-5)<0.1);
    await page.click('#back');assert.ok(await page.locator('#audio').evaluate(a=>a.currentTime)<0.1);
    await page.click('#next');assert.equal(await page.locator('#sample-select').inputValue(),data.display_order[1]);
    await page.click('#previous');assert.equal(await page.locator('#sample-select').inputValue(),first);
    for(const id of ['boundary-sensitivity','script-sensitivity','methods','identifiers']){
      await page.locator('#'+id+' > summary').click();
      assert.equal(await page.locator('#'+id).evaluate(n=>n.open),true);
      await page.locator('#'+id+' > summary').click();
    }
    const revisedEnglish=analysis.samples.find(s=>s.reference_kind==='english'&&s.previous_reference&&s.previous_reference!==s.reference);
    if(revisedEnglish){
      await page.selectOption('#sample-select',revisedEnglish.id);
      await page.locator('#previous-reference-details > summary').click();
      assert.equal(await page.locator('#previous-reference-details').evaluate(n=>n.open),true);
      assert.equal(await page.locator('#previous-reference').isVisible(),true);
      assert.equal(await page.locator('#previous-reference').textContent(),revisedEnglish.previous_reference);
      await page.locator('#previous-reference-details').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(dir,'desktop-prior-reference.png')});
    }
    const native=analysis.samples.find(s=>s.reference_kind==='native_mixed');
    if(native){
      await page.selectOption('#sample-select',native.id);
      await page.selectOption('#trelis-size','15');
      assert.equal(await page.locator('#reference-text').textContent(),native.reference);
      assert.match(await page.locator('#trelis-text').textContent(),/[\u0900-\u097f]/);
      await page.locator('#reference-heading').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(dir,'desktop-native-reference.png')});
      await page.locator('#trelis-size').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(dir,'desktop-native-draft.png')});
    }
    await page.selectOption('#sample-select',first);
    await page.locator('#sample-title').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(dir,'desktop-review.png')});
    await page.locator('#apex-size').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(dir,'desktop-drafts.png')});
    await page.setViewportSize({width:390,height:900});await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:path.join(dir,'mobile-summary.png')});
    await page.locator('#sample-title').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(dir,'mobile-player.png')});
    await page.locator('#trelis-size').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(dir,'mobile-trelis.png')});
    if(native){
      await page.selectOption('#sample-select',native.id);
      await page.locator('#trelis-size').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(dir,'mobile-native-trelis.png')});
    }
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'Horizontal overflow');
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    Object.assign(report,{passed:true,samples:analysis.samples.length,literal_drafts_verified:draftChecks,checked_references_verified:referenceChecks,audio_hashes_and_durations_verified:audioChecks,exact_draft_assessment_scopes_verified:assessmentChecks,rendered_table_cells_verified:tableCells,prior_reference_expansion_verified:Boolean(revisedEnglish),native_reference_and_draft_verified:Boolean(native),source_timestamps_verified:referenceChecks,playback_verified:true,seek_verified:true,sensitivity_tables_verified:true,mobile_overflow:false,page_errors:errors,external_requests:external});
    fs.writeFileSync(path.join(dir,'browser-validation.json'),JSON.stringify(report,null,2)+'\n');
    process.stdout.write(JSON.stringify(report,null,2)+'\n');
  }finally{await browser.close();}
}
main().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
