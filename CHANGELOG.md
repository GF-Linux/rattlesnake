# Mudanças

O `ratt check-update` e o `ratt upgrade` leem este arquivo: cada `## versão` é o que você
vê antes de responder `[s/N]`.

## 0.12.0

- **arquivos nas colunas**: dá para pôr o conteúdo de um arquivo numa coluna `bytea` (qualquer
  arquivo) ou de texto (só UTF-8) e tirá-lo de lá, sem escrever SQL.
  - **Botão `arquivo…`** nos formulários de inserir e de alterar: escolhe o arquivo pelo caminho
    ou pela árvore de pastas, e o campo passa a mostrar `‹nome · tamanho›`. Se você editar o campo,
    o arquivo é descartado. A tela lembra a pasta do último arquivo carregado.
  - **Na grade**, um `bytea` aparece como `‹arquivo · tamanho›`, sem trazer os bytes.
  - **`s` na tela Dados** salva em arquivo o conteúdo da célula (`bytea` ou texto).
  - **Limite de 10 MB** por arquivo; acima disso o ratt recusa e explica. O SQL mostrado abrevia o
    conteúdo longo (`/* arquivo · … */`), mas o que é aplicado é o conteúdo inteiro.

## 0.11.0

- **usuários e permissões pela tela**: duas telas novas no `ratt db`, para mexer em quem entra
  onde e em quem pode o quê sem escrever `CREATE ROLE`, `GRANT` ou `REVOKE`. Como nas outras
  telas, o que você faz vira um rascunho com o SQL à vista; `enter` aplica, `e` ensaia, `w` salva
  num `.sql`, `u` desfaz e `r` leva o foco ao painel do SQL.
  - **Usuários** (`u` na tela do banco): uma grade com os papéis nas linhas — a linha `PUBLIC`
    (todo mundo) em cima, fixa — e, nas colunas, o que cada um pode fazer, os grupos de que faz
    parte e o acesso a cada banco. Na célula do banco, `D` é dono, `C` é o `CONNECT` dado direto,
    `c` é o acesso que vem de outro lugar (ser superusuário, o `PUBLIC` ou um grupo) e `—` é sem
    acesso. `a` cria um usuário, `espaço` muda os poderes (entra, superusuário, cria bancos, cria
    papéis) e a senha, `g` os grupos, `c` dá ou tira o acesso a um banco (num `c`, a tela explica
    de onde ele vem e oferece tirar de lá), `o` troca o dono do banco da coluna, `m` marca vários
    e `x` remove.
  - **O rascunho do servidor** é um só, guardado à parte dos rascunhos dos bancos
    (`canvas/{porta}-servidor/`). Ao aplicar, cada banco roda numa transação, na ordem, e para no
    primeiro erro: o que já foi feito sai do rascunho, a operação que parou fica marcada
    "parcial" e o que falta continua lá — é só aplicar de novo.
  - **A senha só aparece ao aplicar.** O SQL mostrado, o `.sql` salvo e o histórico de aplicados
    nunca têm senha: no lugar vai um comentário. Ao aplicar, o ratt pede a senha duas vezes, na
    parte do terminal que a tela deixa, e manda ao servidor só o hash (SCRAM), calculado aqui; o
    ensaio não pede senha. Atenção: com `log_statement` ligado no servidor, o log dele pode
    guardar esse **hash** (nunca a senha).
  - **Remover do jeito certo**: remover um ou vários usuários mostra antes, banco a banco, o que
    passa para o novo dono (você, ou quem escolher) e o que some junto — os privilégios que
    outros deram a ele, os que ele repassou, as permissões padrão. Antes de mudar qualquer coisa
    o ratt confere o servidor de agora e **recusa** se o papel tem sessão aberta, se um banco que
    não aceita conexão ainda depende dele, ou se há banco que aceita conexão e ficou fora da
    remoção.
  - **Travas contra se trancar para fora**: o botão fica desligado, com o motivo, quando a
    operação tiraria o seu acesso — remover a si mesmo ou o `postgres`, tirar de si o `LOGIN` ou
    o `SUPERUSER`, deixar o servidor sem superusuário que entra, e também pelo caminho indireto:
    sair do grupo que dá o `CONNECT`, tirar o `CONNECT` do `PUBLIC` quando é por ele que você
    entra, trocar o dono do banco em que você está.
  - **`PUBLIC`**: dar ou tirar o `CONNECT` do `PUBLIC` avisa o que muda para todo mundo; e
    quando um `REVOKE` não tira o acesso porque outro o concedeu, o ratt diz quem concedeu em
    vez de fingir que deu certo.
  - **Falta poder? `sudo`**: remover papéis exige superusuário. Se você não é, ou se o servidor
    recusa por falta de poder, o ratt mostra o que falta e pergunta `[s/N]` se roda como
    `postgres` pelo `sudo`, banco por banco; o SQL vai pela entrada do `psql`.
- **permissões das tabelas pela tela** (`P` no canvas, na tela Dados e na Usuários — nesta, ele
  pergunta de qual schema): uma grade de papéis por tabelas com as sete letras `S I U D T R t`
  (select, insert, update, delete, truncate, references, trigger), `dono` para quem é dono, e a
  linha `PUBLIC` em cima. Só tabelas entram; views e tabelas estrangeiras ficam de fora.
  - `espaço` abre o formulário de privilégios da célula ou da seleção (conceder e revogar); `l`
    seleciona a linha do papel, `k` a coluna da tabela e `*` tudo. "Todas" gera a lista das
    tabelas, nunca `ALL TABLES IN SCHEMA`.
  - **"Também as tabelas criadas depois"** gera o `ALTER DEFAULT PRIVILEGES FOR ROLE` de quem
    você é: vale só para as tabelas que **você** criar naquele schema, e a tela avisa.
  - `s` dá ou tira `USAGE` e `CREATE` do schema. Falta `USAGE`? Um aviso diz que o papel ainda
    não vai ver as tabelas e oferece dar junto.
  - O rascunho é o mesmo do canvas e da tela Dados. A grade relê sozinha quando alguém faz
    `GRANT` ou `REVOKE` por fora. Um papel criado no rascunho do servidor só aparece nela depois
    de aplicado.

## 0.10.0

- **os dados pela tela**: `e` no `ratt db` (com a tabela escolhida na árvore) e `v` no canvas
  abrem a tela Dados da tabela: a grade em largura toda, a barra do filtro em cima e o painel do
  SQL embaixo. Inserir, alterar e apagar linhas sem escrever o SQL — e vendo o SQL que sai. A
  grade mostra o banco como está; o que está no rascunho aparece depois de aplicar, e uma linha
  diz quando há mudança pendente nesta tabela. Views e tabelas estrangeiras abrem só para ver e
  filtrar.
  - **Filtro**: `f` monta uma condição (coluna, operação, valor), `o` troca entre E e OU,
    `backspace` tira a última e `F` limpa. Os operadores são em palavras e mudam com o tipo:
    é igual a, é diferente de, está vazio, está preenchido, está na lista, contém, começa com,
    termina com, é maior que, está entre, é antes de, é verdadeiro… Em data, vale o mês, o ano,
    o dia da semana ou o dia (com o mês escolhido pelo nome, `junho`), e também "há mais de N
    dias" e "nos últimos N meses". "Vazio" é o NULL — e, em texto, também o texto em branco.
    Dá para filtrar pelo que **existe (ou não existe) em outra tabela**, seguindo uma chave
    estrangeira: os órfãos, os clientes sem pedidos.
  - **WHERE em texto**: `t` abre o `WHERE` para você escrever, começando pelo que o filtro
    montado já gera. Ele precisa ser uma expressão só: é recusado se tiver `;`, comentário ou
    uma barra invertida dentro de `'…'`. Enquanto o filtro está em texto, as condições montadas
    ficam de lado; voltar a elas (ou limpar tudo com `F`) pergunta antes. No painel do SQL, a
    operação com o filtro em texto vem marcada "WHERE escrito à mão".
  - **Avisos do filtro**: o vazio não é "igual" nem "diferente" de nada, então quem tem vazio
    fica de fora de `=`, `<` e companhia — a tela conta quantas linhas e avisa; "não está na
    lista" com vazio na lista não pega nada (já "está na lista" com o vazio, `∅`, pega as linhas
    vazias); "contém" em texto com espaço sobrando nas pontas diz quantas linhas não casam por
    isso. Clicar numa célula filtra "igual a este valor" — menos em `real`, `double precision` e
    `interval`, cujo valor mostrado não volta exato (a tela diz por quê).
  - **Inserir**: `i` abre um formulário com um campo por coluna. A chave estrangeira é
    escolhida numa lista das linhas da tabela pai (com busca), sem digitar o número. Coluna
    com valor padrão ou identidade `BY DEFAULT` é opcional: vazio deixa o banco preencher.
    O que não serve para o tipo, o obrigatório vazio e o valor repetido numa coluna única
    avisam (✖) antes de ir para o rascunho. Cada linha inserida é um `INSERT`.
  - **Alterar**: `m` muda as linhas filtradas — para um valor, para vazio, por uma limpeza da
    própria coluna (sem espaços nas pontas, minúsculas, maiúsculas, texto em branco vira vazio)
    ou para o valor de outra coluna. Antes de entrar no rascunho, mostra quantas linhas mudam e
    uma amostra do antes e do depois.
  - **Apagar**: `x` apaga as linhas filtradas e mostra antes quantas são e o que cai junto, em
    qualquer schema e em cadeia: as filhas apagadas, as que ficam vazias e as que impedem o
    apagar. Quando a conta não fecha, diz "pelo menos N". Sem filtro, pede uma confirmação a
    mais.
  - **Duplicatas**: `D` apaga as repetidas de uma ou mais colunas e mantém por grupo a de menor
    chave, a de maior, ou as mais recentes por uma coluna de data — você escolhe quantas ficam
    (por padrão, uma). Linhas com a coluna
    vazia não contam como repetidas. Respeita o filtro atual.
  - **A contagem vem antes**: alterar, apagar e duplicatas só entram no rascunho depois que a
    contagem chegou (ou avisou que não deu para contar), e o botão espera por ela. Se o banco mudar enquanto o formulário está
    aberto, a contagem é refeita.
  - **Um rascunho só**: o canvas e a tela Dados dividem o mesmo rascunho. Criar a tabela no
    canvas e inserir linhas nela pela tela Dados vai tudo numa transação, na ordem em que foi
    feito. `enter` aplica, `e` ensaia, `w` salva num `.sql`, `u` desfaz e `r` leva o foco ao
    painel do SQL (nele, `x` tira a operação), igual ao canvas.
  - **O aplicar confere**: cada alterar, apagar e duplicatas guarda quantas linhas você viu.
    No ensaio, cada um mostra "pegou X, você viu Y". No aplicar, se o número for outro, tudo é
    desfeito e a operação fica marcada: o banco mudou desde que você olhou — salvo quando uma
    operação anterior do próprio rascunho mexe na mesma tabela; aí só mostra "pegou X (você viu Y)".
    A confirmação do aplicar lista no alto, com o que apaga, cada alterar, apagar e duplicatas
    com quantas linhas você viu (ou "não contado").
- corrigido (da 0.9.0): ligar uma coluna nova ou criar um N:N com uma chave primária de tipo
  próprio (enum, domínio) agora funciona; mover uma caixa no modo foco guarda a posição; `F`,
  num schema grande, pergunta antes e desenha em segundo plano, sem travar a tela.
- corrigido (formulários): "cancelar" com o mouse em "nova tabela" não fecha mais o canvas;
  `enter` num campo de coluna só acrescenta a coluna; os botões do formulário não saem do lugar
  quando os avisos chegam.

## 0.9.0

- **o canvas do ratt db**: `d` na tela do banco abre o diagrama das tabelas, desenhado no
  terminal. Cada tabela é uma caixa com as colunas e os tipos (`PK` na chave primária); cada
  chave estrangeira é uma linha entre as caixas, com o pé de galinha (`<`) do lado "muitos" e o
  `┼` do lado "um". A coluna que aceita vazio ganha um `o` na ponta da linha, e a tabela de
  junção aparece ligada às duas pontas — o N:N.
  - Num banco grande, `f` mostra só a tabela escolhida e as vizinhas (no máximo 25); `F` mostra
    tudo de novo. A tabela muito referenciada aparece recolhida com `←N`; `+` a abre. `s` troca
    o schema. Setas, `PageUp` e `PageDown` rolam; `shift` com as setas move a caixa.
  - Um `⚠` marca tabela sem chave primária e chave estrangeira sem índice ou que aceita vazio;
    a linha acima do painel do SQL diz em palavras os avisos do que está selecionado.
  - **Editar pelo desenho**: `a` nova tabela, `c` nova coluna, `l` liga duas colunas (ou liga
    duas tabelas "como N:N", e o ratt cria a tabela de junção), `espaço` abre o menu da coluna
    (renomear, obrigatória/opcional, única), `x` apaga — e mostra antes o que cai junto — e
    `u` desfaz.
  - **O rascunho**: nada vai para o banco ao editar. O SQL do que você fez fica sempre à vista,
    embaixo do desenho, cada tabela com o schema (`public.users`), e é guardado em
    `~/.local/share/ratt/canvas/{porta}-{banco}/`: se você sair e voltar, ele está lá. Cada
    formulário mostra antes os avisos do que a operação vai encontrar no banco (linhas com
    NULL, valores repetidos, linhas sem pai, o que depende do que você apaga). `r` põe o foco no
    painel do SQL. Esse SQL é só para ler e exportar; para mexer nele, `w` salva num `.sql`, e
    aí é o VS Code e o `ratt db run`.
  - **Aplicar e ensaiar**: `enter` mostra o SQL — e no alto o que ele apaga, com as linhas que
    somem quando foram contadas — e pede a confirmação antes de aplicar, tudo numa transação:
    se um comando falha, nada muda e a operação que falhou aparece marcada. O SQL aplicado fica
    em `aplicados/`, na mesma pasta do rascunho. `e` é o ensaio: roda tudo e desfaz no fim,
    igual ao `--dry-run`.
- corrigido: a tela do `ratt db` agora percebe quando mudam as restrições, o NOT NULL e os
  índices (um `ALTER TABLE` pelo `ratt db run` ou pelo `psql`) e se atualiza sozinha.

## 0.8.0

- **ratt db** (sem comando, num terminal): a tela do banco, no lugar do DBeaver. A árvore à
  esquerda — esquemas, tabelas (com o número de linhas), views, funções e sequências — e o
  objeto escolhido à direita, em quatro abas:
  - **Dados**: as linhas, com o tipo embaixo do nome da coluna, o vazio (NULL) `∅` em laranja e
    o texto com espaço sobrando em amarelo, e os avisos embaixo — a mesma releitura do `run`.
    200 linhas por página (`n` / `N`).
  - **Estrutura**: colunas, tipos, se aceitam vazio, valor padrão, chave primária e para onde
    cada chave estrangeira aponta; índices e restrições.
  - **Relações**: as chaves estrangeiras que saem da tabela e as que chegam nela.
  - **SQL**: o que recria o objeto (o mesmo que o `pg_dump` escreveria).
- a tela olha o banco a cada dois segundos: um `ratt db run` (ou o `psql`) que cria uma tabela
  ou muda dados aparece sozinho. Deixe aberta ao lado do VS Code.
- `p` abre o `psql` no banco e `\q` volta para a tela; `b` troca de banco; `r` recarrega.
- o ratt agora instala o `textual`, que desenha a tela.

## 0.7.0

- **ratt db run <arquivo.sql>**: o `.sql` que você escreve no VS Code, rodado no banco atual
  comando por comando. Cada um mostra o que fez (`3 linhas inseridas · 2 ms`) e um `SELECT`
  aparece relido: o tipo embaixo de cada coluna, o vazio (NULL) `∅` em laranja, o texto vazio `''`
  e o espaço sobrando nas pontas em amarelo, com os avisos embaixo da tabela.
- o arquivo inteiro é uma transação: se um comando falha, os anteriores são desfeitos, e o erro
  aponta **a linha do arquivo**, com um `^` embaixo do lugar quando o PostgreSQL diz onde foi.
- `--dry-run` roda tudo e desfaz no fim — "apagaria 2 linhas", sem apagar nada.
- `--watch` roda de novo a cada vez que o arquivo é salvo: deixe aberto no terminal do VS Code.
- `--autocommit` para os comandos que não rodam numa transação (`CREATE DATABASE`, `VACUUM`).
- o `;` dentro de texto, de comentário e de `$$ … $$` não corta o comando; comandos do psql
  (`\d`, `\i`) aparecem marcados e são pulados; `RAISE NOTICE` aparece embaixo do comando.

## 0.6.0

- **ratt db reset**: apaga todos os bancos e usuários e deixa o PostgreSQL como antes do setup
  (o pacote fica; a pasta de dados some — no Debian/Ubuntu, o cluster inteiro). Antes, mostra
  cada banco com tabelas e tamanho, oferece um backup de tudo (`pg_dumpall`, na sua pasta
  pessoal, só você lê) e pede que você **digite o nome da máquina** — a trava contra o terminal
  errado numa VM do trabalho. No fim, pergunta se já quer rodar o `ratt db setup`.
- `-y` sozinho não zera nada: precisa de `--confirm <nome da máquina>`. `--no-backup` pula o backup.
- as senhas de localhost no `~/.pgpass` saem junto, porque os usuários delas sumiram.
- corrigido: a ajuda do `ratt db connect` fazia o Python mostrar um aviso (`SyntaxWarning: "\q"`).

## 0.5.0

- **ratt db connect**: abre o `psql` num banco. Sem nome, mostra a lista numerada — o número
  escolhe, Enter abre o atual. O comando do `psql` aparece antes, igual ao que você digitaria.
- **ratt db list**: os bancos, com dono e tamanho; o ● marca o atual (o último aberto ou criado).
- **ratt db new <banco>**: cria um banco com você como dono, ou `--owner` outro usuário.
- **ratt db user**: os usuários, o que cada um pode e de quais bancos é dono. `user add <nome>`
  cria um usuário com senha (`--createdb`, `--superuser`); `user passwd [nome]` troca a senha e
  atualiza o `~/.pgpass` se ela estava lá.
- quando o seu usuário não tem poder para algo (criar outro usuário, dar um banco a outro dono),
  o ratt avisa e faz pelo superusuário `postgres`, via sudo.
- a senha vira hash SCRAM no próprio ratt, antes de ir ao servidor: o texto puro não chega lá.

## 0.4.0

- **ratt db setup**: o PostgreSQL do zero até o primeiro banco, em seis passos — instalar,
  inicializar, ligar (agora e no boot), login por senha, o seu usuário e o primeiro banco. Cada
  passo mostra o comando antes de rodar e confere antes de fazer: rodar de novo só pula o que já
  está pronto, e quando está tudo pronto ele diz "nada a fazer" sem pedir a senha do sudo.
- funciona nas três famílias: Debian/Ubuntu (apt), Fedora/Rocky/Alma/RHEL (dnf) e Arch (pacman).
  No Fedora troca o `ident` do `pg_hba.conf` por senha (com cópia `.bak`); no Arch inicializa já
  pedindo senha, em vez do `trust` que o `initdb` usa sem opções.
- usuário e banco com o nome que você quiser (`--user`, `--db`), em minúsculas como o PostgreSQL
  guarda; nome inválido é perguntado de novo. A senha pode ficar no `~/.pgpass`.
- `-y` faz tudo sem perguntar, para repetir a mesma configuração em outra máquina:
  `ratt db setup -y --user app --db sistema < senha.txt`
- `ratt db status` passa a conferir o usuário que o setup criou.

## 0.3.0

- **ratt db**: o começo do braço do PostgreSQL. `ratt db status` confere, em ordem, cada peça
  de que o banco precisa — o sistema, o sudo, a instalação, a pasta de dados, o serviço, a porta,
  como o login pela rede é feito e se você já entra — e diz o próximo passo. Não muda nada e não
  pede senha. Reconhece as famílias Debian (Ubuntu, Mint…), Red Hat (Fedora, Rocky, Alma…) e
  Arch (Manjaro, EndeavourOS…).
- `ratt db lang pt|en`: a interface do `ratt db` em português ou inglês. Os comandos e o SQL são
  os mesmos nos dois; `RATT_LANG=en` vale para uma execução.
- o ratt agora instala o `psycopg`, o driver do PostgreSQL.

## 0.2.0

- **tuplas**: um `print` que recebe uma tupla com objetos do pandas agora relê **cada item
  sozinho**, com o template dele. Vale para os três jeitos de montar a tupla:
  `return df, df.shape, df.head()` numa função, `x = df.shape, df.head()` e
  `print((df.shape, df.dtypes))`. O nome de cada item vem do seu código.
- `describe()` de muitas colunas de texto agora se divide em blocos que cabem na tela.

## 0.1.0

A primeira versão: 27 templates, em quatro grupos.

- **inspeção** — `shape`, `describe` (numérico e de texto), `head`/`tail`/`sample`, `print(df)`,
  `print(df["col"])`, `dtypes`, `columns`, `index`, `isna().sum()`, `nunique()`,
  `value_counts()` (contagem e proporção), `unique()`
- **transformação** — filtros, o molde genérico de tabela (o que mudou: linhas, ordem, colunas,
  vazios) e de coluna (quantos valores mudaram), reduções com o "de N"
- **agrupamento** — `groupby` (uma e duas chaves, `agg`), `pivot_table`/`unstack`, `crosstab`,
  `corr`, `pd.cut`/`pd.qcut`
- **tempo** — `to_datetime`, `.dt`, índice de datas (ordem, buracos, ritmo), `date_range`,
  `to_period`, `resample` com o "de N", `rolling`/`diff`/`shift`/`pct_change` com a série em
  ▁▂▃▄▅▆▇█
