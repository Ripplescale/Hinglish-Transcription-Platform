"""Fictional IPC fixtures for live Trelis/fallback, with no ASR or devices."""
import argparse
import json
from pathlib import Path
from prepare_native_dual_pass import prepare, write_json


def prepare_live(python, output):
    fixture = prepare(python, output)
    output = Path(fixture['data_root'])
    config = json.loads((output / 'runtime.json').read_text(encoding='utf-8'))
    export = output / 'synthetic-export'
    export.mkdir()
    write_json(export / 'conversion-provenance.json', {'synthetic_only': True, 'not_a_model': True})
    config['models']['trelis'].update(backend='openvino', device='GPU', export_path=str(export))
    config['synthetic_live_holds'] = True
    write_json(output / 'runtime.json', config)
    fixture['live_workflow_fixture'] = True
    fixture['live_session'] = fixture['unfinished_session']
    write_json(output / 'fixture.json', fixture)
    return fixture


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_live(args.python, args.output)))
