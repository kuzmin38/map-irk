"""Люся переспрашивает вместо того, чтобы утверждать несуразицу.

В чат ушла карточка: «🎙 4-я Советская 26 · 2 видео — В квартире 71
обнаружено подтопление потолка и стояка». 4-я Советская 26 — парковка,
жилых квартир там нет; 71 — это номер дома Седова 71. Оба факта лежат
в справочнике, и ни один не был проверен: модель собрала связный текст
из двух роликов и голосовых, а связный он был только на вид.

Заказчик: «Надо как-то сделать, чтобы она не несла чушь. Если непонятно —
пусть переспрашивает».
"""
import pytest

from bot import db, houses, somneniya
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.SERIES.clear()


def dom(address):
    return next(h for h in houses.HOUSES if h['address'] == address)


# ── сам справочник ──────────────────────────────────────────────────────

def test_parkovka_pomechena_nezhiloy():
    """Проверка держится на этой пометке — без неё всё остальное бессмысленно."""
    assert somneniya.nezhiloy(dom('4-я Советская 26'))
    assert not somneniya.nezhiloy(dom('4-я Советская 30'))


def test_kvartira_v_nezhilom_dome_vopros():
    voprosy = somneniya.proverit(
        dom('4-я Советская 26'),
        'В квартире 71 обнаружено подтопление потолка и стояка.')
    assert voprosy
    assert 'квартир там нет' in voprosy[0]
    assert 'Парковка' in voprosy[0]
    assert 'Счётчики' not in voprosy[0], 'вопрос короткий, заметка целиком не нужна'


def test_nesushchestvuyushchaya_kvartira_vopros():
    voprosy = somneniya.proverit(dom('4-я Советская 30'), 'Течь в квартире 999')
    assert voprosy and '999' in voprosy[0]
    assert 'с 1 по' in voprosy[0], 'подсказываем, какие номера в доме есть'


def test_nastoyashchaya_kvartira_voprosov_ne_vyzyvaet():
    """Ложная тревога дороже пропуска: спрашивать по делу или молчать."""
    assert somneniya.proverit(dom('4-я Советская 30'),
                              'Течь в квартире 71', 'течь в квартире 71') == []


def test_goloe_chislo_nazvannoe_kvartiroy():
    """Сантехник сказал «71», модель написала «в квартире 71». А это дом."""
    voprosy = somneniya.proverit(dom('4-я Советская 30'),
                                 'Подтопление в квартире 71',
                                 'подтопление на 71')
    assert voprosy and 'Седова 71' in voprosy[0]


def test_adres_iz_sosednego_soobshcheniya_ogovarivaetsya():
    voprosy = somneniya.proverit(dom('Седова 71'), 'Течь в подвале',
                                 'течь в подвале',
                                 istochnik=somneniya.IZ_SOSEDNEGO)
    assert voprosy and 'из соседнего сообщения' in voprosy[0]


def test_adres_iz_rechi_ne_ogovarivaetsya():
    assert somneniya.proverit(dom('Седова 71'), 'Течь в подвале',
                              'течь в подвале') == []


def test_bolshe_dvuh_voprosov_ne_zadayot():
    voprosy = somneniya.proverit(dom('4-я Советская 26'),
                                 'Квартира 71, квартира 999, квартира 888',
                                 istochnik=somneniya.IZ_SOSEDNEGO)
    assert len(voprosy) <= 2


def test_kvartiry_naydeny_vo_vseh_napisaniyah():
    assert somneniya.kvartiry('в квартире 71') == [71]
    assert somneniya.kvartiry('кв. 105 подмес') == [105]
    assert somneniya.kvartiry('105 квартира') == [105]
    assert somneniya.kvartiry('кв.№8') == [8]
    assert somneniya.kvartiry('площадь 60 кв.м') == []


# ── карточка отчёта ─────────────────────────────────────────────────────

async def test_kartochka_perespashivaet(monkeypatch):
    """Тот самый отчёт целиком."""
    async def fake_ask(prompt, **kw):
        return 'В квартире 71 обнаружено подтопление потолка и стояка.'

    monkeypatch.setattr(H.ai, 'ask', fake_ask)
    text = await H.short_summary(['там семьдесят один топит потолок'],
                                 '4-я Советская 26')
    assert 'Какой это дом?' in text
    assert '❓' in text


async def test_kartochka_bez_somneniy_ostayotsya_chistoy(monkeypatch):
    async def fake_ask(prompt, **kw):
        return 'Подтопление в квартире 71, стояк перекрыт.'

    monkeypatch.setattr(H.ai, 'ask', fake_ask)
    text = await H.short_summary(['в квартире 71 топит, стояк перекрыл'],
                                 '4-я Советская 30')
    assert '❓' not in text


async def test_v_zadanii_zapret_nazyvat_chislo_kvartiroy(monkeypatch):
    zadacha = {}

    async def fake_ask(prompt, **kw):
        zadacha['text'] = prompt
        return 'коротко'

    monkeypatch.setattr(H.ai, 'ask', fake_ask)
    await H.short_summary(['семьдесят один'], None)
    assert 'голое число' in zadacha['text']
    assert 'номером дома' in zadacha['text']


# ── находки по квартирам ────────────────────────────────────────────────

def test_nahodka_v_nezhilom_dome_ne_zapisyvaetsya():
    """Иначе парковка обзаведётся историей квартиры 71."""
    otvet = H.zapisat_nahodku(1, dom('4-я Советская 26'),
                              'в 71 квартире нашёл подмес', 5, 'Андрей')
    assert otvet and '❓' in otvet
    assert not db.flat_notes(dom('4-я Советская 26')['id'], 71)


def test_nahodka_v_zhilom_dome_zapisyvaetsya():
    h = dom('4-я Советская 30')
    otvet = H.zapisat_nahodku(1, h, 'в 71 квартире нашёл подмес', 5, 'Андрей')
    assert otvet and '❓' not in otvet
    assert db.flat_notes(h['id'], 71)
