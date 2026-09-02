"""Голос в обход MAX: своя страница с кнопкой записи.

MAX не отдаёт ботам голосовые в личке — ни в уведомлении, ни по прямому
запросу. Это ограничение платформы. Поэтому голос идёт мимо: бот и так
поднимает свой веб-сервер, к нему добавлена страница с одной кнопкой.
"""
import types

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot import db, golos, webapp
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()
    webapp._cache.update(html=None, at=0.0)


async def klient(monkeypatch, path='tayna'):
    monkeypatch.setenv('MINIAPP_PATH', path)
    c = TestClient(TestServer(webapp.create_app()))
    await c.start_server()
    return c


# ---------- Личная ссылка ----------

def test_ssylka_u_kazhdogo_svoya():
    db.upsert_user(100, 'Андрей')
    db.upsert_user(200, 'Костя')

    assert db.issue_token(100) != db.issue_token(200)


def test_ssylka_ne_menyaetsya():
    assert db.issue_token(100) == db.issue_token(100)


def test_po_tokenu_uznayotsya_chelovek():
    token = db.issue_token(100)

    assert db.token_user(token) == 100
    assert db.token_user('чужое') is None


# ---------- Страница ----------

def test_stranitsa_zovyot_po_imeni():
    html = golos.stranitsa('Андрей')

    assert 'Андрей' in html
    assert 'Говорить' in html
    assert 'MediaRecorder' in html, 'запись делает браузер'


async def test_chuzhaya_ssylka_ne_otkryvaetsya(monkeypatch):
    client = await klient(monkeypatch)

    otvet = await client.get('/tayna/golos/подобрал/')

    assert otvet.status == 404


async def test_svoya_ssylka_otdayot_stranitsu(monkeypatch):
    db.upsert_user(100, 'Андрей')
    token = db.issue_token(100)
    client = await klient(monkeypatch)

    otvet = await client.get(f'/tayna/golos/{token}/')

    assert otvet.status == 200
    assert 'Андрей' in await otvet.text()


# ---------- Запись ----------

async def test_zapis_rasshifrovyvaetsya_i_obrabatyvaetsya(monkeypatch):
    db.upsert_user(100, 'Андрей')
    token = db.issue_token(100)

    async def fake_rasshifrovat(data, mime):
        assert data == b'zvuk'
        return 'перекрыл стояк по 105 квартире на 65а/3'

    monkeypatch.setattr(golos, 'rasshifrovat', fake_rasshifrovat)
    client = await klient(monkeypatch)

    otvet = await client.post(f'/tayna/golos/{token}/golos', data=b'zvuk')
    data = await otvet.json()

    assert data['text'] == 'перекрыл стояк по 105 квартире на 65а/3'
    assert 'Перекрыт стояк' in data['reply'], 'сказанное пошло обычным путём'
    assert db.open_shutoffs(), 'и записалось'


async def test_pustaya_zapis_ne_lomaet(monkeypatch):
    token = db.issue_token(100)
    client = await klient(monkeypatch)

    otvet = await client.post(f'/tayna/golos/{token}/golos', data=b'')

    assert (await otvet.json())['error']


async def test_nerazobrannaya_rech_govorit_pryamo(monkeypatch):
    token = db.issue_token(100)

    async def fake_rasshifrovat(data, mime):
        return None

    monkeypatch.setattr(golos, 'rasshifrovat', fake_rasshifrovat)
    client = await klient(monkeypatch)

    otvet = await client.post(f'/tayna/golos/{token}/golos', data=b'shum')

    assert 'Не разобрала' in (await otvet.json())['error']


async def test_otvet_dubliruetsya_v_max(monkeypatch):
    db.upsert_user(100, 'Андрей')
    ushlo = []

    class FakeBot:
        async def send_message(self, user_id=None, text=None, attachments=None):
            ushlo.append((user_id, text))

    otvet = await golos.obrabotat(100, 'привет', FakeBot())

    assert ushlo and ushlo[0][0] == 100, 'ответ пришёл и в мессенджер'
    assert otvet


# ---------- Команда ----------

def test_komanda_golos_est_v_menyu():
    payload = next((p for name, _, p in H.QUICK_COMMANDS if name == 'голос'), None)

    assert payload == 'golos'
