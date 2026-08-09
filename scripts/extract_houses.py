#!/usr/bin/env python3
"""Извлекает дома (адрес, звено, координаты, число заявок) из index.html (folium-карта)
и сохраняет в bot/data/houses.json. Запускать из корня репозитория после обновления карты."""
import json
import re
import sys

html = open('index.html', encoding='utf-8').read()

markers = [(m.start(), float(m.group(1)), float(m.group(2)))
           for m in re.finditer(r'L\.circleMarker\(\s*\[([\d.]+),\s*([\d.]+)\]', html)]

popups = [(m.start(), m.group(1).strip(), int(m.group(2)), int(m.group(3)))
          for m in re.finditer(r'<b>([^<]+)</b><br>Звено (\d)<br>Заявок: (\d+)', html)]

if len(markers) != len(popups):
    sys.exit(f'Не совпало число маркеров ({len(markers)}) и попапов ({len(popups)})')

houses = []
for i, ((_, lat, lng), (_, addr, zveno, req)) in enumerate(zip(markers, popups)):
    houses.append({'id': i, 'address': addr, 'zveno': zveno,
                   'lat': lat, 'lng': lng, 'requests_year': req})

houses.sort(key=lambda h: (h['zveno'], h['address']))
for i, h in enumerate(houses):
    h['id'] = i

with open('bot/data/houses.json', 'w', encoding='utf-8') as f:
    json.dump(houses, f, ensure_ascii=False, indent=1)

print(f'OK: {len(houses)} домов, звенья:',
      {z: sum(1 for h in houses if h["zveno"] == z) for z in sorted({h["zveno"] for h in houses})})
