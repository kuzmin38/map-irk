#!/usr/bin/env python3
"""Собирает мини-приложение MAX: вшивает данные из bot/data в miniapp/index.html.

Запускать из корня репозитория после любого обновления данных:
    python3 scripts/build_miniapp.py

Привязку домов к ЖК берём из bot/data/house_complex.json (если есть) —
это выгрузка из рабочей базы бота, чтобы приложение показывало те же ЖК.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'bot', 'data')


def load(name, default=None):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)


houses = load('houses.json')
complexes = {c['id']: c['name'] for c in load('complexes.json')}
risers = load('risers.json')
docs = load('docs_catalog.json', [])
directory = load('directory.json')['sections']
house_complex = load('house_complex.json', {})  # {"<id дома>": "<id ЖК>"}

for h in houses:
    h.pop('zveno', None)  # звенья больше не используются
    cx = house_complex.get(str(h['id']))
    if cx:
        h['complex'] = cx

payload = {
    'houses': houses,
    'complexes': complexes,
    'risers': risers,
    'docs': docs,
    'directory': directory,
}

with open(os.path.join(ROOT, 'miniapp', 'template.html'), encoding='utf-8') as f:
    html = f.read()

marker = '/*__DATA__*/{}'
if marker not in html:
    raise SystemExit('В шаблоне не найден маркер для данных')
html = html.replace(marker, json.dumps(payload, ensure_ascii=False, separators=(',', ':')))

out = os.path.join(ROOT, 'miniapp', 'index.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)

print(f'Готово: {out}')
print(f'  домов: {len(houses)} (с привязкой к ЖК: {len(house_complex)})')
print(f'  таблиц стояков: {len(risers)}, документов: {len(docs)}, '
      f'разделов справочника: {len(directory)}')
print(f'  размер страницы: {os.path.getsize(out) / 1024:.0f} КБ')
