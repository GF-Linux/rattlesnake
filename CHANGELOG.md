# Mudanças

O `ratt check-update` e o `ratt upgrade` leem este arquivo: cada `## versão` é o que você
vê antes de responder `[s/N]`.

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
