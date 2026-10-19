import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.config import Product, Settings, Store


@pytest.mark.e2e
async def test_parked_config_is_rechecked_without_navigation_or_bag_click():
    url = 'https://www.apple.com.cn/shop/buy-iphone/iphone-duo/mk2m4ch/a'
    settings = Settings(product=Product(name='iPhone Duo', url=url, max_total='100.00', options={
                        'dimensionColor': 'starwhite', 'dimensionCapacity': '256gb'}),
                        stores=[Store('Apple 南京东路', store_id='R359')])
    html = '''<main><h1>购买 iPhone Duo</h1>
      <label><input type="radio" name="dimensionColor" value="starwhite" checked>Star White</label>
      <label><input type="radio" name="dimensionCapacity" value="256gb" checked>256GB RMB 100</label>
      <label><input type="radio" name="trade" checked>不折抵换购</label>
      <label><input type="radio" name="care" checked>不加 AppleCare+ 服务计划</label>
      <button onclick="window.bagClicks++">添加到购物袋</button>
      <script>window.bagClicks=0;window.changes=0;document.addEventListener('change',()=>window.changes++);</script></main>'''
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        requests = []
        async def respond(route):
            requests.append(route.request.url)
            await route.fulfill(content_type='text/html; charset=utf-8', body=html)
        await context.route('**/*', respond)
        page = await context.new_page()
        adapter = AppleAdapter(page, Control(False))
        try:
            await adapter.prepare(settings)
            assert await adapter.is_open()
            await adapter.select_product(settings)
            await adapter.resume_product(settings)
            assert requests == [url]
            assert await page.evaluate('window.bagClicks') == 0
            assert await page.evaluate('window.changes') == 0
            assert page.url == url
        finally:
            adapter.detach()
            await browser.close()
