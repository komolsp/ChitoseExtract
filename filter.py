from __future__ import annotations

import os
import re
import shutil

import file_ops
import task_runner
from filter_rules import compile_directory_name_patterns, pattern_allows_directory_match

# 无损主音源：有这些时才允许按规则删除 MP3（避免误删 MP3-only 作品）
_LOSSLESS_PATTERN = re.compile(r'\.(?:WAV|FLAC|AIF|AIFF)\b', re.IGNORECASE)
_MP3_PATTERN = re.compile(r'\.MP3$', re.IGNORECASE)
_MP3_DIR_PATTERN = re.compile(r'MP3', re.IGNORECASE)


class FilterDeleteError(RuntimeError):
    pass


def _compile_filter_patterns(keyword_list: list, logger) -> list[tuple[re.Pattern, str, bool]]:
    compiled: list[tuple[re.Pattern, str, bool]] = []
    for key in keyword_list:
        if not key:
            continue
        try:
            compiled.append((
                re.compile(key, re.IGNORECASE),
                key,
                pattern_allows_directory_match(key),
            ))
        except re.error as err:
            if logger:
                logger.warning(f'过滤规则正则无效，已跳过：[ {key} ] {err}')
    return compiled


def match_target(path: str, base: str | None) -> str:
    """把待匹配路径转换为「相对作品根」的匹配对象。

    过滤关键词只应作用于作品内部的名字。若直接匹配绝对路径，作品根之上任意一层
    目录名命中关键词（例如音声库根叫 FULL、上级分组目录叫 反転）都会让整部作品
    的每个文件都被判为过滤目标，进而整树删除。

    返回值保留一个前导分隔符，以兼容依赖 [\\/] 前缀识别文件夹名的既有规则。
    作品根自身返回空串（不参与匹配），作品根之外的路径按原样返回（防御性）。
    """
    if not base:
        return path
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        # 不同盘符等无法计算相对路径的情况，退回原路径。
        return path
    if rel == os.curdir:
        return ''
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return path
    return os.sep + rel


class Filter():
    def __init__(self, keyword_list: list, filter_dir: bool, logger):
        self.keyword_patterns = _compile_filter_patterns(keyword_list, logger)
        self.dir_name_patterns = compile_directory_name_patterns(keyword_list, logger)
        self.filter_dir = filter_dir
        self.logger = logger

    def _matches_file(self, path: str, base: str | None = None) -> str | None:
        target = match_target(path, base)
        for pattern, source, _allows_dir in self.keyword_patterns:
            if pattern.search(target):
                return source
        return None

    def _matches_directory(self, dir_path: str, base: str | None = None) -> str | None:
        base_name = os.path.basename(dir_path.rstrip('\\/'))
        for pattern, source in self.dir_name_patterns:
            if pattern.search(base_name):
                return source
        target = match_target(dir_path, base)
        for pattern, source, allows_dir in self.keyword_patterns:
            if not allows_dir:
                continue
            if pattern.search(target):
                return source
        return None

    @staticmethod
    def _scope_has_lossless(names: list[str]) -> bool:
        return any(_LOSSLESS_PATTERN.search(name) for name in names)

    @staticmethod
    def _looks_like_mp3(path: str, *, for_directory: bool = False) -> bool:
        if for_directory:
            return bool(_MP3_DIR_PATTERN.search(os.path.basename(path.rstrip('\\/'))))
        return bool(_MP3_PATTERN.search(path))

    def _skip_mp3_without_lossless(self, target_path: str, has_lossless: bool) -> bool:
        """作品目录内若无 WAV/FLAC 等无损音源，则跳过对 MP3 文件的删除。"""
        return not has_lossless and self._looks_like_mp3(target_path)

    def _move_to_parent(self, src: str, parent: str) -> None:
        name = os.path.basename(src)
        dest = os.path.join(parent, name)
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dest)):
            return
        base, ext = os.path.splitext(name)
        counter = 1
        while file_ops.path_exists(dest):
            dest = os.path.join(parent, f'{base}_{counter}{ext}')
            counter += 1
        shutil.move(src, dest)
        self.logger.info('移出非过滤文件: [ {} ] -> [ {} ]'.format(src, dest))

    def _is_extension_only_dir_match(self, matched: str) -> bool:
        return matched.endswith(':dir')

    @staticmethod
    def _delete_path(path: str) -> None:
        task_runner.delete_file(path)
        # 兼容现有删除钩子：旧实现成功时可能返回 None，以源路径是否仍存在
        # 作为最终依据；只要源仍存在，就不能把过滤记录为成功。
        # 存在性判断必须走 file_ops（\\?\ 扩展路径）：Win32 普通路径会吃掉
        # 末尾空格/点，导致漏删的文件被判为「已删除」。
        if file_ops.path_exists(path):
            raise FilterDeleteError('过滤目标删除失败：{}'.format(os.path.normpath(path)))

    def _try_delete_path(self, path: str) -> bool:
        """单目标删除失败不再中断整棵作品的过滤，只记录并继续。"""
        try:
            self._delete_path(path)
            return True
        except Exception as err:
            self.logger.error('过滤目标删除失败，已跳过：[ {} ]: {}'.format(path, err))
            return False

    def _delete_directory_tree(self, dir_path: str, matched: str) -> bool:
        if not file_ops.is_dir_path(dir_path):
            return False
        self._delete_path(dir_path)
        self.logger.info('过滤文件夹: [ {} ] 命中关键词： [ {} ]'.format(dir_path, matched))
        return True

    def _remove_matching_directory(self, dir_path: str, has_lossless: bool, matched: str,
                                   base: str | None = None) -> bool:
        parent = os.path.dirname(dir_path)
        hit = False
        for root, dirs, files in os.walk(dir_path, topdown=False):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                if not file_ops.is_file_path(file_path):
                    continue
                if self._skip_mp3_without_lossless(file_path, has_lossless):
                    self._move_to_parent(file_path, parent)
                    hit = True
                    continue
                if self._matches_file(file_path, base):
                    if self._try_delete_path(file_path):
                        self.logger.info('过滤文件: [ {} ] 命中关键词： [ {} ]'.format(file_path, matched))
                    hit = True
                else:
                    self._move_to_parent(file_path, parent)
                    hit = True
            for dir_name in dirs:
                sub_dir = os.path.join(root, dir_name)
                try:
                    os.rmdir(sub_dir)
                except OSError as err:
                    self.logger.warning('过滤文件夹残留（非空或被占用）：[ {} ]: {}'.format(sub_dir, err))
        if file_ops.is_dir_path(dir_path):
            if self._try_delete_path(dir_path):
                self.logger.info('过滤文件夹: [ {} ] 命中关键词： [ {} ]'.format(dir_path, matched))
            hit = True
        return hit

    def pre_filter(self, file_list: list):
        # file_list 是压缩包内的相对成员名，不存在祖先目录问题，保持原样匹配。
        has_lossless = self._scope_has_lossless(file_list)
        hit = False
        result_list = []
        for file in file_list:
            if self._skip_mp3_without_lossless(file, has_lossless):
                result_list.append(file)
                continue
            matched = self._matches_file(file)
            if matched:
                self.logger.info('跳过文件: [ {} ] 命中关键词： [ {} ]'.format(file, matched))
                hit = True
                continue
            result_list.append(file)
        if not hit:
            return None
        return result_list

    def post_filter(self, path):
        if not path or not file_ops.path_exists(path):
            if path:
                self.logger.warning(f'过滤路径不存在，已跳过（不做模糊匹配）：[{path}]')
            return False

        # 关键词只匹配「相对作品根」的名字：绝对路径中的祖先目录（库根、上级分组）
        # 不应触发过滤，否则上层目录名含 FULL/反転 会让整部作品被整树删除。
        base = path

        # 先扫一遍判断是否有无损主音源；正式删除用实时 walk，
        # 删掉文件夹后从 dirs 剔除，避免对已移走的子路径再删一次误报失败。
        has_lossless = any(
            _LOSSLESS_PATTERN.search(name)
            for root, dirs, files in os.walk(path)
            for name in list(files) + list(dirs)
        )
        hit = False
        failed: list[str] = []
        for root, dirs, files in os.walk(path, topdown=True):
            if self.filter_dir:
                for dir_name in dirs[:]:
                    dir_path = os.path.join(root, dir_name)
                    matched = self._matches_directory(dir_path, base)
                    if matched:
                        try:
                            if self._is_extension_only_dir_match(matched):
                                if self._remove_matching_directory(dir_path, has_lossless, matched, base):
                                    hit = True
                            elif self._delete_directory_tree(dir_path, matched):
                                hit = True
                        except Exception as err:
                            failed.append(dir_path)
                            self.logger.error('过滤文件夹失败，已跳过：[ {} ]: {}'.format(dir_path, err))
                        dirs.remove(dir_name)
            for file in files:
                file_path = os.path.join(root, file)
                if not file_ops.path_exists(file_path):
                    continue
                if self._skip_mp3_without_lossless(file_path, has_lossless):
                    continue
                matched = self._matches_file(file_path, base)
                if matched:
                    try:
                        self._delete_path(file_path)
                    except Exception as err:
                        failed.append(file_path)
                        self.logger.error('过滤文件失败，已跳过：[ {} ]: {}'.format(file_path, err))
                        continue
                    self.logger.info('过滤文件: [ {} ] 命中关键词： [ {} ]'.format(file_path, matched))
                    hit = True
        if failed:
            # 单个目标失败（例如被播放器占用）不再中断整棵作品，但仍要让用户知道
            # 哪些没删掉，避免日志显示「过滤完成」而磁盘上仍然残留。
            shown = ' | '.join(os.path.normpath(item) for item in failed[:10])
            more = '' if len(failed) <= 10 else ' …（共 {} 个）'.format(len(failed))
            self.logger.warning(
                '过滤完成，但有 {} 个目标未能删除，已跳过：[ {}{} ]'.format(len(failed), shown, more))
        return hit
