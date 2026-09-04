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


# По этим байтам в начале файла видно, что за картинка на самом деле.
# Объявить PNG как jpeg нельзя: Google отвечает «Provided image is not valid»
SIGNATURY = (
    (b'\x89PNG\r\n\x1a\n', 'image/png'),
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'GIF87a', 'image/gif'),
    (b'GIF89a', 'image/gif'),
)


def opoznat(data: bytes) -> str | None:
    """Что это за картинка. None — если байты вообще не похожи на картинку."""
    for podpis, mime in SIGNATURY:
        if data.startswith(podpis):
            return mime
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if data[4:12] in (b'ftypheic', b'ftypheix', b'ftypmif1'):
        return 'image/heic'
    return None


async def _skachat(url: str):
    """(mime, base64) картинки — или None.

    Скачивать надо до конца. Первая версия брала resp.content.read(предел),
    а он отдаёт то, что успело накопиться в буфере, — картинка приходила
    обрезанной, и Google отвечал «Provided image is not valid». Читаем
    кусками, как это давно делает расшифровка голосовых.
    """
    predel = MAX_MB * 1024 * 1024
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    log.warning('Картинку не скачать: HTTP %s', resp.status)
                    return None
                kuski, vsego = [], 0
                async for kusok in resp.content.iter_chunked(1 << 16):
                    vsego += len(kusok)
                    if vsego > predel:
                        log.warning('Картинка больше %s МБ — пропускаю', MAX_MB)
                        return None
                    kuski.append(kusok)
        data = b''.join(kuski)
    except Exception:
        log.exception('Ошибка загрузки картинки')
        return None
    mime = opoznat(data)
    if not mime:
        # Ссылка протухла или отдали страницу вместо файла. Без этой записи
        # в логе «не смогла разобрать» неотличимо от отказа модели
        log.warning('Это не картинка: %s байт, начало %s', len(data), data[:16])
        return None
    return mime, base64.b64encode(data).decode()


class NeSkachalos(Exception):
    """Ни одной картинки получить не удалось — модель тут ни при чём."""


async def sobrat(urls: list, vopros: str | None = None) -> list:
    """Вопрос и картинки одним куском — как их ждёт модель."""
    soderzhimoe = [{'type': 'text',
                    'text': f"{ZADANIE}\n\nВопрос: {vopros or VOPROS_PO_UMOLCHANIYU}"}]
    for url in urls[:MAX_KARTINOK]:
        skachano = await _skachat(url)
        if skachano:
            mime, dannye = skachano
            soderzhimoe.append({
                'type': 'image_url',
                'image_url': {'url': f'data:{mime};base64,{dannye}'}})
    return soderzhimoe


async def prochitat(urls: list, vopros: str | None = None) -> str | None:
    """Ответ по картинкам. Несколько картинок — один ответ на все.

    Скриншоты длинной таблицы приходят пачкой, и ответ по ним нужен один:
    шесть отдельных кусков списка для обзвона бесполезны.
    """
    if not ai.enabled() or not urls:
        return None
    soderzhimoe = await sobrat(urls, vopros)
    if len(soderzhimoe) == 1:
        raise NeSkachalos(f'ни одной из {len(urls)} картинок')
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
