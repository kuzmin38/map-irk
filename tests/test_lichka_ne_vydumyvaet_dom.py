"""В личке свободный текст с номером дома не должен уходить к ИИ «вслепую».

Реальный случай: Люся получила в личку отчёт обхода «Подмес 28 - 123 ...»
и ответила, что дома с номером 28 у неё в списке нет — не вызвав ни одного
инструмента (см. tests/test_podmes_zagolovok.py про сам разбор номера).
Здесь проверяем сквозной путь on_text: раз houses.search() ничего не
нашёл, а houses.detect_house() нашёл дом по номеру, агент должен получить
это как готовый факт, а не гадать сам.
"""
import types

import pytest

from bot import db
import bot.handlers as H

REAL_TEXT = (
    'Подмес 28 - 123\n\n'
    '22 сентября 8:15\n'
    '123 - нет дома\n'
    '105 - обнаружен незакрытый гиг душ.'
)


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


class Msg:
    def __init__(self, text):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=100, chat_id=None, chat_type='dialog')
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


def event(text):
    e = types.SimpleNamespace()
    e.message = Msg(text)
    e.bot = None
    return e


async def test_agent_poluchaet_uzhe_naydennyy_dom(monkeypatch):
    poluchennoe = {}

    async def fake_answer(uid, name, text, chat_id=None):
        poluchennoe['text'] = text
        return 'ответ'

    monkeypatch.setattr(H.agent, 'answer', fake_answer)

    await H.on_text(event(REAL_TEXT))

    assert '4-я Советская 28' in poluchennoe['text']
    assert 'find_house' in poluchennoe['text']
    assert REAL_TEXT in poluchennoe['text']


async def test_bez_nomera_tekst_uhodit_kak_est(monkeypatch):
    poluchennoe = {}

    async def fake_answer(uid, name, text, chat_id=None):
        poluchennoe['text'] = text
        return 'ответ'

    monkeypatch.setattr(H.agent, 'answer', fake_answer)

    await H.on_text(event('какие нормативы по срокам устранения течи?'))

    assert poluchennoe['text'] == 'какие нормативы по срокам устранения течи?'
