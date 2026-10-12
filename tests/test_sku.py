"""Public SKU matching and observed landing controls, with no live checkout."""
from playwright.async_api import async_playwright
from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.config import Product,Settings,Store
from app.services.catalog import CatalogCache
from app.services.sku import SKU_LINK_SCRIPT,selected_variant,variants_from_links


def offer():
    return {'name':'iPhone 17','url':'https://www.apple.com.cn/shop/buy-iphone/iphone-17',
            'groups':[{'key':'dimensionColor','title':'颜色','choices':[{'value':'white','label':'白色'}]},
                      {'key':'dimensionCapacity','title':'容量','choices':[{'value':'256gb','label':'256GB','price':'6799.00'}]}]}


def links():
    return [{'url':'https://www.apple.com.cn/shop/buy-iphone/iphone-17/mg6x4ch/a',
             'color':'白色','capacity':'256 GB','price':'RMB 6,799'}]


def test_sku_matching_requires_exact_public_configuration_price_and_family():
    item=offer();item['variants']=variants_from_links(item,links())
    options={'dimensionColor':'white','dimensionCapacity':'256gb'}
    assert selected_variant(item,options)['url']==links()[0]['url']
    assert not selected_variant(item,{'dimensionColor':'white'})
    assert not selected_variant(item,{**options,'dimensionScreensize':'6_9inch'})
    for changes in ({'color':'黑色'},{'price':'RMB 7,799'},
                    {'url':'https://example.com/shop/buy-iphone/iphone-17/mg6x4ch/a'},
                    {'url':'https://www.apple.com.cn/shop/buy-iphone/iphone-duo/mk2m4ch/a'},
                    {'url':item['url']},{'url':links()[0]['url']+'?token=private'}):
        assert variants_from_links(item,[{**links()[0],**changes}])==[]
    assert variants_from_links(item,[*links(),{**links()[0],'url':links()[0]['url'].replace('mg6x4','mg704')}])==[]


def test_pro_and_pro_max_links_are_disambiguated_by_the_official_capacity_price():
    item=offer();item['url']='https://www.apple.com.cn/shop/buy-iphone/iphone-18-pro'
    item['locked_options']={'dimensionScreensize':'6_9inch'}
    item['groups'][1]['choices'][0]['price']='10999.00'
    candidates=[{**links()[0],'url':item['url']+'/mjy74ch/a','price':'RMB 10,999'},
                {**links()[0],'url':item['url']+'/mjt84ch/a','price':'RMB 9,999'}]
    variants=variants_from_links(item,candidates)
    assert len(variants)==1 and variants[0]['options']['dimensionScreensize']=='6_9inch'
    assert variants[0]['url'].endswith('/mjy74ch/a')


def test_bundled_catalog_has_unique_skus_for_all_published_configurations(tmp_path):
    products=CatalogCache(tmp_path).load()
    assert {p['name']:len(p['variants']) for p in products}=={
        'iPhone Duo':8,'iPhone 17':10,'iPhone 17e':6,'iPhone 18 Pro':16,'iPhone 18 Pro Max':16}
    for product in products:
        assert len({tuple(sorted(v['options'].items())) for v in product['variants']})==len(product['variants'])
        for variant in product['variants']:assert selected_variant(product,variant['options'])==variant


async def test_catalog_reads_public_noscript_links_and_sku_404_falls_back_once():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True);page=await browser.new_page();requests=[]
        html='''<main><h1>购买 iPhone 17</h1></main><div hidden><a href="/shop/buy-iphone/iphone-17/mg6x4ch/a">
            <span class="dimensionCapacity">256<small>GB</small><sup>脚注 1</sup></span>
            <span class="dimensionColor">白色</span><span class="current_price">RMB 6,799</span></a></div>'''
        async def route(r):
            requests.append(r.request.url)
            await r.fulfill(status=404 if '/mg6x4ch/a' in r.request.url else 200,
                            content_type='text/html; charset=utf-8',body=html)
        await page.route('**/*',route)
        config=Settings(product=Product(name='iPhone 17',url=links()[0]['url'],max_total='6799.00'),stores=[Store('Apple 南京东路')])
        adapter=AppleAdapter(page,Control(False))
        await adapter.prepare(config)
        assert requests==[links()[0]['url'],offer()['url']]
        assert config.product.url==offer()['url']
        read=await page.locator('a[href]').evaluate_all(SKU_LINK_SCRIPT)
        assert variants_from_links(offer(),read)[0]['url']==links()[0]['url']
        await browser.close()


async def test_selected_sku_controls_are_verified_without_reclicking_choices():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True);page=await browser.new_page()
        html='''<main><h1>购买 iPhone 17</h1>
          <label>白色<input type="radio" name="dimensionColor" value="white" checked onclick="window.choiceClicks++"></label>
          <label>256GB<input type="radio" name="dimensionCapacity" value="256gb" checked onclick="window.choiceClicks++"></label>
          <button>添加到购物袋</button></main><script>window.choiceClicks=0;</script>'''
        await page.route('**/*',lambda r:r.fulfill(content_type='text/html; charset=utf-8',body=html))
        await page.goto(links()[0]['url'])
        adapter=AppleAdapter(page,Control(False))
        config=Settings(product=Product(name='iPhone 17',url=links()[0]['url'],max_total='6799.00',
            options={'dimensionColor':'white','dimensionCapacity':'256gb'}))
        await adapter.select_product(config)
        assert await page.evaluate('window.choiceClicks')==0
        await browser.close()
