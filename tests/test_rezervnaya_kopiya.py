"""Резервная копия базы и заметки по домам в Markdown.

Копий не было вообще: вся работа — заявки, показания, паспорта — лежала
в одном файле на диске Railway. Заодно копия решает вторую задачу: дома
выгружаются в Markdown, который заказчик читает в Obsidian.
"""
import io
import os
import sqlite3
import zipfile

import pytest

from bot import backup, db, houses


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


@pytest.fixture
def dom():
    h = next(x for x in houses.HOUSES if x['address'] == 'Седова 71')
    db.set_passport_field(h['id'], 'rozliv', 'Нижний, сталь ДУ50', 'Андрей')
    m = db.add_meter(h['id'], 'hvs', 'ВСХд-15 подвал', 'Андрей')
    db.update_meter(m, serial='64380455')
    db.add_reading(m, 1234.5, '2026-08', 1, 'Андрей')
    p = db.add_point(h['id'], 'ИТП, подача', 'ТП-1', 'Андрей')
    db.add_device(p, '12345', '2028-07-01', 1, 'Андрей')
    db.add_work(h['id'], 'Промывка системы', '2026-09-01', 'Андрей', 1)
    return h


def test_kopiya_bazy_chitaetsya(tmp_path, dom):
    """Копию делаем средствами SQLite: файл могли скопировать во время записи."""
    dest = str(tmp_path / 'copy' / 'bot.db')

    backup.snapshot(dest)

    c = sqlite3.connect(dest)
    meters = c.execute('SELECT label, serial FROM meters').fetchall()
    c.close()
    assert meters == [('ВСХд-15 подвал', '64380455')]


def test_v_zametke_est_vsyo_hozyaystvo_doma(dom):
    text = backup.house_markdown(dom)

    assert 'Нижний, сталь ДУ50' in text, 'паспорт'
    assert '64380455' in text, 'заводской номер счётчика'
    assert '1234' in text, 'последнее показание'
    assert '01.07.2028' in text, 'срок поверки манометра'
    assert 'Промывка системы' in text, 'работа'


def test_zametka_prigodna_dlya_obsidian(dom):
    text = backup.house_markdown(dom)

    assert text.startswith('---\n'), 'свойства заметки сверху'
    assert 'tags: [дом, жк/' in text, 'тег по ЖК'
    assert f"# {dom['address']}" in text, 'заголовок — адрес'
    assert '- [ ] Промывка системы' in text, 'невыполненная работа — галочкой'


def test_vypolnennaya_rabota_otmechena_galochkoy(dom):
    w = db.add_work(dom['id'], 'Опрессовка', '2026-07-01', 'Андрей', 1)
    db.update_work(w, status=db.WORK_DONE)

    assert '- [x] Опрессовка' in backup.house_markdown(dom)


def test_rasshifrovka_iz_chata_popadaet_v_zametku(dom):
    rid = db.add_chat_record(7, 'm1', 100, 'Виталя', None,
                             house_id=dom['id'], has_files=True, is_issue=True)
    db.set_chat_transcript(rid, 'Подтапливает по стояку, перекрыли')

    text = backup.house_markdown(dom)

    assert 'Подтапливает по стояку' in text
    assert 'Виталя' in text


def test_oglavlenie_svyazyvaet_zametki():
    text = backup.index_markdown()

    assert '[[Седова 71]]' in text, 'ссылка Obsidian на заметку дома'
    assert 'ЖК Четыре солнца' in text, 'группировка по ЖК'


def test_arhiv_soderzhit_bazu_i_zametki(dom):
    data, name = backup.make_archive()

    z = zipfile.ZipFile(io.BytesIO(data))
    imena = z.namelist()
    assert 'bot.db' in imena
    assert 'Дома/Седова 71.md' in imena
    assert 'Дома/00 Оглавление.md' in imena
    assert 'Дома/00 Опись имущества.md' in imena
    # заметка на дом плюс два общих файла: оглавление и опись
    assert len([n for n in imena if n.startswith('Дома/')]) == len(houses.HOUSES) + 2
    assert name.startswith('lusya_') and name.endswith('.zip')


def test_starye_kopii_ne_kopyatsya_beskonechno(dom, monkeypatch):
    monkeypatch.setattr(backup, 'KEEP', 3)

    for i in range(5):
        path = backup.save_archive()
        os.rename(path, os.path.join(backup.backup_dir(), f'lusya_2026-08-{10 + i}.zip'))

    ostalos = [f for f in os.listdir(backup.backup_dir()) if f.endswith('.zip')]
    assert len(ostalos) <= backup.KEEP + 1


def test_adres_s_drobyu_ne_lomaet_imya_fayla():
    """«Байкальская 126/1» — дробь в имени файла недопустима."""
    assert '/' not in backup._safe('Байкальская 126/1')
    assert backup._safe('Байкальская 126/1') == 'Байкальская 126-1'


# ---------- Модель ----------

def test_razgovornaya_i_zvukovaya_modeli_odnogo_postavschika():
    """Один ключ и один счёт OpenRouter — отдельно ничего оплачивать не надо."""
    from bot import ai, transcribe

    assert ai.KIMI_MODEL == transcribe.AUDIO_MODEL


def test_est_zapasnaya_model():
    """Когда основная молчит, вопрос человека не должен пропадать."""
    from bot import ai

    assert ai.FALLBACK_MODEL and ai.FALLBACK_MODEL != ai.KIMI_MODEL


async def test_pri_molchanii_osnovnoy_probuem_zapasnuyu(monkeypatch):
    from bot import ai

    zvali = []

    async def fake_call(model, messages, tools, max_tokens, temperature, timeout=None):
        zvali.append(model)
        return None if len(zvali) == 1 else {'role': 'assistant', 'content': 'ответ'}

    monkeypatch.setattr(ai, '_one_call', fake_call)
    monkeypatch.setattr(ai, 'KIMI_API_KEY', 'ключ')

    otvet = await ai.chat([{'role': 'user', 'content': 'привет'}])

    assert zvali == [ai.KIMI_MODEL, ai.FALLBACK_MODEL]
    assert otvet['content'] == 'ответ'
