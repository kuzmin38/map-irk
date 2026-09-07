"""Находка называется термином и не висит в отрыве от дома.

В чате подряд ушли два сообщения: «Трилиссера 22, кв. 34 — записала: течка»
и «Трилиссера 22, кв. 33 — записала: течка». Заказчик: «Чё за течка???
Сделай, чтобы она видела контекст, понимала картину в целом и выражалась
нормальными словами: течь!»

Обе беды разные. «Течка» — Люся печатала находку ровно как услышала.
Отрыв — она не связывала две квартиры одного дома, хотя сантехник тут же
рядом написал «отключили 3, 4 стояки», и шахматка это подтверждает.
"""
import pytest

from bot import db, flats, houses
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


def dom(address):
    return next(h for h in houses.HOUSES if h['address'] == address)


# ── термин ──────────────────────────────────────────────────────────────

def test_techka_stanovitsya_techyu():
    assert flats.nahodka('22 дом - 34 кв. течка по стояку') == 'течь'
    assert flats.nahodka('кв 33 течет') == 'течь'
    assert flats.nahodka('в 12 кв подтекает') == 'течь'
    assert flats.nahodka('кв 7 сочится по резьбе') == 'течь'


def test_ostalnye_terminy():
    assert flats.nahodka('105 квартира нашёл подмес') == 'подмес'
    assert flats.nahodka('кв. 8 засорён лежак') == 'засор'
    assert flats.nahodka('кв 5 не перекрывается кран') == 'запорная арматура не держит'
    assert flats.nahodka('кв 9 плесень в углу') == 'плесень'


def test_odna_nahodka_odin_kind():
    """«Течка» и «течь» теперь один вид — повтор перестал двоиться."""
    assert flats.kind_of(flats.nahodka('кв 1 течка')) == \
           flats.kind_of(flats.nahodka('кв 1 течь'))


def test_zhivaya_rech_ostayotsya_v_zapisi():
    """Термин — для строчки отчёта, а живая фраза сохраняется целиком."""
    text = 'Трилиссера 22, кв. 34, течка из-под счётчика ГВС'
    assert 'течка' in flats.summary(text)


def test_neizvestnoe_slovo_ne_portim():
    assert flats.termin('обратка идёт') == 'подмес'
    assert flats.termin('') == ''


# ── картина по дому ─────────────────────────────────────────────────────

def test_pervaya_nahodka_bez_hvosta():
    """Связывать пока не с чем — лишней строки быть не должно."""
    otvet = H.zapisat_nahodku(1, dom('Трилиссера 22'),
                              'кв. 34 течка из-под счётчика', 5, 'Андрей')
    assert 'течь' in otvet
    assert 'течка' not in otvet
    assert '📎' not in otvet


def test_vtoraya_nahodka_svyazyvaetsya_so_pervoy():
    """Тот самый случай: 33 и 34 на Трилиссера 22."""
    h = dom('Трилиссера 22')
    H.zapisat_nahodku(1, h, 'кв. 34 течка из-под счётчика', 5, 'Андрей')
    otvet = H.zapisat_nahodku(2, h, 'кв. 33 течка', 5, 'Андрей')

    assert '📎' in otvet
    assert 'кв. 34' in otvet
    assert 'Этаж 6' in otvet
    assert 'стояки 3 и 4' in otvet, 'сантехник сказал то же: «отключили 3,4 стояки»'


def test_raznye_nahodki_ne_svyazyvayutsya():
    """Засор в одной квартире и течь в другой — не одно событие."""
    h = dom('Трилиссера 22')
    H.zapisat_nahodku(1, h, 'кв. 34 засор', 5, 'Андрей')
    otvet = H.zapisat_nahodku(2, h, 'кв. 33 течка', 5, 'Андрей')
    assert '📎' not in otvet


def test_odin_stoyak_nazyvaetsya_stoyakom():
    """Квартиры одного стояка на разных этажах — это стояк, а не этаж."""
    h = dom('Трилиссера 22')
    # 3 и 33: по шахматке оба третий стояк, этажи разные
    H.zapisat_nahodku(1, h, 'кв. 3 течка', 5, 'Андрей')
    otvet = H.zapisat_nahodku(2, h, 'кв. 33 течка', 5, 'Андрей')
    assert 'один стояк' in otvet


def test_bez_shahmatki_tolko_kvartiry():
    """Шахматки нет — связь называем, а стояки не выдумываем."""
    h = dom('Байкальская 126/3')
    H.zapisat_nahodku(1, h, 'кв. 34 течка', 5, 'Андрей')
    otvet = H.zapisat_nahodku(2, h, 'кв. 33 течка', 5, 'Андрей')
    assert '📎' in otvet and 'кв. 34' in otvet
    assert 'стояк' not in otvet and 'Этаж' not in otvet


def test_drugoy_dom_ne_pritaskivaetsya():
    H.zapisat_nahodku(1, dom('Трилиссера 22'), 'кв. 34 течка', 5, 'Андрей')
    otvet = H.zapisat_nahodku(2, dom('Трилиссера 18б'), 'кв. 33 течка', 5, 'Андрей')
    assert '📎' not in otvet


# ── намерение — не находка ──────────────────────────────────────────────

SLUCHAY = ('Отключили 3,4 стояки на 22 доме. Но, вероятно, что это всё один '
           'счётчик виноват. Ждём 33 кв. чтобы подтвердить догадку')


def test_tot_samyy_sluchay_nichego_ne_pishet():
    """Заказчик: «И в 33 нет течи. Я написал, что мы ждём доступ»."""
    assert flats.parse_note(SLUCHAY) is None


def test_zapisat_nahodku_na_takom_soobshchenii_molchit():
    otvet = H.zapisat_nahodku(1, dom('Трилиссера 22'), SLUCHAY, 5, 'Андрей')
    assert otvet is None
    assert not db.flat_notes(dom('Трилиссера 22')['id'])


@pytest.mark.parametrize('text', [
    'Ждём доступ в 33 квартиру для осмотра, возможно там течь',
    'кв. 5 надо проверить, может быть течь',
    'В 12 кв не пустили, вероятно засор',
    'Если в кв. 7 будет течь, отключим стояк',
    'Планируем осмотреть кв. 9, похоже на подмес',
])
def test_namerenie_i_dogadka_ne_zapisyvayutsya(text):
    assert flats.parse_note(text) is None


@pytest.mark.parametrize('text,zhdyom', [
    ('Проверил кв. 5 - течь по резьбе', (5, 'течь')),
    ('Был в 12 кв, засор в лежаке', (12, 'засор')),
    ('71/1, 105 квартира, нашёл подмес', (105, 'подмес')),
    ('105 квартира. Нашёл подмес.', (105, 'подмес')),
    ('22 дом - 34 кв. Разгерметизация счётчика ГВС, течка', (34, 'течь')),
])
def test_fakt_v_proshedshem_vremeni_zapisyvaetsya(text, zhdyom):
    """Проверять надо оба края: «проверим» — план, «проверил» — факт."""
    assert flats.parse_note(text) == zhdyom


def test_nomer_iz_odnoy_frazy_ne_kleitsya_so_slovom_iz_drugoy():
    """Суть поломки: квартиру и находку искали по всему тексту врозь."""
    text = 'В кв. 34 течь. В кв. 33 был, всё сухо.'
    assert flats.parse_note(text) == (34, 'течь')


def test_tochka_v_sokrashchenii_frazu_ne_konchaet():
    """«В кв. 34 течь» рвалось на «В кв.» и «34 течь» — находка теряла квартиру."""
    assert flats._frazy('В кв. 34 течь.') == ['В кв. 34 течь.']
    assert flats._frazy('Был на д. 22. Всё сухо.') == ['Был на д. 22.', 'Всё сухо.']
