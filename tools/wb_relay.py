#!/usr/bin/env python3
"""Релей до Wildberries для Евы.

Ева живёт на Railway, а WB режет адрес Railway целиком — 403 на каждый
запрос, проверено вживую (35 из 35). Собственный адрес Айхора это не
блокирует, поэтому Ева стучится сюда, а этот код уже сам ходит на WB
и отдаёт ей ответ как есть.

Только стандартная библиотека Python — на свежем сервере ничего не
нужно доустанавливать, `python3 wb_relay.py` работает сразу.

Ничего не хранит и никуда не пишет: пришёл запрос — ушёл на WB — ответ
вернулся, только пока висит в памяти на секунду. Без токена в заголовке
ручка не отвечает вообще (404, чтобы снаружи не было видно, что тут
что-то есть — тот же приём, что в ручке для Люси).

Переменные окружения:
  WB_RELAY_TOKEN  — пропуск, обязателен, без него сервис не стартует
  WB_RELAY_PORT   — порт, по умолчанию 8899
"""
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = (os.environ.get("WB_RELAY_TOKEN") or "").strip()
PORT = int(os.environ.get("WB_RELAY_PORT") or "8899")

WB_URL = "https://search.wb.ru/exactmatch/ru/common/v4/search"
TIMEOUT = 15

# Без него WB отвечает заметно неохотнее
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# Только эти ключи пересылаем на WB — мало ли что подсунут в запросе
RAZRESHENO = {"query", "resultset", "limit", "dest", "curr", "lang", "spp"}


class Ruchka(BaseHTTPRequestHandler):
    server_version = "wb-relay/1"

    def _proveren(self) -> bool:
        zagolovok = self.headers.get("Authorization", "")
        ozhidaetsya = f"Bearer {TOKEN}"
        return bool(TOKEN) and secrets.compare_digest(zagolovok, ozhidaetsya)

    def _otkazat(self):
        # 404, а не 401 — снаружи не должно быть видно, что тут вообще что-то есть
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        razbor = urllib.parse.urlparse(self.path)
        if razbor.path != "/wb/search" or not self._proveren():
            self._otkazat()
            return

        vhod = urllib.parse.parse_qs(razbor.query)
        parametry = {k: v[0] for k, v in vhod.items() if k in RAZRESHENO}
        if not parametry.get("query"):
            self.send_response(400)
            self.end_headers()
            self.wfile.write("query обязателен".encode("utf-8"))
            return

        url = f"{WB_URL}?{urllib.parse.urlencode(parametry)}"
        zapros = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(zapros, timeout=TIMEOUT) as otvet:
                telo = otvet.read()
                status = otvet.status
        except urllib.error.HTTPError as e:
            telo = e.read()
            status = e.code
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))
            return

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(telo)))
        self.end_headers()
        self.wfile.write(telo)

    def log_message(self, format, *args):
        pass  # тихо: это фоновый сервис, не веб-приложение с логами для человека


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Задай WB_RELAY_TOKEN в окружении перед запуском")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Ruchka)
    print(f"WB-релей слушает на :{PORT}")
    server.serve_forever()
