from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace,asdict
from decimal import Decimal
import json
import pytest
from app.models.config import Settings,Product,Store,Mode,State,Identity,ConfigError,apple_url,money
from app.models.flow import Summary,FlowError,UserAction,StateMachine
from app.services.storage import Repository
from app.security.privacy import Redactor
from app.automation.control import ProcessLock

@pytest.mark.parametrize('url',['http://www.apple.com.cn/shop','https://apple.com.cn.evil.test','https://evilapple.com.cn','https://www.apple.com.cn@evil.test','https://user:pw@www.apple.com.cn/shop','https://www.apple.com.cn:8080/shop','file:///etc/passwd'])
def test_untrusted_destinations_rejected(url):assert not apple_url(url)
@pytest.mark.parametrize('url',['https://www.apple.com.cn/shop','https://secure6.www.apple.com.cn/shop/checkout'])
def test_official_origin_allowed(url):assert apple_url(url)
@pytest.mark.parametrize('value',['NaN','Infinity','-1','invalid'])
def test_invalid_money_rejected(value):
    with pytest.raises(ConfigError):money(value)

def config():return Settings(product=Product(name='Phone',url='https://www.apple.com.cn/shop/product/test',options={'color':'white'},max_total='300'),stores=[Store('Apple Test',store_id='R1')],payments=['支付宝'])
def summary():return Summary('Phone',{'color':'white'},1,Decimal('100'),'Apple Test','R1','支付宝',verified=True)
@pytest.mark.parametrize('changes',[{'product':'Other'},{'options':{'color':'black'}},{'quantity':2},{'total':Decimal('301')},{'total':Decimal('NaN')},{'total':Decimal('-1')},{'store_id':'R2'},{'payment':'Other'},{'currency':'USD'},{'fulfillment':'delivery'},{'verified':False}])
def test_mismatched_summary_never_authorized(changes):
    with pytest.raises(FlowError):replace(summary(),**changes).authorize(config())
def test_full_authorization_matches():summary().authorize(config())

def test_submission_claim_atomic_across_threads(tmp_path):
    path=tmp_path/'db.sqlite';Repository(path)
    def claim(_):return Repository(path).claim_submission('intent','run')
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(claim,range(20)))
    assert sum(results)==1
    repo=Repository(path);repo.record_submission('intent','UNKNOWN')
    assert not repo.claim_submission('intent','other-run')
    repo.record_submission('intent','CONFIRMED','order')
    assert not repo.claim_submission('intent','third-run')

def test_process_lock_prevents_parallel_sessions(tmp_path):
    with ProcessLock(tmp_path/'run.lock'):
        with pytest.raises(FlowError):
            with ProcessLock(tmp_path/'run.lock'):pass

def test_logs_strip_registered_identity_and_url_tokens(tmp_path):
    secret=Identity('私密测试姓名','护照','E987654321','13800138000','private@example.com')
    r=Redactor();r.register(secret);repo=Repository(tmp_path/'db.sqlite',r)
    message=' '.join(asdict(secret).values())+' https://www.apple.com.cn/shop?token=TOP_SECRET'
    repo.update_run('run','website_dry_run','PAUSED',message)
    output=tmp_path/'logs.json';repo.export_logs(output);text=output.read_text()
    for value in [secret.name,secret.document_number,secret.phone,secret.email,'TOP_SECRET']:assert value not in text
    assert '[已脱敏]' in text

def test_config_never_accepts_identity_payload():
    with pytest.raises(ConfigError):Settings.from_dict({'document_number':'123'})

def test_order_state_cannot_jump_to_submission():
    machine=StateMachine()
    with pytest.raises(FlowError):machine.move(State.ORDER_SUBMITTING)
    machine.move(State.PREPARING);machine.move(State.PAUSED);machine.move(State.PREPARING)
    assert machine.state==State.PREPARING

def test_simulated_identity_not_accepted_for_apple():
    with pytest.raises(ConfigError):Identity.synthetic().validate()

@pytest.mark.parametrize('raw',[
    {'interval':'5'},{'interval':float('nan')},{'max_wait':True},
    {'display_timezone':'No/SuchPlace'},{'payments':'支付宝'},
    {'installment_months':24.0},{'installment_months':True},{'installment_months':61},
])
def test_malformed_config_cannot_run(raw):
    with pytest.raises(ConfigError):Settings.from_dict(raw)

def test_last_four_only_identity_supported_without_full_identifier():
    i=Identity('本地格式检查','居民身份证后四位','123X','00000000000','test@example.com')
    i.validate()
    i.document_number='123'
    with pytest.raises(ConfigError):i.validate()

def test_summary_rejects_unexpected_installment():
    cfg=config();cfg.payments=['招商银行'];cfg.installment_months=24
    s=Summary(cfg.product.name,dict(cfg.product.options),1,Decimal('100'),cfg.stores[0].name,cfg.stores[0].store_id,payment='招商银行',verified=True,installment_months=12)
    with pytest.raises(FlowError):s.authorize(cfg)
