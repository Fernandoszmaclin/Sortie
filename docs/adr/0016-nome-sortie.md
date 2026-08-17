# ADR 0016 — O produto se chama Sortie

**Status:** aceito — 2026-08-16
**Substitui:** o nome "Mission Control Hub Desktop" e o §2.4 que o justificava
**Contexto de referência:** §1, §2.1, §2.3, §2.4

## Contexto

O nome anterior tinha dois problemas, e o gatilho de renomeação previsto em §2.4 não era nenhum deles.

**§2.4 previa:** *"se o protocolo de missão for cortado do escopo, o projeto deve ser renomeado"*. Esse
gatilho não disparou — o protocolo está na v0.4, adiado e não cortado.

**O problema real é de categoria.** "Mission Control" é o termo consagrado para estação de controle de
solo, que é precisamente o que a especificação passa três seções negando: §1 abre com "não é uma estação
de controle de solo"; §2.1 classifica toda capacidade de GCS como "requisito de paridade, nunca
argumento de venda"; §2.3 gasta uma frase dizendo "não compete com o QGroundControl". Um nome que anuncia
a categoria errada obriga o documento a desfazer na primeira página o que a capa fez. "Hub", em paralelo,
sugere agregação e roteamento — que é o MAVProxy.

**Havia um custo de escopo junto.** O nome antigo *forçava* o protocolo de missão para dentro do escopo,
por §2.4. Um nome que não promete controle devolve essa liberdade.

## Decisão

O produto se chama **Sortie**.

Uma *sortie* é uma execução de missão — uma saída, do armar ao desarmar. É exatamente a unidade que este
software trata como objeto de primeira classe: algo que se grava, se indexa, se reabre e se compara
contra outra. Nenhuma das ferramentas de §2.1 faz isso; todas tratam o voo como sessão a observar, não
como registro a confrontar.

Esquema de nomes:

| Item | Valor |
|---|---|
| Repositório | `sortie/` |
| Executável | `sortie` / `sortie.exe` |
| Diretório de dados | `Sortie/` sob `AppLocalDataLocation` |
| Logs | `sortie.log`, `sortie.crash` |
| Documento canônico | `docs/sortie.md` |
| Pacote Python interno | `app/` (inalterado) |

## Consequências

**O gatilho de renomeação muda.** Deixa de ser "se o protocolo de missão for cortado" e passa a ser
**"se o produto deixar de comparar execuções"**. O nome promete uma coisa só, e é a de §2.2: a sortie é
comparável.

**O protocolo de missão (§9.5) continua requisito de primeira classe**, mas agora pelo mérito próprio em
§11.2 — comparar N execuções da mesma missão exige conhecer o plano, e `mission_plan_hash` é o que agrupa
execuções comparáveis. A amarração entre o protocolo e o nome era artefato do nome antigo e foi desfeita
com ele.

**Colisões de namespace, verificadas:**

| Namespace | Estado | Impacto |
|---|---|---|
| PyPI `sortie` | ocupado — formatador de `pyproject.toml`, v0.1.1 | nenhum: §18 distribui ZIP de Release, o projeto não publica no PyPI |
| Usuário GitHub `sortie` | ocupado — Jonas Termansen | nenhum: o repositório mora em `<usuário>/sortie` |
| PyPI `sortiebench` | livre | reserva, caso um nome no PyPI passe a ser necessário |

O custo residual é um papercut: quem procurar no PyPI encontra um formatador de TOML. Aceito.

**Não verificado:** marca registrada. "Sortie" tem uso militar corrente; se o projeto algum dia for
comercializado, checar antes.
