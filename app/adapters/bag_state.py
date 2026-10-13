"""Distinguish a rendered empty bag from a bag that is still loading."""
import asyncio
from playwright.async_api import Error as PlaywrightError
from app.models.flow import FlowError, UserAction


class BagConfigurationMismatch(FlowError):
    """A single correct model/quantity has a different color or capacity."""


BAG_STATE_SCRIPT = r'''wait => {
  const visible=e=>!!e.getClientRects().length && !['hidden','collapse'].includes(getComputedStyle(e).visibility);
  const text=e=>(e.getAttribute('aria-label') || e.innerText || '').replace(/\s+/g,' ').trim();
  const read=()=>({
    items:Array.from(document.querySelectorAll('button,[role="button"]')).filter(visible).filter(e=>/^移除\s/.test(text(e))).length,
    empty:Array.from(document.querySelectorAll('h1,[role="heading"][aria-level="1"]')).filter(visible)
      .some(e=>/^(?:你的)?购物袋(?:目前)?(?:是空的|为空|(?:中)?没有商品)[。.!！]?$/.test(text(e)))
  });
  const ready=s=>s.items>0 || s.empty;
  const initial=read();if(ready(initial))return initial;
  return new Promise(resolve=>{
    const finish=s=>{observer.disconnect();clearTimeout(timer);resolve(s)};
    const observer=new MutationObserver(()=>{const s=read();if(ready(s))finish(s)});
    observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true,characterData:true});
    const timer=setTimeout(()=>finish(read()),wait);
  });
}'''


# Public merchandise fields only. Never dump the bag body, arbitrary links,
# hidden inputs, cookies, or a later checkout page containing contact details.
BAG_DETAILS_SCRIPT = r'''() => {
  const visible=e=>!!e.getClientRects().length && !['hidden','collapse'].includes(getComputedStyle(e).visibility)
    && !e.closest('[hidden],[aria-hidden="true"]');
  const text=e=>(e.innerText || '').replace(/\s+/g,' ').trim();
  const items=Array.from(document.querySelectorAll('li[data-autom^="bag-item-"]'));
  const roots=items.length?items:[document.querySelector('main,[role="main"]') || document.body];
  const headings=roots.flatMap(r=>Array.from(r.querySelectorAll('h2,[role="heading"][aria-level="2"]'))).filter(visible);
  const merchandiseLinks=Array.from(new Set(headings.flatMap(h=>Array.from(
    (h.closest('li[data-autom^="bag-item-"]') || h).querySelectorAll('a[href]')))));
  const links=merchandiseLinks.filter(visible).flatMap(a=>{
    const u=new URL(a.href,location.href);
    if(u.protocol!=='https:' || !/^(?:www|secure\d*)\.apple\.com\.cn$/.test(u.hostname))return [];
    const m=u.pathname.match(/^\/shop\/(?:product|buy-iphone\/iphone-[a-z0-9-]+)\/([a-z0-9]{5,16})\/a\/?$/i);
    return m?[m[1].toLowerCase()+'/a']:[];
  });
  const labelText=l=>{const c=l.cloneNode(true);c.querySelectorAll('select,input').forEach(e=>e.remove());
    return (c.textContent || '').replace(/\s+/g,' ').trim();};
  const quantities=Array.from(document.querySelectorAll('select')).filter(visible).filter(e=>
    (e.getAttribute('aria-label') || Array.from(e.labels || []).map(labelText).join(' ').trim())==='数量');
  const remove_count=Array.from(document.querySelectorAll('button,[role="button"]')).filter(visible)
    .filter(e=>/^移除\s/.test(e.getAttribute('aria-label') || text(e))).length;
  const total=Array.from(document.querySelectorAll('h1,[role="heading"][aria-level="1"]')).filter(visible)
    .map(text).find(t=>/总计\s*RMB/.test(t)) || '';
  return {titles:headings.map(text),skus:Array.from(new Set(links)),quantities:quantities.map(e=>e.value),remove_count,total};
}'''


async def bag_details(page):
    return await page.evaluate(BAG_DETAILS_SCRIPT)


async def bag_has_items(page,control,guard,*,timeout=15):
    deadline=asyncio.get_running_loop().time()+timeout
    while asyncio.get_running_loop().time()<deadline:
        control.check()
        await guard(page)
        try:snapshot=await page.evaluate(BAG_STATE_SCRIPT,min(500,max(1,int((deadline-asyncio.get_running_loop().time())*1000))))
        except PlaywrightError:
            raise UserAction('官网购物袋读取未完成，当前页面保留；不会再次加购') from None
        if snapshot['items']:return True
        if snapshot['empty']:return False
    raise UserAction('官网购物袋仍在加载，不能判定为空；当前页面保留，不重复加购')
