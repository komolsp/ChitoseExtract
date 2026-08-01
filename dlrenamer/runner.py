"""
dlsite-doujin-renamer v0.3.2 初始化逻辑。
"""
from __future__ import annotations

from typing import Any, Optional

from config_file import verify_config_dict
from renamer import Renamer
from scanner import Scanner
from scraper import Locale, CachedScraper
from scraper.rjcode_locales import normalize_display_locales

_scraper_singleton: Optional[CachedScraper] = None
_active_scraper_key: Optional[tuple] = None


def _resolve_locale(value) -> Locale:
    if isinstance(value, Locale):
        return value
    return Locale[value]


def _scraper_cache_key(config: dict[str, Any]) -> tuple:
    return (
        _resolve_locale(config['scraper_locale']).name,
        config.get('scraper_http_proxy'),
        config['scraper_connect_timeout'],
        config['scraper_read_timeout'],
        config['scraper_sleep_interval'],
    )


def get_shared_scraper(config: dict[str, Any]) -> CachedScraper:
    """复用 CachedScraper 实例，避免 reload 时重复打开数据库连接。"""
    global _scraper_singleton, _active_scraper_key

    key = _scraper_cache_key(config)
    if _scraper_singleton is not None and _active_scraper_key == key:
        return _scraper_singleton

    scraper_http_proxy = config['scraper_http_proxy']
    if scraper_http_proxy:
        proxies = {
            'http': scraper_http_proxy,
            'https': scraper_http_proxy,
        }
    else:
        proxies = None

    _scraper_singleton = CachedScraper(
        locale=_resolve_locale(config['scraper_locale']),
        connect_timeout=config['scraper_connect_timeout'],
        read_timeout=config['scraper_read_timeout'],
        sleep_interval=config['scraper_sleep_interval'],
        proxies=proxies,
    )
    _active_scraper_key = key
    return _scraper_singleton


def _build_renamer(config: dict[str, Any]) -> Renamer:
    scanner = Scanner(max_depth=config['scanner_max_depth'])
    cached_scraper = get_shared_scraper(config)
    tags_option = {
        'ordered_list': config['renamer_tags_ordered_list'],
        'max_number': 999999 if config['renamer_tags_max_number'] == 0 else config['renamer_tags_max_number'],
    }

    return Renamer(
        scanner=scanner,
        scraper=cached_scraper,
        template=config['renamer_template'],
        release_date_format=config['renamer_release_date_format'],
        delimiter=config['renamer_delimiter'],
        cv_list_left=config['renamer_cv_list_left'],
        cv_list_right=config['renamer_cv_list_right'],
        tags_list_left=config['renamer_tags_list_left'],
        tags_list_right=config['renamer_tags_list_right'],
        exclude_square_brackets_in_work_name_flag=config['renamer_exclude_square_brackets_in_work_name_flag'],
        renamer_illegal_character_to_full_width_flag=config['renamer_illegal_character_to_full_width_flag'],
        make_folder_icon=config['renamer_make_folder_icon'],
        remove_jpg_file=config['renamer_remove_jpg_file'],
        tags_option=tags_option,
        age_cat_map_gen=config['renamer_age_cat_map_gen'],
        age_cat_map_r15=config['renamer_age_cat_map_r15'],
        age_cat_map_r18=config['renamer_age_cat_map_r18'],
        age_cat_left=config['renamer_age_cat_left'],
        age_cat_right=config['renamer_age_cat_right'],
        age_cat_ignore_r18=config['renamer_age_cat_ignore_r18'],
        mode=config['renamer_mode'],
        move_root=config['renamer_move_root'],
        move_template=config['renamer_move_template'],
        series_name_left=config['renamer_series_name_left'],
        series_name_right=config['renamer_series_name_right'],
        rjcode_display_locales=normalize_display_locales(
            config.get('renamer_rjcode_display_locales'),
        ),
    )


def create_renamer_from_dict(renamer_config: dict) -> tuple[Optional[Renamer], list[str]]:
    """
    从配置字典创建 Renamer 实例（用于 config.yaml 的 renamer 段）。
    """
    validated, strerror_list = verify_config_dict(renamer_config)
    if validated is None:
        return None, strerror_list
    return _build_renamer(validated), []
