"""DLsite 各语言版本 RJ 号识别与聚合。"""

from __future__ import annotations

from typing import Callable, Optional

from scraper.dlsite import Dlsite

RJCODE_DISPLAY_LOCALES: tuple[str, ...] = ('ja_jp', 'zh_cn', 'zh_tw')
RJCODE_LOCALE_LABELS: dict[str, str] = {
    'ja_jp': '日',
    'zh_cn': '简',
    'zh_tw': '繁',
}
METADATA_SCHEMA_VERSION = 4

_SCRAPER_LOCALE_TO_EDITION_LANG: dict[str, str] = {
    'ja_jp': 'JPN',
    'zh_cn': 'CHI_HANS',
    'zh_tw': 'CHI_HANT',
    'en_us': 'ENG',
    'ko_kr': 'KO_KR',
}

_LANG_ALIASES: dict[str, str] = {
    'ja_jp': 'ja_jp',
    'ja': 'ja_jp',
    'jpn': 'ja_jp',
    'japanese': 'ja_jp',
    'zh_cn': 'zh_cn',
    'zhcn': 'zh_cn',
    'cn': 'zh_cn',
    'chi_hans': 'zh_cn',
    'chinese_simplified': 'zh_cn',
    'zh_tw': 'zh_tw',
    'zhtw': 'zh_tw',
    'tw': 'zh_tw',
    'chi_hant': 'zh_tw',
    'chinese_traditional': 'zh_tw',
}


def normalize_workno(value) -> str | None:
    if not value:
        return None
    code = str(value).strip().upper()
    if Dlsite.WORKNO_PATTERN.fullmatch(code):
        return code
    return None


def translation_lang_to_locale(lang) -> str | None:
    if lang is None:
        return None
    normalized = str(lang).strip().lower().replace('-', '_')
    return _LANG_ALIASES.get(normalized)


def options_to_locale(options) -> str | None:
    if not options:
        return None
    tokens = {
        str(token).strip().lower().replace('-', '_')
        for token in str(options).split('#')
        if str(token).strip()
    }
    for token in ('chi_hans', 'zh_cn', 'zhcn', 'cn'):
        if token in tokens:
            return 'zh_cn'
    for token in ('chi_hant', 'zh_tw', 'zhtw', 'tw'):
        if token in tokens:
            return 'zh_tw'
    for token in ('jpn', 'ja_jp', 'ja'):
        if token in tokens:
            return 'ja_jp'
    return None


def classify_workno_locale(product_info: dict) -> str | None:
    """根据 product.json 的 translation_info / options 判断作品语言版本。"""
    translation_info = product_info.get('translation_info') or {}
    locale = translation_lang_to_locale(translation_info.get('lang'))
    if not locale:
        locale = options_to_locale(product_info.get('options'))
    if locale in RJCODE_DISPLAY_LOCALES:
        return locale

    if translation_info.get('is_original') is True:
        return 'ja_jp'

    workno = normalize_workno(product_info.get('workno'))
    original = normalize_workno(translation_info.get('original_workno'))
    if original and workno and original != workno:
        return None
    if not original and translation_info.get('is_child') is not True:
        return 'ja_jp'
    return None


def _gather_language_edition_worknos(product_info: dict | None) -> set[str]:
    worknos: set[str] = set()
    if not product_info:
        return worknos
    for edition in product_info.get('language_editions') or []:
        code = normalize_workno(edition.get('workno'))
        if code:
            worknos.add(code)
    return worknos


def rjcodes_from_language_editions(product_info: dict | None) -> dict[str, str]:
    """从 product.json 的 language_editions 映射各语言 RJ 号。"""
    by_locale: dict[str, str] = {}
    if not product_info:
        return by_locale
    for edition in product_info.get('language_editions') or []:
        locale = translation_lang_to_locale(edition.get('lang'))
        workno = normalize_workno(edition.get('workno'))
        if locale in RJCODE_DISPLAY_LOCALES and workno and locale not in by_locale:
            by_locale[locale] = workno
    return by_locale


def resolve_edition_workno_for_locale(product_info: dict, scraper_locale: str | None) -> str | None:
    """当前 RJ 与目标主数据语言不一致时，返回 language_editions 中对应版本 RJ。"""
    if not product_info:
        return None
    requested = normalize_scraper_locale(scraper_locale)
    if not requested:
        return normalize_workno(product_info.get('workno'))
    if classify_workno_locale(product_info) == requested:
        return normalize_workno(product_info.get('workno'))
    edition_lang = _SCRAPER_LOCALE_TO_EDITION_LANG.get(requested)
    if not edition_lang:
        return normalize_workno(product_info.get('workno'))
    for edition in product_info.get('language_editions') or []:
        if str(edition.get('lang') or '').upper() != edition_lang:
            continue
        workno = normalize_workno(edition.get('workno'))
        if workno:
            return workno
    return normalize_workno(product_info.get('workno'))


def normalize_scraper_locale(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, 'name'):
        return str(value.name)
    text = str(value).strip()
    return text or None


def metadata_needs_locale_refresh(
        metadata: dict | None,
        *,
        scraper_locale: str | None = None) -> bool:
    """旧缓存或主数据语言变更时需要重新刮削。"""
    if not metadata:
        return True
    if metadata.get('metadata_schema_version', 1) < METADATA_SCHEMA_VERSION:
        return True
    if 'rjcodes_by_locale' not in metadata:
        return True
    expected_locale = normalize_scraper_locale(scraper_locale)
    if expected_locale and metadata.get('scraper_locale') != expected_locale:
        return True
    return False


def _gather_related_worknos(*product_infos: dict | None) -> set[str]:
    """收集 translation 链上的相关 RJ；language_editions 仅作映射，不在此抓取。"""
    worknos: set[str] = set()
    for product_info in product_infos:
        if not product_info:
            continue
        workno = normalize_workno(product_info.get('workno'))
        if workno:
            worknos.add(workno)
        translation_info = product_info.get('translation_info') or {}
        for key in ('original_workno', 'parent_workno'):
            code = normalize_workno(translation_info.get(key))
            if code:
                worknos.add(code)
        for child in translation_info.get('child_worknos') or []:
            code = normalize_workno(child)
            if code:
                worknos.add(code)
    return worknos


def _resolve_primary_locale(product_info: dict, scraper_locale: str | None) -> str | None:
    locale = classify_workno_locale(product_info)
    if locale:
        return locale
    if scraper_locale in RJCODE_DISPLAY_LOCALES:
        return scraper_locale
    return None


def collect_rjcodes_by_locale(
        product_info: dict,
        original_product_info: dict | None,
        fetch_product_info: Callable[[str], Optional[dict]],
        *,
        scraper_locale: str | None = None,
) -> dict[str, str]:
    """收集日/简/繁各语言版本 RJ 号；优先使用已返回的 language_editions，避免多余 API 请求。"""
    by_locale: dict[str, str] = {}
    primary = normalize_workno(product_info.get('workno'))
    primary_locale = _resolve_primary_locale(product_info, scraper_locale)
    if primary and primary_locale:
        by_locale[primary_locale] = primary

    def _accept_locale_workno(locale: str, workno: str | None):
        if not workno or locale in by_locale:
            return
        if workno == primary:
            by_locale[locale] = workno
            return
        if fetch_product_info(workno) is not None:
            by_locale[locale] = workno

    attempted_editions: set[tuple[str, str]] = set()

    def _accept_editions_from(source: dict | None):
        if not source:
            return
        for locale, workno in rjcodes_from_language_editions(source).items():
            key = (locale, workno or '')
            if key in attempted_editions:
                continue
            attempted_editions.add(key)
            _accept_locale_workno(locale, workno)

    _accept_editions_from(product_info)
    _accept_editions_from(original_product_info)

    if all(locale in by_locale for locale in RJCODE_DISPLAY_LOCALES):
        return by_locale

    infos: dict[str, dict] = {}

    def _register(info: dict | None):
        workno = normalize_workno(info.get('workno') if info else None)
        if workno and workno not in infos:
            infos[workno] = info

    _register(product_info)
    _register(original_product_info)

    pending = _gather_related_worknos(product_info, original_product_info) - set(infos)
    while pending:
        workno = pending.pop()
        if workno in infos:
            continue
        info = fetch_product_info(workno)
        if info is None:
            continue
        _register(info)
        pending.update(_gather_related_worknos(info) - set(infos))

    for info in infos.values():
        workno = normalize_workno(info.get('workno'))
        if not workno or workno == primary:
            continue
        locale = classify_workno_locale(info)
        if locale and locale not in by_locale:
            by_locale[locale] = workno

    _accept_editions_from(product_info)
    _accept_editions_from(original_product_info)
    return by_locale


def normalize_display_locales(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    selected: list[str] = []
    seen: set[str] = set()
    for item in raw:
        locale = str(item or '').strip()
        if locale in RJCODE_DISPLAY_LOCALES and locale not in seen:
            selected.append(locale)
            seen.add(locale)
    return selected
