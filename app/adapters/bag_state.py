"""Distinguish a rendered empty bag from a bag that is still loading."""
import asyncio
from playwright.async_api import Error as PlaywrightError
from app.models.flow import UserAction


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
