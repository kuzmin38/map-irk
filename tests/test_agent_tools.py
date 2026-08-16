import json

import pytest

from bot import agent, db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


def _house_id(address):
    return next(h['id'] for h in agent.houses.HOUSES if h['address'] == address)


def test_find_house_known_address():
    result = json.loads(agent._tool_find_house('Байкальская 99'))
    addresses = [h['address'] for h in result['found']]
    assert 'Байкальская 99' in addresses


def test_find_house_unknown_address():
    result = json.loads(agent._tool_find_house('Несуществующая улица 999'))
    assert result['found'] == []


def test_get_passport_empty():
    house_id = _house_id('Байкальская 99')
    result = json.loads(agent._tool_get_passport(house_id))
    assert result['passport'] == {}
    assert 'не заполнен' in result['note']


def test_get_passport_filled():
    house_id = _house_id('Байкальская 99')
    db.set_passport_field(house_id, 'year', '1985', 'тест')
    result = json.loads(agent._tool_get_passport(house_id))
    assert result['passport']['Год постройки'] == '1985'


def test_get_passport_unknown_house():
    result = json.loads(agent._tool_get_passport(999999))
    assert 'error' in result


def test_get_riser_known():
    result = json.loads(agent._tool_get_riser('4-я Советская 30', 1))
    assert result['floor'] == 2
    assert result['riser'] == 1
    assert result['flats_on_floor'] == 8


def test_get_riser_unknown_flat():
    result = json.loads(agent._tool_get_riser('4-я Советская 30', 9999))
    assert 'error' in result


def test_get_directory_all():
    result = json.loads(agent._tool_get_directory('all'))
    ids = [s['id'] for s in result]
    assert 'norms' in ids


def test_get_directory_section():
    result = json.loads(agent._tool_get_directory('norms'))
    assert 'НОРМАТИВЫ' in result['text']


def test_get_directory_unknown_section():
    result = json.loads(agent._tool_get_directory('bogus'))
    assert 'error' in result


def test_list_docs_empty():
    house_id = _house_id('Байкальская 99')
    result = json.loads(agent._tool_list_docs(house_id))
    assert result['docs'] == []


def test_get_house_works_empty():
    house_id = _house_id('Байкальская 99')
    result = json.loads(agent._tool_get_house_works(house_id))
    assert result['works'] == []


def test_get_house_works_with_data():
    house_id = _house_id('Байкальская 99')
    db.add_work(house_id, 'Опрессовка', '2026-09-01', 'Тест')
    result = json.loads(agent._tool_get_house_works(house_id))
    assert result['works'][0]['title'] == 'Опрессовка'


def test_get_open_requests_filtered_by_house():
    house_id = _house_id('Байкальская 99')
    other_id = _house_id('Байкальская 87')
    db.add_request(house_id, 'Байкальская 99', 'Течёт кран', 1, 'Тест')
    db.add_request(other_id, 'Байкальская 87', 'Другая проблема', 1, 'Тест')
    result = json.loads(agent._tool_get_open_requests(house_id))
    assert len(result['requests']) == 1
    assert result['requests'][0]['description'] == 'Течёт кран'


def test_get_meetings_returns_only_ready_protocols():
    import json as _json
    db.add_meeting(1, 'Тест')                       # ещё расшифровывается
    mid = db.add_meeting(1, 'Тест')
    db.set_meeting_result(mid, title='Планёрка', protocol=_json.dumps(
        {'summary': 'Обсудили опрессовку', 'decisions': ['Начать с Квадрума'],
         'tasks': [], 'questions': []}, ensure_ascii=False),
        status=db.MEETING_READY)

    result = json.loads(agent._tool_get_meetings())
    assert len(result['meetings']) == 1              # без протокола не отдаём
    assert result['meetings'][0]['decisions'] == ['Начать с Квадрума']
