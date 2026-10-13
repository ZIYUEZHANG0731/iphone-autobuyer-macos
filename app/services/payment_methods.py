"""Payment preferences shared by the GUI and verified checkout summary."""
import re


PAYMENT_METHODS=('招商银行','支付宝','微信支付','花呗分期','微信分付',
                 '中国建设银行','中国工商银行','Apple Pay','信用卡','电汇')
FULL_PAYMENT_METHODS=frozenset(('支付宝','微信支付','Apple Pay','信用卡','电汇'))
PAYMENT_ALIASES={
    '招商银行':('招商银行','CMB'),
    '中国建设银行':('中国建设银行','建设银行','CCB'),
    '中国工商银行':('中国工商银行','工商银行','ICBC'),
    '支付宝':('支付宝','Alipay'),
    '微信支付':('微信支付','WeChat Pay'),
    '花呗分期':('花呗分期',),
    '微信分付':('微信分付',),
    'Apple Pay':('Apple Pay',),
    '信用卡':('信用卡',),
    '电汇':('电汇','银行转账'),
}


def normalized_payment(value):
    return re.sub(r'\s+','',value).casefold()


def payment_matches(method,label):
    return normalized_payment(label) in {
        normalized_payment(alias) for alias in PAYMENT_ALIASES.get(method,(method,))}


def installment_payment(method):
    return method not in FULL_PAYMENT_METHODS


def payment_months(method,months):
    # Alipay and Huabei are distinct checkout radios, as are WeChat and Fenfu.
    # A full-payment backup must not inherit the preferred bank's term.
    return months if installment_payment(method) else 0


def payment_plan(methods,months):
    return ' → '.join(method+(f' · {term} 期' if (term:=payment_months(method,months)) else '')
                      for method in methods)


def payment_app(method):
    return {'支付宝':'支付宝','花呗分期':'支付宝','微信支付':'微信','微信分付':'微信',
            '招商银行':'掌上生活'}.get(method,'')
