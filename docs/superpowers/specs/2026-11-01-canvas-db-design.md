# O canvas do ratt db: o diagrama das tabelas, desenhado e editado no terminal

Data: 2026-11-01 · Branch: `db` · Fase 1 de 2 (a fase 2 é o filtro de linhas; ver o fim)

## Para que serve

Hoje o `ratt db` permite **ver** o banco (a tela: árvore, dados, estrutura, relações, SQL) e
**rodar** SQL escrito à mão (`ratt db run`). Quem sabe só o básico de SQL — `CREATE`, `INSERT`,
`DELETE`, `WHERE` — não tem como *construir* a estrutura de um banco sem escrever cada comando,
nem como enxergar de uma vez como as tabelas se ligam.

O canvas é um diagrama das tabelas com setas de chave estrangeira, no estilo de um diagrama ER
(como o de `users` / `users_roles` / `roles` / `profiles` que motivou a ideia), **editável**:
criar tabela, coluna, ligação, apagar — e cada gesto vira o SQL que faria a mesma coisa, sempre
à vista. Ele serve primeiro para **aprender** (desenhar e ler o SQL que saiu) e, com o tempo,
para **cuidar de um banco de verdade** de forma rápida e sem erro.

O princípio da casa vale aqui: **avisa, não decide**. O canvas mostra o que uma mudança vai
fazer e o que pode dar errado; quem decide é a pessoa.

## Objetivos

1. Mostrar as tabelas de um schema como caixas (colunas, tipos, PK, FK) ligadas por setas com a
   notação pé de galinha, refletindo **o que o banco realmente garante** (lido do catálogo).
2. Editar a estrutura pelo desenho: as operações da lista "O que entra na fase 1".
3. Toda edição entra num **rascunho**: aparece no desenho como mudança pendente e como SQL num
   painel sempre visível. Nada vai para o banco sem a pessoa aplicar.
4. Aplicar o rascunho pelo mesmo caminho do `ratt db run`: uma transação, o erro apontando o
   comando, e a opção de ensaio (`--dry-run`: roda e desfaz).
5. Avisos que ensinam (lista em "Avisos"), no espírito dos avisos de vazio e espaço sobrando que
   o ratt já dá.
6. Continuar atualizando sozinho quando o banco muda por fora (`ratt db run --watch`, `psql`),
   como a tela atual já faz.

## Fora do escopo (não-objetivos)

- **O filtro de linhas** (apagar/alterar dados com `WHERE` montado visualmente) — é a fase 2.
- **Usuários e permissões** (`CREATE ROLE`, `GRANT`) em forma visual — um canvas próprio, depois.
- Editar views, funções, triggers, políticas, sequências: aparecem na árvore da tela atual como
  hoje; o canvas da fase 1 desenha **só tabelas**.
- Mudar o tipo de uma coluna que já tem dados (`ALTER COLUMN ... TYPE ... USING`) — fica para
  depois; na fase 1 o tipo só é escolhido ao criar a coluna.
- Ferramenta de migração (versões, histórico de mudanças aplicadas). O rascunho é um `.sql`, e
  só.
- Qualquer versão web.
- Agilidade de uso avançado (lotes, macros, atalhos compostos) — refinamento posterior.

## Anti-objetivos (o que tornaria isto um fracasso mesmo funcionando)

- Algo ir para o banco **sem a pessoa ter visto o SQL** antes.
- O desenho **mentir** sobre o banco: mostrar uma ligação, cardinalidade ou coluna que o catálogo
  não confirma (exceto as mudanças pendentes do rascunho, que aparecem marcadas como tais).
- **Perder um rascunho** (fechar a tela, trocar de banco, o banco mudar por fora).
- Uma operação destrutiva sem prévia do que ela leva junto.
- Virar uma ferramenta que esconde o SQL e cria dependência dela: o `.sql` gerado tem de ser
  legível e utilizável fora do ratt.

## Restrições

- Python ≥ 3.9 (`pyproject.toml`), Textual ≥ 1.0 (instalado: 8.2.8), psycopg 3 (instalado:
  3.3.6). Sem dependências novas.
- Terminal: alvo principal Konsole (mouse e cores exatas). Linux.
- Interface em português e inglês: todo texto novo entra em `ratt/db/idioma.py` (`TEXTOS`, com
  `pt` e `en`). O SQL é sempre SQL.
- Identificadores sempre montados com `psycopg.sql.Identifier`/`sql.SQL`, nunca por f-string.
- A tela atual (`ratt/db/tela.py`) continua funcionando igual; o canvas é acrescentado a ela.

## Abordagens consideradas

| | Como funciona | Troca |
|---|---|---|
| **A. Desenhista próprio que gera texto** *(escolhida)* | Uma função pura recebe o modelo (tabelas, ligações, posições, rascunho) e devolve as linhas de texto do canvas. Um widget fino do Textual só exibe e traduz clique/arrasto em "qual caixa, qual coluna". Edição por formulários sobre o desenho. | Controle total e testes sem abrir a tela; o roteamento das setas é código nosso. |
| B. Cada tabela é um widget do Textual + camada de setas | Caixas são componentes reais com foco e edição dentro; outra camada desenha as linhas e as sincroniza. | Edição dentro da caixa de graça; duas camadas frágeis de manter alinhadas, testes por fotografia de tela, muitos widgets em banco grande. |
| C. Mapa automático, sem arrastar | Tabelas organizadas sozinhas em colunas pela direção das FKs. | Simples e escala bem; deixa de ser um canvas. |

**Escolhida: A**, pela pessoa, depois de comparar A e B lado a lado
(https://claude.ai/artifact/9ogSBhFC24d2pCBMV6DEt8). A posição automática de C entra em A como
ponto de partida antes de a pessoa arrastar.

## O que foi decidido

### Quem usa e como

- Primeiro público: alguém que sabe o básico de SQL e quer aprender vendo o efeito de cada
  mudança. Depois, a mesma ferramenta para cuidar de um banco real.
- O fluxo é **desenhar primeiro, ler o SQL depois**. O SQL gerado fica sempre à vista; para casos
  que a TUI não cobre, o SQL continua sendo o caminho: o rascunho é **exportado** como `.sql`, a
  pessoa edita no VS Code e roda com `ratt db run`; o canvas mostra o resultado depois de
  aplicado (pelo catálogo).
- TUI no terminal, não web.

### A tela

```
ratt db · estudos › Diagrama · public                                  rascunho: 2
users─────────────────┐        users_roles───────┐        roles─────────┐
PK id           int8 ├┼──────<┤PK user_id   int8 │        PK id     int8│
   first_name   text │        PK role_id   int8 ├>──────┼┤   name   text│
   email        text │        └──────────────────┘        └──────────────┘
   + birth      date │   ← pendente (verde)
└──────────┬─────────┘        profiles──────────┐
           └────────────────┼┤  user_id  int8   │
                              └──────────────────┘
┌ SQL do rascunho ─────────────────────────────────────────────────┐
│ 1 ALTER TABLE public.users ADD COLUMN birth date;                │
│ 2 ...                                                            │
└──────────────────────────────────────────────────────────────────┘
a tabela  c coluna  l ligar  x apagar  u desfazer  enter aplicar  e ensaio  w salvar .sql
```

- Abre a partir da tela atual (`ratt db`) como uma tela nova do Textual, usando a mesma conexão.
  *(Atalho de entrada: decisão minha, abaixo.)*
- **Canvas** ocupa a largura toda; o **painel do SQL do rascunho** fica embaixo, sempre visível
  (pode ser recolhido para uma linha, mas nunca escondido por completo enquanto houver rascunho).
- O canvas é maior que a tela: rola com setas/roda do mouse/arrastar o fundo.
- Mostra **um schema por vez** (o `public` primeiro); uma tecla troca de schema.

### Desenho

- **Caixa** = tabela: nome no topo, uma linha por coluna com marca (`PK`, `FK` implícita pela
  seta), nome e tipo curto. Colunas de auditoria podem ser recolhidas (decisão do construtor).
- **Notação pé de galinha**, derivada do catálogo, nunca desenhada à mão:
  - lado "um": `┼`; lado "muitos": `<` / `>`; opcional (FK que aceita NULL): `o` junto da marca.
  - FK cuja(s) coluna(s) é/são `UNIQUE` ou PK → 1:1; senão 1:N.
- **Tabela de ligação N:N** (PK = exatamente duas colunas, cada uma FK) é desenhada como caixa
  normal na fase 1; recolher em uma aresta "N:N via X" é melhoria posterior.
- **Auto-referência** (ex.: `employees.reports_to`) = seta em laço na própria caixa.
- **FK composta** = uma seta só, rotulada com as colunas.
- **Partições** recolhidas na tabela-mãe, com selo da quantidade.
- **Mudanças pendentes do rascunho** aparecem no desenho com estilo próprio: o que será criado em
  verde com `+`, o que será apagado em laranja riscado/`−`. O desenho com rascunho = como o banco
  ficará depois de aplicar.
- **Posição automática** na primeira vez: camadas pela direção das FKs (tabelas referenciadas
  mais à esquerda/acima), sem sobrepor caixas. A pessoa arrasta com o mouse (e move com o teclado,
  para quem não usa mouse). As posições ficam guardadas por banco e schema.
- **Setas** ortogonais, desviando das caixas sempre que possível; cruzamentos são aceitos. O
  algoritmo é do construtor; o critério é: nunca passar *por dentro* de uma caixa e nunca
  ficar ambígua sobre qual coluna liga a qual.
- **Banco grande**: acima de um limite de tabelas no schema (sugestão: ~25), abre em **modo
  foco** — a tabela escolhida mais as vizinhas a 1 ligação, com filtro por nome. Tabelas com
  muitas ligações chegando mostram um contador (`← 210`) em vez de todas as setas, expandível.
  *(Pesquisa: até ~20 tabelas o todo cabe; GitLab tem 1136 tabelas e 1928 FKs; `users` recebe
  210.)*

### O que entra na fase 1 (operações)

Cada uma abre um formulário pequeno sobre o desenho; o formulário mostra **o SQL que vai gerar**,
atualizado enquanto se preenche, e um botão "pôr no rascunho".

| Operação | SQL gerado (forma) | Avisos/checagens antes |
|---|---|---|
| Criar tabela | `CREATE TABLE s.t (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, ...)` | nome válido e livre |
| Adicionar coluna | `ALTER TABLE s.t ADD COLUMN c tipo [NOT NULL] [DEFAULT ...]` | `NOT NULL` sem `DEFAULT` numa tabela com linhas falha: avisar com a contagem |
| Renomear tabela / coluna | `ALTER TABLE ... RENAME [COLUMN] ... TO ...` | nome livre |
| Tornar obrigatória / opcional | `ALTER TABLE ... ALTER COLUMN c SET/DROP NOT NULL` | contagem de linhas com NULL que impediriam |
| Tornar única / não única | `ALTER TABLE ... ADD CONSTRAINT ... UNIQUE (c)` / `DROP CONSTRAINT` | contagem de valores repetidos que impediriam |
| Ligar (criar FK) | `ALTER TABLE filho ADD [COLUMN pai_id tipo,] ADD CONSTRAINT ... FOREIGN KEY (c) REFERENCES pai (pk) [ON DELETE ...]` | tipos compatíveis; linhas órfãs que impediriam (contagem); pergunta "um ou vários?" → 1:1 acrescenta `UNIQUE`; oferece criar índice na coluna da FK (o PostgreSQL não cria sozinho) |
| Ligar como N:N | `CREATE TABLE a_b (a_id ..., b_id ..., PRIMARY KEY (a_id, b_id), FOREIGN KEY ..., FOREIGN KEY ...)` | nome sugerido `a_b` |
| Apagar ligação | `ALTER TABLE ... DROP CONSTRAINT ...` | — |
| Apagar coluna | `ALTER TABLE ... DROP COLUMN c` | o que depende dela (índices, FKs, views) |
| Apagar tabela | `DROP TABLE s.t` (`CASCADE` só se a pessoa escolher, vendo o que leva) | **prévia da cascata**: quais tabelas/objetos dependem, e quantas linhas a tabela tem |

O texto exato de cada SQL (nomes de constraint, `ON DELETE` padrão, etc.) é do construtor, desde
que siga o que o `pg_dump` / os nomes automáticos do PostgreSQL fariam, para que o `.sql` pareça
o de uma pessoa experiente.

### O rascunho

- Uma lista ordenada de operações, cada uma com seu SQL. Mostrada no painel de SQL, numerada.
- **Desfazer** a última (`u`) e **tirar** uma qualquer da lista (no painel).
- **Tirar uma operação de que outras dependem** (ex.: "criar tabela X" com colunas/FKs em X
  depois; ou renomear e depois mexer pelo nome novo): o canvas mostra quais operações dependem
  dela e oferece tirar todas juntas — a mesma ideia da prévia da cascata. Não recusa e não deixa
  operações órfãs no rascunho. Para isso, as operações referem-se a tabelas/colunas por uma
  identidade estável do modelo, não só pelo nome. *(Decisão minha.)*
- **Checagens são só leitura.** As contagens (NULL, repetidos, órfãos) e a leitura de
  dependências são `SELECT`s no banco **atual**, nunca DDL. O que o rascunho cria ainda não existe
  no banco e é deduzido no modelo (tabela nova = 0 linhas; coluna nova = toda NULL ou DEFAULT).
  Quando a checagem depende de algo que o rascunho mudou e o modelo não consegue deduzir, ela diz
  isso em vez de chutar. A verificação completa, contra o banco de verdade, é o **ensaio** — que a
  pessoa dispara. Motivo: nada de DDL chega ao servidor sem a pessoa mandar (e um `ALTER TABLE`
  tranca a tabela inteira enquanto a transação durar). *(Decisão minha.)*
- As contagens rodam com tempo limite curto (`statement_timeout`, ~2 s); passou disso, o aviso
  diz "não deu para contar a tempo" e o ensaio continua sendo a prova. *(Decisão minha.)*
- **Nunca se perde**: é gravado em disco a cada mudança, por banco, e reaberto quando a pessoa
  volta ao canvas daquele banco. Se o banco mudar por fora e uma operação do rascunho deixar de
  fazer sentido (a tabela sumiu), ela fica marcada, não é apagada.
- **Salvar como `.sql`** (`w`): escreve o rascunho num arquivo que a pessoa escolhe (padrão: a
  pasta atual), para abrir no VS Code e rodar depois com `ratt db run`.
- **O SQL do rascunho é só leitura e exportação.** O rascunho contém apenas operações que o canvas
  sabe fazer; o painel não edita SQL e o canvas não lê `.sql` de volta. Motivos: o caminho de
  editar SQL já existe (VS Code + `run --watch` + a tela que se atualiza); ler SQL escrito à mão
  seria um interpretador de SQL dentro do ratt; e só assim o desenho com rascunho mostra
  exatamente como o banco ficará e os avisos valem para tudo que está nele. Um "importar `.sql`"
  que aceite só os comandos conhecidos fica como melhoria futura.

### Aplicar

- **Aplicar** (`enter`, com confirmação) e **ensaiar** (`e`) usam `rodar.executar` sobre o
  rascunho gravado como `.sql`: a TUI é suspensa (como o `p` faz com o `psql`), a saída é a mesma
  do `ratt db run` — cada comando com o que fez, o erro apontando o comando —, e uma tecla volta.
- Uma transação só: falhou um, nada fica. Deu certo: o rascunho é esvaziado (e guardado como
  `.sql` aplicado, para histórico simples — decisão do construtor onde). Falhou: o rascunho
  continua, com a operação que falhou marcada.
- Depois de aplicar, o desenho se atualiza pelo catálogo (a vigia por `assinatura` já existente).
  **Atenção:** hoje `catalogo.assinatura` não enxerga mudança de restrições — criar/apagar FK,
  `UNIQUE`, `SET/DROP NOT NULL` não a alteram. Ela precisa passar a incluir `pg_constraint` e
  `attnotnull`, senão o canvas não redesenha depois dessas operações.
- **`rodar.executar` precisa de um ajuste pequeno:** hoje devolve só 0/1 (o canvas precisa saber
  *qual* comando falhou, para marcar a operação), muda o `autocommit` da conexão que recebe e
  acrescenta um tratador de avisos a cada chamada. O aplicar usa **uma conexão própria**, aberta
  só para isso — nunca a da tela, que é `autocommit=True` e dividida com a thread da vigia.
- Operações que o PostgreSQL não roda em transação não existem na fase 1.

### Avisos (os que ensinam)

Aparecem junto da caixa/coluna e na hora de pôr no rascunho; nunca bloqueiam sozinhos (exceto o
que o PostgreSQL de fato recusaria, que é avisado *antes* com a contagem que explica):

- tabela sem chave primária;
- FK sem índice na coluna de origem;
- FK que aceita NULL (a ligação é opcional — dito em palavras);
- criar FK com linhas órfãs; tornar obrigatória com NULLs; tornar única com repetidos (com a
  contagem e um exemplo);
- apagar com dependências (a cascata, em palavras e destacada no desenho);
- ligação desenhada como 1:1 vira `UNIQUE` — e o canvas explica que sem isso o banco aceitaria
  vários.

## Partes do código que mudam (mapa, não plano)

- `ratt/db/catalogo.py` — já lê colunas, PK, FKs por tabela. Precisa de uma leitura **do schema
  inteiro de uma vez** (todas as tabelas com colunas, todas as FKs com nulidade/unicidade,
  partições), para não fazer uma consulta por tabela.
- Novo módulo do **modelo + rascunho + geração de SQL**, puro (sem Textual, sem conexão para
  gerar texto): operações → `psycopg.sql` composto.
- Novo módulo do **desenhista**: posição automática, roteamento, e modelo → linhas de texto com
  estilos. Puro, testável comparando texto.
- Novo módulo da **tela do canvas** (Textual): o widget que exibe, mouse/teclado, formulários, o
  painel do SQL, aplicar/ensaiar via `rodar.executar` com a TUI suspensa.
- `ratt/db/tela.py` — um atalho que abre o canvas.
- `ratt/db/idioma.py` — os textos novos em `pt` e `en`.
- `tests/` — testes que **não precisam de PostgreSQL** (como os de `test_db.py`): geração de SQL
  por operação, cardinalidade derivada, posição automática sem sobreposição, desenho de casos
  pequenos (as 4 tabelas do PNG) comparado com texto esperado. O que precisa de banco real fica
  marcado para pular quando não houver PostgreSQL.
- `README.md` e `CHANGELOG.md` — nova versão (0.9.0), no padrão das anteriores.

## Decisões que tomei (a pessoa pode trocar)

- **Atalho de entrada**: `d` (diagrama) na tela `ratt db`, abrindo o schema da tabela em foco.
  Dentro do canvas, apagar é `x`/`Delete` para não colidir.
- **PK padrão de tabela nova**: `id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY` (o jeito
  moderno do PostgreSQL; editável no formulário).
- **Posições e rascunhos** guardados em `~/.local/share/ratt/` (ou `$XDG_DATA_HOME/ratt/`), onde
  o ratt já guarda `instalacoes.json` — são dados, não configuração. Uma pasta por porta+banco.
- **Só tabelas** no canvas da fase 1 (views e o resto continuam na árvore da tela atual).
- **Aplicar suspende a TUI** e reaproveita a saída do `ratt db run`, em vez de reescrever
  `rodar.executar` para devolver resultados à TUI. Mais barato, consistente com o `p` → `psql`, e
  a pessoa aprende o formato do `run`. Uma versão integrada pode vir depois.
- **Limite do modo foco**: ~25 tabelas no schema (ajustável).
- **Checagens só de leitura** no banco atual, com tempo limite; a prova completa é o ensaio.
- **Tirar operação com dependentes** do rascunho oferece tirar as dependentes juntas.

Decidido pela pessoa, por recomendação minha: o SQL do rascunho é **só leitura e exportação**
na fase 1.

## Deixado para o construtor

- O algoritmo de posição automática e de roteamento das setas (dentro dos critérios acima).
- A disposição exata dos formulários e das teclas secundárias, desde que listadas no rodapé.
- O formato do arquivo de rascunho em disco (o `.sql` exportado é que tem formato definido: SQL
  puro, um comando por bloco, comentado com a operação que o gerou).
- Nomes de módulos e funções, seguindo o estilo do projeto (português, docstrings que explicam o
  porquê).
- Se as colunas de auditoria (`created_at`, `updated_at`) começam recolhidas.

## Fase 2 (registro, não escopo)

O **filtro de linhas**: montar `WHERE` com o mouse para ver, apagar e alterar dados, na aba
Dados. A pesquisa feita para ele está em
[`2026-11-01-pesquisa-schemas-e-filtros.md`](2026-11-01-pesquisa-schemas-e-filtros.md). A gramática
pensada — coluna → transformação opcional → operador por tipo → valor, com E/OU/NÃO, e o `WHERE`
editável como texto para o que não couber — e as três extensões que a pesquisa pediu (modo
"duplicatas: manter uma", "existe em outra tabela onde…", "vazio" e data relativa como itens
prontos) ficam para o brainstorm da fase 2.
