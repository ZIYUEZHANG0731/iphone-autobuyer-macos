from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import random
import time
from uuid import uuid4
from contextlib import nullcontext
from app.models.config import Settings, Identity, Mode, State, STATE_LABELS, ConfigError
from app.models.flow import (StateMachine, RunResult, Receipt, FlowError, UserAction, Unavailable,
                             SubmissionUnknown, Stopped, Availability, RateLimited)
from app.automation.control import Control, ProcessLock
from app.services.storage import Repository
from app.utils.scheduling import future_start

class Engine:
    def __init__(self, repo: Repository, control: Control, *, callback=None, lock_held=False):
        self.repo=repo
        self.control=control
        self.callback=callback or (lambda _:None)
        self.machine=StateMachine()
        self.run_id=str(uuid4())
        self.mode=Mode.WEBSITE_DRY_RUN
        self.steps=[]
        self.order_id=None
        self.message=""
        self.started=time.monotonic()
        self.submit_claimed=False
        self.intent=""
        self.lock_held=lock_held

    def emit(self,state: State,message: str):
        self.machine.move(state)
        self.message=self.repo.redactor.clean(message)
        event={'state':str(state),'label':STATE_LABELS[state],'message':self.message,
               'elapsed':round(time.monotonic()-self.started,2),'mode':str(self.mode),'run_id':self.run_id}
        self.steps.append(event)
        self.repo.update_run(self.run_id,str(self.mode),str(state),self.message,event['elapsed'])
        self.callback(event)

    async def operation(self,func, *, opening_deadline=None):
        while True:
            self.control.check()
            try:
                # Bound stop latency for browsing/selection without terminating a submit request.
                task=asyncio.create_task(func())
                try:
                    while not task.done():
                        await self.control.sleep(.1)
                    return await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task,return_exceptions=True)
            except UserAction as exc:
                # Only a rejected public-entry document GET can retry itself.
                # Login, bag mutations, checkout and submit never use this path.
                if isinstance(exc, RateLimited) and exc.retry_opening and opening_deadline is not None:
                    if time.monotonic() + exc.retry_after >= opening_deadline:
                        raise FlowError('等待商店开放超时；官网要求的等待尚未结束') from None
                    self.emit(self.machine.state, f'官网入口暂时返回异常；{exc.retry_after:.1f} 秒后按原会话重试')
                    await self.control.sleep(exc.retry_after)
                    continue
                previous=self.machine.state
                self.emit(State.PAUSED,str(exc))
                if not self.control.interactive:
                    raise
                await self.control.wait_resume()
                self.emit(previous,"你已请求继续，重新检查当前页面")

    def choose_store(self,config:Settings,states:list[Availability]):
        acceptable=[]
        for a in states:
            if not a.available: continue
            if (config.pickup_from or config.pickup_to) and not a.pickup_date: continue
            if config.pickup_from and a.pickup_date<config.pickup_from: continue
            if config.pickup_to and a.pickup_date>config.pickup_to: continue
            acceptable.append(a)
        for preferred in config.stores:
            if not preferred.enabled: continue
            match=next((a for a in acceptable if (preferred.store_id and preferred.store_id==a.store.store_id) or
                        (not preferred.store_id and preferred.name==a.store.name)),None)
            if match: return match
        return acceptable[0] if acceptable and config.allow_other_stores else None

    async def run(self,config:Settings,adapter,mode:Mode,identity:Identity|None=None):
        self.mode=mode
        self.intent=config.intent_id if mode==Mode.LIVE else f"{mode}:{self.run_id}"
        try:
            with nullcontext() if self.lock_held else ProcessLock(self.repo.path.with_suffix('.run.lock')):
                config.validate(mode)
                if identity:
                    self.repo.redactor.register(identity)
                    identity.validate(test=mode==Mode.LOCAL_TEST)
                self.emit(State.PREPARING,"检查配置并启动正常浏览器会话")
                previous=self.repo.submission(self.intent)
                if previous:
                    # Restart always reconciles; even a rejection requires a new explicit purchase intent.
                    receipt=await self.operation(lambda:adapter.reconcile(config))
                    if receipt:
                        return self.confirm(receipt)
                    self.emit(State.ORDER_UNCERTAIN,"恢复时仍未确认旧请求结果；禁止再次下单")
                    return self.result()
                if mode==Mode.LIVE and config.starts_at:
                    future_start(config.starts_at)
                if mode==Mode.LIVE and config.starts_at and config.checkout_strategy!='guest' and hasattr(adapter,'check_login'):
                    await self.operation(adapter.check_login)
                opening_deadline=time.monotonic()+config.max_wait
                last_refresh_started=time.monotonic()
                await self.operation(lambda:adapter.prepare(config), opening_deadline=opening_deadline)
                if mode==Mode.LIVE and config.starts_at:
                    target=datetime.fromisoformat(config.starts_at).astimezone(timezone.utc)
                    if target>datetime.now(timezone.utc):
                        self.emit(State.WAITING,"浏览器已就绪，按带时区的开售时间等待")
                        rechecked=(target-datetime.now(timezone.utc)).total_seconds()<=600
                        while target>datetime.now(timezone.utc):
                            if not rechecked and (target-datetime.now(timezone.utc)).total_seconds()<=600 and config.checkout_strategy!='guest' and hasattr(adapter,'check_login'):
                                await self.operation(adapter.check_login)
                                rechecked=True
                                self.emit(State.WAITING,'开售前会话检查结束，继续等待；浏览器未重启')
                            await self.control.sleep(min(1,(target-datetime.now(timezone.utc)).total_seconds()))
                        self.emit(State.PREPARING,"已到开售时间，开始检查公开购买页面")
                        if hasattr(adapter,'reload'):
                            last_refresh_started=time.monotonic()
                            await self.operation(adapter.reload, opening_deadline=time.monotonic()+config.max_wait)
                deadline=time.monotonic()+config.max_wait
                delay=5.0 if mode!=Mode.LOCAL_TEST else config.interval
                while not await self.operation(adapter.is_open, opening_deadline=deadline):
                    if time.monotonic()>deadline: raise FlowError("等待商店开放超时")
                    queued=getattr(adapter,'in_queue',False)
                    if getattr(adapter,'navigation_pending',False) or (queued and config.entry_strategy=='wait'):
                        notice='官网正在加载，等待当前请求完成' if getattr(adapter,'navigation_pending',False) else '官网显示等待进店，按所选策略保留页面等待放行'
                        if self.message!=notice:self.emit(State.STORE_UNAVAILABLE,notice)
                        await self.control.sleep(1)
                        continue
                    now=time.monotonic()
                    busy_left=max(0,getattr(adapter,'busy_until',0)-now)
                    last=getattr(adapter,'refresh_started_at',last_refresh_started)
                    # Five seconds between starts, including time already spent
                    # loading. Do not interrupt navigation; a waiting-page GET
                    # follows the user's refresh/wait preference.
                    wait=max(0,last+delay-now,busy_left)
                    if now+wait>=deadline:raise FlowError('等待商店开放超时')
                    self.emit(State.STORE_UNAVAILABLE,f"{'官网显示等待进店，按所选策略刷新入口' if queued else '官网未开放或暂时拥堵'}；{wait:.1f} 秒后重试，登录会话保留")
                    await self.control.sleep(wait)
                    if hasattr(adapter,'reload'):
                        last_refresh_started=time.monotonic()
                        await self.operation(adapter.reload, opening_deadline=deadline)
                    if mode==Mode.LOCAL_TEST:delay=min(config.max_interval,delay*1.8+random.uniform(0,.3))
                self.emit(State.STORE_OPEN,"购买页面可访问；继续验证配置和自提")
                await self.operation(lambda:adapter.select_product(config))
                self.emit(State.PRODUCT_SELECTED,"已选择并检查商品配置")
                resumed_cart=await self.operation(lambda:adapter.resume_cart(config)) if hasattr(adapter,'resume_cart') else False
                selected=None
                if resumed_cart:
                    self.emit(State.PRODUCT_SELECTED,'已有购物袋核对通过；在当前结账页选择允许的取货门店，不重新加购')
                else:
                    stock_delay=max(config.interval,10.0) if mode!=Mode.LOCAL_TEST else config.interval
                    while True:
                        states=await self.operation(lambda:adapter.availability(config))
                        selected=self.choose_store(config,states)
                        if selected: break
                        if config.no_stock=='stop' or time.monotonic()>deadline:
                            raise Unavailable("所有允许的门店均无可接受的自提选项；未切换配送")
                        self.emit(State.PRODUCT_SELECTED,f"允许的门店暂不可取货；{stock_delay:.1f} 秒后重新查询，会话保留")
                        await self.control.sleep(stock_delay)
                        stock_delay=min(config.max_interval,stock_delay*1.8)
                    await self.operation(lambda:adapter.select_pickup(selected,config))
                    self.emit(State.PICKUP_SELECTED,f"已选择 {selected.store.name}；库存尚未锁定")
                await self.operation(lambda:adapter.fill_customer(identity,config))
                actual=getattr(adapter,'selected',None)
                if resumed_cart or (isinstance(actual,Availability) and actual.store.store_id!=selected.store.store_id):
                    if not isinstance(actual,Availability) or not actual.available:
                        raise UserAction('当前结账门店尚未确认，购物袋保留')
                    self.emit(State.PICKUP_SELECTED,f'已核对结账取货门店 {actual.store.name}；库存尚未锁定')
                self.emit(State.CUSTOMER_INFO_FILLED,"取货信息已填写并验证，日志不保存身份字段")
                await self.operation(lambda:adapter.select_payment(config))
                self.emit(State.PAYMENT_SELECTED,"已选择当前页面实际可用的授权付款方式")
                summary=await self.operation(lambda:adapter.review(config))
                self.emit(State.REVIEW_READY,"核对商品、总金额、数量、门店和付款方式")
                summary.authorize(config)
                if mode==Mode.WEBSITE_DRY_RUN:
                    self.emit(State.COMPLETED,"官网试跑已到最终核对，未提交真实订单")
                    return self.result()
                if mode==Mode.LIVE and not config.auto_submit:
                    raise UserAction("自动提交未获授权；请在浏览器核对后自行操作")
                if not adapter.supports_submit:
                    raise UserAction("此网站真实订单提交尚待验证，已暂停")
                self.control.check()
                if not self.repo.claim_submission(self.intent,self.run_id):
                    raise UserAction("购买计划已存在订单提交记录，禁止重复提交")
                self.submit_claimed=True
                self.emit(State.ORDER_SUBMITTING,"订单锁已保存，只允许一次提交")
                try:
                    # Never cancel a submission then retry. Stop or crash leaves the durable lock.
                    receipt=await adapter.submit(config)
                    if not receipt or not receipt.order_id:
                        raise SubmissionUnknown("未收到服务器订单确认")
                except Exception:
                    self.repo.record_submission(self.intent,"UNKNOWN")
                    self.emit(State.ORDER_UNCERTAIN,"提交结果不明，仅查询正常订单页面，不重复提交")
                    deadline=time.monotonic()+config.reconcile_timeout
                    receipt=None
                    while time.monotonic()<deadline and not self.control.stop_event.is_set():
                        receipt=await adapter.reconcile(config)
                        if receipt: break
                        await self.control.sleep(min(max(config.interval,.15),3))
                    if not receipt: return self.result()
                return self.confirm(receipt)
        except Stopped:
            if self.submit_claimed and self.machine.state not in (State.ORDER_CONFIRMED,State.PAYMENT_PENDING,State.COMPLETED):
                self.repo.record_submission(self.intent,"UNKNOWN")
                if self.machine.state==State.ORDER_SUBMITTING:
                    self.emit(State.ORDER_UNCERTAIN,"停止时提交结果尚未确认；订单锁保留，请先查询订单")
            else:
                self.emit(State.STOPPED,"用户已停止，当前阶段已保存")
        except UserAction as exc:
            if self.machine.state!=State.PAUSED:
                self.emit(State.PAUSED,str(exc))
        except (FlowError,ConfigError) as exc:
            self.emit(State.FAILED,str(exc))
        except Exception as exc:
            # Raw exceptions from browser filling can echo private values. Do not log them.
            self.emit(State.FAILED,f"操作未完成（{type(exc).__name__}）；请检查浏览器当前页面及网络后重试")
        return self.result()

    def confirm(self,receipt:Receipt):
        self.order_id=receipt.order_id
        self.repo.record_submission(self.intent,"CONFIRMED",receipt.order_id)
        self.emit(State.ORDER_CONFIRMED,"服务器已确认订单创建；自动提交已停止")
        if receipt.status=='payment_pending':
            deadline=f"；页面付款截止时间：{receipt.payment_deadline}" if receipt.payment_deadline else "；页面未提供付款截止时间"
            self.emit(State.PAYMENT_PENDING,"请在浏览器手动完成付款"+deadline)
        else:
            self.emit(State.COMPLETED,"服务器订单状态已保存")
        return self.result()

    def result(self):
        return RunResult(self.run_id,str(self.mode),str(self.machine.state),self.message,self.steps,self.order_id)
