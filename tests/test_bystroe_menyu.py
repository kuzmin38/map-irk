"""Быстрое меню: команды под полем ввода вместо поиска кнопок в ленте.

Заказчик: «нужна какая-то кнопка быстрого меню, чтобы мне не крутить ленту
и не искать вообще, где эти дома, где эти счётчики». Клавиатура в MAX
привязана к сообщению и уезжает вверх вместе с ним; команды же всегда
лежат под полем ввода.
"""
import re
import types

import pytest

from bot import db, main
from bot import handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


class Event:
    """Сообщение «/schet» от сантехника в личке."""

    def __init__(self, text):
        self.sent = []
        self.keyboards = []
        outer = self

        class Msg:
            body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
            sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
            recipient = types.SimpleNamespace(user_id=100, chat_id=None, chat_type='dialog')

            async def answer(self, text=None, attachments=None):
                outer.sent.append(text or '')
                outer.keyboards.append(attachments)

        self.message = Msg()

    @property
    def text(self):
        return '\n'.join(self.sent)


def handler_for(name):
    """Обработчик, который MAX вызовет на команду /name."""
    for h in H.dp.event_handlers:
        if h.update_type != 'message_created':
            continue
        if any(name in getattr(f, 'commands', []) for f in h.base_filters):
            return h.func_event
    return None


async def test_kazhdaya_komanda_vedyot_na_sushchestvuyushchiy_ekran():
    """Команда без экрана молча ничего не покажет — это хуже её отсутствия."""
    for name, _, payload in H.QUICK_COMMANDS:
        e = Event(f'/{name}')
        await H.run_action(payload, e.message, 100, e)

        assert e.text.strip(), f'/{name} ведёт в никуда'


def test_imena_komand_ne_povtoryayutsya_i_prigodny_dlya_max():
    names = [n for n, _, _ in H.QUICK_COMMANDS]

    assert len(names) == len(set(names))
    assert len(names) <= 32, 'MAX принимает не больше 32 команд'
    for name in names:
        assert re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name), name


def test_u_kazhdoy_komandy_est_opisanie():
    """В меню MAX человек видит описание, а не имя команды."""
    for name, text, _ in H.QUICK_COMMANDS:
        assert text and len(text) <= 128, name


async def test_komanda_otkryvaet_tot_zhe_ekran_chto_i_knopka():
    handler = handler_for('schet')
    assert handler, 'команда /schet не зарегистрирована'

    e = Event('/schet')
    await handler(e)

    assert 'дом' in e.text.lower()
    assert e.keyboards[-1], 'экран пришёл с кнопками, а не голым списком'


async def test_komanda_doma_pokazyvaet_zhk():
    e = Event('/doma')
    await handler_for('doma')(e)

    assert 'ЖК' in e.text


async def test_vse_komandy_krome_menyu_zaregistrirovany():
    """/menu уже висел на своём обработчике — второй раз вешать нечего."""
    for name, _, _ in H.QUICK_COMMANDS:
        assert handler_for(name), f'/{name} не зарегистрирована'


async def test_komandy_uhodyat_v_max_pri_zapuske():
    отправлено = []

    class FakeBot:
        async def set_commands(self, *commands):
            отправлено.extend(commands)

    await main.register_commands(FakeBot())

    assert [c.name for c in отправлено] == [n for n, _, _ in H.QUICK_COMMANDS]
    assert [c.description for c in отправлено] == [t for _, t, _ in H.QUICK_COMMANDS]


async def test_otkaz_max_ne_ronyaet_bota():
    """Меню — удобство. Без него бот обязан работать, команды наберутся руками."""
    class FakeBot:
        async def set_commands(self, *commands):
            raise RuntimeError('MAX недоступен')

    await main.register_commands(FakeBot())   # не должно бросить
