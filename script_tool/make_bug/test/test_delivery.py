import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import delivery

class DeliveryTests(unittest.TestCase):
    def test_missing_stale_broken_and_complete_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / 'task'; task.mkdir()
            run = Path(tmp) / 'run'; run.mkdir()
            before = delivery.inventory(task)
            value = delivery.checkpoint(task, run, before, 'timeout')
            self.assertEqual(len(value['missing']), 5)
            for name in delivery.REQUIRED:
                path = task / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('content')
            before = delivery.inventory(task)
            self.assertTrue(delivery.checkpoint(task, run, before, 'completed')['warnings'])
            (task / 'result/REPRO.md').write_text('[missing](missing.png)')
            self.assertIn('链接', delivery.checkpoint(task, run, before, 'interrupted')['warnings'][0])
            (task / 'result/REPRO.md').write_text('已运行；仍需人工验收。')
            value = delivery.checkpoint(task, run, before, 'completed')
            self.assertEqual(value['delivery'], 'files_present')
            self.assertEqual(value['review'], 'pending')
            self.assertTrue((run / 'REPRO.partial.md').exists())
