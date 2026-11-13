"""Private one-shot native worker; no Django, source checkout imports or video."""
import hashlib
import json
import os
from pathlib import Path
import resource
import socket
import sys
from types import SimpleNamespace


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_FSIZE, (4096, 4096))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    def denied(*args, **kwargs):
        raise RuntimeError('manufacturer network disabled')
    socket.socket.connect = socket.socket.connect_ex = denied
    # The binding prints native diagnostics even with verbose=False. Keep those
    # off the result pipe, which admits only the bounded structured response.
    saved_stdout = os.dup(1)
    with open(os.devnull, 'wb') as sink:
        os.dup2(sink.fileno(), 1)
    lock_path=Path(sys.argv[1]).resolve()
    lock = json.loads(lock_path.read_text())
    for item in lock['artifacts'].values():
        path=Path(item['path']).expanduser()
        item['path']=str(path if path.is_absolute() else lock_path.parent/path)
    for key in ('model', 'projector'):
        item = lock['artifacts'][key]
        with Path(item['path']).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                raise RuntimeError('manufacturer asset changed')
    # -I excludes ambient PYTHONPATH; admit only the target-owned adapter directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from llama_backend import LlamaBackend
    from prompts import PROMPT
    import ctypes
    cuda = ctypes.CDLL(lock.get('cuda_runtime_library','libcudart.so.13'))
    def memory():
        free, total = ctypes.c_size_t(), ctypes.c_size_t()
        if cuda.cudaMemGetInfo(ctypes.byref(free), ctypes.byref(total)) != 0:
            return None
        return dict(used_mib=(total.value-free.value)/2**20, total_mib=total.value/2**20)
    png = sys.stdin.buffer.read(8*1024*1024+1)
    if len(png) > 8*1024*1024 or not png.startswith(b'\x89PNG\r\n\x1a\n'):
        raise RuntimeError('invalid crop transport')
    backend = LlamaBackend(SimpleNamespace(device='cuda:0', context_size=4096, max_new_tokens=128),
                           lock['artifacts']['model']['path'], lock['artifacts']['projector']['path'])
    try:
        loaded = memory()
        response = backend.generate(png, PROMPT, temperature=0.0, top_p=1.0, max_tokens=128)
        choice = response['choices'][0]
        text = choice['message']['content']
        valid = choice['finish_reason'] == 'stop' and type(text) is str and len(text.encode()) <= 2048
        result = dict(outcome='completed' if valid else 'malformed', text=text if valid else '',
                      gpu_memory=dict(after_load=loaded, after_inference=memory(),
                                      scope='CUDA-visible instance, includes other processes; not a peak'))
    finally:
        backend.close()
    sys.stdout.flush()
    os.dup2(saved_stdout, 1)
    os.close(saved_stdout)
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()
