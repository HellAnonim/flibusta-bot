# Flibusta Library Bot

Telegram-бот для поиска и отправки книг из Flibusta через INPX + сайт flibusta + email.

## Что умеет
- читать локальный файл `flibusta_fb2_local.inpx`
- работать с локальным файлом `.inpx`
- индексировать книги в локальный JSONL-индекс
- искать по автору, названию и комбинациям слов
- извлекать книгу из архивов `.zip` на WebDAV
- отправлять книги пользователю по email
- поддерживать формат отправки: `fb2`, `fb2.zip`, `epub`
- конвертировать `fb2 -> epub` через `ebook-convert` (Calibre)
- авторизовывать нового пользователя через OTP (`/otp 123456`)
- дробить вложения на несколько писем, если общий размер превышает лимит

## Быстрый старт
1. Скопируй пример конфига:
   ```bash
   cp config.example.json config.json
   ```
2. Заполни `config.json`.
3. Подготовь источник книг:
   - положи локальный `.inpx` файл в рабочий каталог бота или укажи путь к нему в конфиге
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

## Локальный файл INPX
Бот умеет работать не только через WebDAV, но и с локальным `.inpx` файлом.

Что нужно:
- положить `.inpx` файл в каталог бота или в отдельный каталог на сервере
- указать корректный путь в конфиге, если используется локальный источник
- после обновления `.inpx` пересобрать индекс командой:

```bash
python3 library_bot.py index --config config.json
```

Локальный `.inpx` используется для индексации и поиска, а сами файлы книг бот скачивает с сайта Flibusta.

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
Бот отправляет книги на email, который пользователь указывает в Telegram:
- через `/email user@kindle.com`
- или в онбординге при первом входе

## Важно
- Для Kindle обычно лучше использовать `epub`
- Не публикуй в репозитории реальные токены, SMTP-пароли и рабочий `config.json`
- Для скачивания книг требуется доступность сайта Flibusta с сервера, где запущен бот
