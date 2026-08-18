"""Привязка домов к ЖК берётся из файла, а не проставляется руками по одному."""
import pytest

from bot import db, houses


@pytest.fixture(autouse=True)
def chisto(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    return tmp_path


def fayl(tmp_path, monkeypatch, text):
    path = tmp_path / 'house_complex.txt'
    path.write_text(text, encoding='utf-8')
    monkeypatch.setattr(houses, 'COMPLEX_FILE', str(path))


def test_adresa_iz_fayla_popadayut_v_bazu(chisto, monkeypatch):
    fayl(chisto, monkeypatch, '4-я Советская 30 = 4sun\nСедова 65а/2 = zhemchuzhina\n')

    db.init()

    dom = next(h for h in houses.ALL_HOUSES if h['address'] == '4-я Советская 30')
    assert db.get_house_complex(dom['id']) == '4sun'
    assert len(db.all_house_complexes()) == 2


def test_primechaniya_i_pustye_stroki_propuskayutsya(chisto, monkeypatch):
    fayl(chisto, monkeypatch,
         '# комментарий\n\n4-я Советская 30 = 4sun  # ЖК Четыре солнца\n')

    db.init()

    assert len(db.all_house_complexes()) == 1


def test_zapis_rukami_ne_perezapisyvaetsya(chisto, monkeypatch):
    """Человек в боте поправил — файл его мнение не отменяет."""
    dom = next(h for h in houses.ALL_HOUSES if h['address'] == '4-я Советская 30')
    db.init()
    db.set_house_complex(dom['id'], 'kvartal')

    fayl(chisto, monkeypatch, '4-я Советская 30 = 4sun\n')
    db.seed_house_complexes()

    assert db.get_house_complex(dom['id']) == 'kvartal'


def test_neizvestnyy_adres_ne_ronyaet_zapusk(chisto, monkeypatch, caplog):
    import logging

    fayl(chisto, monkeypatch, 'Несуществующая улица 1 = 4sun\n4-я Советская 30 = 4sun\n')

    with caplog.at_level(logging.WARNING, logger='db'):
        db.init()

    assert 'неизвестный адрес' in caplog.text
    assert len(db.all_house_complexes()) == 1, 'остальные привязки применились'


def test_povtornyy_zapusk_nichego_ne_menyaet(chisto, monkeypatch):
    fayl(chisto, monkeypatch, '4-я Советская 30 = 4sun\n')
    db.init()

    assert db.seed_house_complexes() == 0


def test_pustoy_fayl_ostavlyaet_vsyo_kak_est(chisto, monkeypatch):
    fayl(chisto, monkeypatch, '# только комментарии\n')

    db.init()

    assert db.all_house_complexes() == {}
