"""Build a private minute-by-minute listening worksheet from selected excerpts.

Original reviews remain unchanged. New machine drafts never become references.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import sys
import wave

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from sttbench.manifest import file_digest, read_json, write_json
from sttbench.runtime import transcribe
from prepare_expanded_review import inside, pcm_slice


def pcm(path):
    with wave.open(str(path), 'rb') as src:
        if (src.getnchannels(), src.getsampwidth(), src.getframerate()) != (1, 2, 16000):
            raise ValueError('Expected PCM16 mono at 16 kHz')
        raw = src.readframes(src.getnframes())
        if len(raw) != src.getnframes() * 2:
            raise ValueError('Truncated PCM')
        return raw


def prepare(source_manifest, output):
    if output.exists() or any(p.lower().startswith('onedrive') for p in output.parts):
        raise ValueError('Choose a new private output folder outside OneDrive')
    source = read_json(source_manifest)
    if len(source['clips']) != 12:
        raise ValueError('Expected the selected twelve-excerpt review')
    calls = {c['id']: c for c in source['calls']}
    verified = {}
    for call_id, call in calls.items():
        audio = inside(source_manifest.parent, call['analysis'])
        if file_digest(audio) != call['analysis_sha256']:
            raise ValueError('Decoded source changed')
        verified[call_id] = audio
    output.mkdir(parents=True)
    (output / 'audio').mkdir(); (output / 'chunks').mkdir()
    doc = {'version': 1, 'kind': 'sttbench_minute_review', 'created_at': datetime.now(timezone.utc).isoformat(),
           'source_manifest': str(source_manifest), 'source_manifest_sha256': file_digest(source_manifest),
           'selection': 'Each previously selected 28-second excerpt plus 16 seconds on each side',
           'clock_basis': 'Sample clock of the existing decoded PCM recording; not validated MP4 packet timestamps',
           'inference_policy': 'Two disjoint 30-second chunks per minute; literal outputs joined without editing',
           'reference_status': 'pending', 'human_reviewed': False, 'sections': []}
    for number, clip in enumerate(source['clips'], 1):
        call = calls[clip['call_id']]; source_audio = verified[clip['call_id']]
        core = inside(source_manifest.parent, clip['audio'])
        if file_digest(core) != clip['audio_sha256'] or clip['duration_seconds'] != 28:
            raise ValueError('Original review excerpt changed')
        start = clip['start_seconds'] - 16
        end = start + 60
        section_id = f'section-{number:02d}'
        audio = output / 'audio' / f'{section_id}.wav'
        if pcm_slice(source_audio, audio, start, end) != 60:
            raise ValueError('Minute clip length mismatch')
        raw = pcm(audio)
        if raw[16*32000:44*32000] != pcm(core):
            raise ValueError('Original 28 seconds not preserved exactly')
        chunks = []
        for part in range(2):
            chunk = output / 'chunks' / f'{section_id}-{part+1}.wav'
            pcm_slice(audio, chunk, part*30, (part+1)*30)
            chunks.append({'id': f'{section_id}-{part+1}', 'audio': chunk.relative_to(output).as_posix(),
                           'audio_sha256': file_digest(chunk), 'start_seconds': part*30, 'end_seconds': (part+1)*30})
        if b''.join(pcm(output/c['audio']) for c in chunks) != raw:
            raise ValueError('Inference chunks do not reconstruct the minute clip')
        doc['sections'].append({'id': section_id, 'number': number, 'source_clip_id': clip['id'],
            'call_id': clip['call_id'], 'call_title': call['title'], 'source_audio': str(source_audio),
            'source_audio_sha256': call['analysis_sha256'], 'source_original_sha256': call['source_sha256'],
            'start_seconds': start, 'end_seconds': end, 'duration_seconds': 60,
            'original_excerpt_inside_minute_seconds': [16,44], 'original_excerpt_sha256': clip['audio_sha256'],
            'audio': audio.relative_to(output).as_posix(), 'audio_sha256': file_digest(audio), 'chunks': chunks,
            'human_reviewed': False, 'reference_status': 'pending'})
    write_json(output/'review-manifest.json', doc)
    print(json.dumps({'sections': len(doc['sections']), 'audio_seconds': 60*len(doc['sections']), 'root': str(output)}))


def infer(root, model_id, config_path):
    manifest = root/'review-manifest.json'; data = read_json(manifest); config = read_json(config_path)
    if config.get('timestamps') is not False or config.get('backend') not in ('whisper_cpp', 'transformers'):
        raise ValueError('Use a local text-only whisper.cpp or Transformers profile')
    spec = dict(next(m for m in read_json(LAB/'models.json')['models'] if m['id'] == model_id))
    spec['artifact_path'] = config['artifact_path']
    output = root/'results'/f'{model_id}.json'; output.parent.mkdir(exist_ok=True)
    report = {'kind': 'sttbench_minute_review_drafts', 'model_id': model_id, 'model_spec': spec,
              'manifest_sha256': file_digest(manifest), 'runtime_config': config,
              'runtime_config_sha256': file_digest(config_path), 'human_reviewed': False,
              'network_attempts': [], 'chunks': {}}
    if output.exists():
        old = read_json(output)
        if any(old.get(k) != report[k] for k in ('manifest_sha256', 'model_spec', 'runtime_config')):
            raise ValueError('Cannot resume different inference inputs')
        report = old
    saved = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    def denied(*args, **kwargs):
        report['network_attempts'].append('blocked Python socket connection')
        raise OSError('Network disabled during private inference')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = denied
    try:
        for section in data['sections']:
            for chunk in section['chunks']:
                audio = inside(root, chunk['audio'])
                if file_digest(audio) != chunk['audio_sha256']:
                    raise ValueError('Audio changed')
                if report['chunks'].get(chunk['id'],{}).get('status') == 'ok':
                    continue
                effective = dict(spec)
                if section['id'] in config.get('section_decoding', {}):
                    if model_id != 'trelis':
                        raise ValueError('Section decoding overrides are only supported for Trelis')
                    effective['decoding'] = {**spec['decoding'], **config['section_decoding'][section['id']]}
                result = transcribe(effective, audio, config)
                result['audio_sha256'] = chunk['audio_sha256']
                report['chunks'][chunk['id']] = result
                write_json(output, report)
                print(json.dumps({'model':model_id,'chunk':chunk['id'],'status':result['status']}), flush=True)
                if result['status'] != 'ok':
                    raise ValueError(f"Inference failed for {chunk['id']}: {result.get('error')}")
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = saved
        write_json(output, report)
    if report['network_attempts']:
        raise ValueError('Unexpected network attempt')


def stamp(seconds):
    ms = round(seconds*1000); minutes, tail = divmod(ms,60000); sec, millis = divmod(tail,1000)
    return f'{minutes:02d}:{sec:02d}.{millis:03d}'


def build(root, output):
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from create_comparison_doc import link, table_style, label, response_line

    if output.exists() or not output.resolve().is_relative_to(root.resolve()):
        raise ValueError('Use a new document path in the private review folder')
    manifest = root/'review-manifest.json'; data = read_json(manifest)
    results = {m: read_json(root/'results'/f'{m}.json') for m in ('apex','swift')}
    for model, result in results.items():
        if result['manifest_sha256'] != file_digest(manifest) or result['network_attempts']:
            raise ValueError('Draft inputs do not match the document')
        for section in data['sections']:
            for chunk in section['chunks']:
                pred = result['chunks'][chunk['id']]
                if pred['status'] != 'ok' or pred['audio_sha256'] != chunk['audio_sha256']:
                    raise ValueError('A complete verified draft is required')

    doc = Document(); page = doc.sections[0]
    page.page_width = Inches(8.5); page.page_height = Inches(11)
    page.top_margin = Inches(.65); page.bottom_margin = Inches(.65)
    page.left_margin = Inches(.7); page.right_margin = Inches(.7); page.footer_distance = Inches(.3)
    for name in ('Normal','Title','Subtitle','Heading 1','Heading 2','Heading 3'):
        style=doc.styles[name]; style.font.name='Calibri'; style.font.size=Pt(11)
        style.font.color.rgb=RGBColor(0,0,0)
        style.paragraph_format.space_after=Pt(7); style.paragraph_format.line_spacing=1.12
        for color in style.element.xpath('.//w:color'):
            for attr in ('themeColor','themeTint','themeShade'): color.attrib.pop(qn('w:'+attr),None)
        for border in style.element.xpath('.//w:pBdr'): border.getparent().remove(border)
    doc.styles['Title'].font.size=Pt(27); doc.styles['Title'].font.bold=True
    doc.styles['Subtitle'].font.size=Pt(14)
    doc.styles['Heading 1'].font.size=Pt(19); doc.styles['Heading 2'].font.size=Pt(13)
    doc.styles['Heading 2'].paragraph_format.space_before=Pt(12)
    doc.core_properties.title='Hinglish transcript review'
    doc.core_properties.author='STT App'
    doc.core_properties.subject='Twelve one minute excerpts for listening and correction'
    footer=page.footer.paragraphs[0]; footer.alignment=WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run('Transcript review  |  Page ').font.size=Pt(9)
    field=OxmlElement('w:fldSimple'); field.set(qn('w:instr'),'PAGE'); footer._p.append(field)
    doc.add_paragraph('Hinglish transcript review','Title')
    doc.add_paragraph('Twelve one minute listening sections','Subtitle')
    doc.add_paragraph('Review the twelve new passages below, expanded to one minute each. Listen, compare the drafts, and write what you hear on the following correction page. Save after each section and leave the machine drafts unchanged. Total listening time is twelve minutes.')
    doc.add_paragraph('Keep Hindi and English as spoken, using Roman letters. Check names, numbers and units. Write [unclear] for words you cannot hear, or [no speech] for silence. Correct the full minute; the earlier short excerpt is at 00:16 to 00:44.')
    doc.add_paragraph('Audio links open local files on this computer; use Ctrl+click in Word if needed. Every clip runs from 00:00 to 01:00. Source ranges use the decoded recording clock. The clip link gives the exact listening boundaries.')
    doc.add_paragraph('Apex Q5 and Swift Q5 are unchecked machine drafts, generated locally using GPU and CPU respectively. Each combines two 30-second recognition windows without rewriting. Paragraph breaks separate those windows, not speakers.')
    doc.add_heading('Listening order',2)
    table=doc.add_table(rows=1,cols=3)
    for cell,text in zip(table.rows[0].cells,('Section','Recording','Source range')): cell.text=text
    for section in data['sections']:
        cells=table.add_row().cells
        link(cells[0].paragraphs[0],f"{section['number']:02d}",root/section['audio'])
        cells[1].text=label({'title':section['call_title']}).replace(' recording starting at',' at').replace(' 2026','')
        cells[2].text=f"{stamp(section['start_seconds'])} to {stamp(section['end_seconds'])}"
    table_style(table,[.65,3.35,3.1])

    for section in data['sections']:
        number=section['number']; call_label=label({'title':section['call_title']})
        heading=doc.add_heading(f'Section {number:02d} comparison',1)
        heading.paragraph_format.page_break_before=True
        doc.add_paragraph(call_label)
        doc.add_paragraph(f"Source {stamp(section['start_seconds'])} to {stamp(section['end_seconds'])}  |  One minute")
        p=doc.add_paragraph(); link(p,'Play this one minute clip',root/section['audio'])
        p.add_run('    '); link(p,'Open full decoded recording',Path(section['source_audio']))
        doc.add_paragraph('Listen first, then use these drafts to check what was said. Write your answer on the following correction page.')
        table=doc.add_table(rows=2,cols=2)
        for col,model in enumerate(('apex','swift')):
            table.cell(0,col).text=f'{model.title()} Q5 draft'
            cell=table.cell(1,col); cell.text=''
            for part,chunk in enumerate(section['chunks']):
                text=results[model]['chunks'][chunk['id']]['text']
                p=cell.paragraphs[0] if part==0 else cell.add_paragraph()
                p.add_run(text if text.strip() else '[No text returned by this model]')
        table_style(table,[3.55,3.55])
        for cell in table.rows[1].cells:
            for p in cell.paragraphs:
                p.paragraph_format.space_after=Pt(9)
                for run in p.runs: run.font.size=Pt(11)
        note=doc.add_paragraph('The audio is the authority. Draft wording, punctuation, repetitions and omissions have been preserved.')
        note.paragraph_format.space_before=Pt(8)
        heading=doc.add_heading(f'Section {number:02d} your transcript',1)
        heading.paragraph_format.page_break_before=True
        doc.add_paragraph(call_label)
        p=doc.add_paragraph(); p.add_run(f"Source {stamp(section['start_seconds'])} to {stamp(section['end_seconds'])}    ")
        link(p,'Replay this clip',root/section['audio'])
        doc.add_heading('What was actually said',2)
        doc.add_paragraph('Type your corrected transcript here. Keep the full minute in order.')
        for _ in range(11): response_line(doc)
        doc.add_heading('Names numbers and unclear words',2)
        for _ in range(2): response_line(doc)
        doc.add_paragraph('Closer to the audio   [ ] Apex   [ ] Swift   [ ] Neither   [ ] Unsure')
        doc.add_paragraph('Review completed   [ ] I listened to the full minute and checked my transcript')
    doc.save(output)
    write_json(root/'document-provenance.json',{'document':output.name,'sha256':file_digest(output),
        'manifest_sha256':file_digest(manifest),'drafts_sha256':{m:file_digest(root/'results'/f'{m}.json') for m in results},
        'sections':12,'expected_pages':25,'human_reviewed':False,'audio_seconds':720,
        'model_text_preserved':True,'corrected_references_created':False})
    print(str(output))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare'); p.add_argument('--source',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('infer'); p.add_argument('--root',type=Path,required=True);p.add_argument('--model',choices=('apex','trelis'),required=True);p.add_argument('--config',type=Path,required=True)
    p=sub.add_parser('build'); p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=parser.parse_args()
    if a.command=='prepare': prepare(a.source.resolve(),a.output.resolve())
    elif a.command=='infer': infer(a.root.resolve(),a.model,a.config.resolve())
    else: build(a.root.resolve(),a.output.resolve())
