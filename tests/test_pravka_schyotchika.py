"""Счётчик можно поправить, а номер — прочитать с фотографии.

Заказчик завёл ХВС домовой без названия и номера, прислал фото и ждал, что
Люся сама всё срисует. Не срисовала, и поправить было нечем: поля для
заводского номера у счётчика вообще не было.
"""
import pytest

from bot import db, houses
from bot import handlers as H
from bot.transcribe import parse_meter_answer


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(H, 'DOCS_DIR', str(tmp_path / 'docs'))
    db.init()


@pytest.fixture
def schyotchik():
    d = next(h for h in houses.ALL_HOUSES if h['address'] == 'Седова 71')
    return db.add_meter(d['id'], 'hvs', 'ХВС', 'Андрей')


def test_nazvanie_menyaetsya(schyotchik):
    db.update_meter(schyotchik, label='Подвал, ввод ХВС на дом')

    assert db.get_meter(schyotchik)['label'] == 'Подвал, ввод ХВС на дом'


def test_zavodskoy_nomer_dobavlyaetsya(schyotchik):
    """Поля для номера раньше не было вовсе."""
    db.update_meter(schyotchik, serial='04517')

    assert db.get_meter(schyotchik)['serial'] == '04517'


def test_nomer_mozhno_ochistit(schyotchik):
    db.update_meter(schyotchik, serial='04517')
    db.update_meter(schyotchik, serial=None)

    assert db.get_meter(schyotchik)['serial'] is None


def test_pokazaniya_ne_teryayutsya_pri_pravke(schyotchik):
    db.add_reading(schyotchik, 1234, '2026-08', 1, 'Андрей')

    db.update_meter(schyotchik, label='новое имя', serial='04517')

    assert db.meter_readings(schyotchik)[0]['value'] == 1234


def test_foto_privyazyvaetsya_k_schyotchiku(schyotchik):
    db.update_meter(schyotchik, photo='/data/docs/meters/1.jpg')

    assert db.get_meter(schyotchik)['photo'] == '/data/docs/meters/1.jpg'


@pytest.mark.parametrize('otvet, ozhidaem', [
    ('{"serial": "04517", "value": 1234.5}', {'serial': '04517', 'value': 1234.5}),
    ('```json\n{"serial": "04517", "value": "1234,5"}\n```',
     {'serial': '04517', 'value': 1234.5}),
    ('{"serial": null, "value": 1234}', {'serial': None, 'value': 1234.0}),
    ('{"serial": "04517", "value": null}', {'serial': '04517', 'value': None}),
])
def test_otvet_modeli_razbiraetsya(otvet, ozhidaem):
    assert parse_meter_answer(otvet) == ozhidaem


@pytest.mark.parametrize('pusto', [
    '{"serial": null, "value": null}',
    'не могу разобрать',
    '',
    '{сломанный json',
])
def test_nerazobrannoe_foto_ne_pridumyvaetsya(pusto):
    """Лучше признаться, что не видно, чем записать выдуманный номер."""
    assert parse_meter_answer(pusto) is None


def test_zapros_k_modeli_trebuet_ne_ugadyvat():
    from bot.transcribe import METER_PROMPT

    assert 'не угадывай' in METER_PROMPT
    assert 'null' in METER_PROMPT


def test_moduli_importiruyutsya_v_lyubom_poryadke():
    """numbers и houses обращались друг к другу при импорте — замкнутый круг."""
    import subprocess
    import sys

    for modul in ('transcribe', 'numbers', 'houses', 'handlers'):
        r = subprocess.run([sys.executable, '-c', f'from bot import {modul}'],
                           capture_output=True, text=True)
        assert r.returncode == 0, f'{modul} первым: {r.stderr[-400:]}'
