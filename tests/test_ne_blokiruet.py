"""Долгий ответ ИИ не подвешивает бота для всех остальных.

Диспетчер разбирал события по одному прямо в цикле опроса: пока Люся думала
над вопросом к модели, она не спрашивала MAX о новых сообщениях. Один запрос
к ИИ мог занять до шести минут — всё это время бот молчал для всего звена.
"""
import asyncio

import pytest

from bot import agent, ai, db
from bot.handlers import dp


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    monkeypatch.setattr(agent.ai, 'enabled', lambda: True)


def test_sobytiya_obrabatyvayutsya_parallelno():
    """Главная защита: без этого один медленный ответ держит весь опрос."""
    assert dp.use_create_task is True


def test_zapros_k_modeli_ne_zhdyot_polutora_minut():
    assert ai.REQUEST_TIMEOUT <= 30


def test_ves_razgovor_ogranichen_po_vremeni():
    """Кругов четыре, и без общего предела они складываются в минуты."""
    assert agent.BUDGET < agent.MAX_ROUNDS * ai.REQUEST_TIMEOUT


async def test_zavisshaya_model_ne_derzhit_polzovatelya(monkeypatch):
    """Модель молчит — Люся сдаётся сама, а не ждёт до последнего."""
    monkeypatch.setattr(agent, 'BUDGET', 0.05)

    async def zavisla(messages, tools=None, max_tokens=900, temperature=0.4):
        await asyncio.sleep(10)
        return {'role': 'assistant', 'content': 'слишком поздно'}

    monkeypatch.setattr(agent.ai, 'chat', zavisla)

    result = await asyncio.wait_for(
        agent.answer(1, 'Андрей', 'что по нормативам ГВС?'), timeout=2)

    assert result is None, 'None — сигнал ответить заготовкой, а не молчать'


async def test_ischerpannyy_budzhet_ne_nachinaet_novyy_krug(monkeypatch):
    """Время вышло между кругами — новый запрос к модели не уходит."""
    monkeypatch.setattr(agent, 'BUDGET', -1)
    calls = []

    async def fake_chat(messages, tools=None, max_tokens=900, temperature=0.4):
        calls.append(messages)
        return {'role': 'assistant', 'content': 'ответ'}

    monkeypatch.setattr(agent.ai, 'chat', fake_chat)

    assert await agent.answer(1, 'Андрей', 'вопрос') is None
    assert calls == [], 'к модели вообще не обращались'
