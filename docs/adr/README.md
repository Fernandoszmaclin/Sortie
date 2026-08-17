# Registros de decisão de arquitetura

Um ADR por decisão que **não é derivável dos princípios de §5** do documento canônico
([`../sortie.md`](../sortie.md)), escrito **no momento da decisão**, não no fim (P10).

O documento canônico descreve o alvo. Estes registros dizem por que o alvo é esse.

## Numeração

Números são referências permanentes: **nunca reutilizar, nunca renumerar.** Um ADR que contradiz outro
tem de nomeá-lo, senão o par lê como desacordo não resolvido.

| # | Decisão | Status |
|---|---|---|
| [0001](0001-cliente-mavlink.md) | pymavlink como cliente MAVLink, não MAVSDK | aceito |
| 0002 | `QThread` + `Signal`/`QueuedConnection` e não asyncio no loop do Qt | Bloco A |
| 0003 | Record raw, derive views: por que o snapshot não é a unidade de armazenamento | Bloco C |
| 0005 | Replay como adaptador e não como módulo | Bloco D |
| 0006 | ROS 2 fora do processo: rclpy, PyInstaller e a amarração de versão do Python | v0.6 |
| 0007 | SQLite em WAL com writer dedicado; backpressure e shutdown ordenado | Bloco C |
| 0008 | `--onedir` e conformidade LGPL | Bloco E |
| 0009 | Posicionamento contra as GCS existentes | pendente |
| 0010 | Escopo read-mostly: quais comandos o software envia, e por quê | v0.4 |
| 0011 | Linux-first com contrato de portabilidade verificado no CI | **substituído por 0012** |
| [0012](0012-windows-first-ate-v03.md) | Windows-first até a v0.3, contrato invertido no CI | aceito |
| [0013](0013-relogio-e-eixo-do-replay.md) | `perf_counter_ns`, e `t_recv_ns` como eixo do cursor | aceito |
| [0014](0014-chave-primaria-das-amostras.md) | Chave primária das amostras, e uma tabela por msgid | aceito |
| [0015](0015-fixtures-sinteticas.md) | Fixtures sintéticas como estratégia primária | aceito |
| [0016](0016-nome-sortie.md) | O produto se chama Sortie | aceito |
| 0017 | Mapa em pyqtgraph sobre MBTiles raster, não QtLocation | v0.4 |

Os números 0002-0010 vêm da lista planejada em §22 do documento canônico e são escritos quando a
decisão correspondente for tomada. Um número reservado e não escrito é dívida visível; um número
reutilizado é confusão permanente.

## Formato

Contexto → Decisão → Consequências. O Contexto registra a força que obrigou a escolher, incluindo o que
foi descoberto e derrubou uma premissa anterior. As Consequências registram o que passa a ser verdade,
inclusive o que fica pior.
