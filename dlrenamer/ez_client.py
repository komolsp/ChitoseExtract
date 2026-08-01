import copy
import json
import logging
import os
import traceback

from renamer import Renamer, RenameDuplicateError

import file_ops
import pk_logger
from dlrenamer.runner import create_renamer_from_dict
from template_field_list import finalize_folder_name, resolve_rename_template


def _config_digest(config: dict) -> str:
    return json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)


class ez_client:
    """ChitoseExtract 对 dlsite-doujin-renamer v0.3.2 的薄封装。"""

    _logger_configured = False

    def __init__(self, renamer_config: dict):
        self._renamer_config = copy.deepcopy(renamer_config)
        self.renamer = None
        self._setup_logger()
        self._load_renamer()

    def _setup_logger(self):
        if ez_client._logger_configured:
            return
        Renamer.logger.handlers.clear()
        Renamer.logger.setLevel(logging.DEBUG)
        Renamer.logger.propagate = False

        file_handler = pk_logger.SafeRotatingFileHandler('log.txt', 'a', 1240 * 1240 * 5, 3, encoding='utf-8')
        file_handler.setFormatter(pk_logger.default_formatter)
        Renamer.logger.addHandler(file_handler)
        pk_logger.attach_gui_handler(Renamer.logger)

        ez_client._logger_configured = True

    def _load_renamer(self):
        self.renamer, errors = create_renamer_from_dict(self._renamer_config)
        for err in errors:
            Renamer.logger.error(err)

    def reload(self, renamer_config: dict):
        incoming = copy.deepcopy(renamer_config)
        if _config_digest(incoming) == _config_digest(self._renamer_config):
            return
        self._renamer_config = incoming
        self._load_renamer()

    def _create_rename_renamer(self):
        user_template = str(self._renamer_config.get('renamer_template', '') or '')
        exec_template, strip_rj_after = resolve_rename_template(user_template)
        rename_config = copy.deepcopy(self._renamer_config)
        rename_config['renamer_template'] = exec_template
        renamer, errors = create_renamer_from_dict(rename_config)
        for err in errors:
            Renamer.logger.error(err)
        return renamer, strip_rj_after, user_template, exec_template

    def _strip_rj_after_rename(self, folder_paths: list[str], user_template: str) -> list[str]:
        """仅处理本次重命名成功的作品文件夹，避免误伤同目录其它作品。返回最终路径列表。"""
        final_paths: list[str] = []
        seen: set[str] = set()
        for folder_path in folder_paths:
            norm = os.path.normcase(os.path.normpath(folder_path))
            if norm in seen:
                continue
            seen.add(norm)
            if not os.path.isdir(folder_path):
                Renamer.logger.warning(
                    f'跳过移除 RJ（文件夹不存在）："{os.path.normpath(folder_path)}"'
                )
                continue
            dirname, basename = os.path.split(folder_path)
            new_basename = finalize_folder_name(user_template, basename)
            if not new_basename or new_basename == basename:
                final_paths.append(folder_path)
                continue
            new_path = os.path.join(dirname, new_basename)
            if file_ops.safe_rename_path(folder_path, new_path):
                final_paths.append(new_path)
                Renamer.logger.info(
                    f'已移除 RJ 号："{os.path.normpath(folder_path)}" -> "{os.path.normpath(new_path)}"'
                )
                if Renamer._repair_folder_custom_icon(new_path):
                    Renamer.logger.debug(
                        f'已刷新文件夹图标："{os.path.normpath(new_path)}"'
                    )
            else:
                Renamer.logger.warning(
                    f'移除 RJ 失败，保留原文件夹名："{os.path.normpath(folder_path)}"'
                )
                final_paths.append(folder_path)
        return final_paths

    @staticmethod
    def _resolve_renamed_root(original: str, final_paths: list[str]) -> str | None:
        """从本次重命名结果中解析与 original 对应的作品目录最终路径。"""
        if not final_paths:
            return None
        if len(final_paths) == 1:
            return final_paths[0]
        orig_norm = os.path.normcase(os.path.normpath(original))
        for folder_path in final_paths:
            if os.path.normcase(os.path.normpath(folder_path)) == orig_norm:
                return folder_path
        orig_parent = os.path.normcase(os.path.dirname(original))
        for folder_path in final_paths:
            if os.path.normcase(os.path.dirname(folder_path)) == orig_parent:
                return folder_path
        return final_paths[0]

    def run_renamer(self, path):
        if not self.renamer:
            self._load_renamer()
        if not self.renamer:
            return None
        try:
            rename_renamer, strip_rj_after, user_template, exec_template = self._create_rename_renamer()
            if not rename_renamer:
                return None

            work_folders = rename_renamer.list_work_folders(path)
            if not work_folders:
                Renamer.logger.warning(
                    f'未发现含 RJ 号的文件夹，跳过重命名："{os.path.normpath(path)}"'
                )
                return None

            Renamer.logger.debug(
                f'重命名模板：user="{user_template}" exec="{exec_template}" strip_rj={strip_rj_after}'
            )
            renamed_paths = rename_renamer.rename(path)
            if not renamed_paths:
                Renamer.logger.warning(
                    f'重命名未产生任何结果："{os.path.normpath(path)}"'
                )
                return None

            final_paths = (
                self._strip_rj_after_rename(renamed_paths, user_template)
                if strip_rj_after else renamed_paths
            )
            final_path = self._resolve_renamed_root(path, final_paths)
            if not final_path:
                return None
            return final_path
        except RenameDuplicateError:
            raise
        except Exception as err:
            Renamer.logger.error(f'[Unexpected exception] {str(err)}')
            traceback.print_exc()
            return None


_client: ez_client | None = None


def ensure_client(renamer_config: dict) -> ez_client:
    """获取全局唯一的 ez_client；配置变化时才重建 Renamer。"""
    global _client
    if _client is None:
        _client = ez_client(renamer_config)
    else:
        _client.reload(renamer_config)
    return _client
