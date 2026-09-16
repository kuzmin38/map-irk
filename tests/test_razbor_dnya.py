"""Ночной разбор: в паспорт дома — итог, а не паника по дороге к нему.

Заказчик: авария «потоп с балкона над 17 квартирой» и находка «в 121 кв.
пробка вылетела из циркуляционного насоса» оказались одним и тем же
происшествием. Пока причина не найдена, в чате шумели — все говорили,
что топит, что виноваты соседи сверху, «это всё вилами на воде писано».
«Вот это должно попасть в паспорт дома... а вот этого шума сохранять
не нужно».

Код здесь механический: собрать ленту, сохранить то, что вернула модель.
Судит модель — а инструкция ей прямо говорит не превращать версии и
обвинения в факт, ждать подтверждения.
"""
import pytest

from bot import db, houses, razbor


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


def dom(address):
    return next(h for h in houses.HOUSES if h['address'] == address)


# ── сама инструкция ──────────────────────────────────────────────────────

def test_zadanie_zapreshchaet_versii_i_obvineniya():
    assert 'версии' in razbor.ZADANIE
    assert 'без подтверждения' in razbor.ZADANIE


def test_zadanie_velit_shvatyvat_proisshestvie_v_odin_itog():
    assert 'итог' in razbor.ZADANIE


def test_zadanie_ne_soderzhit_nastoyashchih_adresov():
    """Тот же запрет, что и везде: пример с реальным адресом модель
    перепишет в разбор как факт."""
    import re
    adres = re.compile(r'[А-ЯЁ][а-яё]{4,}\s+\d{1,3}[а-я]?(?:/\d+)?')
    assert not adres.findall(razbor.ZADANIE)


# ── lenta_za_den: механика сборки ───────────────────────────────────────

def test_lenta_sobiraet_transkript_i_tekst():
    h = dom('Красных Мадьяр 14')
    zapis = db.add_chat_record(chat_id=-1, mid='m1', user_id=7, user_name='Виталя',
                               text='топит', house_id=h['id'])
    db.set_chat_transcript(zapis, 'Нашёл причину: в квартире 121 пробку вырвало с насоса.')

    lenta = razbor.lenta_za_den(db.now().split()[0])
    assert 'Виталя' in lenta
    assert h['address'] in lenta


def test_lenta_propuskaet_pustye_zapisi():
    db.add_chat_record(chat_id=-1, mid='m1', user_id=7, user_name='Костя', text='')
    lenta = razbor.lenta_za_den(db.now().split()[0])
    assert lenta == ''


# ── razobrat_den: короткий день не гоняем через модель ─────────────────

async def test_pustoy_den_ne_zovyot_model(monkeypatch):
    zvali = {'da': False}

    async def fake_ask(*a, **kw):
        zvali['da'] = True
        return '{}'

    monkeypatch.setattr(razbor.ai, 'ask', fake_ask)
    itog = await razbor.razobrat_den('01.01.2027')
    assert itog == {'дома': [], 'повисло': []}
    assert not zvali['da']


async def test_polnyy_den_idyot_na_medlennoy_modeli(monkeypatch):
    h = dom('Красных Мадьяр 14')
    db.add_chat_record(chat_id=-1, mid='m1', user_id=7, user_name='Виталя',
                       text='В доме ' + h['address'] + ' случился потоп с '
                       'балкона, соседи говорят разное, но пока непонятно, '
                       'кто виноват — ждём результатов осмотра, ' * 3,
                       house_id=h['id'])

    zapros = {}

    async def fake_ask(text, **kw):
        zapros.update(kw)
        return '{"дома": [], "повисло": []}'

    monkeypatch.setattr(razbor.ai, 'ask', fake_ask)
    await razbor.razobrat_den(db.now().split()[0])

    assert zapros.get('model') == razbor.ai.SLOW_MODEL
    assert zapros.get('temperature') == 0


# ── sohranit: механика записи ───────────────────────────────────────────

def test_sohranit_zapisyvaet_odin_itog_a_ne_shum():
    """Ровно случай заказчика: модель уже схлопнула происшествие в итог —
    задача кода просто сохранить его, не изобретая ничего своего."""
    h = dom('Красных Мадьяр 14')
    razbor_dnya = {
        'дома': [{
            'адрес': h['address'],
            'факты': [
                {'что': 'В квартире 121 пробку вырвало с циркуляционного '
                        'насоса, из-за этого топило соседей ниже',
                 'вид': 'находка'},
            ],
        }],
        'повисло': [],
    }
    n = razbor.sohranit('01.09.2027', razbor_dnya)
    assert n == 1
    fakty = db.house_facts(h['id'])
    assert len(fakty) == 1
    assert 'циркуляционного насоса' in fakty[0]['text']


def test_sohranit_propuskaet_neopoznannyy_dom():
    razbor_dnya = {'дома': [{'адрес': 'Непонятная улица 1', 'факты': [
        {'что': 'что-то случилось'}]}]}
    assert razbor.sohranit('01.09.2027', razbor_dnya) == 0
    assert db.facts_for_day('2027-09-01') == []


def test_sohranit_propuskaet_pustye_fakty():
    h = dom('Красных Мадьяр 14')
    razbor_dnya = {'дома': [{'адрес': h['address'], 'факты': [{'что': '  '}]}]}
    assert razbor.sohranit('01.09.2027', razbor_dnya) == 0


def test_sohranit_bez_domov_nichego_ne_pishet():
    assert razbor.sohranit('01.09.2027', {}) == 0
    assert razbor.sohranit('01.09.2027', {'дома': []}) == 0


# ── day_already_parsed / повторный разбор ───────────────────────────────

def test_povtorno_ne_razbiraet_uzhe_razobrannyy_den():
    h = dom('Красных Мадьяр 14')
    razbor.sohranit('01.09.2027', {'дома': [{'адрес': h['address'],
                     'факты': [{'что': 'нашли причину'}]}]})
    assert db.day_already_parsed('2027-09-01') is True
    assert db.day_already_parsed('2027-09-02') is False
