"""One public stock read with installed Chrome and a disposable separate profile."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.async_api import async_playwright
from app.adapters.apple import AppleAdapter
from app.automation.control import Control, ProcessLock
from app.models.config import Settings
from app.models.flow import FlowError
from app.utils.paths import data_dir


async def inspect(args):
    config = Settings.from_dict(json.loads((Path(__file__).parent / 'stocked-example.json').read_text()))
    config.auto_submit = False
    config.transmit_identity = False
    config.profile_id = ''
    report = {
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'browser_channel': 'chrome', 'fresh_profile': True, 'headless': False,
        'locale': 'system' if args.natural_context else 'zh-CN',
        'timezone': 'system' if args.natural_context else 'Asia/Shanghai',
        'service_workers': 'default' if args.natural_context else args.service_workers,
        'settle_seconds':args.settle_seconds,'checks': [], 'events': [], 'added_to_bag': False,
        'checkout_started': False, 'orders_submitted': 0,
        'limitation': 'Browser version and profile both differ from the earlier app trial; this is not an isolated causal test.'
    }
    phase = 'browser_start'
    started = time.monotonic()
    context = None
    diagnostics=[]
    with ProcessLock(data_dir() / 'autobuyer.run.lock'):
        with TemporaryDirectory(prefix='autobuyer-chrome-compat-') as profile:
            Path(profile).chmod(0o700)
            async with async_playwright() as playwright:
                try:
                    options={} if args.natural_context else {'locale':'zh-CN','timezone_id':'Asia/Shanghai','service_workers':args.service_workers}
                    context = await playwright.chromium.launch_persistent_context(
                        profile, channel='chrome', headless=False, **options,
                        handle_sigint=False, handle_sigterm=False, handle_sighup=False)
                    page = await context.new_page()
                    page.set_default_timeout(15000)
                    page.set_default_navigation_timeout(30000)

                    async def diagnostic(response):
                        if response.status!=541:return
                        from urllib.parse import urlsplit
                        path=urlsplit(response.url).path
                        if not path.startswith('/shop/'):return
                        entry={'path':path,'status':541,'content_type':response.headers.get('content-type','')}
                        try:
                            body=await response.text()
                            entry.update(body_length=len(body),html=body.lstrip().lower().startswith(('<!doctype','<html')),
                                         shield_marker=bool(re.search(r'Apple.?Shield|/shield/|shield\.apple|shield-check',body,re.I)),
                                         captcha_marker=bool(re.search(r'captcha|verify.you.are.human',body,re.I)))
                        except Exception:entry['body_unavailable']=True
                        report.setdefault('rejection_diagnostics',[]).append(entry)

                    def response_diagnostic(response):
                        task=asyncio.create_task(diagnostic(response));diagnostics.append(task)
                    page.on('response',response_diagnostic)

                    def progress(kind, data):
                        if kind in ('http_status', 'stock_query'):
                            report['events'].append({'kind': kind, 'phase': phase, **data})

                    adapter = AppleAdapter(page, Control(False), progress=progress)
                    phase = 'prepare'
                    await adapter.prepare(config)
                    if args.settle_seconds:
                        phase='page_settle'
                        print(json.dumps({'phase':phase,'wait_seconds':args.settle_seconds,'extra_requests':False}),flush=True)
                        await asyncio.sleep(args.settle_seconds)
                        await adapter.guard()
                    phase = 'opening_check'
                    if not await adapter.is_open():
                        raise FlowError('官网购买页当前未开放')
                    phase = 'product_selection'
                    await adapter.select_product(config)
                    phase = 'stock_query'
                    states = await adapter.availability(config)
                    report['checks'].append({
                        'checked_at': datetime.now(timezone.utc).isoformat(),
                        'stores': [{'name': s.store.name, 'id': s.store.store_id,
                                    'available': s.available, 'quote': s.quote} for s in states],
                    })
                    report['state'] = 'COMPLETED'
                    if args.screenshot:
                        path = Path(args.screenshot)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        await page.screenshot(path=str(path))
                        path.chmod(0o600)
                except Exception as exc:
                    report['state'] = 'PAUSED'
                    report['phase'] = phase
                    report['message'] = str(exc) if isinstance(exc, FlowError) else f'对照读取未完成（{type(exc).__name__}）'
                finally:
                    if diagnostics:await asyncio.gather(*diagnostics,return_exceptions=True)
                    if context:
                        await context.close()
    report['elapsed'] = round(time.monotonic() - started, 2)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    path.chmod(0o600)
    print(json.dumps({
        'state': report['state'], 'completed_checks': len(report['checks']),
        'message': report.get('message', ''), 'elapsed': report['elapsed'],
        'orders_submitted': 0,
    }, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='reports/apple-stock-chrome-compat.json')
    parser.add_argument('--screenshot')
    parser.add_argument('--service-workers',choices=('block','allow'),default='block',help='Normal website feature compatibility comparison')
    parser.add_argument('--natural-context',action='store_true',help='Use the installed browser normal locale, timezone and site-feature settings')
    parser.add_argument('--settle-seconds',type=int,choices=range(0,181),default=0,help='Observe the first-party page after load without extra navigation')
    asyncio.run(inspect(parser.parse_args()))
