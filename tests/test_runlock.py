"""Only one run at a time. Run: python -m unittest"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rlvid.runlock import RunLock

HOLD = """
import sys, time
sys.path.insert(0, {root!r})
from pathlib import Path
from rlvid.runlock import RunLock
lock = RunLock(Path({path!r}))
print("got" if lock.acquire() else "busy", flush=True)
time.sleep(30)
"""


class RunLockTest(unittest.TestCase):
    def test_second_holder_is_refused_and_sees_who_holds_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "run.lock"
            with RunLock(path) as first:
                self.assertIsNotNone(first.f)
                second = RunLock(path)
                self.assertFalse(second.acquire())
                self.assertIn("process", second.holder)
            third = RunLock(path)  # released: free again
            self.assertTrue(third.acquire())
            third.release()

    def test_lock_is_released_when_the_process_dies(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "run.lock"
            root = str(Path(__file__).resolve().parent.parent)
            p = subprocess.Popen([sys.executable, "-c", HOLD.format(root=root, path=str(path))],
                                 stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(p.stdout.readline().strip(), "got")
                self.assertFalse(RunLock(path).acquire())  # held by the other process
            finally:
                p.kill()  # like a crash: no clean release
                p.wait()
            lock = RunLock(path)
            self.assertTrue(lock.acquire())  # the OS released it
            lock.release()


if __name__ == "__main__":
    unittest.main()
