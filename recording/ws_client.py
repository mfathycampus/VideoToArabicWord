"""عميل WebSocket (RFC 6455) بمكتبة بايثون القياسية فقط.

**لماذا لا نستعمل حزمة جاهزة؟** التسجيل يجب أن يعمل لكل مستخدم، وفي ذلك
الأجهزة المُدارة والنسخة المثبَّتة التي لا pip فيها. حزمةٌ خارجية تعني
«ثبّتها» لمن لا يستطيع، وتعني تعبئتها وتحديثها مع كل إصدار. وما نحتاجه
هنا ضيّق: اتصال ``ws://127.0.0.1`` واحد برسائل نصية صغيرة مع OBS، فمئة
سطر تكفي وتُختبَر على اتصال حقيقي.

المدعوم: المصافحة مع التحقق من ``Sec-WebSocket-Accept``، رسائل نصية،
تجزئة الرسائل (fragmentation)، ping/pong، إطار الإغلاق، وأطوال الحمولة
الثلاثة. غير المدعوم عمدًا: ``wss://`` والضغط والإضافات (لا يحتاجها OBS).
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
from typing import Optional

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_OP_CONT, _OP_TEXT, _OP_BINARY = 0x0, 0x1, 0x2
_OP_CLOSE, _OP_PING, _OP_PONG = 0x8, 0x9, 0xA
#: سقف حجم الرسالة المستقبَلة — ردود OBS صغيرة؛ ما يتجاوزه عطلٌ أو عبث.
_MAX_MESSAGE = 16 * 1024 * 1024


class WebSocketError(ConnectionError):
    """فشل المصافحة أو انقطاع الاتصال أو إطار غير صالح."""


class WebSocketClient:
    def __init__(self, host: str, port: int, *, timeout: float = 10.0,
                 subprotocol: Optional[str] = None, path: str = "/") -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.subprotocol = subprotocol
        self.path = path
        self._sock: Optional[socket.socket] = None
        self._buffer = bytearray()

    # -- الاتصال ---------------------------------------------------
    def connect(self) -> "WebSocketClient":
        try:
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise WebSocketError(f"تعذّر الاتصال بـ {self.host}:{self.port}: {exc}") from exc
        self._sock.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [
            f"GET {self.path} HTTP/1.1",
            f"Host: {self.host}:{self.port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        if self.subprotocol:
            lines.append(f"Sec-WebSocket-Protocol: {self.subprotocol}")
        self._send_raw(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))

        head = self._read_until(b"\r\n\r\n")
        status_line, _, header_block = head.decode(
            "latin-1").partition("\r\n")
        if " 101" not in status_line[:16]:
            self.close()
            raise WebSocketError(f"رفض الخادم ترقية الاتصال: {status_line}")
        headers = {}
        for line in header_block.split("\r\n"):
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(
            hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            self.close()
            raise WebSocketError("ردّ المصافحة لا يطابق مفتاح الاتصال.")
        return self

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            sock.sendall(self._frame(_OP_CLOSE, struct.pack(">H", 1000)))
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    # -- الإرسال والاستقبال -----------------------------------------
    def send(self, text: str) -> None:
        self._send_raw(self._frame(_OP_TEXT, text.encode("utf-8")))

    def recv(self) -> str:
        """يعيد رسالة نصية كاملة (يجمع الأجزاء ويجيب على ping)."""
        message = bytearray()
        opcode_of_message = None
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == _OP_PING:
                self._send_raw(self._frame(_OP_PONG, payload))
            elif opcode == _OP_PONG:
                continue
            elif opcode == _OP_CLOSE:
                self.close()
                raise WebSocketError("أغلق الخادم الاتصال.")
            elif opcode in (_OP_TEXT, _OP_BINARY):
                opcode_of_message = opcode
                message = bytearray(payload)
                if fin:
                    return self._decode(opcode_of_message, message)
            elif opcode == _OP_CONT:
                if opcode_of_message is None:
                    raise WebSocketError("إطار متابعة بلا بداية.")
                message += payload
                if len(message) > _MAX_MESSAGE:
                    raise WebSocketError("رسالة أكبر من الحد المسموح.")
                if fin:
                    return self._decode(opcode_of_message, message)
            else:
                raise WebSocketError(f"نوع إطار غير معروف: {opcode}")

    # -- داخلي -----------------------------------------------------
    @staticmethod
    def _decode(opcode: int, data: bytearray) -> str:
        return bytes(data).decode("utf-8", errors="replace")

    @staticmethod
    def _frame(opcode: int, payload: bytes) -> bytes:
        """إطار مُقنَّع (العميل يقنّع دائمًا بحسب RFC 6455 §5.3)."""
        length = len(payload)
        head = bytearray([0x80 | opcode])
        if length < 126:
            head.append(0x80 | length)
        elif length < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", length)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", length)
        mask = os.urandom(4)
        head += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return bytes(head) + masked

    def _read_frame(self):
        first = self._read_exact(2)
        fin = bool(first[0] & 0x80)
        opcode = first[0] & 0x0F
        masked = bool(first[1] & 0x80)
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        if length > _MAX_MESSAGE:
            raise WebSocketError("إطار أكبر من الحد المسموح.")
        mask = self._read_exact(4) if masked else None
        payload = self._read_exact(length)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return fin, opcode, bytes(payload)

    def _read_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            self._buffer += self._read_some()
        data = bytes(self._buffer[:count])
        del self._buffer[:count]
        return data

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buffer:
            self._buffer += self._read_some()
            if len(self._buffer) > 65536:
                raise WebSocketError("ترويسة ردّ المصافحة أكبر من المعقول.")
        index = self._buffer.index(marker)
        data = bytes(self._buffer[:index])
        del self._buffer[:index + len(marker)]
        return data

    def _read_some(self) -> bytes:
        if self._sock is None:
            raise WebSocketError("الاتصال مغلق.")
        try:
            chunk = self._sock.recv(65536)
        except socket.timeout as exc:
            raise WebSocketError("انتهت مهلة الانتظار.") from exc
        except OSError as exc:
            raise WebSocketError(f"انقطع الاتصال: {exc}") from exc
        if not chunk:
            raise WebSocketError("أغلق الخادم الاتصال.")
        return chunk

    def _send_raw(self, data: bytes) -> None:
        if self._sock is None:
            raise WebSocketError("الاتصال مغلق.")
        try:
            self._sock.sendall(data)
        except OSError as exc:
            raise WebSocketError(f"انقطع الاتصال: {exc}") from exc
