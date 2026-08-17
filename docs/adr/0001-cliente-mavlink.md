# ADR 0001 — pymavlink como cliente MAVLink, não MAVSDK

**Status:** aceito — 2026-08-16
**Contexto de referência:** §9.2, §9.3, §11.2, §19

## Contexto

O produto precisa de três coisas do cliente MAVLink que não são negociáveis:

1. **Acesso a qualquer mensagem do dialeto.** `EKF_STATUS_REPORT` (#193), `STATUSTEXT` com severidade,
   `NAMED_VALUE_FLOAT` e todo o `ardupilotmega` são o material com que §11.3 explica por que uma missão
   terminou como terminou. Sem eles o software não cumpre a própria razão de existir.
2. **Paridade ArduPilot / PX4.** §2.2 é comparar execuções; um cliente que trate um firmware como
   cidadão de segunda classe inviabiliza metade dos casos.
3. **Empacotamento previsível.** §18 distribui um `--onedir` que roda em máquina sem Python.

O MAVSDK-Python falha nos três. É cliente gRPC de um binário C++ (`mavsdk_server`) que precisa ser
distribuído junto; não expõe mensagem arbitrária, só o que os plugins modelam — `Telemetry.GpsInfo` tem
`num_satellites` e `fix_type`, sem HDOP, e não há equivalente do bitmask
`onboard_control_sensors_health`; e o suporte a ArduPilot é parcial por design.

## Decisão

**pymavlink**, com `dialect='ardupilotmega'` (superset estrito de `common`) e MAVLink 2 fixado.

MAVSDK pode entrar depois como adaptador **adicional, declarado PX4-only** — nunca como alternativa
transparente.

## Consequências

**Licenciamento.** pymavlink é **LGPLv3-or-later**. Só os módulos de dialeto *gerados* por `mavgen.py`
são MIT; a biblioteca de runtime que se importa (`mavutil`, `mavwp`, `DFReader`) é LGPL e carrega a
obrigação de relink — que é um dos motivos de §18 exigir `--onedir`. Não deixar §19 reivindicar
cobertura MIT para o pacote inteiro.

**Não é puro-Python.** Traz `lxml` (C) e `fastcrc` (Rust) como wheels nativas transitivas. Consequências:
entram na análise do PyInstaller, entram no `THIRD_PARTY_NOTICES.md`, e o job `ubuntu-latest` precisa
verificar que as wheels manylinux resolvem para os mesmos pins. Instalação offline exige cache de
wheel, não sdist.

**O dialeto é global ao processo, não por conexão.** Isto atinge §11.2 diretamente: comparar um `.tlog`
de ArduPilot contra um de PX4 na mesma janela não pode ser feito com dialetos distintos. Resolução:
`ardupilotmega` para **toda** fonte, sempre. Teste unitário afirma que nenhum caminho de código chama
`set_dialect` mais de uma vez. Se algum dia um dialeto genuinamente PX4-only for necessário, decodificar
em subprocesso.

**Robustez é responsabilidade nossa.** `parse_char` não levanta exceção em bytes corrompidos sob
configuração default — devolve `BAD_DATA` ou `MAVLink_unknown`. Os contadores de §9.6 têm de ser
dirigidos por inspeção desses objetos, e a asserção do teste de fuzz precisa exigir que os contadores
subam; caso contrário passa vacuamente.

**Correções à tabela de §9.2** feitas na v1.2 do documento canônico: a linha de natureza dizia "parser
puro-Python"; a de PyInstaller dizia "sem binário nativo"; a de signing dizia "não suportado" quando o
correto é "não documentado / sem API pública"; e a coluna deve dizer **MAVSDK-Python**, já que o MAVSDK
em C++ tem `MavlinkPassthrough`.
