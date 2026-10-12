from __future__ import annotations
from copy import deepcopy
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from threading import Thread,Lock
from urllib.parse import urlsplit,parse_qs
from uuid import uuid4
import json
import time
from app.models.config import Settings,Store

SCENARIOS={
 'first_available':'首选门店有货', 'second_available':'首选缺货，第二家有货', 'all_unavailable':'所有门店无货',
 'stock_changes':'首选门店稍后补货', 'missing_option':'商品配置不存在', 'payment_unavailable':'付款方式不可用',
 'identity_incomplete':'取货信息不完整', 'maintenance':'维护后开放', 'network_timeout':'页面暂时超时',
 'submit_timeout':'提交超时且结果未知', 'delayed_success':'订单成功但响应延迟', 'order_failure':'服务器拒绝订单',
 'rate_limited':'HTTP 429 暂停', 'stop':'等待中手动停止',
}

class SimStore:
    def __init__(self,config:Settings,scenario='first_available'):
        self.config=deepcopy(config)
        self.scenario=scenario
        self.token=str(uuid4())
        self.orders={}
        self.submit_count=0
        self.poll_count=0
        self.lock=Lock()
        self.server=None
        self.thread=None
        self.started=time.monotonic()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_address[1]}'

    def start(self):
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def reply(self,data,status=200,html=False):
                raw=data.encode() if html else json.dumps(data,ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json')
                self.send_header('Cache-Control','no-store')
                self.send_header('Content-Length',str(len(raw)))
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'")
                self.end_headers()
                try: self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError): pass
            def do_GET(self):
                path=urlsplit(self.path).path
                if path=='/':
                    if owner.scenario=='maintenance' and time.monotonic()-owner.started<.6:
                        return self.reply('<html><body><h1>商店维护</h1></body></html>',503,True)
                    if owner.scenario=='network_timeout' and owner.poll_count==0:
                        owner.poll_count+=1
                        time.sleep(.3)
                    page=Path(__file__).with_name('sim_store.html').read_text(encoding='utf-8')
                    return self.reply(page,html=True)
                if path=='/api/catalog':
                    options=owner.config.product.options or {'颜色':'云白','容量':'256GB'}
                    choices={k:[v,'其他测试选项'] for k,v in options.items()}
                    if owner.scenario=='missing_option': choices={k:['不存在的选项'] for k in options}
                    return self.reply({'name':owner.config.product.name,'options':choices,'price':'100.00','token':owner.token})
                if path=='/api/stores':
                    owner.poll_count+=1
                    if owner.scenario=='rate_limited': return self.reply({'error':'rate limit'},429)
                    stores=owner.config.stores or [Store('模拟一店'),Store('模拟二店')]
                    return self.reply([{'id':s.store_id or f'sim-{i}','name':s.name,'city':s.city,
                      'available':False if owner.scenario in ('all_unavailable','stop') else (i>0 if owner.scenario=='second_available' else (owner.poll_count>2 if owner.scenario=='stock_changes' else True)),
                      'date':'2026-10-11'} for i,s in enumerate(stores)])
                if path=='/api/payments':
                    return self.reply(['测试银行卡'] if owner.scenario=='payment_unavailable' else owner.config.payments)
                if path=='/orders':
                    token=parse_qs(urlsplit(self.path).query).get('token',[''])[0]
                    order=owner.orders.get(token)
                    return self.reply({'order':order})
                self.reply({'error':'not found'},404)
            def do_POST(self):
                if urlsplit(self.path).path!='/api/order': return self.reply({'error':'not found'},404)
                # Strict loopback Origin plus unguessable token; no PII retained.
                if self.headers.get('Origin')!=owner.url: return self.reply({'error':'origin'},403)
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=10000: return self.reply({'error':'size'},400)
                payload=json.loads(self.rfile.read(length))
                if payload.get('token')!=owner.token: return self.reply({'error':'token'},403)
                if not str(payload.get('identity',{}).get('document_number','')).startswith('TEST-'):
                    return self.reply({'error':'only synthetic identities'},400)
                with owner.lock:
                    owner.submit_count+=1
                    if owner.scenario=='order_failure': return self.reply({'error':'模拟订单被服务器拒绝'},409)
                    if owner.scenario!='submit_timeout':
                        owner.orders.setdefault(owner.token,{'order_id':'SIM-'+owner.token[:8],'status':'payment_pending','deadline':None})
                if owner.scenario in ('submit_timeout','delayed_success'): time.sleep(.8)
                if owner.scenario=='submit_timeout': return self.reply({'error':'unknown'},504)
                return self.reply({'order':owner.orders[owner.token]})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.thread=Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        return self

    def close(self):
        if self.server:
            self.server.shutdown();self.server.server_close()
            self.thread.join(timeout=2)
            self.server=None

    def __enter__(self): return self.start()
    def __exit__(self,*_): self.close()

def local_config(scenario='first_available'):
    config=Settings()
    config.product.name='模拟 Phone / 可改为任意商品'
    config.product.url='http://127.0.0.1'
    config.product.options={'颜色':'云白','容量':'256GB'}
    config.product.max_total='300.00'
    config.stores=[Store('模拟一店',store_id='sim-0'),Store('模拟二店',store_id='sim-1')]
    config.scenario=scenario
    config.no_stock='stop' if scenario=='all_unavailable' else 'wait'
    config.interval=.1
    config.max_interval=1
    config.max_wait=3
    config.operation_timeout=.2
    config.reconcile_timeout=1.4
    return config
