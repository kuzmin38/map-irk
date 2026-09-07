"""Чтение общей памяти — Obsidian-хранилища заказчика.

Памятью до сих пор пользовалась только Ева, личный ассистент. Люся про
хранилище не знала ничего, и заказчик это заметил: «мы выяснили, что Люся
не ходит в память».

Дать ей хранилище целиком нельзя. Там лежат медкарта, долги, кредиты,
профили жены и дочери, проповеди и личные переписки. А Люся работает в
чате с восемнадцатью коллегами, включая директора. Один неудачный ответ —
и это читают на работе. Поэтому доступ устроен так:

  читает только разрешённые папки — по умолчанию одну, свою;
  запретные разделы закрыты отдельным списком, поверх разрешений, —
      чтобы ошибка в настройке не открыла здоровье и финансы;
  ничего не пишет: память правит Ева и человек, не Люся;
  в рабочем чате не читает вовсе — это решается в agent, по chat_id.

Файлы берём с диска: папку наполняет любой канал, который заказчик выберет
потом — синхронизация, git или руками. Код от этого выбора не зависит.
"""
import logging
import os
import re

log = logging.getLogger('memory')

# Где лежит копия хранилища. На Railway это том, который живёт между
# перезапусками; локально — папка рядом с данными бота
DIR = os.environ.get('MEMORY_DIR') or (
    '/data/memory' if os.path.isdir('/data') else
    os.path.join(os.path.dirname(__file__), 'data', 'memory'))

# Что Люсе разрешено. Список через запятую, пути от корня хранилища.
# По умолчанию — одна папка, которую заказчик наполняет сам
RAZRESHENO = [p.strip() for p in
              os.environ.get('MEMORY_ALLOW', 'Люся').split(',') if p.strip()]

# Запрет поверх разрешений: даже если папку случайно откроют целиком,
# эти разделы Люся не прочитает. Проверяется по каждой части пути
ZAPRESHENO = ('здоровье', 'медкарт', 'финанс', 'долг', 'кредит', 'семья',
              'вера', 'духовн', 'проповед', 'переписк', 'контакт', 'дневник',
              'обо мне', 'профиль')

MAX_FAYL = 64 * 1024      # больше в подсказку всё равно не влезет
MAX_NAYDENO = 5           # сколько файлов отдавать на один вопрос
MAX_VYDERZHKA = 700       # символов из каждого файла


def _chasti(otnositelnyy: str) -> list:
    return [c.lower().replace('ё', 'е')
            for c in otnositelnyy.replace('\\', '/').split('/') if c]


def zapretno(otnositelnyy: str) -> bool:
    """Попадает ли путь в запретный раздел."""
    for chast in _chasti(otnositelnyy):
        for slovo in ZAPRESHENO:
            if slovo in chast:
                return True
    return False


def dostupno(otnositelnyy: str) -> bool:
    """Можно ли Люсе читать этот файл."""
    put = (otnositelnyy or '').replace('\\', '/').strip('/')
    if not put or not put.lower().endswith('.md'):
        return False
    if '..' in _chasti(put):
        return False
    if zapretno(put):
        return False
    for razresheno in RAZRESHENO:
        koren = razresheno.replace('\\', '/').strip('/').lower()
        if put.lower() == koren or put.lower().startswith(koren + '/'):
            return True
    return False


def vklyuchena() -> bool:
    """Есть ли вообще что читать."""
    return bool(spisok())


def spisok(limit: int = 200) -> list:
    """Разрешённые файлы хранилища, путями от корня."""
    if not os.path.isdir(DIR):
        return []
    out = []
    for koren, papki, fayly in os.walk(DIR):
        papki[:] = [p for p in papki if not p.startswith('.')]
        for f in sorted(fayly):
            put = os.path.relpath(os.path.join(koren, f), DIR)
            if dostupno(put):
                out.append(put.replace('\\', '/'))
            if len(out) >= limit:
                return sorted(out)
    return sorted(out)


def chitat(otnositelnyy: str) -> str | None:
    """Содержимое файла. None — если читать его нельзя или его нет."""
    if not dostupno(otnositelnyy):
        log.info('Память: доступ к «%s» закрыт', otnositelnyy)
        return None
    put = os.path.join(DIR, otnositelnyy.replace('\\', '/'))
    # Символическая ссылка из разрешённой папки может указывать на медкарту:
    # снаружи путь разрешён, а файл — запретный. Проверяем, куда ссылка
    # ведёт на самом деле, и разрешение спрашиваем ещё раз уже для цели
    koren = os.path.realpath(DIR)
    nastoyashchiy = os.path.realpath(put)
    if not nastoyashchiy.startswith(koren + os.sep):
        log.warning('Память: «%s» ведёт за пределы хранилища', otnositelnyy)
        return None
    tsel = os.path.relpath(nastoyashchiy, koren).replace('\\', '/')
    if tsel != otnositelnyy.replace('\\', '/').strip('/') and not dostupno(tsel):
        log.warning('Память: «%s» ведёт в закрытый раздел (%s)', otnositelnyy, tsel)
        return None
    try:
        with open(nastoyashchiy, encoding='utf-8') as f:
            return f.read(MAX_FAYL)
    except OSError:
        return None


def _slova(zapros: str) -> list:
    return [s for s in re.findall(r'[\w\-]{4,}', (zapros or '').lower()) if s]


def iskat(zapros: str, limit: int = MAX_NAYDENO) -> list:
    """[(путь, выдержка)] — где в разрешённой памяти есть эти слова.

    Ищем кодом, а не моделью: искать в личных записях наугад — последнее,
    что тут нужно.
    """
    slova = _slova(zapros)
    if not slova:
        return []
    naydeno = []
    for put in spisok():
        text = chitat(put)
        if not text:
            continue
        nizhe = text.lower().replace('ё', 'е')
        popadaniy = sum(1 for s in slova if s.replace('ё', 'е') in nizhe)
        if popadaniy:
            naydeno.append((popadaniy, put, _vyderzhka(text, slova)))
    naydeno.sort(key=lambda x: -x[0])
    return [(put, vyd) for _n, put, vyd in naydeno[:limit]]


def _vyderzhka(text: str, slova: list) -> str:
    """Строки файла, в которых есть искомые слова, плюс заголовок."""
    stroki = text.splitlines()
    zagolovok = next((s for s in stroki if s.startswith('#')), '').strip('# ').strip()
    nuzhnye = []
    for s in stroki:
        nizhe = s.lower().replace('ё', 'е')
        if any(sl.replace('ё', 'е') in nizhe for sl in slova) and s.strip():
            nuzhnye.append(s.strip())
        if sum(len(x) for x in nuzhnye) > MAX_VYDERZHKA:
            break
    kusok = '\n'.join(nuzhnye)[:MAX_VYDERZHKA]
    return f'{zagolovok}\n{kusok}'.strip() if zagolovok else kusok


def blok_dlya_podskazki(zapros: str) -> str:
    """Что нашлось в памяти — куском для подсказки модели. '' если нечего."""
    naydeno = iskat(zapros)
    if not naydeno:
        return ''
    stroki = []
    for put, vyderzhka in naydeno:
        stroki.append(f'— {put}:\n{vyderzhka}')
    return '\n'.join(stroki)
