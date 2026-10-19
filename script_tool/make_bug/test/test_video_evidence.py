import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import video_evidence as video


class VideoEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='video evidence ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source video.webm'
        self.source.write_bytes(b'original video bytes')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.write_tool('ffprobe', "print('{\"streams\":[{\"codec_type\":\"video\",\"duration\":\"2.0\"}],\"format\":{\"duration\":\"2.0\"}}')")
        self.write_tool('ffmpeg', "from pathlib import Path\nPath(sys.argv[-1]).write_bytes(b'\\x89PNG\\r\\n\\x1a\\nfixture')")
        self.environment = patch.dict(os.environ, {'PATH': str(self.bin)}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.parent_environment = patch.dict(os.environ, {key: '' for key in ('MAKE_BUG_DEADLINE_MS', 'MAKE_BUG_WRAP_UP_MS')})
        self.parent_environment.start()
        self.addCleanup(self.parent_environment.stop)

    def write_tool(self, name, body):
        path = self.bin / name
        path.write_text(f'#!{sys.executable}\nimport sys\n' + body + '\n')
        path.chmod(0o755)

    def collect(self, timestamps=(0.0, 1.0), timeout=3):
        return video.collect(self.source, timestamps, self.root / 'derived', timeout)

    def test_records_source_and_requested_video_seconds_without_touching_original(self):
        output, record = self.collect()
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['source']['sha256'], hashlib.sha256(b'original video bytes').hexdigest())
        self.assertEqual(record['source']['duration_seconds'], 2.0)
        self.assertEqual(record['source']['path'], str(self.source.resolve()))
        self.assertEqual([frame['requested_seconds'] for frame in record['frames']], [0, 1])
        self.assertTrue(all((output / frame['file']).is_file() for frame in record['frames']))
        self.assertIn('不', record['limitations'][0])
        self.assertEqual(json.loads((output / 'evidence.json').read_text()), json.loads(json.dumps(record)))
        self.assertEqual(self.source.read_bytes(), b'original video bytes')

    def test_rejects_entire_out_of_range_request_before_extracting_frames(self):
        for timestamp in (-1, float('nan'), float('inf'), 2, 3):
            with self.subTest(timestamp=timestamp):
                output, record = video.collect(self.source, (0, timestamp), self.root / str(timestamp), 3)
                self.assertEqual(record['status'], 'error')
                self.assertEqual(record['frames'], [])
                self.assertFalse(list(output.glob('*.png')))

    def test_rejects_bad_timeout_and_too_many_frames(self):
        for timeout in (0, -1, 121, float('nan'), float('inf')):
            with self.subTest(timeout=timeout):
                _, record = video.collect(self.source, (0,), self.root / f'timeout-{timeout}', timeout)
                self.assertEqual(record['status'], 'error')
        _, record = video.collect(self.source, tuple(range(9)), self.root / 'too-many', 3)
        self.assertEqual(record['status'], 'error')

    def test_existing_output_is_never_overwritten(self):
        output = self.root / 'derived'
        output.mkdir()
        (output / 'evidence.json').write_text('keep me')
        with self.assertRaises(FileExistsError):
            self.collect()
        self.assertEqual((output / 'evidence.json').read_text(), 'keep me')

    def test_missing_tool_and_invalid_metadata_are_explicit_errors(self):
        (self.bin / 'ffprobe').unlink()
        _, missing = self.collect()
        self.assertEqual(missing['status'], 'error')
        self.assertIn('ffprobe', missing['reason'])
        for index, metadata in enumerate(('{}', '{"streams":[],"format":{"duration":"2"}}',
                                         '{"streams":[{"codec_type":"video"}],"format":{"duration":"N/A"}}')):
            self.write_tool('ffprobe', f'print({metadata!r})')
            _, record = video.collect(self.source, (0,), self.root / f'meta-{index}', 3)
            self.assertEqual(record['status'], 'error')
            self.assertEqual(record['frames'], [])

    def test_shared_deadline_retains_successful_frame_and_cleans_children(self):
        self.write_tool('ffmpeg', """from pathlib import Path
import subprocess, time
target = Path(sys.argv[-1])
if '002' in target.name:
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
    (target.parent / 'child.pid').write_text(str(child.pid))
    time.sleep(60)
else:
    target.write_bytes(b'\\x89PNG\\r\\n\\x1a\\nfixture')
""")
        started = time.monotonic()
        output, record = self.collect(timeout=.5)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(record['status'], 'timeout')
        self.assertEqual(len(record['frames']), 1)
        self.assertTrue((output / record['frames'][0]['file']).exists())
        pid = int((output / 'child.pid').read_text())
        path = Path(f'/proc/{pid}/stat')
        if path.exists():
            self.assertEqual(path.read_text().split()[2], 'Z')
        self.assertEqual(record['commands'][-1]['cleanup']['status'], 'completed')

    def test_probe_and_frames_share_one_deadline(self):
        self.write_tool('ffprobe', "import time\ntime.sleep(.3)\nprint('{\"streams\":[{\"codec_type\":\"video\",\"duration\":\"2\"}]}')")
        self.write_tool('ffmpeg', 'import time\ntime.sleep(.3)')
        _, record = self.collect(timeout=.5)
        self.assertEqual(record['status'], 'timeout')
        self.assertEqual(record['frames'], [])

    def test_parent_wrap_up_deadline_stops_before_launch(self):
        with patch.dict(os.environ, {'MAKE_BUG_DEADLINE_MS': str((time.time() + 5) * 1000), 'MAKE_BUG_WRAP_UP_MS': '10000'}):
            _, record = self.collect()
        self.assertEqual(record['status'], 'timeout')
        self.assertEqual(record['commands'], [])

    def test_no_frame_is_not_reported_as_success(self):
        self.write_tool('ffmpeg', 'pass')
        _, record = self.collect()
        self.assertEqual(record['status'], 'error')
        self.assertEqual(record['frames'], [])

    def test_cli_reports_failure_and_keeps_machine_record(self):
        result = subprocess.run([sys.executable, str(Path(video.__file__)), str(self.source),
                                 '--at', '20', '--output', str(self.root / 'cli')],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        summary = json.loads(result.stdout)
        self.assertEqual(summary['status'], 'error')
        self.assertTrue(Path(summary['record']).is_file())

    def test_probe_only_needs_no_ffmpeg_and_cli_accepts_no_timestamps(self):
        (self.bin / 'ffmpeg').unlink()
        output, record = self.collect(())
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['source']['duration_seconds'], 2)
        self.assertEqual(record['frames'], [])
        self.assertEqual(len(record['commands']), 1)
        result = subprocess.run([sys.executable, str(Path(video.__file__)), str(self.source),
                                 '--output', str(self.root / 'probe-cli')],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['frames'], 0)

    def test_sigterm_writes_interrupted_record_and_reaps_child(self):
        self.write_tool('ffmpeg', """from pathlib import Path
import os, time
(Path(sys.argv[-1]).parent / 'started.pid').write_text(str(os.getpid()))
time.sleep(60)
""")
        output = self.root / 'signal'
        process = subprocess.Popen([sys.executable, str(Path(video.__file__)), str(self.source),
                                    '--at', '0', '--output', str(output)], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 3
            while not (output / 'started.pid').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue((output / 'started.pid').exists())
            process.terminate()
            stdout, stderr = process.communicate(timeout=4)
            self.assertEqual(json.loads(stdout)['status'], 'interrupted', stderr)
            record = json.loads((output / 'evidence.json').read_text())
            self.assertEqual(record['commands'][-1]['cleanup']['status'], 'completed')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=4)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), '完整 ffmpeg/ffprobe 未安装；不下载依赖')
class RealVideoTests(unittest.TestCase):
    def test_synthetic_video_extracts_frames_and_rejects_endpoint(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'synthetic.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=32x32:r=10:d=1',
                            '-c:v', 'mpeg4', str(source)], check=True, timeout=10)
            output, record = video.collect(source, (0, .4), root / 'frames', 10)
            self.assertEqual(record['status'], 'completed', record['reason'])
            self.assertEqual(len(record['frames']), 2)
            self.assertTrue((output / record['frames'][0]['file']).read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
            _, rejected = video.collect(source, (record['source']['duration_seconds'],), root / 'rejected', 10)
            self.assertEqual(rejected['status'], 'error')


if __name__ == '__main__':
    unittest.main()
