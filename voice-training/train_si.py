"""Piper training for Sinhala, with two fixes found on the first trial run.

1. espeak-ng writes Sinhala's prenasalised consonants (ඳ ඟ ඹ ඬ) with superscript nasals:
   ⁿ ᵑ ᵐ. They are not in the voice's symbol table, so Piper dropped them with a warning,
   and the nasal would have been lost from every "අඳුර" and "ගඟ". They become n, ŋ, m --
   the same mapping JARVIS applies when it speaks (SINHALA_NASALS in offline_voice.py).
2. Run with --data.num_workers 0: the first trial ended in an access violation after the
   first epoch, the known way background loader processes fail on Windows.

Usage: same arguments as `python -m piper.train fit`.
"""
import sys

import piper.phoneme_ids as phoneme_ids

SINHALA_NASALS = {'ⁿ': 'n', 'ᵑ': 'ŋ', 'ᵐ': 'm'}
_original = phoneme_ids.phonemes_to_ids


def phonemes_to_ids(phonemes, id_map=None):
    return _original([SINHALA_NASALS.get(p, p) for p in phonemes], id_map)


phoneme_ids.phonemes_to_ids = phonemes_to_ids

import piper.train.__main__ as piper_main  # noqa: E402  (after the patch, deliberately)

# 3. With the UTMOS scorer off (it is another model download), Piper's second checkpoint
#    rule, monitoring "val_mos", raised MisconfigurationException at the end of the first
#    epoch -- although Piper's own comment says it would be skipped. Keep the val_mel rule.
# 4. And the val_mel rule saved an 846 MB checkpoint, often two, after EVERY epoch: six of
#    them, 5 GB, in the first ninety seconds -- 2.5 TB of disk writes over 1,500 epochs.
#    Every 50 epochs, best three kept.
from lightning.pytorch.callbacks import ModelCheckpoint  # noqa: E402

piper_main._DEFAULT_CALLBACKS[:] = [ModelCheckpoint(
    monitor='val_mel', mode='min', save_top_k=3, save_last=True, every_n_epochs=50,
    filename='epoch={epoch}-val_mel={val_mel:.4f}', auto_insert_metric_name=False)]
main = piper_main.main

if __name__ == '__main__':
    sys.argv[0] = 'piper.train'
    main()
