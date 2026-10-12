"""Known Apple privacy choices, bounded activation and visible notice checks."""
import hashlib
import json
import re
from urllib.parse import urlsplit
from playwright.async_api import Error as PlaywrightError, expect
from app.models.flow import UserAction
from app.adapters.controls import ensure_checked
from app.services.consent import matches_saved_notice, consent_reason, NOTICE_VERSION


class AppleConsent:
    def consent_container(self,page):
        return page.get_by_role('button',name='同意并继续',exact=True).locator(
            'xpath=ancestor::*[contains(.,"和你的数据隐私")][1]')

    async def handle_consent(self,page,text):
        # Read visible page text in memory only; never log a DOM dump with identities.
        # These legal consent controls are observed on Apple's actual checkout.
        consent=page.get_by_role("button",name="同意并继续",exact=True)
        if await consent.is_visible():
            snapshot=await self.consent_snapshot(page,text)
            if not snapshot:self.control.consume_consent('')
            checkout=urlsplit(page.url).path.rstrip('/')=='/shop/checkout'
            saved_choice=bool(snapshot and checkout and not self.preconsent_used and matches_saved_notice(snapshot,self.privacy_config))
            current_choice=bool(snapshot and self.control.consume_consent(snapshot['key']))
            if saved_choice or current_choice:
                boxes=self.consent_container(page).get_by_role('checkbox').filter(visible=True)
                stage='显示隐私声明';submitted=False
                try:
                    await self.focus_control(page)
                    for index,box in enumerate(await boxes.all()):
                        stage='勾选隐私政策' if index==0 else '勾选境外处理知情声明'
                        await ensure_checked(box,focus=self.focus_control)
                    stage='等待“同意并继续”按钮可用'
                    await expect(consent).to_be_enabled(timeout=5000)
                    if saved_choice:self.preconsent_used=True
                    submitted=True;stage='确认后等待官网继续'
                    await consent.click()
                    await consent.wait_for(state='hidden',timeout=15000)
                except (AssertionError,PlaywrightError,UserAction):
                    self.privacy_seen=False
                    detail='确认已尝试，不会重复提交' if submitted else '尚未提交确认，页面已保留'
                    raise UserAction(f'隐私声明卡在{stage}；{detail}，请查看页面后继续') from None
                self.privacy_seen=False
                self.progress('consent_completed',{'title':'Apple 数据隐私知情同意书',
                                                  'source':'saved' if saved_choice else 'current',
                                                  'notice_version':NOTICE_VERSION if saved_choice else ''})
                return
            key=snapshot['key'] if snapshot else True
            if self.privacy_seen != key:
                self.privacy_seen=key
                diagnostic=consent_reason(snapshot,self.privacy_config,used=self.preconsent_used)
                self.progress('consent_status',diagnostic)
                self.progress('consent_required',dict(snapshot or {'title':'Apple 数据隐私知情同意书'},reason=diagnostic))
            raise UserAction("Apple 要求签署数据隐私知情同意书，请在软件中选择本次确认，或在浏览器处理后点“继续检查”")
        self.privacy_seen=False

    async def consent_snapshot(self,page,text):
        """Only offer the two observed Apple China choices; unknown terms stay manual."""
        heading=re.search(r'Apple\s*和你的数据隐私',text)
        if not heading:return None
        container=self.consent_container(page)
        if not await container.count():return None
        labels=await container.get_by_role('checkbox').filter(visible=True).evaluate_all('''els=>els.map(e=>
          (Array.from(e.labels||[]).map(l=>l.innerText).join(' ') || e.getAttribute('aria-label') ||
           (e.getAttribute('aria-labelledby')||'').split(/\\s+/).map(id=>document.getElementById(id)?.innerText||'').join(' ').trim() ||
           e.closest('label')?.innerText || '').replace(/\\s+/g,' ').trim())''')
        if len(labels)!=2 or not re.search(r'^我同意\s*Apple.*隐私政策',labels[0],re.I) or not re.search(r'^我了解.*中国境外.*存储和处理',labels[1]):return None
        legal=text[heading.start():]
        choice=re.search(r'我同意\s*Apple',legal,re.I)
        if not choice or choice.start()>2000:return None
        details=legal[:choice.start()].strip()
        # Read the visible notice's common container, including text below its
        # button. A new paragraph there must also invalidate a saved choice.
        notice_text=await container.evaluate('''root=>{
          const clone=root.cloneNode(true);
          const originals=Array.from(root.querySelectorAll('*'));
          const copies=Array.from(clone.querySelectorAll('*'));
          originals.forEach((e,i)=>{
            const style=getComputedStyle(e);
            if(style.display==='none' || style.visibility==='hidden' || style.visibility==='collapse') copies[i].remove();
          });
          clone.querySelectorAll('script,style').forEach(e=>e.remove());
          clone.querySelectorAll('button,[role="button"]').forEach(e=>{
            if(/^(同意并继续|不同意并继续购物|关闭|×)$/.test(e.textContent.trim()) || e.getAttribute('aria-label')==='关闭') e.remove();
          });
          return clone.textContent;
        }''')
        notice_heading=re.search(r'Apple\s*和你的数据隐私',notice_text)
        if notice_heading:notice_text=notice_text[notice_heading.start():]
        notice_text=notice_text.replace('不同意并继续购物','').replace('同意并继续','').strip()
        # Private URL/body are hashed in memory only, never emitted or saved.
        key=hashlib.sha256(json.dumps([page.url,text,labels],ensure_ascii=False).encode()).hexdigest()
        return {'title':'Apple 数据隐私知情同意书','key':key,'details':details,'choices':labels,'notice_text':notice_text,
                'scope':urlsplit(page.url).hostname}
