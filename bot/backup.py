"""Резервная копия базы и выгрузка домов в Markdown.

Две задачи одним куском, потому что это одно и то же действие.

Первая: копий базы не было вообще. Вся работа — заявки, показания,
паспорта, история — лежала в одном файле на диске Railway. Сбой хранилища
или ошибка при обновлении стирали её без следа.

Вторая: паспорта домов нужны заказчику снаружи бота — в Obsidian, где он
уже ведёт заметки. Markdown для этого родной формат: файл на дом, ссылки
между ними, теги по ЖК. Заодно это копия, которую можно прочитать
человеческими глазами, даже если базы и бота больше нет.
"""
import asyncio
import io
import logging
import os
import re
import sqlite3
import zipfile
from datetime import date, datetime

from . import db, houses

log = logging.getLogger('backup')

# Держим на диске неделю: база меньше мегабайта, места это не стоит,
# а откатиться на «как было до вчерашней ошибки» иногда нужно
KEEP = 7
HOUR = 3          # ночью по Иркутску: людей не будим, нагрузки нет


def backup_dir() -> str:
    """Папка для копий — рядом с базой, то есть на постоянном диске."""
    return os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), 'backups')


def snapshot(dest: str) -> str:
    """Копия базы штатным механизмом SQLite.

    Просто скопировать файл нельзя: в этот момент может идти запись, и
    копия окажется битой. Штатный backup делает согласованный снимок.
    """
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    src = sqlite3.connect(db.DB_PATH)
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    return dest


# ---------- Markdown ----------

def _safe(name: str) -> str:
    """Имя файла для заметки: без символов, которых не любят файловые системы."""
    return re.sub(r'[\\/:*?"<>|]', '-', name).strip() or 'дом'


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return '—'
    try:
        return date.fromisoformat(iso).strftime('%d.%m.%Y')
    except ValueError:
        return iso


def house_markdown(house) -> str:
    """Заметка про дом: паспорт, приборы, работы, заявки, что было в чате."""
    from .handlers import PASSPORT_LABELS, fmt_period, fmt_value

    hid = house['id']
    complexes = db.all_house_complexes()
    cx_id = complexes.get(hid)
    cx = next((c['name'] for c in houses.COMPLEXES if c['id'] == cx_id), None)

    tag_cx = re.sub(r'\s+', '-', (cx or 'без-жк').lower().replace('жк ', ''))
    lines = [
        '---',
        f"адрес: {house['address']}",
        f"жк: {cx or 'не указан'}",
        f"обновлено: {db.now()}",
        f"tags: [дом, жк/{tag_cx}]",
        '---',
        '',
        f"# {house['address']}",
        '',
    ]
    if house.get('note'):
        lines += [f"> {house['note']}", '']

    passport = db.get_passport(hid) or {}
    lines.append('## Паспорт')
    if passport:
        for key, value in passport.items():
            if value:
                lines.append(f"- **{PASSPORT_LABELS.get(key, key)}:** {value}")
    else:
        lines.append('_Пока не заполнен._')
    lines.append('')

    meters = db.list_meters(hid)
    lines.append('## Счётчики')
    if meters:
        lines.append('| Прибор | Заводской № | Последнее | Период | Состояние |')
        lines.append('|---|---|---|---|---|')
        for m in meters:
            rs = db.meter_readings(m['id'], limit=1)
            last = fmt_value(rs[0]['value']) if rs else '—'
            period = fmt_period(rs[0]['period']) if rs else '—'
            sostoyanie = ('снят на поверку' if m['status'] == db.METER_REMOVED
                          else 'на месте')
            lines.append(f"| {m['label']} | {m['serial'] or '—'} | {last} | "
                         f"{period} | {sostoyanie} |")
    else:
        lines.append('_Не заведены._')
    lines.append('')

    points = db.list_points(hid)
    lines.append('## Манометры')
    if points:
        for p in points:
            dev = db.active_device(p['id'])
            mesto = ' '.join(x for x in (p['tp'], p['place']) if x)
            if dev:
                lines.append(f"- {mesto} — № {dev['serial'] or '—'}, "
                             f"поверка до {_fmt_date(dev['verified_until'])}")
            else:
                lines.append(f"- {mesto} — прибор не установлен")
    else:
        lines.append('_Не заведены._')
    lines.append('')

    works = db.list_works(house_id=hid, open_only=False, limit=50)
    lines.append('## Работы')
    if works:
        for w in works:
            galka = 'x' if w['status'] == db.WORK_DONE else ' '
            srok = f" — до {_fmt_date(w['deadline'])}" if w['deadline'] else ''
            kto = f" ({w['assignee']})" if w['assignee'] else ''
            lines.append(f"- [{galka}] {w['title']}{srok}{kto}")
    else:
        lines.append('_Нет._')
    lines.append('')

    vse = db.list_requests(statuses=(db.STATUS_NEW, db.STATUS_WORK, db.STATUS_DONE),
                           limit=300)
    requests = [r for r in vse if r['house_id'] == hid]
    lines.append('## Заявки')
    if requests:
        for r in requests[:20]:
            lines.append(f"- {r['created_at']} — {r['description']} "
                         f"({r['status']}, {r['created_by_name'] or '—'})")
    else:
        lines.append('_Нет._')
    lines.append('')

    records = db.house_chat_records(hid, limit=20)
    lines.append('## Что говорили в чате')
    if records:
        for rec in records:
            what = (rec['transcript'] or rec['text'] or '').strip()
            if not what:
                continue
            mark = '🚨 ' if rec['is_issue'] else ''
            lines.append(f"- **{rec['created_at']}** {rec['user_name'] or '—'}: "
                         f"{mark}{what}")
    else:
        lines.append('_Тихо._')
    lines.append('')

    return '\n'.join(lines)


def index_markdown() -> str:
    """Оглавление: ссылки на дома, сгруппированные по ЖК."""
    complexes = db.all_house_complexes()
    po_zhk = {}
    for h in houses.HOUSES:
        cx_id = complexes.get(h['id'])
        name = next((c['name'] for c in houses.COMPLEXES if c['id'] == cx_id),
                    'Без привязки к ЖК')
        po_zhk.setdefault(name, []).append(h)

    lines = ['---', 'tags: [оглавление]', f'обновлено: {db.now()}', '---', '',
             '# Дома в обслуживании', '',
             f'Всего домов: {len(houses.HOUSES)}. Выгружено Люсей {db.now()}.', '']
    for name in sorted(po_zhk):
        lines.append(f'## {name}')
        for h in sorted(po_zhk[name], key=lambda x: x['address']):
            lines.append(f"- [[{_safe(h['address'])}]]")
        lines.append('')
    return '\n'.join(lines)


def make_archive() -> tuple[bytes, str]:
    """Собирает zip: база + папка заметок по домам. Возвращает (данные, имя)."""
    buf = io.BytesIO()
    stamp = datetime.now(db.IRKUTSK_TZ).strftime('%Y-%m-%d_%H-%M')
    tmp_db = os.path.join(backup_dir(), f'tmp_{stamp}.db')
    snapshot(tmp_db)
    try:
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.write(tmp_db, 'bot.db')
            z.writestr('Дома/00 Оглавление.md', index_markdown())
            for h in houses.HOUSES:
                z.writestr(f"Дома/{_safe(h['address'])}.md", house_markdown(h))
    finally:
        if os.path.exists(tmp_db):
            os.unlink(tmp_db)
    return buf.getvalue(), f'lusya_{stamp}.zip'


def save_archive() -> str:
    """Кладёт копию на диск и подчищает старые. Возвращает путь."""
    data, name = make_archive()
    folder = backup_dir()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, 'wb') as f:
        f.write(data)
    starye = sorted(f for f in os.listdir(folder) if f.endswith('.zip'))
    for old in starye[:-KEEP]:
        os.unlink(os.path.join(folder, old))
    return path


async def backup_loop(bot):
    """Раз в сутки ночью — копия на диск и файлом администратору.

    Копия рядом с базой спасает от ошибки в данных, но не от потери диска.
    Поэтому вторая копия уходит человеку в мессенджер: там она переживёт
    и Railway, и меня.
    """
    while True:
        now = datetime.now(db.IRKUTSK_TZ)
        # ближайшие три часа ночи
        wait = ((HOUR - now.hour) % 24) * 3600 - now.minute * 60 - now.second
        await asyncio.sleep(wait if wait > 60 else wait + 24 * 3600)
        try:
            path = save_archive()
            log.info('Резервная копия готова: %s (%.1f КБ)', path,
                     os.path.getsize(path) / 1024)
            await send_to_admins(bot, path)
        except Exception:
            log.exception('Не удалось сделать резервную копию')


async def send_to_admins(bot, path: str):
    """Отправляет архив тем, кто отвечает за данные."""
    from maxapi.enums.upload_type import UploadType
    from maxapi.types.input_media import InputMediaBuffer

    with open(path, 'rb') as f:
        data = f.read()
    for u in db.list_users():
        if u['role'] not in ('admin', 'engineer'):
            continue
        try:
            media = await bot.upload_media(InputMediaBuffer(
                buffer=data, filename=os.path.basename(path), type=UploadType.FILE))
            await bot.send_message(
                user_id=u['user_id'],
                text=f'🗄 Резервная копия за {db.now()}.\n'
                     'Внутри база и папка «Дома» — заметки по каждому дому '
                     'в Markdown, их можно положить в Obsidian.',
                attachments=[media])
        except Exception:
            log.warning('Не удалось отправить копию пользователю %s', u['user_id'],
                        exc_info=True)
