"""Люся смотрит присланные картинки.

Заказчик скинул шесть скриншотов таблицы жильцов и попросил список квартир
с телефонами для обзвона. Люся ответила «у меня нет доступа к номерам
телефонов жильцов» — и это была неправда: номера лежали прямо в сообщении,
она их не открывала. До сих пор она умела читать только фотографии
счётчиков, и то по отдельной команде.

Здесь общее зрение: что на картинке, то и прочитано. Отдельно — правило
про персональные данные. ФИО, телефоны и лицевые счета жильцов Люся
показывает тому, кто их же и прислал, но в базу не пишет: оттуда они уйдут
в паспорт дома, в выгрузку инженеру и в отчёт руководителю, и вычистить
их потом будет неоткуда.
"""
import base64
import logging
import os
import re

import aiohttp

from . import ai

log = logging.getLogger('kartinki')

MODEL = os.environ.get('OPENROUTER_VISION_MODEL',
                       os.environ.get('OPENROUTER_AUDIO_MODEL',
                                      'google/gemini-2.5-flash'))
MAX_KARTINOK = 10          # больше в один вопрос не отправляем
MAX_MB = 8                 # на картинку

ZADANIE = (
    'На картинках — то, что прислал сантехник управляющей компании: '
    'скриншот таблицы, фотография прибора, документа, места работ или '
    'переписки.\n\n'
    'Прочитай и ответь на вопрос человека. Правила, нарушать нельзя:\n'
    '— только то, что видно. Ничего не додумывай: ни цифр, ни фамилий, '
    'ни адресов, которых на картинке нет;\n'
    '— если картинок несколько, это части одного целого: сведи их вместе, '
    'не повторяя строки дважды;\n'
    '— таблицу передавай построчно, коротко, без рамок и заголовков '
    'колонок, если о них не спросили;\n'
    '— цифры перепроверь по картинке: номер, прочитанный неверно, хуже '
    'непрочитанного. Не разобрал — так и напиши, вместо того чтобы гадать;\n'
    '— без вступлений и без оценок, сразу по делу.'
)

VOPROS_PO_UMOLCHANIYU = (
    'Что на картинках? Ответь коротко: что это за документ или место и '
    'какие данные на нём видны. Содержимое целиком не переписывай.')

# Похоже ли прочитанное на персональные данные жильцов
TELEFON = re.compile(r'(?:\+7|\b8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\b')
LICEVOY = re.compile(r'лицев\w*\s*счет\w*|л/с\s*№?\s*\d', re.I)
FIO = re.compile(r'\b[А-ЯЁ][а-яё]+(?:ов|ев|ин|ын|ский|цкий|ко|ва|ева|ина|ына|ская|цкая)\b'
                 r'\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:ович|евич|овна|евна|ична)\b')


def lichnye_dannye(text: str) -> bool:
    """Есть ли в прочитанном персональные данные жильцов.

    Хватает двух совпадений: один телефон может оказаться диспетчерской,
    а вот столбец телефонов — это уже список жильцов.
    """
    if not text:
        return False
    ochki = (len(TELEFON.findall(text)) + len(FIO.findall(text))
             + len(LICEVOY.findall(text)))
    return ochki >= 2


async def _skachat(url: str) -> str | None:
    """Картинка в base64 — MAX отдаёт их по временным ссылкам."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    log.warning('Картинку не скачать: HTTP %s', resp.status)
                    return None
                data = await resp.content.read(MAX_MB * 1024 * 1024 + 1)
        if len(data) > MAX_MB * 1024 * 1024:
            log.warning('Картинка больше %s МБ — пропускаю', MAX_MB)
            return None
        return base64.b64encode(data).decode()
    except Exception:
        log.exception('Ошибка загрузки картинки')
        return None


async def prochitat(urls: list, vopros: str | None = None) -> str | None:
    """Ответ по картинкам. Несколько картинок — один ответ на все.

    Скриншоты длинной таблицы приходят пачкой, и ответ по ним нужен один:
    шесть отдельных кусков списка для обзвона бесполезны.
    """
    if not ai.enabled() or not urls:
        return None
    soderzhimoe = [{'type': 'text',
                    'text': f"{ZADANIE}\n\nВопрос: {vopros or VOPROS_PO_UMOLCHANIYU}"}]
    for url in urls[:MAX_KARTINOK]:
        dannye = await _skachat(url)
        if dannye:
            soderzhimoe.append({
                'type': 'image_url',
                'image_url': {'url': f'data:image/jpeg;base64,{dannye}'}})
    if len(soderzhimoe) == 1:
        return None
    payload = {
        'model': MODEL,
        'messages': [{'role': 'user', 'content': soderzhimoe}],
        'max_tokens': 3000,
        'temperature': 0,
    }
    headers = {
        'Authorization': f'Bearer {ai.KIMI_API_KEY}',
        'HTTP-Referer': 'https://github.com/kuzmin38/map-irk',
        'X-Title': 'Lusya Bot',
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f'{ai.OPENROUTER_BASE_URL}/chat/completions',
                              json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=180)) as resp:
                body = await resp.json()
                if resp.status != 200:
                    log.error('Чтение картинок не удалось, OpenRouter %s: %s',
                              resp.status, body)
                    return None
                return (body['choices'][0]['message'].get('content') or '').strip() or None
    except Exception:
        log.exception('Ошибка чтения картинок')
        return None
