"""Страница состояния: понять, почему бот молчит, не открывая логи Railway."""
import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot import db, status, webapp


@pytest.fixture(autouse=True)
def chisto(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    status.STATE.update(bot_username=None, bot_id=None, me_error=None, polls=0,
                        last_error=None, last_error_at=None, updates=0,
                        last_update_at=None, last_update_kind=None)


def test_bez_tokena_pryamo_govorit_o_tokene():
    status.note_me(None, None, error=RuntimeError('403 Forbidden'))
    out = status.report('сборка', None, 'работает')
    assert 'НЕ ПРИНЯТ' in out
    assert '403 Forbidden' in out
    assert 'MAX_BOT_TOKEN' in out


def test_token_prinyat_no_soobscheniy_net_podozrenie_na_dvoynik():
    status.note_me('lusya_bot', 42)
    status.note_poll_start()
    out = status.report('сборка', None, 'работает')
    assert '@lusya_bot' in out
    assert 'НИ ОДНОГО' in out
    assert 'второй запущенный экземпляр' in out


def test_kogda_soobscheniya_idut_preduprezhdeniy_net():
    status.note_me('lusya_bot', 42)
    status.note_poll_start()
    status.note_update('личка')
    out = status.report('сборка', None, 'работает')
    assert '⚠️' not in out
    assert 'личка' in out


def test_oshibka_oprosa_vidna():
    status.note_me('lusya_bot', 42)
    status.note_poll_error(ConnectionError('связь оборвалась'))
    out = status.report('сборка', None, 'работает')
    assert 'ConnectionError: связь оборвалась' in out


async def test_stranica_otdayotsya_po_sekretnomu_puti(monkeypatch):
    monkeypatch.setenv('MINIAPP_PATH', 'tainiy-adres')
    c = TestClient(TestServer(webapp.create_app()))
    await c.start_server()
    try:
        r = await c.get('/tainiy-adres/status')
        assert r.status == 200
        assert 'Токен MAX' in await r.text()
        # вне секретного пути состояние наружу не выставлено
        assert (await c.get('/status')).status == 404
    finally:
        await c.close()
