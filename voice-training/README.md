# The offline Sinhala voice, and how to train it again

`offline_voice.py` speaks Sinhala with no internet, using a Piper voice trained on this
computer. Everything needed to train it again is in this folder. The work happens in
`D:\me\piper-train`, outside the repository: the data, PyTorch and the checkpoints are
several gigabytes.

| | |
|---|---|
| Recordings | OpenSLR 30, Google's Sinhala TTS corpus, CC BY-SA 4.0 -- speaker `sin_6314`, 117 clips, 14 minutes. All twelve speakers are women. |
| Starting weights | Piper's English `lessac` medium checkpoint. Only the starting point: the result speaks Sinhala. |
| Phonemes | espeak-ng's Sinhala voice (`si`), as shipped inside `piper-tts` |
| Trained on | RTX 4060 Laptop (8 GB), Windows, no WSL. About 5.5 s an epoch at batch 16. |

## Steps

1. A Python 3.12 venv; PyTorch with CUDA (`--index-url https://download.pytorch.org/whl/cu126`;
   download the wheel first if Windows says the file is in use); then
   `lightning tensorboard tensorboardX "jsonargparse[signatures]" onnx onnxscript pysilero-vad cython librosa piper-tts`.
2. `build_align.bat` -- compiles Piper's alignment extension with Visual Studio's compiler.
3. `analyse_speakers.py` -- clips, minutes and pitch per speaker.
4. A `metadata.csv` of `file.wav|Sinhala text` for one speaker.
5. `clean_checkpoint.py lessac.ckpt lessac-clean.ckpt` -- makes the 2022 checkpoint load in
   PyTorch's safe mode, which it otherwise refuses (a Linux path object in its settings).
6. `train.bat EPOCHS 16`, which runs `train_si.py`.
7. `evaluate.sh CHECKPOINT NAME` -- exports with `export_si.py` and scores with
   `bench_offline_voice.py`.
8. Copy the `.onnx` and `.onnx.json` to `data/models/si_LK-offline-medium.onnx[.json]`.

## What the wrappers fix

`train_si.py`
- Maps espeak's superscript nasals ⁿ ᵑ ᵐ (ඳ ඟ ඹ ඬ) to n, ŋ, m. Piper otherwise dropped them,
  and the same mapping is applied when JARVIS speaks.
- Removes the `val_mos` checkpoint rule, which raised an error when the UTMOS scorer is off.
- Saves a checkpoint every 50 epochs, keeping the best three. By default it saved an
  846 MB file after every epoch: 5 GB in the first ninety seconds.
- Run with `--data.num_workers 0`: worker processes ended the first trial with an access
  violation on Windows.

`export_si.py` -- uses PyTorch's classic ONNX exporter; the default one in 2.14 fails on
Piper's spline.
