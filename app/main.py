from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys

# Direct execution and PyInstaller entry point must resolve the local package.
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.utils.paths import data_dir
from app.utils.files import write_private_json

def parser():
    p=argparse.ArgumentParser(description='Apple Store 购买准备与官网试跑')
    p.add_argument('--live-check',action='store_true',help='官网试跑，到人工接管点为止，永不提交')
    p.add_argument('--local-test',choices=['all','first_available','second_available','all_unavailable','stock_changes','missing_option','payment_unavailable','identity_incomplete','maintenance','network_timeout','submit_timeout','delayed_success','order_failure','rate_limited','stop'])
    p.add_argument('--config',type=Path)
    p.add_argument('--headless',action='store_true')
    p.add_argument('--report',type=Path)
    p.add_argument('--smoke-test',action='store_true')
    p.add_argument('--embedded-smoke-test',action='store_true',help='内嵌浏览器离线启动与正常退出检查')
    p.add_argument('--screenshot',type=Path)
    p.add_argument('--window-on-top',action='store_true',help='将软件窗口置顶，便于查看官网流程')
    p.add_argument('--install-browser',action='store_true')
    p.add_argument('--check-environment',action='store_true',help='离线检查依赖、资源及北京时间；不访问 Apple')
    return p

async def run_cli(args):
    from app.automation.browser import BrowserSession
    from app.automation.engine import Engine
    from app.automation.control import Control
    from app.adapters.apple import AppleAdapter
    from app.models.config import Mode,Settings
    from app.services.storage import Repository
    from app.services.simulation import SCENARIOS
    from app.services.local_test import run_local
    from app.services.traffic import SiteCooldown
    from app.models.flow import FlowError
    root=data_dir();repo=Repository(root/'autobuyer.sqlite3');results=[]
    if args.local_test:
        names=list(SCENARIOS) if args.local_test=='all' else [args.local_test]
        for scenario in names:
            results.append(await run_local(scenario, root, repo, Control(interactive=False),
                headless=args.headless, callback=lambda e:print(json.dumps(e,ensure_ascii=False))))
    else:
        config=Settings.from_dict(json.loads(args.config.read_text(encoding='utf-8'))) if args.config else repo.load_config()
        config.auto_submit=False;config.transmit_identity=False
        async with BrowserSession(root,headless=args.headless,kind=config.browser_kind) as session:
            cooldown=SiteCooldown(root)
            if cooldown.remaining():raise FlowError(f'官网访问暂需等待 {cooldown.remaining()} 秒；本次不发送新请求')
            control=Control(interactive=False,site_cooldown=cooldown)
            progress=[]
            def observed(kind,value):
                safe=repo.redactor.object(value);progress.append({'kind':kind,'value':safe});print(json.dumps({'kind':kind,'value':safe},ensure_ascii=False))
            adapter=AppleAdapter(session.page,control,progress=observed,public_screenshot=args.screenshot)
            engine=Engine(repo,control,callback=lambda e:print(json.dumps(e,ensure_ascii=False)))
            result=asdict(await engine.run(config,adapter,Mode.WEBSITE_DRY_RUN));result['observations']=progress
            # Only save a pre-identity screenshot from an isolated, unauthenticated run.
            # A persistent session could contain account details: deliberately skip it here.
            results.append(result)
    if args.report:
        write_private_json(args.report,results)
    return 0 if all(r['state'] not in ('FAILED',) for r in results) else 1

def main():
    from app.platforms.runtime import prepare_stdio
    prepare_stdio()
    from app.utils.browser_runtime import prepare_browser_runtime
    prepare_browser_runtime()
    args=parser().parse_args()
    if args.check_environment:
        from app.services.environment import check_environment
        result=check_environment()
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if args.report:write_private_json(args.report,result)
        return 0 if result['ok'] else 1
    if args.install_browser:
        from playwright.__main__ import main as install
        sys.argv=[sys.argv[0],'install','chromium'];return install()
    if args.live_check or args.local_test:
        from app.models.flow import FlowError
        from app.models.config import ConfigError
        try:
            return asyncio.run(run_cli(args))
        except (FlowError,ConfigError) as exc:
            result={'state':'FAILED','message':str(exc)}
        except Exception as exc:
            result={'state':'FAILED','message':f'运行未完成（{type(exc).__name__}），请检查环境或浏览器'}
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if args.report:
            write_private_json(args.report,[result])
        return 1
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete, isValid
    from app.utils.qt import prepare_plugins, single_shot
    from app.gui.window import MainWindow
    prepare_plugins()
    qt=QApplication(sys.argv[:1]);qt.setApplicationName('iPhone AutoBuyer');qt.setOrganizationName('AppleAutoBuyer')
    window=MainWindow(data_dir());window.pin_window.setChecked(args.window_on_top);window.show()
    if args.window_on_top and window.config.browser_kind=='embedded':window.ensure_embedded()
    qt.aboutToQuit.connect(window.shutdown)
    if args.embedded_smoke_test:
        from PySide6.QtCore import QUrl
        window.ensure_embedded();window.stack.setCurrentIndex(6)
        page=window.embedded_browser.views[window.embedded_browser.primary_id].page()
        finished=[False]
        fixture_started=[False]
        def embedded_done(value=None):
            if finished[0]:return
            finished[0]=True
            success=value=='Offline embedded startup check'
            if args.report:
                write_private_json(args.report,{'check':'embedded','ok':success})
            if args.screenshot and success:
                args.screenshot.parent.mkdir(parents=True,exist_ok=True);window.grab().save(str(args.screenshot))
            print('EMBEDDED_SMOKE_OK' if success else 'EMBEDDED_SMOKE_FAILED',flush=True)
            qt.exit(0 if success else 1)
        def embedded_loaded(value):
            if value=='Offline embedded startup check':embedded_done(value)
        def embedded_document(loaded):
            if not loaded:embedded_done();return
            if not fixture_started[0]:
                fixture_started[0]=True
                page.setHtml('<h1>Offline embedded startup check</h1><p>Synthetic page only.</p>',QUrl('https://autobuyer.invalid/'))
            else:page.runJavaScript("document.querySelector('h1')?.innerText",embedded_loaded)
        page.loadFinished.connect(embedded_document)
        page.renderProcessTerminated.connect(lambda *_:embedded_done())
        single_shot(window,12000,embedded_done)
    elif args.smoke_test:
        def done():
            if args.report:
                write_private_json(args.report,{'check':'gui','ok':True})
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True,exist_ok=True);window.grab().save(str(args.screenshot))
            print('GUI_SMOKE_OK',flush=True);qt.quit()
        single_shot(window,1200,done)
    try:
        return qt.exec()
    finally:
        if isValid(window):
            window.shutdown()
            # Keep QApplication and PySide's binding runtime alive while native
            # accessibility objects and all child widgets are destroyed. Do not
            # leave a populated QTableWidget to Python's atexit cleanup.
            delete(window)

if __name__=='__main__':
    raise SystemExit(main())
