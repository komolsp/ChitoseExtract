import difflib
import os
import re
import shutil
import stat
import sys
import zlib
from dataclasses import dataclass

import chardet as chardet

import pk_logger

from volume import collect as volume_collect
from volume import parse as volume_parse
from volume import resolve_volume_archives
from volume.resolver import VolumeResolver

logger = pk_logger.Pk_logger('file_ops_logger', 'log.txt').add_log_handler().get_logger()

# 套娃文件夹拍平后，空外壳的处置回调（由 task_runner 注入，移入内置回收站）。
_discard_dir_path_hook = None
# 误判解压垃圾文件的处置回调（由 task_runner 注入，逻辑删除进回收站）。
_delete_path_hook = None

# 7-Zip 可能识别的压缩包/载体扩展名（小写，含点）
ARCHIVE_EXTENSIONS = frozenset({
    '.zip', '.7z', '.rar', '.tar', '.gz', '.bz2', '.xz', '.tgz', '.tbz2', '.txz',
    '.lzma', '.cab', '.arj', '.z', '.cpio', '.deb', '.rpm', '.iso',
    '.cbz', '.cbr', '.cb7',  # 漫画包
})

# Windows PE 可执行文件；与自解压包区分，避免误解压普通 .exe
_EXE_EXTENSION = '.exe'
# SFX 压缩数据通常追加在 PE 尾部
_EXE_SFX_TAIL_SCAN_BYTES = 4 * 1024 * 1024

# 分卷后缀：.001 .002 或 .z01 等
_VOLUME_EXT_PATTERN = re.compile(r'^\.(?:z\d{2}|\d{3})$', re.IGNORECASE)
# 套娃包常见 7z 变体后缀：.7 / .7zz（完整 .7z 已在 ARCHIVE_EXTENSIONS）
_7Z_VARIANT_EXT_PATTERN = re.compile(r'^\.7(?:z{0,2})?$', re.IGNORECASE)

# 纯音频：不做内嵌扫描（音频数据里可能出现 PK 等字节，易误判）
_PURE_AUDIO_EXTENSIONS = frozenset({
    '.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.opus',
})

# 纯图片：无压缩魔数时不视为套娃压缩包（解压内容里常见封面/预览图）
_PURE_IMAGE_EXTENSIONS = frozenset({
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
})

# 纯视频：默认真媒体文件；仅当检测到压缩魔数（首部或内嵌）才视为隐写套娃
_PURE_VIDEO_EXTENSIONS = frozenset({
    '.mp4', '.mkv', '.avi', '.wmv', '.mov', '.webm',
    '.ts', '.mts', '.m2ts',
})

# APK 与 ZIP 同为 PK 头；通过 ZIP 内典型 Android 条目区分真 APK 与改后缀压缩包
_APK_EXTENSION = '.apk'
_APK_MARKER_NAMES = frozenset({
    'AndroidManifest.xml', 'classes.dex', 'resources.arsc',
})
_APK_MARKER_PREFIXES = ('META-INF/', 'lib/', 'assets/', 'res/')

# Office Open XML 文档本身是 ZIP 容器。套娃扫描若只看 PK 文件头，会把
# 台本.docx 等正常素材当作内层压缩包解开并删除原文件。
_OOXML_PACKAGE_PREFIXES = {
    '.docx': 'word/', '.docm': 'word/', '.dotx': 'word/', '.dotm': 'word/',
    '.xlsx': 'xl/', '.xlsm': 'xl/', '.xltx': 'xl/', '.xltm': 'xl/', '.xlsb': 'xl/',
    '.pptx': 'ppt/', '.pptm': 'ppt/', '.potx': 'ppt/', '.potm': 'ppt/',
    '.ppsx': 'ppt/', '.ppsm': 'ppt/', '.sldx': 'ppt/', '.sldm': 'ppt/',
}

# 7-Zip 需 -t# 解析的载体后缀（内嵌压缩或视频隐写）
_COVERED_CARRIER_EXTENSIONS = frozenset({
    '.mp4', '.mkv', '.avi', '.wmv', '.mov', '.webm',
    '.ts', '.mts', '.m2ts',
})

# 改后缀伪装 / 隐写载体：优先用 -t# 打开，避免按扩展名误判格式
_DISGUISED_ARCHIVE_EXTENSIONS = frozenset({
    '.r',  # 常见 RAR 改后缀（如 032e703d_vrn2.r）
    '.dat', '.bin', '.data', '.pack', '.pkg',
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
    '.pdf',
    '.txt', '.text', '.log',
    '.mp3', '.flac', '.m4a',
    '.dll', '.msi', '.sfx',
})
# 解压目录内的纯文本：无压缩魔数时不视为套娃，避免 テキスト.txt 等误判
_PLAIN_TEXT_DISGUISED_EXTENSIONS = frozenset({'.txt', '.text', '.log'})

# 无后缀文件也可能是套娃压缩包（7-Zip 可识别）
_EXTENSIONLESS_ARCHIVE_MIN_BYTES = 1024

# 文件头魔数（offset 0）→ 7-Zip -t 类型；None 表示交给 7-Zip 自动识别
_LEADING_SIGNATURE_FORMATS = (
    (b'PK\x03\x04', 'zip'),
    (b'PK\x05\x06', 'zip'),
    (b'PK\x07\x08', 'zip'),
    (b'7z\xbc\xaf\x27\x1c', '7z'),
    (b'Rar!\x1a\x07\x01\x00', None),  # RAR5：-trar 会失败，必须自动识别
    (b'Rar!\x1a\x07\x00', 'rar'),
    (b'Rar!', None),
    (b'\x1f\x8b', 'gzip'),
    (b'BZh', 'bzip2'),
    (b'\xfd7zXZ\x00', 'xz'),
)
_RAR5_SIGNATURE = b'Rar!\x1a\x07\x01\x00'
_LEADING_ARCHIVE_SIGNATURES = tuple(sig for sig, _ in _LEADING_SIGNATURE_FORMATS)
# DLsite 等改后缀伪装：.z7777 等会让 7-Zip 误判为 zip 分卷
_ZIP_DISGUISE_EXT_PATTERN = re.compile(r'^\.z\d+$', re.IGNORECASE)

# 容器内嵌压缩包特征（offset > 0，如前部保留 ftyp / %PDF / JPEG 头）
_EMBEDDED_ARCHIVE_SIGNATURES = _LEADING_ARCHIVE_SIGNATURES

_EMBEDDED_SCAN_LIMIT = 32 * 1024 * 1024
_EMBEDDED_SCAN_CHUNK = 256 * 1024
# 伪装 MP4 的 mdat 区：压缩魔数可能在 moov 之后很远（如 300MB+）
_MP4_MDAT_ARCHIVE_SCAN_LIMIT = 512 * 1024 * 1024
# moov 元数据里易出现 PK/1F8B 等误判字节，仅认强 archive 特征
_STRONG_ARCHIVE_SIGNATURES = (
    b'PK\x03\x04',
    b'PK\x05\x06',
    b'PK\x07\x08',
    b'7z\xbc\xaf\x27\x1c',
    b'Rar!\x1a\x07\x01\x00',
    b'Rar!\x1a\x07\x00',
    b'Rar!',
)


@dataclass(frozen=True)
class ArchiveProbe:
    is_candidate: bool
    covered: bool = False
    format_type: str | None = None  # 7-Zip -t 类型，应对改后缀导致格式误判


def _read_file_header(file_path: str) -> bytes:
    try:
        with open(file_path, 'rb') as f:
            return f.read(max(len(sig) for sig in _LEADING_ARCHIVE_SIGNATURES))
    except OSError:
        return b''


def _match_signature(header: bytes, signatures: tuple[bytes, ...]) -> bool:
    return any(header.startswith(sig) for sig in signatures)


def detect_leading_archive_format(file_path: str) -> str | None:
    """根据文件头返回 7-Zip -t 类型；RAR5 等返回 None 表示自动识别。"""
    header = _read_file_header(file_path)
    for sig, fmt in _LEADING_SIGNATURE_FORMATS:
        if header.startswith(sig):
            return fmt
    return None


def is_rar5_archive(file_path: str) -> bool:
    return _read_file_header(file_path).startswith(_RAR5_SIGNATURE)


def has_leading_archive_magic(file_path: str) -> bool:
    """文件头 offset 0 是否为已知压缩格式。"""
    header = _read_file_header(file_path)
    return any(header.startswith(sig) for sig, _ in _LEADING_SIGNATURE_FORMATS)


def _is_likely_mpeg_transport_stream(file_path: str) -> bool:
    """用连续同步包识别 MPEG-TS/M2TS，避免把码流中的随机压缩魔数当成套娃。"""
    packet_layouts = (
        (0, 188),   # MPEG-TS
        (4, 192),   # M2TS：4 字节时间戳 + 188 字节 TS 包
        (0, 204),   # 带 16 字节纠错数据的 TS
    )
    sample_size = max(offset + stride * 7 + 1 for offset, stride in packet_layouts)
    try:
        with open(file_path, 'rb') as f:
            sample = f.read(sample_size)
    except OSError:
        return False
    for offset, stride in packet_layouts:
        positions = range(offset, offset + stride * 7, stride)
        if all(pos < len(sample) and sample[pos] == 0x47 for pos in positions):
            return True
    return False


def _is_likely_video_container(file_path: str) -> bool:
    """文件头是否为常见视频容器（MP4/MKV/AVI 等），用于与隐写套娃区分。"""
    try:
        with open(file_path, 'rb') as f:
            header = f.read(12)
    except OSError:
        return False
    if len(header) >= 8 and header[4:8] == b'ftyp':
        return True
    if header.startswith(b'\x1a\x45\xdf\xa3'):
        return True
    if len(header) >= 12 and header.startswith(b'RIFF') and header[8:12] == b'AVI ':
        return True
    if _is_likely_mpeg_transport_stream(file_path):
        return True
    return False


def _archive_magic_after_ftyp_box(file_path: str) -> bool:
    """常见 .mp4 伪装：最前仅放一个 ftyp 盒子，其后紧跟 zip/rar/7z。"""
    try:
        with open(file_path, 'rb') as f:
            header = f.read(64)
    except OSError:
        return False
    if len(header) < 12 or header[4:8] != b'ftyp':
        return False
    box_size = int.from_bytes(header[0:4], 'big')
    if box_size < 8 or box_size > 1024 * 1024:
        return False
    try:
        with open(file_path, 'rb') as f:
            f.seek(box_size)
            magic = f.read(max(len(sig) for sig in _LEADING_ARCHIVE_SIGNATURES))
    except OSError:
        return False
    return _match_signature(magic, _LEADING_ARCHIVE_SIGNATURES)


def _archive_format_at_offset(file_path: str, offset: int) -> str | None:
    """返回指定偏移处的压缩格式；空字符串表示已识别但需交给 7-Zip 自动判断。"""
    try:
        with open(file_path, 'rb') as f:
            f.seek(offset)
            header = f.read(max(len(sig) for sig in _LEADING_ARCHIVE_SIGNATURES))
    except OSError:
        return None
    for sig, fmt in _LEADING_SIGNATURE_FORMATS:
        if header.startswith(sig) and _is_plausible_embedded_archive_header(file_path, offset):
            return fmt or ''
    return None


def _mp4_appended_archive_format(file_path: str) -> str | None:
    """识别追加在完整 MP4/MOV 顶层 box 后的压缩包。"""
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        return None
    try:
        with open(file_path, 'rb') as f:
            offset = 0
            for _ in range(256):
                if offset + 8 > file_size:
                    break
                f.seek(offset)
                header = f.read(16)
                if len(header) < 8:
                    break
                box_size = int.from_bytes(header[0:4], 'big')
                box_type = header[4:8]
                header_len = 8
                if box_size == 1:
                    if len(header) < 16:
                        break
                    box_size = int.from_bytes(header[8:16], 'big')
                    header_len = 16
                elif box_size == 0:
                    break
                # 首个顶层 box 必须是 MP4/MOV 的 ftyp，避免在任意文件中套用 box 解析。
                if offset == 0 and box_type != b'ftyp':
                    break
                if box_size < header_len or offset + box_size > file_size:
                    break
                offset += box_size
                if offset >= file_size:
                    break
                archive_format = _archive_format_at_offset(file_path, offset)
                if archive_format is not None:
                    return archive_format
    except OSError:
        return None
    return None


def _mp4_mdat_data_offset(file_path: str) -> int | None:
    """返回 mdat 媒体数据起始偏移；用于 moov+mdat 式伪装压缩包。"""
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        return None
    if file_size < 16:
        return None
    try:
        with open(file_path, 'rb') as f:
            offset = 0
            for _ in range(64):
                if offset + 8 > file_size:
                    break
                f.seek(offset)
                header = f.read(8)
                if len(header) < 8:
                    break
                box_size = int.from_bytes(header[0:4], 'big')
                box_type = header[4:8]
                header_len = 8
                if box_size == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        break
                    box_size = int.from_bytes(ext, 'big')
                    header_len = 16
                elif box_size == 0:
                    box_size = file_size - offset
                if box_size < header_len:
                    break
                if box_type == b'mdat':
                    return offset + header_len
                next_offset = offset + box_size
                if next_offset <= offset or next_offset > file_size:
                    break
                offset = next_offset
    except OSError:
        return None
    return None


def _region_has_strong_archive_magic(
    file_path: str,
    start: int,
    limit: int,
) -> bool:
    """在指定区间内搜索强压缩魔数（排除 moov 内 gzip 等弱特征误判）。"""
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        return False
    start = max(0, start)
    end = min(file_size, start + limit)
    if start >= end:
        return False
    max_sig = max(len(sig) for sig in _STRONG_ARCHIVE_SIGNATURES)
    overlap = max(max_sig - 1, 0)
    try:
        with open(file_path, 'rb') as f:
            f.seek(start)
            previous_tail = b''
            while f.tell() < end:
                to_read = min(_EMBEDDED_SCAN_CHUNK, end - f.tell())
                chunk = f.read(to_read)
                if not chunk:
                    break
                window = previous_tail + chunk
                for sig in _STRONG_ARCHIVE_SIGNATURES:
                    if window.find(sig) >= 0:
                        return True
                previous_tail = window[-overlap:] if overlap else b''
    except OSError:
        return False
    return False


def _mp4_mdat_looks_like_video_bitstream(file_path: str, mdat_offset: int) -> bool:
    """mdat 起始已是 H.264/HEVC 等媒体码流时，深层随机 PK/Rar 字节不应视为套娃。"""
    try:
        with open(file_path, 'rb') as f:
            f.seek(mdat_offset)
            head = f.read(256)
    except OSError:
        return False
    if len(head) < 5:
        return False
    if head.startswith(b'\x00\x00\x00\x01') or head.startswith(b'\x00\x00\x01'):
        return True
    if (
        b'Lavc' in head or b'avc' in head or b'hev' in head or b'H264' in head
        or b'x264' in head or b'h264' in head or b'H.264' in head
    ):
        return True
    if len(head) >= 6 and head[2:3] == b'\x00' and b'Lav' in head:
        return True
    # MP4 avc1：mdat 常见 4 字节 NAL 长度前缀 + NAL 头（如 000002b10605...）
    nlen = int.from_bytes(head[0:4], 'big')
    if 1 <= nlen <= 4 * 1024 * 1024 and len(head) >= nlen + 4:
        nal_type = head[4] & 0x1F
        if nal_type in (1, 2, 3, 4, 5, 6, 7, 8, 9):
            return True
    return False


def _probe_mp4_mov_stego(file_path: str, *, nested: bool) -> ArchiveProbe | None:
    """识别 ftyp+moov+mdat 式伪装压缩包；nested 内层扫描也需命中。"""
    if _archive_magic_after_ftyp_box(file_path):
        format_hint = _format_hint_for_file(file_path)
        return ArchiveProbe(True, covered=True, format_type=format_hint)
    appended_format = _mp4_appended_archive_format(file_path)
    if appended_format is not None:
        return ArchiveProbe(
            True,
            covered=True,
            format_type=appended_format or None,
        )
    mdat_offset = _mp4_mdat_data_offset(file_path)
    if mdat_offset is not None:
        if _mp4_mdat_looks_like_video_bitstream(file_path, mdat_offset):
            return ArchiveProbe(False)
        if _region_has_strong_archive_magic(
            file_path, mdat_offset, _MP4_MDAT_ARCHIVE_SCAN_LIMIT,
        ):
            format_hint = _format_hint_for_file(file_path)
            return ArchiveProbe(True, covered=True, format_type=format_hint)
        return ArchiveProbe(False)
    if not nested and _has_embedded_archive_magic(file_path):
        format_hint = _format_hint_for_file(file_path)
        return ArchiveProbe(True, covered=True, format_type=format_hint)
    return ArchiveProbe(False)


def _is_likely_image_file(file_path: str) -> bool:
    """文件头是否为常见图片格式，用于与隐写套娃 / JPEG 内嵌字节误判区分。"""
    try:
        with open(file_path, 'rb') as f:
            header = f.read(32)
    except OSError:
        return False
    if len(header) >= 3 and header[0:3] == b'\xff\xd8\xff':
        return True
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return True
    if header.startswith(b'GIF87a') or header.startswith(b'GIF89a'):
        return True
    if len(header) >= 12 and header.startswith(b'RIFF') and header[8:12] == b'WEBP':
        return True
    if header.startswith(b'BM'):
        return True
    return False


def _zip_local_entry_names(
        file_path: str, *, max_entries: int = 16, scan_bytes: int = 512 * 1024) -> list[str]:
    """从 ZIP/APK 本地文件头快速读取前若干条目名。"""
    names: list[str] = []
    try:
        file_size = os.path.getsize(file_path)
        with open(file_path, 'rb') as f:
            chunk = f.read(min(file_size, scan_bytes))
    except OSError:
        return names
    pos = 0
    while len(names) < max_entries and pos + 30 <= len(chunk):
        idx = chunk.find(b'PK\x03\x04', pos)
        if idx == -1:
            break
        if idx + 30 > len(chunk):
            break
        fn_len = chunk[idx + 26] | (chunk[idx + 27] << 8)
        extra_len = chunk[idx + 28] | (chunk[idx + 29] << 8)
        name_start = idx + 30
        name_end = name_start + fn_len
        if name_end > len(chunk):
            break
        raw_name = chunk[name_start:name_end]
        try:
            names.append(raw_name.decode('utf-8'))
        except UnicodeDecodeError:
            names.append(raw_name.decode('cp437', errors='replace'))
        comp_size = int.from_bytes(chunk[idx + 18:idx + 22], 'little')
        next_pos = name_end + extra_len + comp_size
        if next_pos <= idx + 4:
            next_pos = idx + 4
        pos = next_pos
    return names


def _name_looks_like_apk_entry(name: str) -> bool:
    if not name:
        return False
    base = name.rsplit('/', 1)[-1]
    if base in _APK_MARKER_NAMES or name in _APK_MARKER_NAMES:
        return True
    return any(name.startswith(prefix) for prefix in _APK_MARKER_PREFIXES)


def _is_likely_apk_file(file_path: str) -> bool:
    """真 Android APK（ZIP 结构 + 典型条目）；改后缀 zip/7z 不应命中。"""
    header = _read_file_header(file_path)
    if not header.startswith(b'PK'):
        return False
    for name in _zip_local_entry_names(file_path):
        if _name_looks_like_apk_entry(name):
            return True
    try:
        import zipfile
        with zipfile.ZipFile(file_path, 'r') as zf:
            for name in zf.namelist():
                if _name_looks_like_apk_entry(name):
                    return True
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError):
        return False
    return False


def _probe_apk_path(file_path: str) -> ArchiveProbe:
    """真 APK 跳过解压；7z/rar/zip 改后缀为 .apk 仍视为压缩包。"""
    if _is_likely_apk_file(file_path):
        return ArchiveProbe(False)
    if has_leading_archive_magic(file_path):
        format_hint = _format_hint_for_file(file_path)
        return ArchiveProbe(True, covered=False, format_type=format_hint)
    if _has_embedded_archive_magic(file_path):
        format_hint = _format_hint_for_file(file_path)
        return ArchiveProbe(True, covered=True, format_type=format_hint)
    return ArchiveProbe(False)


def _is_likely_ooxml_document(file_path: str, extension: str) -> bool:
    """识别合法 OOXML 文档；只有扩展名和包结构同时匹配才跳过解压。"""
    package_prefix = _OOXML_PACKAGE_PREFIXES.get(extension.lower())
    if not package_prefix or not _read_file_header(file_path).startswith(b'PK'):
        return False
    try:
        import zipfile
        with zipfile.ZipFile(file_path, 'r') as zf:
            names = {
                name.replace('\\', '/').lstrip('/').lower()
                for name in zf.namelist()
            }
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError):
        return False
    return (
        '[content_types].xml' in names
        and any(name.startswith(package_prefix) for name in names)
    )


def _is_likely_pe_executable(file_path: str) -> bool:
    """标准 Windows PE（MZ + PE\\0\\0）。"""
    try:
        with open(file_path, 'rb') as f:
            if f.read(2) != b'MZ':
                return False
            f.seek(0x3C)
            pe_offset_bytes = f.read(4)
            if len(pe_offset_bytes) < 4:
                return False
            pe_offset = int.from_bytes(pe_offset_bytes, 'little')
            if pe_offset < 64 or pe_offset > 64 * 1024 * 1024:
                return False
            f.seek(pe_offset)
            return f.read(4) == b'PE\x00\x00'
    except OSError:
        return False


def _tail_has_archive_magic(file_path: str, tail_bytes: int = _EXE_SFX_TAIL_SCAN_BYTES) -> bool:
    """扫描文件尾部 archive 魔数（PE 自解压包常见布局）。"""
    try:
        file_size = os.path.getsize(file_path)
        with open(file_path, 'rb') as f:
            f.seek(max(0, file_size - tail_bytes))
            data = f.read()
    except OSError:
        return False
    for sig in _LEADING_ARCHIVE_SIGNATURES:
        pos = data.find(sig)
        if pos >= 0:
            absolute = max(0, file_size - len(data)) + pos
            if absolute > 0:
                return True
    return False


def _probe_exe_path(file_path: str, *, nested: bool = False) -> ArchiveProbe:
    """真 PE 程序跳过；7z/rar/zip 改后缀或尾部带 SFX 的 .exe 仍视为压缩包。"""
    if _is_likely_pe_executable(file_path):
        if nested:
            return ArchiveProbe(False)
        if _tail_has_archive_magic(file_path):
            format_hint = _format_hint_for_file(file_path)
            return ArchiveProbe(True, covered=True, format_type=format_hint)
        return ArchiveProbe(False)
    if has_leading_archive_magic(file_path):
        format_hint = _format_hint_for_file(file_path)
        return ArchiveProbe(True, covered=False, format_type=format_hint)
    if not nested and _has_embedded_archive_magic(file_path):
        format_hint = _format_hint_for_file(file_path)
        return ArchiveProbe(True, covered=True, format_type=format_hint)
    return ArchiveProbe(False)


def _extension_misleads_7zip(ext: str, format_type: str) -> bool:
    """后缀与真实格式不一致，或易触发 7-Zip 按 zip 分卷解析。"""
    ext = ext.lower()
    if _ZIP_DISGUISE_EXT_PATTERN.match(ext):
        return format_type != 'zip'
    if format_type == '7z':
        return not (ext in {'.7z', '.cb7'} or _7Z_VARIANT_EXT_PATTERN.match(ext))
    if format_type == 'zip':
        return ext not in {'.zip', '.cbz', '.jar'} and not _VOLUME_EXT_PATTERN.match(ext)
    if format_type == 'rar':
        return ext not in {'.rar', '.cbr'}
    if format_type == 'gzip':
        return ext not in {'.gz', '.tgz', '.gzip'}
    if format_type == 'bzip2':
        return ext not in {'.bz2', '.tbz2'}
    if format_type == 'xz':
        return ext not in {'.xz', '.txz'}
    return True


def _format_hint_for_file(file_path: str) -> str | None:
    _, ext = os.path.splitext(file_path)
    format_type = detect_leading_archive_format(file_path)
    if not format_type:
        return None
    if _extension_misleads_7zip(ext, format_type):
        return format_type
    return None


def _has_leading_archive_magic(file_path: str) -> bool:
    """读取文件头，识别 zip / 7z / rar / gzip 等（应对改后缀伪装）。"""
    return has_leading_archive_magic(file_path)


def _has_embedded_archive_magic(file_path: str) -> bool:
    """搜索并校验内嵌压缩包头，避免把媒体码流中的随机魔数视为套娃。"""
    max_sig = max(len(sig) for sig in _EMBEDDED_ARCHIVE_SIGNATURES)
    overlap = max(max_sig - 1, 0)
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        return False
    if file_size < max_sig:
        return False

    scan_limit = min(file_size, _EMBEDDED_SCAN_LIMIT)
    # 小文件若已在头部命中，由 _has_leading_archive_magic 处理
    if scan_limit <= max_sig:
        return False
    scan_regions = [(0, scan_limit)]
    tail_start = max(scan_limit, file_size - _EMBEDDED_SCAN_LIMIT)
    if tail_start < file_size:
        scan_regions.append((tail_start, file_size))

    try:
        with open(file_path, 'rb') as f:
            for region_start, region_end in scan_regions:
                f.seek(region_start)
                offset = region_start
                previous_tail = b''
                while offset < region_end:
                    chunk = f.read(min(_EMBEDDED_SCAN_CHUNK, region_end - offset))
                    if not chunk:
                        break
                    window = previous_tail + chunk
                    for sig in _EMBEDDED_ARCHIVE_SIGNATURES:
                        search_from = 0
                        while True:
                            pos = window.find(sig, search_from)
                            if pos == -1:
                                break
                            absolute = offset - len(previous_tail) + pos
                            if (
                                absolute > 0
                                and _is_plausible_embedded_archive_header(file_path, absolute)
                            ):
                                return True
                            search_from = pos + 1
                    offset += len(chunk)
                    previous_tail = window[-overlap:] if overlap else b''
    except OSError:
        return False
    return False


def _is_plausible_embedded_archive_header(file_path: str, offset: int) -> bool:
    """对魔数后的固定头字段做廉价校验；仅用于 offset > 0 的隐写候选。"""
    try:
        file_size = os.path.getsize(file_path)
        with open(file_path, 'rb') as f:
            f.seek(offset)
            header = f.read(96)
    except OSError:
        return False

    if header.startswith(b'PK\x03\x04'):
        if len(header) < 30:
            return False
        version_needed = int.from_bytes(header[4:6], 'little')
        flags = int.from_bytes(header[6:8], 'little')
        method = int.from_bytes(header[8:10], 'little')
        name_length = int.from_bytes(header[26:28], 'little')
        extra_length = int.from_bytes(header[28:30], 'little')
        known_methods = {0, 1, 6, 8, 9, 12, 14, 93, 95, 98, 99}
        return (
            10 <= version_needed <= 63
            and not (flags & 0xC000)
            and method in known_methods
            and 0 < name_length <= 32768
            and offset + 30 + name_length + extra_length <= file_size
        )

    if header.startswith(b'PK\x05\x06'):
        if len(header) < 22:
            return False
        disk_number = int.from_bytes(header[4:6], 'little')
        central_disk = int.from_bytes(header[6:8], 'little')
        entries_on_disk = int.from_bytes(header[8:10], 'little')
        total_entries = int.from_bytes(header[10:12], 'little')
        central_size = int.from_bytes(header[12:16], 'little')
        central_offset = int.from_bytes(header[16:20], 'little')
        comment_length = int.from_bytes(header[20:22], 'little')
        return (
            disk_number == 0
            and central_disk == 0
            and entries_on_disk == total_entries
            and (total_entries == 0 or central_size > 0)
            and central_offset + central_size <= offset
            and offset + 22 + comment_length <= file_size
        )

    # 数据描述符不是一个可独立打开的 ZIP 起点，不能单凭它认定为隐写包。
    if header.startswith(b'PK\x07\x08'):
        return False

    if header.startswith(b'7z\xbc\xaf\x27\x1c'):
        if len(header) < 32:
            return False
        expected_crc = int.from_bytes(header[8:12], 'little')
        return (zlib.crc32(header[12:32]) & 0xFFFFFFFF) == expected_crc

    if header.startswith(b'Rar!\x1a\x07\x00'):
        return True
    if header.startswith(b'Rar!\x1a\x07\x01\x00'):
        return True
    if header.startswith(b'Rar!'):
        return False

    if header.startswith(b'\x1f\x8b'):
        # RFC 1952 头校验后再让 zlib 解析可选字段与首段 DEFLATE 数据。
        if len(header) < 10 or header[2] != 8 or header[3] & 0xE0:
            return False
        try:
            with open(file_path, 'rb') as f:
                f.seek(offset)
                sample = f.read(256 * 1024)
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            decoder.decompress(sample, 64 * 1024)
            return True
        except (OSError, zlib.error):
            return False

    if header.startswith(b'BZh'):
        return (
            len(header) >= 10
            and header[3:4] in b'123456789'
            and header[4:10] in (b'\x31\x41\x59\x26\x53\x59', b'\x17\x72\x45\x38\x50\x90')
        )

    if header.startswith(b'\xfd7zXZ\x00'):
        if len(header) < 12:
            return False
        expected_crc = int.from_bytes(header[8:12], 'little')
        return (zlib.crc32(header[6:8]) & 0xFFFFFFFF) == expected_crc

    return False


def is_disguised_archive_extension(ext: str) -> bool:
    return ext.lower() in _DISGUISED_ARCHIVE_EXTENSIONS


def zip_has_encrypted_entries(file_path: str) -> bool:
    """检测 ZIP 内是否有加密条目（含 WinZip AES / ZipCrypto）。"""
    try:
        import zipfile
        with zipfile.ZipFile(file_path, 'r') as zf:
            return any(info.flag_bits & 0x1 for info in zf.infolist())
    except (OSError, zipfile.BadZipFile, RuntimeError, KeyError):
        return False


def zip_smallest_probe_member(file_path: str, candidates: list[str]) -> str | None:
    """从 ZIP 目录中选最小的加密成员；无加密成员时选最小普通成员。"""
    if not candidates:
        return None
    try:
        import zipfile
        with zipfile.ZipFile(file_path, 'r') as zf:
            entries = [
                (info.filename.replace('\\', '/'), info)
                for info in zf.infolist()
                if not info.is_dir()
            ]
    except (OSError, zipfile.BadZipFile, RuntimeError, KeyError):
        return None

    matched = []
    for index, candidate in enumerate(candidates):
        normalized = candidate.replace('\\', '/')
        infos = [info for name, info in entries if name == normalized]
        if not infos and '?' in normalized:
            # get_namelist 会把空格、下划线等字符替换为 7-Zip 的单字符
            # 通配符；按相同语义匹配 ZIP 中的原始成员名。
            pattern = re.compile(
                '^' + re.escape(normalized).replace(r'\?', '[^/]') + '$',
            )
            infos = [info for name, info in entries if pattern.match(name)]
        if infos:
            matched.append((candidate, infos, index))
    if not matched:
        return None
    encrypted = [
        item for item in matched
        if any(info.flag_bits & 0x1 for info in item[1])
    ]
    pool = encrypted or matched
    return min(
        pool,
        key=lambda item: (sum(info.file_size for info in item[1]), item[2]),
    )[0]


def zip_uses_wz_aes(file_path: str) -> bool:
    """检测 ZIP 是否使用 WinZip AES（compress_type=99）；7-Zip 对此类包验密/解压不可靠。"""
    try:
        import zipfile
        with zipfile.ZipFile(file_path, 'r') as zf:
            return any(info.compress_type == 99 for info in zf.infolist())
    except (OSError, zipfile.BadZipFile, RuntimeError, KeyError):
        return False


def is_standard_archive_file(file_path: str) -> bool:
    """文件头已是压缩格式且后缀为标准压缩包（非隐写载体）。"""
    if not os.path.isfile(file_path):
        return False
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    if ext in ARCHIVE_EXTENSIONS or _VOLUME_EXT_PATTERN.match(ext) or _7Z_VARIANT_EXT_PATTERN.match(ext):
        return has_leading_archive_magic(file_path)
    return False


def should_allow_covered_open_strategy(
    file_path: str | None,
    extension: str,
) -> bool:
    """标准 .zip/.7z/.rar 等禁止 -t# covered，否则会伪列目录并在错误密码下误解压。"""
    if file_path and is_standard_archive_file(file_path):
        return False
    ext = extension.lower()
    if ext in _DISGUISED_ARCHIVE_EXTENSIONS or ext in _COVERED_CARRIER_EXTENSIONS:
        return True
    if file_path and has_leading_archive_magic(file_path):
        return False
    return True


def is_covered_extract_junk_basename(name: str) -> bool:
    """隐写/套娃载体误解压产生的无意义碎片文件名。"""
    if name in _UNNEST_IGNORED_NAMES:
        return False
    if _COVERED_JUNK_BASENAME_RE.fullmatch(name):
        return True
    if _COVERED_JUNK_ZST_RE.fullmatch(name):
        return True
    return False


def _is_disguised_archive_extension(ext: str) -> bool:
    return is_disguised_archive_extension(ext)


def build_archive_open_strategies(
    probe: ArchiveProbe,
    extension: str,
    file_path: str | None = None,
    is_volume: bool = False,
) -> list[tuple[str | None, bool]]:
    """生成 7-Zip 打开策略序列（format_type, covered），应对改后缀与隐写压缩包。"""
    ext = extension.lower()
    strategies: list[tuple[str | None, bool]] = []
    seen: set[tuple[str | None, bool]] = set()

    def add(fmt: str | None, covered: bool):
        if covered and not should_allow_covered_open_strategy(file_path, ext):
            return
        key = (fmt, covered)
        if key not in seen:
            seen.add(key)
            strategies.append(key)

    # 分卷压缩包：由 7-Zip 从首卷自动读取整组，只用普通自动识别即可。
    # 绝不能启用 covered(-t#) 或强制格式，否则会把后续分卷(.002 ...)误判成
    # 独立/隐写压缩包，导致错误“解压成功”并误删源分卷。
    if is_volume:
        add(None, False)
        return strategies

    # RAR5 改后缀（如 .r）：仅自动识别，-trar / -t# 均不可用或极慢
    if file_path and is_rar5_archive(file_path) and has_leading_archive_magic(file_path):
        add(None, False)
        return strategies

    disguised = _is_disguised_archive_extension(ext)
    if disguised and file_path and has_leading_archive_magic(file_path):
        add(None, False)
        if probe.format_type:
            add(probe.format_type, False)
        add(None, True)
        return strategies

    if disguised:
        add(None, True)
        if probe.format_type:
            add(probe.format_type, True)
            add(probe.format_type, False)
        add(None, False)
    elif probe.covered:
        add(probe.format_type, True)
        add(None, True)
        if probe.format_type:
            add(probe.format_type, False)
        add(None, False)
    elif probe.format_type:
        add(probe.format_type, False)
        add(None, False)
        add(probe.format_type, True)
        add(None, True)
    else:
        add(None, False)
        add(None, True)

    return strategies


def probe_archive(file_path: str, *, nested: bool = False) -> ArchiveProbe:
    """
    探测文件是否应交给 7-Zip 处理。

    nested: 是否为解压目录内的深层扫描。内层 JPEG 素材易含 PK 等误判字节，需更保守。

    策略：
      1. 已知压缩后缀 / 分卷命名 → 直接识别
      2. .apk → 真 Android APK（含 AndroidManifest 等）跳过；7z/rar/zip 改后缀仍识别
      3. .exe → 真 PE 程序跳过；改后缀或尾部 SFX 仍识别
      4. 其它后缀 → 先看文件头是否为压缩魔数（改后缀伪装）
      5. 仍未命中 → 对非纯音频文件扫描内嵌魔数（PDF/JPG 套娃等，需 -t#）
    """
    if not os.path.isfile(file_path):
        return ArchiveProbe(False)
    basename = os.path.basename(file_path)
    if nested and is_covered_extract_junk_basename(basename):
        return ArchiveProbe(False)
    if is_volume_zip(file_path, readonly=True):
        return ArchiveProbe(True)

    _, ext = os.path.splitext(file_path)
    ext = ext.lower()

    if _is_likely_ooxml_document(file_path, ext):
        return ArchiveProbe(False)

    if ext == _APK_EXTENSION:
        return _probe_apk_path(file_path)

    if ext == _EXE_EXTENSION:
        return _probe_exe_path(file_path, nested=nested)

    if ext in _PURE_VIDEO_EXTENSIONS:
        # 改后缀：文件头直接为 zip/rar 时优先识别。
        if has_leading_archive_magic(file_path):
            format_hint = _format_hint_for_file(file_path)
            return ArchiveProbe(True, covered=False, format_type=format_hint)
        if _is_likely_video_container(file_path):
            if ext in {'.mp4', '.mov'}:
                return _probe_mp4_mov_stego(file_path, nested=nested)
            # 对真实媒体仅接受通过格式头结构校验的内嵌包；同时扫描首尾区域，
            # 既过滤码流随机撞码，也保留追加式隐写压缩包识别。
            if _has_embedded_archive_magic(file_path):
                format_hint = _format_hint_for_file(file_path)
                return ArchiveProbe(True, covered=True, format_type=format_hint)
            return ArchiveProbe(False)
        # 无视频容器头、仅改后缀：顶层仍可扫描内嵌魔数；包内素材保守跳过。
        if not nested and _has_embedded_archive_magic(file_path):
            format_hint = _format_hint_for_file(file_path)
            return ArchiveProbe(True, covered=True, format_type=format_hint)
        return ArchiveProbe(False)

    if ext in _PURE_IMAGE_EXTENSIONS:
        if _is_likely_image_file(file_path):
            if has_leading_archive_magic(file_path):
                format_hint = _format_hint_for_file(file_path)
                return ArchiveProbe(True, covered=False, format_type=format_hint)
            # 解压目录内的真实图片（JPEG/GIF/PNG 等）：数据区易出现 PK 等字节，不做内嵌扫描以免误判。
            if nested:
                return ArchiveProbe(False)
            # 顶层拖入的改后缀套娃：仍检测内嵌压缩魔数。
            if _has_embedded_archive_magic(file_path):
                format_hint = _format_hint_for_file(file_path)
                return ArchiveProbe(True, covered=True, format_type=format_hint)
            return ArchiveProbe(False)

    if ext in ARCHIVE_EXTENSIONS:
        return ArchiveProbe(True, covered=ext in _COVERED_CARRIER_EXTENSIONS)
    if _VOLUME_EXT_PATTERN.match(ext):
        return ArchiveProbe(True)
    if _7Z_VARIANT_EXT_PATTERN.match(ext):
        return ArchiveProbe(True)

    if _is_disguised_archive_extension(ext):
        try:
            if os.path.getsize(file_path) < _EXTENSIONLESS_ARCHIVE_MIN_BYTES:
                return ArchiveProbe(False)
        except OSError:
            return ArchiveProbe(False)
        if is_rar5_archive(file_path):
            return ArchiveProbe(True, covered=False, format_type=None)
        format_hint = _format_hint_for_file(file_path) if has_leading_archive_magic(file_path) else None
        if has_leading_archive_magic(file_path):
            return ArchiveProbe(True, covered=False, format_type=format_hint)
        if nested and ext in _PURE_IMAGE_EXTENSIONS and _is_likely_image_file(file_path):
            return ArchiveProbe(False)
        if _has_embedded_archive_magic(file_path):
            return ArchiveProbe(True, covered=True, format_type=format_hint)
        # 常见纯音频素材若无压缩魔数，视为普通文件而非套娃。
        if ext in _PURE_AUDIO_EXTENSIONS:
            return ArchiveProbe(False)
        # 解压目录内的说明文本：无魔数时按普通文件处理。
        if nested and ext in _PLAIN_TEXT_DISGUISED_EXTENSIONS:
            return ArchiveProbe(False)
        return ArchiveProbe(True, covered=True)

    format_hint = _format_hint_for_file(file_path)

    if not ext:
        try:
            if os.path.getsize(file_path) < _EXTENSIONLESS_ARCHIVE_MIN_BYTES:
                return ArchiveProbe(False)
        except OSError:
            return ArchiveProbe(False)
        basename = os.path.basename(file_path)
        if volume_parse.parse_trailing_numeric(basename) or volume_parse.parse_leading_numeric(basename):
            from volume.collect import peek_cross_stem_group
            if peek_cross_stem_group(os.path.dirname(file_path), file_path):
                return ArchiveProbe(True, format_type=format_hint)
        if _has_leading_archive_magic(file_path):
            return ArchiveProbe(True, format_type=format_hint)
        if _has_embedded_archive_magic(file_path):
            return ArchiveProbe(True, covered=True)
        return ArchiveProbe(False)

    if _has_leading_archive_magic(file_path):
        return ArchiveProbe(True, format_type=format_hint)

    if ext in _PURE_AUDIO_EXTENSIONS:
        return ArchiveProbe(False)

    if nested and ext in _PURE_IMAGE_EXTENSIONS and _is_likely_image_file(file_path):
        return ArchiveProbe(False)

    if _has_embedded_archive_magic(file_path):
        return ArchiveProbe(True, covered=True)

    return ArchiveProbe(False)


RJ_CODE_PATTERN = re.compile(r'[RBV]J(\d{6}|\d{8})(?!\d+)', re.IGNORECASE)
# 8 位优先于 6 位，避免 12345678 被拆成 123456
BARE_RJ_DIGITS_PATTERN = re.compile(r'(?<!\d)(\d{8}|\d{6})(?!\d)')
_BARE_RJ_MIN_AUDIO_FILES = 2
_RJ_SCAN_MAX_DEPTH = 8


def is_audio_file_path(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower() in _PURE_AUDIO_EXTENSIONS


def count_audio_files_in_paths(paths: list[str]) -> int:
    return sum(1 for path in paths if path and is_audio_file_path(path))


def count_audio_files_in_directory(root: str, max_depth: int = _RJ_SCAN_MAX_DEPTH) -> int:
    if not root or not is_dir_path(root):
        return 0
    root = os.path.normpath(root)
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == '.' else rel.count(os.sep) + 1
        if depth > max_depth:
            dirnames.clear()
            continue
        for filename in filenames:
            if is_audio_file_path(join_dir(dirpath, filename)):
                count += 1
    return count


def allow_bare_rj_digit_match(
        *,
        directory_roots: list[str] | None = None,
        file_paths: list[str] | None = None,
) -> bool:
    """裸数字 RJ 识别：仅当同目录/压缩包内存在多个音频文件时启用（避免日期等误判）。"""
    for root in directory_roots or []:
        if root and is_dir_path(root):
            if count_audio_files_in_directory(root) >= _BARE_RJ_MIN_AUDIO_FILES:
                return True
    if count_audio_files_in_paths(file_paths or []) >= _BARE_RJ_MIN_AUDIO_FILES:
        return True
    return False


def parse_rj_code(text: str, *, allow_bare: bool = False) -> str | None:
    """从文本中提取 RJ 号：默认仅匹配 RJ/BJ/VJ 前缀；裸数字需显式 allow_bare=True。"""
    for code, _source in _iter_rj_codes_in_text(text, allow_bare=allow_bare):
        return code
    return None


def parse_rj_code_for_folder(folder_path: str) -> str | None:
    """从文件夹名解析 RJ 号；裸数字仅当该文件夹内音频文件 ≥2 时启用。"""
    if not folder_path:
        return None
    basename = os.path.basename(folder_path.rstrip(' \\')).rstrip(' .')
    allow_bare = allow_bare_rj_digit_match(directory_roots=[folder_path])
    return parse_rj_code(basename, allow_bare=allow_bare)


def has_prefixed_rj_in_text(text: str) -> bool:
    """文本中是否含 RJ/BJ/VJ 前缀作品号（不含裸数字）。"""
    for _code, source in _iter_rj_codes_in_text(text, allow_bare=False):
        if source == 'prefixed':
            return True
    return False


def _iter_rj_codes_in_text(text: str, *, allow_bare: bool = True):
    """从文本中依次产出 (RJ号, 来源)；来源为 prefixed 或 bare。"""
    if not text:
        return
    prefixed_codes: set[str] = set()
    for match in RJ_CODE_PATTERN.finditer(text):
        code = match.group().upper()
        prefixed_codes.add(code)
        yield code, 'prefixed'
    if not allow_bare:
        return
    for match in BARE_RJ_DIGITS_PATTERN.finditer(text):
        code = f'RJ{match.group(1)}'
        if code not in prefixed_codes:
            yield code, 'bare'


def rj_match_source(text: str, rjcode: str) -> str:
    """判断 rjcode 在 text 中是否由 RJ/BJ/VJ 前缀匹配得到；否则为 bare。"""
    if not text or not rjcode:
        return 'bare'
    target = rjcode.upper()
    for code, source in _iter_rj_codes_in_text(text):
        if code == target:
            return source
    return 'bare'


def _score_rj_candidate(scores: dict[str, tuple[int, str]], code: str, source: str, weight: int):
    if not code:
        return
    prev_score, prev_source = scores.get(code, (0, source))
    merged_source = 'prefixed' if prev_source == 'prefixed' or source == 'prefixed' else 'bare'
    scores[code] = (prev_score + weight, merged_source)


def _sorted_rj_candidates(scores: dict[str, tuple[int, str]]) -> list[tuple[str, str, int]]:
    items = [(code, source, score) for code, (score, source) in scores.items()]
    # 带 RJ/BJ/VJ 前缀的候选始终优先于纯数字匹配，再按得分排序
    items.sort(key=lambda item: (item[1] != 'prefixed', -item[2], item[0]))
    return items


_RJ_CONTENT_SCAN_EXTENSIONS = frozenset({
    '.txt', '.html', '.htm', '.url', '.ini', '.json', '.xml', '.md', '.nfo',
    '.lrc', '.srt', '.ass', '.csv', '.log', '.yaml', '.yml', '.cfg', '.conf',
})
_RJ_CONTENT_SCAN_MAX_BYTES = 256 * 1024


def find_rj_candidates_in_names(
        names: list[str], *, allow_bare: bool = False) -> list[tuple[str, str, int]]:
    """从文件名/路径列表中提取 RJ 候选，按得分降序返回 (code, source, score)。"""
    scores: dict[str, tuple[int, str]] = {}
    for name in names:
        if not name:
            continue
        base = os.path.basename(name.replace('/', '\\'))
        for code, source in _iter_rj_codes_in_text(base, allow_bare=allow_bare):
            _score_rj_candidate(scores, code, source, 2)
        for code, source in _iter_rj_codes_in_text(name, allow_bare=allow_bare):
            _score_rj_candidate(scores, code, source, 1)
    return _sorted_rj_candidates(scores)


def strip_rj_from_basename(basename: str) -> str:
    """从文件夹名中移除 RJ 号（含 [RJxxxxxx]、裸 RJ 号及独立 6/8 位数字）。"""
    name = basename
    name = re.sub(r'\[[RBV]J\d{6,8}\]', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\[\d{6,8}\]', '', name)
    name = RJ_CODE_PATTERN.sub('', name)
    name = BARE_RJ_DIGITS_PATTERN.sub('', name)
    name = re.sub(r'\[\s*\]', '', name)
    name = re.sub(r'\s{2,}', ' ', name)
    return name.strip(' .')


def _scan_text_file_for_rj(file_path: str, *, allow_bare: bool = False) -> list[tuple[str, str]]:
    try:
        size = os.path.getsize(file_path)
    except OSError:
        return []
    if size <= 0 or size > _RJ_CONTENT_SCAN_MAX_BYTES:
        return []
    try:
        with open(file_path, 'rb') as f:
            data = f.read()
    except OSError:
        return []
    for encoding in ('utf-8', 'gbk', 'cp932', 'latin-1'):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        hits = list(_iter_rj_codes_in_text(text, allow_bare=allow_bare))
        if hits:
            return hits
    return []


def find_rj_candidates_in_directory(
        root: str, max_depth: int = _RJ_SCAN_MAX_DEPTH,
        *, allow_bare: bool = False) -> list[tuple[str, str, int]]:
    """
    在解压后的目录中深度寻找 RJ 候选：
      1. 所有文件/文件夹名
      2. 常见文本文件内容（readme、lrc、html 等）
    """
    if not root or not is_dir_path(root):
        return []

    root = os.path.normpath(root)
    scores: dict[str, tuple[int, str]] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == '.' else rel.count(os.sep) + 1
        if depth > max_depth:
            dirnames.clear()
            continue

        shallow_bonus = max(3 - depth, 1)
        for name in dirnames + filenames:
            for code, source in _iter_rj_codes_in_text(name.rstrip(' .'), allow_bare=allow_bare):
                _score_rj_candidate(scores, code, source, shallow_bonus)

        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            file_path = join_dir(dirpath, filename)
            if ext in _RJ_CONTENT_SCAN_EXTENSIONS or ext == '':
                for code, source in _scan_text_file_for_rj(file_path, allow_bare=allow_bare):
                    _score_rj_candidate(scores, code, source, 2)

    return _sorted_rj_candidates(scores)


_FOLDER_ICON_RJ_RE = re.compile(r'^@folder-icon-([RBV]J\d{6,8})\.ico$', re.IGNORECASE)


def find_rj_from_folder_icon(folder_path: str) -> str | None:
    """从文件夹封面图标文件名提取 RJ 号。"""
    if not folder_path or not is_dir_path(folder_path):
        return None
    try:
        for name in os.listdir(folder_path):
            match = _FOLDER_ICON_RJ_RE.match(name)
            if match:
                return match.group(1).upper()
    except OSError:
        return None
    return None


def find_rj_candidates_for_folder(
        folder_path: str, *, allow_bare: bool | None = None) -> list[tuple[str, str, int]]:
    """从文件夹内推断 RJ 候选：优先封面图标，其次扫描目录内容。"""
    if allow_bare is None:
        allow_bare = allow_bare_rj_digit_match(directory_roots=[folder_path])
    scores: dict[str, tuple[int, str]] = {}
    icon = find_rj_from_folder_icon(folder_path)
    if icon:
        _score_rj_candidate(scores, icon, 'prefixed', 10)
    for code, source, score in find_rj_candidates_in_directory(
            folder_path, max_depth=2, allow_bare=allow_bare):
        _score_rj_candidate(scores, code, source, score)
    return _sorted_rj_candidates(scores)


def find_rj_for_folder(folder_path: str) -> str | None:
    """从文件夹内推断 RJ 号：优先封面图标，其次扫描目录内容。"""
    allow_bare = allow_bare_rj_digit_match(directory_roots=[folder_path])
    candidates = find_rj_candidates_for_folder(folder_path, allow_bare=allow_bare)
    return candidates[0][0] if candidates else None


def build_rj_prefixed_basename(basename: str, rj: str) -> str:
    rj = rj.upper()
    name = basename.rstrip(' \\').rstrip(' .')
    if has_prefixed_rj_in_text(name):
        return name
    return f'[{rj}]{name}'


def restore_rj_prefix(folder_path: str) -> tuple[str | None, str | None]:
    """
    为缺少 RJ 前缀的文件夹恢复 [RJ] 前缀。
    返回 (新路径, 错误信息)；成功时错误为 None，失败时新路径为 None。
    """
    if not folder_path or not is_dir_path(folder_path):
        return None, '路径不是文件夹'

    basename = os.path.basename(folder_path.rstrip(' \\')).rstrip(' .')
    if has_prefixed_rj_in_text(basename):
        return folder_path, None

    rj = find_rj_for_folder(folder_path)
    if not rj:
        return None, '未找到 RJ 号（无封面图标且目录内无匹配）'

    dirname = os.path.dirname(folder_path)
    new_basename = build_rj_prefixed_basename(basename, rj)
    new_path = os.path.join(dirname, new_basename)
    if os.path.normcase(folder_path) == os.path.normcase(new_path):
        return folder_path, None
    if path_exists(new_path):
        return None, f'目标已存在：{new_basename}'
    if not safe_rename_path(folder_path, new_path):
        return None, '重命名失败'
    return new_path, None


def restore_rj_in_library(library_root: str) -> list[tuple[str, str | None, str | None]]:
    """
    扫描音声库顶层文件夹，为缺失 RJ 前缀的作品恢复 [RJ] 前缀。
    返回 [(原路径, 新路径或 None, 错误信息或 None), ...]
    """
    results: list[tuple[str, str | None, str | None]] = []
    if not library_root or not is_dir_path(library_root):
        return results

    try:
        entries = os.listdir(library_root)
    except OSError as err:
        logger.error(f'无法读取音声库目录：{library_root}: {err}')
        return results

    for name in sorted(entries):
        folder_path = os.path.join(library_root, name)
        if not is_dir_path(folder_path):
            continue
        if has_prefixed_rj_in_text(name.rstrip(' .')):
            continue
        new_path, error = restore_rj_prefix(folder_path)
        results.append((folder_path, new_path, error))
    return results


def mk_if_not_exit(path):  # 若文件夹不存在则创建
    if not os.path.exists(path):
        os.makedirs(path)


def is_volume_zip(file_name, *, readonly=False):  # 判断是否是分卷压缩
    if readonly:
        return VolumeResolver.is_volume_readonly(file_name)
    return VolumeResolver.is_volume(file_name)


# 找到属于同分卷压缩包的所有分卷
def volume_zip_list(file_path):
    volumes = resolve_volume_archives(file_path)
    if volumes:
        return volumes
    from volume.resolver import VolumeResolver
    peeked = VolumeResolver.peek_volumes(file_path)
    if peeked:
        return peeked
    basename = os.path.basename(file_path)
    if volume_parse.parse_7z_split(basename):
        dirname = os.path.dirname(file_path)
        single = volume_collect.collect_7z(dirname, file_path)
        return single if single else [file_path]
    return None


def encode_detect(str_name):
    # 分别尝试用GBK和UTF8解码文件名
    try:
        encode_name = str_name.encode('gbk')
    except UnicodeEncodeError:
        encode_name = str_name.encode('utf-8')
    # 检测可能的正确编码
    result = chardet.detect(encode_name)
    if not result['encoding']:
        return False
    return result['encoding'] == 'SHIFT_JIS'


def get_similar(path):  # 获得与输入路径相似文件路径
    if os.path.exists(path + "(1)"):
        return path + "(1)"
    filename, _ = os.path.splitext(path)
    if os.path.exists(filename):
        return filename
    father, name = os.path.split(path)  # 所在文件夹
    try:
        files = os.listdir(father)
    except OSError:
        return None
    max_similar = 0  # 相似度最高值
    result = None
    for file in files:
        file_path = os.path.join(father, file)
        similar = difflib.SequenceMatcher(None, name, file).quick_ratio()
        if similar > 0.9 and similar > max_similar:
            max_similar = similar
            result = file_path
    # 只返回相似路径不返回相同路径
    return result if not result == path else None


def get_similar_path(path):
    path_list = path.split('\\')
    new_path = path_list[0] + '\\'
    for item in path_list[1:]:
        temp = os.path.join(new_path, item)
        if '?' not in item:
            new_path = temp

        similar = get_similar(temp)
        if similar:
            new_path = similar

    return new_path if not new_path == path else None


_UNNEST_IGNORED_NAMES = frozenset({'desktop.ini', 'Thumbs.db', '.DS_Store'})
_UNNEST_IGNORED_PREFIXES = ('@folder-icon-',)
# macOS 压缩包附带的元数据目录，不是套娃外壳；拍平会把 ._ 垃圾文件散落到作品根目录
_UNNEST_JUNK_DIR_NAMES = frozenset({'__MACOSX'})
# 隐写/套娃载体（-t#）误解析时 7-Zip 常解出的无意义碎片名
_COVERED_JUNK_BASENAME_RE = re.compile(r'^\d{1,4}$')
_COVERED_JUNK_ZST_RE = re.compile(r'^\d{1,4}\.zst$', re.IGNORECASE)


def _absolute_path_preserve_trailing(path: str) -> str:
    """拼绝对路径，不调用 abspath/normpath，避免吃掉末尾空格/点。"""
    path = path.replace('/', '\\')
    if len(path) >= 2 and path[1] == ':':
        return path
    cwd = os.getcwd().replace('/', '\\').rstrip('\\')
    rel = path.lstrip('\\')
    return f'{cwd}\\{rel}' if rel else cwd


def _extended_path(path: str) -> str:
    """Windows 长路径前缀，保留末尾空格/点。"""
    if not path:
        return path
    if path.startswith('\\\\?\\'):
        return path
    path = path.replace('/', '\\')
    if path.startswith('\\\\'):
        return '\\\\?\\UNC\\' + path[2:]
    if not (len(path) >= 2 and path[1] == ':'):
        path = _absolute_path_preserve_trailing(path)
    return '\\\\?\\' + path


def _win_file_attributes(path: str) -> int:
    import win32api
    return win32api.GetFileAttributes(_extended_path(path))


def _needs_trailing_space_fix(name: str) -> bool:
    stripped = name.rstrip(' .')
    return bool(stripped) and stripped != name


def safe_rename_path(src: str, dest: str) -> bool:
    """重命名路径；Windows 下用扩展路径以支持末尾空格/点。"""
    last_err: BaseException | None = None
    if sys.platform == 'win32':
        try:
            import win32file
            win32file.MoveFile(_extended_path(src), _extended_path(dest))
            return True
        except Exception as err:
            last_err = err
            logger.debug('扩展路径重命名失败 [{}] -> [{}]: {}'.format(src, dest, err))
    try:
        os.rename(src, dest)
        return True
    except OSError as err:
        last_err = err
        logger.debug('os.rename 失败 [{}] -> [{}]: {}'.format(src, dest, err))
    if last_err is not None:
        logger.warning('重命名失败 [{}] -> [{}]: {}'.format(src, dest, last_err))
    return False


def is_cross_drive_path(src: str, dest: str) -> bool:
    """Windows/UNC 路径是否跨卷；跨卷时 rename/MoveFile 必然失败。"""
    src_drive = os.path.splitdrive(os.path.abspath(src))[0]
    dest_drive = os.path.splitdrive(os.path.abspath(dest))[0]
    return bool(
        src_drive
        and dest_drive
        and os.path.normcase(src_drive) != os.path.normcase(dest_drive)
    )


def move_into_directory(src: str, dest_dir: str) -> str | None:
    """将文件或文件夹移入目标目录；重名时自动追加 (1)。"""
    if not src or not dest_dir or not path_exists(src):
        return None
    mk_if_not_exit(dest_dir)
    if not is_dir_path(dest_dir):
        return None
    basename = os.path.basename(src.rstrip(' \\'))
    dest = join_dir(dest_dir, basename)
    src_norm = os.path.normcase(os.path.normpath(src))
    if src_norm == os.path.normcase(os.path.normpath(dest)):
        return src
    if is_dir_path(src):
        while path_exists(dest):
            dest += '(1)'
    else:
        base, ext = os.path.splitext(dest)
        while path_exists(dest):
            dest = base + '(1)' + ext
    # 跨盘时 rename/MoveFile 必然返回 WinError 17，直接走复制+删除，
    # 避免把正常降级记录成“重命名失败”警告。
    if not is_cross_drive_path(src, dest) and safe_rename_path(src, dest):
        return dest
    # 同盘 rename/MoveFile 无法跨盘移动（资源库常在另一分区），
    # 回退到 shutil.move 走“复制+删除”，实现跨盘搬运。
    try:
        shutil.move(src, dest)
        return dest
    except Exception as err:
        logger.error('跨盘移动失败 [{}] -> [{}]: {}'.format(src, dest, err))
    return None


def path_exists(path: str) -> bool:
    if sys.platform == 'win32':
        try:
            _win_file_attributes(path)
            return True
        except Exception:
            return False
    return os.path.exists(path)


def is_dir_path(path: str) -> bool:
    if sys.platform == 'win32':
        try:
            import win32con
            attrs = _win_file_attributes(path)
            return bool(attrs & win32con.FILE_ATTRIBUTE_DIRECTORY)
        except Exception:
            return False
    return os.path.isdir(path)


def is_path_under(root: str, path: str) -> bool:
    """path 是否位于 root 之下（含 root 自身）。"""
    if not root or not path:
        return False
    try:
        root_norm = os.path.normpath(os.path.abspath(root))
        path_norm = os.path.normpath(os.path.abspath(path))
        return os.path.commonpath([root_norm, path_norm]) == root_norm
    except ValueError:
        return False


def is_file_path(path: str) -> bool:
    if sys.platform == 'win32':
        try:
            import win32con
            attrs = _win_file_attributes(path)
            return not bool(attrs & win32con.FILE_ATTRIBUTE_DIRECTORY)
        except Exception:
            return False
    return os.path.isfile(path)


def join_dir(parent: str, name: str) -> str:
    return os.path.join(parent, name)


def list_dir_names(dir_path: str) -> list[str]:
    """列出目录内容。"""
    if not is_dir_path(dir_path):
        return []
    try:
        return os.listdir(dir_path)
    except OSError:
        return []


def _unnest_visible_entries(dir_path: str) -> list[str]:
    names = list_dir_names(dir_path)
    visible = []
    for name in names:
        if name in _UNNEST_IGNORED_NAMES:
            continue
        if name.startswith(_UNNEST_IGNORED_PREFIXES):
            continue
        visible.append(name)
    return visible


def _dir_has_direct_files(dir_path: str) -> bool:
    for name in _unnest_visible_entries(dir_path):
        if is_file_path(join_dir(dir_path, name)):
            return True
    return False


def set_discard_dir_path_hook(handler):
    """注册套娃空文件夹移除时的处置函数（如移入回收站）。"""
    global _discard_dir_path_hook
    _discard_dir_path_hook = handler


def set_delete_path_hook(handler):
    """注册文件删除处置函数（误判解压垃圾等，逻辑删除进回收站）。"""
    global _delete_path_hook
    _delete_path_hook = handler


def _delete_file_path(file_path: str):
    if _delete_path_hook:
        try:
            _delete_path_hook(file_path)
            return
        except Exception as err:
            logger.error('删除文件失败，尝试直接删除：[{}]: {}'.format(file_path, err))
    clear_shell_folder_attributes(file_path)
    try:
        os.remove(file_path)
    except OSError as err:
        logger.warning('删除文件失败：[{}]: {}'.format(file_path, err))


def _is_covered_extract_junk_file(file_path: str) -> bool:
    """判断是否为隐写/套娃载体误解压产生的垃圾碎片文件。"""
    if not is_file_path(file_path):
        return False
    name = os.path.basename(file_path.rstrip(' \\'))
    return is_covered_extract_junk_basename(name)


def cleanup_covered_extract_junk(root: str) -> int:
    """移除误判解压残留的垃圾文件（如 1、2.zst），返回删除数量。"""
    if not root or not is_dir_path(root):
        return 0
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames
            if name.upper() not in _UNNEST_JUNK_DIR_NAMES
        ]
        for name in list(filenames):
            path = join_dir(dirpath, name)
            if not _is_covered_extract_junk_file(path):
                continue
            logger.info('移除误判解压垃圾文件：[{}]'.format(os.path.normpath(path)))
            _delete_file_path(path)
            removed += 1
    return removed


def _is_junk_metadata_dir(dir_path: str) -> bool:
    base = os.path.basename(dir_path.rstrip(' \\'))
    return base.upper() in _UNNEST_JUNK_DIR_NAMES


def _purge_junk_metadata_dirs(root: str):
    """移除 macOS 元数据目录（如 __MACOSX），避免被误判为套娃拍平。"""
    if not is_dir_path(root):
        return
    for dirpath, dirnames in _walk_dirpaths_bottom_up(root):
        for name in list(dirnames):
            child = join_dir(dirpath, name)
            if is_dir_path(child) and _is_junk_metadata_dir(child):
                logger.info('移除 macOS 元数据文件夹：[{}]'.format(os.path.normpath(child)))
                clear_shell_folder_attributes(child)
                _remove_dir_path(child)


def is_wrapper_dir(dir_path: str) -> bool:
    """
    可拍平的套娃目录：自身没有直接文件，且仅含唯一一个子文件夹。
    含多个子文件夹或含直接文件的目录一律不视为套娃，内部结构保持不变。
    """
    if not is_dir_path(dir_path):
        return False
    if _is_junk_metadata_dir(dir_path):
        return False
    if _dir_has_direct_files(dir_path):
        return False
    entries = _unnest_visible_entries(dir_path)
    if len(entries) != 1:
        return False
    only = join_dir(dir_path, entries[0])
    return is_dir_path(only)


def _hard_remove_dir_path(dir_path: str):
    clear_shell_folder_attributes(dir_path)
    shutil.rmtree(dir_path, ignore_errors=True)


def _remove_dir_path(dir_path: str):
    if _discard_dir_path_hook:
        try:
            _discard_dir_path_hook(dir_path)
            return
        except Exception as err:
            logger.error('套娃文件夹移入回收站失败，改为直接删除：[{}]: {}'.format(dir_path, err))
    _hard_remove_dir_path(dir_path)


def _hoist_wrapper_dir(wrapper: str, parent: str):
    logger.info('移除套娃文件夹：[{}] 内容上移至 [{}]'.format(
        os.path.normpath(wrapper), os.path.normpath(parent)))
    if not is_dir_path(wrapper) or not path_exists(wrapper):
        return
    wrapper_norm = os.path.normcase(os.path.normpath(wrapper))
    for item in list_dir_names(wrapper):
        src = join_dir(wrapper, item)
        dest = join_dir(parent, item)
        if is_dir_path(src) and path_exists(dest):
            dest_norm = os.path.normcase(os.path.normpath(dest))
            if dest_norm == wrapper_norm:
                _hoist_wrapper_dir(src, parent)
                clear_shell_folder_attributes(src)
                if is_dir_path(src):
                    _remove_dir_path(src)
                continue
        while path_exists(dest):
            dest += '(1)'
        if sys.platform == 'win32':
            try:
                import win32file
                win32file.MoveFile(_extended_path(src), _extended_path(dest))
                continue
            except OSError:
                pass
        shutil.move(src, dest)
    clear_shell_folder_attributes(wrapper)
    if is_dir_path(wrapper):
        _remove_dir_path(wrapper)


def _walk_dirpaths_bottom_up(root: str) -> list[tuple[str, list[str]]]:
    """基于 listdir 的深度优先遍历，兼容末尾带空格/点的文件夹名。"""
    collected: list[tuple[str, list[str]]] = []

    def visit(dirpath: str):
        names = _unnest_visible_entries(dirpath)
        subdirs = []
        for name in names:
            child = join_dir(dirpath, name)
            if is_dir_path(child):
                subdirs.append(name)
                visit(child)
        collected.append((dirpath, subdirs))

    visit(root)
    collected.reverse()
    return collected


def normalize_trailing_space_dirnames(root: str):
    """去掉文件夹名末尾空格/点，避免 Windows 无法访问或删除。"""
    if not is_dir_path(root):
        return
    for dirpath, dirnames in _walk_dirpaths_bottom_up(root):
        for name in list(dirnames):
            if not _needs_trailing_space_fix(name):
                continue
            src = join_dir(dirpath, name)
            if not path_exists(src):
                continue
            stripped = name.rstrip(' .')
            dest = join_dir(dirpath, stripped)
            suffix = 1
            while path_exists(dest):
                dest = join_dir(dirpath, f'{stripped}_{suffix}')
                suffix += 1
            if safe_rename_path(src, dest):
                logger.info('规范化文件夹名：[{}] -> [{}]'.format(name, os.path.basename(dest)))
            else:
                logger.warning('规范化文件夹名失败：[{}]'.format(src))


def flatten_wrapper_dirs(work_root: str) -> str:
    """
    拍平套娃目录：仅折叠 work_root 下连续的顶层套娃层（自身无直接文件、仅一个子文件夹）。
    不递归处理作品内部的单层子目录，避免误拍平 Freetalk/ 等内容结构。
    返回拍平后的根目录路径。
    """
    if not is_dir_path(work_root):
        return work_root
    current = work_root
    normalize_trailing_space_dirnames(current)
    _purge_junk_metadata_dirs(current)
    current = collapse_top_wrapper(current)
    _purge_junk_metadata_dirs(current)
    cleanup_covered_extract_junk(current)
    normalize_trailing_space_dirnames(current)
    return current


def collapse_top_wrapper(root: str) -> str:
    """折叠 work_root 下连续的顶层套娃层（自身无直接文件、仅一个子文件夹）。"""
    if not is_dir_path(root):
        return root
    guard = 0
    while guard < 64:
        guard += 1
        normalize_trailing_space_dirnames(root)
        entries = _unnest_visible_entries(root)
        if len(entries) != 1:
            break
        only = join_dir(root, entries[0])
        if not is_wrapper_dir(only):
            break
        _hoist_wrapper_dir(only, root)
    return root


def clear_shell_folder_attributes(path: str):
    """清除文件夹封面产生的只读/隐藏/系统属性，便于移动或删除。"""
    if not path or not path_exists(path):
        return
    if sys.platform == 'win32':
        try:
            import win32api
            import win32con
        except ImportError:
            win32api = None
        if win32api:
            clear_mask = ~(win32con.FILE_ATTRIBUTE_READONLY
                           | win32con.FILE_ATTRIBUTE_HIDDEN
                           | win32con.FILE_ATTRIBUTE_SYSTEM)

            def _clear_one(target: str):
                try:
                    attrs = win32api.GetFileAttributes(_extended_path(target))
                    win32api.SetFileAttributes(_extended_path(target), attrs & clear_mask)
                except OSError:
                    pass

            if is_dir_path(path):
                for root, dirs, files in os.walk(path):
                    for entry in files + dirs:
                        _clear_one(join_dir(root, entry))
            _clear_one(path)
            return
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for name in files:
                try:
                    os.chmod(os.path.join(root, name), stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
