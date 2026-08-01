from typing import NotRequired, TypedDict


CV_LIST_SEPARATOR = ';'


# 同人作品元数据
class WorkMetadata(TypedDict):
    metadata_schema_version: NotRequired[int]
    scraper_locale: NotRequired[str]
    rjcode: str
    work_name: str
    maker_id: str
    maker_name: str
    release_date: str
    series_id: str
    series_name: str
    age_category: str
    tags: list[str]
    cvs: list[str]
    cover_url: str
    rjcodes_by_locale: dict[str, str]
