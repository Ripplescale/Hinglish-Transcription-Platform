"""Check activation cannot advertise a missing or failed local model."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "tools" / "setup_speaker_runtime.py"
SPEC = importlib.util.spec_from_file_location("speaker_runtime_setup", TOOL)
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class SpeakerSetupPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.assets = self.root / "assets"
        self.logs = self.assets / "setup"
        self.logs.mkdir(parents=True)
        memory = patch.object(setup, "available_memory_bytes", return_value=8 * 1024**3)
        memory.start()
        self.addCleanup(memory.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_access_never_publishes_runtime_or_downloads(self):
        denied = subprocess.CompletedProcess([], 2, json.dumps({"ready": False, "reason": "Login required"}), "")
        with patch.object(setup.subprocess, "run", return_value=denied), patch.object(setup, "run") as run:
            code = setup.activate(Path("python.exe"), self.logs, {}, self.data, self.assets)
        self.assertEqual(code, 2)
        run.assert_not_called()
        self.assertFalse((self.data / "speaker-runtime.json").exists())

    def test_model_validation_failure_preserves_existing_runtime(self):
        self.data.mkdir()
        published = self.data / "speaker-runtime.json"
        published.write_text('{"old": true}', encoding="utf-8")
        access = {"ready": True, "revision": "a" * 40, "model_id": "pyannote/speaker-diarization-community-1"}
        approved = subprocess.CompletedProcess([], 0, json.dumps(access), "")
        model = self.assets / "lab" / "models" / "community1" / access["revision"]
        model.mkdir(parents=True)
        (model / "sttapp-model-manifest.json").write_text("{}")
        with patch.object(setup.subprocess, "run", return_value=approved), patch.object(setup, "run", return_value='{"model_validated": false}'):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                setup.activate(Path("python.exe"), self.logs, {}, self.data, self.assets)
        self.assertEqual(json.loads(published.read_text()), {"old": True})
        self.assertFalse((self.assets / "speaker-runtime").exists())

    def test_success_binds_published_config_to_validated_model_and_worker(self):
        access = {"ready": True, "revision": "b" * 40, "model_id": "pyannote/speaker-diarization-community-1"}
        approved = subprocess.CompletedProcess([], 0, json.dumps(access), "")
        model = self.assets / "lab" / "models" / "community1" / access["revision"]
        model.mkdir(parents=True)
        (model / "sttapp-model-manifest.json").write_text("{}")
        validation = {"model_validated": True, "model_revision": access["revision"], "network_disabled": True}
        def checked_probe(*args, **kwargs):
            self.assertFalse((self.data / "speaker-runtime.json").exists())
            self.assertFalse((self.assets / "speaker-runtime").exists())
            return json.dumps(validation)
        with patch.object(setup.subprocess, "run", return_value=approved), patch.object(setup, "run", side_effect=checked_probe):
            code = setup.activate(Path("python.exe"), self.logs, {}, self.data, self.assets)
        self.assertEqual(code, 0)
        config = json.loads((self.data / "speaker-runtime.json").read_text())
        self.assertEqual(config["model_revision"], access["revision"])
        self.assertEqual(config["validation"], validation)
        self.assertTrue(Path(config["worker_script"]).is_file())
        for filename, expected in config["source_hashes"].items():
            self.assertEqual(setup.sha(Path(config["worker_script"]).parent / "sttbench" / filename), expected)

    def test_low_memory_defers_model_smoke_and_publication(self):
        access = {"ready": True, "revision": "c" * 40, "model_id": "pyannote/speaker-diarization-community-1"}
        approved = subprocess.CompletedProcess([], 0, json.dumps(access), "")
        model = self.assets / "lab" / "models" / "community1" / access["revision"]
        model.mkdir(parents=True)
        manifest = model / "sttapp-model-manifest.json"
        manifest.write_text("{}")
        with patch.object(setup.subprocess, "run", return_value=approved), patch.object(setup, "run") as run, patch.object(setup, "available_memory_bytes", return_value=1024**3):
            with self.assertRaisesRegex(RuntimeError, "4 GiB"):
                setup.activate(Path("python.exe"), self.logs, {}, self.data, self.assets)
        run.assert_not_called()
        self.assertTrue(manifest.exists())
        self.assertFalse((self.data / "speaker-runtime.json").exists())

    @unittest.skipUnless(os.name == "nt", "Windows DLL lookup bootstrap")
    def test_decoder_bootstrap_excludes_current_directory_and_relative_path_entries(self):
        environment = self.assets / "env"
        (environment / "Lib" / "site-packages").mkdir(parents=True)
        ffmpeg = self.assets / "ffmpeg" / "bin"
        bootstrap = setup.install_decoder_bootstrap(environment, ffmpeg)
        original = ';.;relative;C:relative;C:\\Windows;"C:\\Program Files\\Trusted Tools";'
        with patch.dict(os.environ, {"PATH": original}), patch.object(os, "add_dll_directory") as register:
            exec(compile(bootstrap.read_text(), str(bootstrap), "exec"), {})
            entries = os.environ["PATH"].split(os.pathsep)
            self.assertEqual(entries, [str(ffmpeg), 'C:\\Windows', 'C:\\Program Files\\Trusted Tools'])
            self.assertEqual(os.environ["NoDefaultCurrentDirectoryInExePath"], "1")
            register.assert_called_once_with(str(ffmpeg))


if __name__ == "__main__":
    unittest.main()
