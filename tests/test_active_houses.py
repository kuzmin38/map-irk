"""Ограничение списка домов файлом active_houses.txt."""
import importlib

import pytest

import bot.houses as H


def reload_with(tmp_path, monkeypatch, content):
    """Перечитывает модуль домов с подставленным файлом активных адресов."""
    path = tmp_path / 'active_houses.txt'
    if content is not None:
        path.write_text(content, encoding='utf-8')
    monkeypatch.setattr(H, 'ACTIVE_FILE', str(path))
    mod = importlib.reload(H)
    mod.ACTIVE_FILE = str(path)
    mod.ACTIVE = mod.load_active()
    mod.HOUSES = ([h for h in mod.ALL_HOUSES if mod._norm_addr(h['address']) in mod.ACTIVE]
                  if mod.ACTIVE else list(mod.ALL_HOUSES))
    mod.HOUSES_BY_ID = {h['id']: h for h in mod.HOUSES}
    return mod


@pytest.fixture(autouse=True)
def restore():
    yield
    importlib.reload(H)


def test_bez_fayla_vidny_vse_doma(tmp_path, monkeypatch):
    mod = reload_with(tmp_path, monkeypatch, None)
    assert len(mod.HOUSES) == len(mod.ALL_HOUSES)


def test_pustoy_fayl_nichego_ne_ogranichivaet(tmp_path, monkeypatch):
    mod = reload_with(tmp_path, monkeypatch, '# только комментарии\n\n')
    assert len(mod.HOUSES) == len(mod.ALL_HOUSES)


def test_ostayutsya_tolko_perechislennye(tmp_path, monkeypatch):
    mod = reload_with(tmp_path, monkeypatch,
                      'Седова 65а/2\nСедова 65а/3   # БС3\nТрилиссера 8/1\n')
    assert sorted(h['address'] for h in mod.HOUSES) == [
        'Седова 65а/2', 'Седова 65а/3', 'Трилиссера 8/1']


def test_regist_i_yo_ne_meshayut(tmp_path, monkeypatch):
    mod = reload_with(tmp_path, monkeypatch, 'ул. СЕДОВА 65А/2\n')
    assert [h['address'] for h in mod.HOUSES] == ['Седова 65а/2']


def test_poisk_ne_nakhodit_otklyuchennye(tmp_path, monkeypatch):
    mod = reload_with(tmp_path, monkeypatch, 'Седова 65а/2\n')
    assert mod.search('Байкальская 237') == []
    assert [h['address'] for h in mod.search('Седова 65а/2')] == ['Седова 65а/2']


def test_v_zhivoy_rechi_otklyuchennyy_dom_ne_lovitsya(tmp_path, monkeypatch):
    mod = reload_with(tmp_path, monkeypatch, 'Седова 65а/2\n')
    assert mod.detect_house('на Байкальской 237 течь в подвале') is None
    assert mod.detect_house('на Седова 65а/2 нет ГВС')['address'] == 'Седова 65а/2'


def test_neizvestnyy_adres_v_spiske_prosto_ignoriruetsya(tmp_path, monkeypatch):
    mod = reload_with(tmp_path, monkeypatch, 'Седова 65а/2\nНесуществующая 999\n')
    assert [h['address'] for h in mod.HOUSES] == ['Седова 65а/2']


def test_boevoy_spisok_bez_opechatok():
    """Опечатка в адресе молча выкинула бы дом из работы — проверяем реальный файл."""
    import bot.houses as real

    known = {real._norm_addr(h['address']) for h in real.ALL_HOUSES}
    lishnie = sorted(a for a in real.load_active() if a not in known)

    assert not lishnie, f'в active_houses.txt адреса, которых нет в houses.json: {lishnie}'


def test_boevoy_spisok_ne_pustoy_i_menshe_polnogo():
    """Звено 2: дома сузили осознанно, остальные остались в справочнике."""
    import bot.houses as real

    assert 0 < len(real.load_active()) < len(real.ALL_HOUSES)
