import logging
import sys

from app.core.config import get_settings

settings = get_settings()


def setup_logging() -> None:
    level = logging.DEBUG if settings.DEBUG else logging.INFO
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    logging.basicConfig(stream=sys.stdout, level=level, format=fmt)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


logger = logging.getLogger("crypto_alerts")
