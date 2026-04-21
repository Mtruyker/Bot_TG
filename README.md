# Telegram-бот для Яндекс.Музыки

Бот принимает ссылку на трек Яндекс.Музыки и возвращает название, артиста и длительность.

Поддерживается формат ссылки:

```text
https://music.yandex.ru/album/1193829/track/10994777
```

## Запуск

1. Создайте Telegram-бота через `@BotFather` и получите токен.
2. Установите зависимости:

```powershell
pip install -r requirements.txt
```

3. Передайте токен бота через переменную окружения:

```powershell
$env:TELEGRAM_BOT_TOKEN="ваш_токен_telegram"
```

4. При необходимости добавьте токен Яндекс.Музыки:

```powershell
$env:YANDEX_MUSIC_TOKEN="ваш_токен_yandex_music"
```

5. Если сервер не имеет прямого доступа к Telegram API, укажите HTTP/SOCKS-прокси:

```powershell
$env:TELEGRAM_PROXY_URL="http://user:password@host:port"
```

6. Запустите бота:

```powershell
python yandex_music_telegram_bot.py
```
