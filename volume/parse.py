"""分卷文件名解析（标准命名 + 改后缀/插字）。"""

import os
import re

FUZZY_ZIP_SPLIT_RE = re.compile(r'^(?P<prefix>.+\.zip)\..+$', re.IGNORECASE)
SPLIT_7Z_RE = re.compile(r'^(?P<base>.+\.7z)\.(?P<part>\d{3}|补)$', re.IGNORECASE)
RAR_PART_RE = re.compile(r'^(?P<stem>.+)\.part(?P<part>\d+)', re.IGNORECASE)
RAR_OLDSTYLE_CONT_RE = re.compile(r'^(?P<stem>.+)\.r(?P<num>\d{2})$', re.IGNORECASE)
RAR_OLDSTYLE_FIRST_RE = re.compile(r'^(?P<stem>.+)\.rar(?P<junk>.*)$', re.IGNORECASE)
SIMPLE_NUMERIC_RE = re.compile(r'^(?P<stem>.+)\.(?P<part>\d{1,3})$')
TRAILING_NUMERIC_RE = re.compile(r'^(?P<stem>.+?)(?P<part>\d{1,3})$')
LEADING_NUMERIC_RE = re.compile(r'^(?P<part>\d{1,3})(?P<stem>.+)$')
APPENDED_SUFFIX_SPLIT_RE = re.compile(
    r'^(?P<stem>.+?)(?:\.(?:7z|zip|rar))?'
    r'\.(?P<part>\d{3})\.(?P<cover>[^.]+)$',
    re.IGNORECASE,
)
_STANDALONE_ARCHIVE_SUFFIXES = frozenset({
    '7z', 'zip', 'rar', 'gz', 'bz2', 'xz', 'tar', 'tgz', 'tbz', 'txz', 'zst',
})
_NON_VOLUME_EXTENSIONS = frozenset({
    'mp4', 'mkv', 'avi', 'wmv', 'flv', 'mov', 'webm', 'm4v', 'ts',
    'mp3', 'flac', 'wav', 'aac', 'ogg', 'm4a',
    'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg',
    'pdf', 'txt', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'epub',
    'srt', 'ass', 'vtt', 'lrc',
})


def fuzzy_zip_split_prefix(basename: str) -> str | None:
    match = FUZZY_ZIP_SPLIT_RE.match(basename)
    return match.group('prefix') if match else None


def fuzzy_digit_order(suffix: str) -> int:
    digits = re.sub(r'\D', '', suffix)
    if not digits:
        return 0
    return int(digits)


def parse_7z_split(basename: str) -> tuple[str, str] | None:
    match = SPLIT_7Z_RE.match(basename)
    if not match:
        return None
    return match.group('base'), match.group('part')


def order_7z_part(part: str) -> int:
    if part.casefold() == '补':
        return 1
    return int(part)


def parse_rar_part(basename: str) -> tuple[str, int] | None:
    match = RAR_PART_RE.match(basename)
    if not match:
        return None
    return match.group('stem'), int(match.group('part'))


def parse_rar_oldstyle(basename: str) -> tuple[str, int] | None:
    """旧式 RAR 分卷：stem.rar（首卷）+ stem.r00 / stem.r01 …"""
    if parse_rar_part(basename):
        return None
    match = RAR_OLDSTYLE_FIRST_RE.match(basename)
    if match:
        return match.group('stem'), 1
    match = RAR_OLDSTYLE_CONT_RE.match(basename)
    if match:
        return match.group('stem'), int(match.group('num')) + 2
    return None


def parse_simple_numeric(basename: str) -> tuple[str, int] | None:
    """无后缀数字分卷：stem.1 / stem.2（须同目录 ≥2 个才视为分卷）。"""
    if fuzzy_zip_split_prefix(basename):
        return None
    match = SIMPLE_NUMERIC_RE.match(basename)
    if not match:
        return None
    part = int(match.group('part'))
    if part < 1 or part > 999:
        return None
    return match.group('stem'), part


def parse_trailing_numeric(basename: str) -> tuple[str, int] | None:
    """无点号尾数分卷：测试1 / 测试2（7z 等改后缀分卷常见）。"""
    if '.' in basename:
        return None
    if parse_7z_split(basename):
        return None
    match = TRAILING_NUMERIC_RE.match(basename)
    if not match:
        return None
    stem = match.group('stem')
    if not stem or stem.isdigit():
        return None
    part = int(match.group('part'))
    if part < 1 or part > 999:
        return None
    return stem, part


def parse_leading_numeric(basename: str) -> tuple[str, int] | None:
    """无点号前置卷号分卷：2猫 / 3测试（数字在 stem 前）。"""
    if '.' in basename:
        return None
    if parse_7z_split(basename):
        return None
    if parse_trailing_numeric(basename):
        return None
    match = LEADING_NUMERIC_RE.match(basename)
    if not match:
        return None
    stem = match.group('stem')
    if not stem or stem.isdigit():
        return None
    part = int(match.group('part'))
    if part < 1 or part > 999:
        return None
    return stem, part


def parse_leading_dot_disguised(basename: str) -> tuple[str, int] | None:
    """首字符为点的混字分卷：.哈001 / .气002（对标 乌拉拉.哈001）。"""
    if not basename.startswith('.') or len(basename) < 3:
        return None
    if parse_7z_split(basename):
        return None
    tail_match = re.search(r'(\d+)$', basename)
    if not tail_match:
        if len(basename) >= 2 and re.search(r'[^\d.]', basename[1:]):
            return basename, 1
        return None
    stem = basename[:tail_match.start()]
    if len(stem) < 2 or not re.search(r'[^\d.]', stem):
        return None
    part = int(tail_match.group(1))
    if part < 1 or part > 999:
        return None
    return stem, part


def parse_disguised_split(basename: str) -> tuple[str, int] | None:
    """改后缀/插字分卷：下载.吗1对 / MAC.0删01 / 下载.part2掉 等。"""
    # Standard numeric volumes with one camouflage suffix appended, such as
    # archive.7z.001.pdf, archive.zip.002.jpg, or archive.003.txt.
    # Restrict the appended suffix to known ordinary file types to avoid
    # classifying arbitrary multi-extension files as volume parts.
    appended = APPENDED_SUFFIX_SPLIT_RE.fullmatch(basename)
    if appended and appended.group('cover').lower() in _NON_VOLUME_EXTENSIONS:
        return appended.group('stem'), int(appended.group('part'))
    if parse_rar_oldstyle(basename):
        return None
    if fuzzy_zip_split_prefix(basename):
        return None
    if '.' not in basename:
        return None
    leading_dot = parse_leading_dot_disguised(basename)
    if leading_dot:
        return leading_dot
    stem, suffix = basename.rsplit('.', 1)
    if not stem:
        return None
    if suffix.lower() in _NON_VOLUME_EXTENSIONS:
        return None
    part_match = re.match(r'^part(\d+)', suffix, re.IGNORECASE)
    if part_match:
        return stem, int(part_match.group(1))
    if re.fullmatch(r'\d{3}', suffix):
        return stem, int(suffix)
    z_match = re.fullmatch(r'z(\d{2})', suffix, re.IGNORECASE)
    if z_match:
        return stem, int(z_match.group(1)) + 1
    if suffix.lower() in _STANDALONE_ARCHIVE_SUFFIXES:
        return None
    # 混字改后缀：优先取后缀末尾卷号，避免「7z你1」被拼成 71
    if re.search(r'[^\d]', suffix):
        tail_match = re.search(r'(\d+)$', suffix)
        if tail_match:
            part = int(tail_match.group(1))
            if 1 <= part <= 999:
                return stem, part
    order = fuzzy_digit_order(suffix)
    if order > 0 and re.search(r'\d', suffix):
        return stem, order
    # 混字首卷可省略末尾卷号：乌拉拉.哈 + 乌拉拉.气002
    if re.search(r'[^\d.]', suffix) and not re.search(r'\d', suffix):
        return stem, 1
    return None


def disguised_suffix_is_implicit(basename: str) -> bool:
    """混字分卷省略了后缀卷号，须与同组其它分卷一起推断。"""
    if not basename or '.' not in basename:
        return False
    if parse_rar_oldstyle(basename) or fuzzy_zip_split_prefix(basename):
        return False
    if basename.startswith('.'):
        if re.search(r'(\d+)$', basename):
            return False
        return len(basename) >= 2 and bool(re.search(r'[^\d.]', basename[1:]))
    stem, suffix = basename.rsplit('.', 1)
    if not stem:
        return False
    if suffix.lower() in _STANDALONE_ARCHIVE_SUFFIXES:
        return False
    if suffix.lower() in _NON_VOLUME_EXTENSIONS:
        return False
    return bool(re.search(r'[^\d.]', suffix) and not re.search(r'\d', suffix))


def assign_implicit_disguised_parts(volumes: list[str]) -> list[tuple[int, str]] | None:
    """为省略卷号的混字分卷推断卷号（首卷或末卷均可省略）。"""
    entries: list[tuple[int, str, bool]] = []
    for path in volumes:
        basename = os.path.basename(path)
        parsed = parse_disguised_split(basename)
        if not parsed:
            return None
        entries.append((parsed[1], path, disguised_suffix_is_implicit(basename)))
    if not any(implicit for _, _, implicit in entries):
        entries.sort(key=lambda item: (item[0], os.path.basename(item[1]).lower()))
        return [(part, path) for part, path, _ in entries]

    explicit = [(part, path) for part, path, implicit in entries if not implicit]
    implicit_paths = [path for _, path, implicit in entries if implicit]
    used = {part for part, _ in explicit}
    result = list(explicit)
    implicit_paths.sort(key=lambda p: os.path.basename(p).lower())

    next_part = 1 if 1 not in used else max(used) + 1
    for path in implicit_paths:
        while next_part in used:
            next_part += 1
        result.append((next_part, path))
        used.add(next_part)
        next_part += 1

    result.sort(key=lambda item: (item[0], os.path.basename(item[1]).lower()))
    return result
