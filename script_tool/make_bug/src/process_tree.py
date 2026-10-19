"""Linux/WSL 子进程回收：同时覆盖 Pi 创建的新进程组。"""
import ctypes
import os
from pathlib import Path
import signal
import sys
import subprocess
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


class Scope:
    """每个执行阶段建立范围；保护启动前已有的子进程与其后代。

    运行器须串行启动阶段；同一 Python 进程不能并发使用两个 Scope。
    Linux subreaper 收养本阶段另建 session 的孤儿。
    """
    def __init__(self):
        enable()
        self.borrowed = {pid: value[1] for pid, value in descendants().items()}
        self.seen = {}
        self.errors = []

    def register(self, process):
        value = info(process.pid)
        if value:
            self.seen[process.pid] = value[1]
        return {'pid': process.pid, 'starttime': value[1] if value else None,
                'ownership': 'started-by-this-run', 'borrowed_pids': list(self.borrowed)}

    def owned(self):
        current = descendants()
        protected = {pid for pid, value in current.items() if self.borrowed.get(pid) == value[1]}
        while True:
            more = {pid for pid, value in current.items() if value[0] in protected}
            if more.issubset(protected):
                break
            protected |= more
        result = {pid: value for pid, value in current.items() if pid not in protected}
        self.seen.update({pid: value[1] for pid, value in result.items()})
        return result

    def send(self, sig):
        for pid, old in self.owned().items():
            current = info(pid)
            if current and current[1] == old[1] and current[2] != 'Z':
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    self.errors.append(str(exc))

    def stop(self, process, grace=3):
        self.send(signal.SIGTERM)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if all(v[2] == 'Z' for v in self.owned().values()):
                break
            time.sleep(.02)
        self.send(signal.SIGKILL)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.errors.append('主进程强制退出后仍未结束')
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            self.send(signal.SIGKILL)
            for pid in list(self.seen):
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass
            if not self.owned():
                break
            time.sleep(.02)
        remaining = {pid: v[1] for pid, v in self.owned().items() if v[2] != 'Z'}
        return {'status': 'incomplete' if remaining or self.errors else 'completed',
                'owned': self.seen, 'remaining': remaining, 'errors': self.errors,
                'identity': 'Linux PID + /proc starttime; excludes pre-existing children'}
