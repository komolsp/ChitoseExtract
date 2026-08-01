"""WAV/AIF 等无损源文件转 FLAC（优先使用内置 flac.exe，特殊 WAV 回退 ffmpeg）。"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Callable

import app_paths

_WAVE_FORMAT_PCM = 1
_WAVE_FORMAT_IEEE_FLOAT = 3

# Windows 下隐藏 flac / ffmpeg 子进程命令行窗口
_SUBPROCESS_FLAGS: dict = {}
if sys.platform == 'win32':
    _SUBPROCESS_FLAGS['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)


@dataclass(frozen=True)
class ConvertConfig:
    source_extensions: tuple[str, ...] = ('.wav', '.aif', '.aiff')
    flac_compression: int = 5
    delete_source: bool = True
    flac_path: str = ''
    ffmpeg_fallback_path: str = ''
    max_workers: int = 4


def _clamp_workers(value: int | str | None, default: int = 4) -> int:
    try:
        workers = int(value if value is not None else default)
    except (TypeError, ValueError):
        workers = default
    return max(1, min(32, workers))


def _thread_safe_log(log: Callable[[str], None] | None, lock: Lock) -> Callable[[str], None] | None:
    if log is None:
        return None

    def _log(message: str):
        with lock:
            log(message)

    return _log


def _config_value(config: ConvertConfig | dict, key: str, default='') -> str:
    if isinstance(config, dict):
        return str(config.get(key) or default)
    return str(getattr(config, key, default) or default)


def resolve_flac(custom_path: str = '') -> str | None:
    custom = (custom_path or '').strip()
    if custom:
        if os.path.isfile(custom):
            return custom
        return None
    bundled = app_paths.flac_exe()
    if bundled and os.path.isfile(bundled):
        return bundled
    return shutil.which('flac')


def resolve_ffmpeg_fallback(custom_path: str = '') -> str | None:
    custom = (custom_path or '').strip()
    if custom:
        if os.path.isfile(custom):
            return custom
        return None
    bundled = app_paths.ffmpeg_minimal_exe()
    if bundled and os.path.isfile(bundled):
        return bundled
    return shutil.which('ffmpeg')


def _wav_format_tag(path: str) -> int | None:
    try:
        with open(path, 'rb') as handle:
            if handle.read(4) != b'RIFF':
                return None
            handle.read(4)
            if handle.read(4) != b'WAVE':
                return None
            while True:
                chunk_id = handle.read(4)
                if len(chunk_id) < 4:
                    return None
                size_bytes = handle.read(4)
                if len(size_bytes) < 4:
                    return None
                chunk_size = struct.unpack('<I', size_bytes)[0]
                if chunk_id == b'fmt ':
                    header = handle.read(2)
                    if len(header) < 2:
                        return None
                    return struct.unpack('<H', header)[0]
                handle.seek(chunk_size + (chunk_size % 2), 1)
    except OSError:
        return None
    return None


def _needs_ffmpeg_for_source(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext != '.wav':
        return False
    tag = _wav_format_tag(path)
    return tag is not None and tag != _WAVE_FORMAT_PCM


def _subprocess_detail(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or '').strip()
    if not text:
        return ''
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.upper().startswith('ERROR:'):
            return line
    return lines[-1]


def find_convertible_files(root: str, extensions: tuple[str, ...]) -> list[str]:
    if not root or not os.path.isdir(root):
        return []
    ext_set = {ext.lower() for ext in extensions}
    files: list[str] = []
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            _, ext = os.path.splitext(filename)
            if ext.lower() in ext_set:
                files.append(os.path.join(dirpath, filename))
    return sorted(files)


def _convert_with_ffmpeg(
        source: str,
        temp_path: str,
        *,
        ffmpeg: str,
        compression_level: int,
        log: Callable[[str], None] | None = None,
) -> bool:
    cmd = [
        ffmpeg,
        '-hide_banner',
        '-loglevel', 'warning',
        '-y',
        '-i', source,
        '-vn',
        '-sn',
        '-dn',
        '-map_metadata', '-1',
        '-c:a', 'flac',
        '-compression_level', str(compression_level),
        temp_path,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        **_SUBPROCESS_FLAGS,
    )
    if result.returncode != 0:
        if log:
            detail = _subprocess_detail(result)
            log(
                f'ffmpeg 转换失败："{os.path.normpath(source)}"'
                + (f'：{detail}' if detail else '')
            )
        return False
    return True


def _convert_with_flac(
        source: str,
        temp_path: str,
        *,
        flac: str,
        compression_level: int,
        log: Callable[[str], None] | None = None,
) -> bool:
    level = max(0, min(int(compression_level), 12))
    cmd = [
        flac,
        '--force',
        f'--compression-level-{level}',
        '-o', temp_path,
        source,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        **_SUBPROCESS_FLAGS,
    )
    if result.returncode != 0:
        if log:
            detail = _subprocess_detail(result)
            log(
                f'flac 转换失败："{os.path.normpath(source)}"'
                + (f'：{detail}' if detail else '')
            )
        return False
    return True


def convert_to_flac(
        source: str,
        *,
        flac: str,
        ffmpeg: str | None = None,
        compression_level: int = 5,
        delete_source: bool = True,
        log: Callable[[str], None] | None = None,
) -> str | None:
    """将单个音频文件转为 FLAC，成功返回输出路径。"""
    if not os.path.isfile(source):
        return None
    base, _ = os.path.splitext(source)
    target = base + '.flac'
    if os.path.normcase(source) == os.path.normcase(target):
        return None
    if os.path.exists(target):
        try:
            if os.path.getsize(target) > 0:
                if log:
                    log(f'目标已存在，跳过转换："{os.path.normpath(target)}"')
                return target
            os.remove(target)
            if log:
                log(f'发现空目标文件，已删除并重新转换："{os.path.normpath(target)}"')
        except OSError:
            if log:
                log(f'目标已存在，跳过转换："{os.path.normpath(target)}"')
            return target

    temp_dir = os.path.dirname(source) or '.'
    fd, temp_path = tempfile.mkstemp(suffix='.flac', dir=temp_dir)
    os.close(fd)
    try:
        use_ffmpeg = _needs_ffmpeg_for_source(source)
        ok = False
        if use_ffmpeg:
            if ffmpeg:
                if log:
                    tag = _wav_format_tag(source)
                    if tag == _WAVE_FORMAT_IEEE_FLOAT:
                        log(
                            '检测到 float WAV，改用 ffmpeg 转换："{}"'.format(
                                os.path.normpath(source),
                            )
                        )
                ok = _convert_with_ffmpeg(
                    source,
                    temp_path,
                    ffmpeg=ffmpeg,
                    compression_level=compression_level,
                    log=log,
                )
            elif log:
                tag = _wav_format_tag(source)
                log(
                    'flac 不支持该 WAV 编码（format type {}），且未找到 ffmpeg："{}"'.format(
                        tag if tag is not None else '?',
                        os.path.normpath(source),
                    )
                )
        else:
            ok = _convert_with_flac(
                source,
                temp_path,
                flac=flac,
                compression_level=compression_level,
                log=log,
            )
            if not ok and ffmpeg:
                if log:
                    log(
                        'flac 转换失败，尝试 ffmpeg 回退："{}"'.format(
                            os.path.normpath(source),
                        )
                    )
                ok = _convert_with_ffmpeg(
                    source,
                    temp_path,
                    ffmpeg=ffmpeg,
                    compression_level=compression_level,
                    log=log,
                )

        if not ok:
            return None
        if delete_source:
            os.remove(source)
        shutil.move(temp_path, target)
        if log:
            log(f'已转换："{os.path.normpath(source)}" -> "{os.path.normpath(target)}"')
        return target
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def convert_work_folder(
        root: str,
        config: ConvertConfig | dict,
        *,
        log: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    """转换作品目录内全部可转换文件，返回 (成功数, 总数)。"""
    if isinstance(config, dict):
        cfg = ConvertConfig(
            source_extensions=tuple(config.get('source_extensions') or ConvertConfig.source_extensions),
            flac_compression=int(config.get('flac_compression', 5)),
            delete_source=bool(config.get('delete_source', True)),
            flac_path=_config_value(config, 'flac_path'),
            ffmpeg_fallback_path=_config_value(
                config,
                'ffmpeg_fallback_path',
                config.get('ffmpeg_path', '') if isinstance(config, dict) else '',
            ),
            max_workers=_clamp_workers(config.get('max_workers', 4)),
        )
    else:
        cfg = config

    flac_bin = resolve_flac(cfg.flac_path)
    ffmpeg_bin = resolve_ffmpeg_fallback(cfg.ffmpeg_fallback_path)
    if not flac_bin:
        if log:
            log('未找到 flac。请运行 build.py 打包内置版本，或在 config.yaml 的 audio_convert.flac_path 指定路径')
        return 0, 0

    files = find_convertible_files(root, cfg.source_extensions)
    if not files:
        return 0, 0

    needs_ffmpeg = any(_needs_ffmpeg_for_source(path) for path in files)
    if needs_ffmpeg and not ffmpeg_bin and log:
        log(
            '目录内存在 flac 不支持的 WAV（如 32-bit float），'
            '请运行 scripts/build_minimal_ffmpeg.ps1 自编译内置 ffmpeg，'
            '或在 audio_convert.ffmpeg_fallback_path 指定 ffmpeg 路径'
        )

    workers = min(_clamp_workers(cfg.max_workers), len(files))
    log_lock = Lock()
    safe_log = _thread_safe_log(log, log_lock)
    ok = 0

    def _convert_one(path: str) -> bool:
        return convert_to_flac(
            path,
            flac=flac_bin,
            ffmpeg=ffmpeg_bin,
            compression_level=cfg.flac_compression,
            delete_source=cfg.delete_source,
            log=safe_log,
        ) is not None

    if workers <= 1:
        for path in files:
            if _convert_one(path):
                ok += 1
        return ok, len(files)

    if log:
        log(f'并行转flac：{len(files)} 个文件，{workers} 线程')
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_convert_one, path) for path in files]
        for future in as_completed(futures):
            try:
                if future.result():
                    ok += 1
            except Exception as err:
                if safe_log:
                    safe_log(f'转flac线程异常：{err}')
    return ok, len(files)
