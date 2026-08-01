import json
import os
import re
from typing import Annotated, Optional, Union, Literal
from pydantic import Field
from typing_extensions import TypedDict
from pydantic import TypeAdapter, ConfigDict, ValidationError
from scraper import Locale

FilenameStr = Annotated[str, Field(pattern=r'^[^\/:*?"<>|]*$', description="""不能含有系统保留字[^\/:*?`<>|]*""")]
RjcodeStr = Annotated[str, Field(pattern=re.compile(r".*rjcode.*"), description='template 应是一个包含 "rjcode" 的字符串')]

class Config(TypedDict):
    __pydantic_config__ = ConfigDict()

    # scanner
    scanner_max_depth: int
    # scraper
    scraper_locale: Locale
    scraper_connect_timeout: int
    scraper_read_timeout: int
    scraper_sleep_interval: int
    scraper_http_proxy: Optional[str]
    # renamer
    renamer_template: str
    renamer_release_date_format: str # https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes
    renamer_exclude_square_brackets_in_work_name_flag: bool
    renamer_illegal_character_to_full_width_flag: bool
    renamer_make_folder_icon: bool
    renamer_remove_jpg_file: bool
    renamer_delimiter: FilenameStr  # 分隔符
    renamer_cv_list_left: FilenameStr
    renamer_cv_list_right: FilenameStr
    renamer_tags_list_left: FilenameStr
    renamer_tags_list_right: FilenameStr
    renamer_tags_max_number: int  # 标签个数上限
    renamer_tags_ordered_list: list[Union[str, list[str]]]
    renamer_age_cat_map_gen: str
    renamer_age_cat_map_r15: str
    renamer_age_cat_map_r18: str
    renamer_age_cat_left: FilenameStr
    renamer_age_cat_right: FilenameStr
    renamer_age_cat_ignore_r18: bool
    renamer_series_name_left: FilenameStr
    renamer_series_name_right: FilenameStr
    renamer_rjcode_display_locales: list[str]
    renamer_mode: Literal["RENAME", "MOVE", "LINK"]
    renamer_move_root: str
    renamer_move_template: RjcodeStr


ta = TypeAdapter(Config)


DEFAULT_CONFIG: Config = {
    # scanner
    'scanner_max_depth': 5,
    # scraper
    'scraper_locale': 'ja_jp',
    'scraper_connect_timeout': 10,
    'scraper_read_timeout': 10,
    'scraper_sleep_interval': 3,
    'scraper_http_proxy': None,
    # renamer
    'renamer_template': 'age_cat[maker_name][rjcode] work_name cv_list_str',
    'renamer_release_date_format': '%y%m%d',
    'renamer_exclude_square_brackets_in_work_name_flag': True,
    'renamer_illegal_character_to_full_width_flag': True,
    'renamer_make_folder_icon': True,
    'renamer_remove_jpg_file': True,
    'renamer_delimiter': " ",
    'renamer_cv_list_left': "(CV ",
    'renamer_cv_list_right': ")",
    'renamer_tags_list_left': "",
    'renamer_tags_list_right': "",
    'renamer_tags_max_number': 5,
    'renamer_tags_ordered_list': ["标签1", ["标签2", "替换2"], "标签3"],  # 标签顺序列表，每一项可为字符串或[原标签,替换名]
    'renamer_age_cat_map_gen': "全年龄",
    'renamer_age_cat_map_r15': "R15",
    'renamer_age_cat_map_r18': "R18",
    'renamer_age_cat_left': "(",
    'renamer_age_cat_right': ")",
    'renamer_age_cat_ignore_r18': True,
    'renamer_series_name_left': "",
    'renamer_series_name_right': "",
    'renamer_rjcode_display_locales': [],
    'renamer_mode': 'RENAME',
    'renamer_move_root': 'RENAMER_MOVE_ROOT',
    'renamer_move_template': 'maker_name/series_name/age_cat[rjcode] work_name cv_list_str'
}


class ConfigFile(object):
    def __init__(self, file_path: str):
        self.__config: Config = None
        self.__config_dict = None
        self.__file_path = file_path
        if not os.path.isfile(file_path):
            self.save_config(DEFAULT_CONFIG)

    def load_config_dict(self):
        """
        从配置文件中读取配置
        """
        with open(self.__file_path, encoding='UTF-8') as file:
            config_dict = json.load(file)
            self.__config_dict = config_dict

    def save_config(self, config: Config):
        """
        保存配置到文件
        """
        with open(self.__file_path, 'w', encoding='UTF-8') as file:
            json.dump(config, file, indent=2, ensure_ascii=False)

    @property
    def file_path(self):
        return self.__file_path

    @property
    def config(self):
        return self.__config

    def verify_config(self) -> list[str]:
        """
        验证配置是否合理
        """
        validated, strerror_list = verify_config_dict(self.__config_dict)
        if validated is not None:
            self.__config = validated
        return strerror_list


def _normalize_renamer_config(config_dict: dict) -> dict:
    """将旧版键名迁移为统一命名，并补全缺失的默认值。"""
    normalized = dict(DEFAULT_CONFIG)
    normalized.update(config_dict)
    if 'scanner_max_depth' not in config_dict and 'scaner_max_depth' in config_dict:
        normalized['scanner_max_depth'] = config_dict['scaner_max_depth']
    if not normalized.get('renamer_rjcode_display_locales') and normalized.get('renamer_include_alt_rjcodes'):
        normalized['renamer_rjcode_display_locales'] = ['ja_jp', 'zh_cn', 'zh_tw']
    normalized.pop('renamer_include_alt_rjcodes', None)
    return normalized


def verify_config_dict(config_dict: dict) -> tuple[Optional[Config], list[str]]:
    """验证重命名配置字典，可供 config.yaml 的 renamer 段直接调用。"""
    strerror_list: list[str] = []
    try:
        validated = ta.validate_python(_normalize_renamer_config(config_dict))
        return validated, strerror_list
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(map(str, err["loc"]))
            strerror_list.append(
                "\n".join([
                    f"- 错误: {err['msg']}",
                    f"  校验器: {err['type']}",
                    f"  字段: {loc}",
                ])
            )
        return None, strerror_list
