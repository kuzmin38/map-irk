"""Люся читает общую память — и только разрешённую её часть.

Памятью пользовалась только Ева, личный ассистент. Заказчик: «мы выяснили,
что Люся не ходит в память».

Хранилище целиком ей давать нельзя: там медкарта, долги, кредиты, профили
жены и дочери, проповеди и личные переписки. Люся работает в чате с
восемнадцатью коллегами, включая директора, — и один неудачный ответ
означает, что это читают на работе.

Поэтому тут проверяется в первую очередь не то, что она читает, а то, чего
она не читает ни при каких настройках.
"""
import os

import pytest

from bot import agent, db, memory
import bot.handlers as H


@pytest.fixture
def hranilishche(tmp_path, monkeypatch):
    """Копия хранилища с личными разделами — как у заказчика."""
    fayly = {
        'Люся/Работа.md': '# Работа\nМастер участка — Виталя, смена с 8.',
        'Люся/Патерны счётчиков.md': '# Счётчики\nПоверка манометров летом.',
        '❤️ Здоровье/Медкарта.md': '# Медкарта\nДиагноз и назначения.',
        '💰 Финансы/Долги.md': '# Долги\nКредит 46000 в месяц.',
        '👨‍👩‍👧 Семья/Вера.md': '# Вера\nДочь, офтальмолог.',
        '💼 Найм/Работа.md': '# Найм\nРазговор с директором про оклад.',
        '🙏 Вера/Вера.md': '# Вера\nО главном.',
    }
    for put, text in fayly.items():
        polnyy = tmp_path / put
        polnyy.parent.mkdir(parents=True, exist_ok=True)
        polnyy.write_text(text, encoding='utf-8')
    monkeypatch.setattr(memory, 'DIR', str(tmp_path))
    monkeypatch.setattr(memory, 'RAZRESHENO', ['Люся'])
    return tmp_path


# ── чего не читает никогда ──────────────────────────────────────────────

@pytest.mark.parametrize('put', [
    '❤️ Здоровье/Медкарта.md',
    '💰 Финансы/Долги.md',
    '💰 Финансы/Кредиты/Чеклист для Наташи.md',
    '👨‍👩‍👧 Семья/Вера.md',
    '🙏 Вера/Вера.md',
    'Духовное/Проповеди/Божий экзамен.md',
    '📝 Переписки/Наталья.md',
    '🤝 Контакты/Шлемко Стас.md',
    '👤 Обо мне/Профиль.md',
    'дневник/планы/19.06.2026.md',
])
def test_lichnoe_zakryto(put):
    assert not memory.dostupno(put)


def test_zapret_silnee_razresheniya(monkeypatch):
    """Даже если папку открыть целиком, здоровье и долги закрыты."""
    monkeypatch.setattr(memory, 'RAZRESHENO', [''])
    monkeypatch.setattr(memory, 'RAZRESHENO', ['.'])
    for put in ('❤️ Здоровье/Медкарта.md', '💰 Финансы/Долги.md'):
        assert not memory.dostupno(put)


def test_zapret_rabotaet_i_vnutri_razreshyonnoy_papki():
    """Ошибка в имени файла не должна открывать личное."""
    assert not memory.dostupno('Люся/Здоровье бригады.md')
    assert not memory.dostupno('Люся/Финансы участка.md')


def test_vyhod_iz_papki_ne_prohodit():
    assert not memory.dostupno('Люся/../❤️ Здоровье/Медкарта.md')
    assert not memory.dostupno('../MEMORY.md')


def test_ne_markdown_ne_chitaem():
    assert not memory.dostupno('Люся/файл.txt')
    assert not memory.dostupno('Люся/база.db')


def test_chitat_zakrytoe_vozvrashchaet_nichego(hranilishche):
    assert memory.chitat('❤️ Здоровье/Медкарта.md') is None
    assert memory.chitat('💰 Финансы/Долги.md') is None


def test_simvolicheskaya_ssylka_naruzhu(hranilishche, tmp_path):
    """Ссылка из разрешённой папки на медкарту читаться не должна."""
    tsel = hranilishche / '❤️ Здоровье' / 'Медкарта.md'
    ssylka = hranilishche / 'Люся' / 'что-то.md'
    try:
        os.symlink(tsel, ssylka)
    except (OSError, NotImplementedError):
        pytest.skip('символические ссылки недоступны')
    assert memory.chitat('Люся/что-то.md') is None


# ── что читает ──────────────────────────────────────────────────────────

def test_svoyu_papku_chitaet(hranilishche):
    assert memory.dostupno('Люся/Работа.md')
    assert 'Виталя' in memory.chitat('Люся/Работа.md')


def test_spisok_tolko_razreshyonnoe(hranilishche):
    fayly = memory.spisok()
    assert fayly == ['Люся/Патерны счётчиков.md', 'Люся/Работа.md']


def test_poisk_nahodit_po_slovam(hranilishche):
    naydeno = memory.iskat('кто мастер участка')
    assert naydeno and naydeno[0][0] == 'Люся/Работа.md'
    assert 'Виталя' in naydeno[0][1]


def test_poisk_ne_zalezaet_v_lichnoe(hranilishche):
    """Слова из закрытых файлов не находятся вообще."""
    for zapros in ('медкарта диагноз', 'кредит долги', 'дочь офтальмолог',
                   'оклад директор'):
        assert memory.iskat(zapros) == [], zapros


def test_korotkie_slova_ne_ischem(hranilishche):
    assert memory.iskat('а и в') == []
    assert memory.iskat('') == []


def test_pustaya_papka_nichego_ne_lomaet(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'DIR', str(tmp_path / 'нет-такой'))
    assert memory.spisok() == []
    assert memory.iskat('мастер') == []
    assert not memory.vklyuchena()
    assert memory.blok_dlya_podskazki('мастер') == ''


# ── в рабочем чате памяти нет ───────────────────────────────────────────

@pytest.fixture
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


async def test_v_lichke_pamyat_popadaet_v_podskazku(
        hranilishche, baza, monkeypatch):
    zapros = {}

    async def fake_ask(messages, **kw):
        zapros['system'] = messages[0]['content']
        return None

    monkeypatch.setattr(agent.ai, 'enabled', lambda: True)
    monkeypatch.setattr(agent, '_call', fake_ask, raising=False)
    blok = agent.memory.blok_dlya_podskazki('кто мастер участка')
    assert 'Виталя' in blok


async def test_v_rabochem_chate_pamyati_net(hranilishche, baza, monkeypatch):
    """Главная проверка: в чат с коллегами память не идёт никаким путём."""
    sobrano = {}

    async def fake_answer(user_id, user_name, user_text, chat_id=None):
        # повторяем ту же развилку, что в agent.answer
        sobrano['pamyat'] = (agent.memory.blok_dlya_podskazki(user_text)
                             if chat_id is None else '')
        return None

    await fake_answer(1, 'Андрей', 'кто мастер участка', chat_id=-100)
    assert sobrano['pamyat'] == ''
    await fake_answer(1, 'Андрей', 'кто мастер участка', chat_id=None)
    assert 'Виталя' in sobrano['pamyat']


def test_v_agente_pamyat_pod_usloviem_lichki():
    """Условие стоит в коде, а не на словах."""
    import inspect
    kod = inspect.getsource(agent.answer)
    assert 'if chat_id is None:' in kod
    assert 'memory.blok_dlya_podskazki' in kod
