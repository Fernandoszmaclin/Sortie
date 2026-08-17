# ADR 0012 — Desenvolvimento Windows-first até a v0.3, com o contrato de portabilidade invertido no CI

**Status:** aceito — 2026-08-16
**Substitui parcialmente:** ADR 0011 (Linux-first)
**Contexto de referência:** P11, §13.1, §16, §21 (29-35)

## Contexto

O ADR 0011 e o §13.1 fixaram desenvolvimento Linux-first, por três razões concretas: ArduPilot SITL, PX4
SITL, Gazebo e ROS 2 rodam nativamente; o filesystem case-sensitive expõe bugs de import na hora; e o
build de containers é o mesmo ambiente do desenvolvimento.

A máquina de trabalho é Windows 11 sem distro Linux instalada — o WSL tem apenas `docker-desktop`.

O fato decisivo: **a v0.1.0 inteira é declarada 100 % offline em §16.** Mock, SQLite, replay, alertas,
export e empacotamento. Nada nela toca SITL, Gazebo ou ROS 2 — que é exatamente o conjunto que motiva o
Linux. A dependência de Linux só aparece na v0.3.0.

## Decisão

Desenvolver v0.1 e v0.2 em **Windows nativo**. Instalar Ubuntu 22.04 no WSL2 como **gate de entrada da
v0.3.0**, com `networkingMode=mirrored` no `.wslconfig` (armadilha 2).

O contrato de portabilidade **não é relaxado — os papéis no CI são invertidos.** As regras C1-C11 de
§13.1 seguem integrais; muda apenas qual runner é gate e qual é guarda:

| | ADR 0011 | Este ADR |
|---|---|---|
| Gate do `ci.yml` | `ubuntu-latest` | `windows-latest` |
| Guarda de portabilidade (C12) | `windows-latest` | `ubuntu-latest` |
| `release.yml` | `ubuntu-22.04` + `windows-latest` | inalterado |

Falha do guarda **quebra o build**, como o gate.

## Consequências

**A troca é favorável quanto a quando cada armadilha aparece.** Quatro das cinco armadilhas de
portabilidade catalogadas em §21 — 29 (encoding ANSI), 30 (CSV `\r\r\n`), 32 (`SO_REUSEPORT` inexistente),
33 (renomear arquivo aberto) — **só se manifestam no Windows**. Desenvolver lá as expõe na primeira
execução, em vez de deixá-las para o job de guarda.

**A que se perde é C11**, caso em caminho e nome de módulo, invisível num filesystem case-insensitive.
Ela é justamente a detectável por um job barato, e passa a ser a função do `ubuntu-latest`. Como o job
só a pega se o módulo for de fato importado, acrescentar um teste que percorre `pkgutil.walk_packages`
e importa **todo** módulo sob `app/`, inclusive `app/ui`, que fica fora do gate de cobertura.

**Um critério da Etapa 0 é adiado, não omitido:** "HEARTBEAT vindo do SITL local" (§16, Etapa 0, item 1)
move para o gate de entrada da v0.3.0. O plano B de §16 — fixture offline — cobre a demonstração até lá,
o que é P8 funcionando como projetado.

**A release da v0.1.0 é Windows-only.** Publicar binário Linux nunca executado é pior que não publicar,
e contradiz a política da própria especificação, que adia a release Windows até haver máquina Windows; o
caso espelho merece a mesma resposta. O build Linux permanece como job **build-only** no `ci.yml`, com
artefato retido e nunca anexado a uma Release, para pegar regressão de empacotamento continuamente em
vez de na hora da tag.

**Fica em aberto até haver máquina Linux:** comportamento Wayland × X11 (armadilha 35), coleta do plugin
de plataforma no Linux, e a versão mínima de glibc verificada na prática.
