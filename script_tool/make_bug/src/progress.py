"""长命令实时输出；无总量时显示活动条，不伪造完成百分比。"""
import codecs
import os
import selectors
import signal
import subprocess
import sys
import time
from collections import deque


def run_progress(args, *, cwd=None, env=None, log=None, label='执行中', interval=None):
    stream = sys.stderr
    interactive = stream.isatty()
    interval = interval if interval is not None else (1 if interactive else 5)
    started = last_output = last_tick = time.monotonic()
    tail = deque(maxlen=32)
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    print(f'{label}：开始', file=stream, flush=True)
    process = subprocess.Popen([str(x) for x in args], cwd=cwd, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=os.environ.get('MAKE_BUG_PROCESS_GROUP') != 'inherit')
    assert process.stdout is not None
    active_line = False

    def clear():
        nonlocal active_line
        if active_line:
            stream.write('\r\033[K')
            active_line = False

    def emit(text):
        if not text:
            return
        clear()
        if log is not None:
            log.write(text)
            log.flush()
        stream.write(text)
        stream.flush()
        tail.append(text)

    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                for key, _ in selector.select(timeout=0.1):
                    chunk = os.read(key.fd, 8192)
                    if chunk:
                        emit(decoder.decode(chunk))
                        last_output = time.monotonic()
                    else:
                        selector.unregister(key.fileobj)
                        emit(decoder.decode(b'', final=True))
                now = time.monotonic()
                if now - last_tick >= interval and now - last_output >= interval:
                    position = int((now - started) / interval) % 10
                    bar = ' ' * position + '==' + ' ' * (10 - position)
                    message = f'[{bar}] {label} · 已用时 {int(now-started)}s · 等待新输出 {int(now-last_output)}s'
                    clear()
                    stream.write(('\r' if interactive else '\n') + message + ('' if interactive else '\n'))
                    stream.flush()
                    active_line = interactive
                    last_tick = now
            returncode = process.wait()
    except BaseException:
        try:
            if os.environ.get('MAKE_BUG_PROCESS_GROUP') == 'inherit':
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if os.environ.get('MAKE_BUG_PROCESS_GROUP') == 'inherit':
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise
    finally:
        process.stdout.close()
        clear()
    state = '完成' if returncode == 0 else f'失败，退出码 {returncode}'
    print(f'\n{label}：{state}，用时 {time.monotonic()-started:.1f}s', file=stream, flush=True)
    return subprocess.CompletedProcess(args, returncode, stdout=''.join(tail))
