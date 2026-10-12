"""Compare a normally started, app-owned Chrome through its supported CDP API.

Only a public product and its pickup controls are read. No existing personal
profile, cookie export, request replay, fingerprint changes or order actions.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import socket
from tempfile import TemporaryDirectory
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.async_api import async_playwright, expect
from app.adapters.apple import AppleAdapter
from app.automation.control import Control, ProcessLock
from app.models.config import Settings
from app.models.flow import FlowError
from app.utils.paths import data_dir


async def inspect(args):
    config=Settings.from_dict(json.loads((Path(__file__).parent/'stocked-example.json').read_text()))
    config.product.url='https://www.apple.com.cn/shop/buy-iphone/iphone-17/mg6x4ch/a'
    config.auto_submit=False; config.transmit_identity=False; config.profile_id=''
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'browser':'installed Chrome, native launch + CDP',
            'fresh_profile':True,'context_defaults':'unchanged','checks':[], 'events':[],
            'visit_store_first':args.visit_store,
            'radio_activation':'associated label click' if args.click_labels else 'app keyboard',
            'debug_port_mode':'available localhost port' if args.local_port else 'browser assigned',
            'added_to_bag':False,'checkout_started':False,'orders_submitted':0}
    started=time.monotonic(); phase='browser_start'; pending=[]
    with ProcessLock(data_dir()/'autobuyer.run.lock'):
        with TemporaryDirectory(prefix='autobuyer-native-chrome-') as profile_name:
            profile=Path(profile_name); profile.chmod(0o700)
            chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
            if not chrome.exists():raise FlowError('未安装正式 Google Chrome')
            debug_port=0
            if args.local_port:
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1',0));debug_port=listener.getsockname()[1]
            process=await asyncio.create_subprocess_exec(str(chrome),f'--user-data-dir={profile}',
                f'--remote-debugging-port={debug_port}','--remote-debugging-address=127.0.0.1',
                '--no-first-run','--no-default-browser-check','about:blank',
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            browser=None
            try:
                port_file=profile/'DevToolsActivePort'
                deadline=time.monotonic()+15
                while not debug_port and not port_file.exists():
                    if process.returncode is not None or time.monotonic()>deadline:
                        raise FlowError('正常 Chrome 启动或本机连接未完成')
                    await asyncio.sleep(.1)
                port=debug_port or int(port_file.read_text().splitlines()[0])
                async with async_playwright() as pw:
                    while True:
                        try:
                            browser=await pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}',no_defaults=True,timeout=1000)
                            break
                        except Exception:
                            if process.returncode is not None or time.monotonic()>deadline:raise
                            await asyncio.sleep(.1)
                    context=browser.contexts[0]
                    page=await context.new_page(); page.set_default_timeout(15000)
                    def progress(kind,data):
                        if kind in ('http_status','stock_query'):
                            report['events'].append({'kind':kind,'phase':phase,**data})
                    async def diagnostic(response):
                        path=urlsplit(response.url).path
                        if response.status!=541 or not path.startswith('/shop/'):return
                        entry={'path':path,'status':541,'content_type':response.headers.get('content-type','')}
                        try:
                            body=await response.text()
                            title=re.search(r'<title[^>]*>(.*?)</title>',body,re.I|re.S)
                            entry.update(body_length=len(body),html=bool(re.match(r'\s*(<!doctype|<html)',body,re.I)),
                                         public_template_title=re.sub(r'<[^>]+>','',title[1]).strip()[:120] if title else '')
                        except Exception:entry['body_unavailable']=True
                        report.setdefault('rejection_diagnostics',[]).append(entry)
                    def observe(response):pending.append(asyncio.create_task(diagnostic(response)))
                    page.on('response',observe)
                    adapter=AppleAdapter(page,Control(False),progress=progress)
                    if args.click_labels:
                        async def mouse_radio(loc):
                            await expect(loc).to_be_enabled(timeout=15000)
                            if await loc.is_checked():return
                            control_id=await loc.get_attribute('id')
                            label=page.locator('label[for='+json.dumps(control_id)+']').filter(visible=True) if control_id else None
                            if label is not None and await label.count()==1:
                                await label.click(timeout=15000)
                            else:
                                await loc.click(timeout=15000)
                            await expect(loc).to_be_checked(timeout=15000)
                        adapter._check_radio=mouse_radio
                    if args.visit_store:
                        phase='normal_store_entry'
                        await page.goto('https://www.apple.com.cn/store',wait_until='load',timeout=20000)
                        await adapter.guard()
                        report['store_entry_heading_count']=await page.get_by_role('heading',level=1).count()
                    phase='prepare';await adapter.prepare(config)
                    phase='opening_check'
                    if not await adapter.is_open():raise FlowError('官网购买页当前未开放')
                    phase='product_selection';await adapter.select_product(config)
                    phase='stock_query'
                    try:
                        states=await adapter.availability(config)
                        report['checks'].append({'checked_at':datetime.now(timezone.utc).isoformat(),
                            'stores':[{'name':s.store.name,'id':s.store.store_id,'available':s.available,'quote':s.quote} for s in states]})
                        report['state']='COMPLETED'
                    except FlowError as exc:
                        report.update(state='PAUSED',phase=phase,message=str(exc))
                    if args.observe_seconds:
                        print(json.dumps({'phase':'paused_observation','wait_seconds':args.observe_seconds}),flush=True)
                        await asyncio.sleep(args.observe_seconds)
                    report['public_query_buttons']=await page.get_by_role('main').get_by_role('button',name=re.compile('供货|Apple')).all_text_contents()
                    if args.screenshot:
                        path=Path(args.screenshot);path.parent.mkdir(parents=True,exist_ok=True)
                        await page.screenshot(path=str(path));path.chmod(0o600)
                    if pending:await asyncio.gather(*pending,return_exceptions=True)
                    await browser.close();browser=None
            except Exception as exc:
                report.update(state='PAUSED',phase=phase,message=str(exc) if isinstance(exc,FlowError) else f'正常启动对照未完成（{type(exc).__name__}）')
                if pending:await asyncio.gather(*pending,return_exceptions=True)
            finally:
                if process.returncode is None:
                    process.terminate()
                    try:await asyncio.wait_for(process.wait(),5)
                    except asyncio.TimeoutError:process.kill();await process.wait()
    report['elapsed']=round(time.monotonic()-started,2)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2));path.chmod(0o600)
    print(json.dumps({'state':report['state'],'completed_checks':len(report['checks']),
        'message':report.get('message',''),'elapsed':report['elapsed'],'orders_submitted':0},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='reports/apple-native-chrome.json')
    parser.add_argument('--screenshot')
    parser.add_argument('--observe-seconds',type=int,choices=range(0,181),default=0)
    parser.add_argument('--visit-store',action='store_true',help='Visit the normal storefront first so the website can initialize its own session')
    parser.add_argument('--click-labels',action='store_true',help='Compare ordinary mouse activation of actual associated product labels')
    parser.add_argument('--local-port',action='store_true',help='Use an available localhost port for the supported Chrome debugging connection')
    asyncio.run(inspect(parser.parse_args()))
