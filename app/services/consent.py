"""The Apple China checkout notice shown by the user on 2026-10-12.

Saved choices cover these visible terms only. Whitespace and external-link
glyphs may vary in the DOM; any other visible wording change needs a new choice.
"""
import hashlib
import re


PRIVACY_POLICY_URL = 'https://www.apple.com.cn/legal/privacy/'
NOTICE_TITLE = 'Apple 和你的数据隐私'
NOTICE_INTRO = (
    '无论数据在何处进行处理和存储，Apple 始终遵守 Apple 隐私政策，并仅会收集开展服务运营和改进用户体验所必需的数据。',
    '遵照中国大陆隐私保护法律的要求，需要你签署本知情同意书。',
)
NOTICE_CHOICES = (
    '我同意 Apple 的隐私政策',
    '我了解，当我使用我的账号或访客结账时，有限的个人信息可能会在中国境外存储和处理。',
)
NOTICE_MORE = '我们提供包括 Apple 隐私政策在内的更多信息。进一步了解。'


def canonical_notice(text):
    return re.sub(r'[\s↗]', '', text)


NOTICE_TEXT = '\n'.join((NOTICE_TITLE, *NOTICE_INTRO, *NOTICE_CHOICES, NOTICE_MORE))
NOTICE_VERSION = hashlib.sha256(canonical_notice(NOTICE_TEXT).encode()).hexdigest()


def matches_saved_notice(snapshot, config):
    if not config or not (config.privacy_policy_accepted and config.privacy_transfer_acknowledged):
        return False
    if config.privacy_notice_version != NOTICE_VERSION:
        return False
    choices = snapshot.get('choices', [])
    return (len(choices) == 2
            and all(canonical_notice(actual) == canonical_notice(expected)
                    for actual, expected in zip(choices, NOTICE_CHOICES))
            and canonical_notice(snapshot.get('notice_text', '')) == canonical_notice(NOTICE_TEXT))
