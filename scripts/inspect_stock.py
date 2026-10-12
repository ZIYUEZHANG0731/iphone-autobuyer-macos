"""Read public stock through the app's normal browser; never add or check out."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.adapters.apple import AppleAdapter
from app.automation.browser import BrowserSession
from app.automation.control import Control, ProcessLock
from app.models.config import Settings
from app.models.flow import FlowError
from app.utils.paths import data_dir


async def inspect(args):
    config=Settings.from_dict(json.loads((Path(__file__).parent/'stocked-example.json').read_text()))
    if args.product_url:config.product.url=args.product_url
    # Configuration has only public product/store data. No Keychain access.
    config.auto_submit=False;config.transmit_identity=False;config.profile_id=''
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'browser':'app Chromium',
            'headless':args.headless,'wait_for_load':args.wait_for_load,'product':config.product.name,'checks':[],
            'added_to_bag':False,'checkout_started':False,'orders_submitted':0}
    root=data_dir()
    with ProcessLock(root/'autobuyer.run.lock'):
        async with BrowserSession(root,headless=args.headless) as session:
            report['browser']=session.label
            events=[]
            phase='prepare'
            def progress(kind,data):
                if kind in ('http_status','stock_query'):
                    events.append({'kind':kind,'phase':phase,**data})
            adapter=AppleAdapter(session.page,Control(False),progress=progress)
            started=time.monotonic()
            try:
                await adapter.prepare(config)
                if args.wait_for_load:
                    phase='page_load'
                    await session.page.wait_for_load_state('load',timeout=20000)
                    await adapter.guard()
                phase='opening_check'
                if not await adapter.is_open():
                    raise FlowError('官网购买页当前未开放')
                phase='product_selection'
                await adapter.select_product(config)
                for index in range(args.checks):
                    if index:
                        await asyncio.sleep(args.interval)
                    phase='stock_query'
                    states=await adapter.availability(config)
                    record={'checked_at':datetime.now(timezone.utc).isoformat(),
                            'stores':[{'name':s.store.name,'id':s.store.store_id,'available':s.available,'quote':s.quote} for s in states]}
                    report['checks'].append(record)
                    print(json.dumps({'check':index+1,'stores_read':len(states),'elapsed':round(time.monotonic()-started,2)},ensure_ascii=False),flush=True)
                report['state']='COMPLETED'
                if args.screenshot:
                    # Only the public product modal, before any bag or contact step.
                    path=Path(args.screenshot);path.parent.mkdir(parents=True,exist_ok=True)
                    await session.page.screenshot(path=str(path));path.chmod(0o600)
            except Exception as exc:
                report['state']='PAUSED'
                report['phase']=phase
                report['message']=str(exc) if isinstance(exc,FlowError) else f'供货读取未完成（{type(exc).__name__}）'
                from urllib.parse import urlsplit
                if urlsplit(session.page.url).path.startswith('/shop/buy-'):
                    headings=session.page.get_by_role('heading',name=__import__('re').compile('供货情况')).filter(visible=True)
                    report['public_modal_structure']={
                        'dialog_count':await session.page.get_by_role('dialog').count(),
                        'stock_heading_count':await headings.count(),
                        'heading_ancestors':await headings.evaluate_all('''els=>els.map(e=>{const chain=[];for(let n=e;n&&chain.length<7;n=n.parentElement)chain.push({tag:n.tagName,role:n.getAttribute('role'),class:n.className,id:n.id});return chain})'''),
                    }
                    if args.screenshot:
                        path=Path(args.screenshot);path.parent.mkdir(parents=True,exist_ok=True)
                        await session.page.screenshot(path=str(path));path.chmod(0o600)
                if args.observe_seconds:
                    phase='paused_observation'
                    print(json.dumps({'phase':phase,'wait_seconds':args.observe_seconds,'extra_requests':False}),flush=True)
                    await asyncio.sleep(args.observe_seconds)
            finally:
                report['events']=events;report['elapsed']=round(time.monotonic()-started,2)
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2));output.chmod(0o600)
    print(json.dumps({'state':report['state'],'completed_checks':len(report['checks']),
                      'message':report.get('message',''),'elapsed':report['elapsed'],'orders_submitted':0},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checks',type=int,choices=range(1,4),default=2)
    parser.add_argument('--interval',type=float,default=90)
    parser.add_argument('--headless',action='store_true')
    parser.add_argument('--wait-for-load',action='store_true',help='Controlled readiness experiment; normal page load, no custom challenge requests')
    parser.add_argument('--output',default='reports/apple-stock-software.json')
    parser.add_argument('--screenshot')
    parser.add_argument('--product-url',help='An observed public Apple product URL; no identity or checkout actions')
    parser.add_argument('--observe-seconds',type=int,choices=range(0,181),default=0,help='Keep the app-owned failed public page open for native UI diagnosis')
    args=parser.parse_args()
    if args.interval<60:parser.error('公开官网验证的检查间隔至少 60 秒')
    asyncio.run(inspect(args))
