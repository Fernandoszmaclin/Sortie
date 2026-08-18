"""Verifica o bootstrap da aplicação e a guarda de log verboso do Qt (ADR 0018)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import main

REPO = Path(__file__).resolve().parents[1]

# Gatilhos de log verboso do Qt, com um valor que de fato liga cada um.
GATILHOS = [
    {"QT_DEBUG_PLUGINS": "1"},
    {"QT_LOGGING_RULES": "qt.*=true"},
]


def _selftest(**env_extra: str) -> subprocess.CompletedProcess[bytes]:
    """Roda `main.py --selftest` num processo separado e devolve o resultado.

    Precisa ser subprocesso: o modo de falha sob teste é morte do processo
    (0xC0000005 no Windows, SIGSEGV no Linux), e não existe `except` para isso.
    """
    # Copiar o ambiente em vez de montá-lo: sem PATH e SYSTEMROOT o Windows
    # falha ao carregar DLL, e o teste reprovaria pelo motivo errado.
    env = os.environ.copy()
    env.update(env_extra)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return subprocess.run(
        [sys.executable, "main.py", "--selftest"],
        cwd=REPO,
        env=env,
        capture_output=True,
        timeout=120,
    )


def test_selftest_sai_zero() -> None:
    """A aplicação sobe e fecha limpa sem nenhum gatilho ligado."""
    proc = _selftest()
    assert proc.returncode == 0, f"saiu {proc.returncode}: {proc.stderr[-2000:]!r}"


@pytest.mark.parametrize("env_extra", GATILHOS, ids=lambda e: next(iter(e)))
def test_log_verboso_do_qt_nao_derruba_o_app(env_extra: dict[str, str]) -> None:
    """Log verboso do Qt não pode matar o processo (ADR 0018)."""
    proc = _selftest(**env_extra)
    assert proc.returncode == 0, f"saiu {proc.returncode}: {proc.stderr[-2000:]!r}"


@pytest.mark.parametrize(
    ("env", "esperado"),
    [
        ({}, None),
        ({"QT_DEBUG_PLUGINS": ""}, None),
        ({"QT_DEBUG_PLUGINS": "0"}, None),
        ({"QT_DEBUG_PLUGINS": "1"}, "QT_DEBUG_PLUGINS"),
        ({"QT_LOGGING_RULES": ""}, None),
        ({"QT_LOGGING_RULES": "qt.*=true"}, "QT_LOGGING_RULES"),
        ({"QT_LOGGING_CONF": ""}, None),
        ({"QT_LOGGING_CONF": "regras.ini"}, "QT_LOGGING_CONF"),
    ],
)
def test_deteccao_de_log_verboso(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], esperado: str | None
) -> None:
    """`0` e vazio significam desligado; a checagem não é por presença."""
    for nome in main._QT_VERBOSE_ENV:
        monkeypatch.delenv(nome, raising=False)
    for chave, valor in env.items():
        monkeypatch.setenv(chave, valor)
    assert main._qt_verbose_env() == esperado
