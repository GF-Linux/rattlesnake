# Pesquisa: schemas maduros e filtros do dia a dia

Data: 2026-11-01. Feita para embasar o canvas do `ratt db` (fase 1) e o filtro de linhas
(fase 2). Os números saem de contagem por regex nos arquivos baixados (margem ~±15%); o que é
interpretação está marcado como **opinião**.

## Fontes

- Schemas: Pagila, Chinook (PostgreSQL), Northwind (porta PostgreSQL), AdventureWorks para
  PostgreSQL, GitLab `db/structure.sql` (master), Mastodon `db/schema.rb`.
- Lista oficial de comandos do PostgreSQL 18 (183 entradas).
- Tratamento de dados: cópia pública de 2021 do projeto dbt do time de dados do GitLab
  (`sayanchaudhury/gitlab_dbt_analytics`, 2076 arquivos `.sql`, dialeto Snowflake), dbt-utils,
  dbt-expectations, Great Expectations (62 expectations), Mastodon `app/models` e workers,
  perguntas mais votadas de PostgreSQL no Stack Overflow / DBA Stack Exchange.

## 1. Tamanho dos schemas (pesa no canvas)

| Fonte | Tabelas | FKs | |
|---|---|---|---|
| Chinook | 11 | 11 | cabe numa tela |
| Northwind | 14 | 13 | |
| Pagila | 16 (+55 partições) | 19 | |
| AdventureWorks | 68 em 5 schemas | 90 | |
| Mastodon | 122 | 162 | 86 FKs apontam para `accounts` |
| GitLab | 1136 (+339 partições) | 1928 | `projects` recebe 378, `users` 210 |

**Opinião:** até ~20 tabelas o diagrama inteiro cabe; ~70 pede agrupar por schema; acima de 100,
só com foco (tabela + vizinhas) e contador nas tabelas-centro.

## 2. Padrões estruturais recorrentes

- **Tabela de ligação N:N** — em todas as fontes (`film_actor`, `playlist_track`,
  `employee_territories`, `accounts_tags`…).
- **Auto-referência** — Chinook, Northwind, AdventureWorks, Mastodon (4), GitLab (15).
- **FK composta** — rara (1 no AdventureWorks; 55 no GitLab, quase todas por particionamento).
- **Colunas de auditoria** — quase universais em aplicações (GitLab 1020 `created_at`).
- **Particionamento** — Pagila (`payment`, 55 partições), GitLab (106 tabelas).
- **CHECK** — muitos no GitLab (2674) e AdventureWorks (89); zero no Mastodon (valida no Rails).
- **ON DELETE CASCADE** — 87% das FKs do GitLab, 77% do Mastodon. **Opinião:** a prévia da
  cascata é o recurso mais útil para quem aprende e para quem cuida de banco real.

## 3. Comandos e o que vira ação visual (opinião)

- **Bons para a fase 1:** `CREATE TABLE`, `ALTER TABLE` (colunas, NOT NULL, DEFAULT, PK, FK,
  UNIQUE), `DROP TABLE` com prévia, `CREATE INDEX`, `COMMENT`.
- **Bons depois:** `CREATE/DROP ROLE`, `GRANT/REVOKE` como matriz usuários × tabelas; INSERT por
  formulário; UPDATE/DELETE com filtro (fase 2).
- **Ruins de desenhar:** funções, triggers, políticas (são lógica); manutenção (`VACUUM`,
  `REINDEX`) vira botão com explicação, não desenho; `EXPLAIN` em árvore é a exceção visual.
- **Armadilha documentada:** `DROP ROLE` falha se o usuário possui objetos; o caminho é
  `REASSIGN OWNED` → `DROP OWNED` (em cada banco) → `DROP ROLE`.

## 4. Filtros do dia a dia (fase 2)

**Operadores, por frequência:** `=` › intervalo (`<`, `>`, `BETWEEN`, quase sempre data) ›
`IS [NOT] NULL` › booleano › `IN`/`NOT IN` › `<>` › contém › começa com › termina com › regex ›
`EXISTS`. Do `LIKE`: 76% é "contém", 21% prefixo, 3% sufixo.

**Transformações na coluna:** minúsculas/`ILIKE` › data relativa (`now() - interval`) ›
`::date` › parte da data (mês, ano, dia da semana) › `coalesce`/`nullif` › `trim` ›
`date_trunc` › `length` › `substring`.

**Rotinas que se repetem:** apagar duplicatas mantendo uma (a mais frequente e mais perguntada) ·
vazio = nulo ou texto em branco · `trim` e caixa · padronizar valores (`'canceled'`,
`'cancelled'` → `'cancelado'`) · apagar órfãos (`NOT EXISTS`) · apagar/arquivar antigos (retenção)
· anonimizar em vez de apagar · soft delete · corrigir datas guardadas como texto · manter só os N
mais recentes por grupo.

**Veredito sobre a gramática** `coluna → transformação → operador → valor`, com E/OU/NÃO:
cobre ~95% dos predicados de uma tabela no dbt e ~80–85% dos filtros do Mastodon. **Falha** nas
dúvidas de DELETE/UPDATE mais votadas no Stack Overflow (duplicatas, alterar com dados de outra
tabela, top-N por grupo). Para chegar a ~95% do uso real de limpeza, faltam três extensões:

1. modo **"duplicatas por (colunas): manter a mais recente / as N últimas"** (`row_number()`);
2. **"existe / não existe em outra tabela, onde …"** com sub-filtro;
3. **"vazio"** e **data relativa** como itens prontos do menu.

E: "contém" padrão em `ILIKE`; avisar que `<>` e `NOT IN` deixam de fora as linhas com NULL.
