"""Synthetic binding, script and coverage checks for fresh-review intake."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import score_boundary_corrections as subject
from sttbench.manifest import file_digest,read_json,write_json
from sttbench.normalization import basic_tokens,word_errors


class BoundaryCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='stt-boundary-review-test-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'pilot';self.root.mkdir()
        self.source=self.root.parent/'review.json'
        samples=[]
        for sid in ('fresh-1','fresh-2','fresh-3'):
            audio=self.root/f'{sid}.wav'
            with wave.open(str(audio),'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\x01\x00'*16000)
            sample={'id':sid,'group':'fresh','call_title':'Synthetic call','source_start_seconds':40,
                'duration_seconds':1,'audio':audio.name,'audio_sha256':file_digest(audio),'partitions':{}}
            for size in (15,20,30):
                sample['partitions'][f'{size}:aligned']=[{'id':f'{sid}:{size}:aligned:1','audio':audio.name,
                    'audio_sha256':sample['audio_sha256'],'start_seconds':0,'end_seconds':1}]
            samples.append(sample)
        manifest={'kind':'boundary_pilot','version':1,'profiles':subject.PROFILES,'samples':samples}
        write_json(self.root/'manifest.json',manifest)
        summary={'manifest_sha256':file_digest(self.root/'manifest.json'),'models':{},'samples':[]}
        for model,sizes in subject.PROFILES.items():
            result={'kind':'boundary_pilot_results','state':'complete','model_id':model,
                'manifest_sha256':summary['manifest_sha256'],'network_attempts':[],
                'model_spec':{'id':model},'runtime_config':{},'chunks':{}}
            for sample in samples:
                for size in sizes:
                    part=sample['partitions'][f'{size}:aligned'][0]
                    result['chunks'][part['id']]={'status':'ok','audio_sha256':sample['audio_sha256'],
                        'sample_id':sample['id'],'size':size,'condition':'aligned',
                        'text':'नब्बे paisa Vivek' if model=='trelis' else 'nabbe paisa Vivek'}
            write_json(self.root/'results'/f'{model}.json',result)
            summary['models'][model]={'state':'complete','result_sha256':file_digest(self.root/'results'/f'{model}.json')}
        for sample in samples:
            summary['samples'].append({'id':sample['id'],'drafts':{
                f'{m}:{size}:aligned':{'complete':True,'text':'नब्बे paisa Vivek' if m=='trelis' else 'nabbe paisa Vivek'}
                for m,sizes in subject.PROFILES.items() for size in sizes}})
        write_json(self.root/'summary.json',summary)
        review={'kind':'boundary_pilot_human_review','version':1,'binding':{},'reviews':{
            s['id']:{'reference':'नब्बे paisa Vivek','notes':'Names incorrect/missing- Vivek','reviewed':True,
                'draft_checks':{'trelis:15':'looks_complete'},'reference_provenance':None} for s in samples}}
        write_json(self.source,review);self.rebind()

    def rebind(self):
        manifest=read_json(self.root/'manifest.json');summary=read_json(self.root/'summary.json')
        manifest_hash=file_digest(self.root/'manifest.json')
        hashes={}
        for model in subject.PROFILES:
            path=self.root/'results'/f'{model}.json';value=read_json(path)
            value['manifest_sha256']=manifest_hash;write_json(path,value);hashes[model]=file_digest(path)
            summary['models'][model]['result_sha256']=hashes[model]
        summary['manifest_sha256']=manifest_hash;write_json(self.root/'summary.json',summary)
        review=read_json(self.source)
        review['binding']={'manifest_sha256':manifest_hash,'summary_sha256':file_digest(self.root/'summary.json'),
            'audio_sha256':{s['id']:s['audio_sha256'] for s in manifest['samples'] if s['group']=='fresh'},'model_results_sha256':hashes}
        write_json(self.source,review)

    def test_native_references_and_user_assessments_are_preserved(self):
        a=subject.build_analysis(self.root,self.source)
        self.assertEqual(a['models']['apex']['sample_ids'],[])
        self.assertIsNone(a['models']['apex']['rows'][0]['aggregate']['wer'])
        self.assertEqual(a['models']['trelis']['rows'][0]['aggregate']['wer'],0)
        self.assertEqual(a['samples'][0]['reference'],'नब्बे paisa Vivek')
        self.assertIsNone(a['samples'][0]['reference_provenance'])
        self.assertEqual(a['samples'][0]['draft_checks'],{'trelis:15':'looks_complete'})
        self.assertEqual(a['samples'][0]['reported_name_targets'][0]['literal_counts']['apex:15'],1)

    def test_foreign_binding_and_unknown_profile_are_rejected(self):
        review=read_json(self.source);review['binding']['manifest_sha256']='wrong';write_json(self.source,review)
        with self.assertRaisesRegex(ValueError,'exact pilot'):subject.validate(self.root,self.source)
        self.rebind();review=read_json(self.source);review['reviews']['fresh-1']['draft_checks']['trelis:30']='looks_complete'
        write_json(self.source,review)
        with self.assertRaisesRegex(ValueError,'assessment'):subject.validate(self.root,self.source)

    def test_edited_summary_cannot_replace_raw_outputs_even_with_new_hash(self):
        summary=read_json(self.root/'summary.json');summary['samples'][0]['drafts']['trelis:15:aligned']['text']='rewritten'
        write_json(self.root/'summary.json',summary);self.rebind()
        with self.assertRaisesRegex(ValueError,'raw recognition'):subject.validate(self.root,self.source)

    def test_partition_coverage_is_checked_beyond_hashes(self):
        manifest=read_json(self.root/'manifest.json')
        manifest['samples'][0]['partitions']['15:aligned'][0]['start_seconds']=.5
        write_json(self.root/'manifest.json',manifest);self.rebind()
        with self.assertRaisesRegex(ValueError,'time/length'):subject.validate(self.root,self.source)

    def test_unreviewed_text_is_not_admitted_as_gold(self):
        review=read_json(self.source);review['reviews']['fresh-1']['reviewed']=False
        write_json(self.source,review)
        a=subject.build_analysis(self.root,self.source)
        self.assertIsNone(a['samples'][0]['reference'])
        self.assertEqual(a['samples'][0]['unreviewed_reference_draft'],'नब्बे paisa Vivek')
        self.assertEqual(len(a['models']['trelis']['sample_ids']),2)

    def test_alignment_counts_match_strict_metric_including_repetition(self):
        for expected,actual in [('ninety paisa','ninety five'),('a b c a','a a b a'),('नब्बे paisa','ninety paisa'),('a b',''),('','a a')]:
            ref,hyp=basic_tokens(expected),basic_tokens(actual)
            alignment=subject.token_alignment(ref,hyp);metric=word_errors(ref,hyp)
            for op,key in [('substitute','substitutions'),('delete','deletions'),('insert','insertions')]:
                self.assertEqual(sum(x['operation']==op for x in alignment),metric[key])

    def test_existing_revision_is_never_overwritten(self):
        revision=self.root/'existing';revision.mkdir();(revision/'keep.txt').write_text('original')
        with self.assertRaisesRegex(ValueError,'new revision'):subject.ingest(self.root,self.source,revision)
        self.assertEqual((revision/'keep.txt').read_text(),'original')

    def test_combined_panel_uses_only_aligned_prior_checked_references(self):
        manifest=read_json(self.root/'manifest.json')
        prior=copy.deepcopy(manifest['samples'][0]);prior['id']='prior';prior['group']='checked'
        for key,parts in prior['partitions'].items():parts[0]['id']=f'prior:{key}:1'
        manifest['samples'].append(prior);write_json(self.root/'manifest.json',manifest)
        for model,sizes in subject.PROFILES.items():
            path=self.root/'results'/f'{model}.json';result=read_json(path)
            for size in sizes:
                copied=copy.deepcopy(result['chunks'][f'fresh-1:{size}:aligned:1']);copied['sample_id']='prior'
                result['chunks'][f'prior:{size}:aligned:1']=copied
            write_json(path,result)
        summary=read_json(self.root/'summary.json')
        prior_summary=copy.deepcopy(summary['samples'][0])
        prior_summary.update(id='prior',group='checked',eligible_models=['trelis'],reference='नब्बे paisa Vivek')
        for size in (15,20):
            prior_summary['drafts'][f'trelis:{size}:aligned']['metrics']=word_errors(basic_tokens(prior_summary['reference']),basic_tokens(prior_summary['reference']))
            prior_summary['drafts'][f'trelis:{size}:shift5']={'text':'must not pool this different condition','metrics':{'errors':999}}
        summary['samples'].append(prior_summary);write_json(self.root/'summary.json',summary);self.rebind()
        a=subject.build_analysis(self.root,self.source)
        combined=a['models']['trelis']['combined_aligned']
        self.assertEqual(combined['prior_sample_ids'],['prior'])
        self.assertEqual(combined['rows'][0]['aggregate']['reference_words'],12)
        self.assertEqual(combined['rows'][0]['aggregate']['errors'],0)


if __name__=='__main__':unittest.main()
