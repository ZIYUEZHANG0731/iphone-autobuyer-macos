import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.automation.browser import BrowserSession
from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.config import Settings

async def main():
    async with BrowserSession(Path('/tmp/autobuyer-public-inspect'),headless=True,isolated=True) as s:
        await s.page.goto('https://www.apple.com.cn/shop/buy-iphone/iphone-17',wait_until='domcontentloaded')
        await s.page.get_by_role('radio',name='白色',exact=True).wait_for()
        a=AppleAdapter(s.page,Control(False));print(json.dumps(await a.options(),ensure_ascii=False))
        await a._choice('dimensionColor','white')
        print('COLOR_OK',flush=True)
        print(json.dumps(await a.options(),ensure_ascii=False))
        config=Settings.from_dict(json.loads(Path('scripts/stocked-example.json').read_text()))
        for key,value in config.product.options.items():
            await a._choice(key,value)
            print('CHOICE_OK',key,value,flush=True)
        await a.select_product(config)
        print('PRODUCT_OK',flush=True)
        await s.page.screenshot(path='reports/product-before-capacity.png')
asyncio.run(main())
