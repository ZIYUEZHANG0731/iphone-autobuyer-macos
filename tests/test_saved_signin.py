import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.config import ConfigError, Settings
from app.models.credential import AppleCredential
from app.models.flow import RateLimited, UserAction
from app.security.vault import CredentialVault
from app.security.privacy import Redactor
from app.services.login import ACCOUNT_ENTRY, STORE_ENTRY
from app.services.signin import SignInController, credential_origin


TEST_CREDENTIAL = AppleCredential('signin-fixture@example.invalid', 'Synthetic-Only-Password!')
AUTH_URL = 'https://idmsa.apple.com/appleauth/auth/signin'
STORE_URL = 'https://secure7.www.apple.com.cn/shop/signIn/account'
TOP = '''<h1>登录 Apple Store</h1><iframe src="%s"></iframe><script>
addEventListener('message',e=>{if(e.origin==='https://idmsa.apple.com'&&e.data==='logged')
location.href='/shop/account/home';});</script>''' % AUTH_URL
ONE_STEP = '''<label>电子邮件或电话号码<input id="account"></label>
<label>密码<input id="password" type="password"></label>
<label><input id="remember" type="checkbox">记住我的账户</label><button onclick="submit()">登录</button>'''
TWO_STEP = '''<label>Apple 账户<input id="account"></label><button onclick="next()">继续</button>
<script>let entered;function next(){entered=document.querySelector('#account').value;
document.body.innerHTML='<p>'+entered+'</p><label>密码<input id="password" type="password"></label>'+
'<label><input id="remember" type="checkbox">记住我的账户</label><button onclick="submit()">登录</button>';}</script>'''
SUBMIT = '''<script>function submit(){fetch('/fixture-submit',{method:'POST',body:JSON.stringify({
account:document.querySelector('#account')?.value||entered,password:document.querySelector('#password').value,
remember:document.querySelector('#remember')?.checked||false})}).then(r=>r.text()).then(result=>{
if(result==='success')parent.postMessage('logged','https://secure7.www.apple.com.cn');else document.body.innerHTML=result;});}</script>'''
OTP = '<h2>双重认证</h2><input autocomplete="one-time-code" aria-label="验证码">'
TRUST = '''<h2>信任此浏览器？</h2><button onclick="parent.postMessage('logged','https://secure7.www.apple.com.cn')">信任</button>'''


@pytest.fixture
async def browser():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        yield browser
        await browser.close()


def enabled_settings():
    return Settings(credential_id=str(uuid4()), auto_login=True, trust_browser=True)


async def fixture(browser, *, body=ONE_STEP, outcome='success', top=TOP, auth_host='idmsa.apple.com', auth_status=200):
    context = await browser.new_context()
    page = await context.new_page()
    submissions = []
    reads = []
    events = []
    auth_url=AUTH_URL.replace('idmsa.apple.com',auth_host)
    top=top.replace('idmsa.apple.com',auth_host)

    async def respond(route):
        url = route.request.url
        if route.request.method == 'POST':
            submissions.append(json.loads(route.request.post_data))
            await route.fulfill(status=auth_status,content_type='text/html; charset=utf-8', body=outcome)
        elif url == ACCOUNT_ENTRY:
            # Playwright routes do not re-intercept a server redirect chain.
            # A fixture-only client navigation keeps every request intercepted.
            await route.fulfill(content_type='text/html',body='<script>location.href='+json.dumps(STORE_URL)+'</script>')
        elif url == STORE_ENTRY:
            await route.fulfill(content_type='text/html; charset=utf-8',body='''
                <button id="globalnav-menubutton-link-bag" aria-expanded="false" onclick="
                this.setAttribute('aria-expanded','true');document.querySelector('nav').hidden=false">购物袋</button>
                <nav id="globalnav-submenu-bag" hidden><a href="'''+STORE_URL+'''">登录</a></nav>''')
        elif '/shop/account/home' in url:
            await route.fulfill(content_type='text/html; charset=utf-8', body='<a href="/shop/signOut">注销</a>')
        elif url == auth_url:
            await route.fulfill(content_type='text/html; charset=utf-8', body=body+SUBMIT)
        else:
            await route.fulfill(content_type='text/html; charset=utf-8', body=top)

    await context.route('**/*', respond)
    await page.goto(STORE_URL)
    config = enabled_settings()
    controller = SignInController()

    def provider(ref):
        reads.append(ref)
        return TEST_CREDENTIAL

    adapter = AppleAdapter(page, Control(False), login_config=config, credential_provider=provider,
                           sign_in=controller, progress=lambda kind,data:events.append((kind,data)))
    return SimpleNamespace(context=context, page=page, submissions=submissions, reads=reads,
                           events=events, config=config, controller=controller, adapter=adapter)


@pytest.mark.parametrize('url', ['https://idmsa.apple.com.attacker.invalid/appleauth/auth/signin',
    'https://idmsa.apple.com:444/appleauth/auth/signin', 'https://evil@idmsa.apple.com/appleauth/auth/signin',
    'http://idmsa.apple.com/appleauth/auth/signin', 'https://account.apple.com/account/password',
    'https://www.apple.com.cn/shop/checkout', 'https://www.apple.com.cn/shop/product'])
def test_credentials_never_accept_unknown_or_non_signin_origin(url):
    assert not credential_origin(url)


def test_credential_is_private_and_config_contains_only_opaque_reference():
    config = enabled_settings()
    assert repr(TEST_CREDENTIAL) == 'AppleCredential(<private>)'
    raw = json.dumps(config.to_dict())
    assert TEST_CREDENTIAL.account not in raw and TEST_CREDENTIAL.password not in raw
    for field in ('account', 'password'):
        with pytest.raises(ConfigError, match='未知字段'):
            Settings.from_dict({**config.to_dict(), field:'must-not-persist'})
    with pytest.raises(ConfigError, match='引用'):
        Settings(credential_id=TEST_CREDENTIAL.account).validate('website_dry_run',require_stores=False)
    assert not Settings().auto_login and not Settings().trust_browser
    redactor = Redactor();redactor.register(TEST_CREDENTIAL)
    assert TEST_CREDENTIAL.password not in redactor.clean(TEST_CREDENTIAL.password)


def test_credential_vault_is_separate_and_does_not_expose_backend_errors(monkeypatch):
    vault=CredentialVault();stored={}
    vault.backend=SimpleNamespace(set_password=lambda service,key,value:stored.update({(service,key):value}),
        get_password=lambda service,key:stored.get((service,key)),
        delete_password=lambda service,key:stored.pop((service,key)))
    ref=str(uuid4());vault.put(ref,TEST_CREDENTIAL)
    assert vault.service=='com.autobuyer.apple.credentials'
    assert vault.get(ref)==TEST_CREDENTIAL
    vault.delete(ref);assert not stored
    with pytest.raises(Exception,match='无法读取保存') as failure:vault.get(ref)
    assert TEST_CREDENTIAL.password not in str(failure.value)


@pytest.mark.e2e
@pytest.mark.parametrize('body', [ONE_STEP,TWO_STEP])
@pytest.mark.parametrize('auth_host',['idmsa.apple.com','idmsa.apple.com.cn'])
async def test_saved_account_logs_in_once_and_remembers_only_when_selected(browser,body,auth_host):
    f=await fixture(browser,body=body,auth_host=auth_host)
    try:
        assert await f.adapter.auto_sign_in(f.page)
        assert f.submissions==[dict(account=TEST_CREDENTIAL.account,password=TEST_CREDENTIAL.password,remember=True)]
        assert f.reads==[f.config.credential_id]
        assert await f.adapter.confirm_login(timeout=.2)
        assert TEST_CREDENTIAL.password not in json.dumps(f.events)
        assert TEST_CREDENTIAL.account not in json.dumps(f.events)
    finally:await f.context.close()


@pytest.mark.e2e
@pytest.mark.parametrize('delayed', [False, True])
async def test_observed_idms_arrow_with_changed_or_delayed_label_submits_once(browser, delayed):
    body=ONE_STEP.replace('<button ', '<button id="sign-in" aria-label="登录 Apple Store" '+('hidden ' if delayed else ''))
    if delayed:
        body+='''<script>document.querySelector('#password').addEventListener('input',()=>
            setTimeout(()=>document.querySelector('#sign-in').hidden=false,200));</script>'''
    f=await fixture(browser, body=body, auth_host='idmsa.apple.com.cn')
    try:
        assert await f.adapter.auto_sign_in(f.page)
        assert len(f.submissions)==1 and len(f.reads)==1
        assert await f.adapter.confirm_login(timeout=.2)
    finally:await f.context.close()


@pytest.mark.e2e
async def test_two_visible_idms_arrows_still_pause_without_submitting(browser):
    body=ONE_STEP.replace('<button ', '<button id="sign-in" aria-label="登录 Apple Store" ')
    body+='<button id="sign-in" aria-label="登录 Apple Store">另一登录按钮</button>'
    f=await fixture(browser, body=body)
    try:
        with pytest.raises(UserAction,match='无法唯一确认'):
            await f.adapter.auto_sign_in(f.page)
        assert not f.submissions and len(f.reads)==1
    finally:await f.context.close()


@pytest.mark.e2e
async def test_cn_auth_503_is_reported_as_service_failure_without_a_second_password_submission(browser):
    f=await fixture(browser,auth_host='idmsa.apple.com.cn',auth_status=503,
        outcome='<p>无法验证你的身份。重试。</p>')
    try:
        for _ in range(2):
            with pytest.raises(UserAction,match='HTTP 503'):
                await f.adapter.auto_sign_in(f.page)
        assert len(f.submissions)==1 and len(f.reads)==1
        assert any(d.get('affects')=='login_service' for k,d in f.events if k=='http_status')
        assert any(d.get('authenticated') is None and 'HTTP 503' in d.get('reason','') for k,d in f.events if k=='login')
        with pytest.raises(UserAction,match='HTTP 503'):
            await f.adapter.confirm_login(timeout=.1)
        assert TEST_CREDENTIAL.password not in json.dumps(f.events)
    finally:await f.context.close()


@pytest.mark.e2e
async def test_disabled_permission_never_reads_password_or_submits(browser):
    f=await fixture(browser);f.config.auto_login=False
    try:
        assert not await f.adapter.auto_sign_in(f.page)
        assert not f.reads and not f.submissions
    finally:await f.context.close()


@pytest.mark.e2e
async def test_unknown_loading_page_never_reads_password_or_submits(browser):
    f=await fixture(browser,body='<p>正在载入…</p>')
    try:
        assert not await f.adapter.auto_sign_in(f.page)
        assert not f.reads and not f.submissions
    finally:await f.context.close()


@pytest.mark.e2e
async def test_explicit_http_541_never_reads_or_resubmits_password(browser):
    f=await fixture(browser);f.adapter.http_status=541
    import asyncio
    f.adapter.limited_until=asyncio.get_running_loop().time()+5
    try:
        with pytest.raises(RateLimited):await f.adapter.auto_sign_in(f.page)
        assert not f.reads and not f.submissions
    finally:await f.context.close()


@pytest.mark.e2e
@pytest.mark.parametrize('outcome,message', [('<p>密码不正确</p>','未接受'),('<p>无法验证你的身份。重试。</p>','无法验证身份')])
async def test_failed_authentication_does_not_retry_same_credentials(browser,outcome,message):
    f=await fixture(browser,outcome=outcome)
    try:
        for _ in range(2):
            with pytest.raises(UserAction,match=message):await f.adapter.auto_sign_in(f.page)
        assert len(f.submissions)==1 and len(f.reads)==1
    finally:await f.context.close()


@pytest.mark.e2e
async def test_verification_then_trust_uses_one_password_attempt_and_no_otp_automation(browser):
    f=await fixture(browser,outcome=OTP)
    try:
        for _ in range(2):
            with pytest.raises(UserAction,match='验证码'):await f.adapter.auto_sign_in(f.page)
        frame=f.page.frames[1]
        assert await frame.locator('input').input_value()==''
        assert len(f.submissions)==1 and len(f.reads)==1
        # Simulate the human completing the test-only verifier.
        await frame.evaluate('(html)=>document.body.innerHTML=html',TRUST)
        assert await f.adapter.auto_sign_in(f.page)
        assert len(f.submissions)==1 and len(f.reads)==1
        assert any(data.get('status')=='trust_selected' for _,data in f.events)
    finally:await f.context.close()


@pytest.mark.e2e
async def test_unselected_trust_does_not_click_remember_or_trust(browser):
    f=await fixture(browser,outcome=TRUST);f.config.trust_browser=False
    try:
        with pytest.raises(UserAction,match='自行选择'):await f.adapter.auto_sign_in(f.page)
        assert len(f.submissions)==1 and not f.submissions[0]['remember']
        assert not any(data.get('status')=='trust_selected' for _,data in f.events)
    finally:await f.context.close()


@pytest.mark.e2e
async def test_password_only_form_for_other_account_is_not_filled(browser):
    f=await fixture(browser,body='<p>different@example.invalid</p><input type="password"><button>登录</button>')
    try:
        with pytest.raises(UserAction,match='属于保存'):await f.adapter.auto_sign_in(f.page)
        assert await f.page.frames[1].locator('input').input_value()==''
        assert not f.submissions
    finally:await f.context.close()


@pytest.mark.e2e
async def test_password_change_form_is_not_filled(browser):
    f=await fixture(browser,body='<h1>更改密码</h1><input type="password" autocomplete="new-password"><button>继续</button>')
    try:
        with pytest.raises(UserAction,match='修改密码'):await f.adapter.auto_sign_in(f.page)
        assert not f.reads and not f.submissions
    finally:await f.context.close()


@pytest.mark.e2e
async def test_unknown_iframe_cannot_receive_credentials(browser):
    f=await fixture(browser,top='<iframe src="https://attacker.invalid/appleauth/auth/signin"></iframe>')
    try:
        assert not await f.adapter.auto_sign_in(f.page)
        assert not f.reads and not f.submissions
    finally:await f.context.close()


@pytest.mark.e2e
async def test_account_check_preserves_verification_tab_and_reuses_it_after_manual_success(browser):
    f=await fixture(browser,outcome=OTP)
    try:
        with pytest.raises(UserAction,match='验证码'):await f.adapter.check_login()
        pending=f.controller.pending_page
        assert pending and not pending.is_closed() and len(f.context.pages)==2
        assert len(f.submissions)==1
        # Human success in the retained test-only tab, followed by the same check.
        await pending.goto('https://secure7.www.apple.com.cn/shop/account/home')
        assert await f.adapter.check_login() is True
        assert f.controller.pending_page is None and pending.is_closed()
        assert len(f.context.pages)==1 and len(f.submissions)==1
    finally:await f.context.close()


@pytest.mark.e2e
@pytest.mark.parametrize('outcome', ['success','<p>密码不正确</p>'])
async def test_saved_account_is_attempted_before_guest_and_failure_preserves_bag(browser,outcome):
    top='''<h1>登录 Apple Store</h1><button onclick="guestClicks++">以游客身份继续</button>
    <iframe src="%s"></iframe><script>window.guestClicks=0;addEventListener('message',e=>{
    if(e.origin==='https://idmsa.apple.com'&&e.data==='logged')document.body.innerHTML=
    '<h1>取货联系信息</h1><button>继续选择付款方式</button>';});</script>''' % AUTH_URL
    f=await fixture(browser,outcome=outcome,top=top)
    await f.page.goto('https://secure7.www.apple.com.cn/shop/checkout?_s=Fixture')
    try:
        with pytest.raises(UserAction,match='取货人表单' if outcome=='success' else '未接受'):
            await f.adapter.fill_customer(None,f.config)
        assert await f.page.evaluate('window.guestClicks')==0
        assert len(f.submissions)==1 and len(f.reads)==1
    finally:await f.context.close()


@pytest.mark.e2e
async def test_uncertain_login_is_not_replayed_on_resume(browser):
    f=await fixture(browser,outcome=ONE_STEP);f.config.operation_timeout=.2
    try:
        with pytest.raises(UserAction,match='尚未返回'):await f.adapter.auto_sign_in(f.page)
        with pytest.raises(UserAction,match='已尝试'):await f.adapter.auto_sign_in(f.page)
        assert len(f.submissions)==1 and len(f.reads)==1
    finally:await f.context.close()
