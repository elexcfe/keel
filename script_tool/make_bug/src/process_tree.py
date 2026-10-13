"""Linux/WSL 子进程回收：同时覆盖 Pi 创建的新进程组。"""
import ctypes
import os
from pathlib import Path
import signal
import sys
import time


def enable():
    if not sys.platform.startswith('linux'):
        raise ValueError('当前监测执行器支持 Linux/WSL；其他平台尚未验证子进程回收。')
    libc = ctypes.CDLL(None, use_errno=True)
    # 子孙进程脱离进程组后，其父进程退出时仍由本执行器收养。
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), '无法启用子进程回收')


def info(pid):
    try:
        text = (Path('/proc') / str(pid) / 'stat').read_text()
        values = text[text.rindex(')') + 2:].split()
        return int(values[1]), values[19], values[0]  # ppid, starttime, state
    except (OSError, ValueError, IndexError):
        return None


def descendants():
    processes = {}
    for path in Path('/proc').iterdir():
        if path.name.isdigit():
            value = info(int(path.name))
            if value:
                processes[int(path.name)] = value
    owned = {os.getpid()}
    while True:
        more = {pid for pid, value in processes.items() if value[0] in owned}
        if more.issubset(owned):
            break
        owned |= more
    return {pid: processes[pid] for pid in owned if pid != os.getpid()}


def signal_owned(sig):
    for pid, old in descendants().items():
        current = info(pid)
        if current and current[1] == old[1]:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass


def stop(process):
    signal_owned(signal.SIGTERM)
    # 留时间让 Pi/场景执行器写退出记录；随后处理独立进程组和孤儿进程。
    deadline = time.monotonic() + .4
    while time.monotonic() < deadline:
        if all(value[2] == 'Z' for value in descendants().values()):
            break
        time.sleep(.02)
    signal_owned(signal.SIGKILL)
    process.wait()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        signal_owned(signal.SIGKILL)
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            return
        if not descendants():
            return
        time.sleep(.02)
