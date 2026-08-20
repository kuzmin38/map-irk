"""Мини-приложение отдаётся самим ботом: данные, маршруты, секретный путь."""
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot import db, houses, webapp


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


async def test_bez_slesha_perekidyvaet_na_slesh(monkeypatch):
    c = await client(monkeypatch)
    try:
        r = await c.get('/tainiy-adres', allow_redirects=False)
        assert r.status == 302
        assert r.headers['Location'] == '/tainiy-adres/'
    finally:
        await c.close()


async def test_zhivye_dannye_doma_otdayutsya(monkeypatch):
    db.add_request(7, 'Байкальская 237', 'нет ГВС', 100, 'Андрей')
    db.add_work(7, 'Опрессовка', '2026-09-01', 'Константин')
    db.set_passport_field(7, 'floors', '9', 'Андрей')
    db.add_chat_record(1, 'm1', 100, 'Андрей', 'течёт в подвале',
                       house_id=7, is_issue=True)

    c = await client(monkeypatch)
    try:
        r = await c.get('/tainiy-adres/api/house/7')
        assert r.status == 200
        d = await r.json()
        assert [x['description'] for x in d['requests']] == ['нет ГВС']
        assert d['works'][0]['title'] == 'Опрессовка'
        assert d['passport'][0]['label'] == 'Этажность'
        assert d['chat'][0]['is_issue'] is True
    finally:
        await c.close()


async def test_dannye_chuzhogo_doma_ne_meshayutsya(monkeypatch):
    db.add_request(7, 'Байкальская 237', 'наша', 100, 'Андрей')
    db.add_request(8, 'Седова 65а', 'чужая', 100, 'Андрей')

    c = await client(monkeypatch)
    try:
        d = await (await c.get('/tainiy-adres/api/house/7')).json()
        assert [x['description'] for x in d['requests']] == ['наша']
    finally:
        await c.close()


async def test_api_bez_sekretnogo_puti_zakryt(monkeypatch):
    c = await client(monkeypatch)
    try:
        assert (await c.get('/api/house/7')).status == 404
        assert (await c.get('/miniapp/api/house/7')).status == 404
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


def test_kartochka_doma_soderzhit_svodku(tmp_path, monkeypatch):
    """Пустая карточка выглядела так, будто приложение ничего не умеет."""
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'svodka.db'))
    db.init()
    dom = houses.HOUSES[0]

    state = webapp.house_state(dom['id'])

    s = state['summary']
    assert s['requests_open'] == 0 and s['meters'] == 0
    assert s['passport_total'] == 12, 'знаменатель паспорта на месте'
    assert 'meters' in state


def test_schyotchiki_vidny_v_prilozhenii(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'sch.db'))
    db.init()
    dom = houses.HOUSES[0]
    m_id = db.add_meter(dom['id'], 'hvs', 'Подвал, ввод ХВС', 'Андрей')
    db.update_meter(m_id, serial='04517')
    from bot.handlers import current_period
    db.add_reading(m_id, 1234, current_period(), 1, 'Андрей')

    state = webapp.house_state(dom['id'])

    m = state['meters'][0]
    assert m['label'] == 'Подвал, ввод ХВС'
    assert m['serial'] == '04517'
    assert m['value'] == 1234 and m['done'] is True
    assert state['summary']['meters_done'] == 1


def test_spisok_domov_neset_chisla(tmp_path, monkeypatch):
    """Список адресов без чисел — просто портянка, по нему ничего не понять."""
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'stats.db'))
    db.init()
    dom = houses.HOUSES[0]
    db.add_meter(dom['id'], 'hvs', 'ХВС', 'Андрей')
    db.add_request(dom['id'], dom['address'], 'течь в подвале', 1, 'Андрей')

    payload = webapp.build_payload()

    nash = next(h for h in payload['houses'] if h['id'] == dom['id'])
    assert nash['meters'] == 1
    assert nash['meters_done'] == 0
    assert nash['requests_open'] == 1


def test_chisla_po_domam_ne_ronyayut_stranicu(tmp_path, monkeypatch):
    """Если сбор чисел упал, приложение всё равно должно открыться."""
    monkeypatch.setattr(webapp.db, 'readings_for_period',
                        lambda period: (_ for _ in ()).throw(RuntimeError('база занята')))

    assert webapp.house_stats() == {}


def test_plitki_svodki_vedut_v_razdely():
    """Плитка «Счётчики» должна прыгать к разделу, а не быть надписью."""
    import re

    with open('miniapp/template.html', encoding='utf-8') as f:
        html = f.read()

    assert 'function jump(' in html
    assert 'onclick="jump(' in html
    for razdel in ('requests', 'works', 'meters', 'devices', 'passport', 'docs'):
        assert f"'{razdel}');" in html, f'нет якоря раздела {razdel}'


def test_razdely_risuyutsya_dazhe_pustymi():
    """Иначе прыгать с плитки «0 счётчиков» некуда."""
    with open('miniapp/template.html', encoding='utf-8') as f:
        html = f.read()

    assert 'function empty(' in html
    assert 'Счётчики не заведены' in html
    assert 'Паспорт не заполнен' in html
