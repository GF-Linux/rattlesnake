# Os dados no ratt db: inserir, filtrar, alterar e apagar linhas pela tela

Data: 2026-11-02 · Branch: a criar a partir de `main` (0.9.0) · Fase 2 de 4

A fase 1 (0.9.0, `docs/superpowers/specs/2026-11-01-canvas-db-design.md`) fez o canvas: a
estrutura das tabelas desenhada e editada, com rascunho, ensaio e aplicar. A pesquisa que
embasa esta fase está em `docs/superpowers/specs/2026-11-01-pesquisa-schemas-e-filtros.md`.

## Para que serve

Hoje a tela do `ratt db` só **mostra** as linhas. Para preencher uma tabela, a pessoa precisa
escrever `INSERT INTO … VALUES …` à mão; para apagar "todo mundo com a inicial G" ou "quem faz
aniversário de junho em diante", precisa saber montar o `WHERE`. A fase 2 faz isso pela tela:
um formulário para inserir, um filtro montado com o mouse para escolher linhas, e as ações sobre
as linhas escolhidas (alterar, apagar, apagar duplicatas) — sempre mostrando o SQL que sai.

Serve primeiro para **aprender** (a pessoa sabe o básico de SQL e aprende vendo o `WHERE` e o
`INSERT` que a tela monta) e para **cuidar de dados de verdade** sem erro. O princípio continua:
**avisa, não decide**.

## Objetivos

1. **Inserir linhas** por formulário, um campo por coluna, que entende o tipo da coluna.
2. **Filtrar linhas** com um filtro montado sem digitar SQL (a gramática abaixo), que mostra na
   hora quais linhas pega e o `WHERE` correspondente; para o que não couber, o `WHERE` pode ser
   escrito como texto.
3. **Alterar** e **apagar** as linhas filtradas, e **apagar duplicatas** mantendo uma.
4. Tudo entra no **mesmo rascunho do canvas** e é aplicado do mesmo jeito: SQL à vista,
   confirmação, uma transação, ensaio.
5. Avisos que ensinam, em especial as armadilhas do vazio (`NULL`) no `WHERE`.
6. Um primeiro bloco curto com as pendências da 0.9.0.

## Fora do escopo (não-objetivos)

- Usuários e permissões (fase 3); manutenção e o que falta na estrutura (fase 4).
- **Alterar com dados de outra tabela** (`UPDATE … FROM`): difícil de montar sem digitar; fica
  registrado para depois.
- **Converter texto em data** e mudar tipo de coluna: depende de `ALTER COLUMN TYPE` (fase 4).
- Importar CSV (`COPY`), `TRUNCATE`, inserir-ou-atualizar (upsert): depois.
- Editar uma célula diretamente na grade (planilha). Alterar é sempre "estas linhas filtradas
  recebem isto".
- Mostrar na grade as mudanças pendentes do rascunho (decidido pela pessoa: a grade mostra o
  banco como está; tudo se atualiza ao aplicar).
- Ordenar/agrupar/agregar (`ORDER BY`, `GROUP BY`) como ferramenta de análise.

## Anti-objetivos

- Algo ir para o banco sem a pessoa ter visto o SQL e confirmado (igual à fase 1).
- Um `UPDATE`/`DELETE` pegar linhas que a pessoa não viu: a contagem e a amostra das linhas
  pegas aparecem antes de a operação entrar no rascunho.
- O filtro **mentir por omissão**: deixar de fora linhas com vazio sem dizer.
- Perder o rascunho (vale tudo o que a fase 1 garantiu).
- Uma checagem "só de leitura" escrever no banco.

## Restrições

As da fase 1 continuam (Python ≥ 3.9; sem dependências novas; textos em `idioma.py`, pt e en;
nomes pela função única de citação, sempre com schema; consultas com `psycopg.sql` e
parâmetros; checagens só `SELECT` com tempo limite). E mais:

- **Valores digitados nunca entram no SQL como texto cru**: viram literais citados
  (`psycopg.sql.Literal`, com o tipo da coluna quando preciso: `'2026-06-01'::date`). A exceção
  é o `WHERE` escrito como texto (abaixo), que é SQL da pessoa, mostrado antes de aplicar.
- Checagens que rodam um `WHERE` montado ou digitado rodam numa transação **somente leitura**
  (`SET TRANSACTION READ ONLY`) com `statement_timeout`.

## Abordagens consideradas

| | Como funciona | Troca |
|---|---|---|
| **A. Uma tela nova "Dados", no molde do canvas** *(escolhida)* | Da tela do `ratt db` (e do canvas), uma tecla abre a tabela numa tela própria: linhas na largura toda, barra do filtro em cima, painel do SQL embaixo. O painel do rascunho e o aplicar saem do canvas para um pedaço comum às duas telas. | Reaproveita o que o canvas já resolveu (conexão própria, rascunho, ensaio, aplicar, proteções da revisão final) e não mexe na tela que só mostra; custa uma tela a mais. |
| B. Tudo na aba Dados atual | Filtro, formulários e painel na aba que já existe. | Sem tela nova; mas a tela atual é só de ver, divide a conexão com a vigia e não tem o rascunho — seria refazer o que o canvas tem, na tela que devia ficar igual. |
| C. Dentro do canvas | Linhas num painel do diagrama. | Estrutura e dados juntos; mas não cabe. |

Escolhida **A**, pela pessoa.

## O que foi decidido

### Onde fica e como se chega

- Tela nova **Dados** de uma tabela. Abre:
  - da tela do `ratt db`, com a tabela em foco na árvore — tecla `e` (editar dados);
  - do canvas, com uma tabela selecionada — tecla `v` (ver/editar dados).
  *(Teclas: decisão minha.)*
- Layout:
  ```
  ratt db · estudos › public.pessoas · Dados · rascunho: 3
  filtro: [ nome  começa com  "G" ]  E  [ nascimento  mês ≥  junho ]          (f · F limpa · t texto)
  ── 37 de 454 linhas ─────────────────────────────────────────────────────────────────
   id    nome          nascimento   email
   int8  text          date         text
   12    Gabriel       1990-07-14   gab@x.com
   …
  ⚠ 3 linhas com nascimento vazio (∅) ficam de fora: vazio não é "≥ junho" nem "< junho"
  ┌ SQL ───────────────────────────────────────────────────────────────────────────────┐
  │ -- o filtro:  SELECT … FROM public.pessoas WHERE nome LIKE 'G%' AND …              │
  │ -- 1. inserir em pessoas                                                            │
  │ INSERT INTO public.pessoas (nome, idade, email) VALUES ('Ana', 29, 'ana@x.com');    │
  └─────────────────────────────────────────────────────────────────────────────────────┘
  i inserir · f filtro · m alterar estas · x apagar estas · D duplicatas · u desfazer · enter aplicar · e ensaio
  ```
- A **grade** mostra as linhas do banco como ele está, com o filtro aplicado (200 por página,
  como hoje; o "37 de 454" é a contagem real). A releitura do ratt continua (∅ laranja,
  espaço sobrando amarelo). Uma linha diz quando há mudanças pendentes no rascunho que a grade
  não mostra.
- O painel do SQL mostra, no topo, o `SELECT` do filtro atual (para aprender o `WHERE`), e
  embaixo o rascunho inteiro — o mesmo do canvas.

### Um rascunho só, dividido com o canvas

- O rascunho é **por schema** (como na fase 1) e **o mesmo** no canvas e na tela Dados: criar
  a tabela no canvas e inserir linhas nela pela tela Dados vai para o mesmo rascunho e é
  aplicado numa transação só, na ordem em que foi feito.
- O painel do rascunho, o aplicar (`enter`), o ensaio (`e`), o exportar (`w`) e o desfazer
  (`u`) são os mesmos nas duas telas — extraídos do canvas para um pedaço comum.
- Duas telas abertas ao mesmo tempo (canvas → Dados) usam **o mesmo objeto de rascunho em
  memória** (uma sessão de rascunho por banco+schema, mantida pela aplicação), de modo que uma
  tela coberta nunca grava uma versão velha. *(Decisão minha; a fase 1 já protege a tela coberta
  relendo do disco — aqui a sessão única elimina o problema.)*
- Inserir numa tabela criada no próprio rascunho é permitido (o formulário usa o modelo do
  rascunho); a grade dela fica vazia até aplicar.

### Inserir

- `i` abre um formulário com **um campo por coluna**, na ordem da tabela:
  - coluna identidade (`GENERATED … AS IDENTITY`) ou com `DEFAULT`: campo opcional, vazio
    significa "o banco preenche" (a coluna sai do `INSERT`);
  - coluna obrigatória sem default: marcada;
  - **chave estrangeira**: em vez de digitar o número, escolher numa lista das linhas da tabela
    pai (mostrando o valor da chave e as primeiras colunas de texto), com busca;
  - booleano: sim/não/vazio; data/hora: texto validado (`AAAA-MM-DD`, `AAAA-MM-DD HH:MM`);
    número: validado; texto: livre; outros tipos: texto livre validado pelo banco.
- "pôr no rascunho" e também "pôr e inserir outra" (o formulário fica aberto, limpo).
- **Uma linha inserida = uma operação = um `INSERT`** no rascunho. Não se juntam várias linhas
  num `VALUES (…), (…)`: isso quebraria a ligação "uma operação, um comando" de que dependem o
  desfazer, o tirar do meio e a marca da operação que falhou. *(Decisão minha, depois da
  revisão.)*
- Coluna `GENERATED ALWAYS AS IDENTITY` e coluna gerada (`GENERATED ALWAYS AS (…) STORED`): o
  campo aparece desativado ("o banco preenche"); não se usa `OVERRIDING SYSTEM VALUE`.
- Avisos antes de pôr no rascunho:
  - obrigatório vazio → impede (o PostgreSQL recusaria);
  - valor que não serve para o tipo → impede (validado no banco, só leitura:
    `pg_input_is_valid` no PostgreSQL 16+; antes disso, um cast numa transação só leitura);
  - valor que já existe numa coluna única ou PK → impede, com a contagem;
  - valor de FK que não existe no pai (quando digitado) → impede;
  - texto com espaço sobrando nas pontas → atenção (é a releitura do ratt).

### O filtro

**A gramática** (da pesquisa): cada **condição** é
`coluna → transformação (opcional) → operador → valor`, e as condições se juntam com **E** ou
**OU** (uma escolha para o filtro inteiro), cada uma podendo ser negada (**NÃO**).

- **Operadores por tipo**, na ordem de uso da pesquisa:
  - todos: igual, diferente, **vazio** (para texto: `IS NULL OR trim(col) = ''`; para os outros:
    `IS NULL`), **preenchido** (o contrário), está na lista, não está na lista;
  - texto: **contém** (padrão `ILIKE '%x%'`), começa com, termina com, igual ignorando
    maiúsculas; opção "diferenciar maiúsculas" troca para `LIKE`;
  - número: `=`, `≠`, `<`, `≤`, `>`, `≥`, entre;
  - data/hora: antes de, depois de, entre, **há mais de N dias/meses/anos**, **nos últimos N
    dias/meses/anos** (`now() - interval`);
  - booleano: é verdadeiro, é falso.
- **Transformações** (opcionais, por tipo): minúsculas, sem espaços nas pontas, tamanho do texto;
  para data: **mês**, **ano**, **dia da semana**, **dia** (`extract`), e "só a data" (`::date`).
  Com o mês, o valor é escolhido pelo nome (junho), não pelo número. Isto cobre "aniversário de
  junho em diante".
- **Valor**: digitado (texto livre — "começa com G", "Ga", "Gab"), escolhido de uma célula
  (clicar numa célula da grade oferece "igual a este valor"), lista (vários valores), outra
  coluna da mesma linha.
- **Existe / não existe em outra tabela, onde…**: escolhe uma ligação (FK que chega ou sai da
  tabela) e, opcionalmente, condições sobre a outra tabela (a mesma gramática, um nível só). Gera
  `EXISTS (SELECT 1 FROM outra WHERE outra.fk = tabela.pk AND …)`. Cobre "apagar órfãos" e
  "clientes sem pedidos".
- **WHERE como texto** (`t`): para o que não couber, a pessoa escreve o `WHERE`. A grade e a
  contagem atualizam ao confirmar o texto. Enquanto o filtro estiver em texto, as condições
  montadas ficam desativadas (voltar ao montado descarta o texto, perguntando). O texto é SQL da
  pessoa: entra como está, deve formar uma única expressão (recusado se fechar o comando ou
  trouxer outro), e as checagens dele rodam somente leitura.
- **Avisos do filtro** (as armadilhas da pesquisa):
  - condição com `=`, `≠`, `<`, `>`, "não está na lista" etc. sobre coluna que aceita vazio →
    "N linhas com vazio ficam de fora", com a contagem;
  - "não está na lista" com vazio dentro da lista → "não pega nada" (o `NOT IN` com `NULL`);
  - "contém/começa com" em texto com espaço sobrando nas pontas → quantas linhas não casam por
    causa disso.
- Ao mudar o filtro, a grade e a contagem se atualizam (consulta em segundo plano, como hoje).

### Alterar as linhas filtradas (`m`)

- Formulário: uma ou mais colunas, cada uma recebe:
  - **um valor fixo** (com o mesmo campo do inserir, por tipo; FK por lista);
  - **vazio** (`NULL`);
  - **uma limpeza da própria coluna**: sem espaços nas pontas, minúsculas, maiúsculas, texto em
    branco vira vazio (`NULLIF(trim(col), '')`);
  - **o valor de outra coluna** da mesma linha.
- Gera `UPDATE tabela SET … WHERE <filtro>`. Para as limpezas, acrescenta ao `WHERE` a condição
  que pula as linhas que não mudariam (`col IS DISTINCT FROM lower(trim(col))`), e diz isso.
- Antes de pôr no rascunho: quantas linhas mudam e uma amostra (antes → depois) de até 5.
  Avisos: valor vazio em coluna obrigatória → impede; valor fixo numa coluna única para mais de
  uma linha → impede; FK para valor inexistente → impede.
- Sem filtro, alterar pede uma confirmação extra ("todas as N linhas").

### Apagar as linhas filtradas (`x`)

- Gera `DELETE FROM tabela WHERE <filtro>`.
- Antes de pôr no rascunho: quantas linhas, amostra de até 5, e a **cascata**: para cada FK que
  chega na tabela, quantas linhas filhas seriam apagadas (`ON DELETE CASCADE`), ficariam vazias
  (`SET NULL`) ou **impedem** o apagar (`NO ACTION`/`RESTRICT`).
- Aviso de soft delete (da pesquisa): se a tabela tem `deleted_at`, `discarded_at`,
  `is_deleted`, `ativo`/`active` ou parecido, sugerir "talvez seja um UPDATE dessa coluna em vez
  de apagar" — só sugestão.
- Sem filtro, apagar pede confirmação extra ("todas as N linhas").

### Apagar duplicatas (`D`)

- Formulário: **repetidas por** (uma ou mais colunas; opção "ignorar maiúsculas e espaços" para
  texto), **manter**: a de menor chave / a de maior chave / a mais recente por uma coluna de
  data / as **N** mais recentes por uma coluna de data. Respeita o filtro atual (só entre as
  linhas filtradas).
- Gera `DELETE … WHERE pk IN (SELECT pk FROM (SELECT pk, row_number() OVER (PARTITION BY …
  ORDER BY …) AS rn FROM … WHERE <filtro>) s WHERE rn > N)`. Tabela sem PK: usa `ctid`, com
  aviso de que é o recurso de último caso.
- Antes: quantos grupos repetidos, quantas linhas saem, amostra de um grupo (as que ficam e as
  que saem), e a cascata como no apagar.

### Contagens, rascunho pendente e o aplicar

- **As checagens olham o banco como está** (como na fase 1: só leitura; a prova completa é o
  ensaio). Quando há operações anteriores no rascunho **na mesma tabela**, a contagem aparece
  marcada como "aproximada — não considera N mudanças pendentes nesta tabela".
- Tabela ou coluna que ainda **não existe no banco** (criada ou renomeada no rascunho): a grade
  e as contagens dizem "só depois de aplicar" em vez de dar erro; inserir funciona (usa o modelo
  do rascunho); filtrar, alterar e apagar por uma coluna que ainda não existe no banco ficam
  indisponíveis até aplicar, com o motivo dito.
- **A operação guarda a contagem que a pessoa viu** ao pôr no rascunho (linhas pegas por
  `UPDATE`/`DELETE`/duplicatas). No **ensaio**, a saída mostra, por operação, "pegou X (você viu
  Y)". No **aplicar**, se uma operação pegar um número diferente do que a pessoa viu e não houver
  operação anterior no rascunho na mesma tabela, a transação é desfeita e a operação fica marcada
  com "o banco mudou: pegaria X, você viu Y — revise e ponha de novo". Com operação anterior na
  mesma tabela, a diferença é mostrada mas não bloqueia. *(Decisão minha: é o anti-objetivo "não
  pegar linhas que a pessoa não viu" valendo também para um rascunho que ficou dias no disco.)*
  Para isso o executor passa a informar quantas linhas cada comando afetou.

### Que objetos a tela Dados edita

- **Tabelas comuns e particionadas**: tudo. **Views, views materializadas e tabelas
  estrangeiras**: abrem só para ver e filtrar (`i`, `m`, `x`, `D` indisponíveis, com o motivo).

### O filtro dentro da operação

- O filtro montado é guardado **como estrutura** (colunas e tabelas pelos ids do modelo), então
  renomear uma coluna depois continua valendo, e apagar a coluna invalida a operação — como as
  operações da fase 1.
- O filtro **em texto** guarda o texto e só a tabela: a operação é válida enquanto a tabela
  existir; colunas citadas no texto não são acompanhadas (se uma for renomeada, o erro aparece no
  ensaio ou no aplicar). O painel marca essas operações com "WHERE escrito à mão".

### Teclas do painel do rascunho na tela Dados

- O painel é o mesmo do canvas: `r` põe o foco nele, e com o foco no painel `↑/↓` escolhem a
  operação e `x` a tira (com as dependentes); fora do painel, `x` é "apagar as linhas filtradas".

### Versão do PostgreSQL

- Alvo: **PostgreSQL 13 ou mais novo** (o que as famílias do `ratt db setup` instalam). A
  validação de valor por tipo usa `pg_input_is_valid` no 16+; antes disso, um cast dentro de um
  savepoint numa transação somente leitura. A versão vem de `conexao.info.server_version`. Os
  dois caminhos são testados (o do cast forçado mesmo no 16+).

### O primeiro bloco: pendências da 0.9.0

1. Ligar com coluna nova e N:N aceitam uma PK de tipo enum/domain/extensão: o tipo da coluna
   nova pode ser exatamente o tipo da PK como o catálogo escreve.
2. No modo foco, mover uma caixa guarda a posição (sem estragar o layout do schema inteiro).
3. `F` (mostrar tudo) num schema grande não trava a tela: acima de um limite, avisa e pede
   confirmação, e o desenho do schema inteiro é calculado em segundo plano com "desenhando…".

## Partes do código que mudam (mapa, não plano)

- **Novo, puro:** o filtro (gramática → `WHERE` com literais citados, avisos de vazio,
  validação do texto livre); operações de dados no rascunho (inserir, alterar, apagar, apagar
  duplicatas) — com `refere()` pelos ids da tabela/colunas, como as da fase 1, para o rebase e
  as dependências continuarem valendo.
- **Novo:** a tela Dados (grade, barra do filtro, formulários de inserir/alterar/duplicatas,
  confirmações).
- **Extraído do canvas para um pedaço comum:** painel do rascunho, aplicar/ensaio/exportar,
  sessão de rascunho por banco+schema mantida pela aplicação.
- **`checagem.py`:** contagens do filtro (pegas, vazias de fora, espaços), validade de valores
  por tipo, unicidade, FK existente, cascata por FK que chega, amostras antes/depois.
- **`catalogo.py`:** ler uma página de linhas com `WHERE` montado (a leitura atual de `dados`
  não aceita filtro); listar linhas da tabela pai para o campo de FK.
- **`tela.py`:** tecla `e`; **`canvas.py`:** tecla `v`, usar o pedaço comum.
- **`idioma.py`**, **testes** (puros para filtro e operações; com PostgreSQL para o SQL gerado
  rodar de verdade e as contagens), **README/CHANGELOG** (versão 0.10.0).

## Decisões que tomei (a pessoa pode trocar)

- Teclas: `e` na tela do `ratt db`, `v` no canvas; dentro da tela Dados: `i` inserir, `f`
  filtro, `F` limpar, `t` WHERE em texto, `m` alterar, `x` apagar, `D` duplicatas, `u`
  desfazer, `enter` aplicar, `e` ensaio, `w` exportar, `esc`/`q` voltar.
- Uma sessão de rascunho por banco+schema mantida pela aplicação, dividida entre as telas.
- Uma linha inserida = uma operação = um `INSERT` (sem juntar em `VALUES` de várias linhas).
- Checagens contra o banco atual, com "aproximada" quando há mudanças pendentes na mesma tabela.
- A operação guarda a contagem vista; o aplicar desfaz tudo se ela mudou (sem operação anterior
  na mesma tabela).
- Views e tabelas estrangeiras só para ver e filtrar; colunas `GENERATED ALWAYS` não recebem
  valor.
- Filtro guardado como estrutura; o `WHERE` em texto só acompanha a tabela.
- PostgreSQL 13+.
- "Contém" padrão sem diferenciar maiúsculas (`ILIKE`), com opção de diferenciar.
- O filtro inteiro usa um conectivo só (E ou OU); grupos com parênteses ficam para o `WHERE` em
  texto.
- Amostras de até 5 linhas.
- Versão 0.10.0.

## Deixado para o construtor

- Disposição exata dos formulários e da barra do filtro, desde que as teclas apareçam no rodapé.
- Como a lista do campo de FK pagina e busca (com um limite razoável e busca por texto).
- Formato interno das operações de dados no arquivo do rascunho (o JSON), mantendo a leitura de
  rascunhos da 0.9.0.
- O limite de tabelas para o aviso do `F`.
