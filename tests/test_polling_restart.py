"""Опрос MAX переживает обрывы: цикл поднимается сам, процесс не падает."""
import asyncio

import pytest

from bot import main as M


@pytest.fixture(autouse=True)
def bystro(monkeypatch):
    """Без реальных пауз — иначе тест ждал бы минутами."""
    slept = []

    async def fake_sleep(sec):
        slept.append(sec)

    monkeypatch.setattr(M.asyncio, 'sleep', fake_sleep)
    return slept


async def run_until(monkeypatch, outcomes):
    """Гоняет poll_forever, пока не кончатся заготовленные исходы опроса."""
    calls = []

    async def fake_polling(bot):
        i = len(calls)
        calls.append(i)
        if i >= len(outcomes):
            raise asyncio.CancelledError
        result = outcomes[i]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(M.dp, 'start_polling', fake_polling)
    with pytest.raises(asyncio.CancelledError):
        await M.poll_forever(object())
    return calls


async def test_posle_oshibki_opros_prodolzhaetsya(monkeypatch, bystro):
    calls = await run_until(monkeypatch, [RuntimeError('связь оборвалась')] * 3)
    assert len(calls) == 4, 'после каждой ошибки опрос запускается заново'


async def test_pauza_rastyot_no_ne_beskonechno(monkeypatch, bystro):
    await run_until(monkeypatch, [RuntimeError('нет сети')] * 8)
    assert bystro[0] == M.MIN_DELAY
    assert bystro == sorted(bystro), 'пауза только растёт'
    assert max(bystro) <= M.MAX_DELAY


async def test_posle_dolgoy_raboty_pauza_sbrasyvaetsya(monkeypatch, bystro):
    """Проработал дольше STABLE — связь была, начинаем отсчёт заново."""
    t = {'now': 0.0}
    monkeypatch.setattr(M.time, 'monotonic', lambda: t['now'])

    async def long_then_fail(bot):
        t['now'] += M.STABLE + 1
        raise RuntimeError('обрыв после долгой работы')

    monkeypatch.setattr(M.dp, 'start_polling', long_then_fail)

    async def stop_after_three(sec):
        bystro.append(sec)
        if len(bystro) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(M.asyncio, 'sleep', stop_after_three)
    with pytest.raises(asyncio.CancelledError):
        await M.poll_forever(object())

    assert bystro == [M.MIN_DELAY] * 3, 'пауза не разгоняется, если бот работал'


async def test_ostanovka_ne_perekhvatyvaetsya(monkeypatch, bystro):
    """CancelledError — это штатная остановка, её глушить нельзя."""
    async def cancelled(bot):
        raise asyncio.CancelledError

    monkeypatch.setattr(M.dp, 'start_polling', cancelled)
    with pytest.raises(asyncio.CancelledError):
        await M.poll_forever(object())
    assert bystro == [], 'на остановке пауз не делаем'
