# Sortie — Plano de execução

**Versão 2.1 — derivado de `sortie.md` v1.2**
Cobre da Etapa 0 até a tag `v0.1.0`. Da v0.2 em diante, o roadmap de §16 do documento canônico vale
sem alteração.

Este documento não substitui o canônico. Ele traduz §16 em tarefas com ordem de dependência, registra
as decisões de ambiente tomadas antes do primeiro commit, e **lista as correções que a especificação
precisa receber** — encontradas por verificação independente contra documentação primária.

> **Precedência:** onde a seção 9 (crítica adversarial) conflitar com as seções 4-5, vale a seção 9.
> Onde este documento conflitar com o canônico, vale este — mas a correção deve ser propagada de volta
> para `sortie.md` como errata, não mantida como divergência silenciosa.

---

## Contexto

O repositório contém hoje **dois arquivos**, ambos documentação: a especificação
canônica de ~1500 linhas (`docs/sortie.md`, v1.2) do Sortie — ferramenta de bancada para análise
comparativa de execuções de missão em veículos autônomos. Ingere telemetria MAVLink (ao vivo ou de
logs), normaliza num modelo com eixo temporal explícito, e sobrepõe N execuções do mesmo plano de voo
contra uma baseline, emitindo veredito reprodutível. Não é uma estação de controle de solo.

A especificação é madura — 11 princípios, 6 decisões arquiteturais, 35 armadilhas catalogadas, 11 ADRs
planejados. O que falta é código: **zero commits**.

**Problema a resolver:** traduzir §16 em trabalho executável hoje, resolvendo antes (a) duas
divergências entre o que a spec assume e o que a máquina é, e (b) **três defeitos técnicos na própria
especificação**, encontrados por verificação independente, que impedem o código de compilar ou de
medir o que promete.

**Resultado pretendido:** commit 1 hoje com CI verde nos dois SOs; v0.1.0 — declarada em §16 como
"portfólio completo, que existe mesmo se o projeto parar aqui" — tagueada em ~30 dias.

---

## 1. Correções obrigatórias à especificação

Achados de verificação contra documentação primária (CPython, SQLite, Qt, PyPI, MAVLink). Cada um
invalida algo escrito na spec. **Corrigir antes de escrever o código que depende deles**, não depois.

### C-1 — `time.monotonic_ns()` tem resolução de 15,6 ms no Windows/Python 3.12 `[BLOQUEADOR]`

§7.1 define `t_recv_mono_ns` como `time.monotonic_ns()`. No Python 3.12 em Windows isso é
`GetTickCount64` — **15,625 ms de granularidade**. O CPython só trocou para `QueryPerformanceCounter`
(~1 µs) no 3.13 (gh-88494).

Esse campo é simultaneamente a chave de ordenação, o detector de gap, o instrumento da NFR
"latência recebimento → pixel < 100 ms (p95)" de §12, e o relógio do cursor de replay. A 15,6 ms, o
orçamento de 100 ms é medido com ±16 % de erro de quantização, e o intervalo de coalescing de
10-20 Hz (50-100 ms) tem só 3 a 6 ticks de largura — estatística de jitter vira ruído.

**Correção:** usar `time.perf_counter_ns()` em todo produtor de `t_recv_*`. É QPC no Windows e
`CLOCK_MONOTONIC` no Linux, monotônico nos dois, ~100 ns, API idêntica em 3.11–3.14. Isso torna a
questão ortogonal à versão do Python e **preserva D2**. Renomear o campo para `t_recv_ns` — o nome
atual implica `time.monotonic`.

Consequências: (a) o epoch é local ao processo, jamais comparável entre sessões ou através da
fronteira do bridge (§9.8); (b) QPC conta durante suspensão, então o cursor de replay precisa de
clamp — se `dt > 1 s`, reancorar em vez de avançar, e emitir evento visível (P9); isso também cobre
breakpoint de debugger e pausa de GC.

**Teste que transforma isso em contrato:** `assert time.get_clock_info('perf_counter').resolution <= 1e-6`,
rodando nos dois runners de CI. É o teste que teria pego o defeito.

### C-2 — O schema de §8.4 não compila `[BLOQUEADOR]`

`PRIMARY KEY (session_id, system_id, t_boot_ms) WITHOUT ROWID` é impossível como escrito:

| Defeito | Evidência |
|---|---|
| SQLite exige NOT NULL em toda coluna de PK `WITHOUT ROWID`; §7.1 declara `t_boot_ms` **nullable** | contradição direta |
| 6 das 8 famílias `sample_*` **não têm timestamp de boot nenhum** — `SYS_STATUS`, `BATTERY_STATUS`, `EKF_STATUS_REPORT`, `HEARTBEAT`, `MISSION_CURRENT`, `NAV_CONTROLLER_OUTPUT`, `VFR_HUD`, `STATUSTEXT` não carregam campo de tempo | só `sample_attitude` e a metade `GLOBAL_POSITION_INT` de `sample_position` têm |
| Uma "família" alimentada por dois msgids colide na PK — `ATTITUDE` (#30) e `ATTITUDE_QUATERNION` (#31) com o mesmo `time_boot_ms` | insert falha |
| VTOL/rover com duas baterias colidem — `batt_id` está fora da PK | §7.5 diz que multi-bateria é o motivo do campo existir |
| Reboot do autopiloto no meio da sessão faz o timestamp recuar | PK baseada em tempo quebra |

**Correção:**

```
PK (session_id, system_id, t_recv_ns)  [+ batt_id em sample_battery_status]
WITHOUT ROWID, STRICT
```

- `t_boot_ms` vira coluna **simples e nullable** em todas as tabelas.
- Coluna `boot_epoch INTEGER NOT NULL DEFAULT 0`, incrementada quando `t_boot_ms` recua. É
  descontinuidade **medida**, não estimada — satisfaz P1. Toda chave ou índice sobre tempo de boot
  vira `(session_id, system_id, boot_epoch, t_boot_ms)`.
- **Uma tabela por msgid, não por família** — 13 tabelas: `sample_global_position` (33),
  `sample_gps_raw` (24), `sample_attitude` (30), `sample_attitude_q` (31), `sample_sys_status` (1),
  `sample_battery_status` (147), `sample_ekf_status` (193), `sample_estimator_status` (230),
  `sample_vfr` (74), `sample_mode` (0), `sample_mission_current` (42), `sample_nav_controller` (62),
  `statustext` (253). As 8 famílias de §7.5 sobrevivem como **views de leitura**.
  Isso é P2 (*"a unidade de armazenamento é a mensagem"*) levado ao pé da letra, e elimina a classe
  inteira de colisão.
- `session_id` **INTEGER** (rowid de `mission_session`), nunca TEXT UUID — 37 B por linha × 180 k
  linhas/h = ~6,7 MB/h de pura repetição de chave, e empurra `sample_position` contra o teto de
  largura de linha do `WITHOUT ROWID`.

Afeta as dataclasses do **Bloco B**, não só o DDL do Bloco C. Não bloqueia a Etapa 0.

> **Ver B-3:** esta correção, como escrita, **reintroduz a armadilha 25**. Ler a seção 9 antes de
> escrever o DDL.

### C-3 — O diagrama de concorrência de §10 não é implementável

§10 desenha `recv_match(timeout=0.5)` bloqueante **e** um `QTimer` de coalescing na mesma thread. Um
worker cujo `run()` fica em leitura bloqueante nunca retorna ao `exec()`, então **o QTimer nunca
dispara**: o snapshot é emitido zero vezes e a UI congela no estado inicial — violação de P9 sem
mensagem de erro.

**Correção:** coalescing por deadline monotônico dentro do próprio laço de leitura, sem event loop na
thread do adaptador:

```python
next_emit = time.perf_counter_ns() + PERIOD_NS
while not self._stop:
    msg = conn.recv_match(blocking=True, timeout=0.05)   # 50 ms, não 500
    if msg: self._accumulate(msg)
    if time.perf_counter_ns() >= next_emit:
        self.snapshot_ready.emit(self._freeze())
        next_emit += PERIOD_NS
```

Trivialmente testável com `FakeClock`. A thread do writer (§8.3, lote de 1 s) tem a mesma restrição.

Três correções acopladas a P6, todas antes do Bloco A:

1. **Worker `QObject` + `moveToThread`**, nunca subclasse de `QThread`. Subclassificar dá afinidade de
   GUI a todo atributo e slot do objeto — `source.stop()` chamado da UI executaria **na thread da
   GUI** enquanto `run()` está bloqueado, produzindo corrida silenciosa no estado do parser
   (armadilha 14 sob carga).
2. **O receptor tem de ser um método `@Slot` de um `QObject` da thread da GUI** — nunca lambda,
   nunca `functools.partial`. `worker.sig.connect(lambda s: self.label.setText(...))` toca um
   `QWidget` da thread do adaptador **mesmo com `Qt.QueuedConnection`**. Essa é a regra que de fato
   previne a armadilha 14; `QueuedConnection` explícito é asserção de intenção, não requisito
   funcional.
3. **Imutabilidade profunda:** `@dataclass(frozen=True, slots=True)` é só rasa. Um snapshot congelado
   contendo lista, dict ou `ndarray` ainda aliasa memória que o produtor muta — viola P1 (a UI lê
   valor que mudou depois de amostrado). `TelemetrySnapshot`/`FieldValue` só podem conter escalares,
   `str`, `None` e tuplas. Declarar `Signal(object)`, não `Signal(TelemetrySnapshot)`.

### C-4 — O bootstrap de logging de §18.1 tem um vazamento de descritor

```python
faulthandler.enable(file=open(logfile.with_suffix('.crash'), 'w'))   # ← defeito
```

O objeto de arquivo não é ligado a nada: o `faulthandler` guarda só o fd inteiro, o refcount cai a
zero no retorno, o CPython fecha o arquivo, e **o fd é reciclado pelo próximo `open()` do processo**.
O traceback do segfault acaba dentro do `sortie.log`, dentro do handle do SQLite, ou em lugar nenhum.

```python
_CRASH_FH = open(paths.log_dir() / 'sortie.crash', 'w', buffering=1, encoding='utf-8')
faulthandler.enable(file=_CRASH_FH, all_threads=True)   # nome de módulo, vive o processo inteiro
```

Mais três correções em §18.1:

- **"antes de importar Qt" não pode valer para `qInstallMessageHandler`** — ela *é* Qt. Ordem
  correta: (i) `faulthandler` + `RotatingFileHandler` + `sys.excepthook` + `threading.excepthook`
  antes do import do PySide6, para capturar falha *no próprio import* (o clássico "Could not load the
  Qt platform plugin"); (ii) importar `QtCore`; (iii) `qInstallMessageHandler`, antes de construir o
  `QApplication`, porque os avisos `qt.qpa.plugin` disparam durante a construção; (iv) criar o app.
- **Há um quarto caminho descoberto, e é o modelo de thread do projeto.** `threading.excepthook` só
  cobre `threading.Thread.run()`; um worker de `QThread` não é um `threading.Thread`. Os "3
  excepthooks" da armadilha 19 têm de virar quatro defesas: `try/except` no corpo de todo `run()` e
  de todo `@Slot`, logando e emitindo `link_state=ERROR` (P9 — *"thread que morre derruba o estado de
  conexão"*).
- Com `--windowed`, `sys.stderr` é `None`: setar `logging.raiseExceptions = False` e
  `logging.lastResort = None`.

### C-5 — `paths.py`: enum errado, e uma dependência circular no dia 1

§8.6 e a armadilha 18 prescrevem `QStandardPaths.AppDataLocation`. Para Qt 6 o correto é:

```python
base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
```

— enum **escopado**, `AppLocalDataLocation`, embrulhado em `Path()`, com guarda explícita se vier
string vazia.

**Circularidade:** `QStandardPaths` deriva `<APPNAME>` de `QCoreApplication.applicationName()`, que só
existe depois do `QApplication` — mas o Bloco A exige logging em arquivo **antes** de importar Qt, e o
logger precisa de diretório. Resolver computando o diretório de log de `os.environ['LOCALAPPDATA']`
(Windows) / `XDG_DATA_HOME` (Linux) no bootstrap pré-Qt, e **afirmando depois** que o `QStandardPaths`
concorda.

### C-6 — Correções menores, todas com efeito real

| # | Achado | Correção |
|---|---|---|
| a | §8.1 — o `.tlog` **não** usa `t_unix_us` de `SYSTEM_TIME`; o formato real é wall-clock com máscara | `usec = int(time.time()*1e6) & ~3`. Exceção deliberada à regra "nunca `time.time()`": não é intervalo, é carimbo absoluto exigido por formato externo. Abrir em `'wb'` — modo texto no Windows traduziria bytes `0x0A` dentro do payload binário e corromperia o log em silêncio |
| b | §8.2 está incompleto | Faltam `foreign_keys=ON` (off por padrão — toda FK do schema é decorativa sem ela) e `busy_timeout=5000` (0 por padrão — é exatamente por isso que o checkpoint TRUNCATE falha instantaneamente). Uma única fábrica `connect()` aplica `synchronous`, `temp_store`, `foreign_keys`, `busy_timeout` em **toda** conexão; `journal_mode=WAL` e `user_version` só na criação |
| c | `PRAGMA journal_mode=WAL` pode mentir | Afirmar o retorno: `if row[0].lower() != 'wal': raise`. P9 aplicado ao único pragma que falha em silêncio — relevante porque §8.6 põe o banco no perfil do usuário, que em máquina corporativa pode ser share de rede |
| d | §8.3 — controle de transação no Python 3.12 | `isolation_level=None` + `BEGIN IMMEDIATE`/`COMMIT` explícitos por lote. Não usar `autocommit=False`: ele abre transação no instante do `connect()`, e `journal_mode` não pode mudar dentro de transação |
| e | §8.3 / C8 — o critério "checkpoint funciona com o app aberto" está **invertido** | Com o app aberto ele *não pode* funcionar, e isso é correto. Exige shutdown ordenado: parar ingest → fechar `.tlog` → drenar writer → fechar **toda** conexão de leitura (um cursor não exaurido segura transação de leitura e bloqueia TRUNCATE) → afirmar `row[0] == 0` → fechar writer. Alternativa mais limpa para "um arquivo por missão": `VACUUM INTO 'mission_NNN.db'` |
| f | §8.5 — a justificativa da restrição de migração está errada | Manter a restrição, trocar a razão: o problema de `DROP COLUMN` é **capacidade**, não versão — SQLite proíbe dropar coluna que seja PK, UNIQUE, indexada, ou usada em CHECK/FK/view/trigger, que aqui é praticamente toda coluna. Guarda de runtime: `assert sqlite3.sqlite_version_info >= (3,37,0)` e declarar as tabelas **STRICT** |
| g | §7.5 — `q0..q3` contra formato de fio 1-based | MAVLink #31 manda `q1..q4` com `q1 = w`. Renomear para `q_w, q_x, q_y, q_z` — sem índice, auto-documentado, e torna o mapeamento revisável |
| h | trap 24 — `np.unwrap` atravessa gaps | Aplicar **por segmento contíguo**. Sobre série com dropout de 30 s, `unwrap` inventa uma rotação suave que nunca aconteceu — violação de P1 produzida pela própria função que a spec prescreve. Separar em `NaN`, que o PyQtGraph já renderiza como quebra |
| i | §13 — `PySide6-Essentials` **não** contém QtLocation, QtPositioning nem QtWebEngine | Estão no `PySide6-Addons`. A linha do mapa em §13 contradiz a decisão "não o meta-pacote" da mesma tabela. Não bloqueia v0.1 (mapa é §11.4). Pré-comprometer: mapa em pyqtgraph sobre tiles raster MBTiles pré-renderizados. ADR antes do marco do mapa |
| j | §17.2 — a razão dada para `qtbot.addWidget` está errada | Ele guarda **weakref**, não mantém nada vivo. Manter a regra pelo motivo verdadeiro: garante `close()`+`deleteLater()` antes do teardown do `QApplication`, que é o que evita o segfault |
| k | §13 — ordem de sondagem do PyQtGraph | É `[PyQt6, PySide6, PyQt5, PySide2]`, não a de §13. Isso torna um binding solto **mais** perigoso, não menos. `PYQTGRAPH_QT_LIB` e os `--exclude-module` seguem obrigatórios |

### C-7 — Não existe `.tlog` público redistribuível de origem SITL `[BLOQUEADOR]`

§17.1 diz *"gravar uma vez, por SITL, e commitar"*. Sob D1 não há SITL — e a busca não encontrou
**nenhum** `.tlog` pequeno, comprovadamente de origem SITL e redistribuível: o `flight.tlog` do
dronekit é Apache-2.0 mas de proveniência não documentada (provavelmente voo real) e 2,7 MB; o
`test.BIN` do pymavlink é DataFlash, não MAVLink; as amostras do pyulog são ULog. P8 proíbe a v0.1
depender de infra externa, então a Etapa 0 não tem como produzir `tests/fixtures/*.tlog`.

**Correção:** `tools/make_fixtures.py` vira **entregável da Etapa 0**, não contingência. Gera os
`.tlog` com os próprios encoders do pymavlink, explorando o enquadramento já verificado:

```python
from pymavlink.dialects.v20 import ardupilotmega as d
mav   = d.MAVLink(io.BytesIO(), srcSystem=1, srcComponent=1)
frame = mav.global_position_int_encode(t_boot_ms, lat_e7, lon_e7, alt_mm,
                                       rel_mm, vx, vy, vz, hdg).pack(mav)
out.write(struct.pack('>Q', t_unix_us) + frame)
```

Semear no CMAC (`-35.363261, 149.165230, 584 m, hdg 353` — verbatim de
`ardupilot/Tools/autotest/locations.txt`) e em Zurich Irchel (`47.397742, 8.545594`). Ambos são
constantes publicadas de simulador, então **§20 é satisfeito por construção**, e não por uma auditoria
que ninguém consegue fazer sobre um binário opaco. `malformed_frames.bin` sai do mesmo gerador,
truncando frames, corrompendo CRC e injetando msgid não registrado.

Gate de aceite da Etapa 0: `mavutil.mavlink_connection(fixture)` recupera os campos byte-idênticos, **e**
ler a fixture ArduPilot com `dialect='common'` produz `MAVLink_unknown` para #193 — provando que a
armadilha 28 é de fato detectável. Quando o WSL2 chegar na v0.3, gravar um `.tlog` real e substituir;
v0.1 e v0.2 não podem depender disso.

### C-8 — `file:./voo.tlog` de §15 não existe no pymavlink `[BLOQUEADOR]`

`mavutil.mavlink_connection` **não tem branch `file:`** — log abre por caminho nu. §15 declara que o
contrato de conexão é *"definido no modelo desde o início"*, então isso tem de ser corrigido **antes** de
o modelo ser escrito na Etapa 0; caso contrário todo caminho de replay da v0.2 nasce sobre uma forma
de string que levanta exceção.

**Correção:** o app mantém o esquema `file:` na sua própria UI e **remove o prefixo** antes de entregar
o caminho ao pymavlink — preserva contrato de URL uniforme para o usuário sem inventar comportamento
de biblioteca. Três ressalvas para o `FileReplaySource`:

- o retorno default é `mavmmaplog`, cuja `__init__` é só `(filename, progress_callback)` — ela
  **descarta em silêncio** `dialect`, `robust_parsing` e `source_system` passados ao
  `mavlink_connection`. Não assumir que configuração por conexão teve efeito.
- não há pacing de tempo real: `recv_msg` retorna tão rápido quanto lê. O relógio é do app.
- o prefixo de 8 bytes é microssegundo **Unix**, então mapeia para `t_unix_us`, **não** para
  `t_boot_ms`.

### C-9 — O teste de aceite do handshake (§9.4) valida a coisa errada

§9.4 manda *"falha se nenhuma `GLOBAL_POSITION_INT` chegar em 5 s"*. Mas a assimetria real **não é
ArduPilot × PX4 — é Copter × todo o resto**. Em Plane, Rover e Sub o teste **passa na taxa default do
firmware mesmo com o handshake completamente quebrado**, mascarando em silêncio exatamente o bug que
ele existe para pegar.

**Correção:** afirmar **taxa**, não chegada. Após pedir #33 a 5 Hz, medir o intervalo entre chegadas
numa janela de 5 s e afirmar que bate com o pedido dentro de tolerância. Reescrever a narrativa de
§9.4 como por-veículo. Acrescentar a semântica de `param2 = -1` (desabilitar) e `0` (taxa default),
úteis no caminho de desconexão.

### C-10 — Os contadores de §9.6 nunca subiriam

`parse_char` **não levanta** `MAVError` nem `struct.error` em bytes corrompidos sob configuração
default — quase nada é lançado. Os contadores de §9.6 (`bad_frame_count`, `crc_error_count`,
`unknown_msgid_count`) ficariam **permanentemente em zero**, e o teste de fuzz ("nenhuma exceção
escapa") passaria **vacuamente**.

**Correção:** dirigir os contadores por inspeção — `msg.get_type() == 'BAD_DATA'` (e seu `.reason`)
e `isinstance(msg, MAVLink_unknown)`. Manter o `try/except` por frame como cinto e suspensório
(`recv_msg` é genuinamente desprotegido), mas **a asserção do fuzz vira "nenhuma exceção escapa **E**
os contadores sobem"**. Isso também confirma §9.3: dialeto errado descarta em silêncio, não falha alto.

Sob D1, adicionar `WinError 10022` ao conjunto de exceções esperadas do adaptador — aparece em
`select`/`recv` sobre socket UDP não ligado, que é exatamente o estado "socket aberto, zero HEARTBEAT"
que §15 manda reportar como erro de conexão.

### C-11 — O dialeto do pymavlink é global ao processo

Não é por conexão. **Isso atinge §11.2 diretamente**: o produto inteiro é comparar N execuções — se uma
for `.tlog` de ArduPilot e outra de PX4, não é possível manter as duas abertas com dialetos diferentes
no mesmo processo.

**Correção:** usar `ardupilotmega` (superset estrito de `common`) para **toda** fonte, sempre — que já
é a decisão de §9.3. Teste unitário afirmando que nenhum caminho de código chama `set_dialect` mais de
uma vez. Registrar como restrição no ADR 0001.

### C-12 — Correções de fronteira do adaptador e de licença

| Achado | Correção |
|---|---|
| §7.4 **falta ~9 sentinelas** em mensagens que a spec já persiste | `GPS_RAW_INT.vel` e `.cog` alimentam ground speed e curso — persistir `65535 cm/s` como velocidade medida envenenaria todo veredito de comparação e todo alerta de pico (P1/P4 frontalmente) |
| Duas sentinelas valem **0** ou **65534**, não `UINT16_MAX` | A remoção tem de ser **tabela por campo**, jamais varredura global `if value == 65535`. `GPS_RAW_INT.yaw == 0` mapeado para `NULL` é precisamente a armadilha P1 que a spec existe para evitar; `voltages_ext == 0` é a imagem espelhada |
| A regra de soma de `voltages[]` está incompleta | Pular 65535 em `voltages[]` **e 0 em `voltages_ext[]`**; se `voltages[0] == 65534` o total é `65534 + voltages[1]` e a contagem de células é **desconhecida**; uma única célula preenchida pode ser o total do pack, não a célula 1. Gravar contagem de células derivada sem isso é violação de P1 |
| `pymavlink` **não é** "parser puro-Python sem binário nativo" | `fastcrc` e `lxml` são wheels nativas. §18 tem de incluí-las na análise do PyInstaller, e o guarda `ubuntu-latest` tem de verificar que as wheels manylinux resolvem para os mesmos pins |
| §19 trata `pymavlink` como MIT | Só os **módulos de dialeto gerados** são MIT. A biblioteca de runtime que se importa (`mavutil`, `mavwp`, `DFReader`) é **LGPLv3** e carrega a obrigação de linkagem dinâmica. Não deixar §19 reivindicar cobertura MIT para o pacote inteiro |
| §15 omite a porta que de fato se usa | `5760` = console (com `:wait`, o SITL pode **bloquear no start** até um cliente conectar), **`5762` = MAVLink #1**, `5763` = MAVLink #2. E remover a nota sobre precisar de `SO_REUSEADDR` — o pymavlink já o faz para `udpin` |
| §7.2 — default do `source_component` | É **0** (`MAV_COMP_ID_ALL`), não 1. Enviar com compid 0 é desencorajado, o que reforça fixar 245-250/190 |
| §7.2 — atribuição do sysid 255 ao QGC | Não verificável. Suavizar para "GCSs convencionalmente usam sysid próximo de 255, que é também o default do pymavlink" — o argumento de colisão sobrevive intacto |
| §7.2 — filtro `compid == 1` | `mavlink_id_assignment` avisa: *"you must not assume the type of the component from its ID"*. Manter o default, mas **sobrescritível pelo usuário** |
| §7.3 — `eph`/`epv` | Unidade é `1E-2`, ou seja HDOP×100. Por P3 o campo tem de ser `eph_cm`, ou o valor dividido por 100 antes de ser chamado de HDOP |

**Boas notícias verificadas:** `PySide6-Essentials` **inclui** o plugin `offscreen` no Windows (§17.2
está seguro); os hooks do PyInstaller coletam os plugins de plataforma automaticamente (sem
`--add-data` manual); `pymavlink` tem wheel cp312 win_amd64 (sem compilador C); §12 (50 msg/s com lote
de 1 s em WAL) é folgado; `mav.setup_signing()` existe (§9.3 é implementável); as 8 sentinelas que §7.4
lista estão **todas corretas** (o problema é o que falta); os cinco pontos de divergência de perfis de
firmware em §9.5 estão todos confirmados, e o pymavlink já traz `mavutil.mode_mapping_*`, então a
tabela de perfis pode ser semeada em vez de transcrita à mão.

---

## 2. Decisões

| # | Decisão | Razão |
|---|---|---|
| **D1** | Desenvolver v0.1/v0.2 em **Windows nativo**; Ubuntu 22.04 no WSL2 vira gate de entrada da v0.3 | §13.1 manda Linux-first, mas a máquina é Windows sem distro e **toda a v0.1 é 100 % offline** (§16) — nada nela toca SITL/Gazebo/ROS 2 |
| **D2** | **Python 3.12** via `uv`, `requires-python = ">=3.12,<3.13"` | Verificado: `pymavlink` tem wheel cp312; em 3.14 seria build a partir do fonte. C-1 torna a resolução de relógio ortogonal à versão |
| **D3** | Licença **MIT** | §19 deixa em aberto; MIT é a do PyQtGraph, convive sem atrito com LGPLv3 do PySide6/pymavlink, e maximiza fork num projeto de portfólio |
| **D4** | **GitHub público desde o commit 1** | É o que faz P10 e C12 serem contrato em vez de intenção — sem runner executando, `ci.yml` é um arquivo morto. Actions gratuito em repo público |

**D1 não relaxa o contrato de portabilidade — inverte os papéis no CI.** C1–C11 seguem integrais:

| | Doc canônico v1.1 | Este plano |
|---|---|---|
| Gate do `ci.yml` | `ubuntu-latest` | `windows-latest` |
| Guarda de portabilidade (C12) | `windows-latest` | `ubuntu-latest` |
| `release.yml` | `ubuntu-22.04` + `windows-latest` | inalterado |

Quatro das cinco armadilhas de portabilidade de §21 (29 encoding, 30 CSV, 32 `SO_REUSEPORT`, 33 handle
aberto) **só se manifestam no Windows** — desenvolver lá as expõe na primeira execução. A que se
perde, C11 (caso em caminho e módulo), é justamente a detectável por job barato, e passa a ser a
função do `ubuntu-latest`. Falha do guarda **quebra o build**, como o gate.

---

## 3. Estado do repositório

| Achado | Ação |
|---|---|
| Zero commits, branch `master` | `git branch -m main`; `.gitattributes` **antes** do commit 1 (senão o `eol=lf` não vale para o que já entrou) |
| Diretório `Docs/` com maiúscula; §14 especifica `docs/` | Renomear antes do commit 1. É C11 mordendo antes da primeira linha de código — invisível no Windows, quebra no job `ubuntu-latest`. Com zero commits custa um `Rename-Item`; depois vira `git mv` em dois passos |
| Sem `pyproject.toml`, venv ou CI | Etapa 0 |

---

## 4. Etapa 0 — Ambiente (1-2 h)

### 4.1 Higiene, antes de qualquer código

```bash
git branch -m master main
```

Depois: `Docs/` → `docs/`; `.gitattributes` (`* text=auto eol=lf`); `.gitignore` de §20
(`*.db`, `*.db-wal`, `*.db-shm`, `*.tlog`, `*.bin`, `exports/`, `.venv/`, `__pycache__/`, `dist/`,
`build/`, `*.spec`); `LICENSE` (MIT, D3); `THIRD_PARTY_NOTICES.md` — incluindo **`lxml` e `fastcrc`**,
que chegam transitivamente pelo `pymavlink`.

### 4.2 Ambiente Python

```bash
uv venv --python 3.12
uv add "PySide6-Essentials>=6.11.1,<6.12" "pymavlink>=2.4.49,<2.5" "pyqtgraph>=0.14.0" "pyulog>=1.2.4" "numpy>=2.0"
uv add --dev "pytest>=8.0" "pytest-qt>=4.5.0" "pytest-cov>=5.0" "ruff>=0.16.3" "mypy>=2.3.1,<3" "import-linter>=2.13" "pyinstaller>=6.22.1,<7"
```

Nenhum PyQt no venv, em nenhuma variante (§18). Se algo puxar PyQt5/PyQt6 como transitiva, trava a
Etapa 0 até resolver.

### 4.3 `pyproject.toml`

Blocos que precisam existir no dia 1 — o contrato de camadas em particular, porque seis diretórios
irmãos sem contrato degeneram em import circular no primeiro mês:

```toml
[tool.importlinter]
root_package = "app"

[[tool.importlinter.contracts]]
name = "A6 - regra de dependencia entre pacotes"
type = "layers"
containers = ["app"]
layers = ["ui", "services", "core", "adapters | database", "models"]
exhaustive = true
```

O `|` torna `adapters` e `database` irmãos independentes — nenhum importa o outro, ambos só importam
`models`. Exige `import-linter >= 2.13`; em versão anterior o contrato **falha em parsear em
silêncio**. `exhaustive = true` quebra o build se um novo pacote de topo aparecer sem camada
atribuída.

```toml
[tool.pytest.ini_options]
pythonpath = ["."]           # SEM ISTO, `import app` falha (B-2) e a Etapa 0 não fecha
qt_api     = "pyside6"       # pinar o binding, não confiar na auto-sondagem
addopts    = '-m "not sitl"' # §17.1
markers    = ["sitl: exige SITL rodando", "soak: teste longo, roda no nightly"]
```

`PYTHONPATH: .` também no `env:` do workflow — o `lint-imports` é console script e não enxerga o cwd
(B-2). O `mypy` funciona sem isso, que é por que o sintoma parece ser só do pytest.

`QT_QPA_PLATFORM=offscreen` vem do bloco `env:` do workflow — `pytest` puro **não** entende uma chave
`env = [...]` de ini. Mais `[tool.ruff]` (linha 100, regras padrão + `I`) e `[tool.mypy]` (strict em
`app/models` e `app/core`, permissivo em `app/ui`).

### 4.4 `ci.yml` — três jobs desde o commit 1

| Job | Runner | Papel | Conteúdo |
|---|---|---|---|
| `test` | `windows-latest` | **gate** (D1) | ruff, mypy, import-linter, pytest com `QT_QPA_PLATFORM=offscreen` |
| `portability` | `ubuntu-latest` | **guarda** (C12) | suíte idêntica — é o que pega C11 |
| `package` | `windows-latest` | P10 | PyInstaller `--onedir` da **janela vazia de verdade** (importando PySide6 — ver B-5), artefato subido |
| `package-linux` | `ubuntu-22.04` | P10 | build-only, artefato retido, **nunca anexado a Release** (B-8) |
| `nightly.yml` | `windows-latest` | §12 | `cron` + dispatch, `pytest -m soak`, publica MB/h, pico de RSS, pico de `-wal` e p95 de latência. Criar **vazio na Etapa 0** |

O passo da armadilha 20 precisa de `QT_QPA_PLATFORM: ""` no `env:` **daquele passo** — `env:` de passo
soma com o de workflow em vez de limpar, então como estaria escrito ele rodaria em offscreen e nunca
tocaria em `qwindows.dll` (B-5).

Passo extra nos **dois** jobs de teste, porque nenhuma das duas grandezas é pinável em
`pyproject.toml` — são propriedades do build do interpretador:

```bash
python -c "import sqlite3,time;print(sqlite3.sqlite_version, time.get_clock_info('perf_counter'))"
```

Falha se `sqlite_version < 3.37` (piso de STRICT) ou se `resolution > 1e-6` (C-1). É o teste que teria
pego o defeito do relógio.

### 4.5 `tools/make_fixtures.py` — entregável, não contingência (C-7)

Gerador sintético via encoders do `pymavlink`, semeado em CMAC e Zurich Irchel. Produz
`ardupilot_copter_takeoff.tlog`, `px4_quad_mission.tlog` e `malformed_frames.bin`. §20 satisfeito por
construção. Ver C-7 para o esqueleto e o gate de round-trip.

### 4.6 Modelo de endpoint (C-8)

Escrever o contrato de conexão **já corrigido**: o app expõe `file:` na UI e remove o prefixo antes de
chamar o `pymavlink`. Incluir a linha faltante `5762 = MAVLink #1` e remover a nota de `SO_REUSEADDR`.
§15 diz que esse modelo é definido "desde o início" — se nascer errado, toda a v0.2 nasce sobre ele.

### 4.7 Pronto quando

- [ ] `tools/make_fixtures.py` gera as três fixtures, e o round-trip recupera campos byte-idênticos
- [ ] ler a fixture ArduPilot com `dialect='common'` produz `MAVLink_unknown` para #193 — prova que a
      armadilha 28 é detectável, e não uma afirmação de fé
- [ ] script `pymavlink` de 10 linhas lê a fixture e imprime HEARTBEAT com sysid, compid,
      `custom_mode`, `base_mode`
- [ ] `ci.yml` verde em `windows-latest` **e** `ubuntu-latest`
- [ ] o passo de sanidade de relógio/SQLite passa nos dois runners
- [ ] `import-linter` executa e passa com os pacotes ainda vazios
- [ ] artefato do PyInstaller baixável e executável
- [ ] commit 1 já contém `.gitattributes`, `.gitignore` e `LICENSE`
- [ ] **ADR 0012** (D1), **ADR 0013** (C-2) e **ADR 0001** (C-11, dialeto global) escritos

> Critério **deliberadamente adiado**: "HEARTBEAT vindo do SITL local" (§16, Etapa 0, item 1) move
> para o gate de entrada da v0.3.0. Consequência direta de D1, registrada no ADR 0012 — não é omissão.

---

## 5. v0.1.0 — cinco blocos

§16 lista nove entregas em 30 dias. Reagrupadas por dependência real.

### Bloco A — Esqueleto executável (dias 1-3)

| Arquivo | Conteúdo | Correção aplicada |
|---|---|---|
| `main.py` | bootstrap de logging em 4 passos ordenados; `create_app(argv)`; nada de `QApplication`/`QWidget`/`QTimer` em escopo de módulo; flag `--selftest` | **C-4** |
| `app/core/paths.py` | `AppLocalDataLocation` escopado + `Path()`; log dir do env no bootstrap pré-Qt, afirmado depois contra `QStandardPaths` | **C-5** |
| `app/core/clock.py` | `Protocol Clock`, `RealClock` (`perf_counter_ns`), `FakeClock`; asserção de resolução no startup | **C-1** |
| `app/models/snapshot.py` | `TelemetrySnapshot`, `FieldValue(value, age_ms, is_stale)`, `LinkState`; **só escalares, str, None e tuplas**; limiares de stale de §7.7 | **C-3.3** |
| `app/adapters/base.py` | `TelemetrySource` Protocol + esqueleto worker `QObject` + `moveToThread` | **C-3.1** |
| `app/adapters/mock/source.py` | worker com coalescing por deadline monotônico dentro do laço, sem `QTimer` na thread | **C-3** |
| `app/ui/main_window.py` | janela com `QLabel` de altitude, receptor `@Slot` de `QObject` da GUI | **C-3.2** |

O logging vem antes de tudo porque é o que torna todo bug seguinte diagnosticável (armadilha 19).

**Pronto quando:** janela abre e a altitude do mock atualiza · `--selftest` sai 0 e **`sortie.log` e
`sortie.crash` existem** com fd válido (é o que impede C-4 de regredir) · teste afirma
`worker.thread() is not QApplication.instance().thread()` · replay testado com `FakeClock`, zero
`sleep`.

### Bloco B — Modelo e primeiro gráfico (dias 4-**8**, rebaselinado por B-8)

13 dataclasses (uma por msgid, **C-2**) · `sentinels.py` como **tabela por campo** — nunca varredura
global `== 65535` (**C-12**) — cobrindo as 8 sentinelas de §7.4 **mais as ~9 que faltam**, com as
regras de `voltages[]`/`voltages_ext[]` e sem derivar contagem de células · três relógios + detector
que cobre **wrap de
2³² e reboot para zero** (`boot_epoch`) · `gps_fix_type < 3` → `lat`/`lon` `NULL`, nunca
carry-forward (P1, armadilha 25) · `MockSource` com as 6 categorias de injeção de falha ·
primeiro gráfico PyQtGraph.

Gráfico ao vivo: `setDownsampling(auto=True, method='peak')` + `setClipToView(True)` — a janela de
60 s × 20 Hz = 1200 pontos já cabe no alvo de §12 sem LTTB à mão. Buffers numpy **de propriedade
exclusiva da thread da GUI**: `setData` guarda uma *view*, então escrita pela thread do adaptador
produz leitura rasgada na pintura, sem erro e sem crash — é a armadilha 14 numa forma que a regra
"nada Qt cruza thread" não pega, porque `ndarray` não é objeto Qt. `NaN` marca gap; proibido
`setSkipFiniteCheck(True)`, que apagaria toda marca.

**Pronto quando:** os testes acima verdes · **screenshot no README** (entrega 4 de §16, primeiro
artefato mostrável).

### Bloco C — Persistência (dias 9-14) — maior risco técnico

DDL com a PK de **C-2**, `WITHOUT ROWID`, `STRICT`, `session_id` INTEGER · fábrica `connect()` única
com os pragmas de **C-6b**, retorno do WAL afirmado (**C-6c**) · writer em thread própria, conexão
própria, `isolation_level=None` + `BEGIN IMMEDIATE`/`COMMIT` por lote (**C-6d**) · backpressure A5
(fila limitada, descarta o **mais novo** — decidido, ver B-6 — com o total persistido em
`mission_session` e a faixa em `writer_drop`; nunca bloquear a leitura do socket, que
estouraria o buffer UDP em silêncio e violaria P9) · `.tlog` cru com o formato de **C-6a** ·
shutdown ordenado de **C-6e** · migrações por `user_version` com o teste obrigatório de §8.5.

O soak de 1 h **não** deve perseguir taxa de insert — §12 é folgado. Deve perseguir as duas coisas que
realmente estouram: **inanição de checkpoint** (leitor com transação ou cursor não exaurido aberto
entre repaints faz o `-wal` crescer sem limite e quebra a NFR de memória) e o pico de fsync do
auto-checkpoint, que jamais pode ser disparado da thread da GUI.

**Pronto quando:** soak dentro de §12 **e `-wal` limitado** · fixture `schema_v1.db` migra e o replay
funciona · dois `ATTITUDE` e dois `ATTITUDE_QUATERNION` com `time_boot_ms` idêntico inserem sem
colidir · reboot simulado insere e incrementa `boot_epoch` · fixture de veículo com **duas baterias**
· `.tlog` escrito pelo mock reabre no `pymavlink` com contagem de mensagens conferida · **duas**
medições de MB/h separadas (SQLite e `.tlog`), com a taxa de stream declarada.

### Bloco D — Replay e alertas (dias 15-22, dois dias cedidos ao Bloco B por B-8)

`SqliteReplaySource` como `TelemetrySource` (A1/P7 — a UI não sabe se está ao vivo) · cursor por
`np.searchsorted`, jamais `sleep(dt)` acumulado (armadilha 23), **com o clamp de 1 s de C-1** ·
velocidades 0.25x/1x/16x, pause, scrub, reverso como velocidade negativa · decimação min/max por
bucket **na camada de leitura do SQLite**, não no plot — o ponto é não puxar 180 k linhas do banco ·
sem interpolação (P1), gap visível · `unwrap` **por segmento** (C-6h) e SLERP em numpy (~12 linhas,
sem scipy) com o flip de sinal `d<0` e o fallback nlerp `d>0.9995` · motor de alertas com as 10 regras,
`rule_version` em cada evento, e **nenhuma regra avaliando `NULL`**.

> O cursor precisa declarar como atravessa as tabelas: §11.1 diz que o replay roda sobre `t_boot_ms`,
> mas após C-2 a PK é `t_recv_ns` e 6 das 8 famílias não têm tempo de boot. Resolução: **o cursor roda
> sobre `t_recv_ns`** (único eixo presente em todas as tabelas); `t_boot_ms` + `boot_epoch` permanecem
> como eixo canônico de *correlação* e de alinhamento entre execuções (§11.2), imune a `--speedup`.
> Vai no ADR 0004.

**Pronto quando:** seek, todas as velocidades e o reverso testados com `FakeClock`, sem `sleep` · os
10 alertas disparam contra o mock · nenhum dispara contra `NULL` · série com dropout mostra quebra,
não rotação inventada.

### Bloco E — Export, empacotamento e release (dias 23-30)

CSV com `newline=''` e `encoding='utf-8'`, cabeçalho com unidade (C3/C4, armadilhas 29/30) · GPX/KML ·
PyInstaller `--onedir` com `PYQTGRAPH_QT_LIB=PySide6` e os três `--exclude-module`, validado em runner
limpo com `QT_DEBUG_PLUGINS=1` · §19 completo (THIRD_PARTY_NOTICES com `lxml`/`fastcrc`, diálogo
Sobre/Licenças, textos LGPLv3 no bundle, nota de relink) · banco de demo versionado + `--replay demo.db`
· README com screenshot, GIF e MB/h medidos.

**Pronto quando** — os sete critérios de §16, com um ajuste (revisado por B-8): a v0.1.0 é
**Windows-only na Release** — só o ZIP Windows + SHA-256. Publicar binário Linux nunca executado é
pior que não publicar, e contradiz a política da própria spec, que adia a release Windows até haver
máquina Windows; o caso espelho merece a mesma resposta. O build Linux continua como job **build-only**
no `ci.yml`, com artefato retido e nunca anexado a Release, para pegar regressão de empacotamento
continuamente em vez de na hora da tag.

---

## 6. ADRs

Escritos no momento da decisão (P10), não no fim.

**Numeração congelada** — índice em [`docs/adr/README.md`](adr/README.md). Números são referência
permanente: nunca reutilizar, nunca renumerar.

| # | Decisão | Quando |
|---|---|---|
| **0001** | pymavlink e não MAVSDK; dialeto global ao processo; correções da tabela §9.2 | ✅ **escrito** |
| **0012** | Windows-first até a v0.3, contrato de §13.1 invertido no CI (substitui 0011) | ✅ **escrito** |
| **0013** | `perf_counter_ns`; `t_recv_ns` como eixo do cursor; contrato do `Clock` em `app/models/` | ✅ **escrito** |
| **0014** | Chave primária das amostras; `boot_epoch`; uma tabela por msgid; guarda de fix em #33 | ✅ **escrito** |
| **0015** | Fixtures sintéticas; golden frames; segundo componente | ✅ **escrito** |
| **0016** | O produto se chama Sortie | ✅ **escrito** |
| 0002 | Worker `QObject` + `moveToThread`; coalescing por deadline, não `QTimer` | Bloco A |
| 0003 | Record raw, derive views | Bloco C |
| 0007 | WAL, writer dedicado, backpressure, shutdown ordenado | Bloco C |
| 0005 | Replay como adaptador | Bloco D |
| 0008 | `--onedir` e conformidade LGPL | Bloco E |
| 0017 | Mapa em pyqtgraph sobre MBTiles raster, não QtLocation (C-6i) | v0.4 |

---

## 7. Riscos

| Risco | Sinal | Resposta |
|---|---|---|
| **Bloco B estoura o prazo** (risco reatribuído por B-8 — não é o C) | dia 8 chegando sem as dataclasses e o primeiro gráfico | Já rebaselinado para 5 dias. Ordem de descope: 13 dataclasses → as 5 que o gráfico e o DDL precisam (33, 30, 1, 147, 0); as outras 8 vão ao Bloco C junto das tabelas. Sem isso, o estouro do B é absorvido em silêncio pelo time-box do C, que então "prova" que o risco era o C |
| Bloco C estoura o prazo | soak reprovando repetidamente | Maior risco **técnico** (distinto do risco de prazo). Se passar de 8 dias, cortar o `.tlog` cru e manter só o SQLite |
| **v0.1 sem critério de desistência** — §16 define um para a v0.3 e nenhum para a v0.1 | dia 24 sem os 10 alertas verdes | Publicar v0.1.0 com mock + persistência + replay + export, e mover alertas para a v0.1.1 |
| C11 acumulando sem ninguém ver | `ubuntu-latest` vermelho recorrente | tratar falha do guarda como quebra de build |
| C-2 subestimado | DDL reescrito no meio do Bloco C | ADR 0013 **antes** do Bloco B — as dataclasses dependem dele |
| WSL2 na v0.3 não conectar | bind sem receber pacote nenhum, sem erro | `networkingMode=mirrored` (armadilha 2) |
| Escopo vazando | vontade de abrir `.tlog` antes do Bloco E | v0.1 é portfólio completo por si; fechá-la vale mais que antecipar |

**Deliberadamente adiado:** `MavlinkSource` contra rede (v0.2) · SITL (v0.3) · protocolo de missão,
comandos e mapa (v0.4) · comparação de execuções (v0.5) · bridge ROS 2 (v0.6).

---

## 8. Verificação

Ponta a ponta, na ordem em que fecha cada gate:

1. **Etapa 0** — `uv run python scripts/read_heartbeat.py tests/fixtures/*.tlog` imprime sysid/compid/
   modo; `uv run lint-imports` passa; push verde nos três jobs; baixar o artefato do job `package` e
   executá-lo.
2. **Bloco A** — `uv run pytest tests/test_bootstrap.py` (existência e validade de `sortie.log`/`sortie.crash`);
   `uv run python main.py --selftest; echo $LASTEXITCODE` → 0; `uv run python main.py` mostra altitude
   variando.
3. **Bloco B** — `uv run pytest -k "sentinel or clock or wrap"`; conferir o screenshot do gráfico.
4. **Bloco C** — `uv run pytest tests/test_migration.py` sobre `schema_v1.db`; soak de 1 h medindo
   RSS, tamanho do `-wal` e MB/h; reabrir o `.tlog` gerado com `mavutil` e conferir contagem.
5. **Bloco D** — `uv run pytest tests/test_replay.py` (determinístico, sem `sleep`); disparar as 6
   injeções de falha do mock pela UI e ver os 10 alertas.
6. **Bloco E** — executar o `--onedir` em VM Windows limpa **sem Python**, com `QT_DEBUG_PLUGINS=1`;
   abrir o CSV exportado no Excel e conferir que não há linha em branco entre registros (C4).

---

## 9. Correções da crítica adversarial

Duas passagens independentes atacaram o plano acima. Ambas concluíram **"não executável como
escrito"**. Os achados abaixo têm precedência sobre as seções 4-5 onde conflitarem.

### B-1 — Não há caminho para a amostra chegar ao writer `[BLOQUEADOR]`

O único payload que cruza a fronteira de thread em todo o plano é `snapshot_ready = Signal(object)`
com o `TelemetrySnapshot` coalescido a 15 Hz. Mas o Bloco C define 13 tabelas por msgid e o Bloco D
define `AlertEngine.on_sample(...)` — e **nada produz linha de amostra do lado consumidor**. As
mensagens acumuladas entre deadlines são dobradas no snapshot e descartadas.

Como está desenhado, o banco grava ~15 linhas/s em vez de ~50 msg/s, achata mensagens com timestamps
independentes numa forma de linha só, e viola **P2 e P1 frontalmente**. Pior: **todo teste do Bloco C
sobrevive a isso** — `test_writer_batching` conta COMMITs, não linhas; o soak afirma "zero descartes"
lendo o contador de backpressure, que leria zero porque a perda acontece *a montante* da fila.

**Correção:** o worker do adaptador segura referência direta à `DropOldestQueue` e ao `TlogSink`, e
empurra **toda** `Sample` decodificada e **todo** msgbuf cru ele mesmo — sem Qt, sem thread da GUI, sem
snapshot envolvido. O snapshot continua sendo só view. `AlertEngine.on_sample` roda na thread do
adaptador, no mesmo caminho por mensagem. A6 proíbe `adapters → database`, então a fila entra por um
port em `app/models/ports.py` (`SampleSink.put(samples)`), com o concreto ligado na raiz de composição.

**Teste que teria pego:** rodar `MockSource` por N segundos simulados com contagem conhecida por
msgid, fechar a sessão, e afirmar `SELECT count(*)` por tabela **igual exata** ao gerado.

### B-2 — `import app` não resolve; a Etapa 0 não fecha `[BLOQUEADOR]`

Com `[tool.uv] package = false` e sem `pythonpath`, o projeto nunca é instalado no `.venv` e nada põe
a raiz no `sys.path`. `lint-imports` é console script — `sys.path[0]` é `.venv/Scripts`, não o cwd,
então `root_package = "app"` falha ao importar. `pytest` idem: sem `__init__.py` em `tests/` e sem
`conftest.py` na raiz, o modo prepend insere `tests/`, e todo `from app.models...` levanta
`ModuleNotFoundError`. **O `mypy` não é afetado** (deriva a raiz de `app/__init__.py`) — que é
exatamente por que isso vai parecer um mistério só do pytest.

**Correção:** `pythonpath = ["."]` em `[tool.pytest.ini_options]` e `PYTHONPATH: .` no `env:` do
workflow. Verificar no dia 1 contra os pacotes vazios — é o gate mais barato da Etapa 0 e hoje não
pode ficar verde.

### B-3 — C-2 reintroduz a armadilha 25 `[BLOQUEADOR]`

Esta é a mais séria: **a correção C-2 quebra o que ela deveria proteger.**

`GLOBAL_POSITION_INT` (#33) **não tem campo `fix_type`** — é a saída de posição do EKF, e o ArduPilot
continua publicando, por dead reckoning, depois que o fix de GPS se perde. Na tabela de "família"
unificada de §7.5, o `fix_type` vindo de #24 estava no escopo da regra P1. **Dividir por msgid removeu
esse contexto entre mensagens**, e o único CHECK escrito (`gps_fix_type >= 3 OR lat/lon IS NULL`) fica
em `sample_gps_raw` — que não é de onde o mapa, a trilha, as derivações de haversine e o `XTRACK_HIGH`
leem.

Resultado: o banco grava alegremente posição dead-reckoned como posição medida, sem marca. É
precisamente o defeito que P1 existe para impedir. A afirmação de que a armadilha 25 estava
"estruturalmente" desarmada é **falsa**.

**Correção:** duas colunas medidas e timestampadas em `sample_global_position`, carimbadas pelo
adaptador a partir do último `GPS_RAW_INT` visto:

```sql
gps_fix_type_at_recv INTEGER,
gps_fix_age_ns       INTEGER NOT NULL,
CHECK (gps_fix_type_at_recv >= 3 OR (lat_dege7 IS NULL AND lon_dege7 IS NULL)),
CHECK (gps_fix_type_at_recv IS NOT NULL)   -- CHECK que avalia NULL passa
```

Ambas são observações, não estimativas — P1 se sustenta. Teste: alimentar fix=3, fix=1, fix=3 e
afirmar que a linha #33 do meio tem `lat`/`lon` `NULL` **mesmo com a mensagem de fio carregando
coordenada**.

### B-4 — O banco de demonstração é inalcançável `[BLOQUEADOR]`

Três falhas independentes, e é o último item antes da tag. (1) `.gitignore` bloqueia `*.db` com
negação só para `tests/fixtures/*.db` — `git add` de caminho ignorado é no-op silencioso, então
"versionado" falha. (2) Sem `--add-data`, o banco não entra no bundle `--onedir`; em máquina limpa o
exe não tem o que replayar. (3) Mesmo empacotado, abrir no lugar falha: WAL exige criar `-shm`/`-wal`
ao lado do arquivo, e um bundle em `Program Files` é somente leitura — a armadilha 18 exata que §8.6
se gaba de evitar.

**Correção:** `assets/demo/demo.db` + `!assets/demo/*.db` no `.gitignore` + asserção no CI de que
`git ls-files` retorna não-vazio; `--add-data "assets/demo/demo.db;assets/demo"` + helper
`resource_path()` para `sys._MEIPASS`; e **copiar para `paths.db_dir()` antes de abrir**, para que o
WAL tenha diretório gravável.

### B-5 — Os dois gates de CI da Etapa 0 são no-ops demonstráveis

| Gate | Por que não prova nada | Correção |
|---|---|---|
| Passo da armadilha 20 (`QT_DEBUG_PLUGINS=1`) | O `env:` de workflow fixa `QT_QPA_PLATFORM: offscreen` para **todo** job e passo; `env:` de passo **soma**, não limpa. O passo que existe para provar que `qwindows.dll` foi coletado roda no plugin offscreen e nunca toca nele. Fica verde para sempre, e o erro aparece pela primeira vez na máquina do avaliador | `QT_QPA_PLATFORM: ""` no `env:` daquele passo, e afirmar sobre a saída que ela **não** contém `Could not load the Qt platform plugin` |
| Job `package` da Etapa 0 | O `main.py` da Etapa 0 é stub de log e **não importa Qt**. A análise estática do PyInstaller não acha PySide6, o hook nunca roda, e o `--onedir` produzido **não contém Qt nenhum**. O checkbox fica verde provando nada sobre aquilo que P10 existe para desarmar | Restaurar a **janela vazia**: importar PySide6, `create_app()`, `QMainWindow` nu com `show()`, e `QTimer.singleShot(300, app.quit)` sob `--selftest`. ~15 linhas, e exercita o hook, a coleta de plugin e a ordem do `qInstallMessageHandler` |

### B-6 — Perdas de backpressure nunca são persistidas

O `dropped_total` é publicado no EventBus e pintado no painel — e some ao fechar o processo. Uma
sessão gravada com 20 % das amostras descartadas é **byte-indistinguível** de uma completa ao
reabrir, e o veredito de §11.2 na v0.5 seria calculado sobre subamostra silenciosamente decimada e
emitido como "reprodutível". P1 na camada de análise, P9 na de persistência. Além disso, um buraco de
backpressure replaya **idêntico** a um gap de link, então a marca visual obrigatória de §11.1 mente
sobre a causa.

**Correção:** `queue_dropped_total` em `mission_session`, gravado no fechamento; tabela
`writer_drop(session_id, t_recv_ns_start, t_recv_ns_end, dropped_count)` para o replay desenhar a
faixa como "dado perdido por backpressure", distinta de gap de link; alerta `SAMPLES_DROPPED` no
primeiro descarte; e **ADR 0007 registra que qualquer relatório de comparação sobre sessão com
`queue_dropped_total > 0` é marcado DEGRADED e recusa veredito PASS**.

**Decidido:** A5 passa a **descartar o mais novo**. O consumidor desta fila é o writer de arquivo, não
a UI — descartar o mais novo é igualmente não-bloqueante e preserva o dado anterior, que numa
ferramenta de análise de falha é tipicamente o pré-falha. A redação "mais antigo" da v1.1 vinha da
intuição de fila que alimenta UI ao vivo, onde o recente é o relevante; aqui não é. Já aplicado ao A5
do documento canônico (v1.2).

### B-7 — A NFR de latência que justifica C-1 nunca é medida

C-1 é bloqueador cuja justificativa declarada é que 15,6 ms dá ±16 % contra o orçamento de 100 ms.
E então **nenhum arquivo, teste ou critério mede essa latência**. O projeto paga o custo inteiro da
decisão de relógio e não colhe o benefício.

**Correção:** `newest_t_recv_ns` no snapshot (já é o máximo do acumulador); em
`MainWindow.on_snapshot`, `clock.perf_ns() - snap.newest_t_recv_ns` num ring numpy de tamanho fixo em
`app/core/latency.py`; p50/p95/max no painel; teste com `FakeClock` para a matemática do percentil; e
critério no Bloco E: *"p95 < 100 ms medido em 60 s de mock a 50 msg/s, no executável empacotado"*.

### B-8 — Achados que mudam cronograma e escopo

| Achado | Correção |
|---|---|
| **O Bloco B é o risco de prazo, não o C.** Em 3 dias ele deve entregar 8 arquivos em `models` (13 dataclasses), 9 em `adapters` (tabela de 17 sentinelas, `DECODERS` para 13 msgids, mock emitindo frames, contadores, boot-epoch, relógio de recepção), 3 em `core`, o primeiro painel e ~22 testes. §16 dava dias 4-6 a "modelo interno completo". Ele estoura, e o estouro é silenciosamente absorvido pelo time-box do Bloco C, que então "prova" que o risco era o C | Rebaselinar o Bloco B para **5 dias**, tirados do Bloco D. Ordem de descope: 13 dataclasses → as 5 que o primeiro gráfico e o DDL precisam (33, 30, 1, 147, 0); as outras 8 vão ao Bloco C junto das tabelas |
| **§16 não define critério de desistência para a v0.1** (só para a v0.3) | *"Se o dia 24 chegar sem os 10 alertas verdes, publicar v0.1.0 com mock + persistência + replay + export e mover alertas para a v0.1.1"* |
| Soak inexecutável: marcador `slow` não registrado (com `--strict-markers` o teste **erra na coleta**), `psutil` não é dependência, e `ci.yml` só dispara em push/PR — nenhum workflow roda o soak | Renomear o marcador para `soak`; `psutil>=6.0` no grupo dev (com `--exclude-module psutil` no PyInstaller); **`nightly.yml`** com `cron` + `workflow_dispatch`, publicando MB/h, pico de RSS, pico de `-wal` e p95 no job summary. Criar vazio já na Etapa 0 |
| `schema_v1.db` não tem produtor, e na v0.1 existe **uma** versão de schema — o teste obrigatório de §8.5 é vacuoso, e o plano defende orçamento para ele | `tools/make_schema_v1.py` emite um banco deliberadamente mais velho (m0001 menos uma coluna nullable, `user_version` diferente), e escrever o `m0002` real que a devolve. Aí o teste exercita o runner, os limites de `ADD COLUMN` e a asserção de replay de verdade |
| `stamp()` sem parâmetro só serve ao caminho ao vivo. No `.tlog` o `t_recv_ns` vem do prefixo de 8 bytes, cuja fonte é `time.time()` — ~15,6 ms no Windows. Um log gravado por GCS carimba **muitos frames consecutivos com o mesmo microssegundo**, e o primeiro `.tlog` real viola a PK. A PK congela no Bloco C | `stamp(observed_ns: int | None = None)` retornando `max(observed_ns or perf_ns(), last + 1)`, usado por todo caminho de ingestão. Teste: cinco `observed_ns` idênticos produzem cinco saídas estritamente crescentes |
| O teste de round-trip da fixture é **tautológico** — encoder e decoder do pymavlink compartilham qualquer erro de entendimento do formato. Sob D1 não há SITL nem log real até a v0.3, então o parser fica sem teste contra verdade de campo por toda a vida de v0.1 e v0.2 | `tests/fixtures/golden_frames.py` com frames hex verificados à mão (de mavlink.io / vetores da c_library) para #0, #33, #24 e #147, mais os valores documentados como literais separados. Afirmar que **nosso** encoder reproduz aqueles bytes e **nosso** decoder aqueles valores. Converte auto-consistência em contrato externo, e é o teste que pega bump de versão do pymavlink |
| **`HOME_POSITION` (#242) e `SYSTEM_TIME` (#2) não existem em lugar nenhum** — nem decoder, nem tabela, nem mock. Sem #242, `alt_rel_home_m` não tem significado (palavras de §7.6) e `dist_home_m` não tem origem. Sem #2, `t_unix_us` é NULL em toda linha para sempre, e o GPX do Bloco E sai sem timestamp | Acrescentar ambos como amostra, tabela e saída do mock. No GPX, definir o fallback: derivar de `started_at_unix_us + (t_recv_ns - t0)` e **marcar como derivado de relógio do host** (P1) |
| A v0.1 **não demonstra a tese do produto**. O avaliador vê altitude ao vivo, gráfico, scrubber e alertas — ou seja, um visualizador de telemetria, exatamente a capacidade que §2.1 classifica como *"requisito de paridade, nunca argumento de venda"*. §16 diz que a v0.1 já é portfólio completo; é verdade como portfólio de disciplina de engenharia, não da ideia do produto | Quase de graça: `tools/make_demo_db.py` emite **duas** sessões do mesmo plano sintético — uma nominal, uma com as falhas — e `--replay demo.db` abre as duas sobrepostas no mesmo eixo. O ring buffer e o pyqtgraph já suportam N curvas. É um dia de trabalho e é a única coisa na v0.1 que comunica para que serve o produto. Screenshot dessa sobreposição no topo do README |
| Publicar binário Linux não executado é pior que não publicar, e contradiz a política da própria spec (§13.1 adia a release Windows até haver máquina Windows; o caso espelho merece a mesma resposta) | v0.1.0 **Windows-only na Release**: só o ZIP Windows + SHA-256. Manter o build Linux como job **build-only** no `ci.yml` (artefato retido, nunca anexado a Release), para pegar regressão de empacotamento continuamente em vez de na hora da tag |

### B-9 — Contradições internas a resolver antes do Bloco A

Os dois documentos de design divergem entre si e travariam o Bloco B contra dois contratos
incompatíveis:

- **Clock:** um põe o Protocol em `app/models/clock.py` com `monotonic_ns()/sleep()/wall_unix_us()` e
  as implementações em `app/core/clock.py`; o outro apaga `app/core/clock.py` e usa
  `perf_ns()/wall_us()/sleep_until(ns)`. `sleep(segundos)` e `sleep_until(ns)` têm semânticas
  diferentes, e o `per-file-ignores` do ruff apontaria para arquivo inexistente.
  **Resolver:** Protocol **e** implementações em `app/models/clock.py` (é `time` puro, não importa nada
  do projeto, e A6 faz de `models` a única camada que adaptadores e core enxergam); métodos
  `perf_ns()/wall_us()/sleep_until(ns)`; `assert_clock_resolution()` junto.
- **`FieldValue`:** `is_estimated`/`is_extension` num, `is_derived` no outro. Ficar com
  `FieldValue(value, age_ms, is_stale, is_derived, is_extension)` — **os dois** flags, porque a
  armadilha 9 (campo de extensão chegando zerado em link v1) precisa do segundo.
- **Numeração de ADR colidindo:** 0015 é "mapa" num e "fixtures sintéticas" no outro. Congelar uma
  tabela só: 0012 windows-first · 0013 clock · 0014 chave primária + tabela por msgid · 0015 fixtures
  sintéticas · 0016 endpoint/`file:` · 0017 mapa · 0018 mock emite frames · 0019 eixo do replay ·
  0020 port do reader · 0021 alertas.
- **Layout de testes:** achatado num, espelhado (`tests/models/`, `tests/adapters/`) no outro. Adotar
  o espelhado.

### B-10 — Lacunas menores, todas agendáveis

`CONTRIBUTING.md` com a regra §20 de dado-só-de-SITL não está em bloco nenhum — e a torná-la mecânica
com `test_no_real_coordinates.py` (toda lat/lon commitada a menos de 0,5° de uma das duas origens de
simulador) converte uma frase que ninguém aplica num build vermelho · declaração de privacidade do
README (onde o banco vive, que **não é cifrado em repouso**) ausente do checklist do Bloco E · o gate
de cobertura fica em `fail_under = 0` e **nenhum checklist o levanta**, e `app/database` — o código de
maior risco — está fora da lista do Bloco D · filtro de `compid` escrito e nunca exercitado (nenhuma
fixture tem segundo componente) · teste de fuzz não agendado, e `malformed_frames.bin` é produzido e
nunca consumido · helper `replay_tlog(path, dialect)` de §17.1 não criado, então a armadilha 28 volta
pela porta que §17.1 tentou fechar · leituras de banco sem thread atribuída, violando P6 no ponto em
que o plano se gaba de respeitá-lo · rotação do `RotatingFileHandler` renomeia arquivo aberto — C8 /
armadilha 33 no único arquivo aberto em toda execução, nunca testado · views de família precisam ser
`UNION ALL` com discriminador `src_msgid`, **nunca JOIN**, que fabricaria observação conjunta a partir
de duas medições com timestamps independentes · `next_emit += PERIOD_NS` sem clamp emite rajada de
recuperação após qualquer stall, derrotando o coalescing exatamente quando ele importa
(`next_emit = max(next_emit + PERIOD_NS, now)`) · `libglib2.0-0t64` é nome de pacote do Ubuntu 24.04 e
não existe no `ubuntu-22.04` a que o `release.yml` está fixado.

---

## 10. Artefatos de verificação

As correções C-1 a C-12 vêm de seis agentes de pesquisa independentes contra documentação primária
(CPython, SQLite, Qt, PyPI, MAVLink, ArduPilot). Os retornos completos — incluindo a evidência citada
verbatim de cada achado e dois documentos de design detalhados (Etapa 0 + Bloco A; Blocos B-E), com
código concreto para `paths.py`, `clock.py`, o bootstrap de logging, o esqueleto worker/`moveToThread`
e o DDL — estão em:

```
.claude/projects/…/subagents/workflows/wf_66a52451-dbf/journal.jsonl
```

Entradas 1-4 são os verificadores (objetos estruturados); 5 e 6 são os designs (markdown, ~73 KB cada);
7 e 8 são as críticas (10 bloqueadores, 29 major, 22 minor). Consultar na implementação de cada bloco,
em vez de reconstituir a evidência.

**Cobertura da verificação:** oito agentes, todos concluídos. Quatro verificaram premissas de stack
contra documentação primária; dois desenharam Etapa 0 + Blocos A-E; dois atacaram o resultado — um por
completude (P1-P11, as 35 armadilhas, as NFRs de §12, C1-C12, §17, §19, §20, ADRs), outro por defeito
presente (ordenação, o eixo `t_boot_ms`/`t_recv_ns`, `WITHOUT ROWID`, backpressure, custo real de D1,
vazamento de escopo, e o caminho de 30 segundos do avaliador).

**Ainda não verificado:** as próprias correções da seção 9 não passaram por uma terceira rodada. As de
maior risco de estarem erradas são B-3 (as duas colunas de fix carimbadas resolvem a armadilha 25 sem
introduzir outra?) e B-1 (o worker segurando a fila diretamente respeita A6 via port, ou o port é
cerimônia sobre uma violação?). Ambas são decisões de Bloco B/C — revalidar quando lá chegar.

---

## 11. Errata a propagar para o documento canônico

**Status: aplicada ao documento canônico na v1.2.** A tabela fica como registro do que mudou e por quê
— cada linha é uma afirmação que o canônico fazia e que a verificação derrubou. Consultar quando uma
decisão de implementação parecer contradizer o que alguém lembra de ter lido.

| Seção do canônico | O que está errado | Correção |
|---|---|---|
| §7.1, fonte de `t_recv_mono_ns` | `time.monotonic_ns()` | `time.perf_counter_ns()`; renomear para `t_recv_ns` (C-1) |
| §7.1, "regras invioláveis", item 3 | *"O replay roda sobre `t_boot_ms`"* | O cursor indexa em `t_recv_ns`; o ritmo e o rótulo são em tempo de veículo, por escala de sessão. **§11.1 e §7.5 como escritos são mutuamente inimplementáveis** — é defeito do canônico, não desvio do plano (B-8) |
| §7.2 | default do `source_component` é 1; sysid 255 atribuído ao QGC | É 0 (`MAV_COMP_ID_ALL`); suavizar a atribuição ao QGC (C-12) |
| §7.3 | `eph`/`epv` tratados como HDOP | Unidade é `1E-2` — campo vira `eph_cm` (C-12) |
| §7.4 | tabela de sentinelas incompleta; regra de soma de `voltages[]` incompleta | +~9 linhas; remoção **por campo**, nunca varredura global (C-12) |
| §7.5 | `q0..q3`; oito famílias como tabelas | `q_w,q_x,q_y,q_z`; 13 tabelas por msgid, famílias viram views `UNION ALL` (C-2, C-6g, B-10) |
| §8.1 | `.tlog` usa `t_unix_us` de `SYSTEM_TIME` | `int(time.time()*1e6) & ~3`, modo `'wb'` (C-6a) |
| §8.2 | falta `foreign_keys` e `busy_timeout` | Fábrica `connect()` única (C-6b) |
| §8.4 | PK `(session_id, system_id, t_boot_ms)` | PK sobre `t_recv_ns`; `boot_epoch`; `STRICT` (C-2) |
| §8.5 | justificativa por versão do SQLite | A razão é capacidade de `DROP COLUMN`, não versão (C-6f) |
| §8.6 | `AppDataLocation` | `StandardLocation.AppLocalDataLocation`, escopado (C-5) |
| §9.2 | pymavlink "puro-Python, sem binário nativo" | `fastcrc` e `lxml` são nativas; §19 não pode reivindicar MIT para o pacote (C-12) |
| §9.4 | teste de aceite por chegada | Por **taxa**; assimetria é Copter × resto (C-9) |
| §9.6 | contadores dirigidos por exceção | Por `BAD_DATA` / `MAVLink_unknown` (C-10) |
| §10 | diagrama com `recv_match` bloqueante + `QTimer` | Coalescing por deadline dentro do laço (C-3) |
| §13 | `PySide6-Essentials` cobre o mapa; ordem de sondagem do PyQtGraph | QtLocation está no `-Addons`; ordem é `[PyQt6, PySide6, PyQt5, PySide2]` (C-6i, C-6k) |
| §15 | `file:./voo.tlog`; falta a porta 5762; exige `SO_REUSEADDR` | Caminho nu; `5762 = MAVLink #1`; pymavlink já faz `SO_REUSEADDR` (C-8, C-12) |
| §17.1 | fixtures gravadas de SITL | Geração sintética; helper `replay_tlog` com dialeto explícito (C-7, B-10) |
| §17.2 | razão dada para `qtbot.addWidget` | Ele guarda weakref; a razão real é o teardown ordenado (C-6j) |
| §18.1 | `faulthandler.enable(file=open(...))`; "antes de importar Qt" | Vazamento de fd; `qInstallMessageHandler` *é* Qt; falta a 4ª defesa para worker de `QThread` (C-4) |
| §21, armadilha 4 | *"replay sobre `t_boot_ms`, nunca tempo de parede"* | Ritmo escalado por sessão; nunca tempo de parede não escalado (B-8) |
| §21, armadilha 25 | considerada desarmada | Volta pela divisão por msgid — `GLOBAL_POSITION_INT` não tem `fix_type` (B-3) |

---

## Histórico

| Versão | Mudança |
|---|---|
| 1.0 | Plano inicial. Registra D1 (Windows-first até a v0.3, contrato de portabilidade invertido no CI) e D2 (Python 3.12 via uv). Traduz §16 Etapa 0 e v0.1.0 em cinco blocos com critério de pronto. |
| 2.0 | Incorpora verificação independente de oito agentes contra documentação primária. Acrescenta D3 (MIT) e D4 (GitHub público). Doze correções obrigatórias à especificação (C-1 a C-12), das quais quatro são bloqueadoras: resolução do relógio no Windows, schema que não compila, ausência de fixture redistribuível, e forma de endpoint inválida. Acrescenta a seção 9 com dez achados de crítica adversarial (B-1 a B-10) — incluindo B-3, em que a própria correção C-2 reintroduz a armadilha 25. Risco de prazo reatribuído do Bloco C para o B, que é rebaselinado para 5 dias. Critério de desistência da v0.1 definido. Seção 11 de errata para propagação ao documento canônico. |
| 2.2 | **Errata aplicada** ao documento canônico (v1.2): as 22 correções da §11 estão escritas em `sortie.md`, que deixa de conter afirmações que a verificação derrubou. Backpressure decidida — A5 passa a descartar o **mais novo**, com `queue_dropped_total` persistido, tabela `writer_drop`, alerta `SAMPLES_DROPPED`, e relatório de comparação marcado DEGRADED quando houve descarte. Quatro decisões menores fechadas: contrato do `Clock` em `app/models/clock.py`, migração §8.5 com schema v1 real, `RC_CHANNELS` adiado para v0.2, `arm_event` populado do flanco do bit armado e de `STATUSTEXT`. |
| 2.1 | Renomeação para **Sortie** (ADR 0016). Esquema de nomes fixado: repositório `sortie/`, executável `sortie`, diretório de dados `Sortie/`, logs `sortie.log` / `sortie.crash`, documento canônico `docs/sortie.md`. Colisões verificadas: PyPI `sortie` e usuário GitHub `sortie` estão ocupados, nenhum dos dois no caminho do projeto — a distribuição é ZIP de Release (§18) e o pacote interno é `app/`. |
