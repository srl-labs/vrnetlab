"""Minimal JSON-RPC 2.0 over websocket client (standard library only).

TrueNAS speaks JSON-RPC 2.0 over a websocket in two places:
  - the ISO's installer service (ws://<ip>:8080/ws), used at image build time
  - the middleware API of the installed system (ws://<ip>/api/current)

The vrnetlab base image has no websocket library, so this module implements
the small part of RFC 6455 that a client needs: the upgrade handshake, masked
text frames out, unmasked (possibly fragmented) frames in, ping/pong and close.
"""

import base64
import itertools
import json
import os
import socket
import struct
import time


class RPCError(Exception):
    def __init__(self, method, error):
        self.method = method
        self.error = error
        super().__init__(f"{method}: {json.dumps(error)[:1000]}")


class WebSocket:
    def __init__(self, host, port, path, timeout=30):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("connection closed during websocket handshake")
            resp += chunk
        head, _, self.buf = resp.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status + b" ":
            raise ConnectionError(f"websocket upgrade refused: {status.decode(errors='replace')}")

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def _send_frame(self, opcode, payload):
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def send(self, text):
        self._send_frame(0x1, text.encode())

    def recv(self, timeout=None):
        self.sock.settimeout(timeout)
        message = b""
        while True:
            b0, b1 = self._recv_exact(2)
            opcode = b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b1 & 0x80 else None
            payload = self._recv_exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise ConnectionError("websocket closed by peer")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            message += payload
            if b0 & 0x80:
                return message.decode()

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        self.sock.close()


class Client:
    """JSON-RPC 2.0 client. `notify` receives server notifications (method, params)."""

    def __init__(self, host, port, path, timeout=30, notify=None):
        self.ws = WebSocket(host, port, path, timeout)
        self.ids = itertools.count(1)
        self.notify = notify

    def call(self, method, params=None, timeout=300):
        rid = next(self.ids)
        self.ws.send(
            json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": list(params or [])})
        )
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError(f"{method}: no answer in {timeout}s")
            msg = json.loads(self.ws.recv(timeout=left))
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RPCError(method, msg["error"])
                return msg.get("result")
            if "method" in msg and self.notify:
                self.notify(msg["method"], msg.get("params"))

    def job(self, method, params=None, timeout=1800):
        """Call a middleware method that returns a job id and wait for the job to end"""
        job_id = self.call(method, params)
        deadline = time.time() + timeout
        while time.time() < deadline:
            jobs = self.call("core.get_jobs", [[["id", "=", job_id]]])
            if jobs and jobs[0]["state"] in ("SUCCESS", "FAILED", "ABORTED"):
                if jobs[0]["state"] != "SUCCESS":
                    raise RPCError(method, {"job": job_id, "state": jobs[0]["state"], "error": jobs[0].get("error")})
                return jobs[0].get("result")
            time.sleep(2)
        raise TimeoutError(f"{method}: job {job_id} not finished in {timeout}s")

    def close(self):
        self.ws.close()
