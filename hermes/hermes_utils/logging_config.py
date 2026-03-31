import os
import logging
from logging.handlers import RotatingFileHandler

LOG_FILE = "/data/hermes.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_initialized = False


def setup_logging() -> None:
    """Configure root logger with file + console handlers.

    Reads LOG_LEVEL from the environment (default: INFO).
    Safe to call multiple times -- handlers are only added once.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # Keep noisy third-party loggers quiet unless we're at DEBUG
    if level > logging.DEBUG:
        for name in ("httpx", "httpcore", "telegram", "telegram.ext", "urllib3"):
            logging.getLogger(name).setLevel(logging.WARNING)
