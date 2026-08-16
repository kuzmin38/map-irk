"""Мини-приложение отдаётся самим ботом: данные, маршруты, секретный путь."""
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot import db, webapp


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    webapp._cache.update(html=None, at=0.0)


async def client(monkeypatch, path='tainiy-adres'):
    monkeypatch.setenv('MINIAPP_PATH', path)
    c = TestClient(TestServer(webapp.create_app()))
    await c.start_server()
    return c


def test_payload_soderzhit_spravochniki():
    p = webapp.build_payload()
    assert p['houses'], 'дома должны попасть в приложение'
    assert p['complexes'], 'ЖК должны попасть в приложение'
    assert 'risers' in p and 'docs' in p and 'directory' in p


def test_privyazka_k_zhk_beryotsya_iz_bazy():
    house_id = webapp.build_payload()['houses'][0]['id']
    db.set_house_complex(house_id, 'zhemchuzhina')

    houses = {h['id']: h for h in webapp.build_payload()['houses']}
    assert houses[house_id]['complex'] == 'zhemchuzhina'


def test_zveno_ne_utekaet_v_prilozhenie():
    assert all('zveno' not in h for h in webapp.build_payload()['houses'])


def test_stranica_sobirayetsya_s_dannymi():
    html = webapp.render()
    assert webapp.MARKER not in html, 'маркер должен быть заменён данными'
    assert '"houses"' in html


def test_kesh_ne_peresobiraet_strranicu(monkeypatch):
    monkeypatch.setenv('MINIAPP_TTL', '600')
    first = webapp.render()
    calls = []
    monkeypatch.setattr(webapp, 'build_payload', lambda: calls.append(1) or {})
    assert webapp.render() is first
    assert not calls, 'внутри TTL данные заново не собираются'


async def test_prilozhenie_otdayotsya_po_sekretnomu_puti(monkeypatch):
    c = await client(monkeypatch)
    try:
        r = await c.get('/tainiy-adres/')
        assert r.status == 200
        assert r.headers['X-Robots-Tag'].startswith('noindex')
        assert r.headers['Cache-Control'] == 'no-store'
        assert '"houses"' in await r.text()
    finally:
        await c.close()


async def test_bez_sekretnogo_puti_nichego_net(monkeypatch):
    c = await client(monkeypatch)
    try:
        for url in ('/', '/miniapp/', '/index.html', '/bot/data/risers.json'):
            assert (await c.get(url)).status == 404, url
    finally:
        await c.close()


async def test_healthz_dlya_proverki_zhiv_li(monkeypatch):
    c = await client(monkeypatch)
    try:
        r = await c.get('/healthz')
        assert r.status == 200 and (await r.text()).startswith('ok')
    finally:
        await c.close()


def test_publichnyy_adres_iz_domena_railway(monkeypatch):
    monkeypatch.setenv('MINIAPP_PATH', 'abc123')
    monkeypatch.delenv('MINIAPP_HOST', raising=False)
    monkeypatch.setenv('RAILWAY_PUBLIC_DOMAIN', 'lusya.up.railway.app')
    assert webapp.public_url() == 'https://lusya.up.railway.app/abc123/'


def test_bez_domena_adres_neizvesten(monkeypatch):
    monkeypatch.delenv('MINIAPP_HOST', raising=False)
    monkeypatch.delenv('RAILWAY_PUBLIC_DOMAIN', raising=False)
    assert webapp.public_url() is None
