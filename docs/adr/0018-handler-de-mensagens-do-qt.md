# ADR 0018 — Handler de mensagens do Qt, e a guarda de log verboso

**Status:** aceito — 2026-08-18
**Contexto de referência:** C-4, §18.1, §21 (armadilha 19), ADR 0012

## Contexto

O `qInstallMessageHandler` de `main.py` existe para que aviso do Qt caia em `sortie.log`. O passo de
selftest empacotado do CI o expôs a um defeito do PySide6 que torna o handler inviável sob log verboso.

**O crash.** Com um handler Python instalado e log verboso ligado, o processo morre com `0xC0000005`
(violação de acesso). Medido em Windows 11 26200, PySide6 6.11.1 / Qt 6.11.1 / Python 3.12.13:

| Condição | Resultado |
|---|---|
| Sem handler, `QT_DEBUG_PLUGINS=1` | 0 |
| Handler, volume normal | 0 |
| Handler, `QT_DEBUG_PLUGINS=1` | `0xC0000005`, 12 de 12 |
| Handler, `QT_LOGGING_RULES=qt.*=true` | `0xC0000005` |

Sempre na 26ª mensagem, sempre na thread principal. Reproduz com o interpretador direto, então não é
defeito de empacotamento, e reproduz com `QT_QPA_PLATFORM` tanto em `windows` quanto em `offscreen`.

**Não há conserto do lado Python.** Um handler cujo corpo é `pass` crasha igual, e instalar depois do
`QApplication` crasha igual. A falha está no despacho do callback, não no que o callback faz.

**Os gatilhos são do usuário.** `QT_LOGGING_RULES` é a primeira coisa que um guia de depuração de Qt
manda ligar. Hoje, quem tentasse diagnosticar o Sortie derrubaria o Sortie — e em silêncio, porque nem
o `faulthandler` registra: `sortie.crash` fica com zero byte.

**Remover o handler não é opção.** No Windows o Qt escreve em `OutputDebugString` quando o stderr não é
console, o que num build `--windowed` e em qualquer job de CI com redirecionamento é sempre o caso.
Medido: sem handler e com `QT_DEBUG_PLUGINS=1`, stderr e stdout somam zero byte. O handler não é
conveniência — é a única via pela qual mensagem de Qt chega a um arquivo legível depois. Em Linux ele é
redundante para diagnóstico, porque lá o stderr recebe as mensagens; a assimetria pertence ao contrato
de portabilidade do ADR 0012.

## Decisão

`main.py` detecta log verboso do Qt no bootstrap e, quando ativo, **não instala** o handler, cedendo o
canal ao handler nativo do Qt. A decisão é registrada em `sortie.log`, senão vira um segundo modo de
operação invisível.

A detecção é uma tabela de variável para os valores que significam desligado, **não** uma checagem
uniforme de presença: as semânticas divergem, e ambas foram medidas.

| Variável | Desligada quando | Medido |
|---|---|---|
| `QT_DEBUG_PLUGINS` | ausente, vazia ou `0` | `0` não crasha, `1` crasha |
| `QT_LOGGING_RULES` | ausente ou vazia | `''` não crasha, `qt.*=true` crasha |
| `QT_LOGGING_CONF` | ausente ou vazia | por analogia: carrega regras de arquivo |

`QT_FORCE_STDERR_LOGGING` fica de fora: muda o destino da saída, não o volume.

A guarda **não é contorno**. Quando o usuário liga o modo verboso do Qt ele está pedindo a saída nativa
do Qt, e um handler que a intercepta faz o oposto do pedido. Sair da frente é o comportamento correto, e
continua correto mesmo depois de o defeito do PySide6 ser consertado.

## Consequências

**Perde-se diagnóstico de Qt em `sortie.log` justamente sob log verboso.** Custo aceitável: nesse modo o
Qt escreve por conta própria, e no Windows a saída continua alcançável por depurador anexado.

**A cobertura é a das variáveis previstas.** Outro caminho de volume alto reabre o crash. Mitigação em
`tests/test_bootstrap.py`, que lança o app em subprocesso — o modo de falha é morte do processo, e não
existe `except` para isso — e afirma saída zero sob cada gatilho.

**O grep do CI depende do handler.** O passo de selftest de `ci.yml` procura a falha de plugin em
`sortie.log`, onde ela só chega através do handler. Desfazer esta decisão pelo lado errado — removendo o
handler em vez de guardá-lo — quebra aquela verificação sem quebrar nenhum teste.

**Fica em aberto o defeito upstream, e não foi reportado.** Existe um rascunho de relatório e uma
reprodução mínima de oito linhas; submeter ao projeto PYSIDE está pendente. Enquanto não houver número
de issue aqui, ninguém consegue verificar se a guarda já pode sair — então ela sai apenas quando houver
versão corrigida e o teste de regressão passar sem ela.
