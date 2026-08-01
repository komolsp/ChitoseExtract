import json

from scraper.db import db, WorkMetadataCache
from scraper.locale import Locale
from scraper.rjcode_locales import metadata_needs_locale_refresh
from scraper.scraper import Scraper
from scraper.work_metadata import WorkMetadata


class CachedScraper(Scraper):
    def __init__(self, locale: Locale, proxies=None, connect_timeout: int = 10, read_timeout: int = 10, sleep_interval=3):
        super().__init__(locale, proxies, connect_timeout, read_timeout, sleep_interval)
        db.connect(reuse_if_open=True)
        db.create_tables([WorkMetadataCache], safe=True)

    def scrape_metadata(self, rjcode: str):
        metadata_cache = WorkMetadataCache.get_or_none(WorkMetadataCache.rjcode == rjcode)
        if metadata_cache:
            metadata: WorkMetadata = json.loads(metadata_cache.metadata)
            if not metadata_needs_locale_refresh(metadata, scraper_locale=self.locale.name):
                return metadata
            metadata_cache.delete_instance()

        metadata = super().scrape_metadata(rjcode)
        WorkMetadataCache.create(rjcode=rjcode, metadata=json.dumps(metadata, indent=2, ensure_ascii=False))
        return metadata
