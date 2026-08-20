"""Что по дому требует внимания: критичное и мелочи.

Одна логика на приложение и на бота, иначе они начнут показывать разное.
Красное — то, что стоит денег или нарушает сроки: просроченная поверка,
скачок расхода, показание меньше предыдущего. Жёлтое — недозаполненное:
счётчик без номера, не снятые за месяц показания, пустой паспорт.
"""
import logging
from datetime import date, timedelta

from . import db

log = logging.getLogger('checks')

RED = 'red'
YELLOW = 'yellow'

ANOMALY_FACTOR = 1.8   # во столько раз расход выше обычного — уже подозрительно
VERIFY_SOON_DAYS = 30  # за сколько дней поверка считается близкой


def rashod_problema(readings) -> tuple | None:
    """(уровень, текст) по двум последним показаниям, или None.

    readings — свежие первыми, как отдаёт db.meter_readings.
    """
    if len(readings) < 2:
        return None
    delta = readings[0]['value'] - readings[1]['value']
    if delta < 0:
        return (RED, f"показание меньше предыдущего "
                     f"({readings[0]['value']:g} < {readings[1]['value']:g})")
    if len(readings) >= 3:
        proshlyy = readings[1]['value'] - readings[2]['value']
        if proshlyy > 0 and delta > proshlyy * ANOMALY_FACTOR:
            return (RED, f'расход {delta:g} против {proshlyy:g} в прошлом периоде — '
                         'возможна утечка')
    return None


def house_findings(house_id: int, period: str | None = None) -> list:
    """Список замечаний по дому: [{'level': 'red'|'yellow', 'text': ...}]."""
    today = date.today()
    soon = (today + timedelta(days=VERIFY_SOON_DAYS)).isoformat()
    found = []

    def add(level, text):
        found.append({'level': level, 'text': text})

    try:
        # Манометры и поверки
        for p in db.list_points(house_id):
            mesto = ' '.join(x for x in (p['tp'], p['place']) if x)
            dev = db.active_device(p['id'])
            if not dev:
                add(YELLOW, f'{mesto}: прибор не заведён')
                continue
            if not dev['verified_until']:
                add(YELLOW, f'{mesto}: не указан срок поверки')
            elif dev['verified_until'] < today.isoformat():
                add(RED, f'{mesto}: поверка просрочена ({dev["verified_until"]})')
            elif dev['verified_until'] <= soon:
                add(YELLOW, f'{mesto}: поверка истекает ({dev["verified_until"]})')
            if not dev['serial']:
                add(YELLOW, f'{mesto}: нет заводского номера')

        # Счётчики
        sdano = ({r['meter_id'] for r in db.readings_for_period(period)}
                 if period else set())
        for m in db.list_meters(house_id):
            rs = db.meter_readings(m['id'], limit=3)
            beda = rashod_problema(rs)
            if beda:
                add(beda[0], f'{m["label"]}: {beda[1]}')
            if not m['serial']:
                add(YELLOW, f'{m["label"]}: нет заводского номера')
            if period and m['id'] not in sdano:
                add(YELLOW, f'{m["label"]}: показание за месяц не снято')

        # Работы с истёкшим сроком
        for w in db.list_works(house_id=house_id, open_only=True, limit=40):
            if w['deadline'] and w['deadline'] < today.isoformat():
                add(RED, f'просрочена работа: {w["title"]} (до {w["deadline"]})')

        # Паспорт
        if not db.get_passport(house_id):
            add(YELLOW, 'паспорт дома не заполнен')
    except Exception:
        log.exception('Не удалось собрать замечания по дому %s', house_id)

    found.sort(key=lambda f: 0 if f['level'] == RED else 1)
    return found


def house_level(findings) -> str | None:
    """Худший уровень из замечаний — для значка в списке домов."""
    if any(f['level'] == RED for f in findings):
        return RED
    if findings:
        return YELLOW
    return None
