"""Ponto de entrada do Sortie."""

from __future__ import annotations

import argparse
import faulthandler
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

_CRASH_FH = None


def _log_dir() -> Path:
    """Retorna o diretório de log do usuário, criando-o se não existir."""
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = root / "Sortie" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _install_logging() -> Path:
    """Instala faulthandler, log em arquivo e excepthooks. Retorna o caminho do log."""
    global _CRASH_FH

    logdir = _log_dir()
    logfile = logdir / "sortie.log"

    # Mantém o arquivo aberto durante todo o processo; o faulthandler guarda só o fd.
    _CRASH_FH = open(logdir / "sortie.crash", "w", buffering=1, encoding="utf-8")
    faulthandler.enable(file=_CRASH_FH, all_threads=True)

    handler = RotatingFileHandler(
        logfile, maxBytes=5 << 20, backupCount=3, encoding="utf-8", delay=True
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    # Desliga a escrita de emergência em stderr, ausente em builds --windowed.
    logging.raiseExceptions = False
    logging.lastResort = None

    def _hook(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        logging.getLogger("sortie").critical("excecao nao tratada", exc_info=(exc_type, exc, tb))

    sys.excepthook = _hook
    threading.excepthook = lambda a: logging.getLogger("sortie").critical(
        "excecao em thread", exc_info=(a.exc_type, a.exc_value, a.exc_traceback)
    )
    return logfile


def main(argv: list[str] | None = None) -> int:
    """Abre a janela principal. Com --selftest, fecha após 300 ms."""
    logfile = _install_logging()
    log = logging.getLogger("sortie")

    parser = argparse.ArgumentParser(prog="sortie")
    parser.add_argument("--selftest", action="store_true", help="abre e fecha; usado pelo CI")
    args = parser.parse_args(argv)

    # Importado após o logging para que uma falha neste import seja registrada.
    from PySide6 import QtCore, QtWidgets

    def _qt_message(mode: QtCore.QtMsgType, ctx: QtCore.QMessageLogContext, msg: str) -> None:
        log.warning("qt: %s", msg)

    # Instalado antes do QApplication, que emite avisos de plugin durante a construção.
    QtCore.qInstallMessageHandler(_qt_message)

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setOrganizationName("Sortie")
    app.setApplicationName("Sortie")

    window = QtWidgets.QMainWindow()
    window.setWindowTitle("Sortie")
    window.resize(960, 600)
    window.show()

    log.info("startup ok qt=%s log=%s", QtCore.qVersion(), logfile)

    if args.selftest:
        QtCore.QTimer.singleShot(300, app.quit)

    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
