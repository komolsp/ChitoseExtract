import os
import contextlib
import time
from pathlib import Path
from urllib.request import getproxies
from typing import Union

import requests
from requests.exceptions import RequestException
from pyquery import PyQuery as pq

from scraper.dlsite import Dlsite
from scraper.locale import Locale
from scraper.work_metadata import WorkMetadata

from PIL import Image as img


from scraper.rjcode_locales import (
    METADATA_SCHEMA_VERSION,
    collect_rjcodes_by_locale,
    normalize_workno,
    resolve_edition_workno_for_locale,
)


def _getproxies():
    """
    获取系统代理
    """
    proxies = getproxies()
    # https://github.com/psf/requests/issues/5943
    https_proxy = proxies.get('https', None)
    http_proxy = proxies.get('http', None)
    if https_proxy and https_proxy.startswith(r'https://'):
        proxies['https'] = http_proxy
    return proxies


class Scraper(object):
    def __init__(self, locale: Locale, proxies=None, connect_timeout: int = 10, read_timeout: int = 10, sleep_interval=3):
        self.__locale = locale
        self.__connect_timeout = connect_timeout
        self.__read_timeout = read_timeout
        self.__sleep_interval = sleep_interval
        if not proxies:
            # 获取系统代理
            proxies = _getproxies()
        self.__proxies = proxies

    @property
    def locale(self) -> Locale:
        return self.__locale

    def __request_work_page(self, rjcode: str):
        url = Dlsite.compile_work_page_url(rjcode)
        params = {'locale': self.__locale.name}
        response = requests.get(url,
                                params,
                                timeout=(self.__connect_timeout, self.__read_timeout),
                                proxies=self.__proxies)
        response.raise_for_status()  # 如果返回了不成功的状态码，Response.raise_for_status() 会抛出一个 HTTPError 异常
        html = response.text
        time.sleep(self.__sleep_interval)
        return html

    def __request_product_api(self, rjcode: str):
        url = Dlsite.compile_product_api_url(rjcode)
        params = {'locale': self.__locale.name}
        response = requests.get(url,
                                params,
                                timeout=(self.__connect_timeout, self.__read_timeout),
                                proxies=self.__proxies)
        if len(response.json()) == 0:
            response.status_code = 404
            response.reason = 'Not Found'
        response.raise_for_status()  # 如果返回了不成功的状态码，Response.raise_for_status() 会抛出一个 HTTPError 异常

        product_info = response.json()[0]
        time.sleep(self.__sleep_interval)
        return product_info

    def scrape_metadata(self, rjcode: str):
        rjcode = rjcode.upper()
        if not Dlsite.WORKNO_PATTERN.fullmatch(rjcode):
            raise ValueError
        metadata = self.__scrape_metadata_from_product_api(rjcode)
        return metadata

    def __scrape_metadata_from_product_api(self, workno: str):
        request_cache: dict[str, dict] = {}

        def fetch_product_info(rj: str) -> dict:
            code = rj.upper()
            if code not in request_cache:
                request_cache[code] = self.__request_product_api(code)
            return request_cache[code]

        def try_fetch_product_info(rj: str) -> dict | None:
            code = rj.upper()
            if code in request_cache:
                return request_cache[code]
            try:
                request_cache[code] = fetch_product_info(code)
                return request_cache[code]
            except RequestException:
                request_cache[code] = None
                return None

        scanned_workno = workno.upper()
        seed_info = fetch_product_info(scanned_workno)
        localized_workno = resolve_edition_workno_for_locale(seed_info, self.__locale.name)
        product_info = seed_info
        if localized_workno and localized_workno != normalize_workno(seed_info.get('workno')):
            localized_info = try_fetch_product_info(localized_workno)
            if localized_info is not None:
                product_info = localized_info

        translation_info = product_info.get('translation_info', None)
        original_workno = translation_info.get('original_workno', None) if translation_info else None
        original_product_info = None
        if original_workno:
            original_key = normalize_workno(original_workno)
            if original_key and original_key in request_cache:
                original_product_info = request_cache[original_key]
            else:
                original_product_info = try_fetch_product_info(original_workno)

        metadata: WorkMetadata = {
            'metadata_schema_version': METADATA_SCHEMA_VERSION,
            'scraper_locale': self.__locale.name,
            'rjcode': scanned_workno,
            'work_name': product_info['work_name'],
            'maker_id': original_product_info['maker_id'] if original_product_info else product_info['maker_id'],
            'maker_name': original_product_info['maker_name'] if original_product_info else product_info['maker_name'],
            'release_date': product_info['regist_date'][0:10],
            'series_name': original_product_info['series_name'] if original_product_info else product_info['series_name'],
            'series_id': original_product_info['series_id'] if original_product_info else product_info['series_id'],
            'age_category': '',
            'tags': [],
            'cvs': [],
            'cover_url': 'https:' + product_info['image_main']['url'],
            'rjcodes_by_locale': collect_rjcodes_by_locale(
                seed_info,
                original_product_info,
                try_fetch_product_info,
                scraper_locale=self.__locale.name,
            ),
        }

        # tags
        for genre in product_info['genres']:
            metadata['tags'].append(genre['name'])
        # cvs
        if isinstance(product_info['creaters'], dict) and 'voice_by' in product_info['creaters']:
            for cv in product_info['creaters']['voice_by']:
                metadata['cvs'].append(cv['name'])

        # age_category
        if product_info['age_category'] == 1:
            metadata['age_category'] = 'GEN'
        elif product_info['age_category'] == 2:
            metadata['age_category'] = 'R15'
        else:  # product_info['age_category'] == 3
            metadata['age_category'] = 'R18'

        return metadata
    
    # 获取封面图片链接
    @staticmethod
    def __parse_icon(html: str):
        d = pq(html)
        # parse icon
        work_icon_url_ = str(d('#work_left > div > div > div.product-slider-data > div:nth-child(1)').attr('data-src'))
        work_icon_url = "https:" + work_icon_url_
        return work_icon_url

    def urlretrieve(self, url: str,
                    filename: Union[os.PathLike, str]) -> tuple[str, dict[str, str]]:
        """"
        https://gist.github.com/xflr6/f29ed682f23fd27b6a0b1241f244e6c9
        """
        with contextlib.closing(requests.get(url, stream=True, proxies=self.__proxies)) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8_192):
                    f.write(chunk)

        return filename, r.headers

    def scrape_icon(self, rjcode: str, cover_url: str, icon_dir: str, force: bool = False):
        """
        下载图片并生成.ico文件
        """
        icon_name = f'@folder-icon-{rjcode}.ico'
        jpg_name = 'cover.jpg'
        icon_path = Path(os.path.join(icon_dir, icon_name))
        jpg_path = Path(os.path.join(icon_dir, jpg_name))

        if force:
            icon_path.unlink(missing_ok=True)
            jpg_path.unlink(missing_ok=True)

        if force or not icon_path.exists():
            self.urlretrieve(cover_url, jpg_path)  # 爬取作品图片

            # 用 .jpg 文件生成 .ico 文件
            image = img.open(jpg_path).convert('RGBA')
            x, y = image.size
            size = max(x, y)
            new_im = img.new('RGBA', (size, size), (255, 255, 255, 0))
            new_im.paste(image, ((size - x) // 2, (size - y) // 2))
            new_im.save(icon_path, format='ICO', sizes=[(256, 256)])

        return icon_name, jpg_name  # 返回值用于后续删存操作
