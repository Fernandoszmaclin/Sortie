# Avisos de terceiros

O Sortie é distribuído sob [MIT](LICENSE). Ele depende das bibliotecas abaixo, cada uma sob sua própria
licença. As versões refletem o que é resolvido por `uv.lock` e são atualizadas a cada bump de
dependência.

## Runtime — distribuído no bundle

| Dependência | Licença | Nota |
|---|---|---|
| PySide6-Essentials | **LGPLv3** | obrigação de relink → empacotamento em `--onedir` |
| pymavlink | **LGPLv3-or-later** | só os módulos de dialeto *gerados* por `mavgen.py` são MIT; `mavutil`, `mavwp` e `DFReader` são LGPL ([ADR 0001](docs/adr/0001-cliente-mavlink.md)) |
| lxml | BSD-3-Clause | transitiva do pymavlink; **wheel nativa** (C) |
| fastcrc | MIT / Apache-2.0 | transitiva do pymavlink; **wheel nativa** (Rust) |
| PyQtGraph | MIT | |
| NumPy | BSD-3-Clause | |

## Ferramentas — não distribuídas

pytest, pytest-qt, pytest-cov, psutil, ruff, mypy, import-linter e PyInstaller são usados apenas em
desenvolvimento e não entram no bundle. O PyInstaller é GPLv2+ **com exceção de bootloader**, que não
contamina a aplicação congelada.

## Conformidade com a LGPL

A LGPLv3 §4 exige que o usuário final consiga substituir a biblioteca por uma versão modificada e
executar o resultado. Por isso a distribuição é `--onedir` e nunca `--onefile`: num auto-extraível único
não há mecanismo de substituição.

Para reconstruir com uma versão modificada do Qt, substitua os binários do PySide6 dentro do diretório
`_internal/` do bundle, ou reconstrua a partir do código com
`uv sync --all-groups && uv run pyinstaller ...`.

Os textos completos da LGPLv3 e da GPLv3 acompanham o bundle distribuído.

## Não usar

**Qt Charts** e **Qt Data Visualization** são GPLv3 ou comercial. Trocar o PyQtGraph por eles
contaminaria o projeto inteiro.
