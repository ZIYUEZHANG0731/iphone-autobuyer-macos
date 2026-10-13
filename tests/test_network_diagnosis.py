from __future__ import annotations
import os

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from app.gui.worker import Worker
from app.models.config import Settings
from app.security.vault import CredentialVault
from app.services.network_diagnosis import NetworkDiagnosis, browser_capabilities, request_family, response_shape
from app.services.login import STORE_ENTRY


@pytest.mark.parametrize('url,family', [
    ('https://secure7.www.apple.com.cn/shop/signIn/idms/PRIVATE_TOKEN?ssi=PRIVATE_QUERY', 'shop_idms'),
    ('https://secure7.www.apple.com.cn/shop/signIn?ssi=PRIVATE_QUERY', 'shop_signin'),
    ('https://idmsa.apple.com/appleauth/auth/signin/init?token=PRIVATE_QUERY', 'auth_init'),
    ('https://idmsa.apple.com/appleauth/auth/signin/complete', 'auth_complete'),
    ('https://www.apple.com.cn/shop/fulfillment-messages?part=PUBLIC_SKU', 'delivery'),
    ('https://idmsa.apple.com.cn/appleauth/auth/signin/init?token=PRIVATE_QUERY', 'auth_init'),
    ('https://secure7.www.apple.com.cn/jsc_context?token=PRIVATE_QUERY', 'browser_context'),
])
def test_diagnostic_routes_remove_all_dynamic_paths_queries_and_values(url, family):
    result = request_family(url)
    assert result[1] == family
    assert 'PRIVATE' not in json.dumps(result)
    assert '?' not in json.dumps(result)
    assert request_family('https://idmsa.apple.com.invalid/appleauth/auth/signin') is None
    assert request_family('https://idmsa.apple.com:bad/appleauth/auth/signin') is None


def test_diagnostic_body_classifies_only_fixed_shapes_and_numeric_service_errors():
    text = json.dumps({'serviceErrors': [{'code': '-99999', 'message': 'Unable to verify identity PRIVATE_FIXTURE_PASSWORD'}],
                       'account': 'fixture@example.invalid', 'session_token': 'PRIVATE_TOKEN'})
    result = response_shape(text, 'application/json')
    assert result == {'body_kind': 'json', 'error_class': 'identity_unverified', 'service_error_codes': ['-99999']}
    assert response_shape('<!doctype html><title>Page Not Found - Apple</title>PRIVATE_TOKEN', 'text/html') == {
        'body_kind': 'html', 'error_class': 'apple_not_found_html'}
    assert 'PRIVATE' not in json.dumps(result)
    assert browser_capabilities({'user_agent':'PRIVATE_TOKEN Chrome/140.0.0.1 QtWebEngine/6.11.2',
                                 'languages':['zh-CN','PRIVATE_TOKEN'], 'webdriver':True})['engine_versions'] == {
        'Chrome':'140.0.0.1','QtWebEngine':'6.11.2'}


@pytest.mark.e2e
async def test_passive_observer_keeps_541_callback_and_service_error_after_action_is_idle(tmp_path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        seen = []
        requests = []

        async def route(request):
            requests.append(request.request.method)
            if '/idms/' in request.request.url:
                await request.fulfill(status=541, content_type='text/html',
                    body='<!doctype html><title>Page Not Found - Apple</title><p>PRIVATE_BODY</p>',
                    headers={'set-cookie':'secret_cookie=PRIVATE_COOKIE; Secure'})
            elif '/complete' in request.request.url:
                await request.fulfill(status=200, content_type='application/json',headers={'access-control-allow-origin':'*'},body=json.dumps({
                    'serviceErrors':[{'code':'-99999','message':'Unable to verify identity PRIVATE_ERROR'}],
                    'token':'PRIVATE_RESPONSE_TOKEN'}))
            else:
                await request.fulfill(content_type='text/html; charset=utf-8', body='''<h1>登录</h1>
                    <button onclick="fetch('/shop/signIn/idms/PRIVATE_PATH?ssi=PRIVATE_QUERY')">初始回调</button>
                    <button onclick="fetch('https://idmsa.apple.com/appleauth/auth/signin/complete',{method:'POST',body:'PRIVATE_PASSWORD'})">认证提交</button>''')

        await context.route('**/*', route)
        diagnosis = NetworkDiagnosis(tmp_path, 'chrome', progress=lambda k,v:seen.append(v) if k=='network_diagnosis' else None)
        await diagnosis.start(context, page)
        try:
            await page.goto('https://secure7.www.apple.com.cn/shop/signIn/account')
            await page.get_by_role('button',name='初始回调').click()
            await page.get_by_role('button',name='认证提交').click()
            deadline = asyncio.get_running_loop().time()+3
            while len(seen)<2 and asyncio.get_running_loop().time()<deadline:
                await asyncio.sleep(.03)
        finally:
            await diagnosis.close()
            await browser.close()
        contents = diagnosis.path.read_text(encoding='utf-8')
        report = json.loads(contents)
        if os.name != 'nt':
            assert diagnosis.path.stat().st_mode & 0o777 == 0o600
        assert 'PRIVATE' not in contents and 'secret_cookie' not in contents
        assert sorted(requests) == ['GET','GET','POST']
        assert any(e['family']=='shop_idms' and e.get('status')==541 and e.get('error_class')=='apple_not_found_html' for e in report['events'])
        assert any(e['family']=='auth_complete' and e.get('status')==200 and e.get('service_error_codes')==['-99999'] for e in report['events'])
        assert len(seen)==2


@pytest.mark.e2e
async def test_diagnostics_records_cross_origin_frame_request_and_error_without_field_values(tmp_path):
    from app.automation.browser import BrowserSession, installed_chrome
    from app.services.signin import credential_origin
    if installed_chrome() is None:
        pytest.skip('Native Chrome/CDP observation')
    async with BrowserSession(tmp_path) as session:
        async def respond(route):
            if route.request.url.endswith('/signin/init'):
                await route.fulfill(status=200,content_type='application/json',body=json.dumps({
                    'serviceErrors':[{'code':'-12345','message':'Unable to verify PRIVATE_SECRET'}]}))
            elif 'idmsa.apple.com.cn' in route.request.url:
                await route.fulfill(content_type='text/html;charset=utf-8',body='''
                    <label>电子邮件<input value="fixture@example.invalid"></label>
                    <input type="password" value="PRIVATE_SECRET">
                    <p>无法验证你的身份。重试。</p>
                    <button onclick="fetch('/appleauth/auth/signin/init',{method:'POST',body:'PRIVATE_SECRET'})">登录</button>''')
            else:
                await route.fulfill(content_type='text/html;charset=utf-8',body='''
                    <iframe src="https://idmsa.apple.com.cn/appleauth/auth/authorize?PRIVATE_QUERY"></iframe>''')
        await session.context.route('**/*',respond)
        diagnosis=NetworkDiagnosis(tmp_path,'chrome')
        await diagnosis.start(session.context,session.page)
        try:
            await session.page.goto('https://secure7.www.apple.com.cn/shop/signIn/account')
            await session.page.frame_locator('iframe').get_by_role('button',name='登录').click()
            deadline=asyncio.get_running_loop().time()+3
            while not any(e.get('service_error_codes') for e in diagnosis.events) and asyncio.get_running_loop().time()<deadline:
                await asyncio.sleep(.03)
            await diagnosis.sample_pages()
        finally:
            await diagnosis.close()
        report=json.loads(diagnosis.path.read_text(encoding='utf-8'))
        assert report['health']['capture_errors']==0
        assert any(e['family']=='auth_init' and e.get('phase')=='request_started' for e in report['events'])
        assert any(e.get('service_error_codes')==['-12345'] for e in report['events'])
        frame=next(s for s in report['page_samples'] if not s['main_frame'])
        assert frame['secure_context'] and frame['webcrypto'] and frame['identity_unverified_visible']
        assert 'PRIVATE' not in diagnosis.path.read_text(encoding='utf-8') and 'fixture@example' not in diagnosis.path.read_text(encoding='utf-8')
        assert credential_origin('https://idmsa.apple.com.cn/appleauth/auth/authorize')


@pytest.mark.e2e
async def test_login_diagnosis_does_not_load_saved_password_or_submit_authentication(tmp_path, monkeypatch):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        requests=[]
        async def respond(route):
            requests.append(route.request.method)
            body='''<button id="globalnav-menubutton-link-bag" aria-expanded="false"
                onclick="this.setAttribute('aria-expanded','true');document.querySelector('nav').hidden=false">购物袋</button>
                <nav id="globalnav-submenu-bag" hidden><a href="/shop/signIn?ssi=PRIVATE_QUERY">登录</a></nav>''' if route.request.url == STORE_ENTRY else '<h1>登录 Apple Store</h1><label>Apple 账户<input></label><label>密码<input type="password"></label><button>登录</button>'
            await route.fulfill(content_type='text/html; charset=utf-8',body=body)
        await page.context.route('**/*',respond)
        monkeypatch.setattr(CredentialVault,'get',lambda *_:pytest.fail('Diagnostic action must not read credentials'))
        config=Settings(credential_id='c07c6b9d-e27a-4438-8d88-dab2e7872f08',auto_login=True)
        worker=Worker('login_diagnosis',config,tmp_path,interactive=False)
        try:
            result=await worker.browser_action(SimpleNamespace(page=page),None)
            assert result['state']=='PAUSED'
            assert requests==['GET','GET']
            assert await page.get_by_role('textbox',name='Apple 账户').input_value()==''
        finally:
            await browser.close()
