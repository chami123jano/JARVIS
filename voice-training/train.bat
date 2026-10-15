@echo off
rem Fine-tune a Sinhala Piper voice (speaker sin_6314, OpenSLR 30). The lessac checkpoint
rem is only the starting weights; training starts at epoch 0 with a fresh optimiser.
rem Usage: train.bat MAX_EPOCHS BATCH_SIZE
cd /d D:\me\piper-train
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" train_si.py fit ^
  --data.voice_name "si_LK-offline-medium" ^
  --data.csv_path metadata.csv ^
  --data.audio_dir data ^
  --model.sample_rate 22050 ^
  --data.espeak_voice si ^
  --data.cache_dir cache ^
  --data.config_path voice\si_LK-offline-medium.onnx.json ^
  --data.batch_size %2 ^
  --trainer.max_epochs %1 ^
  --trainer.accelerator gpu ^
  --trainer.devices 1 ^
  --trainer.default_root_dir runs ^
  --model.warmstart_ckpt checkpoint\lessac-medium-clean.ckpt ^
  --model.mos_metric none ^
  --data.num_workers 0
