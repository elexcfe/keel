#!/usr/bin/env python3
"""Decode actual recordings; independent pixel oracle, not a production validator."""
import argparse
import binascii
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import subprocess

WIDTH, HEIGHT = 128, 96  # marker + operation indicator + independent chart pixels
FRAME_BYTES = WIDTH * HEIGHT * 3


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def luminance(frame, x, y):
    return sum(frame[(row * WIDTH + col) * 3 + channel]
               for row in range(y - 1, y + 1)
               for col in range(x - 1, x + 1) for channel in range(3)) / 12


def bits_at(frame, count, y_offset, rows=True):
    bits = []
    for bit in range(count):
        level = luminance(frame, (bit % 16) * 8 + 4,
                          y_offset + ((bit // 16) * 8 + 4 if rows else 8))
        if 75 < level < 180:
            return None
        bits.append(int(level >= 180))
    return bits


def decode_marker(frame, token):
    bits = bits_at(frame, 128, 0)
    if bits is None:
        return None, 'ambiguous'
    data = bytes(sum(bits[i + b] << (7 - b) for b in range(8)) for i in range(0, 128, 8))
    if data[:2] != b'KE' or data[14:] != b'\xa5\x5a':
        return None, 'missing'
    if binascii.crc_hqx(data[:12], 0xffff) != int.from_bytes(data[12:14], 'big'):
        return None, 'checksum'
    if int.from_bytes(data[2:6], 'big') != token:
        return None, 'wrong_run'
    if int.from_bytes(data[6:8], 'big') != 1:
        return None, 'wrong_document'
    return int.from_bytes(data[8:12], 'big'), None


def decode_trial(frame):
    bits = bits_at(frame, 16, 64, rows=False)
    if bits is None:
        return None
    values = [sum(bits[i + b] << (7 - b) for b in range(8)) for i in (0, 8)]
    return values[0] if values[0] ^ values[1] == 255 else None


def match_sample(frame, token, samples):
    seq, error = decode_marker(frame, token)
    if error:
        return seq, None, error
    row = samples.get(seq)
    return seq, row, None if row is not None else 'missing_log'


def chart_pixels(frame):
    votes = Counter()
    for i in range(128 * 80 * 3, FRAME_BYTES, 3):
        r, g, b = frame[i:i + 3]
        if g > r + 60 and g > b + 20:
            votes['present'] += 1
        elif r > g + 60 and r > b + 20:
            votes['absent'] += 1
    for name, count in votes.items():
        if count > 128 * 16 * .9:
            return name
    return 'unknown'


def quantile(values, fraction):
    return sorted(values)[round((len(values) - 1) * fraction)] if values else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--ffprobe', required=True)
    args = parser.parse_args()
    root = args.capture.resolve()
    manifest_path = root / 'captures.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest['status'] != 'completed':
        raise ValueError('Capture did not complete; preserve raw files and inspect captures.json')
    output = root / 'analysis'
    output.mkdir()  # No overwrites of earlier analysis.
    commands = []

    def execute(command, label):
        commands.append({'label': label, 'argv': command})
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        with (output / f'{label}.stderr.log').open('xb') as log:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=log, timeout=60)
        if result.returncode:
            raise RuntimeError(f'{label} failed; see stderr log')
        return result.stdout

    versions = {name: execute([getattr(args, name), '-version'], name).decode().splitlines()[0]
                for name in ('ffmpeg', 'ffprobe')}
    summary = {
        'schema': 1, 'capture_sha256': digest(manifest_path), 'versions': versions,
        'provenance': manifest['provenance'], 'runs': [],
        'limitations': [
            'Synthetic single-document fixed-size Chromium probe; not a Pi or Actual reproduction.',
            'Frame marker identity does not prove the sampled DOM state was painted.',
            'Small balanced repetitions can expose interference, not prove its absence.',
            'Only predeclared pixel regions are interpreted; no general visual Bug detection.',
        ],
    }
    frame_file = (output / 'frames.jsonl').open('x')
    try:
        for run in manifest['runs']:
            video = root / run['video']
            log_path = root / run['events']
            if digest(video) != run['videoSha256'] or digest(log_path) != run['eventsSha256']:
                raise ValueError(f'{run["id"]}: changed source')
            data = json.loads(log_path.read_text())
            probe = json.loads(execute([
                args.ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_frames',
                '-show_entries', 'frame=best_effort_timestamp_time:stream=width,height',
                '-of', 'json', str(video),
            ], run['id'] + '-probe'))
            (output / f'{run["id"]}-probe.json').write_text(json.dumps(probe) + '\n')
            stream = probe['streams'][0]
            if (stream['width'], stream['height']) != (800, 600):
                raise ValueError('Experiment expects unscaled 800x600 frames')
            times = [float(frame['best_effort_timestamp_time']) for frame in probe['frames']]
            if not times or len(times) > 1000 or any(b <= a for a, b in zip(times, times[1:])):
                raise ValueError('Missing, excessive or non-increasing video timestamps')
            raw = execute([
                args.ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-threads', '1', '-i', str(video), '-filter_complex_threads', '1',
                '-filter_complex',
                '[0:v]split=3[a][b][c];[a]crop=128:64:16:16[m];'
                '[b]crop=128:16:16:100[s];[c]crop=128:16:200:220[p];'
                '[m][s][p]vstack=inputs=3,format=rgb24[out]',
                '-map', '[out]', '-fps_mode', 'passthrough', '-threads', '1',
                '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1',
            ], run['id'] + '-decode')
            if len(raw) != FRAME_BYTES * len(times):
                raise ValueError('Decoded frames and ffprobe frame timestamps disagree')
            samples = {row['seq']: row for row in data['samples']}
            counts = Counter()
            trial_frames = {spec['id']: Counter() for spec in data['specs']}
            mismatch_frames, absent_frames, offsets, sequences = [], [], [], []
            negative_checks = False
            previous_seq = None
            for index, pts in enumerate(times):
                frame = raw[index * FRAME_BYTES:(index + 1) * FRAME_BYTES]
                trial = decode_trial(frame)
                pixels = chart_pixels(frame)
                active = trial in trial_frames
                if active:
                    trial_frames[trial][pixels] += 1
                    if pixels == 'absent':
                        absent_frames.append(index)
                seq, row, error = match_sample(frame, run['token'], samples)
                item = {'run': run['id'], 'frame': index, 'pts': pts,
                        'trial': trial, 'pixels': pixels, 'seq': seq}
                if run['mode'] == 'marker' and active:
                    counts['eligible_frames'] += 1
                    if error:
                        counts['invalid_' + error] += 1
                        item['mapping'] = error
                    else:
                        counts['mapped_frames'] += 1
                        item.update(mapping='matched', pageMs=row['pageMs'], sampledPresent=row['present'])
                        if pixels != 'unknown' and row['present'] != (pixels == 'present'):
                            counts['state_mismatch'] += 1
                            mismatch_frames.append(index)
                        if row['trial'] != trial:
                            counts['trial_mismatch'] += 1
                        if seq != previous_seq:
                            offsets.append(pts * 1000 - row['pageMs'])
                            sequences.append(seq)
                        else:
                            counts['repeated_frames'] += 1
                        previous_seq = seq
                        if not negative_checks:
                            # Run identity and checksum failures are checked on REAL decoded pixels.
                            assert decode_marker(frame, run['token'] ^ 1)[1] == 'wrong_run'
                            corrupt = bytearray(frame)
                            # Invert one payload bit (token byte) without updating its CRC.
                            bit = 16
                            x0, y0 = (bit % 16) * 8, (bit // 16) * 8
                            for y in range(y0, y0 + 8):
                                for x in range(x0, x0 + 8):
                                    for channel in range(3):
                                        offset = (y * WIDTH + x) * 3 + channel
                                        corrupt[offset] = 255 - corrupt[offset]
                            assert decode_marker(corrupt, run['token'])[1] == 'checksum'
                            # No matching record must never be converted into a fabricated timestamp.
                            assert match_sample(frame, run['token'], {})[2] == 'missing_log'
                            negative_checks = True
                frame_file.write(json.dumps(item) + '\n')
            trials = []
            for spec in data['specs']:
                events = [event for event in data['events'] if event['trial'] == spec['id']]
                removed = next((e['pageMs'] for e in events if e['type'] == 'removed'), None)
                restored = next((e['pageMs'] for e in events if e['type'] == 'restored'), None)
                trials.append({
                    **spec, 'triggered': removed is not None,
                    'actualHiddenMs': restored - removed if removed is not None and restored is not None else None,
                    'videoAbsentFrames': trial_frames[spec['id']]['absent'],
                    'videoFrames': sum(trial_frames[spec['id']].values()),
                })
            before = {v['name']: v['value'] for v in data['metricsBefore']['metrics']}
            after = {v['name']: v['value'] for v in data['metricsAfter']['metrics']}
            delta = {key: after[key] - before[key]
                     for key in ['Timestamp', 'TaskDuration', 'ScriptDuration', 'LayoutCount', 'RecalcStyleCount']}
            gaps = [b['pageMs'] - a['pageMs'] for a, b in zip(data['samples'], data['samples'][1:])]
            result = {
                'id': run['id'], 'workload': run['workload'], 'round': run['round'], 'mode': run['mode'],
                'videoSha256': run['videoSha256'], 'eventsSha256': run['eventsSha256'],
                'video_frames': len(times), 'video_span_ms': (times[-1] - times[0]) * 1000,
                'sample_count': len(samples), 'marker': dict(counts),
                'marker_unique_sequences': len(sequences),
                'offset_spread_ms': max(offsets) - min(offsets) if offsets else None,
                'sample_gap_p95_ms': quantile(gaps, .95), 'sample_gap_max_ms': max(gaps) if gaps else None,
                'probe_cost_p95_ms': quantile([s['costMs'] for s in data['samples']], .95),
                'task_ms': delta['TaskDuration'] * 1000,
                'task_ms_per_second': delta['TaskDuration'] * 1000 / delta['Timestamp'],
                'cdp_delta': delta, 'negative_checks': negative_checks, 'trials': trials,
                'mismatch_frame_indices': mismatch_frames,
            }
            if run['mode'] == 'marker' and not negative_checks:
                raise ValueError(f'{run["id"]}: no real marker decoded')
            # Save a few exact decoded original frames for human review, never composite or retouch.
            choices = []
            if run['mode'] == 'marker' and run['round'] == 1:
                if absent_frames:
                    choices.append(('absent', absent_frames[0]))
                if mismatch_frames:
                    choices.append(('mismatch', mismatch_frames[0]))
                choices.append(('last', len(times) - 1))
            for label, index in choices:
                png = output / f'{run["id"]}-{label}-frame-{index}.png'
                execute([args.ffmpeg, '-nostdin', '-v', 'error', '-threads', '1',
                         '-i', str(video), '-vf', f'select=eq(n\\,{index})',
                         '-frames:v', '1', '-threads', '1', '-n', str(png)],
                        f'{run["id"]}-{label}-png')
            if digest(video) != run['videoSha256'] or digest(log_path) != run['eventsSha256']:
                raise ValueError('Source changed during analysis')
            summary['runs'].append(result)
            print(f'{run["id"]}: frames={len(times)} marker={dict(counts)}', flush=True)
    finally:
        frame_file.close()
        (output / 'summary.partial.json').write_text(json.dumps(summary, indent=2) + '\n')
    groups = []
    for workload in ('normal', 'busy'):
        for mode in ('baseline', 'sample', 'marker'):
            runs = [r for r in summary['runs'] if r['workload'] == workload and r['mode'] == mode]
            trials = [t for r in runs for t in r['trials']]
            kinds = {}
            for kind in ('short', 'medium', 'long', 'healthy', 'race'):
                selected = [t for t in trials if t['kind'] == kind]
                durations = [t['actualHiddenMs'] for t in selected if t['actualHiddenMs'] is not None]
                kinds[kind] = {
                    'trials': len(selected), 'triggered': sum(t['triggered'] for t in selected),
                    'video_captured': sum(t['videoAbsentFrames'] > 0 for t in selected),
                    'hidden_median_ms': statistics.median(durations) if durations else None,
                    'hidden_max_ms': max(durations) if durations else None,
                }
            counts = Counter()
            for run in runs:
                counts.update(run['marker'])
            groups.append({
                'workload': workload, 'mode': mode, 'runs': len(runs), 'trials': kinds,
                'task_ms_per_second_median': statistics.median(r['task_ms_per_second'] for r in runs),
                'video_frames_median': statistics.median(r['video_frames'] for r in runs),
                'marker': dict(counts),
            })
    summary['groups'] = groups
    summary['status'] = 'completed'
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(groups, indent=2))


if __name__ == '__main__':
    main()
