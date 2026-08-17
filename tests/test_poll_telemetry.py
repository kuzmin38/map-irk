"""Запросы к MAX видны в логах: молчание опроса больше не безымянное."""
import logging

import pytest

from bot import main as M
from bot import status


@pytest.fixture(autouse=True)
def chisto():
    status.STATE.update(fetches=0, last_fetch_at=None, events=0,
                        fetch_error=None, fetch_error_at=None, updates=0,
                        polls=0, bot_username=None, bot_id=None, me_error=None,
                        last_error=None, last_error_at=None, instant=0)


class FakeBot:
    """Бот, у которого get_updates отвечает заготовленным.

    Сигнатура повторяет настоящую: обёртка подставляет timeout, и заглушка
    обязана его принимать — иначе тест разошёлся бы с боевым кодом.
    """

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def get_updates(self, limit=None, timeout=None, marker=None, types=None):
        self.calls.append(marker)
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


async def test_otvety_max_schitayutsya():
    bot = FakeBot({'updates': [{'update_type': 'message_created'},
                               {'update_type': 'message_callback'}], 'marker': 7},
                  {'updates': [], 'marker': 8})
    M.watch_updates(bot)

    await bot.get_updates(marker=None)
    await bot.get_updates(marker=7)

    assert status.STATE['fetches'] == 2
    assert status.STATE['events'] == 2, 'считаем сами события, а не ответы'
    assert status.STATE['last_fetch_at']


async def test_otvet_bez_polya_updates_ne_lomaet_schyot():
    """MAX может ответить пустым объектом — это тоже ответ, а не ошибка."""
    bot = FakeBot({})
    M.watch_updates(bot)

    assert await bot.get_updates() == {}
    assert status.STATE['fetches'] == 1
    assert status.STATE['events'] == 0


async def test_oshibka_zaprosa_zapominaetsya_i_probrasyvaetsya():
    """Ошибку глушить нельзя: её разбирает сама библиотека."""
    bot = FakeBot(ConnectionError('MAX недоступен'))
    M.watch_updates(bot)

    with pytest.raises(ConnectionError):
        await bot.get_updates()

    assert 'MAX недоступен' in status.STATE['fetch_error']
    assert status.STATE['fetch_error_at']


async def test_zapros_uhodit_s_yavnym_timeoutom():
    """Без него MAX иногда отвечает мгновенно, цикл разгоняется и ловит 429."""
    poluchennye = {}

    class Bot:
        async def get_updates(self, marker=None, timeout=None):
            poluchennye.update(marker=marker, timeout=timeout)
            return {'updates': []}

    bot = Bot()
    M.watch_updates(bot)
    await bot.get_updates(marker=3)

    assert poluchennye['timeout'] == M.POLL_TIMEOUT
    assert poluchennye['marker'] == 3, 'свой timeout не вытесняет маркер'


async def test_svoy_timeout_ne_perebivaetsya():
    poluchennye = {}

    class Bot:
        async def get_updates(self, timeout=None):
            poluchennye['timeout'] = timeout
            return {'updates': []}

    bot = Bot()
    M.watch_updates(bot)
    await bot.get_updates(timeout=5)

    assert poluchennye['timeout'] == 5


async def test_zaprosy_ne_chastyat(monkeypatch):
    """У MAX предел пять запросов в секунду, и каждый 429 стоит пяти секунд."""
    pauzy = []

    async def fake_sleep(sec):
        pauzy.append(sec)

    monkeypatch.setattr(M.asyncio, 'sleep', fake_sleep)
    chasy = {'now': 100.0}
    monkeypatch.setattr(M.time, 'monotonic', lambda: chasy['now'])

    bot = FakeBot({'updates': []}, {'updates': []})
    M.watch_updates(bot)

    await bot.get_updates()
    await bot.get_updates()  # сразу следом, время не сдвинулось

    assert pauzy and pauzy[-1] == pytest.approx(M.MIN_INTERVAL)


async def test_posle_pauzy_zapros_uhodit_srazu(monkeypatch):
    pauzy = []

    async def fake_sleep(sec):
        pauzy.append(sec)

    monkeypatch.setattr(M.asyncio, 'sleep', fake_sleep)
    chasy = {'now': 100.0}
    monkeypatch.setattr(M.time, 'monotonic', lambda: chasy['now'])

    bot = FakeBot({'updates': []}, {'updates': []})
    M.watch_updates(bot)

    await bot.get_updates()
    chasy['now'] += 30  # долгое ожидание MAX — тормозить незачем
    await bot.get_updates()

    assert pauzy == [], 'лишних пауз между редкими запросами нет'


async def test_marker_dohodit_do_biblioteki():
    """Обёртка не должна съедать аргументы — иначе события пойдут по кругу."""
    bot = FakeBot({'updates': [], 'marker': 5})
    M.watch_updates(bot)

    await bot.get_updates(marker=42)

    assert bot.calls == [42]


async def test_sobytiya_vidny_v_loge_do_razbora(caplog):
    """Событие неизвестного типа библиотека пропустит молча — здесь оно видно."""
    bot = FakeBot({'updates': [{'update_type': 'message_created'}], 'marker': 9})
    M.watch_updates(bot)

    with caplog.at_level(logging.INFO, logger='bot'):
        await bot.get_updates()

    assert 'MAX прислал событий: 1' in caplog.text
    assert 'message_created' in caplog.text
    assert 'маркер 9' in caplog.text


async def test_strannyy_otvet_ne_ronyaet_opros(caplog):
    """Ради строчки в логе опрос падать не должен, что бы MAX ни прислал."""
    bot = FakeBot({'updates': [42, None], 'marker': 1})
    M.watch_updates(bot)

    with caplog.at_level(logging.INFO, logger='bot'):
        assert await bot.get_updates() == {'updates': [42, None], 'marker': 1}

    assert 'MAX прислал событий: 2' in caplog.text


def test_pervyy_otvet_otmechaetsya_odin_raz():
    assert status.note_fetch(0) is True
    assert status.note_fetch(0) is False


def test_stranica_razlichaet_zavisshiy_zapros_i_tishinu():
    status.note_me('lusya_bot', 42)
    status.note_poll_start()

    zavis = status.report('сборка', None, 'работает')
    assert 'НИ ОДНОГО' in zavis
    assert 'ни разу не ответил' in zavis

    status.note_fetch(0)
    tishina = status.report('сборка', None, 'работает')
    assert 'ни разу не ответил' not in tishina
    assert 'второй запущенный экземпляр' in tishina
    assert '@username' in tishina, 'подсказка сверить бота, которому пишут'


def test_sobytiya_idut_a_do_bota_ne_dohodyat():
    status.note_me('lusya_bot', 42)
    status.note_fetch(3)

    out = status.report('сборка', None, 'работает')
    assert 'до обработчиков не доходят' in out


def test_puls_soderzhit_glavnye_chisla():
    status.note_fetch(2)
    status.note_update('личка')

    out = status.pulse()
    assert 'ответов MAX 1' in out
    assert 'событий 2' in out
    assert 'дошло до бота 1' in out
