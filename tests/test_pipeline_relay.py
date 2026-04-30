"""Functional tests for the pipeline relay buffer.

Covers the realistic interleavings between PUT producer and GET consumer
without going through Flask/network: receiver early, receiver same-time,
receiver late, sender failure, back-pressure with no reader, and reader
detach mid-stream.
"""

import threading
import time
import unittest

import gevent

from app.services.pipeline_relay import (
    PipelineBuffer,
    PipelineDroppedToDisk,
    PipelineRegistry,
)


def _consume(buf):
    return b''.join(buf.read_iter(idle_timeout_s=5.0))


class PipelineBufferTest(unittest.TestCase):
    def test_full_payload_with_attached_reader(self):
        buf = PipelineBuffer('k', 'application/octet-stream', max_buffer_bytes=1024 * 1024)
        self.assertTrue(buf.attach_reader())
        result = []

        def reader():
            for chunk in buf.read_iter(idle_timeout_s=2.0):
                result.append(chunk)

        g = gevent.spawn(reader)
        buf.append(b'hello ')
        buf.append(b'world')
        buf.mark_done()
        g.join(timeout=2.0)
        self.assertEqual(b''.join(result), b'hello world')

    def test_reader_arrives_early_then_data_streams(self):
        buf = PipelineBuffer('k', 'text/plain', max_buffer_bytes=1024 * 1024)
        self.assertTrue(buf.attach_reader())
        produced = [b'alpha', b'beta', b'gamma']
        results = []

        def reader():
            for chunk in buf.read_iter(idle_timeout_s=2.0):
                results.append(chunk)

        g = gevent.spawn(reader)
        gevent.sleep(0.05)  # reader is blocked on the queue
        for chunk in produced:
            buf.append(chunk)
            gevent.sleep(0.01)
        buf.mark_done()
        g.join(timeout=2.0)
        self.assertEqual(b''.join(results), b''.join(produced))

    def test_failed_upload_propagates_to_reader(self):
        buf = PipelineBuffer('k', 'text/plain', max_buffer_bytes=1024 * 1024)
        self.assertTrue(buf.attach_reader())
        buf.append(b'partial')
        buf.mark_failed('disk full')

        gen = buf.read_iter(idle_timeout_s=1.0)
        self.assertEqual(next(gen), b'partial')
        with self.assertRaises(IOError):
            next(gen)

    def test_no_reader_drops_to_disk_when_buffer_overflows(self):
        buf = PipelineBuffer('k', 'application/octet-stream', max_buffer_bytes=128)
        # No attach_reader call.
        buf.append(b'x' * 100)
        self.assertFalse(buf.dropped_to_disk)
        buf.append(b'y' * 100)  # exceeds 128 cap, should drop
        self.assertTrue(buf.dropped_to_disk)
        # attach_reader after drop must fail
        self.assertFalse(buf.attach_reader())

    def test_reader_detach_unblocks_producer(self):
        buf = PipelineBuffer('k', 'application/octet-stream', max_buffer_bytes=128)
        self.assertTrue(buf.attach_reader())

        producer_done = threading.Event()

        def producer():
            buf.append(b'z' * 200)  # would block at byte 128
            producer_done.set()

        g = gevent.spawn(producer)
        gevent.sleep(0.05)
        self.assertFalse(producer_done.is_set(), 'producer should be blocked on space')

        buf.detach_reader()  # should unblock producer (drops to disk)
        g.join(timeout=2.0)
        self.assertTrue(producer_done.is_set())
        self.assertTrue(buf.dropped_to_disk)

    def test_back_pressure_blocks_producer_until_reader_drains(self):
        buf = PipelineBuffer('k', 'application/octet-stream', max_buffer_bytes=128)
        self.assertTrue(buf.attach_reader())

        produced = []

        def producer():
            for _ in range(8):
                payload = b'q' * 64
                buf.append(payload)
                produced.append(payload)
            buf.mark_done()

        consumed = []

        def consumer():
            for chunk in buf.read_iter(idle_timeout_s=2.0):
                consumed.append(chunk)
                gevent.sleep(0.02)

        gp = gevent.spawn(producer)
        gc = gevent.spawn(consumer)
        gevent.joinall([gp, gc], timeout=5.0)
        self.assertEqual(b''.join(consumed), b''.join(produced))
        self.assertFalse(buf.dropped_to_disk)


class PipelineRegistryTest(unittest.TestCase):
    def test_wait_for_resolves_when_writer_opens(self):
        reg = PipelineRegistry(max_buffer_bytes=1024)
        results = {}

        def waiter():
            results['buf'] = reg.wait_for('key1', timeout_s=2.0)

        g = gevent.spawn(waiter)
        gevent.sleep(0.05)
        buf = reg.open_for_write('key1', 'text/plain')
        g.join(timeout=2.0)
        self.assertIs(results.get('buf'), buf)

    def test_wait_for_times_out(self):
        reg = PipelineRegistry(max_buffer_bytes=1024)
        start = time.time()
        result = reg.wait_for('missing', timeout_s=0.2)
        elapsed = time.time() - start
        self.assertIsNone(result)
        self.assertGreaterEqual(elapsed, 0.18)

    def test_terminal_buffer_is_evicted_after_retention(self):
        reg = PipelineRegistry(max_buffer_bytes=1024, retain_after_finish_s=0.1)
        buf = reg.open_for_write('key2', 'text/plain')
        buf.mark_done()
        self.assertIs(reg.get('key2'), buf)
        time.sleep(0.15)
        self.assertIsNone(reg.get('key2'))


if __name__ == '__main__':
    unittest.main()
