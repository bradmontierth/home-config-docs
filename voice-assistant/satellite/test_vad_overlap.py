"""Exercise the production playback/upload functions without mic/model hardware.

Load only these AST definitions so the test needs no NumPy, ALSA or wake models.
Real threads, futures and the production playback lock are used; only playback
and HTTP are faked. Events enforce ordering without relying on timing estimates.
"""
import ast
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import unittest

SOURCE = Path(__file__).with_name('assistant.py')


def load_functions():
    tree = ast.parse(SOURCE.read_text())
    nodes = [n for n in tree.body if
             isinstance(n, ast.FunctionDef) and n.name in {'_play_scaled', 'post_command_with_chirp'}
             or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PLAYBACK_LOCK' for t in n.targets)]
    ns = dict(threading=threading, Future=Future, time=time, PLAYBACK_RELAY_URL='',
              SOUNDS_DIR=Path('/sounds'), COMMAND_TIMEOUT_S=120, PLAYBACK_DEVICE='fake',
              log=lambda _: None)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


class OverlapTest(unittest.TestCase):
    def setUp(self):
        self.ns = load_functions()
        self.chirp_started = threading.Event()
        self.release_chirp = threading.Event()
        self.upload_started = threading.Event()
        def playback(*args, **kwargs):
            self.chirp_started.set()
            if not self.release_chirp.wait(2):
                raise RuntimeError('test did not release chirp')
        self.ns['subprocess'] = SimpleNamespace(run=playback)
        self.ns['play_file'] = lambda path: self.ns['_play_scaled'](b'chirp')
        self.addCleanup(self.release_chirp.set)

    def call(self):
        result = Future()
        def run():
            try: result.set_result(self.ns['post_command_with_chirp']('/command/audio', b'wav'))
            except BaseException as exc: result.set_exception(exc)
        threading.Thread(target=run, daemon=True).start()
        return result

    def test_upload_overlaps_chirp_but_fast_reply_waits(self):
        def upload(path, wav, timeout):
            self.assertEqual((path,wav,timeout),('/command/audio',b'wav',120))
            self.upload_started.set()
            return {'ok': True}
        self.ns['post_wav'] = upload
        result = self.call()
        self.assertTrue(self.chirp_started.wait(1))
        self.assertTrue(self.upload_started.wait(1))
        self.assertFalse(result.done())
        self.release_chirp.set()
        self.assertEqual(result.result(1), {'ok':True})

    def test_server_push_waits_for_chirp_without_deadlocking_http(self):
        reply_playing = threading.Event()
        def upload(*args, **kwargs):
            self.upload_started.set()
            # Simulates the orchestrator waiting on the satellite /speak HTTP
            # handler while the original command HTTP request is in flight.
            with self.ns['PLAYBACK_LOCK']:
                reply_playing.set()
            return {'ok':True}
        self.ns['post_wav'] = upload
        result = self.call()
        self.assertTrue(self.chirp_started.wait(1))
        self.assertTrue(self.upload_started.wait(1))
        self.assertFalse(reply_playing.is_set())
        self.release_chirp.set()
        self.assertEqual(result.result(1), {'ok':True})
        self.assertTrue(reply_playing.is_set())

    def test_http_failure_propagates_after_chirp_and_unlocks_playback(self):
        def upload(*args, **kwargs):
            self.upload_started.set()
            raise TimeoutError('HTTP timeout')
        self.ns['post_wav'] = upload
        result = self.call()
        self.assertTrue(self.chirp_started.wait(1))
        self.assertTrue(self.upload_started.wait(1))
        self.assertFalse(result.done())
        self.release_chirp.set()
        with self.assertRaisesRegex(TimeoutError, 'HTTP timeout'):
            result.result(1)
        self.assertTrue(self.ns['PLAYBACK_LOCK'].acquire(timeout=1))
        self.ns['PLAYBACK_LOCK'].release()

    def test_missing_chirp_does_not_prevent_upload(self):
        self.ns['play_file'] = lambda path: None
        self.ns['post_wav'] = lambda *a, **kw: {'ok':True}
        self.assertEqual(self.call().result(1), {'ok':True})

    def test_remote_playback_keeps_existing_serial_order(self):
        self.ns['PLAYBACK_RELAY_URL'] = 'http://remote'
        order = []
        self.ns['play_file'] = lambda path: order.append('chirp')
        def upload(*a, **kw):
            order.append('upload')
            return {'ok':True}
        self.ns['post_wav'] = upload
        self.assertEqual(self.call().result(1), {'ok':True})
        self.assertEqual(order, ['chirp', 'upload'])


if __name__ == '__main__':
    unittest.main()
