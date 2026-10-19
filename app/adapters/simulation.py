from __future__ import annotations
import json
from decimal import Decimal
from playwright.async_api import TimeoutError as PlaywrightTimeout
from app.models.config import Store
from app.models.flow import FlowError,UserAction,RateLimited,Availability,Summary,Receipt,SubmissionUnknown

class SimulationAdapter:
    supports_submit=True
    def __init__(self,page,server,control):
        self.page=page;self.server=server;self.control=control;self.config=None
    async def prepare(self,config):
        self.config=config
        await self.page.goto(self.server.url,wait_until='domcontentloaded')
    async def is_open(self):
        return not await self.page.get_by_role('heading',name='商店维护',exact=True).count()
    async def reload(self):
        await self.page.reload(wait_until='domcontentloaded')
    async def select_product(self,config):
        await self.page.get_by_role('heading',name='选择商品',exact=True).wait_for(timeout=1500)
        for k,v in config.product.options.items():
            choices=await self.page.get_by_label(k,exact=True).locator('option').all_text_contents()
            if v not in choices: raise FlowError('模拟商品配置不存在')
            await self.page.get_by_label(k,exact=True).select_option(v)
        await self.page.get_by_label('数量',exact=True).fill(str(config.product.quantity))
        await self.page.get_by_role('button',name='继续选择门店',exact=True).click()
        await self.page.get_by_role('heading',name='选择自提门店',exact=True).wait_for(timeout=1500) if self.server.scenario!='rate_limited' else None
    async def availability(self,config):
        if self.server.scenario=='rate_limited': raise RateLimited('模拟 HTTP 429：暂停等待用户确认')
        if await self.page.get_by_role('button',name='刷新门店',exact=True).count():
            await self.page.get_by_role('button',name='刷新门店',exact=True).click()
        radios=await self.page.locator('input[name="store"]').evaluate_all('els=>els.map(e=>({name:e.dataset.name,city:e.dataset.city,id:e.value,available:!e.disabled,date:e.dataset.date,quote:e.dataset.quote}))')
        return [Availability(Store(r['name'],r['city'],store_id=r['id']),r['available'],r['quote'],r['date']) for r in radios]
    async def select_pickup(self,item,config):
        await self.page.get_by_role('radio',name=item.store.name,exact=True).check()
        await self.page.get_by_role('button',name='继续填写取货人',exact=True).click()
    async def fill_customer(self,identity,config):
        if identity is None: raise FlowError('模拟取货信息不完整')
        identity.validate(test=True)
        for field,label in {'name':'取货人姓名','document_type':'证件类型','document_number':'证件号码','phone':'手机号码','email':'电子邮箱'}.items():
            loc=self.page.get_by_label(label,exact=True);value=getattr(identity,field)
            await loc.fill(value)
            if await loc.input_value()!=value: raise FlowError('模拟表单未接受字段')
        await self.page.get_by_role('button',name='继续选择付款方式',exact=True).click()
    async def select_payment(self,config):
        # The preceding button starts an async fetch. Observe the resulting
        # stage instead of depending on the engine's old fixed 100ms pause.
        await self.page.get_by_role('heading',name='选择付款方式',exact=True).wait_for(timeout=1500)
        for p in config.payments:
            loc=self.page.get_by_role('radio',name=p,exact=True)
            if await loc.count() and await loc.is_enabled():
                await loc.check();return p
        raise UserAction('模拟首选及备用付款方式均不可用')
    async def review(self,config):
        await self.page.get_by_role('button',name='继续核对订单',exact=True).click()
        raw=json.loads(await self.page.get_by_test_id('summary').get_attribute('data-json'))
        raw['total']=Decimal(raw['total'])
        return Summary(**raw)
    async def submit(self,config):
        await self.page.get_by_role('button',name='提交模拟订单',exact=True).click()
        try:
            await self.page.get_by_test_id('receipt').wait_for(timeout=int(config.operation_timeout*1000))
            r=json.loads(await self.page.get_by_test_id('receipt').get_attribute('data-json'))
            return Receipt(r['order_id'],r['status'],r.get('deadline'))
        except PlaywrightTimeout:
            raise SubmissionUnknown('模拟提交未确认') from None
    async def reconcile(self,config):
        # A separate normal order page confirms a known synthetic correlation token.
        p=await self.page.context.new_page()
        try:
            await p.goto(f'{self.server.url}/orders?token={self.server.token}')
            data=json.loads(await p.locator('body').inner_text()).get('order')
            return Receipt(data['order_id'],data['status'],data.get('deadline')) if data else None
        finally:
            await p.close()
