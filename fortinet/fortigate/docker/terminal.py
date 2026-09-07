import cmd
import logging
import os
import re
import threading
import time
from collections import deque
from contextlib import contextmanager

from common import TRACE_LEVEL


DEFAULT_BUFFER_LIMIT_BYTES = 1024 * 1024
BUFFER_LIMIT_ENV = "FOS_TERMINAL_BUFFER_LIMIT_BYTES"
READ_POLL_SECONDS = 0.001


def terminal_buffer_limit_bytes():
    configured = os.getenv(BUFFER_LIMIT_ENV)
    if not configured:
        return DEFAULT_BUFFER_LIMIT_BYTES

    limit = int(configured)
    if limit < 1:
        raise ValueError(f"{BUFFER_LIMIT_ENV} must be greater than zero")
    return limit


class Data:
    """A byte view returned by Terminal.expect with authority to discard itself."""

    def __init__(self, value, discard_callback=None):
        self.value = bytes(value)
        self._discard_callback = discard_callback
        self.discarded = False

    def __bytes__(self):
        return self.value

    def __bool__(self):
        return bool(self.value)

    def __contains__(self, item):
        return item in self.value

    def __len__(self):
        return len(self.value)

    def __eq__(self, other):
        return self.value == (bytes(other) if isinstance(other, Data) else other)

    def __repr__(self):
        return repr(self.value)

    def startswith(self, *args, **kwargs):
        return self.value.startswith(*args, **kwargs)

    def decode(self, *args, **kwargs):
        return self.value.decode(*args, **kwargs)

    def discard(self, value=None):
        discard_value = self.value if value is None else bytes(value)
        if not discard_value:
            return
        if self._discard_callback:
            self._discard_callback(discard_value)
        self.discarded = True


class _ByteQueue:
    def __init__(self, max_size):
        self._max_size = max_size
        self._chunks = deque()
        self._size = 0
        self._closed = False
        self._error = None
        self._condition = threading.Condition()

    def put(self, data):
        offset = 0
        while offset < len(data):
            with self._condition:
                while not self._closed and self._error is None and self._size >= self._max_size:
                    self._condition.wait()
                if self._closed or self._error is not None:
                    return

                available = self._max_size - self._size
                chunk = bytes(data[offset:offset + available])
                self._chunks.append(chunk)
                self._size += len(chunk)
                offset += len(chunk)
                self._condition.notify_all()

    def get(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._chunks and not self._closed and self._error is None:
                if deadline is None:
                    self._condition.wait()
                    continue

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b""
                self._condition.wait(remaining)

            if self._error is not None:
                raise self._error
            if not self._chunks:
                return b""

            data = b"".join(self._chunks)
            self._chunks.clear()
            self._size = 0
            self._condition.notify_all()
            return data

    def get_one(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._chunks and not self._closed and self._error is None:
                if deadline is None:
                    self._condition.wait()
                    continue

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b""
                self._condition.wait(remaining)

            if self._error is not None:
                raise self._error
            if not self._chunks:
                return b""

            data = self._chunks.popleft()
            self._size -= len(data)
            self._condition.notify_all()
            return data

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def fail(self, error):
        with self._condition:
            self._error = error
            self._condition.notify_all()


class Terminal:
    """Buffered access to the FortiOS serial console."""

    def __init__(self, connection, logger, default_wait="#"):
        self._connection = connection
        self._logger = logger
        self._default_wait = default_wait
        self._buffer = bytearray()
        self._buffer_limit = terminal_buffer_limit_bytes()
        self._read_queue = _ByteQueue(self._buffer_limit)
        self._write_queue = _ByteQueue(self._buffer_limit)
        self._reader_lock = threading.Lock()
        self._stop_reader = None
        self._reader = None
        self._writer_lock = threading.Lock()
        self._writer = None
        self._output_suppression_depth = 0
        self._output_suppression_context = None

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        self._ensure_writer()
        self._write_queue.put(data)

    def wait_write(
        self,
        cmd,
        wait="__defaultpattern__",
        clean_buffer=False,
        hold="",
        timeout=None,
    ):
        if wait:
            if wait == "__defaultpattern__":
                wait = self._default_wait
            self._logger.info(f"waiting for '{wait}' on serial console")
            pattern = re.escape(wait.encode() if isinstance(wait, str) else wait)
            _, match, output = self.expect([pattern], timeout)

            while match and hold and hold.encode() in output:
                self._logger.info(
                    f"Holding pattern '{hold}' detected, retrying in 10s..."
                )
                self.write(b"\r")
                time.sleep(10)
                _, match, output = self.expect([pattern], timeout)

            if not match:
                self._logger.info(
                    f"timed out waiting for '{wait}' on serial console"
                )

        if clean_buffer:
            self._buffer.clear()

        self._logger.debug(f"writing to serial console: '{cmd}'")
        self.write(f"{cmd}\r")

    def expect(self, regex_list, timeout=None):
        """Read until a pattern matches, retaining unmatched data across calls.

        The match ending earliest in the buffered stream wins. Data through that
        match is returned and discarded; data after it remains buffered.
        """
        self._ensure_reader()
        deadline = None if timeout is None else time.monotonic() + timeout
        received = bytearray()
        result = self._match(regex_list)

        while result is None:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return -1, None, Data(bytes(received), self._discard_prefix)

            data = self._read_queue.get(remaining)
            if data:
                self._buffer.extend(data)
                self._enforce_buffer_limit()
                received.extend(data)
                self._logger.log(TRACE_LEVEL - 1, f"buffer: {self._buffer}")
                result = self._match(regex_list)
                continue

            if deadline is not None and time.monotonic() >= deadline:
                return -1, None, Data(bytes(received), self._discard_prefix)

        index, match = result
        consumed = bytes(self._buffer[:match.end()])
        stable_match = re.search(regex_list[index], consumed)
        del self._buffer[:match.end()]
        return index, stable_match, Data(consumed)

    def _discard_prefix(self, data):
        """Discard bytes that a caller has consumed from the retained buffer."""
        if not data:
            return
        if not self._buffer.startswith(data):
            self._logger.debug("Refusing to discard non-prefix terminal output")
            return
        del self._buffer[:len(data)]

    def _enforce_buffer_limit(self):
        if len(self._buffer) <= self._buffer_limit:
            return

        self._logger.error(
            "Terminal buffer exceeded %s bytes. Tail: %r",
            self._buffer_limit,
            bytes(self._buffer[-512:]),
        )
        raise RuntimeError(
            f"Terminal buffer exceeded {self._buffer_limit} bytes without being consumed"
        )

    def _ensure_reader(self):
        with self._reader_lock:
            if self._reader is not None and self._reader.is_alive():
                return

            self._read_queue = _ByteQueue(self._buffer_limit)
            self._stop_reader = threading.Event()
            self._reader = threading.Thread(
                target=self._read_forever,
                args=(self._stop_reader, self._read_queue),
                name="fortios-terminal-reader",
            )
            self._reader.daemon = True
            self._reader.start()

    def _read_forever(self, stop_reader, read_queue):
        read = getattr(self._connection, "read_blocking", None)
        if read is None:
            read = self._connection.read_very_eager

        while not stop_reader.is_set():
            try:
                data = read()
            except Exception as error:
                if not stop_reader.is_set():
                    self._logger.exception("Terminal reader failed")
                    read_queue.fail(error)
                return

            if data:
                read_queue.put(data)
            else:
                stop_reader.wait(READ_POLL_SECONDS)

    def _ensure_writer(self):
        with self._writer_lock:
            if self._writer is not None and self._writer.is_alive():
                return

            self._write_queue = _ByteQueue(self._buffer_limit)
            self._writer = threading.Thread(
                target=self._write_forever,
                args=(self._write_queue,),
                name="fortios-terminal-writer",
            )
            self._writer.daemon = True
            self._writer.start()

    def _write_forever(self, write_queue):
        while True:
            try:
                data = write_queue.get_one()
            except Exception:
                self._logger.exception("Terminal writer failed")
                return

            if not data:
                return

            try:
                self._connection.write(data)
            except Exception:
                self._logger.exception("Terminal writer failed")
                return

    def close(self):
        with self._reader_lock:
            reader = self._reader
            stop_reader = self._stop_reader
            read_queue = self._read_queue
            self._reader = None
            self._stop_reader = None

        with self._writer_lock:
            writer = self._writer
            write_queue = self._write_queue
            self._writer = None

        if stop_reader is not None:
            stop_reader.set()
        read_queue.close()
        write_queue.close()
        if writer is not None:
            writer.join(1)
        self._buffer.clear()
        self._connection.close()
        if reader is not None:
            reader.join(1)

    @contextmanager
    def suppress_output(self):
        if self._output_suppression_depth == 0:
            self._output_suppression_context = self._connection.suppress_output()
            self._output_suppression_context.__enter__()
        self._output_suppression_depth += 1
        try:
            yield
        finally:
            self._output_suppression_depth -= 1
            if self._output_suppression_depth == 0:
                try:
                    self._output_suppression_context.__exit__(None, None, None)
                finally:
                    self._output_suppression_context = None

    def _match(self, regex_list):
        winner = None
        for index, pattern in enumerate(regex_list):
            match = re.search(pattern, self._buffer)
            if match is None:
                continue
            if winner is None or match.end() < winner[1].end():
                winner = index, match
        return winner
