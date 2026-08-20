"""Название и номер счётчика вводятся одной строкой.

Заказчик: «Очень трудно вводить название и номер счётчика. Всё пишется
в название». Так и было: на вопрос о названии он написал «ВСХд-15
Номер: 64380455», и вся строка целиком стала названием — а поле
заводского номера осталось пустым.
"""
import types

import pytest

from bot import db, houses
from bot import handlers as H
from bot.handlers import CLEAR, split_name_serial


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(H, 'DOCS_DIR', str(tmp_path / 'docs'))
    db.init()


@pytest.fixture
def dom():
    return next(h for h in houses.ALL_HOUSES if h['address'] == 'Седова 71')


@pytest.fixture
def schyotchik(dom):
    return db.add_meter(dom['id'], 'hvs', 'ХВС', 'Андрей')


class Msg:
    """Куда Люся отвечает."""

    def __init__(self):
        self.sent = []
        self.keyboards = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')
        self.keyboards.append(attachments)

    @property
    def text(self):
        return '\n'.join(self.sent)


@pytest.mark.parametrize('stroka, nazvanie, nomer', [
    ('ВСХд-15 Номер: 64380455', 'ВСХд-15', '64380455'),      # случай заказчика
    ('ВСХд-15 № 64380455', 'ВСХд-15', '64380455'),
    ('ХВС подвал 64380455', 'ХВС подвал', '64380455'),
    ('СТВ-50 №12345678, домовой', 'СТВ-50, домовой', '12345678'),
    ('Тепло УТ-1 №2024/1567', 'Тепло УТ-1', '2024/1567'),
])
def test_odna_stroka_delitsya_na_nazvanie_i_nomer(stroka, nazvanie, nomer):
    assert split_name_serial(stroka) == (nazvanie, nomer)


def test_odni_tsifry_eto_nomer():
    assert split_name_serial('64380455') == (None, '64380455')


def test_korotkie_tsifry_v_nazvanii_ne_nomer():
    """«ВСХд-15» — это марка прибора, а не заводской номер."""
    assert split_name_serial('ВСХд-15') == ('ВСХд-15', None)
    assert split_name_serial('ХВС ввод в подвале') == ('ХВС ввод в подвале', None)


def test_procherk_ochischaet_nomer():
    assert split_name_serial('-') == (None, CLEAR)


async def test_nomer_uhodit_v_svoyo_pole_a_ne_v_nazvanie(schyotchik):
    msg = Msg()

    await H.apply_meter_edit(msg, schyotchik, 'ВСХд-15 Номер: 64380455')

    m = db.get_meter(schyotchik)
    assert m['label'] == 'ВСХд-15'
    assert m['serial'] == '64380455'


async def test_lusya_govorit_chto_kuda_zapisala(schyotchik):
    msg = Msg()

    await H.apply_meter_edit(msg, schyotchik, 'ВСХд-15 № 64380455')

    assert 'ВСХд-15' in msg.text and '64380455' in msg.text
    assert 'Исправить' in str(msg.keyboards[-1]), 'разбор мог ошибиться'


async def test_odno_nazvanie_ne_stiraet_nomer(schyotchik):
    db.update_meter(schyotchik, serial='64380455')

    await H.apply_meter_edit(Msg(), schyotchik, 'ХВС домовой')

    m = db.get_meter(schyotchik)
    assert m['label'] == 'ХВС домовой'
    assert m['serial'] == '64380455', 'номер не трогали — он и не должен пропасть'


async def test_odni_tsifry_ne_stirayut_nazvanie(schyotchik):
    msg = Msg()

    await H.apply_meter_edit(msg, schyotchik, '64380455')

    m = db.get_meter(schyotchik)
    assert m['label'] == 'ХВС', 'название прежнее'
    assert m['serial'] == '64380455'
    assert 'ХВС' in msg.text, 'сказано, что название оставлено прежним'


async def test_procherk_ubiraet_nomer(schyotchik):
    db.update_meter(schyotchik, serial='64380455')

    await H.apply_meter_edit(Msg(), schyotchik, '-')

    assert db.get_meter(schyotchik)['serial'] is None


async def test_pustoy_otvet_nichego_ne_portit(schyotchik):
    msg = Msg()

    await H.apply_meter_edit(msg, schyotchik, '   ')

    m = db.get_meter(schyotchik)
    assert m['label'] == 'ХВС' and m['serial'] is None
    assert 'Не поняла' in msg.text


async def test_novyy_schyotchik_zavoditsya_s_nomerom_srazu(dom):
    """Раньше «ВСХд-15 № 64380455» целиком становилось названием прибора."""
    label, serial = split_name_serial('ВСХд-15 в подвале № 64380455')

    assert label == 'ВСХд-15 в подвале'
    assert serial == '64380455'


# ---------- Кнопки: до правки надо ещё добраться ----------

class Event:
    """Нажатие кнопки или сообщение в личку."""

    def __init__(self, text=''):
        self.msg = Msg()
        self.msg.body = types.SimpleNamespace(text=text, attachments=None,
                                              mid='m', markup=None)
        self.msg.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.msg.recipient = types.SimpleNamespace(user_id=100, chat_id=None,
                                                   chat_type='dialog')
        self.message = self.msg
        self.callback = types.SimpleNamespace(
            user=types.SimpleNamespace(user_id=100, full_name='Андрей'))

    @property
    def text(self):
        return self.msg.text


def knopki(event):
    """Тексты и адреса всех кнопок последнего ответа."""
    markup = event.msg.keyboards[-1]
    if not markup:
        return []
    return [(b.text, b.payload) for row in markup[0].payload.buttons for b in row]


async def test_v_kartochke_odna_knopka_pravki_a_ne_dve(schyotchik):
    """Две кнопки — два вопроса и два ответа. На телефоне это долго."""
    e = Event()
    await H.run_action(f'mtc:{schyotchik}', e.msg, 100, e)

    pravka = [t for t, p in knopki(e) if p.startswith('mted')]
    assert pravka == ['✏️ Название и номер']
    assert not [p for _, p in knopki(e) if p.startswith(('mtren', 'mtsn'))]


@pytest.mark.parametrize('staraya', ['mtren', 'mtsn'])
async def test_starye_knopki_iz_lenty_prodolzhayut_rabotat(schyotchik, staraya):
    """Прежние кнопки висят в переписке у людей — нажатие не должно молчать."""
    e = Event()
    await H.run_action(f'{staraya}:{schyotchik}', e.msg, 100, e)

    assert e.text.strip()
    assert H.STATE[100]['mode'] == 'meter_edit'


async def test_otvet_na_vopros_o_nazvanii_delitsya_kak_nado(schyotchik):
    """Целиком тот случай, что был у заказчика: кнопка → строка → два поля."""
    e = Event()
    await H.run_action(f'mtren:{schyotchik}', e.msg, 100, e)

    otvet = Event('ВСХд-15 Номер: 64380455')
    await H.on_text(otvet)

    m = db.get_meter(schyotchik)
    assert (m['label'], m['serial']) == ('ВСХд-15', '64380455')


async def test_vozvrat_k_poslednemu_schyotchiku_pervoy_knopkoy(schyotchik, dom):
    """Карточка уезжает вверх по ленте — искать её глазами не надо."""
    db.upsert_user(100, 'Андрей')     # как при любом обращении к боту
    e = Event()
    await H.run_action(f'mtc:{schyotchik}', e.msg, 100, e)

    pick = Event()
    await H.run_action('mtpick', pick.msg, 100, pick)

    pervaya = knopki(pick)[0]
    assert pervaya[1] == f'mtc:{schyotchik}'
    assert 'ХВС' in pervaya[0] and dom['address'] in pervaya[0]


async def test_bez_istorii_knopki_vozvrata_net():
    db.upsert_user(100, 'Андрей')
    pick = Event()
    await H.run_action('mtpick', pick.msg, 100, pick)

    assert not [p for _, p in knopki(pick) if p.startswith('mtc:')]


async def test_staryy_schyotchik_chinitsya_odnim_nazhatiem(dom):
    """У заказчика номер уже сидит в названии — вынести его должно быть просто."""
    m_id = db.add_meter(dom['id'], 'hvs', 'ВСХд-15 Номер: 64380455', 'Андрей')

    e = Event()
    await H.run_action(f'mtc:{m_id}', e.msg, 100, e)
    fix = [p for _, p in knopki(e) if p.startswith('mtfix')]
    assert fix == [f'mtfix:{m_id}']

    e2 = Event()
    await H.run_action(fix[0], e2.msg, 100, e2)

    m = db.get_meter(m_id)
    assert (m['label'], m['serial']) == ('ВСХд-15', '64380455')


async def test_normalnomu_schyotchiku_knopka_pochinki_ne_nuzhna(schyotchik):
    e = Event()
    await H.run_action(f'mtc:{schyotchik}', e.msg, 100, e)

    assert not [p for _, p in knopki(e) if p.startswith('mtfix')]
