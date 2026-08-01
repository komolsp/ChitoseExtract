import os
import sys
import threading
import time

# 速度数值固定宽度（等宽字体下对齐，容纳 "9999.9 MB/s"）
SPEED_DISPLAY_WIDTH = 11


def drive_letter(path: str) -> str | None:
    if not path:
        return None
    path = os.path.normpath(path)
    if sys.platform == 'win32' and len(path) >= 2 and path[1] == ':':
        return path[0].upper()
    return None


def drives_from_paths(*paths: str) -> list[str]:
    drives: set[str] = set()
    for path in paths:
        if not path:
            continue
        letter = drive_letter(path)
        if letter:
            drives.add(letter)
    return sorted(drives)


def format_speed(bps: float) -> str:
    if bps is None or bps <= 0:
        return '0 B/s'
    value = float(bps)
    units = ('B/s', 'KB/s', 'MB/s', 'GB/s')
    unit_idx = 0
    while value >= 1024 and unit_idx < len(units) - 1:
        value /= 1024
        unit_idx += 1
    if unit_idx == 0:
        return f'{value:.0f} {units[unit_idx]}'
    return f'{value:.1f} {units[unit_idx]}'


def format_speed_display(bps: float) -> str:
    """固定宽度的速度字符串，供界面等宽对齐。"""
    return f'{format_speed(bps):>{SPEED_DISPLAY_WIDTH}}'


class DiskSpeedMonitor:
    """通过 Windows 性能计数器采样逻辑磁盘读写速率。"""

    def __init__(self, drives: list[str], interval: float = 0.5):
        self._drives = [drive.rstrip(':').upper() for drive in drives if drive]
        self._interval = interval
        self._callback = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self, callback):
        if sys.platform != 'win32' or not self._drives:
            return
        self._callback = callback
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name='disk-speed-monitor', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        self._callback = None

    def _run(self):
        try:
            import win32pdh
        except ImportError:
            return

        query = None
        counters: dict[str, tuple] = {}
        try:
            query = win32pdh.OpenQuery()
            for drive in self._drives:
                read_path = win32pdh.MakeCounterPath((
                    None, 'LogicalDisk', f'{drive}:', None, 0, 'Disk Read Bytes/sec',
                ))
                write_path = win32pdh.MakeCounterPath((
                    None, 'LogicalDisk', f'{drive}:', None, 0, 'Disk Write Bytes/sec',
                ))
                counters[drive] = (
                    win32pdh.AddCounter(query, read_path),
                    win32pdh.AddCounter(query, write_path),
                )

            win32pdh.CollectQueryData(query)
            time.sleep(self._interval)

            while not self._stop_event.is_set():
                win32pdh.CollectQueryData(query)
                stats: dict[str, tuple[float, float]] = {}
                for drive, (read_counter, write_counter) in counters.items():
                    try:
                        _, read_bps = win32pdh.GetFormattedCounterValue(
                            read_counter, win32pdh.PDH_FMT_DOUBLE)
                        _, write_bps = win32pdh.GetFormattedCounterValue(
                            write_counter, win32pdh.PDH_FMT_DOUBLE)
                    except win32pdh.error:
                        read_bps = 0.0
                        write_bps = 0.0
                    stats[drive] = (max(float(read_bps), 0.0), max(float(write_bps), 0.0))

                if self._callback:
                    self._callback(stats)

                if self._stop_event.wait(self._interval):
                    break
        except Exception:
            pass
        finally:
            if query is not None:
                win32pdh.CloseQuery(query)
