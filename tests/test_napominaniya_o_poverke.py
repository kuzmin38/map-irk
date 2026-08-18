"""Кого и когда предупреждать о поверке манометров.

Заказчик: руководителю уведомление о наступающей поверке не нужно — это
забота инженера. Предупреждать надо весной, до летней сдачи тепловых узлов,
обо всём, что просрочится в этом году. Руководитель узнаёт только о том,
что срок уже вышел, а замены не было.
"""
from datetime import date

import pytest

from bot import db, houses, reminders

ADRES = '4-я Советская 30'


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    for uid, role in ((1, 'engineer'), (2, 'director'), (3, 'master'),
                      (4, 'plumber'), (5, 'admin')):
        db.upsert_user(uid, f'Пользователь {uid}')
        db.set_user_role(uid, role)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, user_id, text):
        self.sent.append((user_id, text))

    def komu(self):
        return {uid for uid, _ in self.sent}

    def text(self):
        return '\n'.join(t for _, t in self.sent)


def manometr(poverka_do, serial='04517'):
    dom = next(h for h in houses.ALL_HOUSES if h['address'] == ADRES)
    point_id = db.add_point(dom['id'], 'домовой контур, подача', 'ИТП', 'Андрей')
    return db.add_device(point_id, serial, poverka_do, 1, 'Андрей')


async def test_vesnoy_preduprezhdaem_itr_bez_rukovoditelya():
    manometr('2028-07-31')
    bot = FakeBot()

    await reminders._check_verifications(bot, date(2028, 4, 15))

    assert bot.komu() == {1, 3, 5}, 'инженер, мастер, админ — руководителя нет'
    assert '2' not in str(bot.komu())
    assert 'В ЭТОМ ГОДУ' in bot.text()
    assert '31.07.2028' in bot.text()


async def test_rukovoditel_uznayot_tolko_o_prosrochke():
    manometr('2028-07-31')
    bot = FakeBot()

    await reminders._check_verifications(bot, date(2028, 9, 1))

    assert 2 in bot.komu(), 'руководителю про просрочку сообщаем'
    assert 3 not in bot.komu(), 'мастера просрочкой не грузим'
    assert 'ПРОСРОЧЕНА' in bot.text()
    assert 'просрочена на 32 дн.' in bot.text()


async def test_letom_zaranee_ne_dyorgaem():
    """Не апрель и не май, до срока далеко — повода писать нет."""
    manometr('2028-07-31')
    bot = FakeBot()

    await reminders._check_verifications(bot, date(2028, 1, 20))

    assert bot.sent == []


async def test_vesnoy_o_budushchem_gode_ne_pishem():
    """Весной 2027-го поверка 2028 года ещё не забота этого лета."""
    manometr('2028-07-31')
    bot = FakeBot()

    await reminders._check_verifications(bot, date(2027, 4, 15))

    assert bot.sent == []


async def test_pribor_poyavivshiysya_posle_vesny_ne_teryaetsya():
    """Подстраховка: срок близко, а апрель с маем уже прошли."""
    manometr('2028-08-20')
    bot = FakeBot()

    await reminders._check_verifications(bot, date(2028, 8, 1))

    assert bot.komu() == {1, 3, 5}
    assert 'осталось 19 дн.' in bot.text()


async def test_povtorno_v_tot_zhe_den_ne_pishem():
    manometr('2028-07-31')
    bot = FakeBot()

    await reminders._check_verifications(bot, date(2028, 4, 15))
    poslano = len(bot.sent)
    await reminders._check_verifications(bot, date(2028, 4, 16))

    assert len(bot.sent) == poslano, 'в апреле напоминаем раз, а не каждый день'


async def test_prosrochka_napominaetsya_raz_v_nedelyu():
    manometr('2028-07-31')
    bot = FakeBot()

    await reminders._check_verifications(bot, date(2028, 9, 1))
    poslano = len(bot.sent)
    await reminders._check_verifications(bot, date(2028, 9, 5))
    assert len(bot.sent) == poslano, 'через 4 дня молчим'

    await reminders._check_verifications(bot, date(2028, 9, 8))
    assert len(bot.sent) > poslano, 'через неделю напоминаем снова'


async def test_pribor_bez_sroka_ne_meshaet():
    manometr(None)
    bot = FakeBot()

    await reminders._check_verifications(bot, date(2028, 4, 15))

    assert bot.sent == []
