"""Public Apple product choices. No SKU generation or account data."""
from __future__ import annotations
from collections import OrderedDict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from app.models.config import apple_url, money
from app.utils.paths import resource_path


OPTION_TITLES={'dimensionColor':'颜色','dimensionCapacity':'容量','dimensionModel':'机型',
    'dimensionScreensize':'屏幕尺寸','dimensionScreenSize':'屏幕尺寸','dimensionMemory':'内存',
    'dimensionStorage':'存储容量','dimensionProcessor':'芯片','dimensionBandSize':'表带尺寸',
    'dimensionCaseSize':'表壳尺寸','dimensionFinish':'外观'}


def choice_label(label, value=''):
    text=re.split(r'脚注|RMB|人民币',label)[0].strip()
    return text or str(value)


def groups_from_controls(controls):
    groups=OrderedDict()
    for control in controls:
        if control.get('visible') is False:continue
        key=control.get('name','')
        if not key or not ('dimension' in key.lower() or control.get('kind')=='select'):continue
        group=groups.setdefault(key,{'key':key,'title':OPTION_TITLES.get(key,control.get('group') or f'选项 {len(groups)+1}'),'choices':[]})
        candidates=control.get('options',[]) if control.get('kind')=='select' else [control]
        for c in candidates:
            value=c.get('value','');label=c.get('label','')
            if not value or any(old['value']==value for old in group['choices']):continue
            match=re.search(r'RMB\s*([\d,]+(?:\.\d+)?)',label)
            choice={'value':value,'label':choice_label(label,value),'disabled_when_read':bool(c.get('disabled',False))}
            if match:choice['price']=str(money(match[1]))
            group['choices'].append(choice)
    return [g for g in groups.values() if g['choices']]


def normalize_name(name):
    return re.sub(r'\s+','',re.sub(r'^(购买|选购)\s*','',name)).casefold()


def clean_name(name):
    return re.sub(r'^(购买|选购)\s*','',re.sub(r'\s+',' ',name)).strip()


def preorder_time(note, *, now=None):
    match=re.search(r'(\d+)\s*月\s*(\d+)\s*日.{0,4}(?:晚|晚上)?\s*(\d+)\s*点接受预购',note)
    if not match:return None
    now=now or datetime.now(ZoneInfo('Asia/Shanghai'));hour=int(match[3])
    if '晚' in match[0] and hour<12:hour+=12
    try:return datetime(now.year,int(match[1]),int(match[2]),hour,tzinfo=ZoneInfo('Asia/Shanghai'))
    except ValueError:return None


def exact_product_link(items, name):
    matches={i['url'] for i in items if apple_url(i.get('url','')) and normalize_name(i.get('name',''))==normalize_name(name)}
    return next(iter(matches)) if len(matches)==1 else None


def quoted_total(offer, options, quantity=1):
    prices=[]
    for group in offer.get('groups',[]):
        selected=next((c for c in group['choices'] if c['value']==options.get(group['key'])),None)
        if selected and selected.get('price'):prices.append(money(selected['price']))
    # Radio prices can be a base price for a configurable product. Treat it as
    # a displayed quote only; a higher bag/review total requires a fresh choice.
    return str(max(prices)*quantity) if prices else str(money(offer['base_price'])*quantity) if offer.get('base_price') else '0.00'


class CatalogCache:
    def __init__(self, root:Path):
        self.path=Path(root)/'public-catalog.json'

    def load(self):
        for path in (self.path,resource_path('services/official_catalog.json')):
            try:
                raw=json.loads(path.read_text(encoding='utf-8'))
                products=[p for p in raw.get('products',[]) if p.get('name') and apple_url(p.get('url','')) and not re.fullmatch(r'/shop/buy-[^/]+/?',urlsplit(p['url']).path)]
                if products:return products
            except (OSError,ValueError,TypeError,AttributeError,KeyError):pass
        return []

    def save(self, products):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.path.write_text(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),'products':products},ensure_ascii=False,indent=2),encoding='utf-8')
        self.path.chmod(0o600)
