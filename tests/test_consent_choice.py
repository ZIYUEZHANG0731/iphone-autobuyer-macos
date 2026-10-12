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
    (False,True,NOTICE_VERSION),
])
async def test_explicitly_disabling_either_privacy_choice_stops_automatic_consent(consent_page,policy,transfer,version):
    page=consent_page;await page.set_content(SAVED_NOTICE_HTML)
    adapter=AppleAdapter(page,Control(False))
    adapter.privacy_config=Settings(privacy_policy_accepted=policy,privacy_transfer_acknowledged=transfer,
                                   privacy_notice_version=version)
    with pytest.raises(UserAction):await adapter.guard()
    assert await page.evaluate('window.accepts')==0
    assert not any([await c.is_checked() for c in await page.get_by_role('checkbox').all()])


@pytest.mark.parametrize('html',[
    SAVED_NOTICE_HTML.replace(NOTICE_CHOICES[1],NOTICE_CHOICES[1]+'额外授权。'),
    SAVED_NOTICE_HTML.replace('<button','<label><input type="checkbox">额外同意事项</label><button'),
])
async def test_automatic_privacy_choice_does_not_select_unknown_extra_choices(consent_page,html):
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


def styled_notice(kind='native'):
    choices=[]
    for index,label in enumerate(NOTICE_CHOICES):
        if kind=='native':
            choice=f'<input id="choice{index}" type="checkbox" style="position:absolute;opacity:0;pointer-events:none"><label for="choice{index}">{label}</label>'
        else:
            choice=f'<span role="checkbox" tabindex="0" style="display:inline-block;width:20px;height:20px;border:1px solid" aria-checked="false" aria-labelledby="label{index}" onclick="this.setAttribute(\'aria-checked\',this.getAttribute(\'aria-checked\')!==\'true\');update()"></span><span id="label{index}">{label}</span>'
        choices.append(f'<div>{choice}</div>')
    return f'''<main><article role="dialog"><h1>{NOTICE_TITLE}</h1>
      <p>{NOTICE_INTRO[0]}</p><p>{NOTICE_INTRO[1]}</p>{''.join(choices)}
      <p>{NOTICE_MORE}</p><div hidden>隐藏的旧弹窗模板与旧条款</div>
      <button id="accept" disabled onclick="window.accepts++;document.querySelector('article').innerHTML='<h1>取货方式</h1>'">同意并继续</button>
      <button aria-label="关闭">×</button><a href="#">不同意并继续购物</a>
      </article></main><script>
      window.accepts=0;
      function update(){{const choices=document.querySelector('article').querySelectorAll('input,[role="checkbox"]');
        document.querySelector('#accept').disabled=!Array.from(choices).every(e=>e.checked||e.getAttribute('aria-checked')==='true');}}
      document.addEventListener('change',update);
      document.addEventListener('keydown',e=>{{if(e.code==='Space')e.preventDefault();}});
      </script>'''


@pytest.mark.parametrize('kind',['native','role'])
async def test_saved_notice_accepts_styled_controls_and_close_button_without_keyboard(consent_page,kind):
    page=consent_page;await page.set_content(styled_notice(kind));events=[]
    assert await page.get_by_role('button',name='同意并继续',exact=True).is_disabled()
    adapter=AppleAdapter(page,Control(False),progress=lambda k,d:events.append((k,d)))
    adapter.privacy_config=saved_privacy_settings()
    await adapter.guard()
    assert await page.get_by_role('heading',name='取货方式').is_visible()
    assert await page.evaluate('window.accepts')==1
    assert any(k=='consent_completed' and d['source']=='saved' for k,d in events)


async def test_unchecked_control_failure_does_not_consume_saved_submission(consent_page,monkeypatch):
    import app.adapters.apple_consent as consent_module
    page=consent_page;await page.set_content(styled_notice())
    adapter=AppleAdapter(page,Control(False));adapter.privacy_config=saved_privacy_settings()
    activate=consent_module.ensure_checked
    async def unavailable(*args,**kwargs):raise UserAction('fixture control not ready')
    monkeypatch.setattr(consent_module,'ensure_checked',unavailable)
    with pytest.raises(UserAction,match='勾选隐私政策.*尚未提交确认'):await adapter.guard()
    assert not adapter.preconsent_used and await page.evaluate('window.accepts')==0
    monkeypatch.setattr(consent_module,'ensure_checked',activate)
    await adapter.guard()
    assert await page.evaluate('window.accepts')==1 and adapter.preconsent_used


async def test_unknown_privacy_choice_diagnostic_does_not_report_a_failed_click(consent_page):
    page=consent_page;await page.set_content(styled_notice().replace(NOTICE_CHOICES[1],NOTICE_CHOICES[1]+'额外授权。'));events=[]
    adapter=AppleAdapter(page,Control(False),progress=lambda k,d:events.append((k,d)))
    adapter.privacy_config=saved_privacy_settings()
    with pytest.raises(UserAction):await adapter.guard()
    assert next(d for k,d in events if k=='consent_status')=='官网隐私选项结构无法确认，页面已保留；请查看官网提示'
    assert not adapter.preconsent_used and await page.evaluate('window.accepts')==0


async def test_background_checkout_checkbox_is_not_part_of_notice_or_automatically_selected(consent_page):
    page=consent_page
    await page.set_content('<label><input type="checkbox" id="outside">其他结账选择</label>'+styled_notice())
    adapter=AppleAdapter(page,Control(False));adapter.privacy_config=saved_privacy_settings()
    await adapter.guard()
    assert await page.evaluate('window.accepts')==1
    assert not await page.locator('#outside').is_checked()


async def test_actual_apple_link_hint_and_current_intro_preserve_saved_choice(consent_page):
    # Copied public wording from the actual 2026-10-12 checkout UI, rather than
    # assuming that an external link's accessibility hint is part of the terms.
    html=styled_notice().replace(NOTICE_CHOICES[0],
        '我同意<a href="https://www.apple.com.cn/legal/privacy/">Apple 的隐私政策 '
        '<span>(在新窗口中打开)</span></a>').replace(NOTICE_MORE,
        '我们提供包括 Apple 隐私政策在内的更多信息。进一步了解Apple 隐私政策(在新窗口中打开)。')
    page=consent_page;await page.set_content(html);events=[]
    adapter=AppleAdapter(page,Control(False),progress=lambda k,d:events.append((k,d)))
    adapter.privacy_config=saved_privacy_settings()
    # Existing saved choices from v0.7.0 must not be silently discarded.
    assert NOTICE_VERSION=='367ecee9ba9fa0016c563f7b8b0cb97cf45de7c403c81d05a51e555ccb086292'
    await adapter.guard()
    assert await page.evaluate('window.accepts')==1
    assert not any(k=='consent_required' for k,d in events)


async def test_automatic_privacy_choice_is_not_blocked_by_the_notice_paragraph_version(consent_page):
    page=consent_page;html=styled_notice().replace('所必要的数据','所必需的数据')
    adapter=AppleAdapter(page,Control(False));adapter.privacy_config=saved_privacy_settings()
    await page.set_content(html);await adapter.guard()
    assert await page.evaluate('window.accepts')==1
    adapter=AppleAdapter(page,Control(False));adapter.privacy_config=saved_privacy_settings()
    await page.set_content(html.replace('开展服务运营和改进用户体验','开展广告营销和改进用户体验'))
    await adapter.guard()
    assert await page.evaluate('window.accepts')==1


@pytest.mark.parametrize('version',['','older-notice'])
async def test_default_privacy_consent_does_not_require_saving_a_version_first(consent_page,version):
    page=consent_page;await page.set_content(styled_notice());events=[]
    adapter=AppleAdapter(page,Control(False),progress=lambda k,d:events.append((k,d)))
    adapter.privacy_config=Settings(privacy_notice_version=version)
    await adapter.guard()
    assert await page.evaluate('window.accepts')==1
    assert not any(k=='consent_required' for k,d in events)


async def test_resuming_without_consent_republishes_the_current_gui_entry(consent_page):
    page=consent_page;events=[]
    adapter=AppleAdapter(page,Control(False),progress=lambda k,d:events.append((k,d)))
    for _ in range(2):
        with pytest.raises(UserAction):await adapter.guard()
        adapter.control.resume()
    notices=[d for k,d in events if k=='consent_required']
    assert len(notices)==2 and notices[0]['key']==notices[1]['key']
    assert await page.evaluate('window.accepts')==0
