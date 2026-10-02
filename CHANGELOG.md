# Mudanças

O `ratt check-update` e o `ratt upgrade` leem este arquivo: cada `## versão` é o que você
vê antes de responder `[s/N]`.

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
