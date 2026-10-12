from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.config import Settings, Store
from app.models.flow import Availability
from app.services.stores import DIRECTORY_URL, StoreDirectory, parse_directory


def test_bundled_mainland_directory_has_public_addresses_and_unique_ids():
    directory = StoreDirectory()
    assert len(directory.rows) == 49
    assert len(directory.by_id) == len(directory.rows)
    assert len({r['city'] for r in directory.rows}) == 25
    assert all(r['url'].startswith('https://www.apple.com.cn/retail/') and r['address']
               and r['province'] and r['district'] for r in directory.rows)
    assert DIRECTORY_URL == 'https://www.apple.com.cn/retail/storelist/'


def test_public_directory_repairs_old_overwritten_geography_without_enabling_stores():
    directory = StoreDirectory()
    old = Store('Apple 三里屯', city='上海', province='上海', district='黄浦区', enabled=False)
    repaired = directory.enrich(old)
    assert (repaired.province, repaired.city, repaired.district) == ('北京', '北京', '朝阳区')
    assert repaired.store_id and not repaired.enabled
    assert old.city == '上海' and not old.store_id
    unknown = Store('官网尚未收录的新门店', city='离线城市', province='离线省份')
    assert directory.enrich(unknown) == unknown


def test_bad_or_partial_directory_cannot_replace_the_existing_catalog():
    with pytest.raises(ValueError, match='完整读取'):
        parse_directory('<h1>马上回来</h1>')
    rows = ''.join(f'''<div class="store-address"><span>上海, 上海</span>
        <a data-store-number="R{i}" href="/retail/fixture{i}/">测试门店{i}</a>
        <address>上海市黄浦区测试路{i}号<br>400-000-0000</address></div>''' for i in range(20))
    parsed = parse_directory('<h2 class="state-headline">上海</h2>'+rows)
    assert len(parsed['stores']) == 20
    assert all(r['district'] == '黄浦区' for r in parsed['stores'])
    assert all('400-' not in r['address'] for r in parsed['stores'])
    with pytest.raises(ValueError):
        parse_directory('<h2 class="state-headline">上海</h2>'+rows.replace('R1"', 'R0"'))
    with pytest.raises(ValueError, match='中国大陆'):
        parse_directory('<h2 class="state-headline">上海</h2>'+rows.replace('/retail/fixture0/', 'https://attacker.invalid/'))


@pytest.mark.parametrize('second_city_available', [False, True])
async def test_city_queries_preserve_interleaved_store_priority_and_skip_unneeded_cities(second_city_available):
    directory = StoreDirectory()
    preferred = [directory.enrich(Store(name)) for name in
                 ('Apple 南京东路', 'Apple 三里屯', 'Apple 上海环贸 iapm', 'Apple 西湖')]
    config = Settings(stores=preferred)
    events, queries = [], []
    adapter = AppleAdapter(SimpleNamespace(on=lambda *_:None), Control(False),
                           progress=lambda kind, data:events.append((kind, data)))

    async def read(region, *, publish=True):
        queries.append((region.stores[0].province, region.stores[0].city))
        assert not publish
        return [Availability(s, s.name == 'Apple 上海环贸 iapm' or
                             (s.name == 'Apple 三里屯' and second_city_available), '离线供货结果')
                for s in region.stores]

    adapter._read_availability = read
    result = await adapter.availability(config)
    assert queries == [('上海', '上海'), ('北京', '北京')]
    available = {a.store.name for a in result if a.available}
    assert 'Apple 上海环贸 iapm' in available
    assert ('Apple 三里屯' in available) == second_city_available
    assert len([e for e in events if e[0] == 'stores']) == 1
    assert config.stores == preferred


async def test_same_city_stores_need_one_query_and_disabled_stores_need_none():
    directory = StoreDirectory()
    config = Settings(stores=[directory.enrich(Store(name)) for name in
                             ('Apple 南京东路', 'Apple 五角场', 'Apple 七宝')])
    config.stores.append(directory.enrich(Store('Apple 三里屯', enabled=False)))
    queries = []
    adapter = AppleAdapter(SimpleNamespace(on=lambda *_:None), Control(False))

    async def read(region, *, publish=True):
        queries.append(deepcopy(region.stores))
        return [Availability(s, True, '离线可取货') for s in region.stores]

    adapter._read_availability = read
    assert len(await adapter.availability(config)) == 3
    assert len(queries) == 1 and all(s.city == '上海' for s in queries[0])
