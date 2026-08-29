"""Чтение документов: файл → текст → поиск по нему в базе и через агента."""
import json
import types

import pytest

from bot import agent, db, doc_text, houses, project_docs


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


def house_id(address):
    return next(h['id'] for h in houses.HOUSES if h['address'] == address)


PROJECT_TEXT = ('Проект отопления и вентиляции. Розлив нижний, полипропилен, '
                'диаметр ДУ50. Тепловой узел в подвале, два манометра на подаче.')


# ---------- Разбор файла ----------

def test_kind_by_extension():
    assert doc_text.kind('/x/проект.pdf') == 'rich'
    assert doc_text.kind('/x/смета.xlsx') == 'rich'
    assert doc_text.kind('/x/заметка.md') == 'plain'
    assert doc_text.kind('/x/план.dwg') == 'drawing'
    assert doc_text.kind('/x/фото.jpg') == 'other'


def test_plain_file_is_read_without_markitdown(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_text, '_checked', True)
    monkeypatch.setattr(doc_text, '_converter', None)      # markitdown не стоит
    path = tmp_path / 'заметка.md'
    path.write_text(PROJECT_TEXT, encoding='utf-8')

    text, status = doc_text.extract(str(path))
    assert status == db.DOC_OK and 'ДУ50' in text


def test_drawing_is_skipped(tmp_path):
    path = tmp_path / 'план.dwg'
    path.write_bytes(b'AC1024binary')
    assert doc_text.extract(str(path)) == (None, db.DOC_SKIPPED)


def test_pdf_without_markitdown_is_skipped_not_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_text, '_checked', True)
    monkeypatch.setattr(doc_text, '_converter', None)
    path = tmp_path / 'проект.pdf'
    path.write_bytes(b'%PDF-1.4 ...')
    assert doc_text.extract(str(path)) == (None, db.DOC_SKIPPED)


def test_pdf_is_read_when_markitdown_present(tmp_path, monkeypatch):
    class FakeConverter:
        def convert(self, path):
            return types.SimpleNamespace(text_content=PROJECT_TEXT)
    monkeypatch.setattr(doc_text, '_checked', True)
    monkeypatch.setattr(doc_text, '_converter', FakeConverter())

    path = tmp_path / 'проект.pdf'
    path.write_bytes(b'%PDF-1.4 ...')
    text, status = doc_text.extract(str(path))
    assert status == db.DOC_OK and 'манометра' in text


def test_scan_without_text_layer_reports_empty(tmp_path, monkeypatch):
    """Скан — файл есть, букв нет. Это не ошибка, а честный статус."""
    class EmptyConverter:
        def convert(self, path):
            return types.SimpleNamespace(text_content='   \n  ')
    monkeypatch.setattr(doc_text, '_checked', True)
    monkeypatch.setattr(doc_text, '_converter', EmptyConverter())

    path = tmp_path / 'скан.pdf'
    path.write_bytes(b'%PDF-1.4 ...')
    assert doc_text.extract(str(path)) == (None, db.DOC_EMPTY)


def test_broken_file_reports_failed(tmp_path, monkeypatch):
    class Boom:
        def convert(self, path):
            raise RuntimeError('битый файл')
    monkeypatch.setattr(doc_text, '_checked', True)
    monkeypatch.setattr(doc_text, '_converter', Boom())

    path = tmp_path / 'битый.pdf'
    path.write_bytes(b'%PDF-1.4 ...')
    assert doc_text.extract(str(path)) == (None, db.DOC_FAILED)


def test_missing_and_empty_files():
    assert doc_text.extract('/нет/такого.pdf') == (None, db.DOC_FAILED)


def test_long_text_is_trimmed(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_text, 'MAX_CHARS', 100)
    path = tmp_path / 'много.txt'
    path.write_text('строка ' * 500, encoding='utf-8')
    text, status = doc_text.extract(str(path))
    assert status == db.DOC_OK
    assert 'обрезан' in text and len(text) < 200


async def test_extract_async_does_not_block(tmp_path):
    path = tmp_path / 'з.txt'
    path.write_text(PROJECT_TEXT, encoding='utf-8')
    text, status = await doc_text.extract_async(str(path))
    assert status == db.DOC_OK and text


# ---------- Фрагменты ----------

def test_excerpt_centres_on_match():
    text = 'начало ' * 100 + 'диаметр ДУ50 ' + 'конец ' * 100
    piece = doc_text.excerpt(text, 'ДУ50', radius=50)
    assert 'ДУ50' in piece
    assert piece.startswith('…') and piece.endswith('…')
    assert len(piece) < 150


def test_excerpt_without_match_returns_head():
    assert doc_text.excerpt('короткий текст', 'нету').startswith('короткий')
    assert doc_text.excerpt('', 'что-то') == ''


# ---------- Хранение и поиск ----------

def test_save_and_find_doc_text():
    db.save_doc_text('project', 'fid1', PROJECT_TEXT, db.DOC_OK,
                     title='♨️ ОВ 65а.2', addresses='Седова 65а/2')
    row = db.get_doc_text('project', 'fid1')
    assert row['chars'] == len(PROJECT_TEXT) and row['status'] == db.DOC_OK

    found = db.search_doc_texts('ДУ50')
    assert len(found) == 1 and found[0]['title'] == '♨️ ОВ 65а.2'


def test_search_ignores_unreadable_docs():
    db.save_doc_text('project', 'scan', None, db.DOC_EMPTY, title='Скан')
    db.save_doc_text('project', 'dwg', None, db.DOC_SKIPPED, title='Чертёж')
    assert db.search_doc_texts('скан') == []


def test_search_filters_by_address():
    db.save_doc_text('project', 'a', PROJECT_TEXT, db.DOC_OK,
                     title='ОВ Седова', addresses='Седова 65а/2')
    db.save_doc_text('project', 'b', PROJECT_TEXT, db.DOC_OK,
                     title='ОВ Байкальская', addresses='Байкальская 237')
    found = db.search_doc_texts('розлив', address='Седова 65а/2')
    assert [r['key'] for r in found] == ['a']


def test_reindex_overwrites_previous_text():
    db.save_doc_text('project', 'fid1', 'старый текст', db.DOC_OK, title='Док')
    db.save_doc_text('project', 'fid1', 'новый текст', db.DOC_OK, title='Док')
    assert db.get_doc_text('project', 'fid1')['text'] == 'новый текст'
    assert len(db.list_doc_texts()) == 1


def test_stats_by_status():
    db.save_doc_text('project', '1', PROJECT_TEXT, db.DOC_OK)
    db.save_doc_text('project', '2', None, db.DOC_EMPTY)
    db.save_doc_text('project', '3', None, db.DOC_SKIPPED)
    assert db.doc_texts_stats() == {db.DOC_OK: 1, db.DOC_EMPTY: 1, db.DOC_SKIPPED: 1}


# ---------- Разбор всего каталога ----------

async def test_index_all_walks_catalog(tmp_path, monkeypatch):
    doc = {'title': '♨️ ОВ 65а.2', 'url': 'https://drive.google.com/file/d/FID1/view',
           'addresses': ['Седова 65а/2'], 'section': 'ОВ'}
    path = tmp_path / 'FID1.txt'
    path.write_text(PROJECT_TEXT, encoding='utf-8')

    monkeypatch.setattr(project_docs, 'CATALOG', [doc])
    monkeypatch.setattr(project_docs, 'local_path', lambda d: str(path))

    ok, total, report = await project_docs.index_all()
    assert (ok, total) == (1, 1)
    row = db.get_doc_text('project', 'FID1')
    assert row['addresses'] == 'Седова 65а/2' and 'ДУ50' in row['text']


async def test_index_all_skips_already_read(tmp_path, monkeypatch):
    doc = {'title': 'ОВ', 'url': 'https://drive.google.com/file/d/FID1/view',
           'addresses': [], 'section': 'ОВ'}
    monkeypatch.setattr(project_docs, 'CATALOG', [doc])
    monkeypatch.setattr(project_docs, 'local_path', lambda d: '/нет/файла.pdf')
    db.save_doc_text('project', 'FID1', PROJECT_TEXT, db.DOC_OK, title='ОВ')

    ok, total, report = await project_docs.index_all()
    assert ok == 1 and 'уже прочитан' in report[0]


async def test_index_all_ignores_undownloaded_docs(monkeypatch):
    monkeypatch.setattr(project_docs, 'CATALOG', [
        {'title': 'Нет файла', 'url': 'https://drive.google.com/file/d/FID2/view',
         'addresses': [], 'section': 'ОВ'}])
    monkeypatch.setattr(project_docs, 'local_path', lambda d: None)
    ok, total, report = await project_docs.index_all()
    assert (ok, total, report) == (0, 0, [])


# ---------- Инструменты агента ----------

def test_agent_search_docs_returns_excerpt():
    db.save_doc_text('project', 'fid1', PROJECT_TEXT, db.DOC_OK,
                     title='♨️ ОВ 65а.2', addresses='Седова 65а/2')
    result = json.loads(agent._tool_search_docs('ДУ50'))
    assert result['found'][0]['title'] == '♨️ ОВ 65а.2'
    assert 'ДУ50' in result['found'][0]['excerpt']


def test_agent_search_docs_hints_when_nothing_indexed():
    result = json.loads(agent._tool_search_docs('диаметр'))
    assert result['found'] == []
    assert 'не разобраны' in result['note']


def test_agent_search_docs_by_address():
    db.save_doc_text('project', 'a', PROJECT_TEXT, db.DOC_OK,
                     title='ОВ Седова', addresses='Седова 65а/2')
    db.save_doc_text('project', 'b', PROJECT_TEXT, db.DOC_OK,
                     title='ОВ Байкальская', addresses='Байкальская 237')
    result = json.loads(agent._tool_search_docs('розлив', 'Байкальская 237'))
    assert [f['key'] for f in result['found']] == ['b']


def test_agent_read_doc_by_parts():
    db.save_doc_text('project', 'fid1', 'А' * 9000, db.DOC_OK, title='Большой')
    first = json.loads(agent._tool_read_doc('fid1'))
    assert first['part'] == 1 and first['parts_total'] == 3 and len(first['text']) == 4000
    last = json.loads(agent._tool_read_doc('fid1', 3))
    assert len(last['text']) == 1000


def test_agent_read_doc_missing():
    assert 'error' in json.loads(agent._tool_read_doc('нет-такого'))


def test_native_crash_does_not_kill_the_run(tmp_path, monkeypatch):
    """Сломанная нативная библиотека падает не Exception'ом — ловим и это."""
    class Panic:
        def convert(self, path):
            raise BaseException('rust panic')
    monkeypatch.setattr(doc_text, '_checked', True)
    monkeypatch.setattr(doc_text, '_converter', Panic())

    path = tmp_path / 'проект.pdf'
    path.write_bytes(b'%PDF-1.4 ...')
    assert doc_text.extract(str(path)) == (None, db.DOC_FAILED)
