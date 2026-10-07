"""Pace real local_worker input through the recorder journal contract.

This is prerecorded, synthetic two-track routing, not a physical call or UI test.
Only PCM whose capture clock has elapsed is committed into the worker session.
"""
from __future__ import annotations
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import uuid
import wave

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from sttbench.manifest import file_digest, read_json, write_json


def memory():
    class M(ctypes.Structure):
        _fields_ = [('length',ctypes.c_ulong),('load',ctypes.c_ulong),
                    *[(x,ctypes.c_ulonglong) for x in ('total','free','page','pagefree','virtual','virtualfree','extended')]]
    if os.name != 'nt': return {}
    value=M(); value.length=ctypes.sizeof(value)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value))
    return {'total_bytes':value.total,'available_bytes':value.free,'load_percent':value.load}


def pcm(path):
    with wave.open(str(path),'rb') as source:
        if (source.getnchannels(),source.getsampwidth(),source.getframerate()) != (1,2,16000):
            raise ValueError('Expected mono PCM16 16kHz')
        return source.readframes(source.getnframes())


def wav(path, raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),'wb') as target:
        target.setnchannels(1);target.setsampwidth(2);target.setframerate(16000);target.writeframes(raw)


def prepare(output, pilot, config):
    if output.exists(): raise ValueError('Choose a new result directory')
    if any(part.lower().startswith('onedrive') for part in output.resolve().parts):
        raise ValueError('Private audio must stay outside OneDrive')
    old=read_json(pilot/'manifest.json')
    ids=['fresh-call-03','fresh-call-05','fresh-call-06','section-02','section-10']
    output.mkdir(parents=True)
    parts=[]; sources=[];cursor=0
    for identifier in ids:
        sample=next(s for s in old['samples'] if s['id']==identifier)
        source=pilot/sample['audio']
        if file_digest(source)!=sample['audio_sha256']:raise ValueError('Source changed')
        raw=pcm(source)
        if len(raw)!=60*32000:raise ValueError('Expected 60-second parent')
        parts.append(raw)
        sources.append({'id':identifier,'source':str(source),'sha256':file_digest(source),
                        'start_seconds':cursor,'end_seconds':cursor+60})
        cursor+=60
    parts.append(bytes(5*32000))
    wav(output/'replay-source.wav',b''.join(parts))
    runtime=output/'runtime'; runtime.mkdir()
    hashes={}
    for source in sorted((LAB/'sttbench').rglob('*.py')):
        rel=source.relative_to(LAB)
        dest=runtime/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
        hashes[str(rel)]=file_digest(dest)
    shutil.copyfile(LAB/'models.json',runtime/'models.json')
    hashes['models.json']=file_digest(runtime/'models.json')
    (runtime/'worker.py').write_text("import runpy\nrunpy.run_module('sttbench.local_worker',run_name='__main__')\n",encoding='utf-8')
    cfg=read_json(config);cfg.update(worker_script=str(runtime/'worker.py'),registry_path=str(runtime/'models.json'))
    # Keep adapter temporary work separate from unrelated experiments.
    cfg['models']['apex']['work_dir']=str(output/'cli-work')
    write_json(output/'runtime.json',cfg)
    write_json(output/'manifest.json',{'version':1,'kind':'paced_local_worker_qualification',
      'created_at':datetime.now(timezone.utc).isoformat(),'sources':sources,'duration_seconds':305,
      'source_sha256':file_digest(output/'replay-source.wav'),'source_snapshot_sha256':hashes,
      'source_runtime_config_sha256':file_digest(config),'routing':
      'Alternate one active microphone/system track every 20 seconds; both carry identical audio from 260 to 300 seconds as a synthetic simultaneous-input load; all-zero silence from 300 to 305 seconds.',
      'limitations':['Saved audio replay, not live devices or calling application.',
       'Display-ready means durable worker status first observed; excludes native database/UI polling.',
       'Twenty-second windows inherently wait up to twenty seconds before inference.']})
    return {'prepared':str(output),'duration_seconds':305}


def route(source, index, track):
    raw=source[index*32000:(index+1)*32000]
    active=('microphone' if (index//20)%2==0 else 'system')
    return raw if track==active or 260<=index<300 else bytes(len(raw))


def append(journal, row):
    with journal.open('ab') as handle:
        handle.write(json.dumps(row,ensure_ascii=False).encode('utf-8')+b'\n');handle.flush();os.fsync(handle.fileno())


def commit(session, source, index, elapsed):
    if elapsed+1e-6 < index+1:raise ValueError('Attempt to expose future audio')
    for track in ('microphone','system'):
        raw=route(source,index,track)
        relative=f'tracks/{track}/{index:08}.wav';path=session/relative
        partial=path.with_suffix('.part');wav(partial,raw)
        with partial.open('r+b') as handle:os.fsync(handle.fileno())
        partial.replace(path)
        append(session/'timeline.jsonl',{'kind':'audio_chunk','track':track,'file':relative,
          'sequence':index,'sample_rate':16000,'sample_count':16000,'start_sample':index*16000,
          'end_sample':(index+1)*16000,'start_seconds':index,'end_seconds':index+1,'sha256':file_digest(path)})


def percentile(values, q):
    if not values:return None
    ordered=sorted(values);return ordered[max(0,math.ceil(len(ordered)*q)-1)]


def metrics(observed, elapsed, state, finalized_at, completed_at, commits):
    spoken=[s for s in observed if not s['digital_silence']]
    latency=[s['ready_elapsed_seconds']-s['end_seconds'] for s in spoken]
    service=[s['adapter_total_seconds'] for s in spoken if s['adapter_total_seconds'] is not None]
    cadence=[b['ready_elapsed_seconds']-a['ready_elapsed_seconds'] for a,b in zip(spoken,spoken[1:])]
    return {'state':state,'elapsed_seconds':elapsed,'observed_segments':len(observed),
      'nonzero_audio_segments':len(spoken),'window_end_to_ready_p50_seconds':percentile(latency,.5),
      'window_end_to_ready_p95_seconds':percentile(latency,.95),'window_end_to_ready_max_seconds':max(latency,default=None),
      'adapter_service_p50_seconds':percentile(service,.5),'adapter_service_p95_seconds':percentile(service,.95),
      'first_nonzero_window_end_to_ready_seconds':latency[0] if latency else None,
      'subsequent_window_end_to_ready_p95_seconds':percentile(latency[1:],.95),
      'nonzero_update_cadence_p50_seconds':percentile(cadence,.5),'nonzero_update_cadence_p95_seconds':percentile(cadence,.95),
      'adapter_realtime_factor':sum(service)/sum(s['end_seconds']-s['start_seconds'] for s in spoken) if spoken else None,
      'max_observed_backlog_seconds':max((s['backlog_seconds'] for s in observed),default=0),
      'post_stop_drain_seconds':completed_at-finalized_at if state=='complete' and completed_at is not None and finalized_at is not None else None,
      'stop_acknowledgement_seconds':completed_at-finalized_at if state=='stopped' and completed_at is not None and finalized_at is not None else None,
      'last_committed_seconds':len(commits),'max_commit_lateness_seconds':max((c['elapsed_seconds']-c['audio_end_seconds'] for c in commits),default=0),
      'future_audio_exposed':any(c['elapsed_seconds']+1e-6<c['audio_end_seconds'] for c in commits),
      'physical_capture_qualified':False,'ui_latency_qualified':False}


def run(output, profile, max_seconds=1500, run_name=None):
    if profile not in ('apex-20','trelis-5','trelis-10','trelis-20'):raise ValueError('Only selected application profiles')
    manifest=read_json(output/'manifest.json');cfg=read_json(output/'runtime.json')
    Path(cfg['models']['apex']['work_dir']).mkdir(parents=True,exist_ok=True)
    if file_digest(output/'replay-source.wav')!=manifest['source_sha256']:raise ValueError('Replay source changed')
    source=pcm(output/'replay-source.wav');duration=manifest['duration_seconds']
    run_name=run_name or profile
    if not run_name.replace('-','').isalnum():raise ValueError('Unsafe run name')
    dest=output/run_name
    if dest.exists():raise ValueError('Choose an unrun profile/output')
    dest.mkdir();session=dest/'session';session.mkdir();job=dest/'job';job.mkdir()
    write_json(session/'session.json',{'version':1,'id':str(uuid.uuid4()),'synthetic_replay':True})
    (session/'timeline.jsonl').write_bytes(b'')
    request={'job_id':str(uuid.uuid4()),'profile':profile,'language_mode':'hinglish','project_id':'tapf','session_dir':str(session)}
    write_json(job/'request.json',request)
    env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',STTAPP_PARENT_PID=str(os.getpid()),PYTHONUTF8='1')
    observed=[];seen=set();commits=[];status={};finalized_at=None;completed_at=None;stopped_reason=None
    before=memory();started=time.perf_counter()
    with (job/'worker.log').open('wb') as log:
        child=subprocess.Popen([cfg['python_executable'],cfg['worker_script'],'--config',str(output/'runtime.json'),'--request',str(job/'request.json')],
          env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        write_json(dest/'run-identity.json',{'profile':profile,'started_at':datetime.now(timezone.utc).isoformat(),
          'parent_pid':os.getpid(),'worker_pid':child.pid,'memory_before':before,'config_sha256':file_digest(output/'runtime.json'),
          'observer_sha256':file_digest(Path(__file__))})
        try:
            while True:
                elapsed=time.perf_counter()-started
                while len(commits)<duration and elapsed>=len(commits)+1 and stopped_reason is None:
                    index=len(commits);commit(session,source,index,elapsed)
                    commits.append({'audio_end_seconds':index+1,'elapsed_seconds':time.perf_counter()-started})
                    elapsed=time.perf_counter()-started
                if (len(commits)==duration or stopped_reason) and finalized_at is None:
                    append(session/'timeline.jsonl',{'kind':'capture_stopped','at_seconds':len(commits)})
                    append(session/'timeline.jsonl',{'kind':'capture_finalized'})
                    finalized_at=time.perf_counter()-started
                if (job/'status.json').exists():
                    try:
                        status=read_json(job/'status.json')
                    except (PermissionError,FileNotFoundError):
                        # Windows can briefly lock the file during atomic replace.
                        # Poll the next complete snapshot; do not stop the worker.
                        time.sleep(.02);continue
                    for s in status.get('segments',[]):
                        if s['id'] in seen:continue
                        seen.add(s['id'])
                        observed.append({'id':s['id'],'source_track':s['source_track'],'start_seconds':s['start_seconds'],
                          'end_seconds':s['end_seconds'],'ready_elapsed_seconds':time.perf_counter()-started,
                          'backlog_seconds':status.get('backlog_seconds',0),'digital_silence':s['audio_provenance']['digital_silence'],
                          'adapter_total_seconds':s['recognition'].get('timing',{}).get('total_seconds'),
                          'adapter_timing':s['recognition'].get('timing',{}),'quality_flags':s['quality_flags']})
                        print(json.dumps({'profile':profile,'window_end':s['end_seconds'],'track':s['source_track'],
                          'ready_seconds':round(observed[-1]['ready_elapsed_seconds'],2),'backlog':status.get('backlog_seconds'),
                          'silence':observed[-1]['digital_silence']}),flush=True)
                    spoken=[s for s in observed if not s['digital_silence']]
                    slow=[s for s in spoken[-3:] if (s['adapter_total_seconds'] or 0)>s['end_seconds']-s['start_seconds']]
                    if len(slow)==3 and status.get('backlog_seconds',0)>60 and not stopped_reason:
                        stopped_reason='Three consecutive nonzero windows slower than their audio duration, with backlog over 60 seconds.'
                        (job/'stop.request').write_text(stopped_reason,encoding='utf-8')
                    write_json(dest/'observations.json',{'segments':observed,'commits':commits,'memory':memory(),
                      'stopped_reason':stopped_reason,'metrics':metrics(observed,elapsed,status.get('state'),finalized_at,completed_at,commits)})
                    if status.get('state') in ('complete','failed','stopped'):
                        completed_at=time.perf_counter()-started;break
                if child.poll() is not None:break
                if elapsed>max_seconds:
                    stopped_reason='Bounded test time exhausted';(job/'stop.request').write_text(stopped_reason,encoding='utf-8')
                    if elapsed>max_seconds+180:child.terminate();break
                time.sleep(.1)
        finally:
            if child.poll() is None:
                (job/'stop.request').write_text('Paced observer finished',encoding='utf-8')
                try:child.wait(timeout=180)
                except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=30)
    final=metrics(observed,time.perf_counter()-started,status.get('state'),finalized_at,completed_at,commits)
    final.update(stopped_reason=stopped_reason,memory_before=before,memory_after=memory(),worker_exit_code=child.returncode,
       raw_status=str(job/'status.json'),profile=profile,language_mode='hinglish',
       tail_processed=all(status.get('cursors',{}).get(t,0)==duration for t in ('microphone','system')))
    write_json(dest/'observations.json',{'segments':observed,'commits':commits,'metrics':final})
    write_json(dest/'summary.json',final)
    print(json.dumps(final),flush=True);return final


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--output',type=Path,required=True);prep.add_argument('--pilot',type=Path,required=True);prep.add_argument('--config',type=Path,required=True)
    test=sub.add_parser('run');test.add_argument('--output',type=Path,required=True);test.add_argument('--profile',required=True);test.add_argument('--max-seconds',type=int,default=1500);test.add_argument('--run-name')
    args=parser.parse_args()
    print(json.dumps(prepare(args.output,args.pilot,args.config) if args.action=='prepare' else run(args.output,args.profile,args.max_seconds,args.run_name)),flush=True)
