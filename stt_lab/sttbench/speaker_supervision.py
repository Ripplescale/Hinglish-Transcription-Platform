"""Process ownership and orphan prevention without any model imports."""
from __future__ import annotations
import contextlib
import os
from pathlib import Path
import threading


@contextlib.contextmanager
def job_lock(path):
    handle = Path(path).open('a+b')
    try:
        if not Path(path).stat().st_size:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        handle.close()


@contextlib.contextmanager
def parent_watchdog(parent_pid=None, exit_fn=os._exit):
    """A held Win32 process handle survives PID reuse and watches during inference."""
    pid = int(os.environ.get('STTAPP_PARENT_PID', '0')) if parent_pid is None else parent_pid
    if not pid:
        yield
        return
    stop = threading.Event()
    handle = None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            if ctypes.get_last_error() == 87:  # A process with this PID no longer exists.
                exit_fn(2)
                yield
                return
            raise RuntimeError('Could not attach the speaker worker to its parent process')

        def watch():
            while not stop.is_set():
                result = kernel.WaitForSingleObject(handle, 1000)
                if result == 0:
                    exit_fn(2)
                    return
                if result != 258:  # WAIT_TIMEOUT
                    exit_fn(2)
                    return
    else:
        def watch():
            while not stop.wait(1):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    exit_fn(2)
                    return
    thread = threading.Thread(target=watch, name='speaker-parent-watchdog', daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)
        if handle:
            kernel.CloseHandle(handle)
