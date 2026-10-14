"""Runs one approved skill, in its own process, and nothing else.

skills.call() starts this with "python -I": no user site-packages, no current directory
on the path, no PYTHON* environment. It reads {"path", "inputs"} on stdin, loads that one
file, calls run(**inputs), and prints the result as JSON. The parent kills it after a few
seconds. It never sees the JARVIS database, the console token or the conversation --
those live in another process that this one cannot reach.

This is isolation, not a sandbox: Windows offers no cheap way to drop a process's rights.
The real gates are the static check in skills.py and your reading of the code.
"""
import importlib.util
import json
import sys

LIMIT = 20000


def main():
    # Bytes, decoded here: under -I Python ignores PYTHONIOENCODING and would read and
    # write the Windows code page, which has no Sinhala.
    request = json.loads(sys.stdin.buffer.read().decode('utf-8') or '{}')
    spec = importlib.util.spec_from_file_location('skill', request['path'])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.run(**(request.get('inputs') or {}))
    if not isinstance(result, dict):
        result = {'value': result}
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > LIMIT:
        raise ValueError('the skill returned too much')
    sys.stdout.buffer.write(text.encode('utf-8'))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:      # reported to the parent, never raised into it
        sys.stdout.buffer.write(json.dumps({'error': f'{type(error).__name__}: {error}'})
                                .encode('utf-8'))
        sys.exit(1)
