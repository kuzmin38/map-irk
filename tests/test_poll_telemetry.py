"""Запросы к MAX видны в логах: молчание опроса больше не безымянное."""
import pytest

from bot import main as M
from bot import status


@pytest.fixture(autouse=True)
def chisto():
    status.STATE.update(fetches=0, last_fetch_at=None, events=0,
                        fetch_error=None, fetch_error_at=None, updates=0,
                        polls=0, bot_username=None, bot_id=None, me_error=None,
                        last_error=None, last_error_at=None)


class FakeBot:
    """Бот, у которого get_updates отвечает заготовленным."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def get_updates(self, marker=None):
        self.calls.append(marker)
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


async def test_otvety_max_schitayutsya():
    bot = FakeBot({'updates': [1, 2], 'marker': 7}, {'updates': [], 'marker': 8})
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


async def test_marker_dohodit_do_biblioteki():
    """Обёртка не должна съедать аргументы — иначе события пойдут по кругу."""
    bot = FakeBot({'updates': [], 'marker': 5})
    M.watch_updates(bot)

    await bot.get_updates(marker=42)

    assert bot.calls == [42]


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
