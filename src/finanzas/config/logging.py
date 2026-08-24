import logging
import sys
from pathlib import Path

# ═══════════════════════════════════════════
# Configuración de logging profesional
# ═══════════════════════════════════════════


def setup_logging(level: str = "INFO", log_file: str | None = None) -> logging.Logger:
    """
    Configura logging para la aplicación.

    Parameters
    ----------
    level : str
        Nivel de logging (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    log_file : str, optional
        Ruta al archivo de log. Si None, solo imprime a consola.

    Returns
    -------
    logging.Logger
        Logger configurado para la aplicación.
    """
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    logger = logging.getLogger("finanzas")
    logger.setLevel(getattr(logging, level.upper()))

    # Evitar duplicar handlers cuando Streamlit re-ejecuta el script
    if logger.handlers:
        logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))
    logger.addHandler(console_handler)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))
        logger.addHandler(file_handler)

    return logger


# Instanciación:
# setup_logging(level=settings.log_level, log_file=str(settings.log_file_path))
# Logger por módulo: logger = logging.getLogger(__name__)
