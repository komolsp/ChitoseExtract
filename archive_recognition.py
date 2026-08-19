"""压缩包统一识别接口。

该模块只负责从路径提取“文件事实”与 7-Zip 打开策略；
任务调度、密码遍历和输出目录策略仍由原流程处理。
"""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass, replace
from enum import Enum

import file_ops


class RecognitionContext(str, Enum):
    TOP_LEVEL = 'top_level'
    NESTED = 'nested'


class ArchiveLayout(str, Enum):
    PLAIN = 'plain'
    COVERED = 'covered'
    VOLUME = 'volume'


class ArchiveEncryption(str, Enum):
    NONE = 'none'
    UNKNOWN = 'unknown'
    ENCRYPTED = 'encrypted'
    ZIP_CRYPTO = 'zipcrypto'
    WZ_AES = 'wz_aes'


class ExtractBackend(str, Enum):
    SEVEN_ZIP = 'seven_zip'
    WZ_AES = 'wz_aes'


@dataclass(frozen=True)
class ArchiveOpenStrategy:
    format_type: str | None
    covered: bool

    def as_tuple(self) -> tuple[str | None, bool]:
        return self.format_type, self.covered


@dataclass(frozen=True)
class ArchiveRecognition:
    path: str
    context: RecognitionContext
    is_candidate: bool
    extension: str
    actual_format: str | None
    layout: ArchiveLayout
    encryption: ArchiveEncryption
    backend: ExtractBackend
    volumes: tuple[str, ...]
    open_strategies: tuple[ArchiveOpenStrategy, ...]
    format_type: str | None
    covered: bool
    fingerprint: tuple[int, int] | None
    evidence: tuple[str, ...]

    @property
    def is_volume(self) -> bool:
        return self.layout is ArchiveLayout.VOLUME

    @property
    def password_required(self) -> bool:
        return self.encryption not in (
            ArchiveEncryption.NONE,
            ArchiveEncryption.UNKNOWN,
        )

    @property
    def is_direct_archive(self) -> bool:
        return self.is_candidate and self.layout is not ArchiveLayout.COVERED

    def is_format(self, format_type: str) -> bool:
        wanted = (format_type or '').lower()
        return bool(wanted) and (
            (self.actual_format or '').lower() == wanted
            or (self.format_type or '').lower() == wanted
        )

    def strategy_pairs(self) -> list[tuple[str | None, bool]]:
        return [strategy.as_tuple() for strategy in self.open_strategies]

    def matches_current_file(self, path: str) -> bool:
        if os.path.normcase(os.path.abspath(path)) != os.path.normcase(
            os.path.abspath(self.path),
        ):
            return False
        return self.fingerprint == _file_fingerprint(path)

    def with_open_result(
        self,
        *,
        format_type: str | None,
        covered: bool,
        encrypted: bool | None = None,
    ) -> 'ArchiveRecognition':
        actual_format = format_type or self.actual_format
        encryption = self.encryption
        if encrypted is True and encryption in (
            ArchiveEncryption.NONE,
            ArchiveEncryption.UNKNOWN,
        ):
            if actual_format == 'zip' and self.layout is not ArchiveLayout.COVERED:
                encryption = ArchiveEncryption.ZIP_CRYPTO
            else:
                encryption = ArchiveEncryption.ENCRYPTED
        elif encrypted is False and encryption is ArchiveEncryption.UNKNOWN:
            encryption = ArchiveEncryption.NONE

        chosen = ArchiveOpenStrategy(format_type, covered)
        strategies = [chosen]
        strategies.extend(
            strategy for strategy in self.open_strategies if strategy != chosen
        )
        layout = self.layout
        if layout is not ArchiveLayout.VOLUME:
            layout = ArchiveLayout.COVERED if covered else ArchiveLayout.PLAIN
        return replace(
            self,
            actual_format=actual_format,
            layout=layout,
            encryption=encryption,
            format_type=format_type,
            covered=covered,
            open_strategies=tuple(strategies),
        )


_EXTENSION_FORMATS = {
    '.zip': 'zip', '.cbz': 'zip', '.jar': 'zip',
    '.7z': '7z', '.cb7': '7z',
    '.rar': 'rar', '.cbr': 'rar',
    '.gz': 'gzip', '.tgz': 'gzip', '.gzip': 'gzip',
    '.bz2': 'bzip2', '.tbz2': 'bzip2',
    '.xz': 'xz', '.txz': 'xz',
    '.tar': 'tar',
}


def _file_fingerprint(path: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _zip_facts(path: str) -> tuple[bool, bool, bool]:
    """一次读取 ZIP 中央目录，返回：（是否 ZIP、是否加密、是否 WzAES）。"""
    try:
        with zipfile.ZipFile(path, 'r') as archive:
            entries = archive.infolist()
    except (OSError, zipfile.BadZipFile, RuntimeError, KeyError):
        return False, False, False
    encrypted = any(entry.flag_bits & 0x1 for entry in entries)
    uses_wz_aes = any(entry.compress_type == 99 for entry in entries)
    return True, encrypted, uses_wz_aes


def _actual_format(path: str, extension: str, probe: file_ops.ArchiveProbe) -> str | None:
    detected = file_ops.detect_leading_archive_format(path)
    if detected:
        return detected
    if file_ops.is_rar5_archive(path):
        return 'rar'
    if probe.format_type:
        return probe.format_type
    return _EXTENSION_FORMATS.get(extension)


def recognize_archive(
    path: str,
    *,
    context: RecognitionContext = RecognitionContext.TOP_LEVEL,
    volumes: list[str] | tuple[str, ...] | None = None,
) -> ArchiveRecognition:
    """识别压缩包事实并一次性生成打开策略。

    volumes 由分卷解析器传入；识别器本身不重命名或移动文件。
    """
    path = os.path.normpath(path)
    nested = context is RecognitionContext.NESTED
    raw_probe = file_ops.probe_archive(path, nested=nested)
    # 兼容旧调用方/测试中只提供 is_candidate 的轻量探测结果。
    probe = file_ops.ArchiveProbe(
        bool(getattr(raw_probe, 'is_candidate', False)),
        covered=bool(getattr(raw_probe, 'covered', False)),
        format_type=getattr(raw_probe, 'format_type', None),
    )
    extension = os.path.splitext(path)[1].lower()
    volume_paths = tuple(os.path.normpath(item) for item in (volumes or ()))
    is_volume = len(volume_paths) > 1 or file_ops.is_volume_zip(path, readonly=True)
    if is_volume and not volume_paths:
        volume_paths = (path,)

    is_candidate = probe.is_candidate or is_volume
    actual_format = _actual_format(path, extension, probe) if is_candidate else None
    covered = bool(probe.covered and not is_volume)
    layout = (
        ArchiveLayout.VOLUME
        if is_volume
        else ArchiveLayout.COVERED
        if covered
        else ArchiveLayout.PLAIN
    )

    is_zip, zip_encrypted, uses_wz_aes = (
        _zip_facts(path) if is_candidate else (False, False, False)
    )
    if is_zip:
        actual_format = 'zip'
    if uses_wz_aes:
        actual_format = actual_format or 'zip'
        encryption = ArchiveEncryption.WZ_AES
    elif zip_encrypted:
        actual_format = actual_format or 'zip'
        encryption = ArchiveEncryption.ZIP_CRYPTO
    elif actual_format == 'zip':
        encryption = ArchiveEncryption.NONE
    elif is_candidate:
        encryption = ArchiveEncryption.UNKNOWN
    else:
        encryption = ArchiveEncryption.NONE

    backend = (
        ExtractBackend.WZ_AES
        if encryption is ArchiveEncryption.WZ_AES
        and layout is ArchiveLayout.PLAIN
        else ExtractBackend.SEVEN_ZIP
    )

    strategies = ()
    if is_candidate:
        pairs = file_ops.build_archive_open_strategies(
            probe,
            extension,
            path,
            is_volume=is_volume,
        )
        strategies = tuple(
            ArchiveOpenStrategy(format_type, strategy_covered)
            for format_type, strategy_covered in pairs
        )

    evidence: list[str] = [f'extension:{extension or "(none)"}']
    leading_format = file_ops.detect_leading_archive_format(path)
    if leading_format:
        evidence.append(f'leading:{leading_format}')
    elif file_ops.is_rar5_archive(path):
        evidence.append('leading:rar5')
    if actual_format:
        evidence.append(f'format:{actual_format}')
    evidence.append(f'layout:{layout.value}')
    if encryption not in (ArchiveEncryption.NONE, ArchiveEncryption.UNKNOWN):
        evidence.append(f'encryption:{encryption.value}')

    return ArchiveRecognition(
        path=path,
        context=context,
        is_candidate=is_candidate,
        extension=extension,
        actual_format=actual_format,
        layout=layout,
        encryption=encryption,
        backend=backend,
        volumes=volume_paths,
        open_strategies=strategies,
        format_type=probe.format_type,
        covered=covered,
        fingerprint=_file_fingerprint(path),
        evidence=tuple(evidence),
    )
