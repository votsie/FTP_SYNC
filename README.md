# FTP Sync Server — Полная документация

## Оглавление

1. [Обзор проекта](#1-обзор-проекта)
2. [Архитектура](#2-архитектура)
3. [config.json — Конфигурация](#3-configjson--конфигурация)
4. [main.py — Основная программа](#4-mainpy--основная-программа)
5. [service_watchdog.py — Служба перезапуска](#5-service_watchdogpy--служба-перезапуска)
6. [installer.iss — Скрипт установщика](#6-installeriss--скрипт-установщика)
7. [build.bat — Скрипт сборки](#7-buildbat--скрипт-сборки)
8. [requirements.txt — Зависимости](#8-requirementstxt--зависимости)
9. [API-эндпоинты](#9-api-эндпоинты)
10. [Трей-меню](#10-трей-меню)
11. [Логи](#11-логи)

---

## 1. Обзор проекта

FTP Sync Server — программа для Windows, которая:
- Поднимает **локальный FTP-сервер** (принимает файлы от клиентов)
- **Автоматически выгружает** все полученные файлы на **удалённый FTP-сервер**
- Работает **в фоне** (значок в системном трее, без консоли и окон)
- Предоставляет **REST API** для мониторинга и управления
- Имеет **watchdog-процесс**, который перезапускает сервер при падении
- Устанавливается через **графический установщик** (Inno Setup)

### Три режима синхронизации

| Режим | Когда срабатывает | Что делает |
|-------|-------------------|------------|
| **Watchdog (мгновенный)** | Сразу при появлении файла | Загружает новый/изменённый файл на удалённый FTP |
| **Periodic sync** | Каждые N секунд (по умолч. 30) | Полная загрузка всех локальных файлов |
| **Mirror sync** | Каждые N дней (по умолч. 3) | Сверка локального с удалённым, загрузка новых, удаление orphan-файлов |

### Схема работы

```
Клиент ──FTP──▶ Локальный FTP (pyftpdlib :2121)
                       │
                       ├─ watchdog (файловая система) ──▶ мгновенная загрузка на удалённый FTP
                       ├─ periodic_sync_loop (таймер)  ──▶ полная синхронизация каждые 30с
                       └─ mirror_sync_loop (таймер)    ──▶ mirror-сверка каждые 3 дня

FastAPI (uvicorn :8000) ──▶ REST API для управления и мониторинга
pystray                 ──▶ иконка в системном трее
```

---

## 2. Архитектура

### Процессы

| Процесс | EXE-файл | Назначение |
|---------|----------|------------|
| Основной сервер | `ftp_sync_server.exe` | FTP-сервер + синхронизация + API + трей |
| Watchdog | `ftp_sync_watchdog.exe` | Следит за сервером, перезапускает при падении |

### Потоки внутри основного процесса (main.py)

| Поток | Функция | Тип |
|-------|---------|-----|
| **main thread** | `run_tray()` — иконка в трее | Блокирующий (основной) |
| **server_thread** | `uv_server.run()` — uvicorn + FastAPI | daemon-поток |
| **ftp_thread** | `start_local_ftp_server()` — pyftpdlib FTP | daemon-поток |
| **sync_thread** | `periodic_sync_loop()` — периодическая синхронизация | daemon-поток |
| **mirror_thread** | `mirror_sync_loop()` — mirror-синхронизация | daemon-поток |
| **observer** | watchdog.Observer — слежение за файловой системой | daemon-поток |

Все фоновые потоки — daemon, т.е. завершаются автоматически при остановке основного.

### Файлы, создаваемые программой при работе

| Файл | Где создаётся | Описание |
|------|---------------|----------|
| `ftp_sync.log` | Рядом с EXE | Лог основного сервера |
| `watchdog.log` | Рядом с EXE | Лог watchdog-процесса |
| `ftp_root/` (или указанная папка) | По настройке | Папка для входящих FTP-файлов |

---

## 3. config.json — Конфигурация

Файл конфигурации, который лежит рядом с EXE. Создаётся установщиком из данных, введённых пользователем. Формат — JSON с четырьмя секциями.

### Полный пример с пояснениями

```json
{
    "local_ftp": {
        "host": "0.0.0.0",        // IP для прослушивания. "0.0.0.0" = все интерфейсы
        "port": 2121,              // Порт локального FTP-сервера
        "user": "localuser",       // Логин для подключения к локальному FTP
        "password": "localpass",   // Пароль для подключения к локальному FTP
        "root": "./ftp_root",      // Папка для хранения полученных файлов (относительный или абсолютный путь)
        "permissions": "elradfmw"  // Права пользователя FTP (все операции)
    },
    "remote_ftp": {
        "host": "ftp.example.com", // Адрес удалённого FTP-сервера для выгрузки
        "port": 21,                // Порт удалённого FTP
        "user": "remoteuser",      // Логин на удалённом FTP
        "password": "remotepass",  // Пароль на удалённом FTP
        "root": "/",               // Корневая папка на удалённом сервере
        "tls": false               // true = использовать FTPS (шифрованное соединение)
    },
    "sync": {
        "interval_seconds": 30,    // Интервал периодической полной синхронизации (секунды)
        "on_upload": true          // true = загружать файл мгновенно при появлении
    },
    "mirror": {
        "interval_days": 3,        // Интервал mirror-синхронизации (дни)
        "delete_orphans": true     // true = удалять с удалённого файлы, которых нет локально
    }
}
```

### Расшифровка permissions (FTP-права)

| Буква | Значение |
|-------|----------|
| e | Вход в директорию (CWD) |
| l | Листинг файлов (LIST, NLST, MLSD) |
| r | Чтение файлов (RETR) |
| a | Дозапись в файл (APPE) |
| d | Удаление файлов (DELE, RMD) |
| f | Переименование (RNFR, RNTO) |
| m | Создание директорий (MKD) |
| w | Запись файлов (STOR, STOU) |

---

## 4. main.py — Основная программа

Главный файл проекта (867 строк). Содержит: FTP-сервер, FastAPI REST API, систему синхронизации, иконку в трее.

### Строки 1-9: Docstring модуля

```python
"""
FTP Sync Server
===============
Локальный FTP-сервер на базе pyftpdlib + FastAPI для управления.
...
"""
```
Описание назначения программы. Чисто информативное, ни на что не влияет.

### Строки 11-34: Импорты

```python
import os          # Работа с ОС: devnull, startfile для открытия файлов
import sys         # sys.frozen (проверка PyInstaller), sys.exit, sys.executable
import json        # Парсинг config.json
import time        # time.sleep (задержки), time.strftime (форматирование дат)
import ctypes      # ctypes.windll.user32.MessageBoxW — вызов Windows MessageBox
import ftplib      # FTP-клиент стандартной библиотеки Python (подключение к удалённому FTP)
import socket      # Проверка свободности портов через socket.connect_ex
import logging     # Логирование в файл ftp_sync.log
import threading   # Потоки: FTP-сервер, sync-циклы, uvicorn — всё в отдельных потоках
from pathlib import Path          # Удобная работа с путями (кроссплатформенная)
from contextlib import asynccontextmanager  # Для lifespan FastAPI (инициализация/завершение)

from fastapi import FastAPI, HTTPException  # Веб-фреймворк для REST API
from pydantic import BaseModel, Field       # Валидация данных для API-моделей

from pyftpdlib.authorizers import DummyAuthorizer  # Авторизация пользователей FTP
from pyftpdlib.handlers import FTPHandler          # Обработчик FTP-команд
from pyftpdlib.servers import FTPServer            # Сам FTP-сервер

from watchdog.observers import Observer            # Наблюдатель за файловой системой
from watchdog.events import FileSystemEventHandler # Обработчик событий ФС (создание/изменение файлов)

import pystray                     # Иконка в системном трее Windows
from PIL import Image, ImageDraw   # Библиотека изображений — рисуем иконку программно
```

### Строки 36-44: Определение рабочей директории

```python
if getattr(sys, "frozen", False):   # frozen = True когда запущен как PyInstaller EXE
    APP_DIR = Path(sys.executable).parent  # Папка, где лежит EXE-файл
else:
    APP_DIR = Path(__file__).parent  # Папка, где лежит main.py (режим разработки)

CONFIG_PATH = APP_DIR / "config.json"  # Путь к файлу конфигурации
LOG_PATH = APP_DIR / "ftp_sync.log"    # Путь к файлу лога
```

**Зачем:** PyInstaller при сборке `--onefile` распаковывает файлы во временную папку (`_MEIPASS`).
Но config.json и логи должны лежать рядом с EXE, а не во временной папке.
`sys.executable` — это путь к самому EXE, `.parent` — его директория.

### Строки 46-51: Перенаправление stdout/stderr

```python
if sys.stdout is None:              # При --noconsole (без консоли) stdout = None
    sys.stdout = open(os.devnull, "w")  # Перенаправляем в "чёрную дыру" (/dev/null)
if sys.stderr is None:              # Аналогично для stderr
    sys.stderr = open(os.devnull, "w")
```

**Зачем:** PyInstaller с флагом `--noconsole` убирает консоль.
При этом `sys.stdout` и `sys.stderr` становятся `None`.
Библиотеки (uvicorn, pyftpdlib), которые пытаются в них писать, упадут с ошибкой.
Перенаправление в devnull предотвращает краши.

### Строки 55-57: show_error()

```python
def show_error(message: str):
    ctypes.windll.user32.MessageBoxW(0, message, "FTP Sync Server - Ошибка", 0x10)
```

Вызывает нативный Windows MessageBox (иконка ошибки, `0x10 = MB_ICONERROR`).
Используется вместо print(), т.к. консоли нет.
Параметры: `0` = без родительского окна, `message` = текст, далее заголовок и флаги.

### Строки 60-98: load_config()

```python
def load_config(path: Path) -> dict:
```

Читает `config.json` и преобразует вложенную JSON-структуру в плоский словарь.

| Строка | Что делает |
|--------|-----------|
| 62-64 | Если файла нет — MessageBox с ошибкой и выход |
| 67 | Открывает файл с кодировкой `utf-8-sig` (обрабатывает BOM, который добавляет Inno Setup) |
| 68 | Парсит JSON в Python-словарь |
| 69-71 | При ошибке парсинга — MessageBox и выход |
| 73-76 | Извлекает 4 секции из JSON: `local_ftp`, `remote_ftp`, `sync`, `mirror` |
| 78-98 | Формирует плоский dict с приведением типов: `int()` для портов/интервалов, `bool()` для флагов |

**Дефолтные значения:** каждый `.get()` имеет значение по умолчанию на случай отсутствия ключа.

### Строка 101: Загрузка конфигурации

```python
CONFIG = load_config(CONFIG_PATH)
```

Выполняется при импорте модуля. Если config.json невалиден — программа завершится здесь.

### Строки 105-112: Настройка логирования

```python
logging.basicConfig(
    level=logging.INFO,                    # Уровень: INFO и выше (WARNING, ERROR)
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",  # Формат: "2024-01-01 12:00:00 [INFO] ftp_sync: ..."
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),  # Запись в файл ftp_sync.log (UTF-8)
    ],
)
logger = logging.getLogger("ftp_sync")  # Именованный логгер для этого модуля
```

**Важно:** логи идут ТОЛЬКО в файл, не в консоль (консоли нет).

### Строки 116-190: run_startup_checks()

Функция самотестирования. Запускается перед стартом сервера. Выполняет 5 проверок:

| Проверка | Строки | Критичность | Что проверяет |
|----------|--------|-------------|---------------|
| 1. config.json | 121-125 | ERROR | Существует ли файл конфигурации |
| 2. FTP root | 127-133 | ERROR | Можно ли создать/найти директорию для файлов |
| 3. Порт FTP | 135-147 | WARN | Свободен ли порт 2121 (или заданный) |
| 4. Порт API | 149-161 | WARN | Свободен ли порт 8000 |
| 5. Удалённый FTP | 163-178 | WARN | Можно ли подключиться к удалённому серверу |

**Логика проверки порта (строки 138-142):**
```python
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # Создаём TCP-сокет
sock.settimeout(1)                                          # Таймаут 1 секунда
result = sock.connect_ex(("127.0.0.1", local_port))        # Пытаемся подключиться
sock.close()
if result == 0:  # 0 = подключение успешно = порт занят кем-то
```

`connect_ex` возвращает 0 если порт занят (кто-то на нём слушает), иначе код ошибки.

**Поведение:**
- ERROR → MessageBox + return False → программа не запускается
- WARN → только запись в лог → программа запускается (FTP-сервер может быть временно недоступен)

### Строки 195-205: create_tray_image()

```python
def create_tray_image(color: str = "#1976D2") -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))   # Прозрачное изображение 64x64
    draw = ImageDraw.Draw(img)                           # Объект для рисования
    draw.ellipse([2, 2, 62, 62], fill=color)            # Синий круг (Material Blue 700)
    # Буква S — ломаная линия из 5 точек (как символ синхронизации)
    draw.line([(38,16),(22,16),(22,32),(42,32),(42,48),(22,48)], fill="white", width=4)
    draw.polygon([(18,18),(24,12),(24,24)], fill="white")  # Стрелка-треугольник вверху
    draw.polygon([(46,46),(40,40),(40,52)], fill="white")  # Стрелка-треугольник внизу
    return img
```

Иконка генерируется программно через Pillow, чтобы не зависеть от внешнего .ico файла.
Выглядит как синий круг с буквой «S» и двумя стрелками (символ синхронизации).

### Строки 210-236: Pydantic-модели

Модели данных для валидации запросов и ответов REST API:

| Модель | Назначение | Используется в |
|--------|-----------|---------------|
| `RemoteConfig` | Входные данные для PUT `/config/remote` | `update_remote_config()` |
| `SyncStatus` | Статус обычной синхронизации | (определена, но используется неявно через dict) |
| `SyncResult` | Ответ POST `/sync` | `force_sync()` |
| `MirrorResult` | Ответ POST `/mirror` | `force_mirror()` |

`Field(...)` — обязательное поле (без значения по умолчанию).
`Field(21)` — необязательное поле со значением по умолчанию.

### Строки 240-489: class RemoteFTPClient

Класс-обёртка для всех операций с удалённым FTP-сервером.

#### `__init__` (строки 243-250)
Сохраняет параметры подключения в атрибуты экземпляра.

#### `_connect()` (строки 252-263)
Создаёт и возвращает новое FTP-соединение:
1. Создаёт объект `FTP()` или `FTP_TLS()` в зависимости от настройки TLS
2. `ftp.connect()` — устанавливает TCP-соединение (таймаут 30с)
3. `ftp.login()` — авторизация логином/паролем
4. `ftp.prot_p()` — если TLS, переключает канал данных в защищённый режим
5. `ftp.cwd()` — переходит в корневую директорию (если указана)

**Важно:** каждый вызов `_connect()` создаёт НОВОЕ соединение. Это сделано намеренно —
FTP-соединения могут разрываться, и проще пересоздать, чем отслеживать состояние.

#### `_ensure_remote_dir()` (строки 265-280)
Рекурсивно создаёт директории на удалённом сервере.
Пример: для пути `a/b/c` создаст `/a`, `/a/b`, `/a/b/c`.
Пробует сначала `cwd` (перейти), если не получается — `mkd` (создать).
В конце возвращается в корень `ftp.cwd(self.root)`.

#### `upload_file()` (строки 282-301)
Загружает один файл на удалённый сервер:
1. Открывает соединение (`_connect`)
2. Определяет удалённую директорию из пути файла (`Path(relative_path).parent`)
3. Создаёт директорию если нужно (`_ensure_remote_dir`)
4. Переходит в неё (`cwd`)
5. Загружает файл бинарно (`storbinary STOR filename`)
6. Закрывает соединение (`ftp.quit()` в `finally` — гарантированно)

Возвращает `True` при успехе, `False` при ошибке. Ошибки логируются.

#### `sync_all()` (строки 303-313)
Обходит все файлы в локальной директории рекурсивно (`rglob("*")`)
и загружает каждый через `upload_file()`. Собирает списки успешных и неудачных.

#### `test_connection()` (строки 315-322)
Пробует подключиться и сразу отключиться. Возвращает `True/False`.

#### `_list_remote_files()` (строки 326-364)
Рекурсивно собирает все файлы на удалённом сервере через команду `MLSD`.

**MLSD** — современная FTP-команда, возвращающая машинно-читаемый список файлов.
Формат ответа: `type=file;size=12345; filename.txt`

Алгоритм:
1. Запоминает текущую директорию (`pwd`)
2. Переходит в запрошенную (`cwd`)
3. Получает список через `MLSD` (строка 337)
4. Парсит каждую строку: разделяет по `"; "` на факты и имя
5. Из фактов извлекает `type` и `size`
6. Для директорий — рекурсивный вызов
7. Для файлов — добавляет в словарь `{путь: размер}`
8. Возвращается в исходную директорию

#### `_list_remote_files_fallback()` (строки 366-397)
Фолбэк для серверов, не поддерживающих MLSD (старые FTP-серверы).
Использует `NLST` (список имён) + `SIZE` (размер файла).
Отличает файлы от директорий попыткой `cwd` — если получается, это директория.

#### `_delete_remote_file()` (строки 399-406)
Удаляет один файл на удалённом сервере командой `DELETE`.

#### `_remove_empty_dirs()` (строки 408-423)
Рекурсивно удаляет пустые директории снизу вверх.
Проверяет содержимое через `MLSD`, фильтрует `.` и `..`.
Если пусто — `rmd` (удалить директорию), затем проверяет родителя.

#### `mirror_sync()` (строки 425-489)
Главная функция mirror-синхронизации. Алгоритм:

1. **Подключение** (строки 433-437) — если не удалось, возвращает ошибку
2. **Сканирование удалённого** (строки 440-442) — `_list_remote_files` собирает все файлы
3. **Сканирование локального** (строки 444-448) — `rglob("*")` + `stat().st_size`
4. **Закрытие первого соединения** (строка 451) — данные собраны, соединение больше не нужно
5. **Загрузка новых/изменённых** (строки 453-462):
   - Для каждого локального файла проверяет: есть ли он на сервере с таким же размером?
   - Если размер совпадает — пропускаем (skipped)
   - Если нет или отличается — загружаем (uploaded/failed)
6. **Удаление orphan-файлов** (строки 464-483):
   - Orphan = файл есть на сервере, но нет локально
   - `orphans = set(remote_files.keys()) - set(local_files.keys())`
   - Открывает второе соединение (`ftp2`) специально для удаления
   - Удаляет в обратном порядке (`reverse=True`) — сначала вложенные
   - Затем чистит пустые директории

### Строки 492-524: Глобальное состояние и init_remote_client()

```python
sync_state = {...}    # Счётчики обычной синхронизации (synced, failed, last_sync, is_running)
mirror_state = {...}  # Счётчики mirror-синхронизации (uploaded, deleted, skipped, failed, ...)
remote_client = None  # Экземпляр RemoteFTPClient (создаётся в init_remote_client)
ftp_root = None       # Путь к директории FTP-файлов (задаётся в lifespan)
```

`init_remote_client()` — создаёт экземпляр `RemoteFTPClient` из текущего CONFIG.
Вызывается при старте и при обновлении настроек через API.

### Строки 529-553: class FTPUploadHandler

Обработчик событий файловой системы (watchdog). Реагирует на появление/изменение файлов:

- `on_created` — новый файл в директории FTP
- `on_modified` — файл изменился
- `_sync_file` — ждёт 0.5с (чтобы файл дозаписался), затем загружает на удалённый FTP

**Задержка 0.5с:** необходима, т.к. при FTP-загрузке большого файла watchdog может среагировать
до завершения записи. Без задержки отправится неполный файл.

### Строки 558-568: periodic_sync_loop()

Бесконечный цикл полной синхронизации:
1. Спит `interval` секунд
2. Помечает `is_running = True`
3. Вызывает `sync_all()` — загружает ВСЕ файлы из FTP root
4. Обновляет счётчики и время последней синхронизации
5. Помечает `is_running = False`

### Строки 573-609: Mirror-синхронизация

`run_mirror_sync()` — запускает одну итерацию mirror, обновляет глобальное состояние.

`mirror_sync_loop()` — бесконечный цикл:
1. Вычисляет время следующей синхронизации
2. Спит `interval_days * 86400` секунд (дни → секунды)
3. Вызывает `run_mirror_sync()`

### Строки 614-636: start_local_ftp_server()

Настройка и запуск локального FTP-сервера (pyftpdlib):

```python
authorizer = DummyAuthorizer()       # Менеджер пользователей
authorizer.add_user(user, pass, root, perm=...)  # Добавляем пользователя с правами

handler = FTPHandler                  # Обработчик FTP-протокола
handler.authorizer = authorizer       # Привязываем авторизацию
handler.passive_ports = range(60000, 60100)  # Порты для пассивного режима FTP
handler.banner = "FTP Sync Server ready."    # Приветствие при подключении

server = FTPServer((host, port), handler)    # Создаём сервер
server.max_cons = 50                  # Макс. одновременных подключений
server.max_cons_per_ip = 10           # Макс. подключений с одного IP
server.serve_forever()                # Запуск (блокирует поток)
```

**Пассивный режим (passive_ports):** в пассивном FTP сервер открывает порт для данных.
Диапазон 60000-60100 нужен для firewall-правил.

### Строки 641-684: lifespan()

Асинхронный контекстный менеджер FastAPI. Всё, что до `yield`, выполняется при старте.
Всё, что после `yield`, выполняется при остановке.

**При старте:**
1. Создаёт директорию FTP root
2. Инициализирует FTP-клиент для удалённого сервера
3. Запускает FTP-сервер в потоке
4. Запускает watchdog (слежение за файлами) — если `sync_on_upload = true`
5. Запускает периодическую синхронизацию — если `sync_interval > 0`
6. Запускает mirror-синхронизацию — если `mirror_interval_days > 0`

**При остановке:**
Останавливает watchdog observer (`observer.stop()`, `observer.join()`).

### Строки 689-694: FastAPI-приложение

```python
app = FastAPI(title=..., description=..., version="1.0.0", lifespan=lifespan)
```

Создаёт экземпляр приложения. `lifespan` привязывает функцию инициализации/остановки.

### Строки 697-792: API-эндпоинты

Подробнее — в разделе [9. API-эндпоинты](#9-api-эндпоинты).

### Строки 797-845: run_tray()

Создаёт иконку в системном трее с контекстным меню:

**Колбэки:**

| Функция | Строки | Что делает |
|---------|--------|-----------|
| `on_status` | 800-807 | Показывает уведомление Windows с текущей статистикой |
| `on_force_sync` | 809-815 | Запускает синхронизацию в отдельном потоке, уведомляет |
| `on_open_config` | 817-818 | Открывает config.json в системном редакторе (через `os.startfile`) |
| `on_open_log` | 820-822 | Открывает ftp_sync.log в системном редакторе |
| `on_exit` | 824-827 | Ставит uvicorn флаг `should_exit = True` и останавливает трей-иконку |

`icon.run()` — блокирует основной поток до вызова `icon.stop()`.
`icon.notify(msg, title)` — показывает Windows balloon notification.

### Строки 850-866: Точка входа

```python
if __name__ == "__main__":
    import uvicorn                      # Импорт uvicorn только при прямом запуске

    if not run_startup_checks():        # Самотестирование
        sys.exit(1)                     # Если ошибки — выход

    uv_config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    uv_server = uvicorn.Server(uv_config)  # Создаём сервер uvicorn

    server_thread = threading.Thread(target=uv_server.run, daemon=True)
    server_thread.start()               # Запускаем uvicorn в фоновом потоке

    logger.info("Сервер запущен, иконка в трее активна")

    run_tray(uv_server)                 # Трей в основном потоке (блокирует)
```

**Почему uvicorn в потоке, а не в main:**
pystray требует, чтобы его `icon.run()` выполнялся в основном потоке (требование Windows API для системного трея).

**Почему `uvicorn.Config(app, ...)` а не `uvicorn.run("main:app", ...)`:**
Строковый формат `"main:app"` не работает в PyInstaller EXE, т.к. uvicorn
пытается импортировать модуль `main`, а в frozen-режиме это невозможно.

---

## 5. service_watchdog.py — Служба перезапуска

Простая программа (100 строк), которая следит за основным процессом и перезапускает его при падении.

### Строки 1-7: Docstring
Описание назначения.

### Строки 9-13: Импорты
```python
import subprocess  # Запуск процессов (tasklist, Popen)
import time        # time.sleep для интервалов проверки
import sys         # sys.frozen, sys.executable
import logging     # Логирование в watchdog.log
from pathlib import Path
```

### Строки 17-24: Определение путей
```python
APP_DIR = ...                          # Папка с EXE (аналогично main.py)
TARGET_EXE = APP_DIR / "ftp_sync_server.exe"  # Какой процесс мониторить
LOG_PATH = APP_DIR / "watchdog.log"    # Свой лог-файл (отдельный от основного)
CHECK_INTERVAL = 30                    # Проверка каждые 30 секунд
```

### Строки 28-32: Перенаправление stdout/stderr
Аналогично main.py — для --noconsole режима.

### Строки 36-41: Логирование
Отдельный лог `watchdog.log` с именем логгера `"watchdog"`.

### Строка 45: CREATE_NO_WINDOW
```python
CREATE_NO_WINDOW = 0x08000000
```
Флаг Windows API `CREATE_NO_WINDOW` — при создании процесса через subprocess
не показывать консольное окно. Используется и для `tasklist`, и для запуска сервера.

### Строки 48-57: is_process_running()
```python
def is_process_running(exe_name: str) -> bool:
    output = subprocess.check_output(
        ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH", "/FO", "CSV"],
        creationflags=CREATE_NO_WINDOW,
    )
    return exe_name.lower() in output.decode("cp866", errors="ignore").lower()
```

Вызывает Windows-команду `tasklist`:
- `/FI "IMAGENAME eq ..."` — фильтр по имени процесса
- `/NH` — без заголовков таблицы
- `/FO CSV` — формат CSV (проще парсить)

Декодирует вывод в `cp866` (кодировка Windows-консоли для русской локали).
Ищет имя процесса в выводе (без учёта регистра).

### Строки 60-71: start_process()
```python
subprocess.Popen(
    [str(exe_path)],
    cwd=str(exe_path.parent),         # Рабочая директория = папка EXE
    creationflags=CREATE_NO_WINDOW,    # Без окна
)
```
Запускает процесс неблокирующе (Popen). `cwd` важен, чтобы EXE нашёл config.json.

### Строки 76-96: main()
Основной цикл:
1. Логирует параметры запуска
2. Ждёт 10 секунд (`time.sleep(10)`) — даёт серверу время запуститься при первом старте
3. Бесконечный цикл:
   - Если `ftp_sync_server.exe` не в списке процессов...
   - ...и файл EXE существует...
   - ...запускает его
   - Спит 30 секунд до следующей проверки

### Строки 99-100: Точка входа
```python
if __name__ == "__main__":
    main()
```

---

## 6. installer.iss — Скрипт установщика

Скрипт для Inno Setup 6. Компилируется в `FTP_Sync_Setup.exe` — графический установщик Windows.

### Секция [Setup] (строки 14-29)

| Параметр | Значение | Описание |
|----------|----------|----------|
| `AppId` | `{BGD-FTP-SYNC-2024}` | Уникальный ID приложения (для обновлений и удаления) |
| `AppName` | FTP Sync Server | Имя в списке программ Windows |
| `AppVersion` | 1.0.0 | Версия |
| `DefaultDirName` | `{commonpf}\BGD_FTP_SYNC` | Папка установки = `C:\Program Files\BGD_FTP_SYNC` |
| `OutputDir` | `installer_output` | Куда складывать скомпилированный установщик |
| `OutputBaseFilename` | `FTP_Sync_Setup` | Имя выходного файла |
| `Compression` | lzma2 | Алгоритм сжатия (максимальное сжатие) |
| `PrivilegesRequired` | admin | Требует права администратора (запись в Program Files и HKLM) |
| `WizardStyle` | modern | Современный вид мастера |

### Секция [Languages] (строка 32)
Русская локализация интерфейса установщика.

### Секция [Files] (строки 34-37)
Какие файлы включить в установщик:
- `dist\ftp_sync_server.exe` → устанавливается в `{app}` (выбранная папка)
- `dist\ftp_sync_watchdog.exe` → устанавливается в `{app}`
- `config.json` НЕ копируется — он генерируется из кода (секция [Code])

`ignoreversion` — всегда перезаписывать (без проверки версии файла).

### Секция [Icons] (строки 40-41)
Создаёт ярлыки в меню "Пуск":
- Ярлык на ftp_sync_server.exe
- Ярлык на деинсталлятор

### Секция [Registry] (строки 44-48)
```
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run";
ValueName: "FTP Sync Watchdog";
ValueData: """{app}\ftp_sync_watchdog.exe""";
Flags: uninsdeletevalue
```
Добавляет watchdog в автозагрузку Windows через реестр.
Ключ `HKLM\...\Run` — программы, запускаемые при входе ЛЮБОГО пользователя.
`uninsdeletevalue` — удалить запись при деинсталляции.
Тройные кавычки `"""..."""` — экранирование кавычек в Inno Setup (путь с пробелами).

### Секция [Run] (строки 50-54)
Запускает watchdog после установки:
- `postinstall` — выполнить после копирования файлов
- `nowait` — не ждать завершения (watchdog работает постоянно)
- `skipifsilent` — не запускать при тихой установке (`/SILENT`)

### Секция [UninstallRun] (строки 56-60)
При удалении программы убивает оба процесса через `taskkill /F /IM`.
`RunOnceId` — защита от повторного выполнения.

### Секция [Code] — Pascal Script (строки 66-328)

Кастомная логика установщика на языке Pascal (встроенный в Inno Setup).

#### Переменные (строки 68-90)
Объявление элементов управления для трёх кастомных страниц:
- `PageRemote`, `EditRemoteHost`, `EditRemotePort`, ... — страница удалённого FTP
- `PageLocal`, `EditLocalPort`, `EditLocalUser`, ... — страница локального FTP
- `PageSync`, `EditSyncInterval`, `EditMirrorDays`, ... — страница синхронизации

#### Хелперы (строки 95-123)

| Функция | Что создаёт |
|---------|-------------|
| `CreateLabel()` | Текстовую метку (жирный шрифт) |
| `CreateEdit()` | Поле ввода текста (опционально — поле пароля с `PasswordChar='*'`) |
| `CreateCheck()` | Чекбокс (галочка) |

Все контролы привязываются к `Page.Surface` (область содержимого страницы).
`Top` — позиция по вертикали в пикселях.

#### InitializeWizard() (строки 128-190)
Вызывается Inno Setup при создании мастера установки.
Создаёт 3 кастомные страницы, которые вставляются ПОСЛЕ страницы выбора директории (`wpSelectDir`):

**Страница 1 — "Удалённый FTP-сервер":**
6 контролов: хост, порт, логин, пароль (маскированный), корневая папка, чекбокс TLS.

**Страница 2 — "Локальный FTP-сервер":**
4 контрола: порт, логин, пароль (маскированный), папка для файлов (по умолч. `C:\BGD_FTP_DATA`).

**Страница 3 — "Параметры синхронизации":**
2 поля (интервалы) + 2 чекбокса (sync on upload, delete orphans).

#### NextButtonClick() (строки 195-224)
Валидация при нажатии "Далее":
- На странице Remote: проверяет, что хост и логин не пустые
- На странице Local: проверяет, что порт указан
- При ошибке — MessageBox и возврат `False` (не переходит дальше)

#### EscapePath() (строки 229-233)
Заменяет `\` на `/` в путях для JSON.
Пример: `C:\BGD_FTP_DATA` → `C:/BGD_FTP_DATA`.
Python на Windows понимает оба варианта.

#### JsonEscape() (строки 237-242)
Экранирует спецсимволы для JSON:
- `\` → `\\` (бэкслеш)
- `"` → `\"` (кавычка)

#### CurStepChanged() (строки 246-299)
Вызывается при смене шага установки.
При `ssPostInstall` (после копирования файлов):
1. Формирует строку `config.json` из значений полей ввода
2. Чекбоксы преобразуются в `"true"/"false"`
3. Путь FTP root проходит через `EscapePath` (прямые слеши)
4. Строковые значения проходят через `JsonEscape`
5. `SaveStringToFile` — записывает JSON в `{app}\config.json`
6. `ForceDirectories` — создаёт папку для FTP-данных

`#13#10` — символы CR+LF (перенос строки Windows).

#### PrepareToInstall() (строки 304-313)
Вызывается перед началом установки (полезно при обновлении).
Убивает запущенные процессы через `taskkill`, ждёт 1 секунду.
Возвращает пустую строку = "всё ок, продолжаем".

#### CurUninstallStepChanged() (строки 318-328)
Аналогично — убивает процессы при деинсталляции.

---

## 7. build.bat — Скрипт сборки

Batch-файл для сборки проекта. Выполняет 4 шага.

### Строки 1-3: Инициализация
```batch
@echo off                         # Не показывать команды в консоли
setlocal enabledelayedexpansion    # Включить отложенное раскрытие переменных (!VAR!)
chcp 65001 >nul                   # Кодировка UTF-8 для консоли
```

`enabledelayedexpansion` — нужен потому что переменная `ISCC` устанавливается
внутри `if`-блока, и без delayed expansion она была бы пуста при чтении.

### Строки 9-11: Шаг 1 — Зависимости
```batch
pip install -r requirements.txt
if errorlevel 1 goto :err_deps   # При ошибке — переход на метку с сообщением
```

### Строки 14-16: Шаг 2 — Сборка основного EXE
```batch
python -m PyInstaller --onefile --noconsole --name ftp_sync_server
    --hidden-import uvicorn.logging
    --hidden-import uvicorn.loops
    --hidden-import uvicorn.loops.auto
    --hidden-import uvicorn.protocols
    --hidden-import uvicorn.protocols.http
    --hidden-import uvicorn.protocols.http.auto
    --hidden-import uvicorn.protocols.http.h11_impl
    --hidden-import uvicorn.protocols.http.httptools_impl
    --hidden-import uvicorn.protocols.websockets
    --hidden-import uvicorn.protocols.websockets.auto
    --hidden-import uvicorn.lifespan
    --hidden-import uvicorn.lifespan.on
    --hidden-import uvicorn.lifespan.off
    --noconfirm
    main.py
```

| Флаг | Значение |
|------|----------|
| `--onefile` | Один EXE-файл (все зависимости внутри) |
| `--noconsole` | Без консольного окна (Windows subsystem) |
| `--name` | Имя выходного файла |
| `--hidden-import` | Модули, которые PyInstaller не может найти автоматически |
| `--noconfirm` | Не спрашивать подтверждения при перезаписи |

**Почему `python -m PyInstaller` а не просто `pyinstaller`:**
Если PyInstaller установлен, но папка `Scripts` не в PATH, прямой вызов `pyinstaller` не сработает.
`python -m` всегда работает, если Python доступен.

**Почему столько hidden-import:** PyInstaller анализирует импорты статически.
Uvicorn импортирует свои модули динамически (через `importlib`), и PyInstaller их не видит.

### Строки 19-21: Шаг 3 — Сборка watchdog EXE
Аналогично, но проще — нет динамических импортов.

### Строки 24-34: Шаг 4 — Компиляция установщика
```batch
set "ISCC="                       # Обнуляем переменную
if exist "...\ISCC.exe" set "..."  # Ищем Inno Setup в стандартных путях
if "!ISCC!"=="" goto :no_inno      # Если не найден — сообщение и выход
"!ISCC!" installer.iss             # Компилируем .iss в .exe
```

`ISCC.exe` — компилятор командной строки Inno Setup (Inno Setup Compiler CLI).

### Строки 48-77: Метки ошибок
Каждая метка (`:err_deps`, `:err_server`, ...) выводит сообщение об ошибке,
ожидает нажатия клавиши и выходит с кодом 1.

---

## 8. requirements.txt — Зависимости

```
fastapi      # Веб-фреймворк для REST API (управление сервером)
uvicorn      # ASGI-сервер для запуска FastAPI
pydantic     # Валидация данных (модели запросов/ответов API)
pyftpdlib    # FTP-сервер на Python (локальный FTP для приёма файлов)
watchdog     # Мониторинг файловой системы (реакция на новые файлы)
pystray      # Иконка в системном трее Windows
Pillow       # Библиотека изображений (генерация иконки трея)
pyinstaller  # Сборка Python-скрипта в standalone EXE
```

---

## 9. API-эндпоинты

REST API доступен по адресу `http://localhost:8000`.
Документация Swagger UI: `http://localhost:8000/docs`.

### GET `/` — Информация о сервисе
**Строки:** 697-704
**Ответ:**
```json
{
    "service": "FTP Sync Server",
    "local_ftp": "0.0.0.0:2121",
    "remote_ftp": "ftp.example.com:21",
    "endpoints": ["/status", "/sync", ...]
}
```

### GET `/status` — Статус синхронизаций
**Строки:** 707-709
**Ответ:** текущие счётчики `sync_state` и `mirror_state`.

### POST `/sync` — Принудительная синхронизация
**Строки:** 712-725
Загружает ВСЕ файлы из FTP root на удалённый сервер.
Блокирующий вызов — ответ придёт после завершения.
**Ответ:** `SyncResult` со списками synced/failed.

### GET `/files` — Список файлов
**Строки:** 728-741
Возвращает все файлы в локальном FTP root с размером и датой изменения.

### POST `/mirror?delete_orphans=true` — Mirror-синхронизация
**Строки:** 744-758
Полная зеркальная синхронизация. Параметр `delete_orphans`:
- `true` — удалить с сервера файлы, которых нет локально
- `false` — только загрузить новые

Защита от двойного запуска: если `mirror_state["is_running"]` = true, вернёт 409.

### GET `/mirror/status` — Статус mirror
**Строки:** 761-763
Счётчики + настройки mirror (интервал, delete_orphans).

### GET `/test-connection` — Проверка подключения
**Строки:** 766-771
Пробует подключиться к удалённому FTP. Возвращает `{"connected": true/false}`.

### GET `/config` — Текущая конфигурация
**Строки:** 774-779
Возвращает все настройки. **Пароли заменены на `"***"`.**

### PUT `/config/remote` — Обновить настройки удалённого FTP
**Строки:** 782-792
Принимает JSON-тело с полями `RemoteConfig`. Обновляет CONFIG и пересоздаёт FTP-клиент.
Сразу проверяет подключение.

---

## 10. Трей-меню

Правый клик по иконке в трее показывает меню:

| Пункт | Действие |
|-------|----------|
| **FTP Sync Server v1.0** | (заголовок, неактивный) |
| **Status** | Уведомление Windows со статистикой (synced/failed/last sync) |
| **Force Sync** | Запускает полную синхронизацию в фоне |
| **Open config.json** | Открывает конфигурацию в текстовом редакторе |
| **Open Log** | Открывает лог в текстовом редакторе |
| **Exit** | Останавливает uvicorn и закрывает программу |

---

## 11. Логи

### ftp_sync.log (основной сервер)

Формат строки:
```
2024-01-15 14:30:00 [INFO] ftp_sync: Загружен: data/report.xlsx
```

Типичные записи:

| Уровень | Пример | Значение |
|---------|--------|----------|
| INFO | `[CHECK] config.json: OK` | Стартовая проверка пройдена |
| INFO | `Загружен: file.txt` | Файл успешно загружен на удалённый FTP |
| INFO | `Синхронизация завершена: 5 ок, 0 ошибок` | Итог periodic sync |
| INFO | `Mirror завершён: загружено=3, удалено=1, ...` | Итог mirror sync |
| WARNING | `[CHECK] Порт 2121 уже занят` | Порт занят другим процессом |
| ERROR | `Ошибка загрузки file.txt: Connection refused` | Не удалось подключиться к FTP |

### watchdog.log (служба перезапуска)

```
2024-01-15 14:30:00 [INFO] Watchdog запущен. Слежение за: ftp_sync_server.exe
2024-01-15 14:31:00 [WARNING] ftp_sync_server.exe не запущен — перезапуск...
2024-01-15 14:31:01 [INFO] ftp_sync_server.exe успешно запущен
```
