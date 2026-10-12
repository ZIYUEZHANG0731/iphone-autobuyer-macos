"""Default automatic Apple China checkout privacy choice requested by the user.

Recognize the two privacy choices, without gating on paragraph/version text.
Version metadata remains readable for existing saved configurations.
"""
import hashlib
import re


PRIVACY_POLICY_URL = 'https://www.apple.com.cn/legal/privacy/'
NOTICE_TITLE = 'Apple 和你的数据隐私'
NOTICE_INTRO = (
    '无论数据在何处进行处理和存储，Apple 始终遵守 Apple 隐私政策，并仅会收集开展服务运营和改进用户体验所必要的数据。',
    '遵照中国大陆隐私保护法律的要求，需要你签署本知情同意书。',
)
NOTICE_CHOICES = (
    '我同意 Apple 的隐私政策',
    '我了解，当我使用我的账号或访客结账时，有限的个人信息可能会在中国境外存储和处理。',
)
NOTICE_MORE = '我们提供包括 Apple 隐私政策在内的更多信息。进一步了解。'


def canonical_notice(text):
    # Apple uses both ordinary commas and Chinese commas, plus its icon font
    # for external links. These are presentation differences, not new terms.
    # Keep every other character: a new sentence/choice must still stop here.
    def presentation(value):
        value=re.sub(r'[\s↗\uf32d\u200b\ufeff]', '', value.replace(',', '，'))
        return re.sub(r'[（(]在新窗口中打开[）)]','',value)
    text=presentation(text)
    # The live “进一步了解” link includes an accessibility-only destination
    # label in textContent. Normalize this complete observed footer only.
    text=text.replace(presentation(NOTICE_MORE).replace('进一步了解。','进一步了解Apple隐私政策。'),
                      presentation(NOTICE_MORE))
    # The first preset transcribed this one observed clause as 必需. The live
    # Apple notice says 必要. Recognize only these two complete equivalent
    # paragraphs, keeping the saved version valid; all other wording is exact.
    current=presentation(NOTICE_INTRO[0])
    return text.replace(current,current.replace('所必要的数据','所必需的数据'))


NOTICE_TEXT = '\n'.join((NOTICE_TITLE, *NOTICE_INTRO, *NOTICE_CHOICES, NOTICE_MORE))
NOTICE_VERSION = hashlib.sha256(canonical_notice(NOTICE_TEXT).encode()).hexdigest()


def matches_saved_notice(snapshot, config):
    if not config or not (config.privacy_policy_accepted and config.privacy_transfer_acknowledged):
        return False
    choices = snapshot.get('choices', [])
    return (len(choices) == 2
            and all(canonical_notice(actual) == canonical_notice(expected)
                    for actual, expected in zip(choices, NOTICE_CHOICES)))


def consent_reason(snapshot, config, *, used=False):
    if not config or not (config.privacy_policy_accepted and config.privacy_transfer_acknowledged):
        return 'Apple 隐私自动同意已关闭，请在付款与授权中重新启用，或在官网处理后继续'
    if used:return '本次确认已尝试，官网仍显示声明；请核对页面，不重复提交'
    if not snapshot or not snapshot.get('notice_text'):return '官网声明暂未完整读取，已保留页面'
    if not matches_saved_notice(snapshot, config):return '官网隐私选项结构无法确认，页面已保留；请查看官网提示'
    return '已识别提前同意的声明，正在确认官网选项'
