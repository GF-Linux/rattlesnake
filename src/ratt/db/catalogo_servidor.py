"""O servidor lido do catálogo do PostgreSQL: papéis, quem é membro de quem, bancos e quem entra neles; e, dentro
de um banco, o que um papel possui e os privilégios que tem e deu — o que o aviso de remoção mostra.

Tudo só lê: cada consulta é um `SELECT` só (`catalogo.executar`, protocolo estendido) dentro de
`catalogo.somente_leitura`, com tempo limite. Papéis chegam por NOME (como a pessoa os vê) e vão como parâmetro —
nunca colados no SQL; o nome vira papel por `to_regrole(quote_ident(nome))`, exato (maiúsculas e espaços valem).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from ratt.db import catalogo
from ratt.db.servidor import Banco, ModeloServidor, Papel

_LIMITE_MS = 5000

# os esquemas do próprio PostgreSQL: o que o superusuário de instalação "possui" ali não é dele para passar adiante
_SISTEMA = "('pg_catalog', 'information_schema', 'pg_toast')"

# o nome pedido e o papel dele (NULL se não existe); `%s` é a lista de nomes
_ALVO = "SELECT nome, to_regrole(quote_ident(nome))::oid AS papel FROM unnest(%s::text[]) AS nome"


def _nomes(nomes) -> List[str]:
    """Os nomes pedidos, sem repetição (repetido, o `unnest` contaria em dobro)."""
    return list(dict.fromkeys(nomes))


def modelo_servidor(conexao, limite_ms: int = _LIMITE_MS) -> ModeloServidor:
    """O servidor inteiro em 3 consultas: papéis (sem os `pg_*`), quem é membro de quem e os bancos com o CONNECT
    do `datacl` (sem `datacl`, o padrão: PUBLIC e o dono entram). `eu` é o papel da conexão."""
    with catalogo.somente_leitura(conexao, limite_ms):
        linhas = catalogo.executar(conexao, """
            SELECT r.oid, r.rolname, r.rolcanlogin, r.rolsuper, r.rolcreatedb, r.rolcreaterole,
                   r.rolname = current_user, current_setting('server_version_num')::int
            FROM pg_roles r
            WHERE r.rolname !~ '^pg_'
            ORDER BY r.rolname""").fetchall()
        membros = catalogo.executar(conexao, """
            SELECT DISTINCT m.member, m.roleid
            FROM pg_auth_members m
            ORDER BY m.member, m.roleid""").fetchall()
        bancos = catalogo.executar(conexao, """
            SELECT d.oid, d.datname, d.datdba, d.datallowconn, d.datistemplate,
                   coalesce(array_agg(DISTINCT a.grantee ORDER BY a.grantee)
                            FILTER (WHERE a.grantee <> 0 AND a.privilege_type = 'CONNECT'), '{}'),
                   coalesce(bool_or(a.grantee = 0 AND a.privilege_type = 'CONNECT'), false)
            FROM pg_database d
            LEFT JOIN LATERAL aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a ON true
            GROUP BY d.oid, d.datname, d.datdba, d.datallowconn, d.datistemplate
            ORDER BY d.datname""").fetchall()
    papeis: Dict[str, Papel] = {}
    eu, versao = "", 0
    for oid, nome, login, super_, cria_bancos, cria_papeis, sou_eu, versao in linhas:
        papeis[f"r{oid}"] = Papel(f"r{oid}", nome, login, super_, cria_bancos, cria_papeis)
        if sou_eu:
            eu = f"r{oid}"
    grupos: Dict[str, List[str]] = {}
    for membro, grupo in membros:
        if f"r{membro}" in papeis and f"r{grupo}" in papeis:
            grupos.setdefault(f"r{membro}", []).append(f"r{grupo}")
    for id, gs in grupos.items():
        papeis[id].membro_de = tuple(gs)
    modelo_bancos = {
        f"d{oid}": Banco(f"d{oid}", nome, f"r{dono}", aceita, tuple(f"r{g}" for g in diretos if f"r{g}" in papeis),
                         publico, template=template)
        for oid, nome, dono, aceita, template, diretos, publico in bancos}
    return ModeloServidor(papeis, modelo_bancos, eu, versao)


def sessoes(conexao, papeis: List[str], limite_ms: int = _LIMITE_MS) -> Dict[str, int]:
    """Quantas sessões abertas cada papel (por nome) tem no servidor, em qualquer banco — fora a desta conexão e os
    processos do próprio servidor (o lançador da replicação lógica roda com o nome do superusuário de instalação,
    sem banco). Sem permissão de ver a sessão de outro papel, o `backend_type` vem vazio: conta como sessão."""
    with catalogo.somente_leitura(conexao, limite_ms):
        linhas = catalogo.executar(conexao, """
            SELECT u.nome, count(a.pid)
            FROM unnest(%s::text[]) AS u(nome)
            LEFT JOIN pg_stat_activity a
              ON a.usename = u.nome AND a.pid <> pg_backend_pid() AND a.datid IS NOT NULL
             AND coalesce(a.backend_type, 'client backend') = 'client backend'
            GROUP BY u.nome""", (_nomes(papeis),)).fetchall()
    return {nome: int(n) for nome, n in linhas}


def posse_no_banco(conexao, papel_nomes: List[str], limite_ms: int = _LIMITE_MS) -> Dict[str, Dict[str, int]]:
    """O que cada papel possui no banco desta conexão, por tipo: tabela, tabela_estrangeira, view,
    view_materializada, sequencia, funcao, procedimento, agregado, schema, tipo (enum, domínio, range, composto
    avulso — não os tipos de linha nem os de lista que vêm com tabelas e tipos). Papel sem nada (ou que não existe)
    → {}."""
    with catalogo.somente_leitura(conexao, limite_ms):
        linhas = catalogo.executar(conexao, f"""
            WITH alvo AS ({_ALVO}),
            objetos AS (
                SELECT c.relowner AS dono,
                       CASE c.relkind WHEN 'r' THEN 'tabela' WHEN 'p' THEN 'tabela' WHEN 'f' THEN 'tabela_estrangeira'
                                      WHEN 'v' THEN 'view' WHEN 'm' THEN 'view_materializada'
                                      ELSE 'sequencia' END AS tipo
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'p', 'f', 'v', 'm', 'S') AND n.nspname NOT IN {_SISTEMA}
                UNION ALL
                SELECT p.proowner, CASE p.prokind WHEN 'p' THEN 'procedimento' WHEN 'a' THEN 'agregado'
                                                  ELSE 'funcao' END
                FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname NOT IN {_SISTEMA}
                UNION ALL
                SELECT n.nspowner, 'schema' FROM pg_namespace n
                WHERE n.nspname NOT IN {_SISTEMA} AND n.nspname !~ '^pg_(toast|temp)'
                UNION ALL
                SELECT t.typowner, 'tipo'
                FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                LEFT JOIN pg_class c ON c.oid = t.typrelid
                WHERE n.nspname NOT IN {_SISTEMA}
                  AND (t.typtype IN ('e', 'd', 'r') OR (t.typtype = 'c' AND c.relkind = 'c')))
            SELECT a.nome, o.tipo, count(*)
            FROM alvo a JOIN objetos o ON o.dono = a.papel
            GROUP BY a.nome, o.tipo""", (_nomes(papel_nomes),)).fetchall()
    posse: Dict[str, Dict[str, int]] = {nome: {} for nome in papel_nomes}
    for nome, tipo, n in linhas:
        posse[nome][tipo] = int(n)
    return posse


def privilegios_no_banco(conexao, papel_nomes: List[str], limite_ms: int = _LIMITE_MS) -> Dict[str, Tuple[int, int]]:
    """Por papel: (quantos privilégios ele TEM, dados por outro; quantos ele DEU a outros, PUBLIC incluído) nas
    tabelas, views, sequências e schemas do banco desta conexão — cada privilégio de cada objeto conta um. Os do
    dono sobre o que é dele (de si para si) não contam."""
    with catalogo.somente_leitura(conexao, limite_ms):
        linhas = catalogo.executar(conexao, f"""
            WITH alvo AS ({_ALVO}),
            acl AS (
                SELECT (aclexplode(c.relacl)).* FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relacl IS NOT NULL AND n.nspname NOT IN {_SISTEMA}
                UNION ALL
                SELECT (aclexplode(n.nspacl)).* FROM pg_namespace n
                WHERE n.nspacl IS NOT NULL AND n.nspname NOT IN {_SISTEMA})
            SELECT a.nome,
                   count(*) FILTER (WHERE acl.grantee = a.papel AND acl.grantor <> a.papel),
                   count(*) FILTER (WHERE acl.grantor = a.papel AND acl.grantee <> a.papel)
            FROM alvo a LEFT JOIN acl ON a.papel IN (acl.grantee, acl.grantor)
            GROUP BY a.nome""", (_nomes(papel_nomes),)).fetchall()
    return {nome: (int(tem), int(deu)) for nome, tem, deu in linhas}


def permissoes_padrao(conexao, papel_nomes: List[str], limite_ms: int = _LIMITE_MS) -> Dict[str, int]:
    """Por papel: quantas permissões padrão (`ALTER DEFAULT PRIVILEGES`, `pg_default_acl`) do banco desta conexão
    são dele (FOR ROLE ele) ou o citam como quem recebe — o `DROP OWNED` leva as duas."""
    with catalogo.somente_leitura(conexao, limite_ms):
        linhas = catalogo.executar(conexao, f"""
            WITH alvo AS ({_ALVO})
            SELECT a.nome, count(d.oid)
            FROM alvo a LEFT JOIN pg_default_acl d
              ON d.defaclrole = a.papel OR a.papel IN (SELECT x.grantee FROM aclexplode(d.defaclacl) x)
            GROUP BY a.nome""", (_nomes(papel_nomes),)).fetchall()
    return {nome: int(n) for nome, n in linhas}


def password_encryption(conexao, limite_ms: int = _LIMITE_MS) -> str:
    """Como o servidor guarda senhas novas: "scram-sha-256" ou "md5" (o ratt sempre manda SCRAM; md5 pede aviso)."""
    with catalogo.somente_leitura(conexao, limite_ms):
        return catalogo.executar(conexao, "SELECT current_setting('password_encryption')").fetchone()[0]
