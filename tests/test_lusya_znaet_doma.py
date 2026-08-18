"""Люся не может заявить, что дома нет, если он есть.

Модель однажды ответила «проверила — ничего не нашлось», не обратившись ни
к одному инструменту: рассуждала о наличии дома по памяти. Список домов
теперь стоит прямо в подсказке, выдумывать больше нечего.
"""
import pytest

from bot import agent, houses


def test_vse_doma_perechisleny_v_podskazke():
    assert houses.HOUSES, 'список домов пуст — подсказке нечего показывать'
    for h in houses.HOUSES:
        assert h['address'] in agent.SYSTEM_PROMPT, f'{h["address"]} нет в подсказке'


def test_spornyy_dom_na_meste():
    assert '4-я Советская 30' in agent.SYSTEM_PROMPT


def test_id_domov_ryadom_s_adresami():
    """id нужен остальным инструментам — иначе будет лишний круг к модели."""
    h = houses.HOUSES[0]
    assert f"{h['address']} (id {h['id']})" in agent.SYSTEM_PROMPT


def test_podskazka_zapreschaet_otricat_nalichie_doma():
    p = agent.SYSTEM_PROMPT
    assert 'Никогда не говори, что дома нет' in p
    assert 'других у нас нет' in p


def test_podskazka_obyasnyaet_razgovornye_nazvaniya():
    p = agent.SYSTEM_PROMPT
    assert 'тридцатый дом' in p, 'номер словом'
    assert 'ЖК' in p, 'название комплекса — не адрес'


def test_podskazka_ne_razdulas():
    """86 адресов — меньше двух килобайт, это допустимая плата за надёжность."""
    assert len(agent.SYSTEM_PROMPT) < 6000


async def test_vyzovy_instrumentov_popadayut_v_log(monkeypatch, caplog):
    """Иначе «модель соврала» и «инструмент не нашёл» неразличимы в логах."""
    import json
    import logging

    calls = iter([
        {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'c1', 'type': 'function', 'function': {
                'name': 'find_house', 'arguments': json.dumps({'query': '4-я Советская 30'})}}]},
        {'role': 'assistant', 'content': 'Нашла: 4-я Советская 30.'},
    ])

    async def fake_chat(messages, tools=None, max_tokens=900, temperature=0.4):
        return next(calls)

    async def noop(*a, **k):
        pass

    monkeypatch.setattr(agent.ai, 'enabled', lambda: True)
    monkeypatch.setattr(agent.ai, 'chat', fake_chat)
    monkeypatch.setattr(agent, '_update_profile', noop)
    monkeypatch.setattr(agent.db, 'add_chat_message', lambda *a, **k: None)
    monkeypatch.setattr(agent.db, 'get_user_notes', lambda uid: '')
    monkeypatch.setattr(agent.db, 'recent_chat_history', lambda uid, limit=6: [])

    with caplog.at_level(logging.INFO, logger='agent'):
        await agent.answer(1, 'Андрей', 'где 4-я Советская 30')

    assert 'find_house' in caplog.text
    assert '4-я Советская 30' in caplog.text


def test_adres_v_spiske_idyot_pervym():
    """Строка вида «28 — 4-я Советская 30» читалась как «дом 28»: Люся
    принимала служебный id за номер дома и путала адреса между собой."""
    for line in agent._houses_block().split('\n'):
        assert not line[0].isdigit() or line.startswith('4-я'), (
            f'строка начинается со служебного номера: {line!r}')
        assert line.endswith(')'), f'id не помечен как служебный: {line!r}'


def test_pro_id_skazano_pryamo():
    p = agent.SYSTEM_PROMPT
    assert 'НЕ номер дома' in p
    assert 'вслух его не называй' in p


def test_kazhdyy_dom_uznayotsya_po_svoemu_adresu():
    """Проверка от обратного: по адресу из подсказки находится ровно он."""
    for h in houses.HOUSES:
        found = houses.search(h['address'])
        assert found and found[0]['address'] == h['address'], (
            f"{h['address']} нашёлся как {found[0]['address'] if found else '—'}")
