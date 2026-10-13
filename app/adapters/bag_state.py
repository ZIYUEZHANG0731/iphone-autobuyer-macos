"""Distinguish a rendered empty bag from a bag that is still loading."""
import asyncio
from playwright.async_api import Error as PlaywrightError
from app.models.flow import UserAction


BAG_STATE_SCRIPT = r'''wait => {
  const visible=e=>!!e.getClientRects().length && !['hidden','collapse'].includes(getComputedStyle(e).visibility);
  const text=e=>(e.getAttribute('aria-label') || e.innerText || '').replace(/\s+/g,' ').trim();
  const read=()=>{
    const headings=Array.from(document.querySelectorAll('h1,[role="heading"][aria-level="1"]')).filter(visible).map(text);
    const items=Array.from(document.querySelectorAll('button,[role="button"]')).filter(visible).filter(e=>/^移除\s/.test(text(e))).length;
    const quantities=Array.from(document.querySelectorAll('select')).filter(visible).filter(e=>
      /数量/.test(e.getAttribute('aria-label') || Array.from(e.labels||[]).map(text).join(' ')));
    return {items,
      loaded:document.readyState==='complete',
      fingerprint:headings.join('|')+'|'+Array.from(document.querySelectorAll('h2')).filter(visible).map(text).join('|')+'|'+quantities.map(e=>e.value).join(','),
      empty:headings.some(t=>/^(?:你的)?购物袋(?:目前)?(?:是空的|为空|(?:中)?没有商品)[。.!！]?$/.test(t)),
      complete:items>0 && headings.some(t=>/总计\s*RMB\s*[\d,]+/.test(t)) &&
        Array.from(document.querySelectorAll('h2,[role="heading"][aria-level="2"]')).filter(visible).length>=items &&
        quantities.length===items && quantities.every(e=>e.value && !e.disabled),
      busy:Array.from(document.querySelectorAll('[aria-busy="true"]')).some(visible)};
  };
  const ready=s=>!s.busy && (s.complete || (document.readyState==='complete' && s.empty && !s.items));
  const initial=read();if(ready(initial))return initial;
  return new Promise(resolve=>{
    const finish=s=>{observer.disconnect();clearTimeout(timer);resolve(s)};
    const observer=new MutationObserver(()=>{const s=read();if(ready(s))finish(s)});
    observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true,characterData:true});
    const timer=setTimeout(()=>finish(read()),wait);
  });
}'''


async def bag_has_items(page,control,guard,*,timeout=15,expect_items=False,settle=1):
    deadline=asyncio.get_running_loop().time()+timeout
    previous=None;stable_since=None
    while asyncio.get_running_loop().time()<deadline:
        control.check()
        await guard(page)
        try:snapshot=await page.evaluate(BAG_STATE_SCRIPT,min(500,max(1,int((deadline-asyncio.get_running_loop().time())*1000))))
        except PlaywrightError:
            raise UserAction('官网购物袋读取未完成，当前页面保留；不会再次加购') from None
        ready=not snapshot['busy'] and (snapshot['complete'] or (snapshot['loaded'] and snapshot['empty'] and not snapshot['items'] and not expect_items))
        if not ready:
            previous=None;stable_since=None
        elif snapshot!=previous:
            previous=snapshot;stable_since=asyncio.get_running_loop().time()
        elif asyncio.get_running_loop().time()-stable_since>=settle:
            return bool(snapshot['items'])
        await control.sleep(.1)
    if expect_items:
        raise UserAction('加购后购物袋仍未稳定显示目标商品，页面已保留；不会重复加购')
    raise UserAction('官网购物袋仍在加载，不能判定为空；当前页面保留，不重复加购')
