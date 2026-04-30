"""In-memory pipeline relay for local-storage file transfers.

Lets the receiver's GET start streaming bytes before the sender's PUT
finishes, cutting total relay time from (upload + download) to roughly
max(upload, download). Falls back to disk-backed send_file when:
  - The buffer is gone (GET arrives long after PUT completed)
  - Back-pressure activated because no reader attached in time
  - Request has a Range header (pipeline can only stream from offset 0)
  - PUT failed mid-stream

Uses gevent primitives so it cooperates with the gevent WSGI worker.
"""

import time

from gevent.event import Event
from gevent.lock import RLock
from gevent.queue import Empty, Queue


_SENTINEL_DONE = object()
_SENTINEL_FAILED = object()
_SENTINEL_DROPPED = object()


class PipelineDroppedToDisk(Exception):
    """Reader detected the buffer was abandoned to disk-only mode."""


class PipelineBuffer:
    """Single-producer / single-consumer streaming buffer.

    Producer (PUT) calls append() per chunk; consumer (GET) iterates
    read_iter(). When buffer fills:
      - With a reader attached: append() blocks until the reader drains
        (TCP back-pressure to the sender).
      - Without a reader: buffer is abandoned (dropped_to_disk); PUT
        keeps writing to disk so the GET path can fall back to send_file.
    """

    def __init__(self, file_key, content_type, max_buffer_bytes):
        self.file_key = file_key
        self.content_type = content_type
        self.max_buffer_bytes = max_buffer_bytes
        self._queue = Queue()
        self._lock = RLock()
        self._buffered_bytes = 0
        self._space_event = Event()
        self._space_event.set()
        self.terminal_event = Event()
        self.total_received = 0
        self.finished = False
        self.failed = False
        self.fail_reason = ''
        self.terminal_at = None
        self.created_at = time.time()
        self.has_reader = False
        self.dropped_to_disk = False

    def append(self, chunk: bytes):
        if not chunk:
            return
        while True:
            with self._lock:
                if self.dropped_to_disk:
                    return
                if self._buffered_bytes + len(chunk) <= self.max_buffer_bytes:
                    self._queue.put(chunk)
                    self._buffered_bytes += len(chunk)
                    self.total_received += len(chunk)
                    if self._buffered_bytes >= self.max_buffer_bytes:
                        self._space_event.clear()
                    return
                if not self.has_reader:
                    self._discard_queue()
                    self._buffered_bytes = 0
                    self.dropped_to_disk = True
                    self._queue.put(_SENTINEL_DROPPED)
                    self._space_event.set()
                    return
                self._space_event.clear()
            self._space_event.wait()

    def _discard_queue(self):
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Empty:
                break

    def mark_done(self):
        with self._lock:
            if self.finished or self.failed:
                return
            self.finished = True
            self.terminal_at = time.time()
            self._queue.put(_SENTINEL_DONE)
        self.terminal_event.set()

    def mark_failed(self, reason: str = ''):
        with self._lock:
            if self.finished or self.failed:
                return
            self.failed = True
            self.fail_reason = reason
            self.terminal_at = time.time()
            self._queue.put(_SENTINEL_FAILED)
        self.terminal_event.set()

    def attach_reader(self):
        with self._lock:
            if self.has_reader or self.dropped_to_disk:
                return False
            self.has_reader = True
            return True

    def detach_reader(self):
        with self._lock:
            if not self.has_reader:
                return
            self.has_reader = False
            self._discard_queue()
            self._buffered_bytes = 0
            if not self.finished and not self.failed:
                self.dropped_to_disk = True
            self._space_event.set()

    def read_iter(self, idle_timeout_s=30.0):
        while True:
            try:
                item = self._queue.get(timeout=idle_timeout_s)
            except Empty:
                raise TimeoutError('pipeline reader idle timeout')
            if item is _SENTINEL_DONE:
                return
            if item is _SENTINEL_FAILED:
                raise IOError(f'pipeline upload failed: {self.fail_reason}')
            if item is _SENTINEL_DROPPED:
                raise PipelineDroppedToDisk()
            with self._lock:
                self._buffered_bytes -= len(item)
                if self._buffered_bytes < self.max_buffer_bytes:
                    self._space_event.set()
            yield item


class PipelineRegistry:
    def __init__(self, max_buffer_bytes=16 * 1024 * 1024, retain_after_finish_s=2.0):
        self._buffers = {}
        self._waiters = {}
        self._lock = RLock()
        self.max_buffer_bytes = max_buffer_bytes
        self.retain_after_finish_s = retain_after_finish_s

    def _get_locked(self, file_key):
        buf = self._buffers.get(file_key)
        if buf is None:
            return None
        if buf.terminal_at is not None and (time.time() - buf.terminal_at) > self.retain_after_finish_s:
            self._buffers.pop(file_key, None)
            return None
        return buf

    def open_for_write(self, file_key, content_type):
        with self._lock:
            buf = PipelineBuffer(file_key, content_type, self.max_buffer_bytes)
            self._buffers[file_key] = buf
            event = self._waiters.pop(file_key, None)
        if event is not None:
            event.set()
        return buf

    def get(self, file_key):
        with self._lock:
            return self._get_locked(file_key)

    def wait_for(self, file_key, timeout_s):
        """Block up to timeout_s for a buffer to be registered for file_key."""
        with self._lock:
            buf = self._get_locked(file_key)
            if buf is not None:
                return buf
            event = self._waiters.get(file_key)
            if event is None:
                event = Event()
                self._waiters[file_key] = event
        if not event.wait(timeout=timeout_s):
            with self._lock:
                if self._waiters.get(file_key) is event:
                    self._waiters.pop(file_key, None)
            return None
        return self.get(file_key)
