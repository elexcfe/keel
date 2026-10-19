#!/usr/bin/env python3
"""限时提取视频关键帧；只记录来源与时间，不代替 Pi 解释画面。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import process_tree

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 120
MAX_FRAMES = 8
FINALIZE_SECONDS = 5
PNG_HEADER = b'\x89PNG\r\n\x1a\n'


def now():
    return datetime.now(timezone.utc).isoformat()


def collect(video, timestamps, output=None, timeout=DEFAULT_TIMEOUT):
    """返回 (新建派生目录, evidence.json 内容)；已存在的输出目录直接报错。"""
    started = time.monotonic()
    if output is None:
        root = Path.cwd() / 'result'
        root.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix='video-evidence-', dir=root))
    else:
        output = Path(output).resolve()
        output.mkdir(parents=True, exist_ok=False)
    source = Path(video).resolve()
    record = {
        'schema': 1, 'status': 'error', 'reason': '尚未完成', 'started_at': now(),
        'source': {'path': str(source), 'sha256': None, 'duration_seconds': None},
        'frames': [], 'commands': [],
        'limitations': ['仅核对文件、时长和取帧请求，不判断 Bug、操作因果或画面含义。',
                        'requested_seconds 为视频起点的相对秒数；取帧由 ffmpeg 定位，不宣称该值是实际帧的精确时间戳。'],
    }

    def save():
        temporary = output / 'evidence.json.part'
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(output / 'evidence.json')

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise subprocess.TimeoutExpired('视频工具总时限', timeout)
        return seconds

    def command(arguments, label):
        remaining()
        scope = process_tree.Scope()
        item = {'command': arguments, 'stdout': f'{label}.stdout.log',
                'stderr': f'{label}.stderr.log'}
        record['commands'].append(item)
        save()
        process = None
        with (output / item['stdout']).open('xb') as stdout, (output / item['stderr']).open('xb') as stderr:
            try:
                process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=stdout,
                                           stderr=stderr, start_new_session=True)
                item['process'] = scope.register(process)
                item['exit_code'] = process.wait(timeout=remaining())
                if item['exit_code']:
                    raise ValueError(f'{label} 执行失败（退出码 {item["exit_code"]}），见 {item["stderr"]}')
            finally:
                if process is not None:
                    item['cleanup'] = scope.stop(process, grace=.2)
                save()
        if item['cleanup']['status'] != 'completed':
            raise ValueError(f'{label} 子进程未完整回收，见 evidence.json')

    save()
    try:
        if not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT:
            raise ValueError(f'--timeout 必须为大于 0 且不超过 {MAX_TIMEOUT} 的有限秒数')
        deadline = started + timeout
        raw_parent = os.environ.get('MAKE_BUG_DEADLINE_MS')
        if raw_parent:
            parent = float(raw_parent)
            reserve = float(os.environ.get('MAKE_BUG_WRAP_UP_MS') or 0)
            if not math.isfinite(parent) or not math.isfinite(reserve) or reserve < 0:
                raise ValueError('父运行器的 deadline/wrap-up 不是有效时间')
            parent_remaining = (parent - reserve) / 1000 - time.time() - FINALIZE_SECONDS
            deadline = min(deadline, time.monotonic() + max(0, parent_remaining))
        record['budget'] = {'requested_seconds': timeout,
                            'effective_seconds': max(0, deadline - started),
                            'parent_finalize_reserve_seconds': FINALIZE_SECONDS,
                            'note': 'probe、读取原文件和全部取帧共用时限；退出后另做进程回收与记录保存。'}
        remaining()
        timestamps = list(timestamps)
        if len(timestamps) > MAX_FRAMES:
            raise ValueError(f'一次请求最多 {MAX_FRAMES} 个时间点；不传时间点时仅探测时长')
        if any(not math.isfinite(t) or t < 0 for t in timestamps):
            raise ValueError('取帧时间必须为非负有限秒数，不能使用 NaN 或 Infinity')
        if not source.is_file():
            raise ValueError(f'视频不是本地普通文件：{source}')
        ffprobe, ffmpeg = shutil.which('ffprobe'), shutil.which('ffmpeg')
        required = [('ffprobe', ffprobe)] + ([('ffmpeg', ffmpeg)] if timestamps else [])
        missing = [name for name, path in required if not path]
        if missing:
            raise ValueError(f'PATH 中缺少 {", ".join(missing)}；需要可读取该视频并输出 PNG 的完整 FFmpeg 工具，不自动安装')
        identity = source.stat()
        digest = hashlib.sha256()
        with source.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                remaining()
                digest.update(chunk)
        record['source'].update({'sha256': digest.hexdigest(), 'bytes': identity.st_size})
        command([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                 'stream=codec_type,duration:format=duration', '-of', 'json', str(source)], 'probe')
        probe_log = output / 'probe.stdout.log'
        if probe_log.stat().st_size > 1024 * 1024:
            raise ValueError('ffprobe 元数据异常过大')
        metadata = json.loads(probe_log.read_text())
        streams = metadata.get('streams', [])
        if not streams or streams[0].get('codec_type') != 'video':
            raise ValueError('ffprobe 未返回视频流')
        raw_duration = streams[0].get('duration')
        duration_source = 'video_stream'
        if raw_duration in (None, '', 'N/A'):
            raw_duration = metadata.get('format', {}).get('duration')
            duration_source = 'container'
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError):
            raise ValueError('ffprobe 未返回有效的视频时长') from None
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('ffprobe 返回了无效的视频时长')
        record['source'].update({'duration_seconds': duration, 'duration_source': duration_source})
        if any(t >= duration for t in timestamps):
            raise ValueError(f'取帧时间必须小于视频时长 {duration:g} 秒；所有请求均已停止，未提取帧')
        save()
        for index, timestamp in enumerate(timestamps, 1):
            filename = f'frame-{index:03d}.png'
            # 输出侧 -ss 让 ffmpeg 从视频起点定位；不把页面时钟当作视频时间。
            command([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-i', str(source),
                     '-ss', str(timestamp), '-map', '0:v:0', '-frames:v', '1', '-an', '-n',
                     str(output / filename)], f'frame-{index:03d}')
            target = output / filename
            with target.open('rb') as stream:
                if stream.read(len(PNG_HEADER)) != PNG_HEADER:
                    raise ValueError(f'{filename} 未生成有效 PNG 文件')
            record['frames'].append({'file': filename, 'requested_seconds': timestamp,
                                     'time_basis': 'video-relative-seconds', 'bytes': target.stat().st_size})
            save()
        current = source.stat()
        if (identity.st_dev, identity.st_ino, identity.st_size, identity.st_mtime_ns) != (
                current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
            raise ValueError('取帧期间原视频发生变化，不能将输出归于同一来源')
        record.update(status='completed', reason='取帧完成；画面含义仍需 Pi 解释及独立验收' if timestamps
                      else '视频时长探测完成；未提取帧，可按该时长选择下一次取帧时间点')
    except subprocess.TimeoutExpired:
        record.update(status='timeout', reason='视频工具总时限或父运行器收尾边界已到；保留已完成的帧与日志')
    except (KeyboardInterrupt, InterruptedError):
        record.update(status='interrupted', reason='视频工具被中断；保留已完成的帧与日志')
    except (OSError, ValueError, TypeError, AttributeError, IndexError, subprocess.SubprocessError) as exc:
        record.update(status='error', reason=str(exc))
    finally:
        record.update(finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 3))
        save()
    return output, record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', type=Path)
    parser.add_argument('--at', default=[], nargs='+', type=float, help='视频相对秒数，最多 8 个；省略时仅探测时长')
    parser.add_argument('--output', type=Path, help='新的派生目录，必须尚不存在；默认 result/video-evidence-随机名')
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT, help='整次工具共用时限，默认 60 秒，最多 120 秒')
    args = parser.parse_args(argv)

    def interrupted(signum, frame):
        raise InterruptedError(f'signal {signum}')

    signal.signal(signal.SIGTERM, interrupted)
    try:
        output, record = collect(args.video, args.at, args.output, args.timeout)
    except OSError as exc:
        print(f'无法创建新的派生目录：{exc}', file=sys.stderr)
        return 1
    print(json.dumps({'status': record['status'], 'reason': record['reason'],
                      'record': str(output / 'evidence.json'), 'frames': len(record['frames']),
                      'duration_seconds': record['source']['duration_seconds']}, ensure_ascii=False))
    return 0 if record['status'] == 'completed' else 1


if __name__ == '__main__':
    sys.exit(main())
