# Sortie

**Documento de projeto — versão 1.2**
Referência canônica de arquitetura, escopo e execução.

> *Sortie* (fr., aviação): uma saída — uma execução de missão, do início ao fim. É a unidade que este
> software manipula. Ver §2.4.

---

## 0. Como usar este documento

Este é o documento único do projeto. Ele serve a três leituras diferentes:

| Se você quer… | Leia |
|---|---|
| Entender o que é e por que existe | §1, §2, §3 |
| Implementar uma parte | §5 (princípios) → §6-§11 (o quê) → §21 (o que dá errado) |
| Planejar a próxima semana | §16 (roadmap com critério de pronto) |
| Decidir algo que o documento não cobre | §5 (princípios) e depois escreva um ADR (§22) |

**Regra de manutenção:** toda decisão de projeto que não seja derivável dos princípios de §5 vira um
ADR em `docs/adr/`, escrito **no momento da decisão**. Este documento descreve o alvo; os ADRs
registram por que o alvo é esse.

Termos de domínio estão no glossário (§4).

---

## 1. Resumo executivo

Sortie é uma ferramenta de bancada para **análise comparativa de execuções de
missão** em veículos autônomos.

Ele ingere telemetria — ao vivo por MAVLink, ou a partir de logs gravados — normaliza tudo num modelo
interno com eixo temporal explícito, e permite sobrepor, comparar e avaliar N execuções do mesmo plano
de voo contra uma baseline, emitindo um veredito reprodutível.

Não é uma estação de controle de solo. É o instrumento que se usa **depois** e **ao redor** dela.

**Público:** engenheiro de teste de voo, pesquisador de robótica, desenvolvedor de firmware que
precisa saber se a mudança de ontem melhorou ou piorou o rastreamento de trajetória.

**Stack:** Python 3.11+, PySide6, pymavlink, SQLite, PyQtGraph. Desktop, offline-first.

**Plataformas:** desenvolvimento **Linux-first**. Windows é alvo de portabilidade suportado desde o
primeiro dia por contrato verificado no CI (§13.1) — não é um porte futuro. macOS é possível mas não
está na matriz de testes.

---

## 2. Posicionamento

### 2.1 O que já existe e é maduro

| Capacidade | Ferramenta consolidada |
|---|---|
| Telemetria ao vivo, mapa, comando, planejamento de missão | QGroundControl, Mission Planner |
| Inspeção de qualquer campo MAVLink em tempo real | QGC MAVLink Inspector |
| Replay temporal de `.tlog` | QGC Log Viewer |
| Análise post-mortem de log único | PX4 Flight Review, UAV Log Viewer |
| Plot genérico de séries temporais | PlotJuggler |
| Console, mapa e módulos em linha de comando | MAVProxy |

Toda capacidade desta tabela é **requisito de paridade**, nunca argumento de venda. Onde o Mission
Control Hub as implementa, implementa porque precisa delas.

### 2.2 A lacuna que este software ocupa

Nenhuma das ferramentas acima responde a estas perguntas:

1. *"Esta execução da missão foi melhor ou pior que a da semana passada?"*
   Nenhuma GCS faz **diff entre execuções**. É a operação que todo engenheiro de teste de voo faz
   manualmente, exportando CSV.
2. *"A mudança que fiz no firmware ou no parâmetro degradou o rastreamento de trajetória?"*
   Não existe ferramenta consolidada de **regression testing de voo** com veredito pass/fail contra
   baseline.
3. *"O que o stack de percepção estava vendo no instante em que o EKF divergiu?"*
   Não há **correlação temporal entre fontes heterogêneas** (MAVLink + ROS 2 + ULog) com tratamento
   explícito de offset de clock.

O eixo primário do produto é (1)+(2). O item (3) é a extensão natural, tratada na v0.6.

### 2.3 Frase de posicionamento

> Não compete com o QGroundControl. Constrói o que falta ao redor dele: um banco de ensaio que
> transforma execuções de missão em dados comparáveis e em veredito reprodutível.

### 2.4 O nome

Uma *sortie* é uma execução de missão — uma saída, do armar ao desarmar. É exatamente a unidade que
este software trata como objeto de primeira classe: algo que se grava, se indexa, se reabre e se
compara contra outra. Nenhuma das ferramentas de §2.1 faz isso; todas tratam o voo como uma sessão a
observar, não como um registro a confrontar.

O nome promete uma coisa só, e é a de §2.2: **a sortie é comparável.** Se um dia o produto deixar de
comparar execuções e virar visualizador de telemetria, o nome deixa de valer — esse, e não outro, é o
gatilho de renomeação.

**O que o nome deliberadamente não promete é controle.** "Mission Control" é o termo consagrado para
estação de controle de solo, que é precisamente o que §1, §2.1 e §2.3 gastam três seções negando. Um
nome que anuncia a categoria errada obriga o documento a desfazer na primeira página o que a capa
fez.

*Consequência de escopo:* o protocolo de transferência de missão (§9.5) continua sendo requisito de
primeira classe — comparar N execuções **da mesma missão** exige conhecer o plano de voo, e o
`mission_plan_hash` é o que agrupa execuções comparáveis (§7.6). Mas ele agora se sustenta pelo mérito
próprio, em §11.2, e não como dívida contraída pelo nome. A versão anterior deste documento amarrava
os dois; a amarração era artefato do nome antigo e foi desfeita com ele.

---

## 3. Escopo

### 3.1 Dentro do escopo

- Ingestão de telemetria ao vivo via MAVLink (ArduPilot e PX4, SITL ou hardware real).
- Ingestão de logs gravados: `.tlog`, `.bin` (ArduPilot DataFlash), `.ulg` (PX4 ULog).
- Gravação lossless do stream MAVLink cru, em paralelo à persistência estruturada.
- Download e exibição do plano de missão, com progresso de waypoint.
- Replay temporal com seek, velocidade variável, pause e scrub.
- Comparação sobreposta de N execuções da mesma missão, com métricas de delta e veredito.
- Regras de alerta avaliadas na ingestão e reavaliáveis sobre o dado cru.
- Exportação em CSV, GPX/KML e `.tlog`.
- Mapa com trilha histórica e orientação do veículo.

### 3.2 Comando e controle — decisão declarada

O software **envia comandos**, num conjunto mínimo e deliberado:

| Comando | Quando | Obrigatoriedade |
|---|---|---|
| `MAV_CMD_SET_MESSAGE_INTERVAL` (511) | handshake de conexão | **obrigatório** — sem ele o ArduPilot não envia telemetria (§9.4) |
| `MISSION_REQUEST_LIST` / `MISSION_REQUEST_INT` | ao conectar, para baixar o plano | obrigatório a partir da v0.4 |
| `MAV_CMD_COMPONENT_ARM_DISARM` (400) | ação do operador | opcional, v0.4 |
| `MAV_CMD_DO_SET_MODE` (176) | ação do operador | opcional, v0.4 |
| `MAV_CMD_NAV_RETURN_TO_LAUNCH` (20) | ação do operador | opcional, v0.4 |

Os três últimos exigem **três guardas simultâneas**:

1. diálogo de confirmação explícito;
2. UI de comando habilitada somente quando o endpoint é reconhecido como SITL ou loopback;
3. log de auditoria persistido de todo comando enviado, com o `COMMAND_ACK` recebido.

Todo comando trata `MAV_RESULT` integralmente, em especial `TEMPORARILY_REJECTED` (exige retry com
backoff) e `IN_PROGRESS` (exige consumir acks parciais até a conclusão).

### 3.3 Fora do escopo, e por quê

| Item | Decisão | Razão |
|---|---|---|
| **Vídeo / stream de câmera** | Não implementar | GStreamer e QtMultimedia carregam plugins de mídia dinamicamente em runtime; a análise estática do PyInstaller não os detecta, exigindo hooks manuais com resultado diferente por plataforma. O custo cai inteiro sobre o empacotamento, e o retorno em informação de missão é baixo comparado a mapa e progresso de waypoint. |
| **Planejamento e upload de missão** | Só download | O produto analisa execuções, não as projeta. Upload duplicaria o QGC sem ganho. |
| **Calibração de sensores** | Não implementar | Domínio do QGC/Mission Planner; exige protocolo completo e é operação de risco sobre hardware. |
| **Escrita de parâmetros** | Leitura apenas | Escrita é destrutiva sobre veículo; leitura basta para contextualizar uma execução. |
| **Firmware flashing** | Não implementar | Fora do eixo de análise. |

Esta tabela é parte do produto, não anexo. Um item omitido é uma dúvida; um item descartado com razão
técnica é uma credencial.

---

## 4. Glossário

| Termo | Significado |
|---|---|
| **MAVLink** | Protocolo de mensagens binárias para veículos não tripulados. Duas versões em uso: v1 e v2. |
| **Dialeto** | Conjunto de definições de mensagem. `common` é a base; `ardupilotmega` é um superset com mensagens específicas do ArduPilot. |
| **SITL** | *Software In The Loop* — o firmware do autopiloto rodando como processo no PC, sem hardware. |
| **HITL** | *Hardware In The Loop* — firmware rodando no hardware real, com sensores simulados. |
| **Lockstep** | Modo de simulação do PX4 em que o relógio do firmware é conduzido pelo simulador, não pelo relógio de parede. |
| **EKF** | *Extended Kalman Filter* — o estimador de estado do autopiloto. Sua divergência precede quase todo failsafe de posição. |
| **Failsafe** | Comportamento automático de segurança disparado por perda de link, bateria baixa, perda de GPS etc. |
| **`.tlog`** | Telemetry log: sequência de frames MAVLink crus, cada um prefixado por `uint64` big-endian de microssegundos unix. |
| **DataFlash / `.bin`** | Formato de log nativo do ArduPilot, gravado a bordo. |
| **ULog / `.ulg`** | Formato de log nativo do PX4. |
| **GCS** | *Ground Control Station* — QGroundControl, Mission Planner, MAVProxy. |
| **sysid / compid** | `system_id` e `component_id`: o endereçamento de todo frame MAVLink. |
| **Waypoint / leg** | Ponto do plano de missão; *leg* é o trecho entre dois waypoints consecutivos. |
| **xtrack error** | Erro de trilha — distância lateral entre a posição real e a linha entre waypoints. |
| **Baseline** | Execução de referência contra a qual as demais são comparadas. |

---

## 5. Princípios de projeto

Dez regras das quais tudo o mais deriva. Quando este documento não cobrir uma decisão, derive daqui.

**P1 — Nunca gravar um valor estimado com a aparência de valor medido.**
Se o dado não chegou, o campo é `NULL`. Se foi derivado, carrega um flag dizendo isso. Numa ferramenta
de análise de falha, dado extrapolado indistinguível de dado medido é o defeito mais grave possível.

**P2 — A unidade de armazenamento é a mensagem; o snapshot é uma view derivada.**
Mensagens MAVLink chegam em taxas independentes e nunca alinhadas. Achatar isso na gravação força
interpolar, carregar valor antigo ou gravar buracos — os três violam P1.

**P3 — Toda grandeza carrega unidade e referencial no nome do campo.**
`alt_amsl_m`, não `altitude`. A conversão de unidade acontece no adaptador, nunca na UI.

**P4 — Toda sentinela de protocolo vira `NULL` na fronteira do adaptador.**
`-1`, `65535`, `255` e `UINT16_MAX` nunca entram no banco como número. `NULL` significa "não
disponível nesta plataforma", semanticamente distinto de `0`.

**P5 — Tempo é plural.** O relógio do veículo, o relógio do host e o tempo absoluto são três
grandezas diferentes que divergem por construção em simulação. Guardar os três; o replay roda sobre o
do veículo.

**P6 — Nenhuma I/O na thread da GUI, nenhum objeto Qt cruzando fronteira de thread.**
A fronteira é sempre `Signal` com `Qt.QueuedConnection`, carregando dataclasses imutáveis.

**P7 — Toda fonte de dados é um adaptador, inclusive o replay e os arquivos.**
A UI não sabe se está ao vivo. Isso colapsa caminhos de código duplicados e torna o software testável
e demonstrável sem infraestrutura externa.

**P8 — Nada demonstrável pode depender de infraestrutura externa.**
Nenhuma release, nenhum teste de CI e nenhum GIF de demonstração pode exigir SITL, Gazebo ou ROS 2
instalado. O caminho offline sempre existe.

**P9 — Falhar alto, nunca em silêncio.**
Socket aberto sem heartbeat é erro, não "conectado". Frame corrompido incrementa contador visível.
Thread que morre derruba o estado de conexão. Congelar mostrando o último valor válido é o pior modo
de falha de uma ferramenta de monitoramento.

**P10 — Empacotar e publicar desde a semana 1.**
Empacotamento não é etapa final: é atividade contínua. Descobrir problemas de bundling na semana em
que se queria publicar é o modo mais comum de o projeto não ser publicado.

**P11 — Portabilidade é contrato verificado, não porte futuro.**
Desenvolve-se em Linux, mas o CI roda em Windows desde o primeiro commit. Um porte planejado para
depois é um porte que custa semanas; um contrato verificado a cada push é um não-evento. As regras do
contrato estão em §13.1.

---

## 6. Arquitetura

### 6.1 Diagrama

Gazebo **não emite MAVLink**. No fluxo PX4 o autopiloto conversa com o simulador por TCP na porta local
4560; no fluxo ArduPilot o plugin `ardupilot_gazebo` troca structs binárias de FDM por UDP em
9002/9003. Gazebo fica *atrás* do autopiloto, nunca ao lado dele.

```text
  FONTES                          ADAPTADORES            NÚCLEO              APRESENTAÇÃO

  ┌──────────┐  FDM UDP 9002/3
  │  Gazebo  │──────────────┐
  └──────────┘  gz-transport│
                            ▼
                     ┌─────────────┐   MAVLink    ┌──────────────┐
                     │  ArduPilot  │─────────────▶│ MavlinkSource│
                     │   ou PX4    │◀─────────────│  + profile   │
                     └─────────────┘ cmd/streams  │ {apm | px4}  │
  ┌──────────┐  TCP 4560   ▲                      └──────┬───────┘
  │  Gazebo  │─────────────┘                             │
  └──────────┘                                           │ TelemetrySnapshot
                                                         │ (Signal, QueuedConnection)
  ┌────────────────────────┐                             │
  │ .tlog / .bin / .ulg    │──▶ FileReplaySource ────────┤
  │ sessão SQLite gravada  │──▶ SqliteReplaySource ──────┤
  └────────────────────────┘                             │
                                                         ▼
  ┌────────────────────────┐                      ┌─────────────┐
  │  MockSource            │─────────────────────▶│   NÚCLEO    │
  │  (+ injeção de falhas) │                      │ ─────────── │
  └────────────────────────┘                      │ StateStore  │──▶ ┌──────────┐
                                                  │ AlertEngine │    │    UI    │
  ┌────────────────────────┐  UDP / ZeroMQ        │ EventBus    │    │  PySide6 │
  │  ros2_bridge           │─────────────────────▶│ Comparator  │    └──────────┘
  │  (processo separado)   │                      └──────┬──────┘
  └────────────────────────┘                             │
                                                         ▼
                                                  ┌─────────────┐
                                                  │   SQLite    │ writer dedicado
                                                  │  + .tlog cru│ WAL, lote 1 s
                                                  └─────────────┘
```

A seta **autopiloto ↔ adaptador é bidirecional**. Mesmo num software de análise isso é obrigatório: o
ArduPilot não envia telemetria para quem apenas escuta (§9.4).

### 6.2 Decisões arquiteturais

**A1 — Replay é um adaptador.**
`FileReplaySource` e `SqliteReplaySource` implementam a mesma interface `TelemetrySource` do adaptador
ao vivo e emitem os mesmos `TelemetrySnapshot` pela mesma fronteira. Deriva de P7.

**A2 — Um adaptador MAVLink, dois perfis de firmware.**
`adapters/mavlink/` contém toda a máquina de conexão e parsing. `profiles/ardupilot.py` e
`profiles/px4.py` contêm apenas as cinco divergências reais (§9.5). O perfil é selecionado **em
runtime** pelo par `(HEARTBEAT.autopilot, HEARTBEAT.type)` — a URL de conexão não carrega essa
informação.

**A3 — Toda I/O fora da thread da GUI.** `QThread` dedicada por adaptador. Deriva de P6. Ver §10.

**A4 — Motor de alertas na ingestão, reavaliável no replay.**
As regras rodam no pipeline de ingestão (para funcionar ao vivo) e podem ser reexecutadas sobre o dado
cru de uma sessão antiga (para que mudanças de regra se apliquem retroativamente). Cada `alert_event`
grava `rule_version`.

**A5 — Backpressure explícita.**
A fila entre adaptador e writer é limitada. Ao encher, a política é **descartar o mais novo e
incrementar um contador** — nunca bloquear a leitura do socket, o que estouraria o buffer UDP e
perderia pacotes silenciosamente (violaria P9).

*Por que o mais novo, e não o mais antigo (revisão v1.2):* o consumidor desta fila é o writer de
arquivo, não a UI. Descartar o mais novo é igualmente não-bloqueante e preserva o dado **anterior**,
que numa ferramenta de análise de falha é tipicamente o pré-falha — a parte que importa. A v1.1 dizia
"mais antigo", herdado da intuição de fila que alimenta UI ao vivo, onde o recente é o relevante. Aqui
não é.

**O descarte tem de sobreviver ao processo.** Um contador que só existe na UI faz uma sessão gravada
com 20 % das amostras descartadas ficar **byte-indistinguível** de uma completa ao reabrir — e o
veredito de §11.2 seria calculado sobre subamostra silenciosamente decimada e emitido como
"reprodutível". Portanto:

- `queue_dropped_total` em `mission_session`, gravado no fechamento;
- tabela `writer_drop(session_id, t_recv_ns_start, t_recv_ns_end, dropped_count)`, para o replay
  desenhar a faixa como *"dado perdido por backpressure"* — distinta de um gap de link, que tem outra
  causa e não pode ser renderizado igual;
- alerta `SAMPLES_DROPPED` no primeiro descarte (P9);
- **relatório de comparação sobre sessão com `queue_dropped_total > 0` é marcado DEGRADED e recusa
  veredito PASS.**

**A6 — Regra de dependência entre pacotes.**

```
models        → não importa nada do projeto
adapters      → models
database      → models
core/services → models, database, adapters
ui            → tudo; ninguém importa ui
```

Verificada no CI com `import-linter`. Sem a regra escrita e testada, seis diretórios irmãos degeneram
em import circular no primeiro mês.

---

## 7. Modelo de dados

### 7.1 Tempo — três relógios

`time_boot_ms` é `uint32` em ms desde o boot do autopiloto: não é relógio de parede, zera a cada reboot
e faz wrap em 2³² ms ≈ 49,7 dias. E o tempo do veículo **não é** o tempo do PC: ArduPilot SITL roda com
`--speedup` (10x e 100x são comuns em teste automatizado) e PX4 SITL usa lockstep, onde o relógio do
firmware é conduzido pelo simulador e para se o simulador travar.

| Campo | Tipo | Origem | Papel |
|---|---|---|---|
| `t_boot_ms` | INTEGER, nullable | valor cru da mensagem | eixo de **correlação** e de alinhamento entre execuções — imune a speedup |
| `boot_epoch` | INTEGER NOT NULL | contador de descontinuidade | incrementado quando `t_boot_ms` recua (wrap de 2³² **ou** reboot) |
| `t_recv_ns` | INTEGER NOT NULL | `time.perf_counter_ns()` | **chave primária e eixo do cursor de replay**; ordenação, detecção de gap, latência |
| `t_unix_us` | INTEGER, nullable | `SYSTEM_TIME` (#2) | correlação entre sessões e com logs externos |

Regras invioláveis:

- Wrap de `t_boot_ms` detectado acumulando `boot_epoch` quando o valor recuar. Toda chave ou índice
  sobre tempo de boot é `(session_id, system_id, boot_epoch, t_boot_ms)`, nunca `t_boot_ms` sozinho.
- **Nunca** usar `time.time()` para medir intervalo. Única exceção: o carimbo absoluto do `.tlog`
  (§8.1), que é exigido por formato externo e não é intervalo.
- **Nunca** usar `time.monotonic_ns()`. No Windows/Python 3.12 ele é `GetTickCount64`, com resolução
  de **15,625 ms** — o CPython só trocou para `QueryPerformanceCounter` no 3.13 (gh-88494). A 15,6 ms
  a NFR de latência de §12 é medida com ±16 % de erro de quantização e o intervalo de coalescing tem
  3 a 6 ticks de largura. Usar `time.perf_counter_ns()`, que é QPC no Windows e `CLOCK_MONOTONIC` no
  Linux, em toda versão suportada.
- O epoch de `t_recv_ns` é **local ao processo**: aritmética entre sessões, ou através da fronteira do
  bridge (§9.8), é proibida. Só diferenças dentro de uma sessão têm significado.
- **O cursor do replay indexa em `t_recv_ns`** — é o único eixo presente em todas as tabelas de
  amostra. O ritmo e o rótulo de tempo são em tempo de veículo, por escala de sessão. Nunca tempo de
  parede não escalado.
- `perf_counter` conta durante suspensão do host. O cursor precisa de clamp: se `dt > 1 s`,
  reancorar em vez de avançar, e emitir evento visível (P9). Isso também cobre breakpoint de debugger
  e pausa de GC.

> **Nota de correção (v1.2):** até a v1.1 esta seção dizia que o replay rodava sobre `t_boot_ms`. Isso
> era **inimplementável**: §11.1 fixava o cursor em `(session_id, t_boot_ms)` enquanto §7.5 chaveava
> quatro das oito famílias em tempo de recepção, e sob `--speedup` os dois eixos divergem por
> construção. Seis das oito famílias não têm timestamp de boot nenhum. O defeito era da especificação,
> não da implementação. ADR 0013.

### 7.2 Identidade

Um link MAVLink carrega vários componentes: autopiloto (`MAV_COMP_ID_AUTOPILOT1`=1), gimbal (154),
câmera (100), ADSB, e o próprio MAVProxy ou QGC. Todos emitem HEARTBEAT e alguns emitem ATTITUDE.

- `system_id` e `component_id` em toda amostra, e `system_id` na chave da série.
- Filtro default: `get_srcComponent() == 1`. **Sobrescritível pelo usuário** — a especificação MAVLink
  de atribuição de IDs avisa explicitamente que *não se deve inferir o tipo de um componente pelo seu
  ID*, então o default é convenção, não garantia.
- O **próprio app** fixa `source_system` em 245-250 com `source_component` = 190 (`MISSIONPLANNER`).
  O default do pymavlink é `source_system=255` e `source_component=0` (`MAV_COMP_ID_ALL`) — os dois são
  ruins. GCSs convencionalmente usam sysid próximo de 255, então com ambos na rede o autopiloto vê dois
  GCS com a mesma identidade e o failsafe de GCS fica ambíguo; e enviar com compid 0 é desencorajado
  pelo próprio ArduPilot. Escolher 190 para nós não carrega significado de protocolo: é convenção.

### 7.3 Altitude — sempre duas, nomeadas

| Mensagem | Campo | Referencial | Unidade nativa |
|---|---|---|---|
| `GLOBAL_POSITION_INT` (#33) | `alt` | MSL (geoide) | mm |
| `GLOBAL_POSITION_INT` (#33) | `relative_alt` | acima do home | mm |
| `GPS_RAW_INT` (#24) | `alt_ellipsoid` | elipsoide WGS-84 | mm |
| `VFR_HUD` (#74) | `alt` | MSL | m |
| `TERRAIN_REPORT` (#136) | — | acima do terreno | m |

A diferença geoide/elipsoide chega a dezenas de metros no Brasil. Guardamos sempre `alt_amsl_m` **e**
`alt_rel_home_m`. Deriva de P3.

### 7.4 Sentinelas

Deriva de P4. Toda uma vira `NULL` no adaptador:

| Campo | Sentinela | Nota |
|---|---|---|
| `SYS_STATUS.battery_remaining` | -1 | |
| `SYS_STATUS.voltage_battery` | UINT16_MAX | |
| `SYS_STATUS.current_battery` | -1 | |
| `BATTERY_STATUS.voltages[n]` | 65535 | célula não usada |
| `BATTERY_STATUS.voltages[0]` | 65534 | **saturação**: total = 65534 + `voltages[1]` |
| `BATTERY_STATUS.voltages_ext[n]` | **0** | espelho invertido — aqui o zero é a sentinela |
| `BATTERY_STATUS.current_battery` | -1 | |
| `BATTERY_STATUS.temperature` | INT16_MAX | |
| `BATTERY_STATUS.time_remaining` | 0 | e é campo de extensão (ver §9.3) |
| `GPS_RAW_INT.satellites_visible` | 255 | |
| `GPS_RAW_INT.eph` / `epv` | UINT16_MAX | unidade é `1E-2` — o campo é `eph_cm`, não HDOP (P3) |
| `GPS_RAW_INT.vel` | 65535 | **cm/s** — alimenta ground speed |
| `GPS_RAW_INT.cog` | 65535 | cdeg — alimenta curso |
| `GPS_RAW_INT.yaw` | **0** | 0 significa "não disponível"; norte é 36000 |
| `GPS_RAW_INT.alt_ellipsoid` / `h_acc` / `v_acc` | campos de extensão | zerados em link v1 — ver §9.3 |
| `GLOBAL_POSITION_INT.hdg` | 65535 | |
| `VFR_HUD.airspeed` (multirotor) | — | não é medido; ver §7.5 |

**A remoção é tabela por campo, jamais varredura global.** Um `if value == 65535` genérico erra nas duas
direções: deixa passar `voltages_ext == 0` e `yaw == 0`, e mapeia para `NULL` valores legítimos de
campos onde 65535 é medição válida. `GPS_RAW_INT.yaw == 0` tratado como zero medido é precisamente a
armadilha que P1 existe para evitar.

`GPS_RAW_INT.vel` e `.cog` são os mais perigosos da lista: persistir 65535 cm/s como velocidade medida
envenenaria todo veredito de comparação de §11.2 e todo alerta de pico de velocidade.

**Regra de soma de `voltages[]`**, corrigida: pular 65535 em `voltages[]` e 0 em `voltages_ext[]`; se
`voltages[0] == 65534` o total é `65534 + voltages[1]` e **a contagem de células é desconhecida**; uma
única entrada preenchida pode ser o total do pack e não a célula 1. Gravar contagem de células derivada
por contagem de entradas não-sentinela é violação de P1.

> `RC_CHANNELS` (#65) fica **fora da v0.1** — a decisão de armar/desarmar e o alerta `RC_FAILSAFE` são
> derivados dos bits de `SYS_STATUS`, não de #65. Suas sentinelas entram quando a mensagem entrar
> (v0.2). Tabela de sentinela para mensagem que ninguém decodifica é teste que não afirma nada.

### 7.5 Grupos de mensagem persistidos

Deriva de P2: a unidade de gravação é a **mensagem**, cada uma com seu timestamp.

> **Correção estrutural (v1.2).** A v1.1 gravava oito *famílias*, cada uma alimentada por um ou mais
> msgids. Isso colide: `ATTITUDE` (#30) e `ATTITUDE_QUATERNION` (#31) chegando com o mesmo
> `time_boot_ms` são duas linhas com a mesma chave. **Uma tabela por msgid** — treze delas:
> `sample_global_position` (33), `sample_gps_raw` (24), `sample_attitude` (30), `sample_attitude_q`
> (31), `sample_sys_status` (1), `sample_battery_status` (147), `sample_ekf_status` (193),
> `sample_estimator_status` (230), `sample_vfr` (74), `sample_mode` (0), `sample_mission_current` (42),
> `sample_nav_controller` (62), `statustext` (253) — mais `sample_home_position` (242) e
> `sample_system_time` (2), sem os quais `alt_rel_home_m` e `t_unix_us` não têm origem.
>
> As famílias abaixo sobrevivem como **views de leitura**, e cada view é `UNION ALL` com uma coluna
> discriminadora `src_msgid`, **nunca `JOIN`**. Um JOIN fabricaria uma observação conjunta a partir de
> duas medições com timestamps independentes — que é exatamente a violação de P1/P2 que a divisão por
> msgid existe para eliminar, desfeita em silêncio na camada de leitura. ADR 0014.

**`sample_position`** — de `GLOBAL_POSITION_INT` (#33) e `GPS_RAW_INT` (#24)

```
session_id, system_id, t_boot_ms, t_recv_mono_ns, t_unix_us,
lat_deg, lon_deg,
alt_amsl_m, alt_rel_home_m, alt_ellipsoid_m,
vx_ms, vy_ms, vz_ms, hdg_deg,
gps_fix_type, gps_sats, gps_hdop, gps_vdop,
dist_home_m, dist_traveled_m          -- derivadas na ingestão
```

**Regra (P1):** quando `gps_fix_type < 3`, `lat_deg`/`lon_deg` vão como `NULL` — nunca último valor
conhecido, nunca 0.

> **Onde essa regra tem de morar (v1.2).** `GLOBAL_POSITION_INT` (#33) **não tem campo `fix_type`** —
> é a saída de posição do EKF, e o ArduPilot continua publicando, por dead reckoning, depois que o fix
> se perde. Enquanto posição era uma família só, o `fix_type` vindo de #24 estava no escopo da regra.
> Dividir por msgid removeu esse contexto entre mensagens: um CHECK que more em `sample_gps_raw` não
> protege a tabela de onde o mapa, a trilha, a haversine e o `xtrack_error_m` de fato leem.
>
> `sample_global_position` carrega duas colunas **medidas e datadas**, carimbadas pelo adaptador a
> partir do último `GPS_RAW_INT` visto — observações, não estimativas, então P1 se sustenta:
>
> ```sql
> gps_fix_type_at_recv INTEGER NOT NULL,
> gps_fix_age_ns       INTEGER NOT NULL,
> CHECK (gps_fix_type_at_recv >= 3 OR (lat_dege7 IS NULL AND lon_dege7 IS NULL))
> ```
>
> O `NOT NULL` é obrigatório: um CHECK que avalia `NULL` **passa**, então sem ele a guarda vaza.
> Teste de aceite: alimentar fix 3 → 1 → 3 e afirmar que a linha #33 do meio tem `lat`/`lon` `NULL`
> **mesmo com a mensagem de fio carregando coordenada**. Armadilha 25.

Guardar lat/lon/alt na forma inteira nativa do MAVLink (`int32` degE7, `int32` mm) como INTEGER, e não
como REAL de 8 bytes: metade da largura nas colunas mais gordas, estritamente lossless contra o
formato de fio, e mais literalmente P1 do que fazer round-trip de float32 por um double.

`dist_home_m` e `dist_traveled_m` não existem em MAVLink; são calculadas na ingestão (haversine sobre
lat/lon, raio 6371000 m) para não recalcular a cada replay.

**`sample_attitude`** — de `ATTITUDE` (#30) e `ATTITUDE_QUATERNION` (#31)

```
session_id, system_id, t_boot_ms,
roll_rad, pitch_rad, yaw_rad,
rollspeed_rads, pitchspeed_rads, yawspeed_rads,
q_w, q_x, q_y, q_z
```

Os nomes do quaternion **não são** `q0..q3`: o MAVLink #31 manda `q1..q4` com `q1 = w`, e nomes
0-based contra formato de fio 1-based são um off-by-one esperando acontecer. `q_w, q_x, q_y, q_z` é
auto-documentado e torna o mapeamento do adaptador revisável (`q_w=msg.q1, q_x=msg.q2, …`). Deriva de
P3.

`yaw_rad` e `hdg_deg` são grandezas diferentes: com vento lateral, proa e rumo não coincidem. Para
gráfico e replay, aplicar `unwrap` no yaw — senão cada passagem por ±π produz salto de ~360° e a série
fica com riscos verticais. Para interpolação, SLERP sobre quaternion, nunca Euler cru.

**`sample_vfr`** — de `VFR_HUD` (#74)

```
session_id, system_id, t_recv_mono_ns,
ground_speed_ms, air_speed_ms, air_speed_is_estimated,
climb_rate_ms, throttle_pct
```

`VFR_HUD` não tem timestamp próprio. Em multirotor sem sensor de airspeed, `VFR_HUD.airspeed` **não é
medido** — é o groundspeed corrigido pela estimativa de vento. Daí o flag (P1), e daí o painel de
airspeed ficar oculto quando o `MAV_TYPE` não é asa fixa ou VTOL. Em rover e barco o campo é sempre
`NULL`.

**`sample_battery`** — de `SYS_STATUS` (#1) e `BATTERY_STATUS` (#147)

```
session_id, system_id, t_boot_ms, batt_id,
batt_voltage_v, batt_current_a, batt_consumed_mah,
batt_remaining_pct, batt_cell_voltages_mv (json),
batt_temperature_c, batt_time_remaining_s
```

`batt_id` existe porque VTOL e rover comumente têm mais de uma bateria.

**`sample_health`** — de `SYS_STATUS` (#1), `EKF_STATUS_REPORT` (#193) ou `ESTIMATOR_STATUS` (#230)

```
session_id, system_id, t_boot_ms,
sensors_present_mask, sensors_enabled_mask, sensors_health_mask,
drop_rate_comm_cpct, errors_comm,
ekf_vel_var, ekf_pos_horiz_var, ekf_pos_vert_var, ekf_compass_var, ekf_flags
```

Divergência do estimador é o modo de falha mais comum em ArduPilot e PX4, e precede quase todo failsafe
de posição. Sem esses campos o software não consegue explicar por que a missão terminou como terminou —
que é a razão de ele existir.

**`sample_mode`** — de `HEARTBEAT` (#0)

```
session_id, system_id, t_recv_mono_ns,
base_mode, custom_mode, mav_type, autopilot, armed, mode_name
```

Guardar `custom_mode` **cru** além da string resolvida: se a tabela de modos for corrigida depois, o
replay de uma missão antiga pode ser reinterpretado sem regravar nada. `armed` não chega pronto — é o
bit `MAV_MODE_FLAG_SAFETY_ARMED` (0x80) de `HEARTBEAT.base_mode`.

**`sample_mission`** — de `MISSION_CURRENT` (#42) e `NAV_CONTROLLER_OUTPUT` (#62)

```
session_id, system_id, t_recv_mono_ns,
current_wp_seq, wp_dist_m, target_bearing_deg,
nav_roll_deg, nav_pitch_deg, alt_error_m, xtrack_error_m
```

`xtrack_error_m` é a métrica central da comparação entre execuções (§11.2).

**`statustext`** — de `STATUSTEXT` (#253)

```
session_id, system_id, t_recv_mono_ns, severity, text
```

É o log textual do autopiloto, praticamente de graça, e no ArduPilot carrega as mensagens `PreArm:`,
`Arm:` e `Failsafe`. Vale mais para depuração do que metade dos campos numéricos.

### 7.6 Sessão e eventos

**`mission_session`** — o que é constante não se repete a 50 Hz

```
session_id PK, started_at_unix_us, ended_at_unix_us,
source_type, connection_url,
autopilot, mav_type, firmware_version, mavlink_version, dialect,
time_source, boot_to_unix_offset_us, sitl_speedup,
home_lat_deg, home_lon_deg, home_alt_amsl_m,
mission_plan_hash, raw_tlog_path, notes
```

`mission_plan_hash` é o que permite agrupar execuções da mesma missão para comparação.
`home_*` vem de `HOME_POSITION` (#242) e é atualizado quando muda — sem ele, `alt_rel_home_m` não tem
significado.

**`link_event`** — porque estado de conexão não cabe numa tabela de amostras

```
session_id, t_recv_mono_ns, state, reason
```

Quando a conexão cai não chegam mensagens, logo não há amostra para gravar. **A perda de link se
manifesta como ausência de linhas, nunca como uma linha com um valor.** Detecção por watchdog
independente do fluxo de dados, sobre relógio monotônico:

```
DISCONNECTED → CONNECTING → HEALTHY → DEGRADED (1 HB perdido) → LOST (3 HB perdidos)
```

Convenção MAVLink: HEARTBEAT nominal a 1 Hz, sistema considerado perdido após ~3 batimentos ausentes.

**`link_quality`** — amostrado periodicamente

```
session_id, t_recv_mono_ns, drop_rate_comm_cpct, errors_comm,
seq_gaps, rtt_ms, bad_frame_count, crc_error_count, unknown_msgid_count
```

`RADIO_STATUS` (#109) com RSSI só existe com rádio SiK — em SITL sobre UDP essa mensagem nunca aparece.
Qualidade de link em SITL é derivada de gaps de número de sequência MAVLink e de `drop_rate_comm`.

**`arm_event`** — porque a pergunta é *"por que desarmou?"*, não *"estava armado?"*

```
session_id, t_recv_mono_ns, armed, source, mav_result, reason_text
```

Alimentado por `COMMAND_ACK` do `MAV_CMD_COMPONENT_ARM_DISARM` e por `STATUSTEXT`/`EVENT`. Um bool
solitário não distingue crash, failsafe de bateria, failsafe de RC, failsafe de GPS e desarme manual.

**`alert_event`**

```
session_id, t_boot_ms, code, severity, value, threshold, message, rule_version
```

**`mission_plan` / `mission_item`**

```
mission_plan(plan_hash PK, session_id, item_count, downloaded_at_unix_us)
mission_item(plan_hash, seq, command, frame, param1..4, x_deg, y_deg, z_m, autocontinue)
```

### 7.7 O snapshot derivado

`TelemetrySnapshot` é montado em memória para a UI e para o export, nunca gravado. Cada campo carrega o
par `(valor, age_ms)` e é marcado como *stale* acima de um limiar por família:

| Família | Limiar de stale |
|---|---|
| Posição | 500 ms |
| Atitude | 1 s |
| Bateria, sistema, saúde | 3 s |

---

## 8. Persistência

### 8.1 Dois artefatos por sessão

1. **SQLite estruturado** — consultável, indexado, base do replay e da comparação.
2. **`.tlog` cru, gravado em paralelo** — lossless, compatível com Mission Planner e QGC:

```python
usec = int(time.time() * 1e6) & ~3          # wall clock, com os 2 bits baixos zerados (link id)
f.write(struct.pack('>Q', usec) + msg.get_msgbuf())
```

Duas linhas. Sem isso, todo campo não modelado hoje está perdido para sempre nas missões já gravadas, e
cada feature nova exigiria regravar tudo.

Três detalhes que a v1.1 errava e que corrompem o arquivo em silêncio se ignorados:

- O carimbo é `time.time()`, **não** `t_unix_us` de `SYSTEM_TIME` (#2) — que é nullable e não existe
  antes da primeira #2 chegar. É a exceção declarada em §7.1: carimbo absoluto exigido por formato
  externo, não medição de intervalo.
- A máscara `& ~3` é parte do formato.
- Abrir em **`'wb'`**. Modo texto no Windows traduziria bytes `0x0A` dentro do payload binário MAVLink
  e corromperia o log sem erro — é o irmão binário da armadilha 29.

Teste de aceite barato: escrever um `.tlog` pelo mock, reabrir com
`mavutil.mavlink_connection(path, dialect='ardupilotmega')`, e conferir a contagem de mensagens.

### 8.2 Configuração obrigatória

```python
# uma vez, na criação do banco:
PRAGMA journal_mode = WAL          # leitor (UI) concorrente com escritor (telemetria)
PRAGMA user_version = <n>          # versionamento de schema

# em TODA conexão, writer e leitores, via uma única fábrica connect():
PRAGMA synchronous  = NORMAL       # remove o fsync por commit
PRAGMA temp_store   = MEMORY
PRAGMA foreign_keys = ON           # OFF por padrão — sem isto toda FK do schema é decorativa
PRAGMA busy_timeout = 5000         # 0 por padrão — é por isto que o checkpoint TRUNCATE falha na hora
```

Os dois últimos faltavam na v1.1 e são os que de fato mordem. E **o `journal_mode` pode mentir**:
afirmar o retorno, porque §8.6 põe o banco no perfil do usuário, que em máquina corporativa pode ser
um share de rede onde o WAL não é aplicado.

```python
row = conn.execute('PRAGMA journal_mode=WAL').fetchone()
if row[0].lower() != 'wal':
    raise RuntimeError(f'WAL recusado: {row[0]}')      # P9 no único pragma que falha calado
```

O módulo `sqlite3` faz commit implícito por statement. Com `journal_mode=DELETE` e `synchronous=FULL`,
cada commit força `fsync` e a taxa cai para dezenas de commits/s em disco comum.

### 8.3 Writer

- Thread dedicada, **conexão própria** — `sqlite3` usa `check_same_thread=True` por padrão, e reusar a
  conexão da UI vira `ProgrammingError` em produção. **Não** setar `check_same_thread=False` para
  "simplificar": isso troca um `ProgrammingError` alto por uma interleaving silenciosa.
- Alimentada por `queue.Queue` limitada, com a política de descarte de A5.
- Commit em lote a cada **N linhas ou 1 s**, o que vier primeiro, via `executemany`, com
  `isolation_level=None` e **`BEGIN IMMEDIATE` / `COMMIT` explícitos**. Isso torna a fronteira do lote
  uma linha de código visível, funciona idêntico em 3.11-3.14, pega o lock de escrita na entrada (então
  contenção vira espera limpa de `busy_timeout` em vez de rollback no meio), e — o que mais importa —
  **não deixa transação aberta com o writer ocioso**, que em WAL prende um snapshot de leitura e mata o
  checkpoint. Não usar `autocommit=False`: ele abre transação no instante do `connect()`, e
  `journal_mode` não pode mudar dentro de transação.
- **Toda leitura fora da thread da GUI** (P6). Isso inclui o índice do replay e a decimação — carregar
  arrays de tempo inteiros ou rodar SQL por buckets sobre 180 k linhas na thread da GUI é um stall de
  centenas de ms e estoura o orçamento de latência de §12 enquanto roda.

**Shutdown ordenado.** `PRAGMA wal_checkpoint(TRUNCATE)` ao fechar a missão — senão os arquivos
laterais `-wal` e `-shm` quebram a premissa de "um arquivo por missão, fácil de distribuir". Mas com o
app ainda aberto ele **não pode** funcionar, e isso é correto: um só cursor não exaurido segura uma
transação de leitura e bloqueia o TRUNCATE. A ordem é:

1. parar a thread de ingestão;
2. `flush` + `fsync` + `close` explícitos no handle do `.tlog` cru;
3. writer drena a fila, último `executemany` + `COMMIT`;
4. fechar **toda** conexão de leitura — cursores de replay, consultas da UI — e afirmar
   `in_transaction is False`;
5. `row = conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()` e **afirmar `row[0] == 0`**; um
   `execute()` nu aqui é no-op silencioso (P9);
6. `close()` no writer, que sendo a última conexão faz o SQLite apagar `-wal` e `-shm`;
7. só agora renomear, mover ou exportar o `.db`.

Os passos 3-6 rodam **na própria thread do writer** — chamá-los da GUI em `aboutToQuit` levanta
`ProgrammingError` e deixa os arquivos laterais para trás, que é a armadilha 33 exatamente onde ela
mais dói. A fachada da GUI faz `thread.wait(timeout)` e só então executa o passo 7. Envolver 1-6 em
`try/finally`, com fallback `atexit`: processo morto no Windows deixa `-wal`/`-shm`, e a próxima
abertura paga recuperação sob lock exclusivo.

Alternativa mais limpa para o objetivo "um arquivo por missão": `VACUUM INTO 'mission_NNN.db'` —
atômico, arquivo único, sem arquivos laterais e sem quebra-cabeça de ordem.

### 8.4 Índices

Chave primária composta **`(session_id, system_id, t_recv_ns)`** em tabela `WITHOUT ROWID` e `STRICT`,
para não pagar índice secundário a cada insert. O seek do replay vira busca binária na B-tree em vez de
full scan.

`sample_battery_status` acrescenta `batt_id` à chave — VTOL e rover comumente têm mais de uma bateria,
e sem isso duas células chegando no mesmo instante colidem.

> **Nota de correção (v1.2):** até a v1.1 a chave era `(session_id, system_id, t_boot_ms)`. Isso **não
> compila**. O SQLite exige NOT NULL em toda coluna de PK de tabela `WITHOUT ROWID`, e §7.1 declara
> `t_boot_ms` nullable. Pior: **seis das oito famílias `sample_*` não têm timestamp de boot nenhum** —
> `SYS_STATUS`, `BATTERY_STATUS`, `EKF_STATUS_REPORT`, `HEARTBEAT`, `MISSION_CURRENT`,
> `NAV_CONTROLLER_OUTPUT`, `VFR_HUD` e `STATUSTEXT` não carregam campo de tempo. E um reboot do
> autopiloto no meio da sessão faz o timestamp recuar, quebrando qualquer chave temporal. ADR 0014.

`t_recv_ns` é forçado monotônico na ingestão — `stamp(observed) = max(observed ou perf_ns(), último+1)`
— o que torna a chave única por construção. O parâmetro `observed` existe porque na ingestão de `.tlog`
o valor vem do prefixo de 8 bytes do arquivo, cuja fonte é `time.time()`; num log gravado por GCS no
Windows isso carimba muitos frames consecutivos com o mesmo microssegundo, e sem o forçamento o
primeiro `.tlog` real violaria a PK.

`session_id` é **INTEGER** (rowid de `mission_session`), nunca TEXT UUID: 37 B por linha × 180 k
linhas/hora é ~6,7 MB/hora de pura repetição de chave, e empurra as tabelas largas contra o teto de
tamanho de linha do `WITHOUT ROWID`.

### 8.5 Migração

`PRAGMA user_version` com lista ordenada de migrações idempotentes aplicadas na abertura. Recusar abrir
banco de versão **maior** que a do app, com mensagem clara.

Migrações usam apenas `ADD COLUMN` e recriação de tabela. **A razão é capacidade, não versão** — a v1.1
justificava pelos pisos de versão do SQLite (3.25 para `RENAME COLUMN`, 3.35 para `DROP COLUMN`), o que
está errado e seria citado de volta: os builds em uso trazem 3.47+. O motivo real é que o SQLite
**proíbe** dropar coluna que seja PK ou parte dela, UNIQUE, indexada, ou usada em CHECK, FK, coluna
gerada, view ou trigger — que neste schema é praticamente toda coluna que se quereria remover. E
nenhuma variante de `ALTER TABLE` muda uma chave primária, que é justamente a mudança que um desenho
`WITHOUT ROWID` torna mais provável.

Limites de `ADD COLUMN` em vigor: sem PRIMARY KEY, sem UNIQUE, sem default não-constante, e NOT NULL
exige default não nulo. Recriação de tabela segue os 12 passos documentados, sempre **criar-e-renomear**,
nunca renomear-a-antiga-primeiro.

Guarda de runtime em vez de palpite de versão: `assert sqlite3.sqlite_version_info >= (3, 37, 0)` — o
piso de tabelas `STRICT` — e logar `sqlite3.sqlite_version` no boot, com um passo de CI imprimindo-o
nos dois runners para que uma deriva vire build vermelho e não surpresa em runtime.

**Teste obrigatório:** abrir uma fixture de banco da versão anterior, migrar, e afirmar que o replay
funciona.

### 8.6 Onde o banco vive

```python
# app/core/paths.py
if getattr(sys, 'frozen', False):
    loc  = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    if not loc:                                    # P9: string vazia é falha, não default silencioso
        raise RuntimeError('QStandardPaths nao resolveu AppLocalDataLocation')
    base = Path(loc)
else:
    base = Path('./data')
base.mkdir(parents=True, exist_ok=True)
```

Um caminho derivado de `__file__` põe o banco ao lado do executável. Em `C:\Program Files\...` isso é
somente leitura para usuário padrão. Em `--onefile` é pior e silencioso: o bundle é extraído para
`sys._MEIPASS` e **apagado ao sair** — o banco some a cada execução, sem mensagem de erro.

### 8.7 Volume

A 50 Hz agregados: ~180.000 amostras/hora, ~25-40 MB por hora de missão. Retenção configurável e
`VACUUM` manual (exige o dobro do espaço livre em disco). O README documenta MB/hora medidos.

---

## 9. Adaptadores

### 9.1 Interface comum

```python
class TelemetrySource(Protocol):
    snapshot_ready: Signal          # Signal(TelemetrySnapshot)
    state_changed:  Signal          # Signal(LinkState)
    def start(self) -> None: ...
    def stop(self)  -> None: ...
    def request_streams(self) -> None: ...
    def send_command(self, cmd: Command) -> Awaitable[CommandAck]: ...
```

Não é um iterador de snapshots: é um objeto com ciclo de vida e caminho de escrita, porque o caminho de
escrita é obrigatório (§9.4).

Implementações: `MockSource`, `MavlinkSource`, `FileReplaySource`, `SqliteReplaySource`,
`Ros2BridgeSource`.

### 9.2 pymavlink é a base

| | pymavlink | MAVSDK-Python |
|---|---|---|
| Natureza | parser Python **com wheels compiladas** (`lxml` em C, `fastcrc` em Rust) | cliente gRPC de um binário C++ (`mavsdk_server`), localhost:50051 |
| Execução | `recv_match()` bloqueante | async generators (asyncio) |
| Mensagem arbitrária | **sim, todo o dialeto** | **não** — só o que os plugins modelam |
| Suporte ArduPilot | idêntico ao PX4 | parcial por design, adicionado peça a peça |
| Licença | LGPLv3-or-later | BSD-3-Clause |
| PyInstaller | wheels nativas transitivas entram na análise | exige `--add-binary` do `mavsdk_server` |
| MAVLink 2 signing | `mav.setup_signing()` | não documentado / sem API pública |

Com MAVSDK ficariam **inalcançáveis**: `EKF_STATUS_REPORT` (#193), `STATUSTEXT` com severidade,
`NAMED_VALUE_FLOAT` e todo o dialeto `ardupilotmega`. `Telemetry.GpsInfo` tem apenas `num_satellites` e
`fix_type`, sem HDOP, e não há equivalente do bitmask `onboard_control_sensors_health`.

**Decisão registrada em `docs/adr/0001-mavlink-client.md`.** MAVSDK pode entrar depois como adaptador
adicional **declarado PX4-only**, nunca como alternativa transparente.

### 9.3 MAVLink 2, dialeto e signing

Fixar MAVLink 2 explicitamente e usar `dialect='ardupilotmega'` (superset de `common`). Registrar a
versão negociada no log de conexão e na UI.

O modo de falha em v1 é **silencioso**: extensões de `BATTERY_STATUS` (`time_remaining`, `charge_state`)
e de `GPS_RAW_INT` (`alt_ellipsoid`, `h_acc`, `v_acc`) chegam **zeradas, não como erro**. Guardar flag
de quais campos vieram de extensão, para não confundir zero com ausente (P1).

Todo msgid acima de 255 é inacessível em v1, o que excluiria `CURRENT_MODE` (#436) e `AVAILABLE_MODES`
(#435) do Standard Modes Protocol.

Com `dialect='common'`, `EKF_STATUS_REPORT` (#193) chega e vira UNKNOWN silenciosamente — o CRC_EXTRA é
por mensagem e por dialeto, então o dialeto errado **descarta** em vez de falhar alto.

**Signing** (`mav.setup_signing()`): 13 bytes ao final do frame — linkId (1), timestamp (6), e os 6
primeiros bytes de um HMAC-SHA256 com chave compartilhada de 32 bytes. Opcional, ativável no painel de
conexão.

### 9.4 Handshake — obrigatório

O ArduPilot só envia o que foi pedido naquele canal (parâmetros `SRn_RAW_SENS`, `SRn_EXTENDED_STATUS`,
`SRn_POSITION`, `SRn_EXTRA1/2/3`). O PX4 SITL já sai transmitindo um perfil de streams na porta de GCS.

Isso torna o modo de falha **assimétrico**: um adaptador passivo vê HEARTBEAT chegando, conclui que
"conectou", e nunca recebe `GLOBAL_POSITION_INT`. O PX4 parece funcionar de primeira e o ArduPilot
parece "com bug", levando a depuração na direção errada. E se o MAVProxy do `sim_vehicle.py` já
negociou streams a 4 Hz, o comportamento muda conforme você conecta antes ou depois dele — o bug parece
intermitente.

Após o primeiro HEARTBEAT, enviar `MAV_CMD_SET_MESSAGE_INTERVAL` (511, via `COMMAND_LONG`, intervalo em
µs no `param2`) para cada msgid:

| msgid | Mensagem | Taxa alvo |
|---|---|---|
| 0 | `HEARTBEAT` | 1 Hz |
| 1 | `SYS_STATUS` | 2 Hz |
| 30 | `ATTITUDE` | 10 Hz |
| 33 | `GLOBAL_POSITION_INT` | 5 Hz |
| 42 | `MISSION_CURRENT` | 1 Hz |
| 62 | `NAV_CONTROLLER_OUTPUT` | 5 Hz |
| 74 | `VFR_HUD` | 5 Hz |
| 147 | `BATTERY_STATUS` | 1 Hz |
| 193 / 230 | `EKF_STATUS_REPORT` / `ESTIMATOR_STATUS` | 2 Hz |
| 253 | `STATUSTEXT` | evento |

Fallback para `REQUEST_DATA_STREAM` (deprecado) quando o `COMMAND_ACK` vier `MAV_RESULT_UNSUPPORTED` —
firmwares antigos só entendem esse. Reenviar a cada reconexão e a cada N segundos sem dados (o ArduPilot
esquece o pedido se o canal cair).

**Teste de aceite:** após pedir #33 a 5 Hz, medir o **intervalo entre chegadas** numa janela de 5 s e
falhar se a taxa observada não bater com a pedida dentro de tolerância.

> **Correção (v1.2).** A v1.1 mandava *"falhar se nenhuma `GLOBAL_POSITION_INT` chegar em 5 s"*. Isso
> valida a coisa errada, e a assimetria descrita acima **não é ArduPilot × PX4 — é Copter × todo o
> resto**. Em Plane, Rover e Sub o teste passa na taxa default do firmware **mesmo com o handshake
> completamente quebrado**, mascarando exatamente o bug que ele existe para pegar. Chegada não prova
> negociação; taxa prova.

`param2 = -1` desabilita o stream e `0` devolve a taxa default — úteis no caminho de desconexão limpa.

### 9.5 Perfis de firmware — as cinco divergências

| Divergência | ArduPilot | PX4 |
|---|---|---|
| `custom_mode` | inteiro simples cujo significado depende do `MAV_TYPE` — modo 4 = GUIDED em copter, HOLD em rover, ACRO em plane | union `px4_custom_mode`: main_mode nos bits 16-23, sub_mode nos 24-31 |
| Status do EKF | `EKF_STATUS_REPORT` (#193, `ardupilotmega`) | `ESTIMATOR_STATUS` (#230, `common`) |
| Parâmetros | nomes e protocolo próprios | idem, divergentes |
| Stream rate | `SRn_*` + `SET_MESSAGE_INTERVAL` | perfil default + `SET_MESSAGE_INTERVAL` |
| Missão | item 0 do plano é a posição de home | item 0 é um waypoint normal |

Cada perfil é uma **tabela**, não um adaptador. A decodificação de `custom_mode` é indexada por
`(autopilot, MAV_TYPE)` — o mesmo inteiro significa três modos distintos conforme o tipo de veículo.

### 9.6 Robustez do parser

Se uma exceção escapar do loop de leitura, a thread morre em silêncio, a conexão continua marcada como
ativa e **a UI congela nos últimos valores** — violação direta de P9.

> **Correção (v1.2).** A v1.1 dizia que `parse_char` *levanta* `MAVError` ou `struct.error` em bytes
> corrompidos, e derivava os contadores disso. **Sob configuração default quase nada é lançado**: o
> pymavlink devolve um objeto `BAD_DATA` ou `MAVLink_unknown`. Os contadores ficariam permanentemente
> em zero e o teste de fuzz ("nenhuma exceção escapa") passaria **vacuamente** — verde, e provando
> nada.

- Contadores dirigidos por **inspeção**, não por exceção: `bad_frame_count` e `crc_error_count` de
  `msg.get_type() == 'BAD_DATA'` (e seu `.reason`), `unknown_msgid_count` de
  `isinstance(msg, MAVLink_unknown)`. Exibidos no painel de conexão.
- `try/except` por frame **mantido** como cinto e suspensório — `recv_msg` é genuinamente desprotegido.
- **A asserção do fuzz é "nenhuma exceção escapa E os contadores sobem E nenhuma linha de amostra saiu
  de dado ruim"**. Sem as duas últimas o teste não afirma nada.
- `recv_match(blocking=True, timeout=0.5)` — sem timeout, UDP nunca sinaliza desconexão e bloqueia para
  sempre. Não-negociável: há issue documentada de `recv_match` bloquear mesmo com `blocking=False`.
- No Windows, incluir `WinError 10022` no conjunto esperado — aparece em `select`/`recv` sobre socket
  UDP não ligado, que é precisamente o estado "socket aberto, zero HEARTBEAT" que §15 manda reportar
  como erro de conexão.

- Exceção capturada **por frame**, incrementando `bad_frame_count`, `crc_error_count` e
  `unknown_msgid_count`, exibidos no painel de conexão.
- `recv_match(blocking=True, timeout=0.5)` — sem timeout, UDP nunca sinaliza desconexão e bloqueia para
  sempre.
- Bind default em `udpin:127.0.0.1:14550`, editável. MAVLink não autentica e UDP não tem handshake:
  bind em `0.0.0.0` expõe o app a qualquer máquina da LAN, que pode injetar frames que o app parseia,
  persiste e transforma em alerta.
- Filtro por `(sysid, compid)` esperado.
- Teste de fuzz que alimenta bytes aleatórios e truncados e afirma que nenhuma exceção escapa e que os
  contadores sobem.

### 9.7 Mock com injeção de falhas

O mock existe para tornar as funcionalidades desenvolvíveis e testáveis sem infraestrutura (P8). Um mock
que só gera dados normais não permite desenvolver alertas nem exercitar o replay em condições reais.

`MockSource` injeta, por configuração ou por botão na UI:

- perda de heartbeat, parcial e total;
- queda de bateria acelerada, incluindo `battery_remaining = -1`;
- perda de fix de GPS (`fix_type` caindo para 1) e degradação de HDOP;
- divergência do EKF (variâncias subindo);
- picos de velocidade e desvio de trajetória;
- frames malformados.

### 9.8 ROS 2 — bridge fora de processo

`rclpy` **não pode viver no processo do app**:

- Não vem do PyPI: vem de instalação ROS 2 *sourceada*, travada a uma versão minor específica do
  Python. Os binários Windows são compilados contra Python 3.8 (`_rclpy_pybind11.cp38-win_amd64.pyd`),
  enquanto PySide6 6.7+ exige 3.9+ e as versões recentes exigem 3.10+. **Não existe interpretador
  Windows que carregue os dois.**
- Em runtime resolve o middleware por `dlopen` (`rmw_fastrtps_cpp`), carrega typesupport por pacote de
  mensagem também por `dlopen`, e localiza recursos via `AMENT_PREFIX_PATH` e o ament index. Nada disso
  é visível para a análise estática do PyInstaller. A issue `ros2/ros2#1514` registra que distribuir uma
  aplicação ROS 2 standalone não tem caminho suportado.

E "adaptador ROS 2" não é uma coisa só — são três contratos incompatíveis:

| Origem | Tópicos | Amarração |
|---|---|---|
| PX4 via uXRCE-DDS | `/fmu/out/vehicle_local_position`, `/fmu/out/vehicle_status` | `px4_msgs` compilado do branch correspondente à versão exata do firmware |
| ArduPilot via AP_DDS | `/ap/navsat/navsat0`, `/ap/geopose/filtered` | tipos padrão, conjunto menor |
| MAVROS | `/mavros/global_position/global` | terceiro mapa |

**Arquitetura:** `ros2_bridge/` é um pacote ROS 2 separado, com `package.xml` próprio, instalado com
colcon no ambiente ROS. Ele assina os tópicos e republica em JSON/MessagePack por UDP ou ZeroMQ. O app
desktop fala apenas socket e **nunca importa `rclpy`**.

**Propósito:** o bridge não duplica telemetria que o adaptador MAVLink já obtém — mesma origem, mesmas
grandezas, zero informação nova. Ele ingere o que MAVLink **não** carrega: detecções de percepção,
`/tf`, custo de planner, estado de máquina de comportamento — alinhado no mesmo eixo temporal. É a
lacuna §2.2(3).

---

## 10. Concorrência

```
QThread(adaptador)          Thread principal (GUI)        QThread(writer)
─────────────────           ──────────────────────        ───────────────
recv_match(timeout=0.5)
  ↓ parse
  ↓ agrega em snapshot
  ↓ QTimer 10-20 Hz
  └─ Signal ──[QueuedConnection]──▶ StateStore ──▶ widgets
                                         │
                                         └─ Queue(maxsize) ──▶ executemany, lote 1 s
```

- **Coalescing obrigatório:** não emitir um sinal por mensagem MAVLink. `ATTITUDE` a 50 Hz enfileira
  mais rápido que o repaint. Agregar num snapshot e emitir a 10-20 Hz — **por deadline monotônico
  dentro do próprio laço de leitura, não por `QTimer`**:

  ```python
  next_emit = clock.perf_ns() + PERIOD_NS
  while not self._stop:
      msg = conn.recv_match(blocking=True, timeout=0.05)
      if msg: self._accumulate(msg)
      now = clock.perf_ns()
      if now >= next_emit:
          self.snapshot_ready.emit(self._freeze())
          next_emit = max(next_emit + PERIOD_NS, now)   # descartar ticks perdidos, não replayá-los
  ```

  > **Correção (v1.2).** O diagrama acima, lido literalmente, é **inimplementável**: ele põe um
  > `recv_match` bloqueante *e* um `QTimer` na mesma thread. Um `run()` preso em leitura bloqueante
  > nunca retorna ao `exec()`, então o `QTimer` **nunca dispara** — o snapshot é emitido zero vezes e a
  > UI congela no estado inicial, violando P9 sem uma linha de erro. A thread do writer (§8.3) tem a
  > mesma restrição para o seu tick de 1 s.
  >
  > O `max(..., now)` importa: sem ele, qualquer stall — pausa de GC, paint lento, hiccup de disco —
  > produz uma rajada de recuperação de até 15 snapshots seguidos, derrotando o coalescing exatamente
  > nas condições em que ele existe para servir.

- **Worker `QObject` + `moveToThread`, nunca subclasse de `QThread`.** Subclassificar dá afinidade de
  GUI a todo atributo e slot do objeto, então um `source.stop()` chamado da UI executaria na thread da
  GUI enquanto o `run()` está bloqueado — corrida silenciosa no estado do parser.
- **O receptor tem de ser um método `@Slot` de um `QObject` que vive na thread da GUI** — nunca lambda,
  nunca `functools.partial`. `worker.sig.connect(lambda s: self.label.setText(...))` toca um `QWidget`
  a partir da thread do adaptador **mesmo com `Qt.QueuedConnection`**. Essa é a regra que de fato
  previne a armadilha 14; `QueuedConnection` explícito é asserção de intenção, não requisito funcional.
- **Imutabilidade profunda.** `@dataclass(frozen=True, slots=True)` é rasa: um snapshot congelado que
  contenha lista, dict ou `ndarray` ainda aliasa memória que o produtor muta — a UI lê um valor que
  mudou depois de amostrado (P1). `TelemetrySnapshot` e `FieldValue` só podem conter escalares, `str`,
  `None` e tuplas. Declarar `Signal(object)`.
- **Os buffers numpy do plot pertencem à thread da GUI, e só a ela.** `setData` guarda uma *view*, não
  uma cópia; escrita pela thread do adaptador produz leitura rasgada na pintura, sem erro e sem crash.
  É a armadilha 14 numa forma que a regra "nada Qt cruza thread" não pega, porque `ndarray` não é
  objeto Qt.
- Nenhum objeto Qt cruza a fronteira de thread — apenas dataclasses imutáveis (P6).
- Gráficos com `setData` sobre arrays numpy pré-alocados e janela deslizante fixa. Com `append` em
  lista Python e replot a cada amostra, quatro séries a 10 Hz durante 1 h (144 mil pontos) derrubam a
  UI para poucos quadros por segundo.
- Tocar em `QWidget` fora da thread da GUI é comportamento indefinido — produz crashes esporádicos que
  aparecem exatamente na hora da demonstração.

---

## 11. Funcionalidades

### 11.1 Replay

Replay é um adaptador (A1), e é quase inteiramente questão de modelo de dados, não de UI.

- **Cursor** sobre `(session_id, t_recv_ns)` — único eixo presente em todas as tabelas de amostra
  (§7.1) — conduzido por relógio monotônico próprio:
  `t_data = t0_data + (perf_counter() - t0_wall) * speed`, com o próximo índice obtido por **busca
  binária** (`np.searchsorted`). Nunca somar `sleep(dt)` — a granularidade do SO acumula drift. Assim
  0.25x, 1x, 16x, pause, scrub e reprodução reversa (velocidade negativa) caem no mesmo código.
  Se `perf_counter() - último > 1 s`, **reancorar em vez de avançar** e emitir evento visível: o
  `QueryPerformanceCounter` conta durante suspensão do host, então uma tampa fechada no meio do replay
  voltaria com um delta de horas e a busca binária saltaria para o fim da sessão.
- **Ritmo e rótulo em tempo de veículo**, escalados por sessão a partir de `t_boot_ms` + `boot_epoch`.
  A escala é grandeza **derivada** e carrega a marca disso (P1) — nunca gravada como se fosse o
  `--speedup` declarado. Estimar por segmento de `boot_epoch`, nunca por regressão global: o lockstep
  do PX4 para o relógio do veículo quando o simulador trava, então a relação é linear por partes.
- **Decimação** min/max por bucket temporal (estilo LTTB) na leitura, mantendo ~2000 pontos por gráfico
  independente do zoom. A 10x com dados a 50 Hz seriam 500 pontos/s empurrados no PyQtGraph.
- **Sem interpolação** (P1). Reproduz os valores gravados, com marca visual onde a série tem gap maior
  que o intervalo esperado.
- **`Protocol Clock` injetável**, com `FakeClock` nos testes — a lógica de replay é testada sem nenhum
  `sleep`, deterministicamente.
- O README documenta que a fidelidade do replay é limitada pela taxa de stream configurada no
  autopiloto.

### 11.2 Comparação de execuções

O recurso que define o produto. Dado um conjunto de sessões com o mesmo `mission_plan_hash`:

**Sobreposição visual**

- trilhas no mapa, uma cor por execução;
- séries temporais alinhadas por **progresso de missão** (`current_wp_seq`), não por tempo absoluto —
  duas execuções da mesma missão têm durações diferentes.

**Métricas de delta, por execução e por leg**

| Métrica | Origem |
|---|---|
| Erro de trilha (RMS e máximo) | `xtrack_error_m` |
| Erro de altitude (RMS e máximo) | `alt_error_m` |
| Tempo por leg | `current_wp_seq` + timestamps |
| Consumo por leg | `batt_consumed_mah` |
| Contagem de eventos | `alert_event`, `arm_event`, `statustext` com severidade ≥ WARNING |
| Qualidade de estimativa | máximo de `ekf_pos_horiz_var` |

**Veredito**

Uma execução é marcada como baseline. As demais recebem PASS/FAIL por métrica, contra tolerâncias
configuráveis. A saída é um relatório em Markdown/JSON, adequado para anexar a um PR ou a um job de CI.

### 11.3 Alertas

Avaliados na ingestão, reavaliáveis sobre o dado cru (A4), com `rule_version` em cada evento. **Nenhuma
regra avalia campo `NULL`.**

| Código | Condição | Severidade |
|---|---|---|
| `LINK_DEGRADED` | 1 heartbeat perdido | WARNING |
| `LINK_LOST` | 3 heartbeats perdidos | CRITICAL |
| `GPS_FIX_LOST` | `gps_fix_type < 3` | CRITICAL |
| `GPS_HDOP_HIGH` | `gps_hdop > 2.0` | WARNING |
| `EKF_VARIANCE_HIGH` | `ekf_pos_horiz_var > 0.8` | CRITICAL |
| `BATT_CELL_LOW` | tensão por célula sob carga < 3.5 V | WARNING |
| `BATT_CONSUMED_HIGH` | `batt_consumed_mah` > 80% da capacidade configurada | WARNING |
| `XTRACK_HIGH` | `xtrack_error_m` acima da tolerância da missão | WARNING |
| `RC_FAILSAFE` | bit `MAV_SYS_STATUS_SENSOR_RC_RECEIVER` em falha | CRITICAL |
| `SENSOR_UNHEALTHY` | qualquer bit de `sensors_health_mask` em falha | WARNING |

Alertas de **qualidade de estimativa** vêm antes de alertas de velocidade — são os que de fato importam
operacionalmente.

**Nota de demonstrabilidade:** no PX4 SITL a bateria simulada por padrão só se esgota até **50%** da
capacidade. Um limiar em 20% nunca dispara e a demo parece quebrada sem erro no log. O roteiro de teste
documenta `param set SIM_BAT_MIN_PCT 0` e `SIM_BAT_DRAIN` baixo (PX4), e `param set SIM_BATT_VOLTAGE`
(ArduPilot).

### 11.4 Mapa

Trilha histórica, ícone orientado por `hdg_deg`, waypoints do plano e scrubber temporal ligado ao
replay. Tiles servidos de **MBTiles offline empacotado** ou cache local — isso resolve simultaneamente
a Tile Usage Policy do OpenStreetMap (que proíbe uso pesado e exige User-Agent identificável), o
funcionamento sem rede, e o empacotamento.

### 11.5 Exportação

| Formato | Uso |
|---|---|
| CSV | análise em planilha/pandas; cabeçalhos carregam unidade (`alt_amsl_m`, não `altitude`) |
| GPX / KML | trajetória; abre no Google Earth, bom para README |
| `.tlog` | interoperabilidade com Mission Planner e QGC |
| Markdown / JSON | relatório de comparação (§11.2) |

---

## 12. Requisitos não-funcionais

| Requisito | Alvo | Verificação |
|---|---|---|
| Vazão de ingestão | 50 msg/s agregadas sem perda | teste de soak de 1 h contra o mock, no CI |
| Latência recebimento → pixel | < 100 ms (p95) | instrumentação com `t_recv_mono_ns` |
| Tempo de startup | < 3 s até janela interativa | teste de fumaça no executável |
| Memória residente | < 300 MB após 1 h de missão | teste de soak |
| Janela de plot | deslizante fixa, últimos 60 s ao vivo | — |
| Escrita em banco | lote a cada 1 s, WAL ligado | teste de throughput |
| Pontos por gráfico | ~2000, independente do zoom | teste de decimação |
| Volume em disco | documentado em MB/hora | medição publicada no README |

A **taxa de telemetria é decisão de projeto, não do simulador** (§9.4). No ArduPilot, `ATTITUDE` sai a
poucos Hz por padrão e pode ir a 50 Hz com `SRx_EXTRA1`.

---

## 13. Stack

| Camada | Escolha | Nota |
|---|---|---|
| Linguagem | Python 3.11+ | — |
| Interface | **PySide6-Essentials** | não o meta-pacote `PySide6`, que puxa QtWebEngine, Qt3D, QtMultimedia e QtCharts e leva o bundle a centenas de MB |
| MAVLink | **pymavlink** | §9.2 |
| Gráficos | PyQtGraph (MIT) | `PYQTGRAPH_QT_LIB=PySide6` fixado |
| Mapa | **pyqtgraph sobre tiles raster MBTiles** pré-renderizados | §11.4 — QtLocation, QtPositioning e QtWebEngine **não existem** no `PySide6-Essentials`; estão no `PySide6-Addons`. Usá-los contradiria a linha acima e o argumento de tamanho de bundle de §18. ADR 0017 |
| ULog | pyulog (BSD-3, do próprio PX4) | leitura de `.ulg` |
| Banco | SQLite (WAL) | — |
| Testes | pytest, pytest-qt, pytest-cov | — |
| Lint / tipos | ruff, mypy, import-linter | import-linter faz cumprir A6 |
| Empacotamento | PyInstaller **`--onedir`** | §18 |
| ROS 2 (opcional) | rclpy, **fora do processo** | §9.8 |

PyQtGraph escolhe o binding Qt em tempo de import, tentando **PyQt6, PySide6, PyQt5 e PySide2** em
ordem — o que torna um binding solto no ambiente *mais* perigoso, não menos, já que um PyQt6 perdido
ganha de saída —
`importlib` dinâmico que a análise estática do PyInstaller não segue. Daí a variável fixada e os
`--exclude-module` de §18.

### 13.1 Plataformas e contrato de portabilidade

**Decisão:** desenvolvimento em Linux. Windows é alvo suportado, garantido por contrato verificado no
CI desde a semana 1 (P11).

Linux é o ambiente de desenvolvimento correto para este projeto por três razões concretas: ArduPilot
SITL, PX4 SITL, Gazebo e ROS 2 rodam nativamente, sem WSL2 nem ponte de rede; o filesystem é
case-sensitive, o que expõe imediatamente bugs de import e de caminho que o Windows esconderia; e o
build de containers para `integration.yml` é o mesmo ambiente do desenvolvimento.

#### Matriz de suporte

| Componente | Linux | Windows | macOS |
|---|---|---|---|
| Aplicação (UI, adaptadores, banco, replay) | ✅ primário | ✅ suportado | possível, fora da matriz |
| Ingestão de `.tlog` / `.bin` / `.ulg` | ✅ | ✅ | ✅ |
| Executável PyInstaller | ✅ `release.yml` | ✅ `release.yml` | não publicado |
| ArduPilot SITL | ✅ nativo | via WSL2 | via container |
| PX4 SITL | ✅ nativo | **só** WSL2 (não há build nativo) | limitado |
| Gazebo | ✅ nativo | experimental | limitado |
| `ros2_bridge` | ✅ nativo | ❌ não suportado | ❌ |

O `ros2_bridge` é declarado **Linux-only** no README (§9.8). Como ele é um processo separado que fala
socket, um app rodando em Windows pode consumir um bridge rodando em Linux na mesma rede — a limitação
é de onde o bridge roda, não de quem o consome.

#### Regras do contrato — custam zero agora, custam semanas depois

Estas regras não são estilo: são o que impede que "Windows depois" vire reescrita.

| # | Regra | O que quebra sem ela |
|---|---|---|
| C1 | `pathlib.Path` sempre; nunca concatenar caminho com `/` ou `os.sep` literal | caminhos inválidos no Windows |
| C2 | Todo caminho de usuário vem de `QStandardPaths` via `app/core/paths.py` (§8.6) | banco em diretório somente-leitura |
| C3 | `encoding='utf-8'` **explícito** em toda abertura de arquivo texto | o default do Python no Windows é a codepage ANSI, não UTF-8 — acentos e `STATUSTEXT` corrompem |
| C4 | CSV escrito com `open(..., newline='')` | Windows grava `\r\r\n` e o arquivo abre errado |
| C5 | `.gitattributes` com `* text=auto eol=lf` | fim de linha misturado no repositório |
| C6 | Nunca `os.fork`. Se usar `multiprocessing`, código spawn-safe: guard `if __name__ == '__main__'`, argumentos picklable | Windows usa `spawn`, não `fork` — o processo filho reimporta o módulo e entra em loop |
| C7 | Opções de socket isoladas em um único módulo, com branch por plataforma | `SO_REUSEPORT` **não existe** no Windows, e `SO_REUSEADDR` tem semântica diferente lá (permite sequestrar porta já ligada) |
| C8 | Nunca assumir que é possível apagar ou renomear arquivo aberto | Windows trava o handle — afeta rotação de log e checkpoint do `-wal` |
| C9 | Nada depende de bit de execução, shebang ou symlink | não existem no Windows |
| D10 | `subprocess` sem `shell=True`, com lista de argumentos | quoting diverge entre os dois |
| C11 | Nenhum caminho, nome de módulo ou nome de arquivo diferindo só por maiúscula | Windows é case-insensitive e aceitaria; Linux não |
| C12 | Job `windows-latest` no `ci.yml` desde o primeiro commit | é o que transforma o contrato em verificação, e não em intenção |

#### O que só se resolve com máquina Windows na mão

Aceitar e agendar para a primeira release Windows, não antes:

- comportamento de `--windowed` sem console (§18.1) — o código de logging já está pronto, mas só é
  testável lá;
- coleta do plugin de plataforma `qwindows.dll` (`QT_DEBUG_PLUGINS=1`);
- aviso do SmartScreen e ausência de assinatura de código;
- escalonamento de DPI a 125% e 150%;
- falso positivo de antivírus sobre o bundle.

Nenhum desses bloqueia o desenvolvimento em Linux, e nenhum exige mudança arquitetural — é a razão de
serem adiáveis com segurança.

#### Armadilha específica de Linux: glibc

PyInstaller linka contra a glibc da máquina de build, e glibc **não é forward-compatible**. Um binário
construído em Ubuntu 24.04 não roda em Ubuntu 22.04, e o erro em runtime é
`GLIBC_2.xx not found` — não um aviso de build.

**Regra:** construir no runner mais **antigo** que se pretende suportar. No `release.yml`, fixar
`ubuntu-22.04`, nunca `ubuntu-latest`. Documentar a versão mínima de glibc no README.

Distribuição Linux: tarball do `--onedir` como formato primário; AppImage como conveniência opcional.
Nunca `.deb`/`.rpm` como único caminho — amarram a distribuição.

#### Armadilha específica de Linux: Wayland

`QT_QPA_PLATFORM` resolve para `wayland` ou `xcb` conforme a sessão, e há diferenças reais de
comportamento no PySide6 entre as duas — posicionamento de janela, decoração e captura de tela entre
elas. Testar manualmente nas duas antes de cada release, e registrar no README qual foi validada.

Em CI isso não aparece: os testes rodam com `QT_QPA_PLATFORM=offscreen`, que não é nem uma nem outra.

---

## 14. Estrutura do repositório

```text
sortie/
├── app/
│   ├── models/                 # dataclasses puras; não importa nada do projeto
│   ├── adapters/
│   │   ├── mavlink/
│   │   │   ├── source.py       # máquina de conexão, handshake, parsing
│   │   │   └── profiles/
│   │   │       ├── ardupilot.py
│   │   │       └── px4.py
│   │   ├── file_replay/        # .tlog, .bin (DFReader), .ulg (pyulog)
│   │   ├── sqlite_replay/
│   │   └── mock/               # com injeção de falhas
│   ├── database/               # schema, migrações, writer
│   ├── core/
│   │   ├── paths.py
│   │   ├── clock.py            # Protocol Clock + FakeClock
│   │   ├── state_store.py
│   │   ├── alert_engine.py
│   │   └── event_bus.py
│   ├── services/               # comparação de execuções, export
│   └── ui/                     # ninguém importa daqui
├── ros2_bridge/                # pacote ROS 2 separado, package.xml próprio
├── tests/
│   ├── fixtures/
│   │   ├── ardupilot_copter_takeoff.tlog
│   │   ├── px4_quad_mission.tlog
│   │   ├── malformed_frames.bin
│   │   └── schema_v1.db
│   └── ...
├── docs/
│   ├── adr/
│   ├── sortie.md                   # este documento
│   └── validation-gazebo.md
├── .github/workflows/{ci,integration,release}.yml
├── LICENSE
├── THIRD_PARTY_NOTICES.md
├── main.py
├── pyproject.toml
└── .gitignore
```

---

## 15. Endpoints de conexão

O contrato de conexão é uma **string de URL** no formato de `mavutil.mavlink_connection`, definida no
modelo desde o início.

| Cenário | Realidade | Endpoint |
|---|---|---|
| ArduPilot SITL sem MAVProxy | TCP 5760 = serial0, **um cliente por vez**; 5763 = conexões adicionais | `tcp:127.0.0.1:5760` |
| ArduPilot via `sim_vehicle.py` | MAVProxy toma a 5760 e reemite em UDP 14550/14551 | subir com `--out=udp:127.0.0.1:14552`, conectar em `udpin:127.0.0.1:14552` |
| PX4 SITL — porta de GCS | UDP 14550, normalmente já ocupada pelo QGC | `udpin:0.0.0.0:14550` com `SO_REUSEADDR` |
| PX4 SITL — API offboard | UDP 14540, alocado 14540-14549 em multi-veículo | `udpin:0.0.0.0:14540` |
| ArduPilot SITL — porta MAVLink dedicada | 5762 = SERIAL1, o primeiro canal MAVLink de verdade | `tcp:127.0.0.1:5762` |
| Log gravado | — | `./voo.tlog` — **caminho nu** |

Apontar para 14550 em vez de 14540 no PX4 muda o perfil de streams recebido. O `SO_REUSEADDR` **já é
feito pelo pymavlink** para `udpin` — a v1.1 pedia que fizéssemos, o que era redundante. A porta 5760
do ArduPilot é o console e sobe com `:wait`, então o SITL pode bloquear no start até um cliente
conectar; a porta MAVLink de verdade é a 5762.

**Estado obrigatório (P9):** "socket aberto mas zero HEARTBEAT em 3 s" é reportado como **erro de
conexão**, nunca como conectado.

---

## 16. Roadmap

Cada versão tem **critério de pronto verificável**. Nenhuma release depende de infraestrutura externa
(P8).

### Etapa 0 — Ambiente (gate obrigatório, semana 1)

Ambiente de desenvolvimento: **Linux nativo** (Ubuntu 22.04 LTS de referência). Tudo o que o projeto
consome roda nativamente, sem camada de virtualização nem ponte de rede.

1. Python 3.11+ em virtualenv. Nenhum PyQt no venv (§18).
2. `.gitattributes` com `* text=auto eol=lf` e `.gitignore` de §20 — **antes** do primeiro commit.
3. `ci.yml` com os jobs `ubuntu-latest` **e** `windows-latest` (P11, C12). O job Windows existe desde
   o primeiro push, mesmo que rode apenas `ruff` e um `pytest` vazio.
4. ArduPilot SITL compilado localmente: `sim_vehicle.py -v ArduCopter --console`.
   Para PX4, o alvo de simulador `none` basta — não é preciso Gazebo (§16, "Fora do roadmap").
5. Plano B provisionado em paralelo: um `.tlog` público baixado para `tests/fixtures/`, garantindo que
   o desenvolvimento nunca fique bloqueado por SITL (P8).

**Pronto quando:**
- [ ] script pymavlink de 10 linhas imprime HEARTBEAT vindo do SITL local
- [ ] o mesmo script, apontado para o `.tlog` baixado, imprime os mesmos campos
- [ ] `ci.yml` verde nos dois sistemas operacionais

O terceiro item é o que torna o suporte a Windows um contrato em vez de uma intenção. Ele custa
minutos agora.

> **Nota — desenvolvendo em Windows (não é o caso deste projeto)**
> ArduPilot intitula o caminho nativo de *"SITL setup on Windows using Cygwin (not recommended)"* e
> direciona para WSL; PX4 não tem build nativo, só o *"Windows Development Environment (WSL2-Based)"*.
> Nesse cenário, `.wslconfig` precisa de `networkingMode=mirrored`: em modo NAT (padrão), código dentro
> do WSL não alcança `127.0.0.1` do Windows — o host tem outro IP, obtido do nameserver em
> `/etc/resolv.conf` — e um app fazendo bind em `udpin:127.0.0.1:14550` **nunca recebe pacote nenhum,
> com sintoma de "não conecta", sem erro**. O repositório do ArduPilot deve ficar dentro do filesystem
> do WSL, não em `/mnt/c`. Mantido aqui para quem for reproduzir o projeto naquele ambiente.

---

### v0.1.0 — MVP, 100% offline (4-6 semanas)

Nenhum SITL, nenhuma dependência externa. Ordem escolhida para produzir artefato visível **no dia 5**.

| # | Entrega | Dia |
|---|---|---|
| 1 | Janela PySide6 com `QLabel` de altitude e `QTimer` | 1-2 |
| 2 | `MockSource` com injeção de falhas alimentando o label | 3 |
| 3 | Modelo interno completo (§7) | 4-6 |
| 4 | Primeiro gráfico PyQtGraph — **screenshot no README** | 5 |
| 5 | SQLite: schema, WAL, writer em thread, `user_version` | 7-12 |
| 6 | `SqliteReplaySource` e motor de replay (§11.1) | 13-18 |
| 7 | Motor de alertas (§11.3) | 19-22 |
| 8 | Export CSV + GPX | 23-25 |
| 9 | PyInstaller `--onedir` + LICENSE + README com GIF | 26-30 |

**Pronto quando:**
- [ ] `pytest` verde no CI, em ubuntu-latest e windows-latest
- [ ] executável `--onedir` roda em máquina limpa, sem Python instalado
- [ ] replay de uma sessão gravada reproduz com seek e velocidade variável
- [ ] os 10 alertas disparam contra o mock com injeção de falhas
- [ ] teste de soak de 1 h dentro dos limites de §12
- [ ] tag `v0.1.0` e GitHub Release publicados
- [ ] README com screenshot, GIF e instruções de execução

**Esta versão já é um portfólio completo, e existe mesmo se o projeto parar aqui.**

---

### v0.2.0 — MAVLink contra arquivo (2 semanas)

`MavlinkSource` e `FileReplaySource` validados contra `.tlog` gravado. **Zero SITL.**

`mavutil.mavlink_connection('voo.tlog')` lê um telemetry log exatamente como lê um link ao vivo, e
`DFReader` lê DataFlash `.bin`. Um tlog público exercita 100% do parser e do adaptador. **O adaptador
deve ser testável contra arquivo antes de ser testado contra rede.**

**Pronto quando:**
- [ ] `.tlog`, `.bin` e `.ulg` abrem pelo menu File > Open
- [ ] fixtures commitadas cobrem ArduPilot e PX4
- [ ] teste de fuzz do parser passa sem exceção escapando
- [ ] o executável abre um log de exemplo commitado — avaliador vê o software rodando em 30 s, sem WSL,
      sem SITL, sem Python

---

### v0.3.0 — SITL ao vivo (time-box: 5 dias úteis por backend)

ArduPilot primeiro (roda em Docker headless). Depois PX4, com alvo de simulador **`none`** — o PX4 não
precisa de Gazebo para produzir telemetria MAVLink, e o alvo `none` já gera HEARTBEAT e o stream
completo, que é tudo que este software consome.

**Pronto quando:**
- [ ] handshake de §9.4 recebe `GLOBAL_POSITION_INT` em < 5 s nos dois firmwares
- [ ] perfil de firmware é detectado automaticamente pelo HEARTBEAT
- [ ] `integration.yml` roda os dois SITL em schedule noturno
- [ ] reconexão automática após queda de link, verificada

**Critério de desistência:** se o time-box estourar, a versão sai sem o backend; o plano B (`.tlog`)
cobre a demonstração.

---

### v0.4.0 — Missão e comando

Download do plano (`MISSION_REQUEST_LIST` → `MISSION_COUNT` → `MISSION_REQUEST_INT` →
`MISSION_ITEM_INT` → `MISSION_ACK`), com máquina de estados, timeout e retransmissão. Progresso de
waypoint na UI. Mapa com trilha. Comandos de §3.2.

O protocolo de missão é *stateful*, com sequência request/ack por item — é a única parte do MAVLink que
exige máquina de estados de verdade, e é a que cumpre a promessa do nome (§2.4).

**Pronto quando:**
- [ ] plano baixado e renderizado, com waypoint corrente destacado
- [ ] `mission_plan_hash` estável para o mesmo plano em execuções diferentes
- [ ] as três guardas de §3.2 implementadas e testadas
- [ ] `TEMPORARILY_REJECTED` e `IN_PROGRESS` tratados, com teste

---

### v0.5.0 — Comparação de execuções

§11.2. O recurso que define o posicionamento.

**Pronto quando:**
- [ ] N execuções do mesmo plano sobrepostas no mapa e nas séries
- [ ] métricas por leg calculadas e exibidas
- [ ] relatório PASS/FAIL exportável em Markdown e JSON
- [ ] um exemplo de uso do relatório num job de CI, documentado

---

### v0.6.0 — Bridge ROS 2 (opcional)

§9.8. Fora do executável, documentado como Linux-only.

---

### Fora do roadmap: Gazebo

Do ponto de vista do aplicativo, ArduPilot SITL sozinho e ArduPilot SITL + Gazebo produzem **exatamente
o mesmo stream MAVLink**, na mesma porta, com as mesmas mensagens. A diferença está na física que
alimenta o autopiloto, não na interface com o software.

Gazebo é **bônus visual**: uma sessão no WSL gravando um GIF com a janela do Gazebo ao lado da UI, para
o README, documentada em `docs/validation-gazebo.md`. **Nenhum teste, feature ou release pode depender
dele.**

*Extensão possível no futuro:* assinar `/world/<mundo>/pose/info` via `gz.transport` para obter **ground
truth** e comparar com a estimativa do EKF. Isso é um adaptador de categoria diferente, e seria um
diferencial real.

---

### Atividades contínuas, desde a semana 1

CI, build PyInstaller no CI, e um ADR por decisão tomada (P10).

---

## 17. Testes e CI

### 17.1 Fixtures

Testes de adaptador precisam de bytes MAVLink reais. O mock não serve para isso: ele gera
`TelemetrySnapshot` já pronto e exercita a UI, **não o parser**, que é onde moram os bugs de
decodificação.

**Gerar sinteticamente e commitar** (~1-3 MB cada). Não existe `.tlog` público pequeno, comprovadamente
de origem SITL e redistribuível: o `flight.tlog` do dronekit é Apache-2.0 mas de proveniência não
documentada, o `test.BIN` do pymavlink é DataFlash e as amostras do pyulog são ULog. Como P8 proíbe
depender de infra externa, `tools/make_fixtures.py` produz os arquivos com os próprios encoders do
pymavlink, semeados no CMAC (`-35.363261, 149.165230`) e em Zurich Irchel (`47.397742, 8.545594`) —
constantes publicadas de simulador, o que satisfaz §20 **por construção** em vez de por uma auditoria
que ninguém consegue fazer sobre um binário opaco.

Duas armadilhas do gerador sintético, ambas com teste próprio:

- O round-trip encoder→decoder do pymavlink é **tautológico** — prova que a biblioteca é consistente
  consigo mesma, não que nosso entendimento do formato de fio está certo. Acrescentar
  `tests/fixtures/golden_frames.py` com frames hex verificados à mão (de `mavlink.io` / vetores da
  c_library) para #0, #33, #24 e #147, e afirmar que **nosso** encoder reproduz aqueles bytes e
  **nosso** decoder aqueles valores. É também o teste que pega um bump de versão do pymavlink.
- As fixtures precisam de um **segundo componente** (gimbal em compid 154, emitindo HEARTBEAT com o bit
  armado piscando), senão o filtro de `compid` de §7.2 nunca é exercitado e a armadilha 11 só aparece
  com tráfego real.

Quando o SITL chegar na v0.3, gravar um `.tlog` real e substituir. A v0.1 e a v0.2 não podem depender
disso.

```
tests/fixtures/ardupilot_copter_takeoff.tlog
tests/fixtures/px4_quad_mission.tlog
tests/fixtures/malformed_frames.bin     # truncados, CRC inválido, msgid desconhecido
tests/fixtures/schema_v1.db             # para testar migração
```

Helper `replay_tlog(path, dialect) -> Iterator[TelemetrySnapshot]` usado por todos os testes de
adaptador.

**Atenção ao dialeto:** uma fixture ArduPilot lida como `common` descarta silenciosamente as mensagens
específicas em vez de falhar alto.

Testes que exigem SITL: `@pytest.mark.sitl`, desmarcados por default
(`addopts = '-m "not sitl"'` no `pyproject.toml`).

### 17.2 Testes de UI

`QT_QPA_PLATFORM=offscreen` — com PySide6 não é preciso Xvfb. Três armadilhas conhecidas:

- **Um `QApplication` por processo.** A fixture `qapp` do pytest-qt é a dona dele; criar um próprio em
  `main.py` e importá-lo nos testes causa abort.
- **`qtbot.addWidget(w)` é obrigatório** — mas **não** pela razão dada na v1.1. Ele guarda uma
  *weakref* e não mantém nada vivo; manter o widget vivo durante o teste continua sendo trabalho de
  quem escreve o teste (uma variável local basta). A regra é carga útil porque garante
  `close()` + `deleteLater()` **antes** do teardown do `QApplication` de sessão, que é o que evita o
  segfault.
- **Com PySide6 ≥ 6.5.2 o pytest-qt não intercepta mais exceções levantadas dentro de slots** — o
  PySide6 as relança no Python. Não escrever teste que dependa da captura do pytest-qt, e não setar
  `qt_no_exception_capture`.
- **`qtbot.waitSignal` / `waitUntil` em vez de `time.sleep`** — a principal fonte de flakiness em Qt.

Afirmar sobre estado, não sobre pixel: `widget.text()`, `PlotDataItem.getData()`,
`qtbot.waitSignal(bus.alert_raised, timeout=1000)`.

Meta de cobertura sobre `app/core`, `app/adapters` e `app/database` (80%). `app/ui` fica **fora** do
gate, para o número ser honesto em vez de inflado por smoke de widget.

### 17.3 Workflows

PyInstaller **não faz cross-compilation**, então cada artefato sai do seu próprio runner. Gazebo em
runner hospedado não tem GPU — só headless sobre llvmpipe, lento e instável demais para rodar por PR.

| Arquivo | Gatilho | Conteúdo | Runner |
|---|---|---|---|
| `ci.yml` | todo push | ruff, mypy, import-linter, pytest com fixtures, `QT_QPA_PLATFORM=offscreen` | `ubuntu-latest` (gate) **+** `windows-latest` (guarda de portabilidade, P11) |
| `integration.yml` | schedule noturno + dispatch | matriz ArduPilot/PX4 SITL em **containers pré-construídos** (compilar do zero leva ~15-25 min num runner de 4 vCPU), `timeout-minutes` agressivo | `ubuntu-latest` |
| `release.yml` | tag | PyInstaller `--onedir`, artefatos com SHA-256 | **`ubuntu-22.04`** (não `latest` — glibc, §13.1) + `windows-latest` |

O job Windows do `ci.yml` roda a suíte inteira, exceto os testes marcados `@pytest.mark.sitl`. Ele é
barato (2-3 min) e é o único mecanismo que impede que uma regra de §13.1 seja violada sem ninguém
perceber. Uma falha nele **quebra o build**, como qualquer outra.

---

## 18. Empacotamento

Um build PyInstaller roda no CI desde a semana 1, com a janela vazia (P10).

- **`--onedir`, não `--onefile`.** Startup mais rápido, menos falso positivo de antivírus, e é o único
  formato compatível com a obrigação de relink da LGPL (§19). Em `--onefile` o bundle é extraído para
  `%TEMP%` a cada execução, com rescan do Defender toda vez.
- `PySide6-Essentials`, não o meta-pacote.
- `PYQTGRAPH_QT_LIB=PySide6` + `--exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide2`,
  e nenhum PyQt no venv de build.
- Validar num runner limpo, **sem Python instalado**, com `QT_DEBUG_PLUGINS=1` — o plugin de plataforma
  frequentemente não é coletado, produzindo `Could not load the Qt platform plugin "windows"` só em
  runtime.
- Publicar ZIP com checksum SHA-256 e um banco de demonstração versionado, com modo `--replay demo.db`.

**Code signing: não perseguir.** Desde 01/06/2023 o baseline do CA/Browser Forum exige chave privada em
módulo FIPS 140-2 nível 2 ou CC EAL 4+ (token USB ou HSM em nuvem), o que joga o custo para centenas de
dólares por ano. Documentar o aviso do SmartScreen no README e deixar "rodar a partir do código" como
caminho primário.

### 18.1 Logging

Uma exceção levantada dentro de um slot Python invocado a partir do C++ do Qt não pode propagar de
volta, então o PySide6 a encaminha para `sys.excepthook`, cujo default escreve em `sys.stderr`. Num
build `--windowed` no Windows **não existe console**: `sys.stdout` e `sys.stderr` são `None` ou handles
inválidos, e qualquer `print()` ou `StreamHandler` levanta `AttributeError` ou descarta tudo.

Resultado sem defesa: o app funciona com `python main.py` e, empacotado, fecha sozinho sem uma linha de
diagnóstico — violação frontal de P9, exatamente quando alguém está avaliando.

No topo de `main.py`, **antes de importar Qt**:

```python
# (i) ANTES de importar PySide6 — para capturar falha no próprio import
logfile   = paths.log_dir() / 'sortie.log'
_CRASH_FH = open(logfile.with_suffix('.crash'), 'w', buffering=1, encoding='utf-8')
faulthandler.enable(file=_CRASH_FH, all_threads=True)     # segfault do lado C++
logging.getLogger().addHandler(RotatingFileHandler(logfile, maxBytes=5<<20,
                                                   backupCount=3, delay=True))
logging.raiseExceptions = False       # --windowed: sys.stderr é None
logging.lastResort      = None
sys.excepthook          = _log_exception
threading.excepthook    = _log_thread_exception   # sys.excepthook NÃO cobre threads

# (ii) agora sim
from PySide6 import QtCore
# (iii) antes de construir o QApplication — os avisos qt.qpa.plugin disparam na construção
QtCore.qInstallMessageHandler(_log_qt_message)
# (iv) create_app(argv)
```

Quatro correções sobre a v1.1, todas capazes de esconder todo bug posterior:

1. **`_CRASH_FH` tem de ser nome de módulo.** `faulthandler.enable(file=open(...))` não liga o objeto a
   nada: o faulthandler guarda só o fd inteiro, o refcount cai a zero no retorno, o CPython fecha o
   arquivo, e **o fd é reciclado pelo próximo `open()` do processo** — o traceback do segfault acaba
   dentro do `sortie.log`, dentro do handle do SQLite, ou em lugar nenhum.
2. **"antes de importar Qt" não pode valer para `qInstallMessageHandler`** — ela *é* Qt. Daí a ordem
   numerada acima.
3. **Há um quarto caminho descoberto, e é o modelo de thread deste projeto.** `threading.excepthook` só
   cobre `threading.Thread.run()`; um worker de `QThread` não é um `threading.Thread`, então uma
   exceção no `run()` do adaptador não chega a nenhum dos dois hooks. Os "3 excepthooks" viram **quatro
   defesas**: `try/except` no corpo de todo `run()` e de todo `@Slot`, logando e emitindo
   `link_state=ERROR` (P9 — *"thread que morre derruba o estado de conexão"*).
4. `encoding='utf-8'` explícito (C3) e `delay=True` no handler, para o processo ocioso não segurar o
   arquivo aberto à toa — a rotação renomeia arquivo aberto, que é C8 / armadilha 33 sobre o único
   arquivo aberto em toda execução.

Nunca um `StreamHandler` nu. Registros em JSON lines (`session_id`, `adapter`, `sysid`, `msg_type`) para
serem grepáveis. Item de menu "Abrir pasta de logs". Um `--selftest` que roda o executável no CI
afirmando exit code 0 e log gerado.

---

## 19. Licenciamento

| Dependência | Licença | Implicação |
|---|---|---|
| PySide6 | LGPLv3 | obrigação de relink → `--onedir` |
| pymavlink | **LGPLv3-or-later** — só os módulos de dialeto *gerados* por `mavgen.py` são MIT; a biblioteca de runtime que se importa (`mavutil`, `mavwp`, `DFReader`) é LGPL | idem |
| lxml, fastcrc | BSD-3 / MIT-Apache — chegam transitivamente pelo pymavlink, e são **wheels nativas** | declarar em `THIRD_PARTY_NOTICES.md` |
| PyQtGraph | MIT | livre |
| pyulog | BSD-3-Clause | livre |
| MAVSDK (se usado) | BSD-3-Clause | livre |
| PyInstaller | GPLv2+ **com exceção de bootloader** | não contamina o app congelado |
| Qt Charts / Qt Data Visualization | **GPLv3 / comercial** | **não usar** — trocar PyQtGraph por QtCharts contaminaria o projeto inteiro |

A LGPLv3 §4 exige que o usuário final consiga substituir a biblioteca por uma versão modificada e
executar o resultado. Com `--onefile` tudo vira um auto-extraível único e não há mecanismo de
substituição.

Um repositório **sem `LICENSE` significa "todos os direitos reservados"**: ninguém pode legalmente
forkar, o que anula parte do valor de um projeto público.

**Ações:** `LICENSE` (MIT ou Apache-2.0 para o código próprio); `THIRD_PARTY_NOTICES.md` com
dependência, licença e versão; textos de LGPLv3/GPLv3 no bundle; diálogo "Sobre / Licenças" na UI; nota
no README sobre como reconstruir com uma versão modificada do Qt.

---

## 20. Segurança e privacidade

Coordenada de voo real é geolocalização precisa e, associável ao operador, é dado pessoal sob a LGPD
(art. 5) e o GDPR. O ponto de decolagem tipicamente revela residência ou local de trabalho.

- `.gitignore`: `*.db`, `*.db-wal`, `*.db-shm`, `*.tlog`, `*.bin`, `exports/`.
- Regra explícita no CONTRIBUTING: **apenas dados de SITL** entram no repositório e nas mídias de
  demonstração. SITL é seguro e reproduzível porque o home é fixo e público — CMAC/Canberra
  (-35.363261, 149.165230) no ArduPilot, Zurich Irchel Park (47.397742, 8.545594) no PX4.
- README declara onde o banco fica e que ele **não é cifrado em repouso**.
- Toggle opcional de redação no export CSV (offset fixo de coordenada ou truncamento de casas decimais)
  e retenção configurável de missões antigas.
- Bind default em loopback e filtro por `(sysid, compid)` — §9.6.

---

## 21. Armadilhas conhecidas

Tabela de consulta rápida. Cada linha é um bug que já custou semanas a alguém.

| # | Armadilha | Sintoma | Prevenção |
|---|---|---|---|
| 1 | ArduPilot não envia streams sem pedido | HEARTBEAT chega, posição nunca. PX4 "funciona" e ArduPilot "tem bug" | `SET_MESSAGE_INTERVAL` no handshake (§9.4) + teste de 5 s |
| 2 | WSL2 em modo NAT | Bind em `127.0.0.1` nunca recebe pacote, sem erro | `networkingMode=mirrored` no `.wslconfig` |
| 3 | `time_boot_ms` com wrap e reboot | Replay ordena errado; timestamps decrescentes | contador de época; três relógios (§7.1) |
| 4 | SITL não roda em tempo real | Replay 10x mais lento; derivadas erradas por fator 10 | cursor indexa em `t_recv_ns`, com o **ritmo escalado por sessão**; nunca tempo de parede não escalado (§7.1) |
| 5 | `battery_remaining == -1` | Alerta dispara no primeiro segundo, ou nunca | sentinela → `NULL`; regra não avalia `NULL` (P4) |
| 6 | `voltages[]` com 65535 | Soma dá ~65 V a mais por célula vazia | ignorar entradas `== 65535` |
| 7 | PX4 SITL para de drenar a bateria em 50% | Alerta de bateria nunca dispara; demo parece quebrada | `SIM_BAT_MIN_PCT 0` no roteiro de teste |
| 8 | `dialect='common'` com ArduPilot | `EKF_STATUS_REPORT` vira UNKNOWN, em silêncio | `dialect='ardupilotmega'` (superset) |
| 9 | MAVLink 1 no link | Campos de extensão chegam **zerados**, não como erro | fixar MAVLink 2; flag de campo-de-extensão |
| 10 | `source_system=255` (default do pymavlink) | Colide com o sysid do QGC; failsafe de GCS ambíguo | fixar 245-250, compid 190 |
| 11 | Sem filtro de `compid` | Gimbal vira veículo; `armed` pisca sozinho | filtrar `get_srcComponent() == 1` |
| 12 | Exceção de parse escapando do loop | Thread morre; UI congela nos últimos valores | try/except **por frame** + contadores visíveis (P9) |
| 13 | `recv_match(blocking=True)` sem timeout | Bloqueia para sempre; UDP nunca sinaliza desconexão | `timeout=0.5` + watchdog de heartbeat |
| 14 | Widget Qt tocado fora da thread da GUI | Crash esporádico, sempre na hora da demo | `Signal` + `QueuedConnection`; nada de Qt cruza thread |
| 15 | Um sinal por mensagem MAVLink | UI engasga; fila cresce mais rápido que o repaint | coalescing a 10-20 Hz com `QTimer` |
| 16 | `sqlite3` em autocommit | Buffer UDP estoura; UI mostra dados atrasados | WAL + `synchronous=NORMAL` + lote de 1 s |
| 17 | Conexão SQLite compartilhada entre threads | `ProgrammingError` em produção | writer com conexão própria |
| 18 | Banco ao lado do `.exe` | `readonly database` em Program Files; ou some em `--onefile` | `QStandardPaths.StandardLocation.AppLocalDataLocation`, enum escopado, com guarda para string vazia (§8.6) |
| 19 | `--windowed` sem log em arquivo | App fecha sozinho, sem rastro | `faulthandler` + `RotatingFileHandler` + 3 excepthooks (§18.1) |
| 20 | Plugin de plataforma Qt não coletado | `Could not load the Qt platform plugin "windows"` | validar em runner limpo com `QT_DEBUG_PLUGINS=1` |
| 21 | PyQtGraph escolhe binding dinamicamente | Bundle com binding errado, ou dois bindings | `PYQTGRAPH_QT_LIB` + `--exclude-module` |
| 22 | `--onefile` com PySide6 LGPL | Descumprimento da obrigação de relink | `--onedir` |
| 23 | `sleep(dt)` no loop de replay | Drift acumulado; não suporta seek nem velocidade | cursor por busca binária sobre relógio monotônico |
| 24 | Interpolar yaw em Euler | Riscos verticais a cada ±π; média sem sentido | `unwrap` ou SLERP sobre quaternion |
| 25 | Carry-forward de posição sem fix | Replay mostra veículo parado em vez de sem posição | `gps_fix_type_at_recv < 3` → `lat`/`lon` = `NULL`, **com a guarda em `sample_global_position`** e não em `sample_gps_raw` — #33 não tem `fix_type` e continua publicando dead reckoning (§7.5) |
| 26 | `sim_vehicle.py` já ocupou 5760/14550 | "Conecta" e não recebe nada; bug parece intermitente | `--out=udp:127.0.0.1:14552` (§15) |
| 27 | `rclpy` no processo do app | `ModuleNotFoundError` no `_rclpy`, ou morte em `create_node()` | bridge fora de processo (§9.8) |
| 28 | Fixture ArduPilot lida como `common` | Mensagens específicas descartadas, teste passa | dialeto explícito no helper de fixture |

### Portabilidade Linux → Windows

| # | Armadilha | Sintoma | Prevenção |
|---|---|---|---|
| 29 | `open()` sem `encoding='utf-8'` | Acentos e `STATUSTEXT` corrompidos **só no Windows** — o default lá é a codepage ANSI | C3: encoding sempre explícito |
| 30 | CSV sem `newline=''` | Windows grava `\r\r\n`; planilha abre com linha em branco entre registros | C4 |
| 31 | `multiprocessing` com código não spawn-safe | No Windows o filho reimporta o módulo e entra em loop de processos | C6: guard `__main__`, args picklable |
| 32 | `SO_REUSEPORT` no bind do socket | `AttributeError` no Windows — a constante não existe | C7: branch por plataforma num módulo só |
| 33 | Rotação de log ou checkpoint do `-wal` com handle aberto | `PermissionError` no Windows; funciona no Linux | C8: nunca renomear/apagar arquivo aberto |
| 34 | Binário construído em `ubuntu-latest` | `GLIBC_2.xx not found` em distro mais antiga, só em runtime | construir em `ubuntu-22.04`; glibc não é forward-compatible |
| 35 | Diferença de comportamento Wayland × X11 | Janela mal posicionada ou sem decoração numa das sessões; CI não pega (usa `offscreen`) | testar manualmente nas duas antes de cada release |

---

## 22. ADRs planejados

Escritos **no momento da decisão**, não no fim.

| # | Decisão |
|---|---|
| 0001 | pymavlink e não MAVSDK: licença, paridade ArduPilot/PX4, empacotamento |
| 0002 | QThread + Signal/QueuedConnection e não asyncio no loop do Qt |
| 0003 | Record raw, derive views: por que o snapshot não é a unidade de armazenamento |
| 0004 | Três relógios e por que o replay roda sobre `t_boot_ms` |
| 0005 | Replay como adaptador e não como módulo |
| 0006 | ROS 2 fora do processo: rclpy, PyInstaller e a amarração de versão do Python |
| 0007 | SQLite em WAL com writer dedicado; política de backpressure |
| 0008 | `--onedir` e conformidade LGPL |
| 0009 | Posicionamento contra as GCS existentes |
| 0010 | Escopo read-mostly: quais comandos o software envia, e por quê |
| 0011 | Linux-first com contrato de portabilidade verificado no CI, em vez de porte Windows posterior |
| 0022 | Renomear para **Sortie**: por que o nome antigo anunciava a categoria errada, e o que o novo promete (§2.4) |

---

## 23. Referências

- **ArduPilot Dev** — SITL, SITL no WSL, *Requesting Data From The Autopilot*: `ardupilot.org/dev/`
- **PX4 User Guide** — Simulation, Windows Development Environment (WSL2), Simulate Failsafes:
  `docs.px4.io/main/en/`
- **MAVLink** — `common.xml`, `ardupilotmega.xml`, Message Signing, Mission Protocol, Standard Modes:
  `mavlink.io/en/`
- **pymavlink** — `github.com/ArduPilot/pymavlink`
- **MAVSDK** — `mavsdk.mavlink.io` (arquitetura cliente/servidor, compatibility mode, limitações
  ArduPilot)
- **QGroundControl** — `docs.qgroundcontrol.com` (Analyze View, Log Viewer, CSV Logging) e
  `github.com/mavlink/qgroundcontrol` (padrão `FirmwarePlugin`)
- **ROS 2** — REP-2000; issues `ros2/ros2#1514`, `#1656`, `#1675`
- **Gazebo** — `gazebosim.org/docs/harmonic/` (suporte Windows experimental)
- **ardupilot_gazebo** — `github.com/ArduPilot/ardupilot_gazebo` (portas FDM 9002/9003)
- **Qt for Python** — `doc.qt.io/qtforpython-6/`
- **PyInstaller** — `pyinstaller.org` (ausência de cross-compilation, exceção de bootloader)
- **SQLite** — `sqlite.org/wal.html`, `sqlite.org/pragma.html`
- **WSL networking** — `learn.microsoft.com/windows/wsl/networking` (`networkingMode=mirrored`)
- **OpenStreetMap Tile Usage Policy** — `operations.osmfoundation.org/policies/tiles/`
- **PX4 Flight Review** — `github.com/PX4/flight_review`
- **pyulog** — `github.com/PX4/pyulog`

---

## Histórico

| Versão | Mudança |
|---|---|
| 1.0 | Documento canônico. Consolida a especificação inicial, a auditoria técnica e as correções decorrentes. |
| 1.1 | Decisão de plataforma: desenvolvimento Linux-first. Adiciona P11 e §13.1 (contrato de portabilidade, matriz de suporte, armadilhas de glibc e Wayland). Etapa 0 reescrita para Linux nativo, com o caminho WSL2 rebaixado a nota. CI passa a rodar `windows-latest` como guarda desde o primeiro commit; `release.yml` fixa `ubuntu-22.04`. Sete novas armadilhas de portabilidade (§21). |
| 1.2 | **Renomeação: Mission Control Hub Desktop → Sortie.** §2.4 reescrito — o nome passa a nomear a unidade de análise (uma execução de missão) em vez de uma capacidade, e deixa de anunciar a categoria "estação de controle de solo" que §1, §2.1 e §2.3 negam. O protocolo de missão (§9.5) continua requisito de primeira classe, agora pelo mérito em §11.2 e não como dívida do nome; o gatilho de renomeação muda de "se o protocolo for cortado" para "se o produto deixar de comparar execuções". Árvore de §14, `docs/sortie.md` e `sortie.log` atualizados. ADR 0016. |
