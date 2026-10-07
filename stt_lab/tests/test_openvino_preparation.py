"""Preparation and migration use synthetic files, never inference or production."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
import install_local_worker as installer
import prepare_openvino_runtime as preparation
from sttbench.manifest import digest, file_digest, read_json, write_json


class OpenVINOPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='openvino-prepare-test-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.data=self.root/'data';self.data.mkdir()
        self.lab=self.root/'lab'
        (self.lab/'sttbench/runtime').mkdir(parents=True)
        (self.lab/'sttbench/local_worker.py').write_text('# synthetic new worker\n',encoding='utf-8')
        (self.lab/'sttbench/runtime/api.py').write_text('# synthetic adapter\n',encoding='utf-8')
        self.models=[]
        for model,repo in (('apex','Oriserve/Whisper-Hindi2Hinglish-Apex'),('trelis','Trelis/whisper-hinglish-preview')):
            self.models.append({'id':model,'model_id':model,'repo_id':repo,'family':'whisper',
                                'source_revision':preparation.REVISION if model=='trelis' else 'b'*40,
                                'decoding':{'language':'hi','mixed_code':True} if model=='trelis' else {'language':'en'}})
        write_json(self.lab/'models.json',{'models':self.models})
        self.model=self.root/'model';self.model.mkdir()
        configuration={'model_type':'whisper','vocab_size':51867,'decoder_start_token_id':50258,'max_target_positions':448}
        write_json(self.model/'config.json',configuration)
        write_json(self.model/'generation_config.json',{'num_beams':1,'suppress_tokens':[7]})
        write_json(self.model/'tokenizer.json',{})
        write_json(self.model/'preprocessor_config.json',{})
        (self.model/'model.safetensors').write_bytes(b'synthetic model, not executable')
        source_manifest={key:self.models[1][key] for key in ('model_id','repo_id','source_revision')}
        source_manifest['files']=[{'path':file.name,'size':file.stat().st_size,'sha256':file_digest(file)}
                                  for file in self.model.iterdir()]
        write_json(self.model/'artifact-manifest.json',source_manifest)
        self.export=self.root/'export';self.export.mkdir()
        for name in ('config.json','generation_config.json','preprocessor_config.json'):
            (self.export/name).write_bytes((self.model/name).read_bytes())
        for component in ('encoder','decoder'):
            for extension in ('xml','bin'):
                (self.export/f'openvino_{component}_model.{extension}').write_bytes(b'synthetic IR')
        export_manifest={'source':str(self.model),'vocab_size':51867,'load_in_8bit':False,
                         'files':{file.name:{'size':file.stat().st_size,'sha256':file_digest(file)}
                                  for file in self.export.iterdir()}}
        write_json(self.export/'export-complete.json',export_manifest)
        self.python=self.data/'lab/venvs/openvino-py312/Scripts/python.exe'
        self.python.parent.mkdir(parents=True)
        self.python.write_bytes(b'synthetic executable - subprocess is mocked')
        self.current=self.runtime('old-worker',threads=4)
        write_json(self.data/'runtime.json',self.current)
        self.active_bytes=(self.data/'runtime.json').read_bytes()
        self.patches=[patch.object(installer,'LAB',self.lab),patch.object(preparation,'LAB',self.lab),
                      patch.object(preparation,'_inspect_environment',return_value={'packages':dict(preparation.PACKAGES)})]
        for patched in self.patches:
            patched.start();self.addCleanup(patched.stop)

    def runtime(self,name,threads):
        folder=self.data/'runtime'/name
        (folder/'sttbench').mkdir(parents=True)
        (folder/'worker.py').write_text('# synthetic entry point',encoding='utf-8')
        (folder/'sttbench/local_worker.py').write_text('# '+name,encoding='utf-8')
        write_json(folder/'models.json',{'models':self.models})
        return {'version':1,'python_executable':str(self.python),'worker_script':str(folder/'worker.py'),
                'registry_path':str(folder/'models.json'),'worker_source_id':name,
                'models':{'apex':{'backend':'whisper_cpp','device':'vulkan','artifact_path':str(self.model)},
                          'trelis':{'backend':'transformers','device':'cpu','dtype':'float32',
                                    'threads':threads,'artifact_path':str(self.model)}},
                'summaries_enabled':False,'network_inference':False,'preserve_top_level':'unchanged'}

    def job(self,name,runtime=None,*,identity=True):
        job=self.data/'jobs'/name;job.mkdir(parents=True)
        session=self.data/'recordings'/name
        write_json(session/'session.json',{'id':name})
        request={'job_id':name,'session_dir':str(session),'profile':'trelis-20','language_mode':'hinglish'}
        write_json(job/'request.json',request)
        if identity:
            runtime=runtime or self.current
            spec,model_runtime=installer._job_model(runtime,request)
            info={'request_sha256':file_digest(job/'request.json'),'session_sha256':file_digest(session/'session.json'),
                  'spec':spec,'runtime':model_runtime,'worker_sha256':installer._worker_hash(runtime)}
            write_json(job/'status.json',{'identity':info,'identity_sha256':digest(info),'state':'stopped',
                                          'segments':[{'text':'fictional prior output'}],'cursors':{'microphone':20}})
        else:
            write_json(job/'status.json',{'state':'pending','segments':[]})
        return job

    def prepare(self):
        return preparation.prepare(self.data,self.export,self.python)

    def test_candidate_preparation_does_not_activate_or_touch_jobs(self):
        job=self.job('existing')
        checkpoint=(job/'status.json').read_bytes()
        report=self.prepare()
        self.assertFalse(report['activated'])
        self.assertEqual((self.data/'runtime.json').read_bytes(),self.active_bytes)
        self.assertEqual((job/'status.json').read_bytes(),checkpoint)
        self.assertFalse((job/'runtime-snapshot.json').exists())
        candidate=read_json(Path(report['candidate_runtime']))
        self.assertEqual(candidate['models']['apex'],self.current['models']['apex'])
        self.assertEqual(candidate['models']['trelis']['artifact_path'],str(self.model))
        self.assertEqual(candidate['models']['trelis']['backend'],'openvino')
        self.assertEqual(candidate['python_executable'],str(self.python.resolve()))
        self.assertEqual(candidate['preserve_top_level'],'unchanged')
        target=Path(report['export_path'])
        self.assertTrue(target.is_relative_to(self.data/'lab/converted/openvino/trelis'))
        self.assertEqual(file_digest(target/'openvino_decoder_model.bin'),file_digest(self.export/'openvino_decoder_model.bin'))
        receipt=read_json(target/'conversion-provenance.json')
        self.assertEqual(receipt['source_manifest_sha256'],file_digest(self.model/'artifact-manifest.json'))
        self.assertEqual(receipt['converter']['packages'],preparation.PACKAGES)
        self.assertFalse((self.export/'conversion-provenance.json').exists())
        repeated=self.prepare()
        self.assertEqual(repeated['candidate_runtime'],report['candidate_runtime'])

    def test_activation_snapshots_old_runtime_before_replacement_and_preserves_checkpoints(self):
        job=self.job('legacy')
        new_job=self.job('not-started',identity=False)
        checkpoint=(job/'status.json').read_bytes()
        report=self.prepare()
        result=installer.activate_runtime(self.data,Path(report['candidate_runtime']))
        self.assertTrue(result['activated'])
        self.assertEqual(result['snapshots_created'],2)
        for directory in (job,new_job):
            self.assertEqual(read_json(directory/'runtime-snapshot.json'),self.current)
        self.assertEqual((job/'status.json').read_bytes(),checkpoint)
        self.assertEqual(read_json(self.data/'runtime.json')['models']['trelis']['backend'],'openvino')
        backup=list((self.data/'runtime').glob('previous-*.json'))
        self.assertEqual(len(backup),1)
        self.assertEqual(read_json(backup[0]),self.current)

    def test_older_checkpoint_matches_previous_runtime_and_worker_hash(self):
        older=self.runtime('older-worker',threads=2)
        write_json(self.data/'runtime/previous-older.json',older)
        job=self.job('older',older)
        report=self.prepare()
        installer.activate_runtime(self.data,Path(report['candidate_runtime']))
        snapshot=read_json(job/'runtime-snapshot.json')
        self.assertEqual(snapshot,older)
        self.assertEqual(snapshot['models']['trelis']['threads'],2)

    def test_gate_identity_prevents_matching_a_changed_detection_configuration(self):
        gate_worker=Path(self.current['worker_script']).parent/'sttbench/speech_gate.py'
        gate_worker.write_text('# original gate',encoding='utf-8')
        self.current['speech_gate']={'threshold':0.15}
        write_json(self.data/'runtime.json',self.current)
        job=self.job('gate-bound')
        status=read_json(job/'status.json')
        status['identity'].update(speech_gate=copy.deepcopy(self.current['speech_gate']),
                                  speech_gate_worker_sha256=file_digest(gate_worker))
        status['identity_sha256']=digest(status['identity'])
        write_json(job/'status.json',status)
        original_runtime=copy.deepcopy(self.current)
        self.current['speech_gate']['threshold']=0.1
        write_json(self.data/'runtime.json',self.current)
        report=self.prepare()
        with self.assertRaisesRegex(ValueError,'No verified historical runtime'):
            installer.activate_runtime(self.data,Path(report['candidate_runtime']))
        write_json(self.data/'runtime/previous-original-gate.json',original_runtime)
        installer.activate_runtime(self.data,Path(report['candidate_runtime']))
        self.assertEqual(read_json(job/'runtime-snapshot.json'),original_runtime)

    def test_unmatched_checkpoint_prevents_all_migration_and_activation(self):
        good=self.job('a-good')
        unknown=self.runtime('unrecorded-worker',threads=8)
        bad=self.job('b-bad',unknown)
        checkpoint=(bad/'status.json').read_bytes()
        report=self.prepare()
        with self.assertRaisesRegex(ValueError,'No verified historical runtime'):
            installer.activate_runtime(self.data,Path(report['candidate_runtime']))
        self.assertEqual((self.data/'runtime.json').read_bytes(),self.active_bytes)
        self.assertEqual((bad/'status.json').read_bytes(),checkpoint)
        self.assertFalse((good/'runtime-snapshot.json').exists())
        self.assertFalse((bad/'runtime-snapshot.json').exists())

    def test_existing_snapshot_is_never_overwritten(self):
        job=self.job('already-frozen')
        snapshot=job/'runtime-snapshot.json'
        snapshot.write_bytes(b'{"retained":"exact original bytes"}\n')
        original=snapshot.read_bytes()
        report=self.prepare()
        result=installer.activate_runtime(self.data,Path(report['candidate_runtime']))
        self.assertEqual(result['snapshots_retained'],1)
        self.assertEqual(snapshot.read_bytes(),original)
        with self.assertRaises(FileExistsError):installer._write_new_json(snapshot,{'replacement':True})
        self.assertEqual(snapshot.read_bytes(),original)

    def test_source_export_tampering_does_not_activate(self):
        (self.export/'openvino_decoder_model.bin').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):
            self.prepare()
        self.assertEqual((self.data/'runtime.json').read_bytes(),self.active_bytes)
        self.assertFalse(any((self.data/'runtime').glob('candidate-openvino-*.json')))

    def test_worker_hash_mismatch_rejects_falsely_matching_model_config(self):
        job=self.job('changed-code')
        (Path(self.current['worker_script']).parent/'sttbench/local_worker.py').write_text('changed code')
        report=self.prepare()
        with self.assertRaisesRegex(ValueError,'No verified historical runtime'):
            installer.activate_runtime(self.data,Path(report['candidate_runtime']))
        self.assertFalse((job/'runtime-snapshot.json').exists())
        self.assertEqual((self.data/'runtime.json').read_bytes(),self.active_bytes)

    def test_unverifiable_output_or_changed_request_blocks_activation(self):
        job=self.job('changed')
        report=self.prepare()
        request=read_json(job/'request.json');request['language_mode']='english'
        write_json(job/'request.json',request)
        with self.assertRaisesRegex(ValueError,'request changed'):
            installer.activate_runtime(self.data,Path(report['candidate_runtime']))
        write_json(job/'status.json',{'segments':[{'text':'unverifiable output'}]})
        with self.assertRaisesRegex(ValueError,'output without a verifiable identity'):
            installer.activate_runtime(self.data,Path(report['candidate_runtime']))
        self.assertEqual((self.data/'runtime.json').read_bytes(),self.active_bytes)

    def test_legacy_installer_nonactivating_mode_preserves_active_runtime(self):
        study=self.root/'study'
        for model in ('apex','trelis'):
            write_json(study/'configs'/f'{model}.json',self.current['models'][model])
        report=installer.install(self.data,study,self.python,activate=False)
        self.assertFalse(report['activated'])
        self.assertTrue(Path(report['candidate_runtime']).is_file())
        self.assertEqual((self.data/'runtime.json').read_bytes(),self.active_bytes)
        self.assertFalse((self.data/'vaults').exists())

    def test_worker_update_stages_gate_and_profiles_without_changing_models_or_history(self):
        job=self.job('existing-update')
        checkpoint=(job/'status.json').read_bytes()
        report=installer.update_existing(self.data)
        candidate=read_json(Path(report['candidate_runtime']))
        self.assertFalse(report['activated'])
        self.assertEqual(candidate['models'],self.current['models'])
        self.assertEqual(candidate['python_executable'],self.current['python_executable'])
        self.assertEqual(candidate['preserve_top_level'],'unchanged')
        self.assertEqual(candidate['speech_gate']['threshold'],0.15)
        self.assertEqual((self.data/'runtime.json').read_bytes(),self.active_bytes)
        self.assertEqual((job/'status.json').read_bytes(),checkpoint)
        self.assertFalse((job/'runtime-snapshot.json').exists())


class SpeechGateProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='speech-gate-install-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_missing_model_remains_explicit_without_suppressing_input(self):
        with patch.object(installer.urllib.request,'urlopen',side_effect=AssertionError('offline setup must not access network')):
            configuration=installer.install_speech_gate(self.root)
        self.assertEqual(configuration['model_sha256'],installer.SILERO_SHA256)
        self.assertEqual(configuration['model_version'],'6.2.3')
        self.assertEqual(configuration['threshold'],0.15)
        self.assertFalse(Path(configuration['model_path']).exists())

    def test_copy_is_verified_durable_and_reused(self):
        source=self.root/'provided.onnx';source.write_bytes(b'synthetic gate asset')
        with patch.object(installer,'SILERO_SHA256',file_digest(source)):
            configuration=installer.install_speech_gate(self.root,source)
            target=Path(configuration['model_path'])
            self.assertTrue(target.is_relative_to(self.root/'lab/models/silero'))
            self.assertEqual(target.read_bytes(),source.read_bytes())
            original=target.stat().st_mtime_ns
            self.assertEqual(installer.install_speech_gate(self.root,source),configuration)
            self.assertEqual(target.stat().st_mtime_ns,original)
            with patch.object(installer,'_download_silero_model',side_effect=AssertionError('verified asset must be reused')):
                self.assertEqual(installer.install_speech_gate(self.root,download=True),configuration)
            target.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'differs from the pinned'):
                installer.install_speech_gate(self.root)

    def test_unverified_source_is_rejected_without_copying(self):
        source=self.root/'provided.onnx';source.write_bytes(b'unverified')
        with self.assertRaisesRegex(ValueError,'pinned SHA-256'):
            installer.install_speech_gate(self.root,source)
        self.assertFalse((self.root/'lab/models').exists())

    def wheel(self, model=b'synthetic verified ONNX member'):
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            archive.writestr(installer.SILERO_MODEL_MEMBER,model)
            # Model setup reads exactly one member and never imports wheel code.
            archive.writestr('silero_vad/__init__.py','raise RuntimeError("must not execute package")')
        return buffer.getvalue(),model

    def response(self, data, url=installer.SILERO_WHEEL_URL):
        class Response(io.BytesIO):
            def geturl(self):return url
        return Response(data)

    def test_explicit_setup_download_verifies_wheel_and_model_then_reuses_offline(self):
        wheel,model=self.wheel()
        with patch.object(installer,'SILERO_WHEEL_SHA256',hashlib.sha256(wheel).hexdigest()), \
             patch.object(installer,'SILERO_SHA256',hashlib.sha256(model).hexdigest()), \
             patch.object(installer.urllib.request,'urlopen',return_value=self.response(wheel)) as download:
            configuration=installer.install_speech_gate(self.root,download=True)
            self.assertEqual(Path(configuration['model_path']).read_bytes(),model)
            request=download.call_args.args[0]
            self.assertEqual(request.full_url,installer.SILERO_WHEEL_URL)
            self.assertEqual(download.call_args.kwargs['timeout'],30)
            self.assertEqual(installer.install_speech_gate(self.root),configuration)
            self.assertEqual(download.call_count,1)

    def test_download_rejects_wrong_wheel_model_redirect_and_oversize_before_publication(self):
        wheel,model=self.wheel()
        valid_wheel_sha=hashlib.sha256(wheel).hexdigest()
        cases=[('wheel',wheel,installer.SILERO_WHEEL_URL,'b'*64,hashlib.sha256(model).hexdigest(),16*1024*1024),
               ('model',wheel,installer.SILERO_WHEEL_URL,valid_wheel_sha,'b'*64,16*1024*1024),
               ('redirect',wheel,'http://files.pythonhosted.org/insecure.whl',valid_wheel_sha,hashlib.sha256(model).hexdigest(),16*1024*1024),
               ('size',wheel,installer.SILERO_WHEEL_URL,valid_wheel_sha,hashlib.sha256(model).hexdigest(),16)]
        for name,data,url,wheel_sha,model_sha,limit in cases:
            with self.subTest(name=name), \
                 patch.object(installer,'SILERO_WHEEL_SHA256',wheel_sha), \
                 patch.object(installer,'SILERO_SHA256',model_sha), \
                 patch.object(installer,'SILERO_MAX_WHEEL_BYTES',limit), \
                 patch.object(installer.urllib.request,'urlopen',return_value=self.response(data,url)):
                with self.assertRaises(ValueError):
                    installer.install_speech_gate(self.root,download=True)
                self.assertFalse((self.root/'lab/models').exists())

    def test_network_failure_leaves_existing_runtime_unchanged_and_no_model_published(self):
        active=self.root/'runtime.json'
        active.write_bytes(b'{"original":"untouched"}')
        with patch.object(installer.urllib.request,'urlopen',side_effect=OSError('offline')):
            with self.assertRaisesRegex(OSError,'offline'):
                installer.install_speech_gate(self.root,download=True)
        self.assertEqual(active.read_bytes(),b'{"original":"untouched"}')
        self.assertFalse((self.root/'lab/models').exists())


if __name__=='__main__':unittest.main()
