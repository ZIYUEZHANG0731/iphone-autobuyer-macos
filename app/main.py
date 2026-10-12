from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

# Direct execution and PyInstaller entry point must resolve the local package.
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.utils.paths import data_dir

def parser():
    p=argparse.ArgumentParser(description='Apple Store macOS 购买准备与官网试跑')
    p.add_argument('--live-check',action='store_true',help='官网试跑，到人工接管点为止，永不提交')
    p.add_argument('--local-test',choices=['all','first_available','second_available','all_unavailable','stock_changes','missing_option','payment_unavailable','identity_incomplete','maintenance','network_timeout','submit_timeout','delayed_success','order_failure','rate_limited','stop'])
    p.add_argument('--config',type=Path)
    p.add_argument('--headless',action='store_true')
    p.add_argument('--report',type=Path)
    p.add_argument('--smoke-test',action='store_true')
    p.add_argument('--screenshot',type=Path)
    p.add_argument('--install-browser',action='store_true')
    return p

async def run_cli(args):
    from app.automation.browser import BrowserSession
    from app.automation.engine import Engine
    from app.automation.control import Control
    from app.adapters.apple import AppleAdapter
    from app.adapters.simulation import SimulationAdapter
    from app.models.config import Mode,Settings,Identity
    from app.services.storage import Repository
    from app.services.simulation import SimStore,SCENARIOS,local_config
    from app.services.traffic import SiteCooldown
    from app.models.flow import FlowError
    root=data_dir();repo=Repository(root/'autobuyer.sqlite3');results=[]
    if args.local_test:
        names=list(SCENARIOS) if args.local_test=='all' else [args.local_test]
        for scenario in names:
            config=local_config(scenario)
            with SimStore(config,scenario) as server:
                async with BrowserSession(root,headless=args.headless,isolated=True) as session:
                    control=Control(interactive=False);engine=Engine(repo,control,callback=lambda e:print(json.dumps(e,ensure_ascii=False)))
                    stopper=None
                    if scenario=='stop':
                        async def stopping():await asyncio.sleep(.8);control.stop()
                        stopper=asyncio.create_task(stopping())
                    result=asdict(await engine.run(config,SimulationAdapter(session.page,server,control),Mode.LOCAL_TEST,None if scenario=='identity_incomplete' else Identity.synthetic()))
                    if stopper:await stopper
                    result.update(scenario=scenario,submit_count=server.submit_count,synthetic_orders=len(server.orders));results.append(result)
    else:
        config=Settings.from_dict(json.loads(args.config.read_text(encoding='utf-8'))) if args.config else repo.load_config()
        config.auto_submit=False;config.transmit_identity=False
        async with BrowserSession(root,headless=args.headless) as session:
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
        args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8');args.report.chmod(0o600)
    return 0 if all(r['state'] not in ('FAILED',) for r in results) else 1

def main():
    from app.utils.browser_runtime import prepare_browser_runtime
    prepare_browser_runtime()
    args=parser().parse_args()
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
            args.report.parent.mkdir(parents=True,exist_ok=True)
            args.report.write_text(json.dumps([result],ensure_ascii=False,indent=2),encoding='utf-8')
            args.report.chmod(0o600)
        return 1
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from app.utils.qt import prepare_plugins
    from app.gui.window import MainWindow
    prepare_plugins()
    qt=QApplication(sys.argv[:1]);qt.setApplicationName('iPhone AutoBuyer');qt.setOrganizationName('AppleAutoBuyer')
    window=MainWindow(data_dir());window.show()
    if args.smoke_test:
        def done():
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True,exist_ok=True);window.grab().save(str(args.screenshot))
            print('GUI_SMOKE_OK',flush=True);qt.quit()
        QTimer.singleShot(1200,done)
    return qt.exec()

if __name__=='__main__':
    raise SystemExit(main())
