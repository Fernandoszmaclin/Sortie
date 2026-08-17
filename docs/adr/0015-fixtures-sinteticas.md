# ADR 0015 — Fixtures sintéticas como estratégia primária

**Status:** aceito — 2026-08-16
**Contexto de referência:** P8, §17.1, §20, §21 (armadilhas 11, 28)

## Contexto

§17.1 mandava *"gravar uma vez, por SITL, e commitar"*. Duas coisas tornam isso inexecutável:

1. Por ADR 0012 não há SITL até a v0.3.
2. Busca dirigida não encontrou **nenhum** `.tlog` pequeno, comprovadamente de origem SITL e
   redistribuível. O `flight.tlog` do dronekit é Apache-2.0 mas de proveniência não documentada
   (provavelmente voo real) e 2,7 MB; o `test.BIN` do pymavlink é DataFlash, não MAVLink; as amostras do
   pyulog são ULog.

P8 proíbe a v0.1 depender de infra externa, e §20 proíbe coordenada de voo real no repositório — sobre
um binário opaco essa segunda regra é inauditável na prática.

## Decisão

`tools/make_fixtures.py` é **entregável da Etapa 0**, não contingência. Gera os `.tlog` com os próprios
encoders do pymavlink, explorando o enquadramento verificado (prefixo `uint64` big-endian de
microssegundos Unix + frame cru):

```python
from pymavlink.dialects.v20 import ardupilotmega as d
mav   = d.MAVLink(io.BytesIO(), srcSystem=1, srcComponent=1)
frame = mav.global_position_int_encode(...).pack(mav)
out.write(struct.pack('>Q', usec) + frame)
```

Sementes: **CMAC** (`-35.363261, 149.165230, 584 m, hdg 353` — verbatim de
`ardupilot/Tools/autotest/locations.txt`) e **Zurich Irchel** (`47.397742, 8.545594`). Ambas são
constantes publicadas de simulador, então **§20 é satisfeito por construção** e não por auditoria.

Produz `ardupilot_copter_takeoff.tlog`, `px4_quad_mission.tlog` e `malformed_frames.bin` — este último
com frames truncados, CRC corrompido, msgid não registrado e blocos de bytes aleatórios, tudo
determinístico sob seed fixa.

## Consequências

**O round-trip encoder→decoder é tautológico.** Ele prova que o pymavlink é consistente consigo mesmo,
não que nosso entendimento do formato de fio está certo: qualquer erro de escala, ordem de campo,
presença de campo de extensão ou convenção de sentinela é invisível porque as duas metades o
compartilham. E sob ADR 0012 não há SITL nem log real até a v0.3, então o parser — que §17.1 chama de
*"onde moram os bugs de decodificação"* — ficaria sem teste contra verdade de campo por toda a vida de
v0.1 e v0.2.

Mitigação obrigatória: `tests/fixtures/golden_frames.py` com frames hex verificados à mão (de
`mavlink.io` / vetores da c_library) para #0, #33, #24 e #147, mais os valores documentados como
literais separados. Afirmar que **nosso** encoder reproduz aqueles bytes e **nosso** decoder aqueles
valores. Isso converte auto-consistência em contrato externo, e é também o teste que pega um bump de
versão do pymavlink ou do dialeto.

**As fixtures precisam de um segundo componente.** Um gimbal em compid 154 emitindo HEARTBEAT com o bit
armado piscando, senão o filtro de `compid` de §7.2 nunca é exercitado e a armadilha 11 (*"gimbal vira
veículo; `armed` pisca sozinho"*) só aparece com tráfego real, na v0.2.

**Gate de aceite da Etapa 0:** `mavutil.mavlink_connection(fixture)` recupera campos byte-idênticos,
**e** ler a fixture ArduPilot com `dialect='common'` produz `MAVLink_unknown` para #193 — o que prova
que a armadilha 28 é detectável em vez de ser afirmação de fé.

**Helper compartilhado.** `tests/helpers/tlog.py::replay_tlog(path, dialect='ardupilotmega')`, com
verificação de que nenhum teste chama `mavutil.mavlink_connection` diretamente. Sem isso a armadilha 28
volta pela porta que §17.1 tentou fechar.

**Substituição na v0.3.** Quando o SITL chegar, gravar um `.tlog` real e substituir. A v0.1 e a v0.2 não
podem depender disso.
