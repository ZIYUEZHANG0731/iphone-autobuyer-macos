import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.gui.window import ProfileDialog
from app.models.config import Identity
from app.utils.qt import prepare_plugins


@pytest.fixture(scope='module')
def qt():
    prepare_plugins()
    application = QApplication.instance() or QApplication(['pickup-profile-test'])
    yield application


def fill_contact(dialog, number):
    # Format-only fixture: no actual account, Keychain write or website request.
    for key, value in {
        'alias': '格式测试', 'family_name': '测', 'given_name': '试',
        'document_number': number, 'phone': '00000000000', 'email': 'format@example.com',
    }.items():
        dialog.fields[key].setText(value)


@pytest.mark.parametrize('number', ['1234', '123X', '123x'])
def test_new_pickup_profile_accepts_last_four_without_typing_a_document_type(qt, monkeypatch, number):
    messages = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: messages.append(args[-1]))
    dialog = ProfileDialog()
    try:
        fill_contact(dialog, number)
        dialog.accept_checked()
        assert not messages
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.identity().document_type == '居民身份证后四位'
        assert dialog.identity().document_number == number.upper()
        assert 'document_type' not in dialog.fields
        assert dialog.fields['document_number'].accessibleName() == '证件后四位'
    finally:
        dialog.close(); dialog.deleteLater(); qt.processEvents()


@pytest.mark.parametrize('number', ['123', '12345', '123A', '12X4', 'XXXX', '123456789012345678'])
def test_pickup_profile_rejects_wrong_length_or_character_without_truncating(qt, monkeypatch, number):
    messages = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: messages.append(args[-1]))
    dialog = ProfileDialog()
    try:
        fill_contact(dialog, number)
        dialog.accept_checked()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert messages == ['请填写证件后四位：四位数字，身份证末位为 X 时也可填写 X']
        assert dialog.fields['document_number'].text() == number
    finally:
        dialog.close(); dialog.deleteLater(); qt.processEvents()


def test_editing_legacy_resident_profile_only_saves_last_four_after_confirmation(qt):
    identity = Identity('格式测试', '居民身份证', '11010119900101123X',
                        '00000000000', 'format@example.com', '格', '式测试')
    dialog = ProfileDialog(identity=identity, alias='格式测试')
    try:
        assert dialog.fields['document_number'].text() == '123X'
        updated = dialog.identity()
        updated.validate()
        assert updated.document_type == '居民身份证后四位'
        assert updated.document_number == '123X'
        assert identity.document_number == '11010119900101123X'
        assert identity.document_type == '居民身份证'
    finally:
        dialog.close(); dialog.deleteLater(); qt.processEvents()


def test_editing_nonresident_profile_preserves_document_kind_and_only_keeps_last_four(qt):
    identity = Identity('格式测试', '护照', 'E0000AB12',
                        '00000000000', 'format@example.com', '格', '式测试')
    dialog = ProfileDialog(identity=identity, alias='格式测试')
    try:
        updated = dialog.identity()
        updated.validate()
        assert updated.document_type == '护照后四位'
        assert updated.document_number == 'AB12'
        assert identity.document_number == 'E0000AB12'
    finally:
        dialog.close(); dialog.deleteLater(); qt.processEvents()
