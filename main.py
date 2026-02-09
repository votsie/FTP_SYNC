"""
FTP Sync Server
===============
Локальный FTP-сервер на базе pyftpdlib + FastAPI для управления.
Все файлы, полученные по FTP, автоматически синхронизируются на удалённый FTP-сервер.

Работает в фоне со значком в системном трее, без консольного окна.
Конфигурация: config.json рядом с EXE / main.py.
"""

import os
import sys
import json
import time
import ctypes
import ftplib
import socket
import logging
import threading
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import pystray
from PIL import Image, ImageDraw

# ─── Определение рабочей директории (для PyInstaller EXE) ────────────────────

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent

CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "ftp_sync.log"

# ─── Перенаправление stdout/stderr для --noconsole режима ────────────────────

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# ─── Загрузка конфигурации из JSON ──────────────────────────────────────────

def show_error(message: str):
    """Показать окно ошибки Windows."""
    ctypes.windll.user32.MessageBoxW(0, message, "FTP Sync Server - Ошибка", 0x10)


def load_config(path: Path) -> dict:
    """Читает config.json и возвращает плоский словарь конфигурации."""
    if not path.exists():
        show_error(f"Файл конфигурации не найден:\n{path}\n\nСоздайте config.json рядом с программой.")
        sys.exit(1)

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        show_error(f"Ошибка парсинга config.json:\n{e}")
        sys.exit(1)

    local = raw.get("local_ftp", {})
    remote = raw.get("remote_ftp", {})
    sync = raw.get("sync", {})
    mirror = raw.get("mirror", {})

    return {
        "local_ftp_host": local.get("host", "0.0.0.0"),
        "local_ftp_port": int(local.get("port", 2121)),
        "local_ftp_user": local.get("user", "localuser"),
        "local_ftp_pass": local.get("password", "localpass"),
        "local_ftp_root": local.get("root", "./ftp_root"),
        "local_ftp_perm": local.get("permissions", "elradfmw"),

        "remote_ftp_host": remote.get("host", "ftp.example.com"),
        "remote_ftp_port": int(remote.get("port", 21)),
        "remote_ftp_user": remote.get("user", "remoteuser"),
        "remote_ftp_pass": remote.get("password", "remotepass"),
        "remote_ftp_root": remote.get("root", "/"),
        "remote_ftp_tls": bool(remote.get("tls", False)),

        "sync_interval": int(sync.get("interval_seconds", 30)),
        "sync_on_upload": bool(sync.get("on_upload", True)),

        "mirror_interval_days": int(mirror.get("interval_days", 3)),
        "mirror_delete_orphans": bool(mirror.get("delete_orphans", True)),
    }


CONFIG = load_config(CONFIG_PATH)

# ─── Логирование в файл ─────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
logger = logging.getLogger("ftp_sync")

# ─── Стартовые проверки ──────────────────────────────────────────────────────

def run_startup_checks() -> bool:
    """Запускает самотестирование. Логирует результаты. При критической ошибке показывает MessageBox."""
    errors = []
    warnings = []

    # 1. config.json
    if CONFIG_PATH.exists():
        logger.info(f"[CHECK] config.json: OK ({CONFIG_PATH})")
    else:
        errors.append("config.json не найден")

    # 2. FTP root
    ftp_root_path = (APP_DIR / CONFIG["local_ftp_root"]).resolve()
    try:
        ftp_root_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"[CHECK] FTP root: OK ({ftp_root_path})")
    except Exception as e:
        errors.append(f"Не удалось создать FTP root: {e}")

    # 3. Локальный порт FTP
    local_port = CONFIG["local_ftp_port"]
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", local_port))
        sock.close()
        if result == 0:
            warnings.append(f"Порт {local_port} уже занят")
        else:
            logger.info(f"[CHECK] Порт FTP {local_port}: OK (свободен)")
    except Exception:
        logger.info(f"[CHECK] Порт FTP {local_port}: OK")

    # 4. Порт API
    api_port = 8000
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", api_port))
        sock.close()
        if result == 0:
            warnings.append(f"Порт API {api_port} уже занят")
        else:
            logger.info(f"[CHECK] Порт API {api_port}: OK (свободен)")
    except Exception:
        logger.info(f"[CHECK] Порт API {api_port}: OK")

    # 5. Подключение к удалённому FTP
    rhost = CONFIG["remote_ftp_host"]
    rport = CONFIG["remote_ftp_port"]
    try:
        if CONFIG["remote_ftp_tls"]:
            ftp = ftplib.FTP_TLS()
        else:
            ftp = ftplib.FTP()
        ftp.connect(rhost, rport, timeout=10)
        ftp.login(CONFIG["remote_ftp_user"], CONFIG["remote_ftp_pass"])
        if CONFIG["remote_ftp_tls"]:
            ftp.prot_p()
        ftp.quit()
        logger.info(f"[CHECK] Удалённый FTP {rhost}:{rport}: OK")
    except Exception as e:
        warnings.append(f"Удалённый FTP {rhost}:{rport}: {e}")

    for w in warnings:
        logger.warning(f"[CHECK] {w}")
    for e in errors:
        logger.error(f"[CHECK] {e}")

    if errors:
        show_error("Критические ошибки при запуске:\n\n" + "\n".join(errors))
        return False

    logger.info("[CHECK] Все проверки пройдены, запуск сервера...")
    return True


# ─── Иконка трея ─────────────────────────────────────────────────────────────

def create_tray_image(color: str = "#1976D2") -> Image.Image:
    """Создаёт иконку для трея — синий круг с буквой S."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, 62, 62], fill=color)
    # Буква S (sync) — рисуем вручную линиями для независимости от шрифтов
    draw.line([(38, 16), (22, 16), (22, 32), (42, 32), (42, 48), (22, 48)], fill="white", width=4)
    # Стрелочки
    draw.polygon([(18, 18), (24, 12), (24, 24)], fill="white")  # верхняя стрелка
    draw.polygon([(46, 46), (40, 40), (40, 52)], fill="white")  # нижняя стрелка
    return img


# ─── Pydantic-модели ────────────────────────────────────────────────────────

class RemoteConfig(BaseModel):
    host: str = Field(..., description="Хост удалённого FTP")
    port: int = Field(21, description="Порт")
    user: str = Field(..., description="Логин")
    password: str = Field(..., description="Пароль")
    root: str = Field("/", description="Корневая директория на удалённом сервере")
    tls: bool = Field(False, description="Использовать FTPS")

class SyncStatus(BaseModel):
    synced_files: int
    failed_files: int
    last_sync: str | None
    is_running: bool

class SyncResult(BaseModel):
    success: bool
    synced: list[str]
    failed: list[str]
    message: str

class MirrorResult(BaseModel):
    success: bool
    uploaded: list[str]
    deleted_remote: list[str]
    skipped: list[str]
    failed: list[str]
    message: str

# ─── Утилита для работы с удалённым FTP ─────────────────────────────────────

class RemoteFTPClient:
    """Обёртка для подключения и загрузки файлов на удалённый FTP."""

    def __init__(self, host: str, port: int, user: str, password: str,
                 root: str = "/", tls: bool = False):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.root = root
        self.tls = tls

    def _connect(self) -> ftplib.FTP:
        if self.tls:
            ftp = ftplib.FTP_TLS()
        else:
            ftp = ftplib.FTP()
        ftp.connect(self.host, self.port, timeout=30)
        ftp.login(self.user, self.password)
        if self.tls:
            ftp.prot_p()
        if self.root and self.root != "/":
            ftp.cwd(self.root)
        return ftp

    def _ensure_remote_dir(self, ftp: ftplib.FTP, remote_dir: str):
        if not remote_dir or remote_dir == "/":
            return
        parts = remote_dir.strip("/").split("/")
        current = ""
        for part in parts:
            current += f"/{part}"
            try:
                ftp.cwd(current)
            except ftplib.error_perm:
                try:
                    ftp.mkd(current)
                    logger.info(f"Создана удалённая директория: {current}")
                except ftplib.error_perm as e:
                    logger.warning(f"Не удалось создать {current}: {e}")
        ftp.cwd(self.root if self.root else "/")

    def upload_file(self, local_path: Path, relative_path: str) -> bool:
        try:
            ftp = self._connect()
            try:
                remote_dir = str(Path(relative_path).parent)
                if remote_dir and remote_dir != ".":
                    self._ensure_remote_dir(ftp, remote_dir)
                    ftp.cwd(self.root if self.root else "/")
                    ftp.cwd(remote_dir)

                with open(local_path, "rb") as f:
                    ftp.storbinary(f"STOR {Path(relative_path).name}", f)

                logger.info(f"Загружен: {relative_path}")
                return True
            finally:
                ftp.quit()
        except Exception as e:
            logger.error(f"Ошибка загрузки {relative_path}: {e}")
            return False

    def sync_all(self, local_root: Path) -> tuple[list[str], list[str]]:
        synced = []
        failed = []
        for local_file in local_root.rglob("*"):
            if local_file.is_file():
                relative = local_file.relative_to(local_root)
                if self.upload_file(local_file, str(relative)):
                    synced.append(str(relative))
                else:
                    failed.append(str(relative))
        return synced, failed

    def test_connection(self) -> bool:
        try:
            ftp = self._connect()
            ftp.quit()
            return True
        except Exception as e:
            logger.error(f"Ошибка подключения к {self.host}:{self.port} — {e}")
            return False

    # ─── Mirror-синхронизация ────────────────────────────────────────────────

    def _list_remote_files(self, ftp: ftplib.FTP, path: str = "") -> dict[str, int]:
        files = {}
        current_dir = ftp.pwd()
        if path:
            try:
                ftp.cwd(path)
            except ftplib.error_perm:
                return files

        items = []
        try:
            ftp.retrlines("MLSD", lambda line: items.append(line))
        except ftplib.error_perm:
            return self._list_remote_files_fallback(ftp, path)

        for item in items:
            parts = item.split("; ", 1)
            if len(parts) != 2:
                continue
            facts_str, name = parts[0], parts[1]
            if name in (".", ".."):
                continue

            facts = {}
            for fact in facts_str.split(";"):
                if "=" in fact:
                    k, v = fact.split("=", 1)
                    facts[k.strip().lower()] = v.strip()

            rel = f"{path}/{name}".strip("/") if path else name

            if facts.get("type", "").lower() == "dir":
                files.update(self._list_remote_files(ftp, rel))
            elif facts.get("type", "").lower() == "file":
                size = int(facts.get("size", 0))
                files[rel] = size

        ftp.cwd(current_dir)
        return files

    def _list_remote_files_fallback(self, ftp: ftplib.FTP, path: str = "") -> dict[str, int]:
        files = {}
        current_dir = ftp.pwd()
        if path:
            try:
                ftp.cwd(path)
            except ftplib.error_perm:
                return files

        try:
            names = ftp.nlst()
        except ftplib.error_perm:
            ftp.cwd(current_dir)
            return files

        for name in names:
            if name in (".", ".."):
                continue
            rel = f"{path}/{name}".strip("/") if path else name
            try:
                ftp.cwd(name)
                ftp.cwd("..")
                files.update(self._list_remote_files_fallback(ftp, rel))
            except ftplib.error_perm:
                try:
                    size = ftp.size(name) or 0
                except Exception:
                    size = 0
                files[rel] = size

        ftp.cwd(current_dir)
        return files

    def _delete_remote_file(self, ftp: ftplib.FTP, remote_path: str) -> bool:
        try:
            ftp.delete(remote_path)
            logger.info(f"Удалён с удалённого: {remote_path}")
            return True
        except ftplib.error_perm as e:
            logger.error(f"Не удалось удалить {remote_path}: {e}")
            return False

    def _remove_empty_dirs(self, ftp: ftplib.FTP, dir_path: str):
        try:
            items = []
            ftp.retrlines(f"MLSD {dir_path}", lambda line: items.append(line))
            real_items = [
                i for i in items
                if not i.strip().endswith("; .") and not i.strip().endswith("; ..")
            ]
            if len(real_items) == 0:
                ftp.rmd(dir_path)
                logger.info(f"Удалена пустая директория: {dir_path}")
                parent = str(Path(dir_path).parent)
                if parent and parent != "." and parent != "/":
                    self._remove_empty_dirs(ftp, parent)
        except Exception:
            pass

    def mirror_sync(
        self, local_root: Path, delete_orphans: bool = True
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        uploaded = []
        deleted = []
        skipped = []
        failed = []

        try:
            ftp = self._connect()
        except Exception as e:
            logger.error(f"Mirror: не удалось подключиться — {e}")
            return uploaded, deleted, skipped, [f"connection: {e}"]

        try:
            logger.info("Mirror: сканирование удалённого сервера...")
            remote_files = self._list_remote_files(ftp, "")
            logger.info(f"Mirror: найдено {len(remote_files)} файлов на удалённом сервере")

            local_files: dict[str, int] = {}
            for local_file in local_root.rglob("*"):
                if local_file.is_file():
                    rel = str(local_file.relative_to(local_root))
                    local_files[rel] = local_file.stat().st_size

            logger.info(f"Mirror: найдено {len(local_files)} локальных файлов")
            ftp.quit()

            for rel_path, local_size in local_files.items():
                remote_size = remote_files.get(rel_path)
                if remote_size is not None and remote_size == local_size:
                    skipped.append(rel_path)
                    continue
                local_path = local_root / rel_path
                if self.upload_file(local_path, rel_path):
                    uploaded.append(rel_path)
                else:
                    failed.append(rel_path)

            if delete_orphans:
                orphans = set(remote_files.keys()) - set(local_files.keys())
                if orphans:
                    logger.info(f"Mirror: удаление {len(orphans)} orphan-файлов с сервера")
                    ftp2 = self._connect()
                    try:
                        for orphan in sorted(orphans, reverse=True):
                            if self._delete_remote_file(ftp2, orphan):
                                deleted.append(orphan)
                            else:
                                failed.append(f"delete:{orphan}")
                        orphan_dirs = set()
                        for orphan in orphans:
                            parent = str(Path(orphan).parent)
                            if parent and parent != ".":
                                orphan_dirs.add(parent)
                        for d in sorted(orphan_dirs, key=len, reverse=True):
                            self._remove_empty_dirs(ftp2, d)
                    finally:
                        ftp2.quit()

        except Exception as e:
            logger.error(f"Mirror: ошибка — {e}")
            failed.append(f"mirror_error: {e}")

        return uploaded, deleted, skipped, failed


# ─── Глобальное состояние ────────────────────────────────────────────────────

sync_state = {
    "synced_files": 0,
    "failed_files": 0,
    "last_sync": None,
    "is_running": False,
}

mirror_state = {
    "last_mirror": None,
    "next_mirror": None,
    "uploaded": 0,
    "deleted": 0,
    "skipped": 0,
    "failed": 0,
    "is_running": False,
}

remote_client: RemoteFTPClient | None = None
ftp_root: Path | None = None


def init_remote_client():
    global remote_client
    remote_client = RemoteFTPClient(
        host=CONFIG["remote_ftp_host"],
        port=CONFIG["remote_ftp_port"],
        user=CONFIG["remote_ftp_user"],
        password=CONFIG["remote_ftp_pass"],
        root=CONFIG["remote_ftp_root"],
        tls=CONFIG["remote_ftp_tls"],
    )


# ─── Watchdog: мгновенная синхронизация при получении файла ──────────────────

class FTPUploadHandler(FileSystemEventHandler):
    def __init__(self, local_root: Path, client: RemoteFTPClient):
        self.local_root = local_root
        self.client = client

    def on_created(self, event):
        if event.is_directory:
            return
        self._sync_file(event.src_path)

    def on_modified(self, event):
        if event.is_directory:
            return
        self._sync_file(event.src_path)

    def _sync_file(self, filepath: str):
        path = Path(filepath)
        time.sleep(0.5)
        if path.is_file():
            relative = path.relative_to(self.local_root)
            ok = self.client.upload_file(path, str(relative))
            if ok:
                sync_state["synced_files"] += 1
            else:
                sync_state["failed_files"] += 1


# ─── Периодическая полная синхронизация ──────────────────────────────────────

def periodic_sync_loop(local_root: Path, client: RemoteFTPClient, interval: int):
    while True:
        time.sleep(interval)
        logger.info("Запуск периодической синхронизации...")
        sync_state["is_running"] = True
        synced, failed = client.sync_all(local_root)
        sync_state["synced_files"] += len(synced)
        sync_state["failed_files"] += len(failed)
        sync_state["last_sync"] = time.strftime("%Y-%m-%d %H:%M:%S")
        sync_state["is_running"] = False
        logger.info(f"Синхронизация завершена: {len(synced)} ок, {len(failed)} ошибок")


# ─── Mirror-синхронизация раз в N дней ──────────────────────────────────────

def run_mirror_sync(local_root: Path, client: RemoteFTPClient):
    mirror_state["is_running"] = True
    logger.info("=== Запуск MIRROR-синхронизации ===")

    uploaded, deleted, skipped, failed = client.mirror_sync(
        local_root,
        delete_orphans=CONFIG["mirror_delete_orphans"],
    )

    mirror_state["uploaded"] += len(uploaded)
    mirror_state["deleted"] += len(deleted)
    mirror_state["skipped"] += len(skipped)
    mirror_state["failed"] += len(failed)
    mirror_state["last_mirror"] = time.strftime("%Y-%m-%d %H:%M:%S")
    mirror_state["is_running"] = False

    logger.info(
        f"=== Mirror завершён: загружено={len(uploaded)}, "
        f"удалено={len(deleted)}, пропущено={len(skipped)}, "
        f"ошибок={len(failed)} ==="
    )
    return uploaded, deleted, skipped, failed


def mirror_sync_loop(local_root: Path, client: RemoteFTPClient, interval_days: int):
    interval_seconds = interval_days * 24 * 3600
    while True:
        next_time = time.time() + interval_seconds
        mirror_state["next_mirror"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(next_time)
        )
        logger.info(
            f"Mirror: следующая синхронизация через {interval_days} дн. "
            f"({mirror_state['next_mirror']})"
        )
        time.sleep(interval_seconds)
        run_mirror_sync(local_root, client)


# ─── Локальный FTP-сервер ────────────────────────────────────────────────────

def start_local_ftp_server():
    authorizer = DummyAuthorizer()
    authorizer.add_user(
        CONFIG["local_ftp_user"],
        CONFIG["local_ftp_pass"],
        str(ftp_root),
        perm=CONFIG["local_ftp_perm"],
    )

    handler = FTPHandler
    handler.authorizer = authorizer
    handler.passive_ports = range(60000, 60100)
    handler.banner = "FTP Sync Server ready."

    server = FTPServer(
        (CONFIG["local_ftp_host"], CONFIG["local_ftp_port"]),
        handler,
    )
    server.max_cons = 50
    server.max_cons_per_ip = 10

    logger.info(f"Локальный FTP-сервер: {CONFIG['local_ftp_host']}:{CONFIG['local_ftp_port']}")
    server.serve_forever()


# ─── FastAPI Lifespan ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global ftp_root

    ftp_root = (APP_DIR / CONFIG["local_ftp_root"]).resolve()
    ftp_root.mkdir(parents=True, exist_ok=True)
    logger.info(f"FTP root: {ftp_root}")

    init_remote_client()

    ftp_thread = threading.Thread(target=start_local_ftp_server, daemon=True)
    ftp_thread.start()

    observer = None
    if CONFIG["sync_on_upload"]:
        event_handler = FTPUploadHandler(ftp_root, remote_client)
        observer = Observer()
        observer.schedule(event_handler, str(ftp_root), recursive=True)
        observer.start()
        logger.info("Watchdog: мгновенная синхронизация включена")

    if CONFIG["sync_interval"] > 0:
        sync_thread = threading.Thread(
            target=periodic_sync_loop,
            args=(ftp_root, remote_client, CONFIG["sync_interval"]),
            daemon=True,
        )
        sync_thread.start()
        logger.info(f"Периодическая синхронизация каждые {CONFIG['sync_interval']}с")

    if CONFIG["mirror_interval_days"] > 0:
        mirror_thread = threading.Thread(
            target=mirror_sync_loop,
            args=(ftp_root, remote_client, CONFIG["mirror_interval_days"]),
            daemon=True,
        )
        mirror_thread.start()
        logger.info(f"Mirror-синхронизация каждые {CONFIG['mirror_interval_days']} дн.")

    yield

    if observer:
        observer.stop()
        observer.join()


# ─── FastAPI приложение ──────────────────────────────────────────────────────

app = FastAPI(
    title="FTP Sync Server",
    description="Локальный FTP-сервер с автоматической синхронизацией на удалённый FTP",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/", summary="Главная")
async def root():
    return {
        "service": "FTP Sync Server",
        "local_ftp": f"{CONFIG['local_ftp_host']}:{CONFIG['local_ftp_port']}",
        "remote_ftp": f"{CONFIG['remote_ftp_host']}:{CONFIG['remote_ftp_port']}",
        "endpoints": ["/status", "/sync", "/mirror", "/mirror/status", "/config", "/files", "/test-connection"],
    }


@app.get("/status", summary="Статус всех синхронизаций")
async def get_status():
    return {"sync": sync_state, "mirror": mirror_state}


@app.post("/sync", response_model=SyncResult, summary="Принудительная синхронизация")
async def force_sync():
    if not remote_client or not ftp_root:
        raise HTTPException(status_code=500, detail="Сервер не инициализирован")
    sync_state["is_running"] = True
    synced, failed = remote_client.sync_all(ftp_root)
    sync_state["synced_files"] += len(synced)
    sync_state["failed_files"] += len(failed)
    sync_state["last_sync"] = time.strftime("%Y-%m-%d %H:%M:%S")
    sync_state["is_running"] = False
    return SyncResult(
        success=len(failed) == 0, synced=synced, failed=failed,
        message=f"Синхронизировано: {len(synced)}, ошибок: {len(failed)}",
    )


@app.get("/files", summary="Список файлов в локальном FTP")
async def list_files():
    if not ftp_root:
        raise HTTPException(status_code=500, detail="Сервер не инициализирован")
    files = []
    for f in ftp_root.rglob("*"):
        if f.is_file():
            stat = f.stat()
            files.append({
                "path": str(f.relative_to(ftp_root)),
                "size": stat.st_size,
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            })
    return {"root": str(ftp_root), "count": len(files), "files": files}


@app.post("/mirror", response_model=MirrorResult, summary="Принудительная mirror-синхронизация")
async def force_mirror(delete_orphans: bool = True):
    if not remote_client or not ftp_root:
        raise HTTPException(status_code=500, detail="Сервер не инициализирован")
    if mirror_state["is_running"]:
        raise HTTPException(status_code=409, detail="Mirror-синхронизация уже запущена")
    old_val = CONFIG["mirror_delete_orphans"]
    CONFIG["mirror_delete_orphans"] = delete_orphans
    uploaded, deleted, skipped, failed = run_mirror_sync(ftp_root, remote_client)
    CONFIG["mirror_delete_orphans"] = old_val
    return MirrorResult(
        success=len(failed) == 0, uploaded=uploaded, deleted_remote=deleted,
        skipped=skipped, failed=failed,
        message=f"Загружено: {len(uploaded)}, удалено: {len(deleted)}, пропущено: {len(skipped)}, ошибок: {len(failed)}",
    )


@app.get("/mirror/status", summary="Статус mirror-синхронизации")
async def get_mirror_status():
    return {**mirror_state, "interval_days": CONFIG["mirror_interval_days"], "delete_orphans": CONFIG["mirror_delete_orphans"]}


@app.get("/test-connection", summary="Проверка подключения к удалённому FTP")
async def test_connection():
    if not remote_client:
        raise HTTPException(status_code=500, detail="Клиент не инициализирован")
    ok = remote_client.test_connection()
    return {"host": CONFIG["remote_ftp_host"], "port": CONFIG["remote_ftp_port"], "connected": ok}


@app.get("/config", summary="Текущая конфигурация")
async def get_config():
    safe = {k: v for k, v in CONFIG.items()}
    safe["local_ftp_pass"] = "***"
    safe["remote_ftp_pass"] = "***"
    return safe


@app.put("/config/remote", summary="Обновить настройки удалённого FTP")
async def update_remote_config(cfg: RemoteConfig):
    CONFIG["remote_ftp_host"] = cfg.host
    CONFIG["remote_ftp_port"] = cfg.port
    CONFIG["remote_ftp_user"] = cfg.user
    CONFIG["remote_ftp_pass"] = cfg.password
    CONFIG["remote_ftp_root"] = cfg.root
    CONFIG["remote_ftp_tls"] = cfg.tls
    init_remote_client()
    ok = remote_client.test_connection()
    return {"updated": True, "connection_test": ok, "host": cfg.host, "port": cfg.port}


# ─── Системный трей ─────────────────────────────────────────────────────────

def run_tray(uvicorn_server):
    """Запускает иконку в системном трее (блокирует main thread)."""

    def on_status(icon, item):
        msg = (
            f"Synced: {sync_state['synced_files']}\n"
            f"Failed: {sync_state['failed_files']}\n"
            f"Last sync: {sync_state['last_sync'] or 'never'}\n"
            f"Mirror: {mirror_state['last_mirror'] or 'never'}"
        )
        icon.notify(msg, "FTP Sync - Status")

    def on_force_sync(icon, item):
        if remote_client and ftp_root:
            threading.Thread(
                target=lambda: remote_client.sync_all(ftp_root),
                daemon=True,
            ).start()
            icon.notify("Sync started...", "FTP Sync")

    def on_open_config(icon, item):
        os.startfile(str(CONFIG_PATH))

    def on_open_log(icon, item):
        if LOG_PATH.exists():
            os.startfile(str(LOG_PATH))

    def on_exit(icon, item):
        logger.info("Завершение работы по запросу пользователя...")
        uvicorn_server.should_exit = True
        icon.stop()

    icon = pystray.Icon(
        "ftp_sync",
        create_tray_image(),
        "FTP Sync Server",
        menu=pystray.Menu(
            pystray.MenuItem("FTP Sync Server v1.0", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Status", on_status),
            pystray.MenuItem("Force Sync", on_force_sync),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open config.json", on_open_config),
            pystray.MenuItem("Open Log", on_open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", on_exit),
        ),
    )
    icon.run()


# ─── Запуск ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    if not run_startup_checks():
        sys.exit(1)

    # Uvicorn в фоновом потоке
    uv_config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    uv_server = uvicorn.Server(uv_config)

    server_thread = threading.Thread(target=uv_server.run, daemon=True)
    server_thread.start()

    logger.info("Сервер запущен, иконка в трее активна")

    # Трей в основном потоке (блокирует до выхода)
    run_tray(uv_server)
