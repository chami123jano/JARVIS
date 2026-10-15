#!/usr/bin/env bash
# Export a training checkpoint to ONNX and measure how understandable it is.
# Usage: evaluate.sh CHECKPOINT NAME
set -e
cd /d/me/piper-train
mkdir -p eval
./.venv/Scripts/python.exe export_si.py --checkpoint "$1" --output-file "eval/$2.onnx" 2>&1 | grep -iv "warn\|flop\|jit" | tail -2
cp voice/si_LK-offline-medium.onnx.json "eval/$2.onnx.json"
cd /d/me/JARVIS
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe bench_offline_voice.py --model "D:/me/piper-train/eval/$2.onnx" 2>&1 | grep -v "warn\|symlink"
