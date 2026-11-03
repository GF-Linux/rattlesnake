# Usuários e permissões no ratt db (fase 3)

Data: 2026-11-03 · Branch: `usuarios`, a partir de `dados` (0.10.0) · Fase 3 de 4

Fases anteriores: 1 — o canvas (`2026-11-01-canvas-db-design.md`); 2 — os dados
(`2026-11-02-dados-design.md`). Pesquisa: `2026-11-01-pesquisa-schemas-e-filtros.md`, seção 3 (DCL)
e 4 ("operações em lote"). A pessoa delegou as decisões desta fase (vai testar ao fim dela e pedir
mudanças); cada decisão abaixo é minha e está marcada.

## Para que serve

Hoje o `ratt db user` lista, cria e troca a senha de usuários pela linha de comando; na tela não há
nada. Quem aprende PostgreSQL tropeça justamente aqui: quem pode entrar em qual banco, quem é dono do
quê, por que o `DROP ROLE` falha ("o usuário possui objetos"), o que é `GRANT … ON ALL TABLES IN
SCHEMA`, o que é um grupo. A fase 3 mostra isso em grades — quem × o quê — e faz as mudanças pelo
mesmo caminho das fases 1 e 2: rascunho com o SQL à vista, ensaio, confirmação.

## Objetivos

1. **Ver** os usuários (papéis) do servidor: quem entra, os poderes (`SUPERUSER`, `CREATEDB`,
   `CREATEROLE`, `LOGIN`), de quais grupos faz parte, de quais bancos é dono e em quais pode entrar.
2. **Gerenciar usuários** pela tela: criar, mudar poderes, trocar senha, pôr e tirar de grupos,
   dar e tirar o acesso a um banco (`CONNECT`), trocar o dono de um banco, e **remover um ou vários**
   do jeito certo (`REASSIGN OWNED` → `DROP OWNED` em cada banco → `DROP ROLE`).
3. **Permissões nas tabelas**: uma grade usuários × tabelas do schema, com os privilégios
   (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES`, `TRIGGER`), marcação em bloco
   (linha, coluna, "todas do schema"), `USAGE`/`CREATE` no schema e a opção "também as tabelas que
   forem criadas depois" (`ALTER DEFAULT PRIVILEGES`).
4. Tudo **avisa antes**: o que o usuário possui em cada banco antes de removê-lo, quem perderia o
   acesso, o que o PostgreSQL recusaria e por quê.

## Fora do escopo

- Segurança por linha (`CREATE POLICY`/RLS), `SECURITY LABEL`, `SET ROLE` (são de sessão ou
  expressão — a pesquisa os classificou como ruins de desenhar).
- Permissões em colunas, funções, sequências e tipos (podem vir depois; a fase cobre tabelas,
  schema e banco).
- Editar o `pg_hba.conf` (quem entra por onde) — já é do `ratt db setup`.
- Manutenção e o que falta na estrutura (fase 4).

## Anti-objetivos

- Uma **senha** gravada em disco (rascunho, `.sql` exportado, histórico) ou mostrada na tela.
- Remover usuário **perdendo dados sem avisar**: `DROP OWNED` apaga o que o usuário possui e não
  foi passado para outro dono — o ratt sempre passa antes (`REASSIGN OWNED`) e mostra o que passa.
- A pessoa **se trancar para fora**: remover o próprio usuário, o `postgres`, o último superusuário,
  ou tirar de si mesma o acesso ao banco em que está — recusado com o motivo.
- Algo ir para o servidor sem a pessoa ter visto o SQL e confirmado (como nas fases 1 e 2).

## Restrições

As das fases 1 e 2 continuam: Python ≥ 3.9; sem dependências novas; textos em `idioma.py` (pt e en);
nomes por `rascunho.ident()`; valores por literal citado; leituras só `SELECT` em transação somente
leitura, uma instrução, com tempo limite; PostgreSQL 13+. E mais:

- **Papéis são do servidor inteiro**, não de um banco; `GRANT` em tabela é de um banco e schema.
  Por isso a fase tem **dois rascunhos**: o do servidor (papéis, grupos, poderes, acesso a bancos,
  dono de banco, remoção) e o do schema que já existe (permissões em tabelas e no schema, dividido
  com o canvas e a tela Dados).
- Remover um usuário toca **vários bancos**; o PostgreSQL não tem transação entre bancos. O aplicar
  do rascunho do servidor roda **um banco por vez, cada um numa transação**, e para no primeiro
  erro, dizendo o que já foi feito.

## Abordagens consideradas

| | Como funciona | Troca |
|---|---|---|
| **A. Grades** *(escolhida)* | Tela "Usuários": grade usuários × bancos (D dono, C entra, — nada), com os poderes e grupos ao lado. Tela "Permissões": grade usuários × tabelas do schema. Mesmo molde das telas da fase 2 (sessão, painel, aplicar). | Escala com muitos usuários e tabelas, é o formato que a pesquisa sugeriu para `GRANT`, e reaproveita o que já existe; perde o "desenho" com setas. |
| B. Canvas com setas usuários → bancos | Caixas de usuários à esquerda, bancos à direita, linhas de dono/acesso, como o canvas de tabelas. | Mais visual; mas vira emaranhado com 20 usuários × 10 bancos, e a grade de permissões não cabe em setas. |
| C. Só formulários sobre a lista atual | Lista de usuários com um menu de ações. | Simples; mas não mostra "quem pode o quê" de uma vez, que é o que ensina. |

Escolhida **A**. *(Decisão minha: a pessoa delegou.)*

## O que foi decidido

### Telas e como se chega

- **Usuários** — da tela do `ratt db`, tecla `u`. Mostra o servidor:
  ```
  ratt db · localhost:5432 › Usuários · rascunho: 1
  usuário        poderes                 grupos        estudos  sistema  lhv_setup
  jared   ●      superusuário            —             D        C        D
  app            entra, cria bancos      leitura       C        D        —
  leitura        não entra (grupo)       —             C        C        —
  + ana          entra                   leitura       C        —        —      ← pendente
  ┌ SQL ─────────────────────────────────────────────────────────────────────────┐
  │ -- 1. criar o usuário ana                                                    │
  │ CREATE ROLE ana LOGIN;                                                       │
  │ -- a senha de ana é pedida ao aplicar (ou use \password ana no psql)         │
  └──────────────────────────────────────────────────────────────────────────────┘
  a novo · espaço poderes/senha · g grupos · c acesso ao banco · o dono do banco · x remover · P permissões · enter aplicar · e ensaio
  ```
  `D` = dono do banco, `C` = pode entrar (`CONNECT`), `—` = não pode. Clicar numa célula
  alterna o acesso (`C`/`—`); `o` numa célula de banco troca o dono.
- **Permissões** — da tela Usuários (`P`, para o banco atual e um schema escolhido), do canvas e da
  tela Dados (`P`, para o schema deles). Grade usuários × tabelas:
  ```
  ratt db · estudos › public › Permissões · rascunho: 2
  schema public: app USAGE · leitura USAGE
                 pessoas      pedidos      itens
  app            SIUD···      SIUD···      SIUD···
  leitura        S······      S······      S······
  ana            ·······      ·······      ·······   ← sem acesso
  ```
  Cada célula mostra os 7 privilégios por letra (`S`elect `I`nsert `U`pdate `D`elete
  `T`runcate `R`eferences `t`rigger); o dono da tabela aparece como `dono`. Selecionar células
  (uma, uma linha, uma coluna, ou "todas") e `espaço` abre o formulário de privilégios: marcar o que
  conceder e o que revogar, e a opção "também as tabelas criadas depois neste schema".
- As duas telas usam o mesmo pedaço comum das fases anteriores (painel do SQL, `enter` aplicar com
  confirmação mostrando o SQL, `e` ensaio, `w` exportar, `u` desfazer… — *atenção:* `u` na tela do
  `ratt db` abre Usuários; dentro das telas, `u` continua sendo desfazer).

### O rascunho do servidor

- Uma sessão de rascunho nova, por servidor (porta), guardada como as outras
  (`~/.local/share/ratt/canvas/{porta}-servidor/`), com as mesmas garantias (nunca se perde,
  arquivo ilegível vai para o lado, inválidas marcadas).
- Operações (mesmo molde das fases 1–2: `refere()` por identidade estável — o oid do papel e do
  banco; nomes esperados guardados):
  - `CriarPapel(nome, login, superusuario, cria_bancos, cria_papeis)` → `CREATE ROLE nome [LOGIN]
    [SUPERUSER] [CREATEDB] [CREATEROLE]`; com `LOGIN`, a senha é pedida na hora de aplicar.
  - `MudarPoderes(papel, …)` → `ALTER ROLE nome [NO]LOGIN [NO]SUPERUSER …`.
  - `TrocarSenha(papel)` → no SQL mostrado e exportado, só um comentário; ao aplicar, `ALTER ROLE
    nome PASSWORD '<hash>'` com a senha pedida na hora.
  - `PorNoGrupo(papel, grupo)` / `TirarDoGrupo` → `GRANT grupo TO papel` / `REVOKE grupo FROM papel`.
  - `DarAcesso(papel, banco)` / `TirarAcesso` → `GRANT CONNECT ON DATABASE banco TO papel` /
    `REVOKE …` (e o aviso de que `PUBLIC` também pode estar dando acesso: o ratt mostra e oferece
    tirar de `PUBLIC`).
  - `TrocarDonoDoBanco(banco, papel)` → `ALTER DATABASE banco OWNER TO papel`.
  - `RemoverPapeis(papeis, novo_dono)` — **uma operação para um ou vários usuários**; o SQL tem os
    passos em ordem, por banco:
    ```
    -- no banco estudos
    REASSIGN OWNED BY ana, bruno TO jared;
    DROP OWNED BY ana;
    DROP OWNED BY bruno;
    -- no banco sistema
    REASSIGN OWNED BY ana, bruno TO jared;
    DROP OWNED BY ana;
    DROP OWNED BY bruno;
    -- no servidor
    DROP ROLE ana, bruno;
    ```
    Bancos que não aceitam conexão (`datallowconn = false`, como `template0`) ficam fora, e os
    bancos de que eles são donos passam por `ALTER DATABASE … OWNER TO` antes.
- **Senhas:** nunca no rascunho, no `.sql` exportado nem no histórico de aplicados. O SQL mostrado e
  exportado **não tem cláusula de senha** (rodar o `.sql` noutro lugar cria o usuário sem senha; um
  comentário diz para usar `\password`). Ao aplicar, na tela suspensa, o ratt pede a senha de
  cada usuário que precisa (com confirmação), transforma em hash SCRAM no próprio ratt (como o
  `ratt db user add`) e manda só o hash. Ensaio não pede senha (usa um hash descartável).
- **Aplicar em vários bancos:** os comandos são agrupados por banco de destino, na ordem; cada
  grupo roda numa conexão para aquele banco, numa transação; os comandos do servidor (`CREATE/DROP/
  ALTER ROLE`, `GRANT papel`, `GRANT CONNECT`, `ALTER DATABASE`) rodam na conexão ao banco atual,
  por último quando há remoção. Para no primeiro erro e diz: "feito em estudos; falhou em sistema;
  não rodado: servidor". O rascunho guarda o que ficou por fazer. O ensaio roda cada grupo e
  desfaz.
- **Sem poder suficiente** (o usuário da pessoa não é superusuário nem tem `CREATEROLE`): no aplicar,
  um erro de permissão oferece — na tela suspensa, com o comando à vista — rodar o mesmo `.sql` como
  `postgres` pelo `sudo` (como o `ratt db user add` já faz), banco por banco. *(Decisão minha.)*

### Permissões em tabelas (rascunho do schema)

- Operações novas no rascunho do schema (o mesmo do canvas e da tela Dados):
  - `Conceder(papeis, tabelas, privilegios, futuras)` / `Revogar(…)` →
    `GRANT SELECT, INSERT ON public.pessoas, public.pedidos TO app, leitura;` — **um comando com as
    listas** (é o idiomático e ensina o `[, …]`); "todas do schema" vira `… ON ALL TABLES IN SCHEMA
    public …`; `futuras` acrescenta `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT … TO …`.
  - `PermissaoNoSchema(papel, usage, create)` → `GRANT/REVOKE USAGE, CREATE ON SCHEMA public …`.
- Avisos: conceder em tabela sem `USAGE` no schema → "ainda não vai conseguir ver: falta USAGE no
  schema" (oferece junto); revogar de si mesmo → atenção; o dono da tabela já tem tudo (a célula
  diz `dono`); `ALTER DEFAULT PRIVILEGES` só vale para tabelas criadas pelo papel que roda o
  comando — dito em palavras.

### Avisos de usuário (servidor)

- Remover: por banco, o que cada usuário possui (tabelas, views, funções, sequências, schemas —
  contagem por tipo) e tem de privilégio; quem fica como novo dono (padrão: o usuário da pessoa;
  pode escolher); o que é dono de banco. Recusa remover: o próprio usuário, `postgres`, o último
  superusuário, um papel com sessões abertas (mostra quantas) — este último como aviso que impede,
  com a dica de esperar ou encerrar.
- Tirar `CONNECT` de si mesmo no banco atual → impede; tirar `SUPERUSER` do último → impede.
- `PUBLIC` com `CONNECT` (padrão do PostgreSQL) → aviso informativo na grade: "todo mundo pode
  entrar neste banco (PUBLIC)", com a opção de revogar.

### Decisões depois da revisão do construtor (todas minhas)

1. **Remoção sempre como superusuário.** `RemoverPapeis` (e mudar `SUPERUSER`) só aplica com um
   superusuário: o da própria pessoa, se ela for, ou o `postgres` pelo `sudo`. Isso tira do caminho
   as regras de `CREATEROLE`/`ADMIN`/`SET` que mudaram no 16 e garante que `REASSIGN OWNED` e
   `DROP OWNED` podem agir em qualquer banco.
2. **O aviso de remoção lista**, por banco: o que o papel possui (contagem por tipo, que passa para
   o novo dono), os privilégios que ele **tem** e os que ele **deu** a outros (ambos somem no
   `DROP OWNED`), e as permissões padrão (`pg_default_acl`) dele. Bancos em que a pessoa não consegue
   entrar aparecem como "não verificado — só um superusuário vê"; o aplicar, como superusuário, entra.
3. **Conferência antes de aplicar (pré-voo), relida na hora:** sessões abertas dos papéis, bancos que
   não aceitam conexão (`datallowconn = false`, fora `template0`) em que eles possam ter algo, o novo
   dono existe e pode entrar. Qualquer problema recusa **antes de mudar qualquer coisa**. O ratt não
   liga `ALLOW_CONNECTIONS` sozinho: diz o banco e o comando que a pessoa pode rodar.
4. **Falha no meio da remoção:** a operação fica marcada "parcial: feito em X; falta Y". Aplicar de
   novo **recalcula tudo pelo catálogo** (o `REASSIGN`/`DROP OWNED` repetido não faz mal) — não confia
   no que foi visto antes. O ensaio roda e desfaz banco por banco; o texto do ensaio diz que cada
   banco é conferido no estado atual (o banco seguinte não vê o efeito do anterior).
5. **Ordem de aplicação do rascunho do servidor:** as operações comuns rodam na ordem do rascunho,
   numa transação, na conexão ao banco atual (todas são do servidor e valem de qualquer banco); a
   remoção roda **por último**: um passo por banco (cada um numa transação), depois o
   `ALTER DATABASE … OWNER` dos bancos de que eles eram donos e o `DROP ROLE`. Operações que referem
   um papel removido no mesmo rascunho ficam inválidas.
6. **Papel criado no rascunho** recebe um id `novo-p{n}` (como `novo-t` da fase 1); as operações
   seguintes o referem por esse id. Na grade de permissões (rascunho do schema) só aparecem papéis
   que já existem; conceder a um papel pendente pede "aplique primeiro o rascunho do servidor".
   Se um papel some do servidor, as operações do rascunho do schema que o referem ficam inválidas
   (o rebase já faz isso pelo id). Os dois rascunhos aplicam separados.
7. **Acesso direto e efetivo:** na grade de bancos, `C` = acesso dado diretamente, `c` = acesso
   herdado (por `PUBLIC` ou por um grupo), `—` = sem acesso. Clicar num `c` explica de onde vem e
   oferece tirar do `PUBLIC` ou do grupo. A linha **`PUBLIC`** aparece nas duas grades (bancos e
   tabelas/schema), editável.
8. **"Nunca se trancar para fora" vale para todos os caminhos**, pelo acesso efetivo da pessoa
   depois da mudança: remover o próprio papel, o `postgres` ou o último superusuário **com `LOGIN`**;
   tirar de si `LOGIN` ou `SUPERUSER`; tirar `CONNECT` (direto, do `PUBLIC` ou de um grupo) ou sair
   de um grupo, ou trocar o dono do banco, quando isso deixaria a pessoa sem entrar no banco em que
   está → impede, com o motivo.
9. **Senhas:** o hash SCRAM vai ao servidor dentro do comando; o README avisa que, com
   `log_statement` ligado, o log do servidor pode guardar o **hash** (nunca a senha). Pelo caminho do
   `sudo`, o SQL vai pela **entrada padrão** do `psql` (não por arquivo temporário nem por argumento
   visível no `ps`). O ratt gera sempre SCRAM; se o `password_encryption` do servidor for `md5`, avisa.
   O ensaio usa um hash descartável.
10. **Sem `ON ALL TABLES IN SCHEMA`:** "todas" vira a lista explícita das tabelas que a grade mostra
    (o `ALL TABLES` pegaria também views e tabelas estrangeiras, que a grade não mostra). **Futuras**
    gera `ALTER DEFAULT PRIVILEGES FOR ROLE <papel da pessoa> IN SCHEMA s GRANT … TO …` — explícito,
    com o texto "vale para tabelas que <papel> criar"; revogar com "futuras" gera o `REVOKE`
    correspondente.
11. **Grupos:** `GRANT grupo TO papel` com as opções padrão do servidor; `INHERIT`/`SET` (16+)
    aparecem só para ler. Sem poder suficiente para uma operação comum, o erro do aplicar oferece o
    caminho do `sudo` (não há pré-checagem por versão).
12. **Versão testada:** o PostgreSQL local (18); o código lê a versão e trata as diferenças conhecidas
    só onde muda o comando (nenhuma das operações acima muda de sintaxe entre 13 e 18).

## Partes do código que mudam (mapa, não plano)

- Novo, puro: as operações do servidor e de permissões (no `rascunho.py` ou num módulo irmão),
  com SQL por banco de destino.
- Novo: leitura do servidor (`pg_roles`, `pg_auth_members`, `pg_database` com `datacl`/`datdba`,
  posse por banco via `pg_shdepend`/`pg_class.relowner`, privilégios por `aclexplode(relacl)`,
  sessões em `pg_stat_activity`).
- `sessao.py`: sessão do servidor; aplicar por grupos de banco; senha pedida na tela suspensa.
- Novo: telas Usuários e Permissões e seus formulários.
- `tela.py` (`u`), `canvas.py` e `tela_dados.py` (`P`), `idioma.py`, testes (puros e com
  PostgreSQL — criando papéis descartáveis `ratt_teste_*` e removendo no fim), README/CHANGELOG
  (0.11.0).

## Decisões que tomei

- Grades em vez de canvas com setas (abordagem A).
- Dois rascunhos: servidor e schema.
- Senha pedida só ao aplicar, hash SCRAM no cliente, nunca gravada.
- `RemoverPapeis` como uma operação para vários usuários, com o passo a passo por banco.
- Aplicar do servidor banco por banco, cada um numa transação, parando no primeiro erro.
- Sem poder suficiente: oferecer rodar como `postgres` pelo `sudo`, com o comando à vista.
- Teclas: `u` (tela do `ratt db` → Usuários), `P` (canvas/Dados/Usuários → Permissões); dentro
  das telas, as de sempre do rascunho.
- Privilégios cobertos: os 7 de tabela (sem `MAINTAIN` do 17, para valer do 13 ao 18) + `USAGE`/
  `CREATE` de schema + `CONNECT` de banco.
- Versão 0.11.0.

## Deixado para o construtor

- Disposição exata das grades e formulários (as teclas aparecem no rodapé).
- Como paginar grades largas (muitas tabelas ou bancos): rolagem horizontal como a grade da fase 2.
- Formato interno das operações novas no JSON (lendo rascunhos antigos).
