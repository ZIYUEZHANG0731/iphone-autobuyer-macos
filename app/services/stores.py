"""Public Apple mainland retail directory, independent of product availability."""
from copy import deepcopy
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urljoin

from app.models.config import Store, apple_url
from app.utils.paths import resource_path


DIRECTORY_URL = 'https://www.apple.com.cn/retail/storelist/'


def parse_directory(html: str) -> dict:
    parser = _RetailParser()
    parser.feed(html)
    rows = parser.rows
    if not 20 <= len(rows) <= 100 or len({r['store_id'] for r in rows}) != len(rows):
        raise ValueError('官网门店目录未能完整读取；保留现有目录')
    for row in rows:
        # Some retail addresses omit their district. The stock picker needs
        # one query area in that city, not the shop's precise street district.
        # Reuse a published district from another store in the same city.
        if not row['district']:
            row['district'] = next((r['district'] for r in rows if r['city'] == row['city'] and r['district']), '')
            row['district_source'] = 'same_city_query_area'
        else:
            row['district_source'] = 'published_address'
        if not re.fullmatch(r'R\d+', row['store_id']) or not all(row.get(k) for k in ('name', 'city', 'province', 'district', 'address')):
            raise ValueError('官网门店资料不完整；保留现有目录')
        if not apple_url(row['url']):
            raise ValueError('门店链接不属于 Apple 中国大陆')
    return {'source': DIRECTORY_URL, 'checked_at': datetime.now(timezone.utc).isoformat(), 'stores': rows}


class _RetailParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.province = ''
        self.heading = False
        self.heading_text = []
        self.row = None
        self.depth = 0
        self.name = False
        self.address = False
        self.city_text = []
        self.address_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'h2' and 'state-headline' in attrs.get('class', '').split():
            self.heading = True
            self.heading_text = []
        if tag == 'div' and 'store-address' in attrs.get('class', '').split():
            self.row = {'province': self.province}
            self.depth = 1
            self.city_text, self.address_text = [], []
        elif self.row is not None and tag == 'div':
            self.depth += 1
        if self.row is None:
            return
        if tag == 'a' and attrs.get('data-store-number'):
            self.row['store_id'] = attrs['data-store-number']
            self.row['url'] = urljoin(DIRECTORY_URL, attrs.get('href', ''))
            self.row['name'] = 'Apple '
            self.name = True
        if tag == 'address':
            self.address = True
        if tag == 'br' and self.address:
            self.address_text.append('\n')

    def handle_endtag(self, tag):
        if tag == 'h2' and self.heading:
            self.province = ''.join(self.heading_text).strip()
            self.heading = False
        if self.row is None:
            return
        if tag == 'a':
            self.name = False
        if tag == 'address':
            self.address = False
        if tag == 'div':
            self.depth -= 1
            if self.depth == 0:
                row = self.row
                row['name'] = re.sub(r'\s+', ' ', row.get('name', '')).strip()
                row['city'] = ''.join(self.city_text).split(',')[0].strip()
                lines = [s.strip() for s in ''.join(self.address_text).splitlines() if s.strip()]
                row['address'] = ' '.join(s for s in lines if not re.fullmatch(r'400[-\d]+', s))
                district = re.search(re.escape(row['city']) + r'市?(.+?(?:区|县))', row['address'])
                row['district'] = district[1] if district else ''
                # The directory spells this out as 苏州工业园区; Apple's
                # region picker labels its district 工业园区.
                if row['district'] == '苏州工业园区':
                    row['district'] = '工业园区'
                self.rows.append(row)
                self.row = None

    def handle_data(self, text):
        if self.heading:
            self.heading_text.append(text)
        if self.row is not None:
            if self.name:
                self.row['name'] += text
            elif self.address:
                self.address_text.append(text)
            elif 'store_id' not in self.row:
                self.city_text.append(text)


class StoreDirectory:
    def __init__(self, path: Path | None = None):
        self.path = path or resource_path('services/official_stores.json')
        raw = json.loads(self.path.read_text(encoding='utf-8'))
        self.checked_at = raw['checked_at']
        self.rows = raw['stores']
        self.by_id = {r['store_id']: r for r in self.rows}

    def find(self, store: Store) -> dict | None:
        return self.by_id.get(store.store_id) or next((r for r in self.rows if r['name'] == store.name), None)

    def enrich(self, store: Store) -> Store:
        """Correct previously overwritten geography while preserving order/enabled."""
        result = deepcopy(store)
        if row := self.find(store):
            for key in ('name', 'city', 'store_id', 'province', 'district'):
                setattr(result, key, row[key])
        return result

    def store(self, row: dict) -> Store:
        return Store(**{k: row[k] for k in ('name', 'city', 'store_id', 'province', 'district')})
