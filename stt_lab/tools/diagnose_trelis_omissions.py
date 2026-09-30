"""Controlled private Trelis reruns; preserve previous drafts and raw token traces."""
from __future__ import annotations
import argparse
import copy
import importlib.metadata
import json
from pathlib import Path
import socket
import sys
import wave

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from sttbench.manifest import file_digest, read_json, write_json
from sttbench.runtime import api
from prepare_expanded_review import pcm_slice


def run(root, output):
    if output.exists() or any(p.lower().startswith('onedrive') for p in output.parts):
        raise ValueError('Choose a new private diagnosis folder')
    manifest = read_json(root/'review-manifest.json')
    section = next(s for s in manifest['sections'] if s['id']=='section-10')
    chunk = section['chunks'][0]
    audio = root/chunk['audio']
    if file_digest(audio) != chunk['audio_sha256']:
        raise ValueError('Input audio changed')
    old = read_json(root/'results'/'trelis.json')
    if old['manifest_sha256'] != file_digest(root/'review-manifest.json'):
        raise ValueError('Previous result manifest changed')
    output.mkdir(parents=True)
    config = read_json(root/'trelis-original-cpu.json')
    config.pop('section_decoding', None)
    spec = copy.deepcopy(old['model_spec'])
    original_result = old['chunks'][chunk['id']]
    report = {'section':section, 'input_sha256':file_digest(audio),
        'original_result_sha256':file_digest(root/'results'/'trelis.json'),
        'original_result': original_result, 'cases':{}, 'network_attempts':[],
        'model_spec':spec, 'runtime_config':config,
        'versions':{n:importlib.metadata.version(n) for n in ('torch','transformers','numpy')},
        'code_sha256':{'diagnosis':file_digest(Path(__file__)), 'runtime':file_digest(LAB/'sttbench/runtime/api.py')},
        'human_reference':None}
    write_json(output/'diagnosis.json',report)
    saved = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    def deny(*args, **kwargs):
        report['network_attempts'].append('Blocked Python socket connection')
        raise OSError('Private inference is offline')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    import torch
    from transformers.generation.utils import GenerationMixin
    original_generate = GenerationMixin.generate
    traces = []
    def traced_generate(self, *args, **kwargs):
        out = original_generate(self, *args, **kwargs)
        sequence = out.sequences if hasattr(out,'sequences') else out
        ids = sequence[0].detach().cpu().tolist()
        prefix = kwargs.get('decoder_input_ids')
        traces.append({'sequence_ids':ids, 'sequence_length':len(ids),
            'decoder_input_ids':prefix[0].tolist() if prefix is not None else None,
            'eos_token_id':self.generation_config.eos_token_id,
            'last_token_id':ids[-1] if ids else None})
        return out
    GenerationMixin.generate = traced_generate
    def run_case(name, source, language='en', mixed=False, publisher=False):
        traces.clear()
        effective = copy.deepcopy(spec)
        effective['decoding'].update(language=language, mixed_code=mixed)
        if not publisher:
            result = api.transcribe(effective,source,config)
        else:
            # Use the publisher's explicit prefix, on the same CPU/dtype and loaded checkpoint.
            processor,model = next(iter(api._CACHE.values()))
            import numpy as np
            with wave.open(str(source),'rb') as wav:
                samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').astype('float32')/32768
            features=processor.feature_extractor(samples,sampling_rate=16000,return_tensors='pt').input_features
            ids=processor.tokenizer.convert_tokens_to_ids
            prefix=[ids('<|startoftranscript|>'),ids('<|en|>'),ids('<|transcribe|>'),ids('<|notimestamps|>')]
            with api._offline_environment(),torch.inference_mode():
                generated=model.generate(input_features=features,decoder_input_ids=torch.tensor([prefix]),max_new_tokens=440)
            result={'status':'ok','text':processor.tokenizer.decode(generated[0],skip_special_tokens=True).strip(),
                    'profile':'Publisher explicit English prefix; CPU float32 in place of CUDA bfloat16'}
        result['raw_generation_traces']=copy.deepcopy(traces)
        result['audio_sha256']=file_digest(source)
        processor,model=next(iter(api._CACHE.values()))
        for trace in result['raw_generation_traces']:
            trace['decoded_with_special_tokens']=processor.tokenizer.decode(trace['sequence_ids'],skip_special_tokens=False)
        report['cases'][name]=result
        write_json(output/'diagnosis.json',report)
        print(json.dumps({'case':name,'status':result['status'],'text':result['text'],
            'sequence_lengths':[t['sequence_length'] for t in traces]},ensure_ascii=True),flush=True)
        if result['status'] != 'ok':
            raise ValueError(f'Inference failed: {name}: {result.get("error")}')
    try:
        run_case('identical_english_30s_first',audio)
        run_case('identical_english_30s_repeat',audio)
        run_case('publisher_english_30s',audio,publisher=True)
        run_case('hindi_mixedcode_30s',audio,language='hi',mixed=True)
        run_case('english_mixedcode_30s',audio,language='en',mixed=True)
        for index in range(2):
            part=output/f'first-half-part-{index+1}.wav'
            pcm_slice(audio,part,index*15,(index+1)*15)
            run_case(f'english_15s_part_{index+1}',part)
    finally:
        GenerationMixin.generate=original_generate
        socket.socket.connect,socket.socket.connect_ex,socket.create_connection=saved
        write_json(output/'diagnosis.json',report)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    run(a.root.resolve(),a.output.resolve())
