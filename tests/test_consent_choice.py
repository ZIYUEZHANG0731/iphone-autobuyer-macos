import asyncio
from types import SimpleNamespace
import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.flow import UserAction, RateLimited
from app.models.config import Settings
from app.services.consent import NOTICE_TITLE, NOTICE_INTRO, NOTICE_CHOICES, NOTICE_MORE, NOTICE_VERSION
from app.services.storage import Repository


CONSENT='''<h1>Apple 和你的数据隐私</h1><p>中国法律要求签署知情同意书。</p>
  <label><input type="checkbox">我同意Apple 的隐私政策</label>
  <label><input type="checkbox">我了解，当我使用我的账号或访客结账时，有限的个人信息可能会在中国境外存储和处理。</label>
  <button onclick="window.accepts++;document.body.innerHTML='<h1>取货方式</h1>'">同意并继续</button>
  <script>window.accepts=0</script>'''

SAVED_NOTICE_HTML=f'''<h1>{NOTICE_TITLE}</h1>
  <p>{NOTICE_INTRO[0]}</p><p>{NOTICE_INTRO[1]}</p>
  <label><input type="checkbox">{NOTICE_CHOICES[0]}↗</label>
  <label><input type="checkbox">{NOTICE_CHOICES[1]}</label>
  <p>{NOTICE_MORE}</p>
  <button onclick="window.accepts++;document.body.innerHTML='<h1>取货方式</h1>'">同意并继续</button>
  <script>window.accepts=0</script>'''


def saved_privacy_settings():
    return Settings(privacy_policy_accepted=True,privacy_transfer_acknowledged=True,
                    privacy_notice_version=NOTICE_VERSION)


@pytest.fixture
async def consent_page():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        page=await browser.new_page()
        await page.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body=CONSENT))
        await page.goto('https://secure.www.apple.com.cn/shop/checkout')
        try:yield page
        finally:await browser.close()


async def test_consent_requires_this_page_choice_and_is_consumed_once(consent_page):
    page=consent_page;events=[];control=Control(False)
    adapter=AppleAdapter(page,control,progress=lambda k,d:events.append((k,d)))
    with pytest.raises(UserAction):await adapter.guard()
    assert not any([await c.is_checked() for c in await page.get_by_role('checkbox').all()])
    snapshot=events[-1][1];assert len(snapshot['choices'])==2
    assert '中国法律' in snapshot['details'] and snapshot['scope']=='secure.www.apple.com.cn'
    control.approve_consent(snapshot['key']);await adapter.guard()
    assert await page.get_by_role('heading',name='取货方式').is_visible()
    assert await page.evaluate('window.accepts')==1
    assert any(k=='consent_completed' for k,d in events)
    await page.set_content(CONSENT)
    with pytest.raises(UserAction):await adapter.guard()
    assert await page.evaluate('window.accepts')==0


async def test_changed_terms_invalidate_old_choice(consent_page):
    page=consent_page;events=[];control=Control(False)
    adapter=AppleAdapter(page,control,progress=lambda k,d:events.append((k,d)))
    with pytest.raises(UserAction):await adapter.guard()
    control.approve_consent(events[-1][1]['key'])
    await page.set_content(CONSENT.replace('中国法律要求签署知情同意书。','已改变的协议正文。'))
    with pytest.raises(UserAction):await adapter.guard()
    assert await page.evaluate('window.accepts')==0
    assert not any([await c.is_checked() for c in await page.get_by_role('checkbox').all()])
    assert control.consent_approval is None


async def test_unknown_additional_consent_never_uses_the_previous_choice(consent_page):
    page=consent_page;events=[];control=Control(False)
    adapter=AppleAdapter(page,control,progress=lambda k,d:events.append((k,d)))
    with pytest.raises(UserAction):await adapter.guard()
    control.approve_consent(events[-1][1]['key'])
    await page.set_content(CONSENT+'<label><input type="checkbox">未知的新授权</label>')
    with pytest.raises(UserAction):await adapter.guard()
    assert 'key' not in events[-1][1]
    assert await page.evaluate('window.accepts')==0 and control.consent_approval is None


def test_consent_choice_expires_and_task_stop_clears_it(monkeypatch):
    import app.automation.control as module
    clock=[100.];monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    control=Control(False);control.approve_consent('fixture')
    clock[0]=221.;assert not control.consume_consent('fixture')
    control.approve_consent('fixture');control.cancel_task();assert not control.consume_consent('fixture')
    control.approve_consent('fixture');control.reset_task();assert not control.consume_consent('fixture')


async def test_explicit_access_denied_is_not_an_automatic_http_retry(consent_page):
    page=consent_page;control=Control(False);adapter=AppleAdapter(page,control)
    await page.set_content('<h1>Access denied</h1>')
    adapter.http_status=403;adapter.opening_retry=True;adapter.limited_until=asyncio.get_running_loop().time()+5
    with pytest.raises(UserAction) as caught:await adapter.guard()
    assert not isinstance(caught.value,RateLimited)


async def test_saved_choices_survive_restart_and_accept_the_known_notice_without_a_prompt(consent_page,tmp_path):
    repo=Repository(tmp_path/'preferences.sqlite3');repo.save_config(saved_privacy_settings())
    config=Repository(tmp_path/'preferences.sqlite3').load_config()
    page=consent_page;await page.set_content(SAVED_NOTICE_HTML);events=[]
    adapter=AppleAdapter(page,Control(False),progress=lambda k,d:events.append((k,d)))
    adapter.privacy_config=config
    await adapter.guard()
    assert await page.get_by_role('heading',name='取货方式').is_visible()
    assert await page.evaluate('window.accepts')==1
    assert not any(k=='consent_required' for k,d in events)
    assert next(d for k,d in events if k=='consent_completed')['source']=='saved'
    # A failed or recurring notice is not sent again by the same task.
    await page.set_content(SAVED_NOTICE_HTML)
    with pytest.raises(UserAction):await adapter.guard()
    assert await page.evaluate('window.accepts')==0


@pytest.mark.parametrize('policy,transfer,version',[
    (False,False,NOTICE_VERSION),(True,False,NOTICE_VERSION),
    (False,True,NOTICE_VERSION),(True,True,'older-notice'),(True,True,''),
])
async def test_both_saved_choices_and_the_displayed_notice_version_are_required(consent_page,policy,transfer,version):
    page=consent_page;await page.set_content(SAVED_NOTICE_HTML)
    adapter=AppleAdapter(page,Control(False))
    adapter.privacy_config=Settings(privacy_policy_accepted=policy,privacy_transfer_acknowledged=transfer,
                                   privacy_notice_version=version)
    with pytest.raises(UserAction):await adapter.guard()
    assert await page.evaluate('window.accepts')==0
    assert not any([await c.is_checked() for c in await page.get_by_role('checkbox').all()])


@pytest.mark.parametrize('html',[
    SAVED_NOTICE_HTML.replace(NOTICE_INTRO[1],'新的法律声明内容。'),
    SAVED_NOTICE_HTML.replace(NOTICE_CHOICES[1],NOTICE_CHOICES[1]+'额外授权。'),
    SAVED_NOTICE_HTML.replace('<button','<p>新增的一项条款。</p><button'),
    SAVED_NOTICE_HTML.replace('<button','<label><input type="checkbox">额外同意事项</label><button'),
    SAVED_NOTICE_HTML.replace('<script>','<p>声明后新增的一项条款。</p><script>'),
])
async def test_saved_choices_stop_for_changed_or_added_terms(consent_page,html):
    page=consent_page;await page.set_content(html)
    adapter=AppleAdapter(page,Control(False));adapter.privacy_config=saved_privacy_settings()
    with pytest.raises(UserAction):await adapter.guard()
    assert await page.evaluate('window.accepts')==0
    assert not any([await c.is_checked() for c in await page.get_by_role('checkbox').all()])


async def test_saved_checkout_choice_does_not_apply_to_an_account_page(consent_page):
    page=consent_page
    await page.goto('https://secure.www.apple.com.cn/shop/account/home')
    await page.set_content(SAVED_NOTICE_HTML)
    adapter=AppleAdapter(page,Control(False));adapter.privacy_config=saved_privacy_settings()
    with pytest.raises(UserAction):await adapter.guard()
    assert await page.evaluate('window.accepts')==0
