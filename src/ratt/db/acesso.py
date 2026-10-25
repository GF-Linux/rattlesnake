"""Entrar no banco como o usuário de quem roda o ratt — sem sudo e sem perguntar senha.

Primeiro pelo socket local (peer: o nome do Linux é o nome no banco), depois pela rede com a
senha do ~/.pgpass. Se nenhum dos dois entra, quem decide o que fazer é quem chamou.
"""
from ratt.db import sistema


def entrar(porta, usuario, banco="postgres"):
    """(conexão, como) — como é "s.como_socket" ou "s.como_pgpass" — ou (None, None)."""
    import psycopg
    tentativas = []
    pasta_socket = sistema.pasta_socket(porta)
    if pasta_socket:
        tentativas.append((pasta_socket, "s.como_socket"))
    tentativas.append(("localhost", "s.como_pgpass"))
    for host, como in tentativas:
        try:
            return psycopg.connect(host=host, port=porta, user=usuario, dbname=banco, connect_timeout=3,
                                   autocommit=True), como
        except psycopg.Error:
            continue
    return None, None


def bancos(conexao):
    """(nome, dono, tamanho) de cada banco que não é do sistema; tamanho None sem permissão de ver."""
    return conexao.execute("""
        SELECT datname, pg_get_userbyid(datdba),
               CASE WHEN has_database_privilege(oid, 'CONNECT') THEN pg_database_size(oid) END
        FROM pg_database
        WHERE NOT datistemplate AND datname <> 'postgres'
        ORDER BY datname""").fetchall()


def tamanho(n):
    """Bytes em B, kB, MB ou GB; — quando não há permissão para ver."""
    if n is None:
        return "—"
    for unidade in ("B", "kB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unidade}"
        n /= 1024
    return f"{n:.1f} GB"
