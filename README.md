# Flibusta Library Bot

Telegram-бот для поиска и отправки книг из Flibusta через INPX + сайт flibusta + email.

## Что умеет

- индексировать книги в локальный JSONL-индекс
- искать по автору, названию и комбинациям слов
- отправлять книги пользователю по email
- поддерживать формат отправки: `fb2`, `fb2.zip`, `epub`
- фильтровать поиск по языку книг через поле `language` в конфиге
- конвертировать `fb2 -> epub` через `ebook-convert` (Calibre)
- авторизовывать нового пользователя через OTP (`/otp 123456`)
- дробить вложения на несколько писем, если общий размер превышает лимит

## Быстрый старт
1. Скопируй пример конфига:
   ```bash
   cp config.example.json config.json
   ```
2. Заполни `config.json`.
   - для русского каталога укажи `"language": "ru"`
3. Подготовь источник книг:
   - положи локальный `.inpx` файл в рабочий каталог бота или укажи путь к нему в поле `inpx_name` в конфиге
   - сами книги бот получает с сайта Flibusta
4. Построй индекс:
   ```bash
   python3 library_bot.py index --config config.json
   ```
5. Проверь поиск:
   ```bash
   python3 library_bot.py search --config config.json --query "Пелевин Generation"
   ```
6. Запусти бота:
   ```bash
   python3 library_bot.py runbot --config config.json
   ```

## Команды в Telegram
- `/start` — запуск и авторизация
- `/search <запрос>` — поиск книги
- `/send <id,id,...>` — отправка найденных книг
- `/email user@kindle.com` — задать или сменить email
- `/format` — выбрать формат книг (`fb2`, `fb2.zip`, `epub`)
- `/help` — справка

## Настройка почты для отправки
В `config.json` должен быть заполнен SMTP-блок. Пример:

```json
{
  "smtp": {
    "host": "smtp.gmail.com",
    "port": 587,
    "username": "your_mail@gmail.com",
    "password": "your_app_password",
    "from_email": "your_mail@gmail.com",
    "use_tls": true
  }
}
```

### Что настроить
- `host` — SMTP-сервер почты
- `port` — порт SMTP
- `username` — логин SMTP
- `password` — пароль или app password
- `from_email` — адрес отправителя
- `use_tls` — использовать TLS

### Примеры SMTP
- Gmail: `smtp.gmail.com:587`
- Mail.ru: `smtp.mail.ru:587`
- Yandex: `smtp.yandex.ru:465` или `587`

### Важно для Gmail
Если используется Gmail, обычно нужен **App Password**, а не обычный пароль аккаунта.

### Куда бот отправляет книги
Бот отправляет книги на email, который хранится в `routing.user_email_map` и/или задаётся пользователем в Telegram:
- через `/email user@kindle.com`
- или в онбординге при первом входе

## Поля config.json

Ниже — описание полей из `config.json`.

### `inpx_name`
Путь к локальному файлу `.inpx`, который бот использует для построения индекса и поиска книг.

Пример:
```json
"inpx_name": "/root/.openclaw/workspace/bots/flibusta/inpx/flibusta_fb2_local.inpx"
```

### `telegram`
Настройки Telegram-бота.

- `telegram.bot_token` — токен Telegram-бота

### `routing`
Настройки сопоставления Telegram-пользователей и email-адресов для отправки книг.

- `routing.default_email` — старое fallback-поле, может присутствовать в рабочем конфиге
- `routing.user_email_map` — словарь соответствия `TelegramID -> email`

Пример:
```json
"routing": {
  "user_email_map": {
    "427611": "user@kindle.com"
  }
}
```

### `smtp_creds_path`
Путь к файлу с SMTP-учётными данными, если используется файловая схема хранения.

### `smtp`
SMTP-настройки для отправки писем, если используются напрямую в конфиге.

- `smtp.host` — SMTP-сервер
- `smtp.port` — SMTP-порт
- `smtp.username` — логин
- `smtp.password` — пароль или app password
- `smtp.from_email` — адрес отправителя
- `smtp.use_tls` — использовать TLS

### `max_email_bytes`
Максимальный размер одного письма в байтах. Если книги не помещаются, бот разобьёт отправку на несколько писем.

### `search_page_size`
Количество книг на одной странице выдачи поиска.

### `language`
Язык книг для фильтрации поиска.

Пример:
```json
"language": "ru"
```

### `work_dir`
Рабочий каталог бота, где он хранит runtime-файлы:
- индекс
- состояние диалога
- привязки пользователей
- кэш
- логи отправки

## Важно
- Для Kindle обычно лучше использовать `epub`
- Не публикуй в репозитории реальные токены, SMTP-пароли и рабочий `config.json`
- Для скачивания книг требуется доступность сайта Flibusta с сервера, где запущен бот
