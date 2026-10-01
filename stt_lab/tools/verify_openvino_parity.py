"""Compare the integrated offline adapter with a hash-bound saved CPU benchmark."""
import argparse
import copy
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sttbench.manifest import file_digest, read_json, write_json
from sttbench.runtime.api import transcribe, warmup


def verify(manifest_path, baseline_path, config_path, output):
    manifest, baseline, config = map(read_json, (manifest_path, baseline_path, config_path))
    if baseline['manifest_sha256'] != file_digest(manifest_path):
        raise ValueError('Baseline belongs to another audio/reference manifest')
    runtime = config['models']['trelis']
    if runtime['backend'] != 'openvino':
        raise ValueError('An integrated OpenVINO candidate is required')
    registry = read_json(Path(config['registry_path']))
    template = next(row for row in registry['models'] if row['id'] == 'trelis')
    def blocked(*args, **kwargs):
        raise OSError('Network blocked during local parity validation')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = blocked
    report = {'state':'running','manifest_sha256':file_digest(manifest_path),
              'baseline_sha256':file_digest(baseline_path), 'runtime_sha256':file_digest(config_path),
              'chunks':{},'model_spec':template,'runtime':runtime,
              'scope':'Exact raw text parity on supplied saved excerpts; not live or unseen-call accuracy.'}
    report['warmup'] = warmup(template, runtime)
    write_json(output, report)
    if report['warmup']['status'] != 'ok':
        raise ValueError(report['warmup'].get('error'))
    for chunk in manifest['chunks']:
        audio = Path(chunk['path'])
        if file_digest(audio) != chunk['audio_sha256']:
            raise ValueError('Audio hash changed')
        spec = copy.deepcopy(template)
        spec['decoding'].update(chunk['decoding'])
        result = transcribe(spec, audio, runtime)
        prior = baseline['chunks'][chunk['id']]
        matched = result['status'] == prior['status'] == 'ok' and result['text'] == prior['text']
        report['chunks'][chunk['id']] = {'result':result,'exact_raw_text_match':matched,
                                       'audio_sha256':chunk['audio_sha256']}
        write_json(output, report)
        print(json.dumps({'completed':len(report['chunks']), 'total':len(manifest['chunks']),
                          'exact_raw_text_match':matched, 'status':result['status']}), flush=True)
        if result['status'] != 'ok':
            raise ValueError(result.get('error'))
    report.update(state='complete', passed=all(row['exact_raw_text_match'] for row in report['chunks'].values()),
                  audio_seconds=sum(row['duration_seconds'] for row in manifest['chunks']))
    write_json(output, report)
    if not report['passed']:
        raise ValueError('Integrated outputs differ; inspect the saved comparison before activating')
    print(json.dumps({'state':'complete','passed':True,'chunks':len(report['chunks'])}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','baseline','config','output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    verify(args.manifest, args.baseline, args.config, args.output)
