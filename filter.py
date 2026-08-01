import os
import re
import shutil

import task_runner
from filter_rules import compile_directory_name_patterns, pattern_allows_directory_match

# 无损主音源：有这些时才允许按规则删除 MP3（避免误删 MP3-only 作品）
_LOSSLESS_PATTERN = re.compile(r'\.(?:WAV|FLAC|AIF|AIFF)\b', re.IGNORECASE)
_MP3_PATTERN = re.compile(r'\.MP3$', re.IGNORECASE)
_MP3_DIR_PATTERN = re.compile(r'MP3', re.IGNORECASE)


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


class Filter():
    def __init__(self, keyword_list: list, filter_dir: bool, logger):
        self.keyword_patterns = _compile_filter_patterns(keyword_list, logger)
        self.dir_name_patterns = compile_directory_name_patterns(keyword_list, logger)
        self.filter_dir = filter_dir
        self.logger = logger

    def _matches_file(self, path: str) -> str | None:
        for pattern, source, _allows_dir in self.keyword_patterns:
            if pattern.search(path):
                return source
        return None

    def _matches_directory(self, dir_path: str) -> str | None:
        base_name = os.path.basename(dir_path.rstrip('\\/'))
        for pattern, source in self.dir_name_patterns:
            if pattern.search(base_name):
                return source
        for pattern, source, allows_dir in self.keyword_patterns:
            if not allows_dir:
                continue
            if pattern.search(dir_path):
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
        while os.path.exists(dest):
            dest = os.path.join(parent, f'{base}_{counter}{ext}')
            counter += 1
        shutil.move(src, dest)
        self.logger.info('移出非过滤文件: [ {} ] -> [ {} ]'.format(src, dest))

    def _is_extension_only_dir_match(self, matched: str) -> bool:
        return matched.endswith(':dir')

    def _delete_directory_tree(self, dir_path: str, matched: str) -> bool:
        if not os.path.isdir(dir_path):
            return False
        task_runner.delete_file(dir_path)
        self.logger.info('过滤文件夹: [ {} ] 命中关键词： [ {} ]'.format(dir_path, matched))
        return True

    def _remove_matching_directory(self, dir_path: str, has_lossless: bool, matched: str) -> bool:
        parent = os.path.dirname(dir_path)
        hit = False
        for root, dirs, files in os.walk(dir_path, topdown=False):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                if not os.path.isfile(file_path):
                    continue
                if self._skip_mp3_without_lossless(file_path, has_lossless):
                    self._move_to_parent(file_path, parent)
                    hit = True
                    continue
                if self._matches_file(file_path):
                    task_runner.delete_file(file_path)
                    self.logger.info('过滤文件: [ {} ] 命中关键词： [ {} ]'.format(file_path, matched))
                    hit = True
                else:
                    self._move_to_parent(file_path, parent)
                    hit = True
            for dir_name in dirs:
                sub_dir = os.path.join(root, dir_name)
                try:
                    os.rmdir(sub_dir)
                except OSError:
                    pass
        if os.path.isdir(dir_path):
            task_runner.delete_file(dir_path)
            self.logger.info('过滤文件夹: [ {} ] 命中关键词： [ {} ]'.format(dir_path, matched))
            hit = True
        return hit

    def pre_filter(self, file_list: list):
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
        if not path or not os.path.exists(path):
            if path:
                self.logger.warning(f'过滤路径不存在，已跳过（不做模糊匹配）：[{path}]')
            return False

        # 先扫一遍判断是否有无损主音源；正式删除用实时 walk，
        # 删掉文件夹后从 dirs 剔除，避免对已移走的子路径再删一次误报失败。
        has_lossless = any(
            _LOSSLESS_PATTERN.search(name)
            for root, dirs, files in os.walk(path)
            for name in list(files) + list(dirs)
        )
        hit = False
        for root, dirs, files in os.walk(path, topdown=True):
            if self.filter_dir:
                for dir_name in dirs[:]:
                    dir_path = os.path.join(root, dir_name)
                    matched = self._matches_directory(dir_path)
                    if matched:
                        if self._is_extension_only_dir_match(matched):
                            if self._remove_matching_directory(dir_path, has_lossless, matched):
                                hit = True
                        elif self._delete_directory_tree(dir_path, matched):
                            hit = True
                        dirs.remove(dir_name)
            for file in files:
                file_path = os.path.join(root, file)
                if not os.path.exists(file_path):
                    continue
                if self._skip_mp3_without_lossless(file_path, has_lossless):
                    continue
                matched = self._matches_file(file_path)
                if matched:
                    task_runner.delete_file(file_path)
                    self.logger.info('过滤文件: [ {} ] 命中关键词： [ {} ]'.format(file_path, matched))
                    hit = True
        return hit
