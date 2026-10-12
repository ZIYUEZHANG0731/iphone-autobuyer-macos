from types import SimpleNamespace
from uuid import uuid4
from app.models.config import Settings
from app.models.credential import AppleCredential
from app.security.vault import CredentialVault,IdentityVault,settings_for_disk


def test_session_credentials_are_copy_isolated_and_not_persisted_or_unlocked(monkeypatch):
    import app.security.vault as module
    monkeypatch.setattr(module,'credential_backend',lambda:(_ for _ in ()).throw(AssertionError('No native access')))
    reference=str(uuid4());record=AppleCredential('format@example.invalid','Synthetic-secret')
    CredentialVault.remember(reference,record)
    try:
        record.password='changed'
        got=CredentialVault().get(reference);assert got.password=='Synthetic-secret'
        got.password='mutated-copy';assert CredentialVault().get(reference).password=='Synthetic-secret'
        config=Settings(credential_id=reference,auto_login=True,trust_browser=True)
        assert config.credential_id==reference
        assert not settings_for_disk(config)['credential_id']
        assert not settings_for_disk(config)['auto_login']
        assert IdentityVault.cached(reference) is None
        CredentialVault().delete(reference)
        assert not CredentialVault.is_temporary(reference) and CredentialVault.cached(reference) is None
    finally:CredentialVault.forget(reference)


def test_native_records_are_read_once_per_application_session():
    reference=str(uuid4());reads=[]
    vault=CredentialVault()
    vault.backend=SimpleNamespace(get_password=lambda service,key:reads.append(key) or
        '{"account":"format@example.invalid","password":"Synthetic-secret"}')
    try:
        assert vault.get(reference).account=='format@example.invalid'
        other=CredentialVault();other.backend=SimpleNamespace(get_password=lambda *_:(_ for _ in ()).throw(AssertionError('No second read')))
        assert other.get(reference).password=='Synthetic-secret'
        assert reads==[reference] and not vault.is_temporary(reference)
    finally:vault.forget(reference)


def test_session_override_preserves_the_previous_persistent_account_for_restart():
    previous={'credential_id':str(uuid4()),'auto_login':True,'trust_browser':False}
    reference=str(uuid4())
    CredentialVault.remember(reference,AppleCredential('format@example.invalid','Synthetic-secret'),fallback=previous)
    try:
        raw=settings_for_disk(Settings(credential_id=reference,auto_login=True,trust_browser=True))
        assert {k:raw[k] for k in previous}==previous
    finally:CredentialVault.forget(reference)
