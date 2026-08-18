"""Gera as fixtures sintéticas de teste a partir dos encoders do pymavlink (ADR 0015).

Não existe `.tlog` público, pequeno e comprovadamente de origem SITL que se possa
redistribuir, e §20 proíbe coordenada de voo real no repositório. As sementes aqui são
constantes publicadas de simulador, então §20 fica satisfeito por construção.
"""

from __future__ import annotations

import argparse
import io
import math
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as dialeto

REPO = Path(__file__).resolve().parents[1]
DESTINO = REPO / "tests" / "fixtures"

# Semente pública de ardupilot/Tools/autotest/locations.txt.
CMAC_LAT_DEG = -35.363261
CMAC_LON_DEG = 149.165230
CMAC_ALT_M = 584.0
CMAC_HDG_DEG = 353.0

DURACAO_S = 60.0

# Taxas diferentes de propósito: telemetria real chega desencontrada, e é essa
# propriedade que a fixture existe para exercitar. Taxa única apagaria isso.
TAXAS_HZ: dict[str, float] = {
    "heartbeat": 1.0,
    "sys_status": 2.0,
    "attitude": 10.0,
    "global_position_int": 5.0,
    "battery_status": 1.0,
}

# Instante de referência fixo: o gerador tem de ser determinístico, então nada de
# time.time() aqui. 2026-01-01T00:00:00Z em microssegundos Unix.
T0_UNIX_US = 1_767_225_600_000_000

# Fases da decolagem, em segundos. Somam DURACAO_S.
T_ARMA_S = 5.0
T_TOPO_S = 20.0
T_DESCE_S = 45.0
ALT_CRUZEIRO_M = 30.0

# Bateria 3S de LiPo: 4,2 V por célula cheia, 3,87 V ao fim do perfil.
CELULAS = 3
V_CELULA_CHEIA = 4.2
V_CELULA_FIM = 3.87

# uint8 no fio: o seq dá a volta em 256.
SEQ_MODULO = 256

# Sentinela de célula ausente em BATTERY_STATUS.voltages (uint16).
CELULA_AUSENTE = 0xFFFF


@dataclass
class Link:
    """Um par (sysid, compid) com sequência própria.

    O `seq` é por link — o gimbal tem contador independente do veículo. E `pack()`
    lê `mav.seq` mas nunca o incrementa: quem gerencia somos nós (medido).
    """

    sysid: int
    compid: int
    seq: int = 0
    mav: dialeto.MAVLink = field(init=False)

    def __post_init__(self) -> None:
        self.mav = dialeto.MAVLink(io.BytesIO(), srcSystem=self.sysid, srcComponent=self.compid)

    def pack(self, msg: Any) -> bytes:
        """Serializa carimbando o próximo `seq` deste link."""
        self.mav.seq = self.seq
        frame: bytes = msg.pack(self.mav)
        self.seq = (self.seq + 1) % SEQ_MODULO
        return frame


class TlogWriter:
    """Escreve no formato .tlog: prefixo uint64 big-endian de microssegundo Unix + frame."""

    def __init__(self, caminho: Path) -> None:
        self.caminho = caminho
        self._f: Any = None
        self.frames = 0

    def __enter__(self) -> TlogWriter:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.caminho.open("wb")
        return self

    def __exit__(self, *_: object) -> None:
        self._f.close()

    def escreve(self, t_unix_us: int, frame: bytes) -> None:
        self._f.write(struct.pack(">Q", t_unix_us) + frame)
        self.frames += 1


@dataclass
class Estado:
    """Estado do veículo num instante do voo simulado. Unidades do SI, não do fio."""

    t_s: float
    armado: bool
    alt_rel_m: float
    roll_rad: float
    pitch_rad: float
    yaw_rad: float
    tensao_v: float
    corrente_a: float
    bateria_pct: int


def perfil(t_s: float) -> Estado:
    """Estado do veículo em `t_s` segundos de voo.

    Quatro fases: solo desarmado, subida a 2 m/s, cruzeiro e descida. Grandezas em SI —
    a conversão para a unidade do fio acontece nos construtores, e só lá.
    """
    if t_s < T_ARMA_S:
        alt_rel_m, corrente_a = 0.0, 0.6
    elif t_s < T_TOPO_S:
        fracao = (t_s - T_ARMA_S) / (T_TOPO_S - T_ARMA_S)
        alt_rel_m, corrente_a = ALT_CRUZEIRO_M * fracao, 25.0
    elif t_s < T_DESCE_S:
        alt_rel_m, corrente_a = ALT_CRUZEIRO_M, 18.0
    else:
        fracao = (t_s - T_DESCE_S) / (DURACAO_S - T_DESCE_S)
        alt_rel_m, corrente_a = ALT_CRUZEIRO_M * (1.0 - fracao), 10.0

    # Descarga linear no tempo. Não é modelo de bateria — é dado plausível e monotônico,
    # que é o que os alertas do Bloco D precisam para disparar de forma determinística.
    fracao_voo = t_s / DURACAO_S
    v_celula = V_CELULA_CHEIA - (V_CELULA_CHEIA - V_CELULA_FIM) * fracao_voo

    # Oscilação pequena de atitude: dado constante não exercita gráfico nem derivada.
    armado = t_s >= T_ARMA_S
    amplitude = math.radians(2.0) if armado else 0.0

    return Estado(
        t_s=t_s,
        armado=armado,
        alt_rel_m=alt_rel_m,
        roll_rad=amplitude * math.sin(2 * math.pi * 0.2 * t_s),
        pitch_rad=amplitude * math.sin(2 * math.pi * 0.13 * t_s),
        # ATTITUDE.yaw é -pi..+pi, então 353° é -7°, e não 353° positivos.
        yaw_rad=math.radians(CMAC_HDG_DEG - 360.0),
        tensao_v=v_celula * CELULAS,
        corrente_a=corrente_a,
        bateria_pct=round(100 - 22 * fracao_voo),
    )


def constroi_heartbeat(estado: Estado, link: Link, e_gimbal: bool) -> Any:
    """HEARTBEAT do veículo ou do gimbal.

    O gimbal pisca o bit de armado a cada segundo, de propósito: é a armadilha 11 do §21
    (*"gimbal vira veículo; armed pisca sozinho"*). Sem isso, o filtro de compid de §7.2
    nunca é exercitado e o defeito só apareceria com tráfego real, na v0.2.
    """
    if e_gimbal:
        armado = int(estado.t_s) % 2 == 0
        tipo, autopiloto = dialeto.MAV_TYPE_GIMBAL, dialeto.MAV_AUTOPILOT_INVALID
    else:
        armado = estado.armado
        tipo, autopiloto = dialeto.MAV_TYPE_QUADROTOR, dialeto.MAV_AUTOPILOT_ARDUPILOTMEGA

    base_mode = dialeto.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
    if armado:
        base_mode |= dialeto.MAV_MODE_FLAG_SAFETY_ARMED

    return link.mav.heartbeat_encode(
        type=tipo,
        autopilot=autopiloto,
        base_mode=base_mode,
        custom_mode=0,
        system_status=dialeto.MAV_STATE_ACTIVE if armado else dialeto.MAV_STATE_STANDBY,
    )


def constroi_sys_status(estado: Estado, link: Link) -> Any:
    return link.mav.sys_status_encode(
        onboard_control_sensors_present=0,
        onboard_control_sensors_enabled=0,
        onboard_control_sensors_health=0,
        load=350,  # d%: decipercento, faixa 0..1000. 350 = 35 % de carga do laço.
        voltage_battery=round(estado.tensao_v * 1000),  # mV
        current_battery=round(estado.corrente_a * 100),  # cA
        battery_remaining=estado.bateria_pct,  # %
        drop_rate_comm=0,
        errors_comm=0,
        errors_count1=0,
        errors_count2=0,
        errors_count3=0,
        errors_count4=0,
    )


def constroi_attitude(estado: Estado, link: Link) -> Any:
    return link.mav.attitude_encode(
        time_boot_ms=round(estado.t_s * 1000),
        roll=estado.roll_rad,  # rad, não grau
        pitch=estado.pitch_rad,
        yaw=estado.yaw_rad,
        rollspeed=0.0,
        pitchspeed=0.0,
        yawspeed=0.0,
    )


def constroi_global_position_int(estado: Estado, link: Link) -> Any:
    return link.mav.global_position_int_encode(
        time_boot_ms=round(estado.t_s * 1000),
        lat=round(CMAC_LAT_DEG * 1e7),
        lon=round(CMAC_LON_DEG * 1e7),
        # MSL: a altitude de home somada à altura acima dela. Dado diferente do de baixo.
        alt=round((CMAC_ALT_M + estado.alt_rel_m) * 1000),
        relative_alt=round(estado.alt_rel_m * 1000),
        vx=0,
        vy=0,
        vz=0,
        hdg=round(math.degrees(estado.yaw_rad) % 360 * 100),  # cdeg, 0..35999
    )


def constroi_battery_status(estado: Estado, link: Link) -> Any:
    """BATTERY_STATUS de uma bateria 3S.

    O array `voltages` tem 10 posições fixas. A regra do protocolo é que células acima
    da contagem real levem UINT16_MAX — a contagem **não** se deriva do tamanho do array.
    É a primeira sentinela de array do projeto, e o caso que o C-12 usa para exigir
    tabela por campo em vez de varredura global por 65535.
    """
    mv_celula = round(estado.tensao_v / CELULAS * 1000)
    voltages = [mv_celula] * CELULAS + [CELULA_AUSENTE] * (10 - CELULAS)

    return link.mav.battery_status_encode(
        id=0,
        battery_function=dialeto.MAV_BATTERY_FUNCTION_ALL,
        type=dialeto.MAV_BATTERY_TYPE_LIPO,
        temperature=2500,  # cdegC = 25,0 °C. INT16_MAX seria "desconhecido".
        voltages=voltages,  # mV por célula
        current_battery=round(estado.corrente_a * 100),  # cA
        current_consumed=-1,  # -1: autopiloto não estima
        energy_consumed=-1,
        battery_remaining=estado.bateria_pct,
    )


def agenda(duracao_s: float, taxas_hz: dict[str, float]) -> list[tuple[float, str]]:
    """Devolve [(t_s, nome)] ordenado, cada mensagem na sua própria taxa."""
    eventos: list[tuple[float, str]] = []
    for nome, hz in taxas_hz.items():
        passo = 1.0 / hz
        t = 0.0
        while t < duracao_s:
            eventos.append((t, nome))
            t += passo
    eventos.sort(key=lambda e: e[0])
    return eventos


def frames_esperados() -> dict[str, int]:
    """Contagem esperada por mensagem. HEARTBEAT sai duplicado: veículo e gimbal."""
    contagem = Counter(nome for _, nome in agenda(DURACAO_S, TAXAS_HZ))
    esperado = {nome.upper(): n for nome, n in contagem.items()}
    esperado["HEARTBEAT"] *= 2
    return esperado


def gera_tlog(caminho: Path) -> int:
    """Gera um .tlog completo. Devolve a contagem de frames escritos."""
    veiculo = Link(sysid=1, compid=1)
    gimbal = Link(sysid=1, compid=154)

    construtores = {
        "sys_status": constroi_sys_status,
        "attitude": constroi_attitude,
        "global_position_int": constroi_global_position_int,
        "battery_status": constroi_battery_status,
    }

    with TlogWriter(caminho) as saida:
        for t_s, nome in agenda(DURACAO_S, TAXAS_HZ):
            estado = perfil(t_s)
            t_unix_us = T0_UNIX_US + round(t_s * 1e6)

            if nome == "heartbeat":
                # Os dois componentes batem no mesmo instante, cada um no próprio link.
                for link, e_gimbal in ((veiculo, False), (gimbal, True)):
                    saida.escreve(t_unix_us, link.pack(constroi_heartbeat(estado, link, e_gimbal)))
            else:
                msg = construtores[nome](estado, veiculo)
                saida.escreve(t_unix_us, veiculo.pack(msg))

    return saida.frames


def confere(caminho: Path) -> None:
    """Gate de aceite: relê o arquivo e afirma o que precisa ser verdade.

    O round-trip encoder/decoder é tautológico (ADR 0015) — este gate não prova que o
    nosso entendimento do formato está certo, prova que o arquivo é legível e que nada
    se perdeu entre escrita e leitura. A prova externa é `golden_frames.py`.
    """
    conexao = mavutil.mavlink_connection(str(caminho))

    contagem: Counter[str] = Counter()
    seqs: defaultdict[tuple[int, int], list[int]] = defaultdict(list)
    while (msg := conexao.recv_match(blocking=False)) is not None:
        contagem[msg.get_type()] += 1
        seqs[(msg.get_srcSystem(), msg.get_srcComponent())].append(msg.get_seq())

    esperado = frames_esperados()
    if contagem != Counter(esperado):
        raise AssertionError(f"contagem divergente: lido={dict(contagem)} esperado={esperado}")

    # O seq tem de avançar de um em um dentro de cada link, dando a volta em 256.
    for (sysid, compid), lista in seqs.items():
        for anterior, atual in zip(lista[:-1], lista[1:], strict=True):
            if atual != (anterior + 1) % SEQ_MODULO:
                raise AssertionError(f"seq quebrado em ({sysid},{compid}): {anterior} -> {atual}")

    if set(seqs) != {(1, 1), (1, 154)}:
        raise AssertionError(f"componentes inesperados: {sorted(seqs)}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="make_fixtures")
    parser.add_argument("--destino", type=Path, default=DESTINO)
    args = parser.parse_args()

    alvo = args.destino / "ardupilot_copter_takeoff.tlog"
    n = gera_tlog(alvo)
    confere(alvo)
    print(f"gerado: {alvo}  ({n} frames, {alvo.stat().st_size} bytes)")
    print("gate de aceite: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
