from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.flow import UserAction


@pytest.mark.parametrize('path,status,kind,cleared',[
    ('https://idmsa.apple.com.cn/appleauth/auth/signin/complete',409,'xhr',True),
    ('https://idmsa.apple.com.cn/appleauth/auth/verify/trusteddevice/securitycode',200,'xhr',True),
    ('https://secure9.www.apple.com.cn/shop/account/home',200,'document',True),
    ('https://idmsa.apple.com.cn/appleauth/auth/signin/init',200,'xhr',False),
    ('https://www.apple.com.cn/shop/buy-iphone',200,'document',False),
    ('https://idmsa.apple.com.cn/appleauth/auth/signin/complete',503,'xhr',False),
])
async def test_prior_503_is_cleared_by_new_authentication_progress_or_protected_return(path,status,kind,cleared):
    frame=object()
    adapter=AppleAdapter(SimpleNamespace(on=lambda *_:None,main_frame=frame),Control(False))
    adapter.auth_service_status=503
    adapter._response(SimpleNamespace(url=path,status=status,headers={},
        request=SimpleNamespace(resource_type=kind,method='POST' if kind=='xhr' else 'GET',frame=frame)))
    assert (adapter.auth_service_status is None)==cleared


@pytest.mark.e2e
async def test_service_failure_two_factor_progress_and_verified_return_are_distinct():
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        page=await browser.new_page()
        requests=[]
        async def respond(route):
            requests.append((route.request.method,route.request.url))
            if route.request.url.endswith('/signin/complete'):
                count=sum(url.endswith('/signin/complete') for _,url in requests)
                await route.fulfill(status=503 if count==1 else 409,
                    content_type='text/html' if count==1 else 'application/json',
                    body='<h1>Service Temporarily Unavailable</h1>' if count==1 else '{"authType":"hsa2"}')
            else:
                body='<a href="/shop/signOut">退出登录 测试账户</a>' if '/account/home' in route.request.url else '''
                    <label>密码<input type="password"></label>
                    <button onclick="fetch('https://idmsa.apple.com.cn/appleauth/auth/signin/complete',{method:'POST'})">手动验证</button>'''
                await route.fulfill(content_type='text/html; charset=utf-8',body=body)
        await page.context.route('**/*',respond)
        adapter=AppleAdapter(page,Control(False))
        try:
            await page.goto('https://secure9.www.apple.com.cn/shop/signIn?ssi=PRIVATE_CONTEXT')
            async with page.expect_response(lambda response: response.url.endswith('/signin/complete')) as failed:
                await page.get_by_role('button',name='手动验证').click()
            await (await failed.value).finished()
            with pytest.raises(UserAction,match='503'):await adapter.login_status(timeout=0)
            assert sum(method=='POST' for method,_ in requests)==1
            async with page.expect_response(lambda response: response.url.endswith('/signin/complete')) as progressed:
                await page.get_by_role('button',name='手动验证').click()
            await (await progressed.value).finished()
            assert await adapter.login_status(timeout=0) is False
            assert adapter.auth_service_status is None
            await page.goto('https://secure9.www.apple.com.cn/shop/account/home')
            assert await adapter.login_status(timeout=0) is True
            assert sum(method=='POST' for method,_ in requests)==2
        finally:
            adapter.detach()
            await browser.close()


@pytest.mark.e2e
async def test_cached_account_markup_cannot_clear_a_new_service_failure_but_fresh_check_can():
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        context=await browser.new_context()
        page=await context.new_page()
        requests=[]
        async def respond(route):
            requests.append(route.request.method)
            failure=route.request.url.endswith('/signin/complete')
            await route.fulfill(status=503 if failure else 200,content_type='text/html; charset=utf-8',
                body='<h1>Service Temporarily Unavailable</h1>' if failure else '''
                    <a href="/shop/signOut">退出登录 测试账户</a>
                    <button onclick="fetch('https://idmsa.apple.com.cn/appleauth/auth/signin/complete',{method:'POST'})">请求校验</button>''')
        await page.context.route('**/*',respond)
        adapter=AppleAdapter(page,Control(False))
        try:
            await page.goto('https://secure9.www.apple.com.cn/shop/account/home')
            async with page.expect_response(lambda response: response.status==503) as failed:
                await page.get_by_role('button',name='请求校验').click()
            await (await failed.value).finished()
            with pytest.raises(UserAction,match='503'):await adapter.login_status(timeout=0)
            assert await adapter.check_login(auto_recover=False) is True
            assert requests==['GET','POST','GET']
            assert page.context.pages==[page]
        finally:
            adapter.detach();await browser.close()


@pytest.mark.parametrize('same',[False,True])
async def test_page_binding_clears_only_a_different_pages_service_failure_and_keeps_site_limit(same):
    def page():return SimpleNamespace(on=lambda *_:None,remove_listener=lambda *_:None)
    adapter=AppleAdapter(page(),Control(False))
    adapter.auth_service_status=503
    adapter.http_status=541;adapter.limited_until=99999
    adapter.bind_page(adapter.page if same else page())
    assert adapter.auth_service_status==(503 if same else None)
    assert adapter.http_status==541 and adapter.limited_until==99999
