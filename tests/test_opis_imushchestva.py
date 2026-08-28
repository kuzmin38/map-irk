"""Опись: что где лежит.

Заказчик: «нужно сделать инвентаризацию, с пометками что где лежит, в
каком подвале, на каком адресе. Потому что при подтоплении парковки даже
не знали, что у нас есть мотопомпа в компании».
"""
import types

import pytest

from bot import db, houses, inventory
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


class Msg:
    def __init__(self, text):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=7, chat_type='chat')
        self.sent = []
        self.link = None

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')


def event(text):
    e = types.SimpleNamespace()
    e.message = Msg(text)
    e.bot = None
    return e


# ---------- Разбор строки ----------

@pytest.mark.parametrize('fraza', [
    'в инвентарь: мотопомпа, подвал, Седова 71',
    'запиши в опись мотопомпу',
    'мотопомпа, бытовка — на учёт',
])
def test_prosba_zapisat_uznayotsya(fraza):
    assert inventory.wants_add(fraza) is True


@pytest.mark.parametrize('fraza', [
    'поехал за мотопомпой',
    'инвентарь не нужен',          # слова «в инвентарь» нет
    'принял смену',
])
def test_obychnye_soobscheniya_ne_trogaem(fraza):
    assert inventory.wants_add(fraza) is False


def test_veshch_mesto_i_adres_razbirayutsya():
    nazvanie, mesto, dom, skolko = inventory.parse_add(
        'запиши в инвентарь: мотопомпа, подвал, Седова 71')

    assert nazvanie == 'мотопомпа'
    assert mesto == 'подвал'
    assert dom['address'] == 'Седова 71'
    assert skolko == 1


def test_adres_ne_popadaet_v_mesto():
    """«Подвал на Седова 71» — место «подвал», а не «подвал на Седова 71»."""
    _, mesto, dom, _ = inventory.parse_add('в опись: лестница, подвал Седова 71')

    assert dom['address'] == 'Седова 71'
    assert 'седов' not in mesto.lower(), f'адрес остался в месте: {mesto}'
    assert '71' not in mesto


def test_ulitsa_iz_dvuh_slov_i_poryadkovaya():
    _, mesto, dom, _ = inventory.parse_add('в опись: сварочник, ИТП, 4-я Советская 30')
    assert dom['address'] == '4-я Советская 30'
    assert mesto == 'ИТП'

    _, mesto2, dom2, _ = inventory.parse_add('в опись: стремянка, подвал, Красных Мадьяр 14')
    assert dom2['address'] == 'Красных Мадьяр 14'
    assert mesto2 == 'подвал'


@pytest.mark.parametrize('fraza,skolko', [
    ('в инвентарь: тепловая пушка 2 шт, бытовка', 2),
    ('в инвентарь: 3 тепловые пушки, бытовка', 3),
    ('в инвентарь: тепловая пушка, бытовка', 1),
])
def test_kolichestvo(fraza, skolko):
    assert inventory.parse_add(fraza)[3] == skolko


def test_bez_adresa_tozhe_zapisyvaetsya():
    """Инструмент в бытовке к дому не привязан, но искать его всё равно надо."""
    nazvanie, mesto, dom, _ = inventory.parse_add('в инвентарь: вышка-тура, склад')

    assert nazvanie == 'вышка-тура'
    assert mesto == 'склад'
    assert dom is None


# ---------- Поиск ----------

@pytest.mark.parametrize('zapros,nazvanie', [
    ('мотопомпа', 'мотопомпа Хонда'),
    ('мотопомпы', 'мотопомпа'),
    ('помпа', 'мотопомпа'),
    ('туры', 'вышка-тура'),
    ('пушку', 'тепловая пушка'),
])
def test_veshch_nahoditsya_v_lyuboy_forme(zapros, nazvanie):
    assert inventory.matches(zapros, nazvanie) is True


@pytest.mark.parametrize('zapros,nazvanie', [
    ('мотопомпа', 'стремянка'),
    ('сварочник', 'тепловая пушка'),
])
def test_chuzhoe_ne_nahoditsya(zapros, nazvanie):
    assert inventory.matches(zapros, nazvanie) is False


@pytest.mark.parametrize('vopros,chto', [
    ('Люся, где у нас мотопомпа?', 'мотопомпа'),
    ('где тепловая пушка', 'тепловая пушка'),
    ('где лежит сварочник', 'сварочник'),
])
def test_vopros_pro_veshch(vopros, chto):
    assert inventory.chto_ishchut(vopros) == chto


@pytest.mark.parametrize('vopros', [
    'где ты была?',
    'где все?',
    'ты где',
])
def test_vopros_ne_pro_veshch(vopros):
    assert inventory.chto_ishchut(vopros) is None


# ---------- Запись и ответ ----------

async def test_zapis_iz_chata_lozhitsya_v_opis():
    text = 'в инвентарь: мотопомпа, подвал, Седова 71'
    e = event(text)

    vzyala = await H.handle_inventory(e, text, 100)

    assert vzyala is True
    veshchi = db.list_items()
    assert len(veshchi) == 1
    assert veshchi[0]['name'] == 'мотопомпа'
    assert veshchi[0]['place'] == 'подвал'
    assert veshchi[0]['house_id'] == houses.detect_house('Седова 71')['id']
    assert 'Седова 71' in e.message.sent[-1]


async def test_glavnyy_sluchay_gde_motopompa():
    """Тот самый случай: парковку топит, а где мотопомпа — никто не знает."""
    db.add_item('мотопомпа Хонда', 'подвал', houses.detect_house('Седова 71')['id'],
                user_name='Андрей')

    e = event('Люся, где у нас мотопомпа?')
    otvetila = await H.handle_where(e, e.message.body.text, 100)

    assert otvetila is True
    otvet = e.message.sent[-1]
    assert 'мотопомпа Хонда' in otvet
    assert 'Седова 71' in otvet
    assert 'подвал' in otvet


async def test_chego_net_v_opisi_molchim():
    """Не нашли — вопрос идёт дальше по цепочке, а не тонет в «не знаю»."""
    e = event('где мотопомпа')

    assert await H.handle_where(e, 'где мотопомпа', 100) is False
    assert e.message.sent == []


async def test_vopros_pro_cheloveka_ne_perehvatyvaem():
    db.add_item('мотопомпа', 'подвал', None, user_name='Андрей')
    e = event('а где Костя сейчас')

    assert await H.handle_where(e, 'а где Костя сейчас', 100) is False


# ---------- Экраны ----------

async def test_opis_v_bystrom_menyu():
    assert any(name == 'опись' for name, _, _ in H.QUICK_COMMANDS)
    payload = next(p for name, _, p in H.QUICK_COMMANDS if name == 'опись')
    assert payload == 'inv'


async def test_ekran_opisi_gruppiruet_po_adresu():
    dom = houses.detect_house('Седова 71')
    db.add_item('мотопомпа', 'подвал', dom['id'], user_name='Андрей')
    db.add_item('вышка-тура', 'склад', None, user_name='Андрей')

    msg = Msg('')
    await H.run_action('inv', msg, 100, event(''))

    otvet = msg.sent[-1]
    assert 'Седова 71' in otvet
    assert 'мотопомпа' in otvet
    assert 'Без адреса' in otvet
    assert 'вышка-тура' in otvet


async def test_veshch_pereezzhaet():
    dom = houses.detect_house('Седова 71')
    item_id = db.add_item('мотопомпа', 'подвал', dom['id'], user_name='Андрей')
    H.STATE[100] = {'mode': 'inv_move', 'item_id': item_id}

    e = event('бытовка на Трилиссера 22')
    await H.resume_inventory(e, 'бытовка на Трилиссера 22', 100, H.STATE[100])

    it = db.get_item(item_id)
    assert it['house_id'] == houses.detect_house('Трилиссера 22')['id']
    assert it['place'] == 'бытовка'
    assert 100 not in H.STATE


async def test_spisannoe_ne_ischet():
    item_id = db.add_item('мотопомпа', 'подвал', None, user_name='Андрей')
    db.write_off_item(item_id)

    e = event('где мотопомпа')
    assert await H.handle_where(e, 'где мотопомпа', 100) is False


async def test_opis_vidna_v_pasporte_doma():
    dom = houses.detect_house('Седова 71')
    db.add_item('мотопомпа', 'подвал', dom['id'], user_name='Андрей')

    text = H.passport_text(dom)

    assert 'ЗДЕСЬ ЛЕЖИТ' in text
    assert 'мотопомпа' in text
