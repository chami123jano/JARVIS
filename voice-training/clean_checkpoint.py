"""Make the 2022 lessac checkpoint loadable by current PyTorch, without unsafe loading.

It stores a Linux path object (pathlib.PosixPath) among its settings, which PyTorch's safe
loader refuses. It is read here as a pure path -- data, no code -- turned into text, and
saved again. weights_only stays on throughout.
"""
import pathlib
import sys

import torch

source, target = sys.argv[1], sys.argv[2]
with torch.serialization.safe_globals([(pathlib.PurePosixPath, 'pathlib.PosixPath')]):
    checkpoint = torch.load(source, weights_only=True, map_location='cpu')


def plain(value):
    if isinstance(value, pathlib.PurePath):
        return str(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(plain(item) for item in value)
    return value


checkpoint = plain(checkpoint)
torch.save(checkpoint, target)
again = torch.load(target, weights_only=True, map_location='cpu')
print('saved', target, '| epoch', again.get('epoch'), '| keys', sorted(again)[:8])
print('hyper parameters:', {k: again.get('hyper_parameters', {}).get(k)
                            for k in ('sample_rate', 'num_speakers', 'num_symbols')})
