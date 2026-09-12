# Релей до Wildberries

Ева на Railway заблокирована Wildberries целиком (403 на каждый запрос).
Айхор — нет. Этот файл превращает Айхор в промежуточную остановку:
Ева стучится сюда с токеном, а этот код уже сам ходит на WB.

Только стандартная библиотека — на сервере ничего доустанавливать не надо.

## Запуск на Айхоре

```bash
curl -o /root/wb_relay.py https://raw.githubusercontent.com/kuzmin38/map-irk/main/tools/wb_relay.py

python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# сохрани, что напечатает — это токен

cat > /etc/systemd/system/wb-relay.service << 'EOF'
[Unit]
Description=WB relay for Eva
After=network.target

[Service]
Environment=WB_RELAY_TOKEN=ВСТАВЬ_СЮДА_ТОКЕН
ExecStart=/usr/bin/python3 /root/wb_relay.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now wb-relay
systemctl status wb-relay --no-pager

# проверка с самого сервера
curl -H "Authorization: Bearer ВСТАВЬ_СЮДА_ТОКЕН" \
  "http://127.0.0.1:8899/wb/search?query=nasos&resultset=catalog&limit=1&dest=-5827722&curr=rub&lang=ru&spp=30"
```

Если firewall (`ufw status` покажет `active`) — открыть порт снаружи:

```bash
ufw allow 8899/tcp
```

## Переменные для Евы (Railway → worker)

```
WB_RELAY_URL   = http://<внешний IP Айхора>:8899
WB_RELAY_TOKEN = <тот же токен>
```
