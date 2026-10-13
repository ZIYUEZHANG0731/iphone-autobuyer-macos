"""Recover fulfillment in the existing checkout, using only allowed stores."""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from playwright.async_api import Error as PlaywrightError, expect
from app.adapters.checkout_state import checkout_state
from app.models.flow import Availability, FlowError, UserAction


def pickup_date(quote):
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    if '今天' in quote:return today.isoformat()
    if '明天' in quote:return (today+timedelta(days=1)).isoformat()
    match=re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日',quote)
    if match:
        try:return date(today.year,int(match[1]),int(match[2])).isoformat()
        except ValueError:pass


def store_pattern(store):
    name=re.sub(r'^Apple\s*','',store.name).strip()
    return re.compile(r'^(?:\d+\s+)?(?:Apple\s*)?'+re.escape(name).replace(r'\ ',r'\s*')+r'(?:\s|$)')


def available_store(row,config):
    quote=row['label']
    if row['disabled'] or re.search(r'不可取货|无法取货|缺货|无货',quote):return False
    if not re.search(r'可取货|可以取货',quote):return False
    when=pickup_date(quote)
    return (not (config.pickup_from or config.pickup_to) or bool(when)) and (
        not config.pickup_from or when>=config.pickup_from) and (not config.pickup_to or when<=config.pickup_to)


PICKUP_SLOT=re.compile(r'^\s*\d{1,2}[:：]\d{2}\s*[–—\-~至]\s*\d{1,2}[:：]\d{2}\s*$')


class ApplePickup:
    async def _set_missing_checkout_region(self,config):
        # A reused bag can enter fulfillment without the product modal's
        # region cookie. The observed fresh checkout button has aria-label
        # "false", so identify its visible text rather than that broken name.
        if self.checkout_last_continued:return
        location=self.page.locator('button[aria-expanded]').filter(
            has_text=re.compile(r'^\s*选择地区\s*$')).filter(visible=True)
        if not await location.count():return
        if await location.count()!=1:
            raise UserAction('官网取货地区入口无法唯一确认，当前购物袋保留')
        store=self.store_directory.enrich(next(s for s in config.stores if s.enabled))
        if not all((store.province or store.city,store.city,store.district)):
            raise UserAction('官网需要选择取货地区，请完善首选门店的省市和区县；当前购物袋保留')
        try:
            await self.checkout_updates.ready(self.control)
            if await location.get_attribute('aria-expanded')!='true':
                await location.evaluate('(button)=>button.click()')
            province=self.page.get_by_role('tab',name=re.compile(r'省份|省\/市\/自治区')).filter(visible=True)
            try:await expect(province).to_have_count(1,timeout=8000)
            except AssertionError:
                raise UserAction('官网取货省市标签无法唯一确认，当前购物袋保留')
            if await province.get_attribute('aria-selected')!='true':
                await province.evaluate('(button)=>button.click()')
            panel=self.page.get_by_role('tabpanel').filter(visible=True)
            await panel.get_by_role('button',name=store.province or store.city,exact=True).evaluate('(button)=>button.click()')
            await self.guard()
            if store.city!=(store.province or store.city):
                await panel.get_by_role('button',name=store.city,exact=True).evaluate('(button)=>button.click()')
                await self.guard()
            await panel.get_by_role('button',name=store.district,exact=True).evaluate('(button)=>button.click()')
            await expect(location).to_have_count(0,timeout=8000)
            await self.checkout_updates.ready(self.control,label='取货地区')
            self.progress('checkout_region',{'province':store.province,'city':store.city,
                'district':store.district,'message':'已按首选门店设置缺失的取货地区，等待官网门店列表'})
        except (AssertionError,PlaywrightError):
            await self.guard()
            raise UserAction('官网尚未确认取货地区，当前购物袋保留；请查看地区选择提示后继续') from None

    async def _select_pickup_slot(self):
        slots=self.page.get_by_role('combobox',name=re.compile(r'签到时段|取货.*时段|可选时段')).filter(visible=True)
        count=await slots.count()
        if not count:return None
        if count!=1:raise UserAction('官网取货时段无法唯一确认，当前购物袋保留')
        await expect(slots).to_be_enabled(timeout=8000)
        state=await slots.evaluate('''select=>({tag:select.tagName,options:Array.from(select.options||[]).map(o=>({
            value:o.value,label:o.label,selected:o.selected,
            disabled:o.disabled||(o.parentElement.tagName==='OPTGROUP'&&o.parentElement.disabled)
        }))})''')
        if state['tag']!='SELECT':raise UserAction('官网取货时段控件尚不支持，当前购物袋保留')
        available=[o for o in state['options'] if o['value'] and not o['disabled'] and PICKUP_SLOT.fullmatch(o['label'])]
        if not available:raise UserAction('官网暂无可确认的取货时段，当前购物袋保留')
        choice=next((o for o in available if o['selected']),available[0])
        if not choice['selected']:
            await slots.select_option(value=choice['value'])
            # Read back after the DOM change so its normal network events are
            # delivered before checking the pending checkout writes.
            if await slots.input_value()!=choice['value']:
                raise UserAction('官网未接受取货时段，当前购物袋保留')
            await self.checkout_updates.ready(self.control,label='取货时段')
        if await slots.input_value()!=choice['value']:
            raise UserAction('官网未接受取货时段，当前购物袋保留')
        return choice['label']

    def checkout_stores(self,snapshot,config):
        preferences=[self.store_directory.enrich(s) for s in config.stores if s.enabled]
        if self.selected and config.allow_other_stores and not any(s.store_id==self.selected.store.store_id for s in preferences):
            preferences.append(self.selected.store)
        found=[];missing=[]
        for index,store in enumerate(preferences):
            rows=[r for r in snapshot['radios'] if store_pattern(store).match(r['label'])]
            if len(rows)>1:raise UserAction('结账门店无法唯一确认，当前购物袋保留')
            by_id=[r for r in snapshot['radios'] if store.store_id and r['value']==store.store_id]
            if (rows and store.store_id and rows[0]['value']!=store.store_id) or (by_id and not rows):
                raise UserAction('结账门店编号与目标门店不一致，页面已保留；不会选择其他门店')
            if rows:
                if not re.fullmatch(r'R\d{3,6}',rows[0]['value']):
                    raise UserAction('官网取货门店编号无法确认，当前购物袋保留')
                store.store_id=rows[0]['value']
                found.append((index,store,rows[0]))
            else:missing.append((index,store))
        return found,missing

    async def _select_checkout_pickup(self,config):
        if not self.selected:raise UserAction('本次任务尚未核对目标取货门店，请重新检查库存')
        self.checkout_pickup_confirmed=None
        await self.focus_control(self.page)
        pickup=self.page.get_by_role('radio',name=re.compile(r'^(我要)?取货(?:\s|$)')).filter(visible=True)
        if await pickup.count()==1:await self._check_radio(pickup)
        await self._set_missing_checkout_region(config)
        tried=set();initial_store_waited=False
        while True:
            await self.guard()
            snapshot=await checkout_state(self.page)
            # A continuation with uncertain results must not be repeated. A
            # disabled previous store is an explicit stock change, allowing a
            # new fulfillment choice while the same bag remains intact.
            if self.checkout_last_continued:
                previous=next((r for r in snapshot['radios'] if r['value']==self.checkout_last_continued),None)
                if not previous or (not previous['disabled'] and not re.search(r'不可取货|无法取货|缺货|无货',previous['label'])):
                    raise UserAction('取货门店继续已尝试，官网尚未完成跳转；保留页面，不重复提交')
            found,missing=self.checkout_stores(snapshot,config)
            candidates=[(i,s,r) for i,s,r in found if available_store(r,config) and r['value'] not in self.checkout_continued_stores and r['value'] not in tried]
            # Expand once only when a higher-priority store is still hidden, or
            # when no allowed visible store can currently accept pickup.
            higher_missing=bool(candidates and any(index<candidates[0][0] for index,store in missing))
            more=self.page.get_by_role('button',name='显示更多取货地点',exact=True)
            if missing and not found and not any(re.fullmatch(r'R\d{3,6}',r['value']) for r in snapshot['radios']) and not await more.is_visible():
                if initial_store_waited:raise UserAction('官网取货门店列表仍在加载，当前购物袋保留')
                initial_store_waited=True
                pattern=re.compile('|'.join('(?:'+store_pattern(s).pattern+')' for index,s in missing))
                try:await self.page.get_by_role('radio',name=pattern).filter(visible=True).first.wait_for(timeout=8000)
                except PlaywrightError:await self.guard()
                continue
            if missing and (not candidates or higher_missing) and not self.checkout_more_attempted and await more.is_visible():
                self.checkout_more_attempted=True
                await more.click()
                pattern=re.compile('|'.join('(?:'+store_pattern(s).pattern+')' for index,s in missing))
                try:await self.page.get_by_role('radio',name=pattern).filter(visible=True).first.wait_for(timeout=8000)
                except PlaywrightError:await self.guard()
                continue
            if not candidates:
                if missing:
                    raise UserAction('当前结账页尚未显示可确认的允许取货门店，不能判定全部无货；购物袋保留，可在此更改取货区域或等待，不必重新加购')
                raise UserAction('目标门店已不可用，所有允许的备用门店也暂无符合条件的取货选项；购物袋保留，请在当前结账页选择或等待，不必重新加购')
            index,store,row=candidates[0];tried.add(row['value'])
            target=self.page.get_by_role('radio',name=store_pattern(store)).filter(visible=True)
            continuation=self.page.get_by_role('button',name='继续填写取货详情',exact=True)
            try:
                await self._check_radio(target)
                await self.checkout_updates.ready(self.control)
                slot=await self._select_pickup_slot()
                await expect(continuation).to_be_enabled(timeout=8000)
            except UserAction:
                await self.guard()
                raise
            except (AssertionError,PlaywrightError):
                await self.guard()
                fresh=await checkout_state(self.page)
                current=next((r for r in fresh['radios'] if r['value']==row['value']),None)
                if current and not available_store(current,config):continue
                raise UserAction('官网尚未确认门店或取货时间，当前购物袋保留') from None
            fresh=await checkout_state(self.page)
            current=next((r for r in fresh['radios'] if r['value']==row['value']),None)
            if not current or not current['checked']:
                raise UserAction('官网未接受目标取货门店，当前购物袋保留')
            if not available_store(current,config):continue
            if slot:self.progress('checkout_pickup',{'store':store.name,'message':'已选择官网可用取货时段 '+slot})
            old=self.selected.store
            self.selected=Availability(store,True,current['label'],pickup_date(current['label']))
            if old.store_id!=store.store_id:
                self.identity_filled=False
                self.progress('pickup_changed',{'previous':old.name,'store':store.name,'store_id':store.store_id})
            self.pickup_continue_attempted=True
            self.checkout_last_continued=row['value'];self.checkout_continued_stores.add(row['value'])
            # Activate this observed, enabled HTML control directly. An input
            # acknowledgement (mouse/Enter) did not produce a checkout request
            # in the installed Qt host; do not replay the underlying request.
            try:await continuation.evaluate('(button) => button.click()')
            except PlaywrightError:
                raise UserAction('取货门店继续的结果尚未确认；当前购物袋保留，不重复点击') from None
            self.progress('checkout_pickup',{'store':store.name,'store_id':store.store_id,'message':'已核对结账取货门店，等待取货资料页'})
            return

    async def review_pickup_store(self,body):
        confirmed=self.checkout_pickup_confirmed
        if not confirmed or confirmed[0] is not self.page or not self.selected:
            raise UserAction('本次结账取货门店尚未由官网确认；购物袋保留，请完成门店选择后继续')
        store=confirmed[1]
        if (store.store_id!=self.selected.store.store_id or store.name!=self.selected.store.name
                or not self.selected.available):
            raise UserAction('本次结账取货门店已变化，需重新通过官网确认；购物袋保留')
        pickup=self.page.get_by_role('link',name=re.compile(r'^更改零售店(?:\s|$)')).filter(visible=True)
        if await pickup.count()!=1:
            raise UserAction('最终核对页无法确认自提方式；购物袋保留，未提交订单')
        # The actual Review omits the name but retains its pickup edit link.
        # Visible contradictory store information must still reject submission.
        normalized=re.sub(r'\s+','',body)
        if any(row['store_id']!=store.store_id and re.sub(r'\s+','',row['name']) in normalized
               for row in self.store_directory.rows):
            raise FlowError('最终核对页门店与官网已保存的取货选择不一致')
        return store
