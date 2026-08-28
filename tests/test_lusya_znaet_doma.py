"""Люся не может заявить, что дома нет, если он есть.

Модель однажды ответила «проверила — ничего не нашлось», не обратившись ни
к одному инструменту: рассуждала о наличии дома по памяти. Список домов
теперь стоит прямо в подсказке, выдумывать больше нечего.
"""
import re

import pytest

from bot import agent, houses


def test_vse_doma_perechisleny_v_podskazke():
    assert houses.HOUSES, 'список домов пуст — подсказке нечего показывать'
    for h in houses.HOUSES:
        assert h['address'] in agent.SYSTEM_PROMPT, f'{h["address"]} нет в подсказке'


def test_spornyy_dom_na_meste():
    assert '4-я Советская 30' in agent.SYSTEM_PROMPT


def test_sluzhebnyh_nomerov_ryadom_s_adresom_net():
    """Любой номер рядом с адресом Люся принимает за номер дома.

    Сначала список был «28 — 4-я Советская 30», потом «4-я Советская 30
    (id 28)» — оба раза она отвечала «дом 28 — это Советская, 30».
    Теперь в списке только адреса, id берётся через find_house.
    """
    block = agent._houses_block()
    assert '(id' not in block
    for line in block.split('\n'):
        assert line == line.strip(), f'лишние пробелы: {line!r}'
        assert not re.search(r'\bid\b', line), f'служебный номер в строке: {line!r}'


def test_podskazka_zapreschaet_otricat_nalichie_doma():
    p = agent.SYSTEM_PROMPT
    assert 'Никогда не говори, что дома нет' in p
    assert 'других у нас нет' in p


def test_podskazka_obyasnyaet_razgovornye_nazvaniya():
    p = agent.SYSTEM_PROMPT
    assert 'тридцатый дом' in p, 'номер словом'
    assert 'ЖК' in p, 'название комплекса — не адрес'


def test_podskazka_ne_razdulas():
    """Список адресов растёт сам — сторожим то, что пишем руками.

    Раньше здесь стоял общий потолок, и он упирался не в лишние слова, а
    в новые дома: домов прибавляется каждый год, а подсказка от этого
    «раздувшейся» не становится.
    """
    adresa = len(agent._houses_block())
    assert len(agent.SYSTEM_PROMPT) - adresa < 5200, 'инструкции разрослись'
    assert adresa < 4000, 'адреса занимают слишком много'


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
    monkeypatch.setattr(agent.db, 'recent_chat_history', lambda uid, limit=6, chat_id=None: [])

    with caplog.at_level(logging.INFO, logger='agent'):
        await agent.answer(1, 'Андрей', 'где 4-я Советская 30')

    assert 'find_house' in caplog.text
    assert '4-я Советская 30' in caplog.text


def test_v_spiske_rovno_adresa():
    """Строка списка совпадает с адресом дома буква в букву."""
    adresa = {h['address'] for h in houses.HOUSES}
    assert set(agent._houses_block().split('\n')) == adresa


def test_pro_nomer_doma_skazano_pryamo():
    p = agent.SYSTEM_PROMPT
    assert 'Номер дома — часть адреса' in p
    assert 'find_house' in p, 'сказано, откуда брать house_id'
    assert 'сам его не придумывай' in p


def test_kazhdyy_dom_uznayotsya_po_svoemu_adresu():
    """Проверка от обратного: по адресу из подсказки находится ровно он."""
    for h in houses.HOUSES:
        found = houses.search(h['address'])
        assert found and found[0]['address'] == h['address'], (
            f"{h['address']} нашёлся как {found[0]['address'] if found else '—'}")


async def test_find_house_otdayot_house_id_a_ne_id():
    """Короткое «id» рядом с адресом модель принимала за номер дома."""
    import json

    data = json.loads(agent._tool_find_house('4-я Советская 30'))

    nayden = data['found'][0]
    assert nayden['address'] == '4-я Советская 30'
    assert 'house_id' in nayden and 'id' not in nayden
