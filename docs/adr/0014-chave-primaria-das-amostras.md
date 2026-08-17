# ADR 0014 — Chave primária das tabelas de amostra, e uma tabela por msgid

**Status:** aceito — 2026-08-16
**Contexto de referência:** P1, P2, §7.5, §8.4, §21 (armadilha 25)

## Contexto

§8.4 especificava `PRIMARY KEY (session_id, system_id, t_boot_ms)` em tabela `WITHOUT ROWID`. **Isso não
compila**, por cinco motivos independentes:

| Defeito | Evidência |
|---|---|
| SQLite exige NOT NULL em toda coluna de PK de tabela `WITHOUT ROWID` | §7.1 declara `t_boot_ms` nullable |
| Seis das oito famílias não têm timestamp de boot nenhum | `SYS_STATUS`, `BATTERY_STATUS`, `EKF_STATUS_REPORT`, `HEARTBEAT`, `MISSION_CURRENT`, `NAV_CONTROLLER_OUTPUT`, `VFR_HUD`, `STATUSTEXT` não carregam campo de tempo |
| Família alimentada por dois msgids colide | `ATTITUDE` (#30) e `ATTITUDE_QUATERNION` (#31) com o mesmo `time_boot_ms` |
| Multi-bateria colide | `batt_id` estava fora da chave, e §7.5 diz que multi-bateria é o motivo do campo existir |
| Reboot no meio da sessão faz o timestamp recuar | qualquer chave temporal quebra |

## Decisão

```sql
PRIMARY KEY (session_id, system_id, t_recv_ns)     -- + batt_id em sample_battery_status
WITHOUT ROWID, STRICT
```

- `t_boot_ms` vira coluna **simples e nullable** em todas as tabelas.
- `boot_epoch INTEGER NOT NULL DEFAULT 0`, incrementada quando `t_boot_ms` recua — cobre **wrap de 2³²
  e reboot para zero**. É descontinuidade **medida**, não estimada: satisfaz P1. Toda chave ou índice
  sobre tempo de boot é `(session_id, system_id, boot_epoch, t_boot_ms)`.
- **Uma tabela por msgid**, não por família. Quinze: `sample_global_position` (33), `sample_gps_raw`
  (24), `sample_attitude` (30), `sample_attitude_q` (31), `sample_sys_status` (1),
  `sample_battery_status` (147), `sample_ekf_status` (193), `sample_estimator_status` (230),
  `sample_vfr` (74), `sample_mode` (0), `sample_mission_current` (42), `sample_nav_controller` (62),
  `statustext` (253), `sample_home_position` (242), `sample_system_time` (2). Isto é P2 (*"a unidade de
  armazenamento é a mensagem"*) levado ao pé da letra.
- `session_id` **INTEGER** (rowid de `mission_session`), nunca TEXT UUID: 37 B por linha × 180 k
  linhas/hora ≈ 6,7 MB/hora de pura repetição de chave.
- `t_recv_ns` é forçado monotônico: `stamp(observed) = max(observed ou perf_ns(), último + 1)`.

## Consequências

**As oito famílias de §7.5 sobrevivem como views de leitura**, e cada view é `UNION ALL` com coluna
discriminadora `src_msgid`, **nunca `JOIN`**. Um JOIN fabricaria observação conjunta a partir de duas
medições com timestamps independentes — a violação de P1/P2 que a divisão existe para eliminar,
desfeita em silêncio na camada de leitura.

**A guarda de fix tem de mudar de tabela — e este é o risco que a divisão introduziu.**
`GLOBAL_POSITION_INT` (#33) **não tem campo `fix_type`**: é a saída do EKF, e o ArduPilot continua
publicando por dead reckoning depois que o fix se perde. Enquanto posição era uma família só, o
`fix_type` de #24 estava no escopo da regra P1. Um CHECK que more em `sample_gps_raw` não protege a
tabela de onde o mapa, a trilha, a haversine e o `xtrack_error_m` leem. Portanto
`sample_global_position` carrega:

```sql
gps_fix_type_at_recv INTEGER NOT NULL,   -- carimbado do último #24 visto
gps_fix_age_ns       INTEGER NOT NULL,
CHECK (gps_fix_type_at_recv >= 3 OR (lat_dege7 IS NULL AND lon_dege7 IS NULL))
```

O `NOT NULL` é obrigatório: **um CHECK que avalia `NULL` passa**, então sem ele a guarda vaza. Ambas as
colunas são observações, não estimativas — P1 se sustenta.

**O parâmetro `observed` de `stamp()` existe por causa da v0.2.** Na ingestão de `.tlog` o valor vem do
prefixo de 8 bytes, cuja fonte é `time.time()`; num log gravado por GCS no Windows isso carimba muitos
frames consecutivos com o mesmo microssegundo, e sem o forçamento o primeiro `.tlog` real violaria a PK
— com a chave já congelada.

**Testes de aceite:** dois #30 e dois #31 com `time_boot_ms` idêntico inserem sem colidir · reboot
simulado insere e incrementa `boot_epoch` · veículo com duas baterias insere · fix 3→1→3 produz `lat`/
`lon` `NULL` na linha #33 do meio, mesmo com a mensagem de fio carregando coordenada · view de família
com K linhas em #33 e L em #24 retorna exatamente K+L.
