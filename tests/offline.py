"""Pull the plug, in software: every connection that is not to this computer fails.

    with unplugged():
        ...   # Ollama on 127.0.0.1 still answers; Google, Microsoft and the news do not

Done at the socket level, so it catches every library -- requests, edge-tts's asyncio,
SpeechRecognition's urllib -- without knowing how each one connects.
"""
import contextlib
import socket

LOCAL = ('127.0.0.1', '::1', 'localhost', '0.0.0.0')


@contextlib.contextmanager
def unplugged():
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo
    attempts = []

    def local(host):
        return str(host) in LOCAL or str(host).startswith('127.')

    def connect(sock, address):
        if isinstance(address, tuple) and not local(address[0]):
            attempts.append(address[0])
            raise OSError(10051, 'network is unreachable (unplugged for the test)')
        return real_connect(sock, address)

    def connect_ex(sock, address):
        if isinstance(address, tuple) and not local(address[0]):
            attempts.append(address[0])
            return 10051
        return real_connect_ex(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not local(host):
            attempts.append(host)
            raise socket.gaierror(11001, f'{host}: no network (unplugged for the test)')
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    socket.getaddrinfo = getaddrinfo
    try:
        yield attempts
    finally:
        socket.socket.connect, socket.socket.connect_ex = real_connect, real_connect_ex
        socket.getaddrinfo = real_getaddrinfo
