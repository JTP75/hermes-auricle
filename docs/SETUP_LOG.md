# hermes-auricle installation & integration log

## phase 1: repository ingestion & setup
1. **cloned the repo:** cloned your custom plugin from github directly into a temporary workspace:
   `git clone https://github.com/JTP75/hermes-auricle.git ~/hermes-auricle`
2. **placed inside gateway plugins:** copied the code structure over into hermes' native platform folder:
   `/home/pacel/.hermes/hermes-agent/plugins/platforms/auricle/`

## phase 2: dependency alignment inside core virtual environment
the core virtual environment (`~/.hermes/hermes-agent/venv/`) did not have some requirements for openwakeword and offline speech:
1. **pinned numpy:** openwakeword relies on structured array layouts that fail on numpy 2.x due to underlying changes in c-api definitions. installed a stable back-pin:
   `numpy==1.26.4`
2. **installed packages:** registered essential binaries and runtime packages directly inside the core python environment:
   * `openwakeword` (v0.6.0)
   * `vosk` (v0.3.45)
   * `edge-tts` (v7.2.7)

## phase 3: onnx weight preprocessor orchestration
your custom model (`Hey_Hermes_20260524_003610.onnx`) utilizes the **onnx** execution framework instead of google's `tflite`. openwakeword splits pre-processing tasks (melspectrogram + embedding calculations) and main classifier inferences across matching runtimes.

1. **downloaded onnx companion weights:** pulled official onnx preprocessor models directly from the repository releases:
   * `melspectrogram.onnx` (1.1MB)
   * `embedding_model.onnx` (1.3MB)
2. **linked assets:** established symbolic links inside `auricle/models/` pointing directly to your local files:
   * `vosk-model` -> `/home/pacel/misc/stt-python/model`
   * `wakeword.onnx` -> `/home/pacel/misc/stt-python/wakewords/Hey_Hermes_20260524_003610.onnx`
   * `melspectrogram.onnx` -> `/home/pacel/misc/stt-python/wakewords/melspectrogram.onnx`
   * `embedding_model.onnx` -> `/home/pacel/misc/stt-python/wakewords/embedding_model.onnx`

## phase 4: code patches & corrections
we analyzed and patched a few initialization blockages inside the cloned plugin code:

1. **patched adapter initialization (`adapter.py`):**
   integrated the `"onnx"` framework explicitly when constructing `OWWModel` to resolve runtime conflicts:
   ```python
   # inside AuricleAdapter._connect_real()
   oww = OWWModel(
       wakeword_models=[str(ww_path)],
       melspec_model_path=str(ms_path),
       embedding_model_path=str(emb_path),
       inference_framework="onnx", # added this parameter
   )
   ```
2. **swapped default paths (`consts.py`):**
   updated the hardcoded fallback values for models inside `consts.py` to target `.onnx` variables:
   ```python
   DEFAULT_OWW_WAKEWORD_MODEL_PATH  = str(_MODELS_DIR / "wakeword.onnx")
   DEFAULT_OWW_MELSPEC_MODEL_PATH   = str(_MODELS_DIR / "melspectrogram.onnx")
   DEFAULT_OWW_EMBEDDING_MODEL_PATH = str(_MODELS_DIR / "embedding_model.onnx")
   ```
3. **modified arecord probe duration (`adapter.py`):**
   when establishing arecord hardware captures, arecord threw an invalid duration error on `0.1` seconds. bumped the probe duration to `1` second to allow proper checks on raspberry pi interfaces:
   ```python
   # inside AuricleAdapter._connect_real()
   probe = subprocess.run(
       ["arecord", "-D", mic_device, "-f", "S16_LE", "-c", "1",
        "-r", str(SAMPLE_RATE), "-t", "raw", "-d", "1", "-q"], # changed from 0.1 to 1
       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=3,
   )
   ```
4. **audio cue compilation:**
   bootstrapped your `assets/` subdirectory by running the script builder inside your python path. `ping.wav`, `bong.wav`, `ding.wav`, `cleared.wav`, and `error.wav` were generated utilizing neural `en-GB-LibbyNeural` voices.

## phase 5: validation & enablement
1. **toggled plugin registration:**
   `hermes plugins enable platforms/auricle`
   this tells the hermes gateway manager to search and register the platform adapter on startup.
2. **ran an end-to-end dry-run logic check:**
   defined essential mock environments and executed the connection loop directly via a python process:
   ```python
   os.environ['AURICLE_OWW_WAKEWORD_MODEL_PATH'] = '.../wakeword.onnx'
   os.environ['AURICLE_VOSK_MODEL_PATH'] = '.../vosk-model'
   # ...
   success = await adapter.connect()
   ```
   **outcome:** validation, audio layers, arecord bounds, onnx sessions, and kaldi loaders compiled flawlessly together. `Connect returned: True` was successfully emitted.
