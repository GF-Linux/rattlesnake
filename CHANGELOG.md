# Mudanças

O `ratt check-update` e o `ratt upgrade` leem este arquivo: cada `## versão` é o que você
vê antes de responder `[s/N]`.

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
