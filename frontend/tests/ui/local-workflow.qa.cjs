/* Browser journey over synthetic native commands. Never records devices or sends content. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
async function main() {
  const output = process.argv[2]; fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({ channel:'msedge', headless:true });
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[]; page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(() => {
      const calls=[]; window.qaCalls=calls;
      const callbacks=new Map(), listeners=new Map(); let callbackId=0, listenerId=0;
      let recording=false, job=null, segments=[];
      const capture={session_id:'qa-session',session_dir:'C:/STTApp/recordings/qa-session'};
      const workspace={version:1,meeting_id:'qa-meeting',revision:0,notes:'',corrections:[],project_id:null,profile:'trelis-20'};
      window.qaEmit=(event,payload)=>{for(const [id,listener] of listeners){if(listener.event===event)callbacks.get(listener.handler)?.({event,id,payload});}};
      window.qaAdvance=()=>{job={...job,segments:[{id:'live-one',text:'मीरा ने seven notebooks कहा',source_track:'system',start_seconds:0,end_seconds:20}],processed_audio_seconds:20,backlog_seconds:0};};
      window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener(_event,id){listeners.delete(id);}};
      window.__TAURI_OS_PLUGIN_INTERNALS__={platform:'windows',arch:'x86_64',family:'windows',type:'windows',version:'10'};
      window.__TAURI_INTERNALS__={metadata:{currentWindow:{label:'main'},currentWebview:{label:'main'}},convertFileSrc:p=>p,transformCallback:fn=>{callbacks.set(++callbackId,fn);return callbackId;},unregisterCallback:id=>callbacks.delete(id),invoke:async(command,args={})=>{
        calls.push({command,args});
        if(command==='plugin:event|listen'){listeners.set(++listenerId,args);return listenerId;}
        if(command==='plugin:event|unlisten'){listeners.delete(args.eventId);return null;}
        if(command==='get_onboarding_status')return {completed:true};
        if(command==='get_active_capture')return recording?capture:null;
        if(command==='get_local_stt_profiles')return ['trelis-20','apex-20'].map(id=>({id,model:id.split('-')[0],chunk_seconds:20,available:true,live_qualified:false}));
        if(command==='get_recording_state')return {is_recording:recording,is_paused:false,is_active:recording};
        if(command==='is_recording')return recording;
        if(command==='ensure_capture_meeting')return {meeting_id:'qa-meeting',title:'Synthetic call'};
        if(command==='start_local_transcription'){job={job_id:'qa-job',session_dir:capture.session_dir,profile:args.profile,state:'running',segments:[]};return job;}
        if(command==='get_local_transcription_status')return job;
        if(command==='import_local_transcription'){segments=(job?.segments??[]).map(s=>({...s,audio_start_time:s.start_seconds,audio_end_time:s.end_seconds,timestamp:'12:00'}));return {meeting_id:'qa-meeting',imported_count:segments.length};}
        if(command==='api_get_meetings')return [{id:'qa-meeting',title:'Synthetic call'}];
        if(command==='api_get_meeting_metadata')return {id:'qa-meeting',title:'Synthetic call',created_at:'2026-09-30T12:00:00Z',updated_at:'2026-09-30T12:00:00Z',folder_path:capture.session_dir};
        if(command==='api_get_meeting_transcripts')return {transcripts:segments.slice(args.offset,args.offset+args.limit),total_count:segments.length,has_more:args.offset+args.limit<segments.length};
        if(command==='load_transcript_workspace')return workspace;
        if(command==='api_get_summary')return {status:'idle',data:null};
        if(command==='local_get_meeting_audio')return {path:null};
        if(command==='api_get_transcript_config')return {provider:'localWhisper',model:'apex'};
        if(command==='list_local_transcription_jobs')return job?[job]:[];
        if(command==='list_recoverable_captures'||command==='get_audio_devices')return [];
        if(command==='check_first_launch')return false;
        if(command.startsWith('plugin:store|load'))return 1;
        if(command.startsWith('plugin:store|get'))return [null,false];
        if(command.startsWith('plugin:store|has'))return false;
        if(command.startsWith('plugin:os|'))return 'windows';
        return null;
      }};
      window.qaStart=()=>{recording=true;window.qaEmit('recording-started',{capture});};
      window.qaStop=()=>{recording=false;job={...job,state:'complete'};window.qaEmit('recording-stopped',{capture,folder_path:capture.session_dir,meeting_name:'Synthetic call'});};
    });
    await page.goto('http://localhost:3118/',{waitUntil:'domcontentloaded',timeout:120000});
    await page.getByLabel('Transcription timing').waitFor({timeout:60000});
    assert.equal(await page.getByLabel('Local transcription profile').locator('option').count(),2);
    assert.equal(await page.getByLabel('Local transcription profile').inputValue(),'trelis-20');
    await page.waitForFunction(()=>window.qaCalls.some(c=>c.command==='get_active_capture'));
    await page.getByLabel('Local transcription profile').selectOption('apex-20');
    await page.getByLabel('Transcription timing').selectOption('during-recording');
    await page.getByText('New text appears after each 20-second audio window is processed.',{exact:false}).waitFor();
    await page.evaluate(()=>window.qaStart());
    try { await page.waitForFunction(()=>window.qaCalls.some(c=>c.command==='start_local_transcription')); } catch(error) { fs.writeFileSync(path.join(output,'failure.json'),JSON.stringify(await page.evaluate(()=>({calls:window.qaCalls,body:document.body.innerText})),null,2)); throw error; }
    await page.evaluate(()=>window.qaAdvance());
    await page.getByText('मीरा ने seven notebooks कहा',{exact:true}).waitFor({timeout:15000});
    await page.screenshot({path:path.join(output,'live.png'),fullPage:true});
    await page.evaluate(()=>window.qaStop());
    await page.waitForURL(/meeting-details\?id=qa-meeting/,{timeout:15000});
    await page.getByRole('heading',{name:'Synthetic call',exact:true}).waitFor({timeout:60000});
    await page.getByText('मीरा ने seven notebooks कहा',{exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'saved.png'),fullPage:true});
    const evidence=await page.evaluate(()=>window.qaCalls);
    assert.equal(evidence.filter(c=>c.command==='start_local_transcription').length,1);
    assert.ok(evidence.some(c=>c.command==='import_local_transcription'&&c.args.primary===true));
    assert.ok(!evidence.some(c=>/api_process_transcript|builtin_ai_download_model|open_claude_desktop/.test(c.command)));
    assert.deepEqual(errors,[]);
    const report={passed:true,synthetic_ipc:true,checks:['two model choices','Trelis20 default','automatic during-capture worker','incremental native text','automatic primary import','stop opens saved meeting','single worker per capture','no automatic summaries or Claude handoff'],page_errors:errors};
    fs.writeFileSync(path.join(output,'workflow-validation.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
  }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});


