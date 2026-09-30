"""Prepare an isolated native-app fixture from previously authorized audio.

This never opens a microphone or sends audio. Existing recordings and model
weights are not modified. A unique test root is created for each invocation.
"""
import argparse, hashlib, json, shutil, sqlite3, uuid, wave
from pathlib import Path
from datetime import datetime, timezone


def write_json(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def prepare(runtime, source, output):
    output=output.resolve()
    if output.exists(): raise ValueError('Choose a new native QA root')
    if any(part.lower().startswith('onedrive') for part in output.parts): raise ValueError('Private audio must stay outside OneDrive')
    output.mkdir(parents=True)
    shutil.copyfile(runtime,output/'runtime.json')
    # A fresh SQLite header prevents migration of the user's real meeting history.
    db=sqlite3.connect(output/'meeting_minutes.sqlite'); db.execute('VACUUM'); db.close()
    sid=str(uuid.uuid4()); session=output/'recordings'/sid
    track=session/'tracks'/'system';track.mkdir(parents=True)
    now=datetime.now(timezone.utc).isoformat()
    write_json(session/'session.json',{'schema_version':1,'session_id':sid,'meeting_name':'Native integration check — saved audio','created_at':now,'devices':{'microphone':None,'system':'Previously recorded test excerpt'},'format':'pcm_s16le_mono_wav','chunk_target_seconds':1,'timeline_clock':'saved_audio_seconds','transcription_required':False})
    with wave.open(str(source),'rb') as audio:
        rate=audio.getframerate()
        if audio.getnchannels()!=1 or audio.getsampwidth()!=2:raise ValueError('Use mono PCM16 source')
        pcm=audio.readframes(rate*20)
    if len(pcm)!=rate*20*2:raise ValueError('Need twenty seconds of saved speech')
    rows=[]
    for sequence in range(20):
        chunk=track/f'{sequence:08d}.wav'
        with wave.open(str(chunk),'wb') as target:
            target.setparams((1,2,rate,0,'NONE','not compressed'));target.writeframes(pcm[sequence*rate*2:(sequence+1)*rate*2])
        rows.append({'kind':'audio_chunk','track':'system','sequence':sequence,'file':f'tracks/system/{sequence:08d}.wav','sample_rate':rate,'sample_count':rate,'start_seconds':sequence,'end_seconds':sequence+1,'sha256':hashlib.sha256(chunk.read_bytes()).hexdigest()})
    rows.extend([{'kind':'capture_stopped','at_seconds':20},{'kind':'capture_finalized','tracks':['system'],'dropped_samples':0}])
    (session/'timeline.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
    with wave.open(str(session/'audio.wav'),'wb') as target:
        target.setparams((1,2,rate,0,'NONE','not compressed'));target.writeframes(pcm)
    evidence={'data_root':str(output),'session_dir':str(session),'session_id':sid,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'source_start_seconds':0,'duration_seconds':20,'recorded_live':False}
    write_json(output/'fixture.json',evidence)
    return evidence

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(prepare(a.runtime,a.source,a.output)))
