"""O servidor lido do catálogo do PostgreSQL: papéis, quem é membro de quem, bancos e quem entra neles; e, dentro
de um banco, o que um papel possui e os privilégios que tem e deu — o que o aviso de remoção mostra.

Tudo só lê: cada consulta é um `SELECT` só (`catalogo.executar`, protocolo estendido) dentro de
`catalogo.somente_leitura`, com tempo limite. Papéis chegam por NOME (como a pessoa os vê) e vão como parâmetro —
nunca colados no SQL; o nome vira papel por `to_regrole(quote_ident(nome))`, exato (maiúsculas e espaços valem).
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Tuple

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
    """O servidor inteiro em 3 consultas: papéis, quem é membro de quem (e se a pertença herda) e os bancos com o
    CONNECT do `datacl` (sem `datacl`, o padrão: PUBLIC e o dono entram). Os papéis `pg_*` do PostgreSQL ficam no
    modelo, marcados `sistema` (um deles pode ter CONNECT e passá-lo a quem é membro); as telas os escondem.
    `eu` é o current_user da conexão (o ratt nunca faz SET ROLE, então é o usuário com que ele entrou).
    Quem deu cada CONNECT (o grantor do `datacl`, com a opção de conceder) e, no 16+, cada pertença (uma linha de
    `pg_auth_members` por `grantor`, com INHERIT e SET dela) vai em `concedido_por`: o rascunho usa para saber o que
    cai junto quando quem deu é removido.
    `banco_atual` é o banco desta conexão (o current_database()), que as travas não deixam a pessoa perder.
    Herança: no 16+ cada pertença diz (`inherit_option`; `set_option` só para mostrar); até o 15 decide o
    `rolinherit` do membro, para todas as pertenças dele."""
    with catalogo.somente_leitura(conexao, limite_ms):
        linhas = catalogo.executar(conexao, """
            SELECT r.oid, r.rolname, r.rolcanlogin, r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolinherit,
                   r.rolname = current_user, current_setting('server_version_num')::int
            FROM pg_roles r
            ORDER BY r.rolname""").fetchall()
        versao = linhas[0][-1] if linhas else 0
        if versao >= 160000:
            membros = catalogo.executar(conexao, """
                SELECT m.member, m.roleid, bool_or(m.inherit_option), bool_or(m.set_option),
                       array_agg(m.grantor ORDER BY m.grantor), array_agg(m.inherit_option ORDER BY m.grantor),
                       array_agg(m.set_option ORDER BY m.grantor)
                FROM pg_auth_members m
                GROUP BY m.member, m.roleid
                ORDER BY m.member, m.roleid""").fetchall()
        else:
            membros = catalogo.executar(conexao, """
                SELECT DISTINCT m.member, m.roleid, r.rolinherit, true, '{}'::oid[], '{}'::bool[], '{}'::bool[]
                FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
                ORDER BY m.member, m.roleid""").fetchall()
        bancos = catalogo.executar(conexao, """
            SELECT d.oid, d.datname, d.datdba, d.datallowconn, d.datistemplate, d.datname = current_database(),
                   coalesce(array_agg(DISTINCT a.grantee ORDER BY a.grantee)
                            FILTER (WHERE a.grantee <> 0 AND a.privilege_type = 'CONNECT'), '{}'),
                   coalesce(bool_or(a.grantee = 0 AND a.privilege_type = 'CONNECT'), false),
                   coalesce(array_agg(a.grantee ORDER BY a.grantee, a.grantor)
                            FILTER (WHERE a.privilege_type = 'CONNECT'), '{}'),
                   coalesce(array_agg(a.grantor ORDER BY a.grantee, a.grantor)
                            FILTER (WHERE a.privilege_type = 'CONNECT'), '{}'),
                   coalesce(array_agg(a.is_grantable ORDER BY a.grantee, a.grantor)
                            FILTER (WHERE a.privilege_type = 'CONNECT'), '{}')
            FROM pg_database d
            LEFT JOIN LATERAL aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a ON true
            GROUP BY d.oid, d.datname, d.datdba, d.datallowconn, d.datistemplate
            ORDER BY d.datname""").fetchall()
    papeis: Dict[str, Papel] = {}
    eu = ""
    for oid, nome, login, super_, cria_bancos, cria_papeis, herda, sou_eu, _ in linhas:
        papeis[f"r{oid}"] = Papel(f"r{oid}", nome, login, super_, cria_bancos, cria_papeis, herda=herda,
                                  sistema=nome.startswith("pg_"))
        if sou_eu:
            eu = f"r{oid}"
    pertencas: Dict[str, List[Tuple[str, bool, bool, Dict[str, Tuple[bool, bool]]]]] = {}
    for membro, grupo, herda, set_, quem, herdas, sets in membros:
        if f"r{membro}" in papeis and f"r{grupo}" in papeis:
            linhas_por = {f"r{x}": (h, st) for x, h, st in zip(quem, herdas, sets)}    # uma linha por concedente
            pertencas.setdefault(f"r{membro}", []).append((f"r{grupo}", herda, set_, linhas_por))
    for id, ps in pertencas.items():
        papeis[id].membro_de = tuple(g for g, _, _, _ in ps)
        papeis[id].nao_herda = tuple(g for g, herda, _, _ in ps if not herda)
        papeis[id].sem_set = tuple(g for g, _, set_, _ in ps if not set_)
        papeis[id].concedido_por = {g: por for g, _, _, por in ps if por}
    modelo_bancos = {}
    for oid, nome, dono, aceita, template, _, diretos, publico, quem_tem, quem_deu, com_opcao in bancos:
        concedido: Dict[str, Dict[str, bool]] = {}
        for tem, deu, opcao in zip(quem_tem, quem_deu, com_opcao):
            chave = "PUBLIC" if tem == 0 else f"r{tem}"
            if tem == 0 or chave in papeis:
                concedido.setdefault(chave, {})[f"r{deu}"] = opcao
        modelo_bancos[f"d{oid}"] = Banco(f"d{oid}", nome, f"r{dono}", aceita,
                                         tuple(f"r{g}" for g in diretos if f"r{g}" in papeis), publico,
                                         template=template, concedido_por=concedido)
    atual = next((f"d{linha[0]}" for linha in bancos if linha[5]), None)
    return ModeloServidor(papeis, modelo_bancos, eu, versao, atual)


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


class Privilegios(NamedTuple):
    """Os privilégios de um papel nas tabelas, views, sequências e schemas de um banco (cada privilégio de cada
    objeto conta um)."""
    tem: int                # dados a ele por outro (o DROP OWNED os tira)
    deu_como_dono: int      # que ele deu no que é dele (o REASSIGN OWNED os passa ao novo dono, como concedente)
    deu_por_opcao: int      # que ele deu pelo GRANT OPTION no que não é dele (o DROP OWNED os tira, de quem recebeu)


def privilegios_no_banco(conexao, papel_nomes: List[str], limite_ms: int = _LIMITE_MS) -> Dict[str, Privilegios]:
    """Por papel, no banco desta conexão: os privilégios que ele tem e os que deu — como dono e pelo GRANT OPTION
    (PUBLIC conta como quem recebe). Os do dono sobre o que é dele (de si para si) não contam."""
    with catalogo.somente_leitura(conexao, limite_ms):
        linhas = catalogo.executar(conexao, f"""
            WITH alvo AS ({_ALVO}),
            acl AS (
                SELECT c.relowner AS dono, x.grantor, x.grantee
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) x
                WHERE c.relacl IS NOT NULL AND n.nspname NOT IN {_SISTEMA}
                UNION ALL
                SELECT n.nspowner, x.grantor, x.grantee
                FROM pg_namespace n, aclexplode(n.nspacl) x
                WHERE n.nspacl IS NOT NULL AND n.nspname NOT IN {_SISTEMA})
            SELECT a.nome,
                   count(*) FILTER (WHERE acl.grantee = a.papel AND acl.grantor <> a.papel),
                   count(*) FILTER (WHERE acl.grantor = a.papel AND acl.grantee <> a.papel AND acl.dono = a.papel),
                   count(*) FILTER (WHERE acl.grantor = a.papel AND acl.grantee <> a.papel AND acl.dono <> a.papel)
            FROM alvo a LEFT JOIN acl ON a.papel IN (acl.grantee, acl.grantor)
            GROUP BY a.nome""", (_nomes(papel_nomes),)).fetchall()
    return {nome: Privilegios(int(tem), int(dono), int(opcao)) for nome, tem, dono, opcao in linhas}


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
