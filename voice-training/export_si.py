"""Export a Piper checkpoint to ONNX with PyTorch's classic exporter.

PyTorch 2.14 exports through torch.export by default, which fails on Piper's rational
quadratic spline (a data-dependent branch). The classic TorchScript exporter, still in
PyTorch as dynamo=False, is what Piper was written for.

Usage: same arguments as `python -m piper.train.export_onnx`.
"""
import sys

import torch

_export = torch.onnx.export


def classic_export(*args, **kwargs):
    kwargs.setdefault('dynamo', False)
    return _export(*args, **kwargs)


torch.onnx.export = classic_export

from piper.train.export_onnx import main  # noqa: E402

if __name__ == '__main__':
    sys.argv[0] = 'export_onnx'
    main()
