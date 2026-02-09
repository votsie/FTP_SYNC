"""
FTP Sync Watchdog
=================
Следит за процессом ftp_sync_server.exe и перезапускает его при падении.
Запускается при входе в систему через автозагрузку (реестр).
Работает без окна (--noconsole при сборке).
"""

import subprocess
import time
import sys
import logging
from pathlib import Path

# ─── Пути ────────────────────────────────────────────────────────────────────

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent

TARGET_EXE = APP_DIR / "ftp_sync_server.exe"
LOG_PATH = APP_DIR / "watchdog.log"
CHECK_INTERVAL = 30  # секунд

# ─── Перенаправление для --noconsole ─────────────────────────────────────────

import os
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# ─── Логирование ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")],
)
logger = logging.getLogger("watchdog")

# ─── Проверка процесса ──────────────────────────────────────────────────────

CREATE_NO_WINDOW = 0x08000000


def is_process_running(exe_name: str) -> bool:
    """Проверяет, запущен ли процесс с указанным именем."""
    try:
        output = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH", "/FO", "CSV"],
            creationflags=CREATE_NO_WINDOW,
        )
        return exe_name.lower() in output.decode("cp866", errors="ignore").lower()
    except Exception:
        return False


def start_process(exe_path: Path) -> bool:
    """Запускает процесс."""
    try:
        subprocess.Popen(
            [str(exe_path)],
            cwd=str(exe_path.parent),
            creationflags=CREATE_NO_WINDOW,
        )
        return True
    except Exception as e:
        logger.error(f"Не удалось запустить {exe_path.name}: {e}")
        return False


# ─── Основной цикл ──────────────────────────────────────────────────────────

def main():
    exe_name = TARGET_EXE.name
    logger.info(f"Watchdog запущен. Слежение за: {exe_name}")
    logger.info(f"Путь: {TARGET_EXE}")
    logger.info(f"Интервал проверки: {CHECK_INTERVAL}с")

    # Первый запуск — даём серверу 10с на старт, не перезапускаем сразу
    time.sleep(10)

    while True:
        if not is_process_running(exe_name):
            if TARGET_EXE.exists():
                logger.warning(f"{exe_name} не запущен — перезапуск...")
                if start_process(TARGET_EXE):
                    logger.info(f"{exe_name} успешно запущен")
                else:
                    logger.error(f"Не удалось запустить {exe_name}")
            else:
                logger.error(f"Файл не найден: {TARGET_EXE}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
