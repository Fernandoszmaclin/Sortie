# Contribuindo com o Sortie

## Regra inegociável: só dado de simulador entra no repositório

Coordenada de voo real é geolocalização precisa e, associável ao operador, é **dado pessoal** sob a LGPD
(art. 5) e o GDPR. O ponto de decolagem tipicamente revela residência ou local de trabalho.

**Nenhum log de voo real entra no repositório, nas fixtures, ou em qualquer mídia de demonstração** —
screenshot, GIF, vídeo, exemplo de README.

As únicas coordenadas permitidas são as duas origens públicas de simulador:

| Simulador | Local | Coordenada |
|---|---|---|
| ArduPilot SITL | CMAC, Canberra | `-35.363261, 149.165230` |
| PX4 SITL | Zurich Irchel Park | `47.397742, 8.545594` |

Isso é verificado mecanicamente: `tests/test_no_real_coordinates.py` lê toda fixture commitada e afirma
que cada par lat/lon está a menos de 0,5° de uma das duas. Uma regra que ninguém aplica é uma sugestão;
esta é um build vermelho.

As fixtures são **geradas**, não gravadas — `tools/make_fixtures.py`, ver [ADR 0015](docs/adr/0015-fixtures-sinteticas.md).
Isso satisfaz a regra por construção, em vez de por uma auditoria que ninguém consegue fazer sobre um
binário opaco.

## Contrato de portabilidade

Desenvolve-se em Windows; o CI roda Linux como guarda desde o primeiro commit
([ADR 0012](docs/adr/0012-windows-first-ate-v03.md)). As doze regras C1-C12 estão em §13.1 do
[documento canônico](docs/sortie.md). As que mais mordem no dia a dia:

- `pathlib.Path` sempre; nunca concatenar caminho com separador literal.
- `encoding='utf-8'` **explícito** em toda abertura de arquivo texto — o default no Windows é a codepage
  ANSI.
- CSV com `open(..., newline='')`.
- Nenhum caminho, módulo ou arquivo diferindo só por maiúscula. É a única regra que o Windows esconde, e
  o job `ubuntu-latest` existe para pegá-la.

## Decisões

Toda decisão que não seja derivável dos princípios de §5 vira um ADR em [`docs/adr/`](docs/adr/),
escrito **no momento da decisão**, não no fim. Números de ADR nunca são reutilizados nem renumerados.

## Antes de abrir um PR

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy app
uv run lint-imports
uv run pytest
```
