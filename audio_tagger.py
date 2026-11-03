"""从 DLsite 元数据写入 FLAC/MP3/WAV 标签并嵌入封面。"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import Lock
from typing import Callable

import requests
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TXXX
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

import file_ops
from scraper.work_metadata import WorkMetadata, CV_LIST_SEPARATOR


@dataclass(frozen=True)
class TagConfig:
    extensions: tuple[str, ...] = ('.flac', '.mp3', '.wav')
    embed_cover: bool = True
    save_cover_jpg: bool = True
    force_retag: bool = False
    cv_max_count: int = 4
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


def resolve_rj_for_folder(folder: str) -> str | None:
    rj = file_ops.parse_rj_code_for_folder(folder)
    if rj:
        return rj.upper()
    rj = file_ops.find_rj_for_folder(folder)
    return rj.upper() if rj else None


def find_taggable_files(root: str, extensions: tuple[str, ...]) -> list[str]:
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


def find_audio_subdirs(root: str, extensions: tuple[str, ...]) -> list[str]:
    dirs: list[str] = []
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            _, ext = os.path.splitext(filename)
            if ext.lower() in {e.lower() for e in extensions}:
                dirs.append(dirpath)
                break
    return sorted(set(dirs))


def _format_cv_list(cvs: list[str], max_count: int) -> str:
    items = [item.strip() for item in cvs if item and item.strip()]
    if not items:
        return ''
    if max_count > 0 and len(items) > max_count:
        items = items[:max_count] + ['他']
    return CV_LIST_SEPARATOR.join(items)


def _format_title(title: str) -> str:
    temp = title
    for chunk in re.findall(r'【.*?】', temp):
        temp = temp.replace(chunk, '')
    for left, right in (('『', '』'), ('「', '」'), ('(', ')')):
        if temp.startswith(left):
            end = temp.find(right)
            if end > 0:
                return temp[1:end].strip()
    for marker in ('』', '」'):
        if marker in temp:
            return temp[:temp.find(marker) + 1].strip()
    return temp.strip()


def build_tag_values(metadata: WorkMetadata, track_title: str, config: TagConfig) -> dict[str, str]:
    album = _format_title(metadata.get('work_name') or '')
    artist = _format_cv_list(metadata.get('cvs') or [], config.cv_max_count)
    maker = (metadata.get('maker_name') or '').strip()
    release = (metadata.get('release_date') or '').strip()
    year = release[:4] if len(release) >= 4 else ''
    tags = metadata.get('tags') or []
    genre = tags[0] if tags else ''
    series = (metadata.get('series_name') or '').strip()
    age = (metadata.get('age_category') or '').strip()

    values = {
        'title': track_title,
        'album': album,
        'artist': artist,
        'albumartist': maker,
        'date': release,
        'year': year,
        'genre': genre,
        'series': series,
        'age_category': age,
        'rjcode': (metadata.get('rjcode') or '').upper(),
        'tags': ' '.join(tags),
    }
    return values


def _download_cover(url: str, proxies=None) -> bytes:
    if not url:
        return b''
    response = requests.get(url, timeout=(10, 30), proxies=proxies)
    response.raise_for_status()
    return response.content


def _guess_mime(cover_bytes: bytes) -> str:
    if cover_bytes.startswith(b'\xff\xd8'):
        return 'image/jpeg'
    if cover_bytes.startswith(b'\x89PNG'):
        return 'image/png'
    if cover_bytes.startswith(b'RIFF') and cover_bytes[8:12] == b'WEBP':
        return 'image/webp'
    return 'image/jpeg'


# 标签写入结果：跳过（保留原标签）不等同于失败，调用方需区分以免误报步骤失败。
TAG_WRITTEN = 'written'
TAG_SKIPPED = 'skipped'
TAG_FAILED = 'failed'


def _coerce_config(config: TagConfig | dict) -> TagConfig:
    if isinstance(config, TagConfig):
        return config
    return TagConfig(
        extensions=tuple(config.get('extensions') or TagConfig.extensions),
        embed_cover=bool(config.get('embed_cover', True)),
        save_cover_jpg=bool(config.get('save_cover_jpg', True)),
        force_retag=bool(config.get('force_retag', False)),
        cv_max_count=int(config.get('cv_max_count', 4)),
        max_workers=_clamp_workers(config.get('max_workers', 4)),
    )


def _flac_has_user_tags(audio) -> bool:
    """FLAC 是否带用户标签或内嵌封面。

    不能用 len(audio)：FLAC 的 __len__ 返回元数据块数量，而任何 FLAC 都至少
    含一个 STREAMINFO 块，恒为真，会导致所有 FLAC 都被误判为「已有标签」。
    只认 PICTURE 块与 VORBIS_COMMENT 中的条目。
    """
    if getattr(audio, 'pictures', None):
        return True
    tags = getattr(audio, 'tags', None)
    if not tags:
        return False
    try:
        return len(tags) > 0
    except TypeError:
        return True


def _has_existing_tags(path: str, ext: str) -> bool:
    """判断音频是否已带标签或内嵌封面（供 force_retag=False 时保留原样）。"""
    try:
        if ext == '.flac':
            return _flac_has_user_tags(FLAC(path))
        if ext == '.mp3':
            audio = MP3(path, ID3=ID3)
        elif ext in ('.wav', '.wave'):
            audio = WAVE(path)
        else:
            return False
    except Exception:
        # 读不出来时不拦截：交给写入流程报错，避免把损坏文件当成「已有标签」跳过。
        return False
    return bool(getattr(audio, 'tags', None))


def _tag_text(value) -> list[str]:
    """把 mutagen 的文本标签统一为可比较的字符串列表。"""
    if value is None:
        return []
    value = getattr(value, 'text', value)
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _text_tag_matches(tags, key: str, expected: str) -> bool:
    actual = _tag_text(tags.get(key)) if tags is not None else []
    return actual == ([expected] if expected else [])


def _flac_tags_match(audio, values: dict[str, str], cover_bytes: bytes | None) -> bool:
    tags = getattr(audio, 'tags', None)
    fields = {
        'title': 'title',
        'album': 'album',
        'artist': 'artist',
        'albumartist': 'albumartist',
        'date': 'date',
        'year': 'year',
        'genre': 'genre',
        'series': 'series',
        'comment': 'rjcode',
        'keywords': 'tags',
    }
    if not all(
            _text_tag_matches(tags, tag_name, values[value_name])
            for tag_name, value_name in fields.items()):
        return False
    if cover_bytes is None:
        return True
    return any(
        getattr(picture, 'type', None) == 3
        and getattr(picture, 'data', None) == cover_bytes
        for picture in (getattr(audio, 'pictures', None) or [])
    )


def _id3_tags_match(audio, values: dict[str, str], cover_bytes: bytes | None) -> bool:
    tags = getattr(audio, 'tags', None)
    fields = {
        'TIT2': 'title',
        'TALB': 'album',
        'TPE1': 'artist',
        'TPE2': 'albumartist',
        'TDRC': 'year',
        'TCON': 'genre',
        'TXXX:Series': 'series',
        'TXXX:RJcode': 'rjcode',
    }
    if not all(
            _text_tag_matches(tags, tag_name, values[value_name])
            for tag_name, value_name in fields.items()):
        return False
    if cover_bytes is None:
        return True
    return any(
        getattr(picture, 'type', None) == 3
        and getattr(picture, 'data', None) == cover_bytes
        for picture in (tags.getall('APIC') if tags is not None else [])
    )


def _existing_tags_are_correct(
        path: str,
        ext: str,
        values: dict[str, str],
        cover_bytes: bytes | None,
) -> bool:
    """现有标签是否与本程序本次准备写入的字段完全一致。"""
    try:
        if ext == '.flac':
            return _flac_tags_match(FLAC(path), values, cover_bytes)
        if ext == '.mp3':
            audio = MP3(path, ID3=ID3)
        elif ext in ('.wav', '.wave'):
            audio = WAVE(path)
        else:
            return False
        return _id3_tags_match(audio, values, cover_bytes)
    except Exception:
        # 无法完成核对时不能声称标签正确；继续走写入流程，由写入阶段给出结果。
        return False


def save_cover_jpg(folder: str, cover_bytes: bytes) -> None:
    if not cover_bytes:
        return
    for name in ('cover.jpg', 'cover.webp', 'cover.png', 'folder.jpg'):
        if os.path.isfile(os.path.join(folder, name)):
            return
    path = os.path.join(folder, 'cover.jpg')
    with open(path, 'wb') as handle:
        handle.write(cover_bytes)


def _apply_flac_tags(path: str, values: dict[str, str], cover_bytes: bytes | None) -> None:
    audio = FLAC(path)
    audio.delete()
    audio['title'] = values['title']
    if values['album']:
        audio['album'] = values['album']
    if values['artist']:
        audio['artist'] = values['artist']
    if values['albumartist']:
        audio['albumartist'] = values['albumartist']
    if values['date']:
        audio['date'] = values['date']
    if values['year']:
        audio['year'] = values['year']
    if values['genre']:
        audio['genre'] = values['genre']
    if values['series']:
        audio['series'] = values['series']
    if values['rjcode']:
        audio['comment'] = values['rjcode']
    if values['tags']:
        audio['keywords'] = values['tags']
    if cover_bytes:
        picture = Picture()
        picture.type = 3
        picture.mime = _guess_mime(cover_bytes)
        picture.desc = 'Cover'
        picture.data = cover_bytes
        audio.add_picture(picture)
    audio.save()


def _populate_id3_tags(tags: ID3, values: dict[str, str], cover_bytes: bytes | None) -> None:
    tags.add(TIT2(encoding=3, text=values['title']))
    if values['album']:
        tags.add(TALB(encoding=3, text=values['album']))
    if values['artist']:
        tags.add(TPE1(encoding=3, text=values['artist']))
    if values['albumartist']:
        tags.add(TPE2(encoding=3, text=values['albumartist']))
    if values['year']:
        tags.add(TDRC(encoding=3, text=values['year']))
    if values['genre']:
        tags.add(TCON(encoding=3, text=values['genre']))
    if values['series']:
        tags.add(TXXX(encoding=3, desc='Series', text=values['series']))
    if values['rjcode']:
        tags.add(TXXX(encoding=3, desc='RJcode', text=values['rjcode']))
    if cover_bytes:
        tags.add(
            APIC(
                encoding=3,
                mime=_guess_mime(cover_bytes),
                type=3,
                desc='Cover',
                data=cover_bytes,
            )
        )


def _apply_mp3_tags(path: str, values: dict[str, str], cover_bytes: bytes | None) -> None:
    try:
        audio = MP3(path, ID3=ID3)
    except Exception:
        audio = MP3(path)
    if audio.tags is None:
        audio.add_tags()
    else:
        audio.tags.clear()

    _populate_id3_tags(audio.tags, values, cover_bytes)
    audio.save()


def _apply_wav_tags(path: str, values: dict[str, str], cover_bytes: bytes | None) -> None:
    audio = WAVE(path)
    if audio.tags is None:
        audio.add_tags()
    else:
        audio.tags.clear()

    _populate_id3_tags(audio.tags, values, cover_bytes)
    audio.save()


def _tag_file_status(
        path: str,
        metadata: WorkMetadata,
        config: TagConfig | dict,
        *,
        cover_bytes: bytes | None = None,
        log: Callable[[str], None] | None = None,
) -> str:
    """写入标签，返回 TAG_WRITTEN / TAG_SKIPPED / TAG_FAILED。"""
    cfg = _coerce_config(config)

    if not os.path.isfile(path):
        return TAG_FAILED
    _, ext = os.path.splitext(path)
    ext = ext.lower()
    if ext not in {item.lower() for item in cfg.extensions}:
        return TAG_FAILED

    track_title = os.path.splitext(os.path.basename(path))[0]
    values = build_tag_values(metadata, track_title, cfg)
    cover = cover_bytes if cfg.embed_cover else None

    # force_retag=False 时，已有标签只有在本程序负责的字段均正确时才跳过；
    # 缺失或错误的标签仍需重写，不能仅凭“存在标签”就保留错误内容。
    if not cfg.force_retag and _has_existing_tags(path, ext):
        if _existing_tags_are_correct(path, ext, values, cover):
            if log:
                log(f'已有标签且内容正确，已跳过写入："{os.path.normpath(path)}"')
            return TAG_SKIPPED
        if log:
            log(f'已有标签但内容不正确，将重新写入："{os.path.normpath(path)}"')

    try:
        if ext == '.flac':
            _apply_flac_tags(path, values, cover)
        elif ext in ('.mp3', '.wav', '.wave'):
            if ext in ('.wav', '.wave'):
                _apply_wav_tags(path, values, cover)
            else:
                _apply_mp3_tags(path, values, cover)
        else:
            return TAG_FAILED
    except Exception as err:
        if log:
            log(f'写入标签失败："{os.path.normpath(path)}"：{err}')
        return TAG_FAILED

    if log:
        log(f'已写入元数据："{os.path.normpath(path)}"')
    return TAG_WRITTEN


def tag_file(
        path: str,
        metadata: WorkMetadata,
        config: TagConfig | dict,
        *,
        cover_bytes: bytes | None = None,
        log: Callable[[str], None] | None = None,
) -> bool:
    """单个文件写入标签；跳过（保留原标签）与失败都返回 False。

    需要区分「跳过」与「失败」的调用方请用 _tag_file_status。
    """
    return _tag_file_status(
        path, metadata, config, cover_bytes=cover_bytes, log=log,
    ) == TAG_WRITTEN


def tag_work_folder(
        root: str,
        metadata: WorkMetadata,
        config: TagConfig | dict,
        *,
        cover_bytes: bytes | None = None,
        log: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    cfg = _coerce_config(config)

    if cfg.save_cover_jpg and cover_bytes:
        for folder in find_audio_subdirs(root, cfg.extensions):
            save_cover_jpg(folder, cover_bytes)

    files = find_taggable_files(root, cfg.extensions)
    if not files:
        return 0, 0

    workers = min(_clamp_workers(cfg.max_workers), len(files))
    log_lock = Lock()
    safe_log = _thread_safe_log(log, log_lock)
    ok = 0
    skipped = 0

    def _tag_one(path: str) -> str:
        return _tag_file_status(
            path, metadata, cfg, cover_bytes=cover_bytes, log=safe_log,
        )

    def _tally(status: str) -> None:
        # 保留原标签不算失败：force_retag=false 下这是预期行为，
        # 若计入失败会让整个「写入元数据」步骤被判为失败并回滚任务状态。
        nonlocal ok, skipped
        if status == TAG_WRITTEN:
            ok += 1
        elif status == TAG_SKIPPED:
            skipped += 1

    if workers <= 1:
        for path in files:
            _tally(_tag_one(path))
    else:
        if log:
            log(f'并行写入元数据：{len(files)} 个文件，{workers} 线程')
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_tag_one, path) for path in files]
            for future in as_completed(futures):
                try:
                    _tally(future.result())
                except Exception as err:
                    if safe_log:
                        safe_log(f'写入元数据线程异常：{err}')

    if skipped and safe_log:
        safe_log(
            '{} 个文件已有标签且内容正确，已跳过重复写入'.format(
                skipped)
        )
    return ok + skipped, len(files)
