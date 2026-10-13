from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.gui.worker import Worker
from app.models.config import Settings
from app.models.flow import UserAction
from app.services.login import ACCOUNT_ENTRY, STORE_ENTRY, shop_signin_url, visible_login_form
from app.services.signin import SignInController, credential_origin


STORE_HTML = '''<h1>Apple Store</h1>
<button id="globalnav-menubutton-link-bag" aria-expanded="false" onclick="
this.setAttribute('aria-expanded','true');document.querySelector('nav').hidden=false;
setTimeout(()=>document.querySelector('nav').innerHTML=window.fixtureMenu,100)">购物袋</button>
<nav id="globalnav-submenu-bag" hidden></nav><script>window.fixtureMenu=MENU_JSON;</script>'''
FORM_HTML = '<h1>登录 Apple Store</h1><input aria-label="电子邮件或电话号码"><input type="password" aria-label="密码"><button>登录</button>'
ACCOUNT_URL = 'https://secure9.www.apple.com.cn/shop/account/home'
REMEMBERED_MENU = '<a href="/shop/signOut">退出登录 测试账户</a><a href="'+ACCOUNT_URL+'">账户</a>'


@pytest.mark.e2e
async def test_slow_account_navigation_finishes_without_reclick_or_password(browser,tmp_path):
    import asyncio,json
    context=await browser.new_context()
    page=await context.new_page()
    requests=[]
    async def respond(route):
        requests.append((route.request.method,route.request.url))
        if route.request.url==ACCOUNT_URL:
            await asyncio.sleep(5.5)
            body='<a href="/shop/signOut">退出登录 测试账户</a>'
        else:
            body=STORE_HTML.replace('MENU_JSON',json.dumps(REMEMBERED_MENU))
        await route.fulfill(content_type='text/html; charset=utf-8',body=body)
    await context.route('**/*',respond)
    worker=Worker('login',Settings(),tmp_path,interactive=False)
    try:
        result=await worker.browser_action(SimpleNamespace(page=page),None)
        assert result['state']=='COMPLETED'
        assert requests==[('GET',STORE_ENTRY),('GET',ACCOUNT_URL)]
        assert page.url==ACCOUNT_URL and not page.is_closed()
    finally:await context.close()


@pytest.fixture
async def browser():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest.mark.e2e
async def test_slow_signin_navigation_finishes_without_reclick_or_credentials(browser,tmp_path):
    import asyncio,json
    target='https://secure9.www.apple.com.cn/shop/signIn?ssi=PRIVATE_SLOW_CONTEXT'
    context=await browser.new_context();page=await context.new_page();requests=[]
    async def respond(route):
        requests.append((route.request.method,route.request.url))
        if route.request.url==target:
            await asyncio.sleep(5.5)
            body=FORM_HTML
        else:
            body=STORE_HTML.replace('MENU_JSON',json.dumps('<a href="'+target+'">登录</a>'))
        await route.fulfill(content_type='text/html; charset=utf-8',body=body)
    await context.route('**/*',respond)
    worker=Worker('login',Settings(),tmp_path,interactive=False)
    try:
        result=await worker.browser_action(SimpleNamespace(page=page),None)
        assert result['state']=='PAUSED'
        assert requests==[('GET',STORE_ENTRY),('GET',target)]
        assert page.url==target and await visible_login_form(page)
        assert await page.locator('input[type="password"]').input_value()==''
    finally:await context.close()


async def store_fixture(browser, menu, *, authenticated=False):
    import json
    context = await browser.new_context()
    page = await context.new_page()
    requests = []
    async def respond(route):
        requests.append(route.request.url)
        body = STORE_HTML.replace('MENU_JSON',json.dumps(menu)) if route.request.url == STORE_ENTRY else FORM_HTML
        if authenticated and route.request.url==ACCOUNT_URL:body='<a href="/shop/signOut">退出登录 测试账户</a>'
        await route.fulfill(content_type='text/html; charset=utf-8',body=body)
    await context.route('**/*',respond)
    return context,page,requests


@pytest.mark.e2e
async def test_login_follows_delayed_bag_link_and_keeps_website_generated_context(browser,tmp_path):
    target='https://secure9.www.apple.com.cn/shop/signIn?ssi=PRIVATE_MENU_CONTEXT'
    context,page,requests=await store_fixture(browser,f'<a href="{target}">登录</a>')
    worker=Worker('login',Settings(),tmp_path,interactive=False)
    try:
        result=await worker.browser_action(SimpleNamespace(page=page),None)
        assert result['state']=='PAUSED'
        assert requests==[STORE_ENTRY,target]
        assert page.url==target and await visible_login_form(page)
        assert await page.get_by_role('textbox',name='电子邮件或电话号码').input_value()==''
        assert not page.is_closed()
    finally:await context.close()


@pytest.mark.e2e
async def test_logged_in_store_is_verified_through_its_visible_account_link_without_authentication(browser,tmp_path,monkeypatch):
    context,page,requests=await store_fixture(browser,REMEMBERED_MENU,authenticated=True)
    worker=Worker('login',Settings(),tmp_path)
    async def unexpected_wait():raise AssertionError('Must preserve an authenticated Store session')
    monkeypatch.setattr(worker.control,'wait_resume',unexpected_wait)
    try:
        result=await worker.browser_action(SimpleNamespace(page=page),None)
        assert result['state']=='COMPLETED' and '无需重新登录' in result['message']
        assert requests==[STORE_ENTRY,ACCOUNT_URL] and page.url==ACCOUNT_URL
    finally:await context.close()


@pytest.mark.e2e
@pytest.mark.parametrize('entry', [STORE_ENTRY, 'https://secure9.www.apple.com.cn/shop/bag'])
@pytest.mark.parametrize('expired', [False, True])
async def test_public_session_check_uses_website_account_entry_and_preserves_original_page(browser,entry,expired):
    import json
    context=await browser.new_context();page=await context.new_page();requests=[]
    async def respond(route):
        requests.append((route.request.method,route.request.url))
        body=STORE_HTML.replace('MENU_JSON',json.dumps(REMEMBERED_MENU)) if route.request.url==entry else FORM_HTML
        if route.request.url==ACCOUNT_URL and not expired:
            body='<a href="/shop/signOut">退出登录 测试账户</a>'
        await route.fulfill(content_type='text/html; charset=utf-8',body=body)
    await context.route('**/*',respond)
    try:
        await page.goto(entry)
        result=await AppleAdapter(page,Control(False)).check_login(auto_recover=False)
        assert result is (not expired)
        assert requests==[('GET',entry),('GET',entry),('GET',ACCOUNT_URL)]
        assert page.url==entry and context.pages==[page]
        assert await page.locator('#globalnav-menubutton-link-bag').get_attribute('aria-expanded')=='false'
    finally:await context.close()


@pytest.mark.e2e
async def test_unsigned_public_menu_still_requires_fresh_protected_login_check(browser):
    context,page,requests=await store_fixture(browser,'<a href="/shop/signIn">登录</a>')
    try:
        await page.goto(STORE_ENTRY)
        assert await AppleAdapter(page,Control(False)).check_login(auto_recover=False) is False
        assert requests==[STORE_ENTRY,STORE_ENTRY,ACCOUNT_ENTRY]
        assert page.url==STORE_ENTRY and context.pages==[page]
    finally:await context.close()


@pytest.mark.e2e
async def test_store_chooses_profile_login_when_saved_items_has_another_signin_context(browser,tmp_path):
    target='https://secure9.www.apple.com.cn/shop/signIn?ssi=PRIVATE_PROFILE_CONTEXT'
    menu='<p><a href="/shop/signIn?ssi=PRIVATE_FAVORITES_CONTEXT">登录</a>查看收藏商品</p>'
    menu+='<div data-analytics-region="my profile"><ul><li><a data-ac-autom="gn-bagview-link-signIn" class="ac-gn-bagview-nav-link-signin" href="'+target+'">登录</a></li></ul></div>'
    context,page,requests=await store_fixture(browser,menu)
    worker=Worker('login',Settings(),tmp_path,interactive=False)
    try:
        result=await worker.browser_action(SimpleNamespace(page=page),None)
        assert result['state']=='PAUSED'
        assert requests==[STORE_ENTRY,target] and page.url==target
    finally:await context.close()


@pytest.mark.e2e
async def test_exact_duplicate_signin_links_keep_the_same_official_return_context(browser):
    target='https://secure9.www.apple.com.cn/shop/signIn?ssi=PRIVATE_SAME_CONTEXT'
    context,page,requests=await store_fixture(browser,2*f'<a href="{target}">登录</a>')
    adapter=AppleAdapter(page,Control(False))
    try:
        await adapter.open_store_signin()
        assert requests==[STORE_ENTRY,target]
    finally:await context.close()


@pytest.mark.e2e
async def test_profile_marker_never_allows_an_offsite_signin_destination(browser):
    context,page,requests=await store_fixture(browser,
        '<a href="/shop/signIn?ssi=PRIVATE">登录</a><a data-ac-autom="gn-bagview-link-signIn" href="https://apple.com.cn.attacker.invalid/shop/signIn">登录</a>')
    try:
        with pytest.raises(UserAction):await AppleAdapter(page,Control(False)).open_store_signin()
        assert requests==[STORE_ENTRY]
    finally:await context.close()


@pytest.mark.e2e
async def test_login_action_reuses_current_protected_session_without_a_store_navigation(browser,tmp_path):
    context,page,requests=await store_fixture(browser,REMEMBERED_MENU,authenticated=True)
    await page.goto(ACCOUNT_URL)
    worker=Worker('login',Settings(),tmp_path,interactive=False)
    try:
        result=await worker.browser_action(SimpleNamespace(page=page),None)
        assert result['state']=='COMPLETED' and '无需重新登录' in result['message']
        assert requests==[ACCOUNT_URL,ACCOUNT_URL] and page.url==ACCOUNT_URL
        assert context.pages==[page]
    finally:await context.close()


@pytest.mark.e2e
@pytest.mark.parametrize('menu',[
    '<a href="https://apple.com.cn.attacker.invalid/shop/signIn">登录</a>',
    '<a href="/shop/signIn?ssi=one">登录</a><a href="/shop/signIn?ssi=two">登录</a>',
])
async def test_store_menu_does_not_follow_untrusted_or_ambiguous_login_links(browser,menu):
    context,page,requests=await store_fixture(browser,menu)
    adapter=AppleAdapter(page,Control(False))
    try:
        with pytest.raises(UserAction):await adapter.open_store_signin()
        assert requests==[STORE_ENTRY] and page.url==STORE_ENTRY
    finally:await context.close()


@pytest.mark.e2e
@pytest.mark.parametrize('action',['login','login_diagnosis'])
async def test_login_keeps_current_query_style_signin_without_navigating_or_reading_password(browser,tmp_path,action):
    context,page,requests=await store_fixture(browser,'')
    target='https://secure9.www.apple.com.cn/shop/signIn?ssi=PRIVATE_MENU_CONTEXT'
    await page.goto(target)
    worker=Worker(action,Settings(auto_login=action=='login_diagnosis'),tmp_path,interactive=False)
    try:
        result=await worker.browser_action(SimpleNamespace(page=page),None)
        assert result['state']=='PAUSED' and requests==[target]
        assert await page.get_by_role('textbox',name='电子邮件或电话号码').input_value()==''
    finally:await context.close()


@pytest.mark.e2e
async def test_login_return_to_store_opens_bag_to_confirm_existing_session(browser):
    context,page,requests=await store_fixture(browser,REMEMBERED_MENU,authenticated=True)
    try:
        await page.goto(STORE_ENTRY)
        adapter=AppleAdapter(page,Control(False))
        assert await adapter.confirm_login(timeout=1)
        controller=SignInController()
        controller.attempted.add('fixture-id')
        assert await controller._success(page,Settings(credential_id='fixture-id'),lambda *_:None)
        assert not controller.attempted and requests==[STORE_ENTRY,ACCOUNT_URL]
    finally:await context.close()


@pytest.mark.e2e
async def test_remembered_store_identity_with_expired_authentication_is_never_confirmed(browser,tmp_path):
    context,page,requests=await store_fixture(browser,REMEMBERED_MENU)
    worker=Worker('login',Settings(),tmp_path,interactive=False)
    events=[];worker.progress=lambda kind,data:events.append((kind,data))
    try:
        result=await worker.browser_action(SimpleNamespace(page=page),None)
        assert result['state']=='PAUSED' and requests==[STORE_ENTRY,ACCOUNT_URL]
        assert not any(kind=='login' and data.get('authenticated') is True for kind,data in events)
        assert any(kind=='login' and data.get('status')=='remembered' for kind,data in events)
        assert await page.get_by_role('textbox',name='电子邮件或电话号码').input_value()==''
    finally:await context.close()


@pytest.mark.parametrize('url,allowed',[
    ('https://secure9.www.apple.com.cn/shop/signIn?ssi=PRIVATE',True),
    ('https://www.apple.com.cn/shop/signIn/account',True),
    ('https://www.apple.com.cn/shop/signInOther',False),
    ('https://www.apple.com.cn:8443/shop/signIn',False),
    ('https://www.apple.com.cn.attacker.invalid/shop/signIn',False),
])
def test_exact_official_signin_paths_include_the_bag_entry(url,allowed):
    assert shop_signin_url(url) is allowed
    assert credential_origin(url) is allowed
