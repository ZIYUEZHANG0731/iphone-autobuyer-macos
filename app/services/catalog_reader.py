"""A bounded, sequential crawl of Apple's public rendered store pages."""
import asyncio
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit
from app.models.config import apple_url, money
from app.models.flow import RateLimited, UserAction, Stopped
from app.services.catalog import groups_from_controls, clean_name, normalize_name
from copy import deepcopy


LINK_SCRIPT='''els=>els.filter(a=>!!a.getClientRects().length).map(a=>{
 let name=(a.innerText||a.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim();
 if(/^(购买|选购|预购|进一步了解)$/.test(name)){
   let parent=a.parentElement;
   for(let i=0;i<6 && parent;i++,parent=parent.parentElement){
     const headings=parent.querySelectorAll('h2,h3');
     if(headings.length===1){name=headings[0].innerText.replace(/\\s+/g,' ').trim();break;}
     if(headings.length>1)break;
   }
 }
 return {name,url:a.href.split('?')[0]};
}).filter(a=>a.name && /\\/shop\\/(buy-|product\\/)/.test(a.url))'''


def category_url(url):
    parts=urlsplit(url).path.strip('/').split('/')
    return len(parts)==2 and parts[0]=='shop' and parts[1].startswith('buy-')


async def public_links(page):
    return [item for item in await page.locator('a[href]').evaluate_all(LINK_SCRIPT) if apple_url(item['url'])]


async def read_catalog(adapter, *, existing=(), maximum=60, on_progress=None):
    on_progress=on_progress or (lambda *_:None)
    control=adapter.control;page=adapter.page
    await page.goto('https://www.apple.com.cn/shop/buy-iphone',wait_until='domcontentloaded')
    await page.get_by_role('heading',level=1).wait_for()
    await adapter.guard()
    queue=[item for item in await public_links(page) if re.fullmatch(r'/shop/buy-iphone/iphone-(?:duo|17(?:e|-pro|-pro-max)?|18(?:e|-pro|-pro-max)?)/?',urlsplit(item['url']).path)]
    seen={x['url'] for x in queue};visited=set()
    # A second collection can extend a prior crawl without re-reading its
    # already observed product pages. Category pages discover published links.
    products=[p for p in existing if p['url'] in seen]
    cached={p['url'] for p in products};failures=[];categories=[]
    index=0
    while index<len(queue) and len(visited)<maximum:
        item=queue[index];index+=1
        if item['url'] in visited or (item['url'] in cached and not category_url(item['url'])):continue
        visited.add(item['url']);await control.sleep(2)
        try:
            await control.sleep(max(0,adapter.busy_until-asyncio.get_running_loop().time()))
            response=await page.goto(item['url'],wait_until='domcontentloaded',timeout=15000)
            if response and response.status in (403,429,541):
                raise RateLimited(f'官网公开页返回 HTTP {response.status}，目录更新已暂停')
            await adapter.guard()
            if category_url(item['url']):
                categories.append(item['url'])
                for candidate in await public_links(page):
                    if candidate['url'] not in seen:
                        seen.add(candidate['url']);queue.append(candidate)
                continue
            heading=page.get_by_role('heading',level=1)
            await heading.first.wait_for(timeout=6000)
            name=clean_name(await heading.first.inner_text())
            # Give client-rendered selectors their normal opportunity to appear.
            try:await page.locator('input[name*="dimension"],select').first.wait_for(state='attached',timeout=1500)
            except Exception:pass
            controls=await adapter.options();body=await page.locator('body').inner_text()
            notice=next((s.strip() for s in body.splitlines() if '接受预购' in s or '开始预购' in s),'')
            price=re.search(r'RMB\s*([\d,]+(?:\.\d+)?)',body[:3000])
            record={'name':name or clean_name(item['name']),'url':page.url.split('?')[0],
                'groups':groups_from_controls(controls),'preorder_note':notice,
                'observed_at':datetime.now(timezone.utc).isoformat()}
            if price:record['base_price']=str(money(price[1]))
            models=[c for c in controls if c.get('kind')=='radio' and c.get('visible') and re.search(r'iPhone\s+\d+\s+Pro',c['label']) and 'Color' not in c['name']]
            variants=[]
            for model in models or [None]:
                if model:
                    await adapter._choice(model['name'],model['value'])
                    await control.sleep(.4)
                colors=[c for c in await adapter.options() if 'Color' in c['name'] and not c.get('disabled') and c.get('visible')]
                if colors:
                    await adapter._choice(colors[0]['name'],colors[0]['value']);await control.sleep(.3)
                fresh=await adapter.options();variant=deepcopy(record);variant['groups']=groups_from_controls(fresh)
                if model:
                    variant['name']=re.search(r'iPhone\s+\d+\s+Pro(?:\s+Max)?',model['label'])[0]
                    variant['locked_options']={model['name']:model['value']}
                    variant['groups']=[g for g in variant['groups'] if g['key']!=model['name']]
                variants.append(variant)
            for variant in variants:
                if not apple_url(variant['url']):continue
                key=normalize_name(variant['name'])
                duplicate=next((i for i,p in enumerate(products) if normalize_name(p['name'])==key),None)
                if duplicate is None:products.append(variant)
                else:products[duplicate]=variant
                on_progress(len(products),variant['name'])
        except Stopped:raise
        except RateLimited:
            failures.append({'name':clean_name(item['name']),'reason':'公开页面访问受限，停止采集'})
            break
        except Exception as exc:
            failures.append({'name':clean_name(item['name']),'reason':type(exc).__name__})
    # Device families precede accessories. A preorder is a preference choice,
    # never treated as evidence that checkout or pickup is already open.
    products.sort(key=lambda p:(0 if normalize_name(p['name'])=='iphoneduo' else 1 if '/shop/buy-' in p['url'] else 2,p['name']))
    if not products:raise UserAction('官网暂未提供可读取的型号；现有配置保留，稍后再更新')
    return {'checked_at':datetime.now(timezone.utc).isoformat(),'source':'Apple China public rendered store',
        'discovered_link_count':len(seen),'read_page_count':len(visited),'category_pages':categories,
        'products':products,'unread_pages':failures}
