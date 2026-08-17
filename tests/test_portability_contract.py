"""Verifica o contrato de portabilidade nos dois sistemas operacionais."""

from __future__ import annotations

import re
import sqlite3
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _tracked_files() -> list[str]:
    """Retorna os caminhos rastreados pelo git."""
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line]


def test_perf_counter_resolution() -> None:
    """perf_counter é monotônico e tem resolução sub-microssegundo."""
    info = time.get_clock_info("perf_counter")
    assert info.monotonic, "perf_counter precisa ser monotonico"
    assert info.resolution <= 1e-6, f"resolucao insuficiente: {info.resolution}"


def test_sqlite_supports_strict_tables() -> None:
    """O SQLite embutido aceita tabelas STRICT."""
    assert sqlite3.sqlite_version_info >= (3, 37, 0), sqlite3.sqlite_version


def test_no_paths_differing_only_by_case() -> None:
    """Nenhum par de caminhos rastreados difere apenas por maiúscula."""
    seen: dict[str, str] = {}
    for path in _tracked_files():
        low = path.lower()
        assert low not in seen or seen[low] == path, f"colisao de caso: {path} vs {seen[low]}"
        seen[low] = path


def test_source_paths_are_lowercase() -> None:
    """Caminhos sob app/, tools/ e tests/ usam apenas minúscula, dígito, _ . / e -."""
    allowed = re.compile(r"^[a-z0-9_./-]+$")
    for path in _tracked_files():
        if path.startswith(("app/", "tools/", "tests/")):
            assert allowed.match(path), f"nome fora do padrao: {path}"


def test_no_exec_bit_or_symlinks() -> None:
    """Nenhum arquivo rastreado tem bit de execução ou é symlink."""
    out = subprocess.run(
        ["git", "ls-files", "--stage"], cwd=REPO, capture_output=True, text=True, check=True
    )
    for line in out.stdout.splitlines():
        mode = line.split()[0]
        assert mode not in ("100755", "120000"), f"modo {mode} proibido: {line}"
