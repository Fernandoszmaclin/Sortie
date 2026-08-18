"""Verifica o gerador de fixtures sintéticas (ADR 0015).

As fixtures não são versionadas: cada teste gera a sua num diretório temporário. O que
o repositório guarda é o gerador, e é ele que estes testes exercitam.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pytest
from pymavlink import mavutil

from tools import make_fixtures as mf


def _le(caminho: Path) -> list[Any]:
    """Relê todas as mensagens de um .tlog, na ordem em que foram gravadas."""
    conexao = mavutil.mavlink_connection(str(caminho))
    msgs = []
    while (m := conexao.recv_match(blocking=False)) is not None:
        msgs.append(m)
    return msgs


@pytest.fixture(scope="module")
def tlog(tmp_path_factory: pytest.TempPathFactory) -> Path:
    alvo = tmp_path_factory.mktemp("fixtures") / "ardupilot_copter_takeoff.tlog"
    mf.gera_tlog(alvo)
    return alvo


@pytest.fixture(scope="module")
def msgs(tlog: Path) -> list[Any]:
    return _le(tlog)


def test_contagem_por_msgid(msgs: list[Any]) -> None:
    """Nada se perde entre a agenda e o arquivo: contagem exata por mensagem."""
    assert Counter(m.get_type() for m in msgs) == Counter(mf.frames_esperados())


def test_seq_avanca_de_um_em_um_por_link(msgs: list[Any]) -> None:
    """`pack()` não incrementa o seq; o gerador incrementa, e por link (medido)."""
    por_link: defaultdict[tuple[int, int], list[int]] = defaultdict(list)
    for m in msgs:
        por_link[(m.get_srcSystem(), m.get_srcComponent())].append(m.get_seq())

    for link, seqs in por_link.items():
        for anterior, atual in zip(seqs[:-1], seqs[1:], strict=True):
            assert atual == (anterior + 1) % mf.SEQ_MODULO, f"seq quebrado em {link}"


def test_dois_componentes_presentes(msgs: list[Any]) -> None:
    """Sem um segundo componente o filtro de compid de §7.2 nunca é exercitado."""
    assert {(m.get_srcSystem(), m.get_srcComponent()) for m in msgs} == {(1, 1), (1, 154)}


def test_gimbal_pisca_armado_de_forma_independente(msgs: list[Any]) -> None:
    """Armadilha 11: o bit de armado do gimbal não é o do veículo."""
    armado = dialeto_flag()
    veiculo = [m for m in msgs if m.get_type() == "HEARTBEAT" and m.get_srcComponent() == 1]
    gimbal = [m for m in msgs if m.get_type() == "HEARTBEAT" and m.get_srcComponent() == 154]

    assert veiculo and gimbal
    # O gimbal alterna sozinho...
    assert len({bool(m.base_mode & armado) for m in gimbal}) == 2
    # ...e em algum instante discorda do veículo, que é o que expõe a armadilha.
    assert any(
        bool(v.base_mode & armado) != bool(g.base_mode & armado)
        for v, g in zip(veiculo, gimbal, strict=True)
    )


def dialeto_flag() -> int:
    from pymavlink.dialects.v20 import ardupilotmega as d

    return int(d.MAV_MODE_FLAG_SAFETY_ARMED)


def test_alt_msl_nao_e_alt_relativa(msgs: list[Any]) -> None:
    """`alt` é MSL e `relative_alt` é acima de home: trocá-los passa em round-trip."""
    posicoes = [m for m in msgs if m.get_type() == "GLOBAL_POSITION_INT"]
    home_mm = round(mf.CMAC_ALT_M * 1000)

    assert all(m.alt == m.relative_alt + home_mm for m in posicoes)
    # E existe pelo menos um instante em que os dois de fato diferem — sem isso a
    # asserção acima passaria com o veículo parado no solo o voo inteiro.
    assert any(m.relative_alt > 0 for m in posicoes)


def test_celulas_ausentes_usam_a_sentinela(msgs: list[Any]) -> None:
    """A contagem de células não se deriva do tamanho do array (C-12)."""
    baterias = [m for m in msgs if m.get_type() == "BATTERY_STATUS"]
    assert baterias

    for m in baterias:
        assert len(m.voltages) == 10
        assert all(v != mf.CELULA_AUSENTE for v in m.voltages[: mf.CELULAS])
        assert all(v == mf.CELULA_AUSENTE for v in m.voltages[mf.CELULAS :])


def test_atitude_em_radianos_e_heading_em_centigrados(msgs: list[Any]) -> None:
    """Duas unidades diferentes para a mesma grandeza, nas duas mensagens."""
    atitudes = [m for m in msgs if m.get_type() == "ATTITUDE"]
    posicoes = [m for m in msgs if m.get_type() == "GLOBAL_POSITION_INT"]

    # ATTITUDE.yaw é -pi..+pi, então 353° aparece como -7°.
    assert all(math.isclose(m.yaw, math.radians(-7.0), abs_tol=1e-6) for m in atitudes)
    # GLOBAL_POSITION_INT.hdg é 0..35999 cdeg, então o mesmo ângulo vira 35300.
    assert all(m.hdg == round(mf.CMAC_HDG_DEG * 100) for m in posicoes)


def test_geracao_e_determinista(tmp_path: Path) -> None:
    """Mesmo comando, mesmos bytes: sem isso não há passo de regeneração no CI."""
    a, b = tmp_path / "a.tlog", tmp_path / "b.tlog"
    mf.gera_tlog(a)
    mf.gera_tlog(b)
    assert a.read_bytes() == b.read_bytes()


def test_gate_de_aceite_passa(tlog: Path) -> None:
    """O gate do próprio gerador roda limpo sobre o que ele produziu."""
    mf.confere(tlog)
