"""عميل WebSocket القياسي على اتصال TCP حقيقي مع خادم صغير في الاختبار."""
import base64
import hashlib
import json
import socket
import struct
import threading

import pytest
from fake_obs import FakeSocket

from recording.obs_controller import ObsWebSocket
from recording.ws_client import _GUID, WebSocketClient, WebSocketError


def _frame(opcode, payload=b"", fin=True, mask=False):
    """إطار من جهة الخادم (غير مُقنَّع افتراضيًا)."""
    head = bytearray([(0x80 if fin else 0) | opcode])
    n = len(payload)
    flag = 0x80 if mask else 0
    if n < 126:
        head.append(flag | n)
    elif n < 65536:
        head += bytes([flag | 126]) + struct.pack(">H", n)
    else:
        head += bytes([flag | 127]) + struct.pack(">Q", n)
    return bytes(head) + payload


class Server:
    """خادم WebSocket دنيء: يمرّر كل إطار مستقبَل إلى ``script``."""

    def __init__(self, script, accept_override=None):
        self.script = script
        self.accept_override = accept_override
        self.received: list[tuple[int, bytes, bool]] = []   # (opcode, data, masked)
        self.request_head = ""
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _read(self, conn, n):
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                raise EOFError
            data += chunk
        return data

    def _serve(self):
        conn, _ = self.listener.accept()
        conn.settimeout(5)
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                head += conn.recv(1024)
            self.request_head = head.decode()
            key = next(line.split(":", 1)[1].strip()
                       for line in self.request_head.split("\r\n")
                       if line.lower().startswith("sec-websocket-key"))
            accept = self.accept_override or base64.b64encode(
                hashlib.sha1((key + _GUID).encode()).digest()).decode()
            conn.sendall((
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n"
                "Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
            self.script(self, conn, self._read_client_frame)
        except (EOFError, OSError):
            pass
        finally:
            conn.close()
            self.listener.close()

    def _read_client_frame(self, conn):
        first = self._read(conn, 2)
        opcode, masked, n = first[0] & 0x0F, bool(first[1] & 0x80), first[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read(conn, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read(conn, 8))[0]
        mask = self._read(conn, 4) if masked else b"\0\0\0\0"
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(conn, n)))
        self.received.append((opcode, data, masked))
        return opcode, data


def _client(server, **kw):
    return WebSocketClient("127.0.0.1", server.port, timeout=5, **kw).connect()


def test_handshake_sends_subprotocol_and_echo_roundtrip():
    def script(srv, conn, read):
        _, data = read(conn)
        conn.sendall(_frame(1, data))
    server = Server(script)
    client = _client(server, subprotocol="obswebsocket.json")
    client.send("héllo عربي")
    assert client.recv() == "héllo عربي"
    assert "Sec-WebSocket-Protocol: obswebsocket.json" in server.request_head
    assert server.received[0][2] is True            # العميل يقنّع دائمًا
    client.close()


@pytest.mark.parametrize("size", [0, 125, 126, 70_000])
def test_all_payload_length_encodings(size):
    text = "x" * size

    def script(srv, conn, read):
        _, data = read(conn)
        conn.sendall(_frame(1, data))
    server = Server(script)
    client = _client(server)
    client.send(text)
    assert client.recv() == text
    assert server.received[0][1] == text.encode()
    client.close()


def test_ping_gets_pong_and_fragments_are_joined():
    def script(srv, conn, read):
        conn.sendall(_frame(9, b"hb"))                       # ping
        conn.sendall(_frame(1, b"par", fin=False))
        conn.sendall(_frame(0, b"t-", fin=False))
        conn.sendall(_frame(0, b"two"))
        read(conn)                                           # pong
    server = Server(script)
    client = _client(server)
    assert client.recv() == "part-two"
    client.close()
    server.thread.join(3)
    assert (0xA, b"hb", True) in server.received


def test_server_close_frame_raises():
    server = Server(lambda srv, conn, read: conn.sendall(_frame(8, b"\x03\xe8")))
    client = _client(server)
    with pytest.raises(WebSocketError, match="أغلق"):
        client.recv()


def test_bad_accept_key_rejected():
    server = Server(lambda *a: None, accept_override="AAAA")
    with pytest.raises(WebSocketError, match="مفتاح"):
        _client(server)


def test_connection_refused_and_timeout_are_wrapped():
    port = socket.socket()
    port.bind(("127.0.0.1", 0))
    free = port.getsockname()[1]
    port.close()
    with pytest.raises(WebSocketError, match="تعذّر الاتصال"):
        WebSocketClient("127.0.0.1", free, timeout=1).connect()

    def silent(srv, conn, read):
        import time
        time.sleep(1.5)
    server = Server(silent)
    client = WebSocketClient("127.0.0.1", server.port, timeout=0.3).connect()
    with pytest.raises(WebSocketError, match="مهلة"):
        client.recv()


def test_obs_protocol_end_to_end_over_real_socket():
    """مصافحة obs-websocket بتوثيقها كاملةً عبر إطارات حقيقية."""
    fake = FakeSocket("pw", {"GetVersion": lambda d: {"obsVersion": "32.2.2"}})

    def script(srv, conn, read):
        while True:
            while fake.outbox:
                conn.sendall(_frame(1, fake.outbox.pop(0).encode()))
            opcode, data = read(conn)
            if opcode == 8:
                return
            fake.send(data.decode())
            if fake.closed:
                return
    server = Server(script)
    client = ObsWebSocket(
        "127.0.0.1", server.port, "pw", timeout=5,
        connect=lambda url, timeout: WebSocketClient(
            "127.0.0.1", server.port, timeout=timeout,
            subprotocol="obswebsocket.json").connect())
    client.open()
    assert client.request("GetVersion") == {"obsVersion": "32.2.2"}
    client.close()


def test_default_connect_uses_standard_client_without_extra_packages(monkeypatch):
    """الاتصال الافتراضي لا يستورد أي حزمة خارجية (أجهزة مُدارة بلا pip)."""
    import sys

    monkeypatch.setitem(sys.modules, "websocket", None)   # لو استُوردت: ImportError

    def script(srv, conn, read):
        conn.sendall(_frame(1, json.dumps({"op": 0, "d": {}}).encode()))
    server = Server(script)
    from recording.obs_controller import _default_connect

    ws = _default_connect(f"ws://127.0.0.1:{server.port}", 5)
    assert json.loads(ws.recv())["op"] == 0
    ws.close()
    assert "obswebsocket.json" in server.request_head
