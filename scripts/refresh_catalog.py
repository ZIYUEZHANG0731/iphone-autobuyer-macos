"""Save iPhone 17/18/Duo public choices. No login, bag or checkout actions."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.automation.browser import BrowserSession
from app.automation.control import Control
from app.adapters.apple import AppleAdapter
from app.services.catalog_reader import read_catalog


async def collect(args):
    with tempfile.TemporaryDirectory(prefix='autobuyer-public-catalog-') as directory:
        async with BrowserSession(Path(directory)) as session:
            adapter=AppleAdapter(session.page,Control(False))
            result=await read_catalog(adapter,maximum=args.max_products,
                on_progress=lambda count,name:print(f'公开目录已读取 {count}：{name}',flush=True))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'products':len(result['products']),'unread':len(result['unread_pages'])},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--max-products',type=int,default=12)
    args=parser.parse_args();asyncio.run(collect(args))
