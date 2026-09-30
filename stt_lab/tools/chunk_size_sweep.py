"""Reproducible, script-preserving chunk-size development experiment."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import socket
import sys
import time
import wave

LAB=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(LAB))
from sttbench.manifest import read_json, write_json, file_digest, digest
from sttbench.normalization import basic_tokens, word_errors, entity_counts, Normalizer
from sttbench.runtime import transcribe
from prepare_expanded_review import pcm_slice, inside

SIZES=(5,10,15,20,30)


def windows(frame_count, size, rate=16000):
    """Disjoint fixed windows; rebalance last two if the final tail is under 1s."""
    step=int(size*rate)
    if frame_count<=0 or step<=0: raise ValueError('Expected positive duration')
    cuts=[(start,min(start+step,frame_count)) for start in range(0,frame_count,step)]
    if len(cuts)>1 and cuts[-1][1]-cuts[-1][0]<rate:
        start=cuts[-2][0];end=cuts[-1][1];mid=(start+end)//2
        cuts[-2:]=[(start,mid),(mid,end)]
    return cuts


def pcm(path):
    with wave.open(str(path),'rb') as w:
        if (w.getnchannels(),w.getsampwidth(),w.getframerate())!=(1,2,16000): raise ValueError('Unexpected PCM')
        return w.readframes(w.getnframes())


def prepare(bench, output):
    if output.exists() or any(p.lower().startswith('onedrive') for p in output.parts):
        raise ValueError('Choose a new private study folder')
    screening=bench/'screening-20260918-six-calls'
    screen=read_json(screening/'screening.json')
    refs_path=screening/'reviewed-20260922/user-references-v2.json'
    policy_path=screening/'reviewed-20260922/policy-v2.json'
    refs=read_json(refs_path);policy=read_json(policy_path)
    if refs['screening_sha256']!=file_digest(screening/'screening.json') or policy['references_sha256']!=file_digest(refs_path):
        raise ValueError('Reference input bindings changed')
    clips={c['id']:c for c in screen['clips']};calls={c['id']:c for c in screen['calls']}
    samples=[]
    for reference in refs['samples']:
        if not reference['review_completed']: raise ValueError('Reference not checked')
        cid=reference['sample_id'];c=clips[cid];path=inside(screening,c['audio'])
        if file_digest(path)!=c['audio_sha256']:raise ValueError('Source audio changed')
        samples.append({'id':cid,'source_path':str(path),'audio_sha256':c['audio_sha256'],
          'title':f"Checked excerpt {reference['excerpt']}",'call_title':calls[c['call_id']]['title'],
          'source_start_seconds':c['start_seconds'],'duration_seconds':c['duration_seconds'],
          'reference_status':'user_reviewed','reference_roman':reference['reference_roman'],
          'reference_scope_note':reference.get('reference_scope_note',''),
          'entities':policy['entity_annotations'].get(cid,[]),
          'trelis_decoding':{'language':'en','mixed_code':False} if cid=='call-06-01' else {'language':'hi','mixed_code':True},
          'direct_trelis_reference_candidate':cid=='call-06-01'})
    review=bench/'minute-review-20260922';minutes=read_json(review/'review-manifest.json')
    for sid,title in [('section-02','Unreviewed Hinglish continuity check'),('section-10','Unreviewed known omission case')]:
        s=next(s for s in minutes['sections'] if s['id']==sid)
        path=inside(review,s['audio'])
        if file_digest(path)!=s['audio_sha256']:raise ValueError('Minute audio changed')
        samples.append({'id':sid,'title':title,'source_path':str(path),'audio_sha256':s['audio_sha256'],
          'call_title':s['call_title'],'source_start_seconds':s['start_seconds'],'duration_seconds':60,
          'reference_status':'pending','reference_roman':None,'entities':[],
          'trelis_decoding':{'language':'en','mixed_code':False} if sid=='section-10' else {'language':'hi','mixed_code':True},
          'direct_trelis_reference_candidate':False})
    output.mkdir(parents=True);(output/'audio').mkdir();(output/'chunks').mkdir();(output/'configs').mkdir()
    for s in samples:
        original=Path(s.pop('source_path'));parent=output/'audio'/f"{s['id']}.wav"
        shutil.copyfile(original,parent)
        if file_digest(parent)!=s['audio_sha256']:raise ValueError('Audio copy changed')
        s['source_audio_path']=str(original);s['audio']=parent.relative_to(output).as_posix()
        original_pcm=pcm(parent);s['partitions']={}
        for size in SIZES:
            rows=[];parts=[]
            for number,(start,end) in enumerate(windows(len(original_pcm)//2,size),1):
                target=output/'chunks'/f"{s['id']}-{size}s-{number:02}.wav"
                pcm_slice(parent,target,start/16000,end/16000)
                raw=pcm(target);parts.append(raw)
                rows.append({'id':f"{s['id']}:{size}:{number}",'audio':target.relative_to(output).as_posix(),
                    'audio_sha256':file_digest(target),'start_seconds':start/16000,'end_seconds':end/16000,
                    'duration_seconds':(end-start)/16000})
            if b''.join(parts)!=original_pcm:raise ValueError('Partition does not reconstruct source')
            s['partitions'][str(size)]=rows
    write_json(output/'manifest.json',{'version':1,'kind':'chunk_size_development_sweep',
      'created_at':datetime.now(timezone.utc).isoformat(),'sizes':list(SIZES),'samples':samples,
      'reference_sha256':file_digest(refs_path),'policy_sha256':file_digest(policy_path),
      'partition_policy':'Disjoint windows; only a final tail shorter than one second is rebalanced equally with the previous window. Literal joining, no deduplication or rewriting.',
      'trelis_romanization':False,'held_out':False,'factors_held_constant':['model revision','runtime','decoding profile per parent','audio samples'],
      'ranking_policy':'Compare WER, substitutions, deletions and insertions together; no maximum-word-count ranking. Trelis has only one possibly script-compatible English reference; no Hinglish optimum can be established automatically.',
      'timing_policy':'Runtime observations only; workers may run concurrently and no live-speed ranking is admitted.'})
    apex=read_json(bench/'conversion-20260922/apex-vulkan-q5_0-text.json')
    trelis=read_json(review/'trelis-original-cpu.json');trelis.pop('section_decoding',None)
    write_json(output/'configs/apex.json',apex);write_json(output/'configs/trelis.json',trelis)
    print(json.dumps({'samples':len(samples),'audio_seconds':sum(s['duration_seconds'] for s in samples),
      'sizes':SIZES,'chunk_jobs_per_model':sum(len(c) for s in samples for c in s['partitions'].values()),'output':str(output)}))


def infer(root, model):
    manifest_path=root/'manifest.json';manifest=read_json(manifest_path)
    config=read_json(root/'configs'/f'{model}.json')
    spec=copy.deepcopy(next(m for m in read_json(LAB/'models.json')['models'] if m['id']==model))
    spec['artifact_path']=config['artifact_path']
    output=root/'results'/f'{model}.json'
    identity={'manifest_sha256':file_digest(manifest_path),'model_spec':spec,'runtime_config':config,
      'runner_sha256':file_digest(Path(__file__)),'adapter_sha256':file_digest(LAB/'sttbench/runtime/api.py')}
    report={'version':1,'kind':'chunk_size_sweep_results','model_id':model,**identity,
      'state':'running','chunks':{},'network_attempts':[]}
    if output.exists():
        old=read_json(output)
        if any(old.get(k)!=v for k,v in identity.items()):raise ValueError('Cannot resume changed inputs/code')
        report=old
    write_json(output,report)
    prior=socket.socket.connect,socket.socket.connect_ex,socket.create_connection
    def denied(*args,**kwargs):
        report['network_attempts'].append('Blocked Python socket call');raise OSError('Private inference is offline')
    socket.socket.connect=socket.socket.connect_ex=socket.create_connection=denied
    cache={r['request_sha256']:r for r in report['chunks'].values() if r['status']=='ok'}
    try:
        for size in reversed(manifest['sizes']):
            for sample in manifest['samples']:
                effective=copy.deepcopy(spec)
                if model=='trelis':effective['decoding'].update(sample['trelis_decoding'])
                for chunk in sample['partitions'][str(size)]:
                    path=inside(root,chunk['audio'])
                    if file_digest(path)!=chunk['audio_sha256']:raise ValueError('Chunk changed')
                    if report['chunks'].get(chunk['id'],{}).get('status')=='ok':continue
                    key=digest({'audio_sha256':chunk['audio_sha256'],'spec':effective,'config':config})
                    if key in cache:
                        result=copy.deepcopy(cache[key]);result['reused_identical_request']=True
                    else:
                        result=transcribe(effective,path,config)
                        result['reused_identical_request']=False
                    result.update(audio_sha256=chunk['audio_sha256'],request_sha256=key,
                        sample_id=sample['id'],size=size,start_seconds=chunk['start_seconds'],end_seconds=chunk['end_seconds'])
                    report['chunks'][chunk['id']]=result;write_json(output,report)
                    print(json.dumps({'model':model,'chunk':chunk['id'],'status':result['status'],
                        'chars':len(result['text']),'reused':result['reused_identical_request']}),flush=True)
                    if result['status']!='ok':raise ValueError(f"Inference failed: {result.get('error')}")
                    cache[key]=copy.deepcopy(result)
        report['state']='complete'
    finally:
        socket.socket.connect,socket.socket.connect_ex,socket.create_connection=prior
        write_json(output,report)


def aggregate(rows):
    keys=('reference_words','hypothesis_words','errors','substitutions','deletions','insertions')
    out={k:sum(r[k] for r in rows) for k in keys}
    out['wer']=out['errors']/out['reference_words'] if out['reference_words'] else None
    out['deletion_rate']=out['deletions']/out['reference_words'] if out['reference_words'] else None
    return out


def score(root):
    m=read_json(root/'manifest.json')
    output={'kind':'chunk_size_sweep_summary','sizes':m['sizes'],'manifest_sha256':file_digest(root/'manifest.json'),
      'models':{},'held_out':False,'trelis_romanization':False,'release_qualified':False,
      'interpretation':'Development pilot. Raw deletion rates are alignment diagnostics, not complete semantic coverage. Model-specific scoring scopes differ and must not be ranked against each other.'}
    for model in ('apex','trelis'):
        r=read_json(root/'results'/f'{model}.json')
        if r['state']!='complete' or r['manifest_sha256']!=output['manifest_sha256'] or r['network_attempts']:raise ValueError('Incomplete/unbound results')
        rows=[]
        for size in m['sizes']:
            samples=[];scored=[];certain=[]
            for s in m['samples']:
                chunks=[r['chunks'][c['id']] for c in s['partitions'][str(size)]]
                for pred,c in zip(chunks,s['partitions'][str(size)]):
                    if pred['status']!='ok' or pred['audio_sha256']!=c['audio_sha256']:raise ValueError('Invalid prediction')
                text='\n\n'.join(c['text'] for c in chunks)
                devanagari=any('\u0900'<=c<='\u097f' for c in text)
                eligible=s['reference_status']=='user_reviewed' and (model=='apex' or s['direct_trelis_reference_candidate'])
                status='scored' if eligible else ('pending_native_script_reference' if s['reference_roman'] else 'pending_human_review')
                if model=='trelis' and eligible and devanagari: status='script_confounded';eligible=False
                metrics=word_errors(basic_tokens(s['reference_roman']),basic_tokens(text)) if eligible else None
                if metrics:
                    scored.append(metrics)
                    if not s.get('reference_scope_note'):certain.append(metrics)
                samples.append({'sample_id':s['id'],'text':text,'chunks':len(chunks),'score_status':status,
                    'metrics':metrics,'contains_devanagari':devanagari,
                    'entity_counts':entity_counts(s['entities'],text,Normalizer()) if eligible else None})
            rows.append({'size':size,'scored_clips':len(scored),'aggregate':aggregate(scored),
                'boundary_certain_aggregate':aggregate(certain),'samples':samples})
        output['models'][model]={'result_sha256':file_digest(root/'results'/f'{model}.json'),'sizes':rows}
    write_json(root/'summary.json',output)
    print(json.dumps({model:[{'size':r['size'],'scored':r['scored_clips'],**r['aggregate']} for r in output['models'][model]['sizes']] for model in output['models']}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--benchmark-root',type=Path,required=True);prep.add_argument('--output',type=Path,required=True)
    inf=sub.add_parser('infer');inf.add_argument('--root',type=Path,required=True);inf.add_argument('--model',choices=('apex','trelis'),required=True)
    sc=sub.add_parser('score');sc.add_argument('--root',type=Path,required=True)
    a=p.parse_args()
    if a.command=='prepare':prepare(a.benchmark_root.resolve(),a.output.resolve())
    elif a.command=='infer':infer(a.root.resolve(),a.model)
    else:score(a.root.resolve())
