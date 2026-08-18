"""Общие условия для тестов.

Логика бота — поиск, распознавание адреса, инструменты агента — не должна
зависеть от того, какие дома у заказчика сейчас в работе: это настройка,
она меняется. Поэтому тесты видят весь справочник целиком.
"""
import pytest

from bot import agent, houses


@pytest.fixture(autouse=True)
def vse_doma(request, monkeypatch):
    # Тесты самого ограничения перезагружают модуль сами — им не мешаем
    if request.module.__name__.endswith('test_active_houses'):
        yield
        return

    monkeypatch.setattr(houses, 'ACTIVE', set())
    monkeypatch.setattr(houses, 'HOUSES', list(houses.ALL_HOUSES))
    monkeypatch.setattr(houses, 'HOUSES_BY_ID',
                        {h['id']: h for h in houses.ALL_HOUSES})
    monkeypatch.setattr(agent, 'SYSTEM_PROMPT', agent._build_prompt())
    yield
