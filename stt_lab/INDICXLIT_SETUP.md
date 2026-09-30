# Reproduce the local IndicXlit Roman display renderer

**Retired on 22 September 2026.** The user chose original-script Trelis output.
This document describes the previous experiment only. Setup scripts and the
renderer are archived under private `benchmark/retired-20260922/source`;
dedicated weights and the environment have been removed. Do not reinstate
transliteration in the active Apex/Trelis review workflow.

This experiment transliterates only Devanagari word spans. It preserves English,
numbers, punctuation, whitespace, repetitions, and the raw ASR results. It never
reads corrected references and performs no spelling correction or translation.
Scores using this view measure the **ASR plus Roman renderer** pipeline.

## Pinned sources and runtime

| Component | Exact retained version |
| --- | --- |
| [Official AI4Bharat code](https://github.com/AI4Bharat/IndicXlit/tree/802803ee0afc7a8703815bb3eb79f99f061797a1) | `802803ee0afc7a8703815bb3eb79f99f061797a1`, MIT |
| [Official Indic-to-Roman model assets](https://huggingface.co/ai4bharat/IndicXlit/tree/e7049fe725b11c0a187d42edeb2f56aa1ffee5fd/indicxlit-indic-en-v1.0) | `e7049fe725b11c0a187d42edeb2f56aa1ffee5fd`, MIT |
| [fairseq-fixed published wheel](https://pypi.org/project/fairseq-fixed/0.12.3.1/) | `0.12.3.1`, CPython 3.12 / Windows x64, MIT |
| Python | CPython `3.12.14`, x64 |
| PyTorch / torchaudio | `2.8.0+cpu` / `2.8.0+cpu` |
| NumPy / Hydra / OmegaConf | `1.26.4` / `1.3.2` / `2.3.0` |

The runtime is the [JackismyShephard fairseq fork](https://github.com/JackismyShephard/fairseq),
**not an unmodified official fairseq release**. Its reviewed wheel is
`fairseq_fixed-0.12.3.1-cp312-cp312-win_amd64.whl`, SHA256
`3490abbbdfd0693b3b35bd81ab05dfc451b1b47f4258680e343a4a3e1f4ab9d5`.
The wheel's MIT license and import entry points were inspected before execution.
The renderer verifies the installed fairseq/fairseq_cli source and binary files
against that wheel; the successful experiment verified 1,425 files. It records
all installed package versions, Python version, wrapper hash, and asset hashes
in each output. The dependency lock is `indicxlit-requirements.txt`.

## Setup outside OneDrive

Run from the repository root in PowerShell with a 64-bit Python 3.12 executable
available as `py -3.12`. Choose an explicit private local path if the desktop
host redirects `LOCALAPPDATA`. Do not reuse an ASR environment.

```powershell
$labRoot = Join-Path $env:LOCALAPPDATA 'STTApp/lab'
$assetRoot = Join-Path $labRoot 'models/indicxlit/e7049fe725b11c0a187d42edeb2f56aa1ffee5fd'
$venvRoot = Join-Path $labRoot 'venvs/indicxlit'
py -3.12 -B stt_lab/tools/prepare_indicxlit.py --root $assetRoot --rescore
py -3.12 -m venv $venvRoot
$rendererPython = Join-Path $venvRoot 'Scripts/python.exe'
& $rendererPython -m pip install -r stt_lab/indicxlit-requirements.txt
& $rendererPython -m pip install --index-url https://download.pytorch.org/whl/cpu 'torch==2.8.0+cpu' 'torchaudio==2.8.0+cpu'
$reviewedWheel = Join-Path $assetRoot 'runtime/fairseq_fixed-0.12.3.1-cp312-cp312-win_amd64.whl'
& $rendererPython -m pip install --no-deps $reviewedWheel
& $rendererPython -m pip check
$assetsManifest = Join-Path $assetRoot 'renderer-assets-default.json'
& $rendererPython -B stt_lab/tools/romanize_screening.py --assets $assetsManifest --rescore --smoke
```

The asset helper fetches public files only, pins GitHub/Hugging Face revisions,
checks exact file sizes and SHA256 hashes, and creates the renderer manifest.
The model checkpoint is approximately 136 MB; CPU PyTorch is a separate larger
download. Existing mismatched assets are rejected and retained for inspection.
With `--offline`, the helper verifies existing files without network access and
can recreate a missing manifest. It does not install or execute downloaded code.
Package installs require network; rendering requires none. Keep the verified
wheel and manifest with the assets when cleaning caches.

The initial no-rescoring synthetic smoke produced:
`Hello, namaste! keemat 165.50 rupaye; १२३ ml.` from
`Hello, नमस्ते! कीमत 165.50 रुपये; १२३ ml.` with no attempted socket connections.

## Required assets and manifest

The helper creates this layout; all source URLs and hashes are inspectable in
`tools/prepare_indicxlit.py` and `tools/romanize_screening.py`.

```text
<assetRoot>/
  renderer-assets.json                 # original no-rescoring experiment
  renderer-assets-default.json         # additional official rescoring assets
  code/custom_interactive.py
  code/base_engine.py                  # used only for official rescoring
  code/lang_list.txt
  code/LICENSE
  indicxlit-indic-en-v1.0/transformer/indicxlit.pt
  indicxlit-indic-en-v1.0/corpus-bin/dict.<language>.txt
  runtime/fairseq_fixed-0.12.3.1-cp312-cp312-win_amd64.whl
  rescore/word_prob_dicts_en.zip
  rescore/en_word_prob_dict.json
```

The helper retains all 23 published dictionaries. The selected Hindi (`hi`)
pair uses the Hindi and English dictionaries; the official language list is
retained unchanged. The manifest has `version: 1`, `model_repo_id`,
`model_revision`, `code_repo_id`,
`code_revision`, `license`, a `files` list containing each relative `path`,
`size`, `sha256`, and `source_url`, plus `runtime` fields `distribution`,
`version`, `wheel_sha256`, absolute `wheel_path`, `fork_repository`, and `license`.
The helper reconstructs these fields from fixed pins; do not hand-edit hashes.
`--rescore` selects a separate manifest without replacing the original one.

The additional [official English dictionary archive](https://github.com/AI4Bharat/IndicXlit/releases/download/v1.0/word_prob_dicts_en.zip)
is 1,172,782 bytes, SHA256
`27aedbab04c645f3da7d6176fdb9298326847aaa87d5ff62d9e201f293c9fc00`.
Its only file is `word_prob_dicts/en_word_prob_dict.json`, 7,349,616 bytes,
189,315 entries, SHA256
`517ec9c5e31ee59fbc612fc57b67282bb37c41fc95adf7bda329b4ef87c7f057`.
The archive has no separate license notice; its licensing basis is the official
repository's [MIT declaration for code and models](https://github.com/AI4Bharat/IndicXlit/blob/802803ee0afc7a8703815bb3eb79f99f061797a1/README.md#license)
and distribution as the official model's required rescoring asset. The helper
extracts this one named member after verifying the complete archive hash.

## Compatibility choices actually used

The renderer loads the pinned official standalone `custom_interactive.py`
directly. It does not install the full AI4Bharat service wrapper or its unrelated
web/TensorFlow/Urdu dependencies. No official source or installed package is
edited. During initialization it forces CPU detection. While loading the exact
hash-verified legacy checkpoint only, it temporarily passes
`torch.load(weights_only=False)` and rejects any other checkpoint path; it
restores the original function immediately afterward. These compatibility
choices are recorded in output provenance. The official preprocessing adds
`__hi__` and separates each word into characters. Both experiments use beam 4,
four CPU threads, and an exact-word cache. V1 explicitly disables rescoring and
selects the top model candidate. V2 uses the public API's default `rescore=True`:
the official sentence path requests four candidates, reranks them using the
English lexicon and alpha 0.9, then selects the first. To avoid the unrelated
service dependencies, the renderer compiles the **unchanged ASTs** of the
official `BaseEngineTransformer.rescore` and `post_process` methods from the
hash-verified pinned `base_engine.py` (SHA256
`a2af519a211a3348681278d11ed5431322356a8dd04a397673bce1f058e7c1fd`).
It uses standard-library JSON loading; no custom spelling mappings are added.

The source parity review checked [direction and defaults](https://github.com/AI4Bharat/IndicXlit/blob/802803ee0afc7a8703815bb3eb79f99f061797a1/app/ai4bharat/transliteration/transformer/indic2en.py#L37),
[source-language prefixing](https://github.com/AI4Bharat/IndicXlit/blob/802803ee0afc7a8703815bb3eb79f99f061797a1/app/ai4bharat/transliteration/transformer/base_engine.py#L142),
[official reranking](https://github.com/AI4Bharat/IndicXlit/blob/802803ee0afc7a8703815bb3eb79f99f061797a1/app/ai4bharat/transliteration/transformer/base_engine.py#L157),
and [top-candidate sentence selection](https://github.com/AI4Bharat/IndicXlit/blob/802803ee0afc7a8703815bb3eb79f99f061797a1/app/ai4bharat/transliteration/transformer/base_engine.py#L392).
A local task-only check, without model inference, confirmed that `hi-en` and
the full official language-pair list produce identical source/target dictionary
symbols and input token IDs; both language-token transforms are null. Batch
size is one rather than the wrapper's maximum 32; each request is one word.
These checks do not establish numerical parity with every fairseq release.

Python socket connections are blocked during model initialization and rendering;
the output records attempts. This guard is not an operating-system firewall.
The verified run needed none. Hydra/PyTorch deprecation and CPU pin-memory
warnings were informational; they did not require dependency changes.

## Render stable results

Supply only completed result files. Use a new output path; existing artifacts
are never overwritten. Substitute your private screening directory and clip IDs:

```powershell
$screeningRoot = 'C:/private/STTApp/benchmark/my-screening'
$screeningFile = Join-Path $screeningRoot 'screening.json'
$srotaResults = Join-Path $screeningRoot 'results/srota.json'
$trelisResults = Join-Path $screeningRoot 'results/trelis.json'
$romanViews = Join-Path $screeningRoot 'reviewed/roman-views-v2-default.json'
& $rendererPython -B stt_lab/tools/romanize_screening.py --assets $assetsManifest --rescore --screening $screeningFile --results $srotaResults $trelisResults --clips clip-01 clip-02 --output $romanViews
```

The derived artifact binds the screening hash, each complete ASR result-file
hash, raw text hash, model revision, renderer provenance, and exact source-span
replacement trace. Roman text contains no remaining Devanagari letters; digits
remain unchanged. Each occurrence is preserved even when its word is cached.
Per-clip rendering times exclude initialization and share a cache across models;
they are not independent model-speed benchmarks or live latency measurements.
The reviewed scorer consumes this artifact with `--roman-views`; raw ASR results
remain available for inspection and must not be blindly scored against Roman
references. Wordwise transliteration may add spelling errors and does not repair
phonetic English written in Devanagari.

To reproduce V1's explicitly unrescored variant, omit `--rescore` from both the
asset helper and renderer, use `renderer-assets.json`, and choose a different
output name. Preserve both variants; do not select spelling settings by looking
at corrected references.

The focused tests require no inference. With assets present, the integration
test verifies their hashes and runs the exact official postprocessor on
synthetic candidates to confirm reranking and source-text preservation:

```powershell
$env:STT_INDICXLIT_ASSETS = $assetRoot
& $rendererPython -B -m unittest discover -s stt_lab/tests -p test_romanize_screening.py -v
```

All eight tests passed on the retained setup. The public-asset integration test
is skipped on machines without those assets; the other tests are dependency-free.
