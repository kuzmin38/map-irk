"""Поквартирный обход: кто проверен, куда не попали, что нашли.

Заказчик: «идём ищем подмес по стояку. Сантехник сообщает, что в таких-то
квартирах мимо — значит, всё проверено, туда можно не ходить. Такие-то
квартиры ещё нужно проверить. Если обнаружен подмес, обнаружен гигдуш,
обнаружен водогрей — вот эту всю информацию она должна фиксировать. Потом,
если будут заявки о подмесе, мы уже будем знать, где что находится».

Оба отчёта ниже — настоящие, из чата бригады. Модель здесь подставная:
её дело — разложить живую речь по квартирам, а проверяем мы то, что
делает код, и ошибка кода дороже: он пишет в карточки.
"""
import types

import pytest

from bot import db, houses, obhod
import bot.handlers as H

# Костя, чат бригады
OTCHYOT_KOSTI = ('По 14 дому по гулу; 25 завтра в 8 00 предоставят доступ, '
                 '33 гул отсутствует, говорит что ниже, 49 не живут там. '
                 '41 мимо, 1 мимо.')

# Виталя, тот же чат — построчно
OTCHYOT_VITALI = (
    'Подмес 28 - 123\n\n'
    '22 сентября 8:15\n'
    '123 - нет дома\n'
    '114 - нет дома\n'
    '105 - обнаружен незакрытый гиг душ. Наличие обратного клапана можно '
    'посмотреть только эндоскопом.\n'
    '96 - нет дома\n'
    '78 - гиг душа нет, водогрея нет. Жалоб тоже нет.'
)


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


def dom(address):
    return next(h for h in houses.HOUSES if h['address'] == address)


# ---------- Узнаём обход ----------

@pytest.mark.parametrize('text', [OTCHYOT_KOSTI, OTCHYOT_VITALI])
def test_nastoyashchiy_otchyot_pohozh_na_obhod(text):
    assert obhod.pohozh(text)


@pytest.mark.parametrize('text', [
    'Красных Мадьяр 14, 105 квартира, нашёл подмес',
    'Люся, что по нормативам на течь?',
    'привезли 30 хомутов',
    '',
])
def test_obychnoe_soobschenie_ne_obhod(text):
    assert not obhod.pohozh(text)


def test_dom_beryotsya_iz_teksta_kodom():
    """Дом определяет код: ошибка здесь пишет обход в чужую карточку."""
    assert houses.detect_house(OTCHYOT_KOSTI)['address'] == 'Красных Мадьяр 14'
    assert houses.detect_house(OTCHYOT_VITALI)['address'] == '4-я Советская 28'


# ---------- Разбор моделью ----------

async def test_razbor_chitaet_json(monkeypatch):
    async def fake_ask(prompt, **kw):
        return '{"квартиры": [{"кв": 41, "итог": "проверено", "что": "мимо"}]}'
    monkeypatch.setattr(obhod.ai, 'ask', fake_ask)

    assert await obhod.razobrat(OTCHYOT_KOSTI) == [
        {'кв': 41, 'итог': 'проверено', 'что': 'мимо'}]


async def test_razbor_perezhivaet_musor_ot_modeli(monkeypatch):
    async def fake_ask(prompt, **kw):
        return 'конечно! вот: не json'
    monkeypatch.setattr(obhod.ai, 'ask', fake_ask)

    assert await obhod.razobrat(OTCHYOT_KOSTI) == []


def test_zadanie_zapreshchaet_schitat_datu_kvartiroy():
    """«22 сентября 8:15» в отчёте Витали — дата, а не квартира 22."""
    assert 'Дату, время' in obhod.ZADANIE
    assert 'не придумывай' in obhod.ZADANIE


def test_zadanie_zapreshchaet_perevorachivat_otritsanie():
    """«Гиг душа нет» — это не находка «есть гигиенический душ»."""
    assert 'Отрицание не переворачивай' in obhod.ZADANIE


# ---------- Запись ----------

def test_zapisyvaet_kazhduyu_kvartiru_so_svoim_vidom():
    h = dom('4-я Советская 28')
    itogi = obhod.sohranit(h, [
        {'кв': 123, 'итог': 'не попал', 'что': 'нет дома'},
        {'кв': 105, 'итог': 'находка', 'что': 'незакрытый гигиенический душ'},
        {'кв': 78, 'итог': 'проверено', 'что': 'гигиенического душа нет, жалоб нет'},
    ], 100, 'Виталя')

    assert len(itogi['не попал']) == 1
    assert len(itogi['находка']) == 1
    vidy = {z['flat']: z['kind'] for z in db.flat_notes(h['id'])}
    assert vidy[123] == obhod.NET_DOSTUPA
    assert vidy[105] == 'гигиенический душ', 'по этому виду её потом и спросят'
    assert vidy[78] == obhod.PROVERENO


def test_otritsanie_ostayotsya_otritsaniem():
    """«Водогрея нет» не должно превратиться в «есть водонагреватель»."""
    h = dom('4-я Советская 28')
    obhod.sohranit(h, [{'кв': 78, 'итог': 'проверено',
                        'что': 'гигиенического душа нет, водогрея нет'}])

    zapis = db.flat_notes(h['id'], 78)[0]
    assert zapis['kind'] == obhod.PROVERENO
    assert 'нет' in zapis['text']


def test_podmes_lozhitsya_pod_svoim_terminom():
    """Вид тот же, что и у находки из обычного сообщения, — иначе повтор
    по квартире не совпадёт сам с собой."""
    from bot import flats

    h = dom('4-я Советская 28')
    obhod.sohranit(h, [{'кв': 105, 'итог': 'находка', 'что': 'обнаружен подмес'}])

    assert db.flat_notes(h['id'], 105)[0]['kind'] == flats.kind_of('подмес')


def test_kvartiry_kotoroy_net_v_dome_ne_zapisyvaet():
    """Шахматка знает состав дома. Выдуманный номер осядет как факт."""
    h = dom('Красных Мадьяр 14')
    itogi = obhod.sohranit(h, [
        {'кв': 41, 'итог': 'проверено', 'что': 'мимо'},
        {'кв': 1999, 'итог': 'проверено', 'что': 'мимо'},
    ])

    assert itogi['мимо_дома'] == [1999]
    assert [z['flat'] for z in db.flat_notes(h['id'])] == [41]


def test_tot_zhe_obhod_dvazhdy_ne_udvaivaetsya():
    """Один отчёт приходит и из чата бригады, и из «Обслуживания»."""
    h = dom('Красных Мадьяр 14')
    zapisi = [{'кв': 41, 'итог': 'проверено', 'что': 'мимо'}]
    obhod.sohranit(h, zapisi)
    obhod.sohranit(h, zapisi)

    assert len(db.flat_notes(h['id'], 41)) == 1


# ---------- Что осталось по стояку ----------

def test_ostalos_schitaetsya_po_shahmatke():
    """Ровно то, ради чего всё: «такие-то квартиры ещё нужно проверить»."""
    h = dom('Красных Мадьяр 14')

    hvost = obhod.ostalos(h, [1, 33, 41])

    assert 9 in hvost and 17 in hvost, 'эти на том же стояке и не обойдены'
    assert 1 not in hvost and 41 not in hvost, 'по этим уже прошли'


def test_ostalos_pomnit_proshlye_dni():
    """Стояк редко обходят за один заход."""
    h = dom('Красных Мадьяр 14')
    obhod.sohranit(h, [{'кв': 9, 'итог': 'проверено', 'что': 'мимо'}])

    assert 9 not in obhod.ostalos(h, [1, 33])


def test_kuda_ne_popali_ostayotsya_v_spiske():
    """«Нет дома» — не проверено: туда как раз и надо вернуться."""
    h = dom('Красных Мадьяр 14')
    obhod.sohranit(h, [{'кв': 9, 'итог': 'не попал', 'что': 'нет дома'}])

    assert 9 in obhod.ostalos(h, [1, 33])


def test_kvartiry_s_raznyh_stoyakov_ne_schitayutsya():
    """Обходят не стояк — считать нечего, и врать не надо."""
    h = dom('Красных Мадьяр 14')
    assert obhod.ostalos(h, [1, 2, 3]) is None


# ---------- Сводка ----------

def test_svodka_soderzhit_vsyo_glavnoe():
    h = dom('4-я Советская 28')
    itogi = obhod.sohranit(h, [
        {'кв': 123, 'итог': 'не попал', 'что': 'нет дома'},
        {'кв': 105, 'итог': 'находка', 'что': 'незакрытый гигиенический душ'},
        {'кв': 78, 'итог': 'проверено', 'что': 'жалоб нет'},
    ])
    text = obhod.svodka(h, itogi)

    assert '4-я Советская 28' in text
    assert 'Проверено (1): 78' in text
    assert 'кв. 105 — незакрытый гигиенический душ' in text
    assert 'Не попали (1): 123 (нет дома)' in text
    assert 'осталось' in text


def test_svodka_predupredit_o_chuzhoy_kvartire():
    h = dom('Красных Мадьяр 14')
    itogi = obhod.sohranit(h, [{'кв': 1999, 'итог': 'проверено', 'что': 'мимо'}])

    assert 'нет квартир 1999' in obhod.svodka(h, itogi)


# ---------- Чат и личка целиком ----------

class Msg:
    def __init__(self, text, chat_id, chat_type):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Костя')
        self.recipient = types.SimpleNamespace(
            user_id=100 if chat_type == 'dialog' else None,
            chat_id=chat_id, chat_type=chat_type)
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')


def event(text, chat_id=7, chat_type='chat'):
    e = types.SimpleNamespace()
    e.message = Msg(text, chat_id, chat_type)
    e.bot = None
    return e


@pytest.fixture
def model(monkeypatch):
    """Модель разобрала отчёт Кости так, как и должна."""
    async def fake_ask(prompt, **kw):
        return ('{"квартиры": ['
                '{"кв": 25, "итог": "не попал", "что": "завтра в 8:00 дадут доступ"},'
                '{"кв": 33, "итог": "проверено", "что": "гул отсутствует"},'
                '{"кв": 49, "итог": "не попал", "что": "не живут"},'
                '{"кв": 41, "итог": "проверено", "что": "мимо"},'
                '{"кв": 1, "итог": "проверено", "что": "мимо"}]}')
    monkeypatch.setattr(obhod.ai, 'ask', fake_ask)


async def test_obhod_iz_chata_bригады_zapisyvaetsya(model):
    """Лента там выключена — обход это не касается."""
    db.set_recording(7, False)
    e = event(OTCHYOT_KOSTI)

    await H.on_text(e)

    h = dom('Красных Мадьяр 14')
    assert len(db.flat_notes(h['id'])) == 5
    assert 'Обход' in e.message.sent[-1]
    assert db.recent_chat_records() == [], 'в ленту обход не тянем'


async def test_obhod_iz_lichki_zapisyvaetsya(model):
    e = event(OTCHYOT_KOSTI, chat_id=None, chat_type='dialog')

    await H.on_text(e)

    assert len(db.flat_notes(dom('Красных Мадьяр 14')['id'])) == 5
    assert 'осталось' in e.message.sent[-1]


async def test_v_rabochem_chate_obhod_ne_pishetsya_dvazhdy(model):
    """Лента ведётся, но находку из обхода перехватывать ей нельзя."""
    db.set_recording(7, True)
    e = event(OTCHYOT_KOSTI)

    await H.on_text(e)

    h = dom('Красных Мадьяр 14')
    assert len(db.flat_notes(h['id'])) == 5, 'по записи на квартиру, без дублей'
    assert len(db.recent_chat_records()) == 1, 'само сообщение в ленте осталось'
