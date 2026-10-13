"""Encrypted app-owned inputs, restored without native-vault prompts."""
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from app.models.flow import FlowError
from app.security.vault import CredentialVault, IdentityVault


class LocalInputs:
    TYPES = {'account': CredentialVault, 'profile': IdentityVault}

    def __init__(self, root):
        self.root = Path(root) / 'private-inputs'
        self.path = self.root / 'records.enc'
        self.key_path = self.root / 'local.key'
        self.records = {}
        self.readable = True

    @staticmethod
    def _read(path, limit):
        if path.is_symlink() or path.stat().st_size > limit:
            raise ValueError('Invalid private file')
        path.chmod(0o600)
        return path.read_bytes()

    def restore(self, redactor):
        if not self.path.exists():
            return
        try:
            if self.root.is_symlink():
                raise ValueError('Invalid private directory')
            self.root.chmod(0o700)
            key = self._read(self.key_path, 128)
            raw = json.loads(Fernet(key).decrypt(self._read(self.path, 1024 * 1024)))
            if not isinstance(raw, dict) or raw.get('version') != 1 or not isinstance(raw.get('records'), dict):
                raise ValueError('Invalid private records')
            restored = []
            for reference, entry in raw['records'].items():
                vault, record = self._validated(reference, entry)
                restored.append((vault, reference, record))
            self.records = raw['records']
            for vault, reference, record in restored:
                vault.remember(reference, record, temporary=False)
                redactor.register(record)
        except (OSError, ValueError, TypeError, KeyError, InvalidToken):
            self.readable = False
            raise FlowError('本机保存的资料未能载入，原文件已保留；可先直接填写本次资料') from None

    @classmethod
    def _validated(cls, reference, entry):
        if not isinstance(reference, str) or str(UUID(reference)) != reference:
            raise ValueError('Invalid record reference')
        if not isinstance(entry, dict) or not isinstance(entry.get('record'), dict):
            raise ValueError('Invalid private record')
        vault = cls.TYPES[entry['kind']]
        if not all(isinstance(value, str) for value in entry['record'].values()):
            raise ValueError('Invalid private fields')
        record = vault.record_type(**entry['record'])
        record.validate()
        if entry['kind'] == 'profile':
            source = entry.get('source_id', '')
            if (not isinstance(entry.get('alias'), str) or not entry['alias'].strip()
                    or not isinstance(source, str) or (source and str(UUID(source)) != source)):
                raise ValueError('Invalid profile metadata')
        return vault, record

    def has(self, reference):
        return reference in self.records

    def profiles(self):
        return {reference: {'id': reference, 'alias': entry['alias'],
                            'source_id': entry.get('source_id', '')}
                for reference, entry in self.records.items() if entry['kind'] == 'profile'}

    @staticmethod
    def _atomic_write(path, data):
        descriptor, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                os.chmod(name, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _save(self, records):
        if not self.readable:
            raise FlowError('原本机资料暂不可读；请取消“记住在这台电脑上”后使用本次输入')
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.root.is_symlink():
                raise ValueError('Invalid private directory')
            self.root.chmod(0o700)
            if not self.key_path.exists():
                self._atomic_write(self.key_path, Fernet.generate_key())
            key = self._read(self.key_path, 128)
            payload = json.dumps({'version': 1, 'records': records}, ensure_ascii=False).encode()
            encrypted = Fernet(key).encrypt(payload)
            if len(encrypted) > 1024 * 1024:
                raise ValueError('Private records too large')
            self._atomic_write(self.path, encrypted)
        except (OSError, ValueError):
            raise FlowError('本机资料保存失败，请检查软件数据目录；输入未写入日志') from None
        self.records = records

    def put(self, kind, reference, record, *, alias='', source_id=''):
        record.validate()
        records = deepcopy(self.records)
        records[reference] = {'kind': kind, 'record': asdict(record)}
        if kind == 'profile':
            records[reference].update(alias=alias, source_id=source_id)
        self._validated(reference, records[reference])
        self._save(records)

    def remove(self, reference):
        if reference in self.records:
            records = deepcopy(self.records)
            entry = records.pop(reference)
            self._save(records)
            self.TYPES[entry['kind']].forget(reference)
