import logging
import os
import re
import sys
import ctypes
from pathlib import Path
from datetime import datetime

from requests.exceptions import RequestException, ConnectionError, HTTPError, Timeout

from scanner import Scanner
from scraper import WorkMetadata, Scraper
from scraper.work_metadata import CV_LIST_SEPARATOR
from ostool import move_folder, copy_with_symlink, normalize_path

import win32api
import win32con

import file_ops
# Windows 系统的保留字符
# https://docs.microsoft.com/zh-cn/windows/win32/fileio/naming-a-file
# <（小于）
# >（大于）
# ： (冒号)
# "（双引号）
# /（正斜杠）
# \ (反反)
# | (竖线或竖线)
# ? （问号）
# * (星号)
WINDOWS_RESERVED_CHARACTER_PATTERN = re.compile(r'[\\/*?:"<>|]')
WINDOWS_RESERVED_CHARACTER_PATTERN_str = r'\/:*?"<>|'  # 半角字符，原
WINDOWS_RESERVED_CHARACTER_PATTERN_replace_str = '＼／：＊？＂＜＞｜'  # 全角字符，替
WINDOWS_RESERVED_CHARACTER_IGNORE_SLASH_PATTERN = re.compile(r'[*?:"<>|]')
WINDOWS_RESERVED_CHARACTER_IGNORE_SLASH_PATTERN_str = r':*?"<>|'  # 半角字符，原
WINDOWS_RESERVED_CHARACTER_IGNORE_SLASH_PATTERN_replace_str = '：＊？＂＜＞｜'  # 全角字符，替


class RenameDuplicateError(Exception):
    """音声库中已存在同名作品目录，无法重命名。"""

    def __init__(self, rjcode: str, source_path: str, existing_path: str):
        self.rjcode = rjcode
        self.source_path = source_path
        self.existing_path = existing_path
        super().__init__(rjcode, source_path, existing_path)


def _get_logger():
    logger = logging.getLogger('Renamer')
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


from scraper.rjcode_locales import RJCODE_DISPLAY_LOCALES, normalize_display_locales, normalize_workno


def detect_rjcode_wrap_style(template: str) -> str:
    if re.search(r'\[rjcode\]', template or ''):
        return 'square'
    if re.search(r'\(rjcode\)', template or ''):
        return 'round'
    return 'none'


def format_rjcode_replacement(
        metadata: WorkMetadata,
        *,
        display_locales: list[str] | tuple[str, ...] | None,
        delimiter: str,
        wrap_style: str,
) -> str:
    selected = normalize_display_locales(display_locales)
    if not selected:
        primary = normalize_workno(metadata.get('rjcode'))
        return primary or ''

    by_locale = metadata.get('rjcodes_by_locale') or {}
    codes: list[str] = []
    seen: set[str] = set()
    for locale in RJCODE_DISPLAY_LOCALES:
        if locale not in selected:
            continue
        code = normalize_workno(by_locale.get(locale))
        if code and code not in seen:
            codes.append(code)
            seen.add(code)
    if not codes:
        primary = normalize_workno(metadata.get('rjcode'))
        return primary or ''
    if len(codes) == 1:
        return codes[0]
    if wrap_style == 'square':
        return ']['.join(codes)
    if wrap_style == 'round':
        return ')('.join(codes)
    return delimiter.join(codes)


class Renamer(object):
    logger = _get_logger()

    def __init__(
            self,
            scanner: Scanner,
            scraper: Scraper,
            template: str,  # 模板
            # https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes
            release_date_format: str,  # 日期格式
            delimiter,  # 列表转字符串的分隔符
            cv_list_left, # CV列表的左侧分隔符
            cv_list_right, # CV列表的右侧分隔符
            tags_list_left, # 标签列表的左侧包裹符
            tags_list_right, # 标签列表的右侧包裹符
            exclude_square_brackets_in_work_name_flag,  # 设为 True 时，移除 work_name 中【】及其间的内容
            renamer_illegal_character_to_full_width_flag,  # 设为 True 时，新文件名将非法字符转为全角；为 False 时直接移除.
            make_folder_icon, # 设为 True 时，将会下载作品封面并将其设为文件夹封面
            remove_jpg_file, # 设为 True 时，将会保留下载的作品封面
            tags_option,  # 标签相关设置
            # 年龄分级相关配置
            age_cat_map_gen: str,
            age_cat_map_r15: str,
            age_cat_map_r18: str,
            age_cat_left: str,
            age_cat_right: str,
            age_cat_ignore_r18: bool,
            series_name_left: str,
            series_name_right: str,
            rjcode_display_locales: list[str],
            mode: str,  # RENAME/MOVE/LINK
            move_root: str,
            move_template: str
    ):
        self.__scanner = scanner
        self.__scraper = scraper
        self.__template = template
        self.__release_date_format = release_date_format
        self.__delimiter = delimiter
        self.__cv_list_left = cv_list_left
        self.__cv_list_right = cv_list_right
        self.__tags_list_left = tags_list_left
        self.__tags_list_right = tags_list_right
        self.__exclude_square_brackets_in_work_name_flag = exclude_square_brackets_in_work_name_flag
        self.__renamer_illegal_character_to_full_width_flag = renamer_illegal_character_to_full_width_flag
        self.__make_folder_icon = make_folder_icon
        self.__remove_jpg_file = remove_jpg_file
        self.__tags_option = tags_option
        self.__age_cat_map_gen = age_cat_map_gen
        self.__age_cat_map_r15 = age_cat_map_r15
        self.__age_cat_map_r18 = age_cat_map_r18
        self.__age_cat_left = age_cat_left
        self.__age_cat_right = age_cat_right
        self.__age_cat_ignore_r18 = age_cat_ignore_r18
        self.__series_name_left = series_name_left
        self.__series_name_right = series_name_right
        self.__rjcode_display_locales = list(rjcode_display_locales or [])
        self.__mode = mode
        self.__move_root = move_root
        self.__move_template = move_template

    def __format_filename_str(self, name: str):
        if name:
            if self.__renamer_illegal_character_to_full_width_flag:  # 半角转全角
                name = name.translate(name.maketrans(
                    WINDOWS_RESERVED_CHARACTER_PATTERN_str, WINDOWS_RESERVED_CHARACTER_PATTERN_replace_str))
            else:  # 直接移除
                name = WINDOWS_RESERVED_CHARACTER_PATTERN.sub('', name)
            return name.strip()
        else:
            return name


    def __compile_new_name(self, metadata: WorkMetadata):
        """
        根据作品的元数据编写出新的文件名
        """
        if self.__mode == 'RENAME':
            template = self.__template
            template = self.__format_filename_str(template)
        else:
            template = self.__move_template
            if self.__renamer_illegal_character_to_full_width_flag:  # 半角转全角
                template = template.translate(template.maketrans(
                    WINDOWS_RESERVED_CHARACTER_IGNORE_SLASH_PATTERN_str, WINDOWS_RESERVED_CHARACTER_IGNORE_SLASH_PATTERN_replace_str))
            else:  # 直接移除
                template = WINDOWS_RESERVED_CHARACTER_IGNORE_SLASH_PATTERN.sub('', template)
            template = template.strip()

        work_name = self.__format_filename_str(metadata['work_name'])
        if self.__exclude_square_brackets_in_work_name_flag:
            work_name = re.sub(r'【.*?】', '', work_name).strip()
        maker_name = self.__format_filename_str(metadata['maker_name'])
        series_name = self.__format_filename_str(metadata['series_name'])

        new_name = template.replace('work_name', work_name)
        new_name = new_name.replace('maker_id', metadata['maker_id'])
        new_name = new_name.replace('maker_name', maker_name)
        if 'rjcode' in template:
            rjcode_str = format_rjcode_replacement(
                metadata,
                display_locales=self.__rjcode_display_locales,
                delimiter=self.__delimiter,
                wrap_style=detect_rjcode_wrap_style(template),
            )
            new_name = new_name.replace('rjcode', rjcode_str)
        if 'age_cat' in template:
            if self.__age_cat_ignore_r18 and metadata['age_category'] == 'R18':
                new_name = new_name.replace('age_cat', "")
            else:
                if metadata['age_category'] == 'GEN':
                    age_cat = self.__age_cat_map_gen
                elif metadata['age_category'] == 'R15':
                    age_cat = self.__age_cat_map_r15
                else:
                    age_cat = self.__age_cat_map_r18
                new_name = new_name.replace('age_cat', self.__age_cat_left + age_cat + self.__age_cat_right)
        if 'series_name' in template:
            if series_name:
                new_name = new_name.replace('series_name', self.__series_name_left + series_name + self.__series_name_right)
            else:
                new_name = new_name.replace('series_name', '')
        if 'release_date' in template:
            release_date_obj = datetime.strptime(metadata['release_date'], '%Y-%m-%d').date()
            new_name = new_name.replace('release_date', release_date_obj.strftime(self.__release_date_format))

        cv_list = list(map(self.__format_filename_str, metadata['cvs']))  # cv列表
        cv_list_str = self.__cv_list_left + CV_LIST_SEPARATOR.join(cv_list) + self.__cv_list_right if len(cv_list) > 0 else ''
        new_name = new_name.replace('cv_list_str', cv_list_str)

        if "tags_list_str" in template:  # 标签列表
            tags_list = []
            tags_list_flag = []
            for i in self.__tags_option['ordered_list']:  # ordered_list中存在的标签
                if isinstance(i, str) and i in metadata['tags']:
                    tags_list.append(i)
                    tags_list_flag.append(i)
                elif isinstance(i, list) and i[0] in metadata['tags']:
                    tags_list.append(i[1])  # 替换新标签
                    tags_list_flag.append(i[0])
            for i in metadata['tags']:  # 剩余的标签
                if not i in tags_list_flag:
                    tags_list.append(i)
            tags_list = tags_list[: self.__tags_option['max_number']]  # 数量限制
            tags_list = list(map(self.__format_filename_str, tags_list))
            tags_list_str = (
                self.__tags_list_left + self.__delimiter.join(tags_list) + self.__tags_list_right
                if tags_list else ''
            )
            new_name = new_name.replace('tags_list_str', tags_list_str)

        # 占位符为空时（如无系列名的 [series_name]）移除残留空方括号
        new_name = re.sub(r'\[\s*\]', '', new_name)
        new_name = re.sub(r'\(\s*\)', '', new_name)
        new_name = re.sub(r'\s{2,}', ' ', new_name)

        # 文件名中不能包含 Windows 系统的保留字符
        if self.__mode == 'RENAME':
            if self.__renamer_illegal_character_to_full_width_flag:  # 半角转全角
                new_name = new_name.translate(new_name.maketrans(
                    WINDOWS_RESERVED_CHARACTER_PATTERN_str, WINDOWS_RESERVED_CHARACTER_PATTERN_replace_str))
            else:  # 直接移除
                new_name = WINDOWS_RESERVED_CHARACTER_PATTERN.sub('', new_name)
            new_name = new_name.strip()
        else:
            if self.__renamer_illegal_character_to_full_width_flag:  # 半角转全角
                new_name = new_name.translate(new_name.maketrans(
                    WINDOWS_RESERVED_CHARACTER_IGNORE_SLASH_PATTERN_str, WINDOWS_RESERVED_CHARACTER_IGNORE_SLASH_PATTERN_replace_str))
            else:  # 直接移除
                new_name = WINDOWS_RESERVED_CHARACTER_IGNORE_SLASH_PATTERN.sub('', new_name)
            new_name = normalize_path(new_name)

        return new_name

    def preview_folder_name(self, metadata: WorkMetadata, template: str | None = None) -> str:
        """根据元数据预览文件夹名；template 可临时覆盖当前模板（供设置界面使用）。"""
        if template is not None:
            original = self.__template
            self.__template = template
            try:
                return self.__compile_new_name(metadata)
            finally:
                self.__template = original
        return self.__compile_new_name(metadata)

    def list_work_folders(self, root_path: str):
        return list(self.__scanner.scan(root_path))

    @staticmethod
    def __handle_request_exception(rjcode: str, task: str, err: RequestException):
        if isinstance(err, Timeout):
            # 请求超时
            Renamer.logger.warning(f'[{rjcode}] -> {task}失败[Timeout]：dlsite.com 请求超时！\n')
        elif isinstance(err, ConnectionError):
            # 遇到其它网络问题（如：DNS 查询失败、拒绝连接等）
            Renamer.logger.warning(f'[{rjcode}] -> {task}失败[ConnectionError]：{str(err)}\n')
        elif isinstance(err, HTTPError):
            # HTTP 请求返回了不成功的状态码
            Renamer.logger.warning(f'[{rjcode}] -> {task}失败[HTTPError]：{err.response.status_code} {err.response.reason}\n')
        elif isinstance(err, RequestException):
            # requests 引发的其它异常
            Renamer.logger.error(f'[{rjcode}] -> {task}失败[RequestException]：{str(err)}\n')

    def __scrape_metadata_or_none(self, rjcode: str):
        try:
            return self.__scraper.scrape_metadata(rjcode)
        except RequestException as err:
            Renamer.__handle_request_exception(rjcode, '爬取元数据', err)
            return None
        except (ValueError, IndexError, KeyError) as err:
            Renamer.logger.warning(f'[{rjcode}] -> 爬取元数据失败：{err}\n')
            return None

    def verify_rj_code(self, rjcode: str) -> bool:
        """向 DLsite 验证 RJ 号是否有效（裸数字匹配时用于排除误判）。"""
        try:
            self.__scraper.scrape_metadata(rjcode)
            return True
        except (RequestException, ValueError, IndexError, KeyError):
            return False

    def _resolve_rj_metadata(self, folder_path: str, hint_rjcode: str | None = None):
        """从文件夹名与目录内容中按优先级（带前缀 > 裸数字）解析可用 RJ 与元数据。"""
        basename = os.path.basename(folder_path)
        allow_bare = file_ops.allow_bare_rj_digit_match(directory_roots=[folder_path])
        scores: dict[str, tuple[int, str]] = {}
        if hint_rjcode:
            source = file_ops.rj_match_source(basename, hint_rjcode)
            if source != 'bare' or allow_bare:
                file_ops._score_rj_candidate(scores, hint_rjcode.upper(), source, 3)
        for code, source in file_ops._iter_rj_codes_in_text(basename, allow_bare=allow_bare):
            file_ops._score_rj_candidate(scores, code, source, 3)
        for code, source, score in file_ops.find_rj_candidates_in_directory(
                folder_path, max_depth=2, allow_bare=allow_bare):
            file_ops._score_rj_candidate(scores, code, source, score)

        for code, source, _score in file_ops._sorted_rj_candidates(scores):
            if hint_rjcode and code.upper() == hint_rjcode.upper():
                if source == 'bare' and not allow_bare:
                    continue
            if source == 'bare' and not allow_bare:
                Renamer.logger.debug(
                    f'[{code}] -> 裸数字候选已忽略：目录内音频文件不足 2 个'
                )
                continue
            if source == 'bare' and not self.verify_rj_code(code):
                Renamer.logger.debug(
                    f'[{code}] -> 裸数字候选未通过 DLsite 验证，跳过'
                )
                continue
            metadata = self.__scrape_metadata_or_none(code)
            if metadata is not None:
                if hint_rjcode and code != hint_rjcode.upper():
                    Renamer.logger.info(
                        f'[{hint_rjcode}] -> 改用优先级更高的候选 [{code}] 继续重命名'
                    )
                return code, metadata
        return None, None

    def rename(self, root_path: str) -> list[str]:
        renamed_paths: list[str] = []
        work_folders = self.__scanner.scan(root_path)
        for hint_rjcode, folder_path in work_folders:
            Renamer.logger.info(
                f'[{hint_rjcode}] -> 发现 RJ 文件夹："{os.path.normpath(folder_path)}"'
            )
            rjcode, metadata = self._resolve_rj_metadata(folder_path, hint_rjcode=hint_rjcode)
            if metadata is None:
                continue

            dirname, basename = os.path.split(folder_path)

            # 重命名文件夹
            new_basename = self.__compile_new_name(metadata).rstrip(' .')
            new_folder_path = os.path.join(dirname, new_basename) if self.__mode == 'RENAME' else os.path.join(self.__move_root, new_basename)
            try:
                if self.__mode == 'MOVE':
                    move_folder(folder_path, new_folder_path)
                elif self.__mode == 'LINK':
                    copy_with_symlink(folder_path, os.path.join(new_folder_path, basename))
                elif os.path.normcase(folder_path) != os.path.normcase(new_folder_path):
                    dest_exists = (
                        file_ops.path_exists(new_folder_path)
                        and file_ops.is_dir_path(new_folder_path)
                    )
                    if dest_exists:
                        Renamer.logger.warning(
                            f'[{rjcode}] -> 音声库中已有同名作品，存在重复内容：'
                            f'"{os.path.normpath(new_folder_path)}"'
                        )
                        Renamer.logger.warning(
                            f'[{rjcode}] -> 当前待处理文件夹："{os.path.normpath(folder_path)}"，'
                            f'请手动合并或删除重复项后重试\n'
                        )
                        raise RenameDuplicateError(rjcode, folder_path, new_folder_path)
                    if not file_ops.safe_rename_path(folder_path, new_folder_path):
                        Renamer.logger.warning(
                            f'[{rjcode}] -> 重命名({self.__mode})失败：'
                            f'"{os.path.normpath(folder_path)}" -> "{os.path.normpath(new_folder_path)}"\n'
                        )
                        continue
                Renamer.logger.info(f'[{rjcode}] -> 重命名({self.__mode})成功："{os.path.normpath(new_folder_path)}"')
            except RenameDuplicateError:
                raise
            except FileExistsError as err:
                filename2 = os.path.normpath(err.filename2 or new_folder_path)
                if file_ops.path_exists(filename2) and file_ops.is_dir_path(filename2):
                    Renamer.logger.warning(
                        f'[{rjcode}] -> 音声库中已有同名作品，存在重复内容："{filename2}"'
                    )
                    Renamer.logger.warning(
                        f'[{rjcode}] -> 当前待处理文件夹："{os.path.normpath(folder_path)}"，'
                        f'请手动合并或删除重复项后重试\n'
                    )
                    raise RenameDuplicateError(rjcode, folder_path, filename2) from err
                Renamer.logger.warning(
                    f'[{rjcode}] -> 重命名({self.__mode})失败[FileExistsError]：'
                    f'{err.strerror}目标路径："{filename2}"\n'
                )
                continue
            except OSError as err:
                err_msg = f'[{rjcode}] -> 重命名失败[OSError]：{str(err)}'
                if err.winerror == 1314:
                    err_msg = err_msg + "\n" + "Windows 下创建符号链接目录需要管理员权限，或启用 设置-系统-开发者选项-开发人员模式"
                Renamer.logger.error(err_msg + "\n")
                break

            renamed_paths.append(new_folder_path)

            # 修改封面
            if self.__make_folder_icon:
                try:
                    icon_name, _ = Renamer.changeIcon(self, rjcode, metadata['cover_url'], new_folder_path)  # 修改封面
                except RequestException as err:
                    Renamer.__handle_request_exception(rjcode, '下载封面图', err)  # 下载封面图失败
                    continue
                except OSError as err:
                    Renamer.logger.error(f'[{rjcode}] -> 修改封面失败[OSError]：{str(err)}')
                    continue

            Renamer.logger.info(f'[{rjcode}] -> 处理结束\n')
        return renamed_paths

    @staticmethod
    def _refresh_shell_icon(folder_path: str):
        """通知资源管理器刷新文件夹图标缓存。"""
        if sys.platform != 'win32':
            return
        try:
            shell32 = ctypes.windll.shell32
            shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
            shell32.SHChangeNotify(0x00002000, 0x0005, folder_path, None)  # SHCNE_UPDATEITEM
        except Exception:
            pass

    @staticmethod
    def _win_path(path: str) -> str:
        if sys.platform == 'win32':
            return file_ops._extended_path(path)
        return path

    @staticmethod
    def _win_set_file_attributes(path: str, attrs: int):
        try:
            win32api.SetFileAttributes(Renamer._win_path(path), attrs)
        except OSError as err:
            Renamer.logger.debug('SetFileAttributes 失败 [{}]: {}'.format(path, err))

    @staticmethod
    def _win_or_file_attributes(path: str, attrs: int):
        try:
            current = win32api.GetFileAttributes(Renamer._win_path(path))
            Renamer._win_set_file_attributes(path, current | attrs)
        except OSError as err:
            Renamer.logger.warning('无法设置文件夹属性 [{}]: {}'.format(path, err))

    @staticmethod
    def _win_clear_file_attributes(path: str, attrs: int):
        try:
            current = win32api.GetFileAttributes(Renamer._win_path(path))
            Renamer._win_set_file_attributes(path, current & ~attrs)
        except OSError:
            pass

    @staticmethod
    def _desktop_ini_content(icon_name: str) -> str:
        """使用相对路径，文件夹重命名后图标引用仍然有效。"""
        return (
            '[.ShellClassInfo]\r\n'
            'ConfirmFileOp=0\r\n'
            f'IconResource={icon_name},0\r\n'
            '[ViewState]\r\n'
            'Mode=\r\n'
            'Vid=\r\n'
            'FolderType=StorageProviderGeneric\r\n'
        )

    @staticmethod
    def _repair_folder_custom_icon(folder_path: str) -> bool:
        """文件夹重命名后，重写 desktop.ini 并刷新自定义图标。"""
        if sys.platform != 'win32' or not os.path.isdir(folder_path):
            return False
        ini_file_path = os.path.join(folder_path, 'desktop.ini')
        try:
            icon_names = sorted(
                name for name in os.listdir(folder_path)
                if name.startswith('@folder-icon-') and name.lower().endswith('.ico')
            )
        except OSError:
            return False
        if not icon_names:
            return os.path.isfile(ini_file_path)
        icon_name = icon_names[0]
        icon_path = os.path.join(folder_path, icon_name)

        file_ops.clear_shell_folder_attributes(folder_path)
        if os.path.exists(ini_file_path):
            file_ops.clear_shell_folder_attributes(ini_file_path)
        with open(ini_file_path, 'w', encoding='utf-16') as inifile:
            inifile.write(Renamer._desktop_ini_content(icon_name))

        hidden_system = (win32con.FILE_ATTRIBUTE_HIDDEN
                         | win32con.FILE_ATTRIBUTE_SYSTEM)
        Renamer._win_set_file_attributes(ini_file_path, hidden_system)
        Renamer._win_set_file_attributes(icon_path, hidden_system)
        Renamer._win_or_file_attributes(
            folder_path, win32con.FILE_ATTRIBUTE_READONLY)
        Renamer._refresh_shell_icon(folder_path)
        return True

    # 修改文件夹封面
    def changeIcon(self, rjcode: str, cover_url: str, icon_dir: str):
        icon_name = f'@folder-icon-{rjcode}.ico'
        ini_file_path = os.path.join(icon_dir, 'desktop.ini')
        icon_path = os.path.join(icon_dir, icon_name)

        if sys.platform == 'win32':
            file_ops.clear_shell_folder_attributes(icon_dir)

        icon_name, jpg_name = self.__scraper.scrape_icon(
            rjcode, cover_url, icon_dir, force=True)

        if os.path.exists(ini_file_path) and sys.platform == 'win32':
            file_ops.clear_shell_folder_attributes(ini_file_path)
        with open(ini_file_path, 'w', encoding='utf-16') as inifile:
            inifile.write(Renamer._desktop_ini_content(icon_name))

        if sys.platform == 'win32':
            hidden_system = (win32con.FILE_ATTRIBUTE_HIDDEN
                             | win32con.FILE_ATTRIBUTE_SYSTEM)
            Renamer._win_set_file_attributes(ini_file_path, hidden_system)
            Renamer._win_set_file_attributes(icon_path, hidden_system)
            # Explorer 仅在文件夹只读时才读取 desktop.ini 作为自定义图标
            Renamer._win_or_file_attributes(
                icon_dir, win32con.FILE_ATTRIBUTE_READONLY)
            Renamer._refresh_shell_icon(icon_dir)

        Renamer.logger.info(f'[{rjcode}] -> 修改封面成功："{icon_name}"')

        if self.__remove_jpg_file:
            # 删除 .jpg 文件
            jpg_path = Path(os.path.join(icon_dir, jpg_name))
            jpg_path.unlink(missing_ok=True)

        return icon_name, jpg_name
