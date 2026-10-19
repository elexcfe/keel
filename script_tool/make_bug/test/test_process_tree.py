"""两个独立运行器并行，任一清理不能影响另一个或借用服务。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SRC = Path(__file__).resolve().parents[1] / 'src'
SCRIPT = '''
import sys,subprocess,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import process_tree
scope=process_tree.Scope()
p=subprocess.Popen(['sleep','30'],start_new_session=True)
Path(sys.argv[2]+'.pid').write_text(str(p.pid))
while not Path(sys.argv[2]+'.stop').exists(): time.sleep(.01)
r=scope.stop(p)
assert r['status']=='completed',r
'''

class ProcessTests(unittest.TestCase):
    def test_parallel_owners_do_not_kill_each_other_or_unrelated_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            borrowed = subprocess.Popen(['sleep','30'])
            owners = [subprocess.Popen([sys.executable,'-c',SCRIPT,str(SRC),str(root/name)]) for name in ('a','b')]
            try:
                until = time.monotonic()+5
                while not all((root/(name+'.pid')).exists() for name in ('a','b')) and time.monotonic()<until:
                    time.sleep(.02)
                self.assertTrue((root/'b.pid').exists())
                (root/'a.stop').touch()
                self.assertEqual(owners[0].wait(timeout=5),0)
                self.assertIsNone(owners[1].poll())
                self.assertIsNone(borrowed.poll())
                pid = int((root/'b.pid').read_text())
                self.assertNotEqual(Path(f'/proc/{pid}/stat').read_text().split()[2],'Z')
                (root/'b.stop').touch()
                self.assertEqual(owners[1].wait(timeout=5),0)
                self.assertIsNone(borrowed.poll())
            finally:
                for name, owner in zip(('a','b'),owners):
                    (root/(name+'.stop')).touch()
                    owner.wait(timeout=5)
                borrowed.terminate(); borrowed.wait()
