# ADR 0013 — `perf_counter_ns` como relógio, e `t_recv_ns` como eixo do cursor de replay

**Status:** aceito — 2026-08-16
**Contexto de referência:** §7.1, §11.1, §12, §21 (armadilhas 3, 4, 23)

## Contexto

Dois problemas independentes, ambos no mesmo campo.

**1. Resolução.** §7.1 definia `t_recv_mono_ns` como `time.monotonic_ns()`. No Python 3.12 em Windows —
que por ADR 0012 é a plataforma de desenvolvimento e o gate do CI — `monotonic` é `GetTickCount64`, com
resolução de **15,625 ms**. O CPython só trocou para `QueryPerformanceCounter` no 3.13 (gh-88494).

Esse campo é simultaneamente a chave de ordenação, o detector de gap, o instrumento da NFR
"latência recebimento → pixel < 100 ms (p95)" de §12, e o relógio do cursor de replay. A 15,6 ms o
orçamento de 100 ms é medido com ±16 % de erro de quantização, e o intervalo de coalescing de 10-20 Hz
tem 3 a 6 ticks de largura — estatística de jitter vira ruído.

**2. Eixo.** §7.1 declarava *"o replay roda sobre `t_boot_ms`"* e §11.1 fixava o cursor em
`(session_id, t_boot_ms)`. Mas §7.5 chaveava quatro das oito famílias em tempo de recepção, e sob
`--speedup` os dois eixos divergem por construção. Pior: **seis das oito famílias não têm timestamp de
boot nenhum** (ver ADR 0014). Um cursor em `t_boot_ms` não consegue endereçá-las. **A especificação era
internamente inimplementável aqui** — isto é correção de defeito, não desvio.

## Decisão

**Relógio:** `time.perf_counter_ns()` em todo produtor de tempo de recepção. É `QueryPerformanceCounter`
no Windows e `CLOCK_MONOTONIC` no Linux, monotônico nos dois, ~100 ns, API idêntica em 3.11-3.14. O
campo passa a se chamar `t_recv_ns` — o nome antigo implicava `time.monotonic`.

Isso torna a resolução **ortogonal à versão do Python** e preserva o pin 3.12, que é justificado
independentemente pela disponibilidade de wheel do pymavlink.

**Contrato:** Protocol `Clock` **e** implementações (`RealClock`, `FakeClock`, `assert_clock_resolution`)
todos em `app/models/clock.py`. Métodos: `perf_ns()`, `wall_us()`, `sleep_until(deadline_ns)`. A camada
`models` é a única que adaptadores e core podem importar sob A6, e o módulo é `time` puro — não importa
nada do projeto. **`app/core/clock.py` não existe.**

**Eixo do cursor:** `t_recv_ns`, único presente em todas as tabelas de amostra. `t_boot_ms` +
`boot_epoch` permanecem como eixo de **correlação** e de alinhamento entre execuções (§11.2), imune a
`--speedup`.

## Consequências

**O epoch é local ao processo.** Aritmética de `t_recv_ns` entre sessões, ou através da fronteira do
bridge ROS 2 (§9.8), é proibida — só diferenças dentro de uma sessão têm significado. Guarda no
`SampleReader`.

**`perf_counter` conta durante suspensão do host.** O cursor precisa de clamp: se `dt > 1 s`, reancorar
em vez de avançar, e emitir evento visível (P9). Uma tampa fechada no meio do replay voltaria com delta
de horas e a busca binária saltaria para o fim da sessão. O mesmo clamp cobre breakpoint de debugger e
pausa de GC.

**Ritmo e rótulo continuam em tempo de veículo**, escalados por sessão. A escala é grandeza **derivada**
e carrega a marca disso (P1) — nunca gravada como se fosse o `--speedup` declarado. Estimar por segmento
de `boot_epoch`, nunca por regressão global: o lockstep do PX4 para o relógio do veículo quando o
simulador trava, então a relação é linear por partes.

**Contrato verificado, não assumido.** Passo de CI nos dois runners:
`assert time.get_clock_info('perf_counter').resolution <= 1e-6`. É o teste que teria pego o defeito, e
a grandeza não é pinável em `pyproject.toml` — é propriedade do build do interpretador.

**Errata em §21:** a prevenção da armadilha 4 deixa de ser "replay sobre `t_boot_ms`" e passa a ser
"cursor em `t_recv_ns`, com ritmo escalado por sessão; nunca tempo de parede não escalado".
