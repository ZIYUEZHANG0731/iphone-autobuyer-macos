"""Read a user-selected local TXT into form fields, never into settings/logs."""
from pathlib import Path
import re

from app.models.config import ConfigError


FIELD_NAMES = {
    '账号': 'account', 'Apple账号': 'account', 'account': 'account',
    '密码': 'password', 'password': 'password',
    '姓氏': 'family_name', 'family_name': 'family_name',
    '名字': 'given_name', 'given_name': 'given_name',
    '证件后四位': 'document_number', 'document_number': 'document_number',
    '手机号': 'phone', '手机号码': 'phone', 'phone': 'phone',
    '邮箱': 'email', '电子邮箱': 'email', 'email': 'email',
    '别名': 'alias', 'alias': 'alias',
}


def read_private_text(path):
    try:
        with Path(path).open('rb') as source:
            raw = source.read(16385)
        if len(raw) > 16384:
            raise ConfigError('TXT 文件过大，请只保留需要填写的字段')
        text = raw.decode('utf-8-sig')
    except (OSError, UnicodeError):
        raise ConfigError('无法读取 TXT，请选择本机 UTF-8 文本文件') from None
    fields = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'\s*([^=：:]+?)\s*[=：:](.*)', line)
        key = FIELD_NAMES.get(match[1].strip()) if match else None
        if not key or key in fields:
            raise ConfigError('TXT 字段格式不正确或重复，请按“字段=内容”填写')
        # Password spaces and punctuation are part of the credential.
        fields[key] = match[2] if key == 'password' else match[2].strip()
    if not fields:
        raise ConfigError('TXT 没有可填写的字段')
    return fields
