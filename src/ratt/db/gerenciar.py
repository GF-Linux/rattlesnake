"""ratt db list, connect, new e user: o dia a dia depois do setup.

Tudo entra com o seu usuário (socket local ou ~/.pgpass). Quando a operação pede mais poder do
que ele tem — criar outro usuário, por exemplo —, o ratt avisa e faz pelo superusuário postgres,
via sudo, do mesmo jeito que o setup.
"""
import getpass
import os
import shlex
import shutil
import sys

from ratt.cli import c, _casa
from ratt.db import acesso, config, configurar
from ratt.db.idioma import t


def _contexto(op):
    """(porta, usuario, banco atual): o que o setup lembrou, ou o --user da linha de comando."""
    lembrado = config.ler().get("db") or {}
    porta = int(os.environ.get("PGPORT") or lembrado.get("porta") or 5432)
    return porta, op.get("user") or lembrado.get("usuario") or getpass.getuser(), lembrado.get("banco")


def _lembrar(**campos):
    lembrado = config.ler().get("db") or {}
    lembrado.update(campos)
    config.gravar(db=lembrado)


def _conectar(porta, usuario):
    conexao, como = acesso.entrar(porta, usuario)
    if not conexao:
        print(c("  " + t("g.sem_acesso", usuario=usuario), "laranja"))
        raise SystemExit(1)
    return conexao, como


def _executar(conexao, comando):
    """Roda com o seu usuário; sem permissão, roda como postgres (sudo). True se deu certo."""
    import psycopg
    try:
        conexao.execute(comando)
        return True
    except psycopg.errors.InsufficientPrivilege:
        pass
    usuario = conexao.info.user
    print(c(f"  {t('g.via_postgres', usuario=usuario)}", "amarelo"))
    if not configurar.garantir_sudo():
        print(c(t("c.sudo_falhou"), "laranja"))
        return False
    try:
        configurar._psql(comando.as_string(conexao))
    except configurar.Falha as e:
        print(c(f"  ✗ {e.saida.splitlines()[-1] if e.saida else e.cmd}", "laranja"))
        return False
    return True


def _existe(conexao, tabela, coluna, nome):
    return conexao.execute(f"SELECT 1 FROM {tabela} WHERE {coluna} = %s", (nome,)).fetchone() is not None


def _tabela_de_bancos(bancos, atual, numerada=False):
    w = max([len(n) for n, _, _ in bancos] + [5])
    wd = max([len(d) for _, d, _ in bancos] + [4])
    for i, (nome, dono, tamanho) in enumerate(bancos, 1):
        marca = c(f"● {t('g.atual')}", "verde") if nome == atual else ""
        numero = c(f"{i:>3}", "amarelo", "negrito") + "  " if numerada else "  "
        print(f"  {numero}{c(nome.ljust(w), 'negrito')}  {c(dono.ljust(wd), 'cinza')}  "
              f"{c(acesso.tamanho(tamanho).rjust(8), 'cinza')}  {marca}")


# ── list ────────────────────────────────────────────────────────────────────

def cmd_list(args, op):
    porta, usuario, atual = _contexto(op)
    conexao, _ = _conectar(porta, usuario)
    with conexao:
        bancos = acesso.bancos(conexao)
    print(f"{c('ratt db list', 'azul', 'negrito')} {c(f'· {usuario} @ localhost:{porta}', 'cinza')}")
    print()
    if not bancos:
        print(c(f"  {t('g.nenhum_banco')}", "cinza"))
        return 0
    _tabela_de_bancos(bancos, atual)
    print()
    print(c(f"  {t('g.dica_connect')}", "cinza"))
    return 0


# ── connect ─────────────────────────────────────────────────────────────────

def _escolher(bancos, atual):
    """O número digitado, ou Enter para o atual. None = sair."""
    _tabela_de_bancos(bancos, atual, numerada=True)
    print(f"  {c(f'{0:>3}', 'amarelo', 'negrito')}  {t('g.sair')}")
    padrao = next((i for i, (n, _, _) in enumerate(bancos, 1) if n == atual), None)
    while True:
        try:
            escolha = input(f"\n{t('g.qual')} " + (c(f"[{padrao}] ", "cinza") if padrao else "")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not escolha and padrao:
            return bancos[padrao - 1][0]
        if escolha in ("0", "q"):
            return None
        if escolha.isdigit() and 1 <= int(escolha) <= len(bancos):
            return bancos[int(escolha) - 1][0]
        nomes = [n for n, _, _ in bancos]
        if escolha in nomes:
            return escolha
        print(c("  " + t("g.escolha_numero"), "laranja"))


def cmd_connect(args, op):
    porta, usuario, atual = _contexto(op)
    if not shutil.which("psql"):
        print(c("  " + t("g.sem_psql"), "laranja"))
        return 1
    conexao, como = _conectar(porta, usuario)
    with conexao:
        bancos = acesso.bancos(conexao)
    nomes = [n for n, _, _ in bancos]

    if args:
        banco = args[0]
        if banco not in nomes and banco != "postgres":
            print(c("  " + t("g.banco_nao_existe", banco=banco), "laranja"))
            if bancos:
                _tabela_de_bancos(bancos, atual)
            return 1
    elif not bancos:
        print(c("  " + t("g.nenhum_banco"), "cinza"))
        return 1
    elif not sys.stdin.isatty():
        if atual not in nomes:
            print(c("  " + t("g.sem_terminal_connect"), "laranja"))
            return 1
        banco = atual
    else:
        print(f"{c('ratt db connect', 'azul', 'negrito')} {c(f'· {usuario} @ localhost:{porta}', 'cinza')}")
        print()
        banco = _escolher(bancos, atual)
        if banco is None:
            return 0

    _lembrar(banco=banco)
    # como o ratt entrou é como o psql entra: pelo socket (sem senha) ou pela rede (~/.pgpass)
    cmd = ["psql"] + (["-h", "localhost"] if como == "s.como_pgpass" else [])
    cmd += (["-p", str(porta)] if porta != 5432 else []) + ["-U", usuario, banco]
    print(c(f"$ {shlex.join(cmd)}", "cinza"))
    sys.stdout.flush()
    os.execvp("psql", cmd)


# ── new ─────────────────────────────────────────────────────────────────────

def cmd_new(args, op):
    from psycopg import sql
    porta, usuario, _ = _contexto(op)
    if not args:
        print(c("  " + t("g.new_sem_nome"), "laranja"))
        return 2
    banco = configurar.normalizar(args[0], op["yes"])
    dono = configurar.normalizar(op["owner"], op["yes"]) if op["owner"] else usuario
    if not banco or not dono:
        return 1
    conexao, _ = _conectar(porta, usuario)
    with conexao:
        if _existe(conexao, "pg_database", "datname", banco):
            print(c("  " + t("g.banco_ja_existe", banco=banco), "amarelo"))
            return 1
        if not _existe(conexao, "pg_roles", "rolname", dono):
            print(c("  " + t("g.dono_nao_existe", dono=dono), "laranja"))
            return 1
        comando = sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(banco), sql.Identifier(dono))
        if not _executar(conexao, comando):
            return 1
    _lembrar(banco=banco)
    print(f"  {c('✓', 'verde')} {t('g.banco_criado', banco=banco, dono=dono)}")
    print(c(f"  {t('g.dica_entrar', banco=banco)}", "cinza"))
    return 0


# ── user ────────────────────────────────────────────────────────────────────

def _poderes(superusuario, cria_bancos, cria_usuarios):
    if superusuario:
        return t("g.superusuario")
    partes = [t("g.cria_bancos")] if cria_bancos else []
    partes += [t("g.cria_usuarios")] if cria_usuarios else []
    return ", ".join(partes) or t("g.so_entra")


def _user_list(porta, usuario):
    conexao, _ = _conectar(porta, usuario)
    with conexao:
        linhas = conexao.execute("""
            SELECT r.rolname, r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolcanlogin,
                   coalesce(string_agg(d.datname, ', ' ORDER BY d.datname), '')
            FROM pg_roles r
            LEFT JOIN pg_database d ON d.datdba = r.oid AND NOT d.datistemplate
            WHERE r.rolname !~ '^pg_'
            GROUP BY 1, 2, 3, 4, 5
            ORDER BY r.rolname = 'postgres', r.rolname""").fetchall()
    print(f"{c('ratt db user', 'azul', 'negrito')} {c(f'· localhost:{porta}', 'cinza')}")
    print()
    w = max(len(n) for n, *_ in linhas)
    poderes = [_poderes(sup, cb, cu) if entra else t("g.nao_entra") for _, sup, cb, cu, entra, _ in linhas]
    wp = max(len(p) for p in poderes)
    for (nome, sup, _, _, _, donos), p in zip(linhas, poderes):
        dono = c(t("g.dono_de", bancos=donos), "cinza") if donos else ""
        voce = c(f"● {t('g.voce')}", "verde") if nome == usuario else ""
        print(f"  {c(nome.ljust(w), 'negrito')}  {c(p.ljust(wp), 'amarelo' if sup else 'cinza')}  {dono}  {voce}".rstrip())
    return 0


def _guardar_senha(porta, nome, senha, padrao_sim):
    arquivo = os.path.expanduser("~/.pgpass")
    ja_tinha = os.path.exists(arquivo) and f"localhost:{porta}:*:{nome}:" in open(arquivo, encoding="utf-8").read()
    if ja_tinha or configurar._sim(f"  {t('g.guardar_senha', usuario=nome)}", padrao_sim):
        configurar.gravar_pgpass(porta, nome, senha)
        print(c(f"  {t('c.senha_guardada', arquivo=_casa(arquivo))}", "cinza"))


def _user_add(porta, usuario, nome, op):
    from psycopg import sql
    nome = configurar.normalizar(nome, op["yes"])
    if not nome:
        return 1
    conexao, _ = _conectar(porta, usuario)
    with conexao:
        if _existe(conexao, "pg_roles", "rolname", nome):
            print(c("  " + t("g.usuario_ja_existe", nome=nome), "amarelo"))
            return 1
        senha = configurar._ler_senha(nome)
        # a senha vira o hash SCRAM aqui, no cliente: o texto puro não vai para o servidor nem para o log
        hash_ = conexao.pgconn.encrypt_password(senha.encode(), nome.encode(), b"scram-sha-256").decode()
        poderes = "SUPERUSER" if op["superuser"] else ("CREATEDB" if op["createdb"] else "")
        comando = sql.SQL("CREATE ROLE {} LOGIN " + poderes + " PASSWORD {}").format(
            sql.Identifier(nome), sql.Literal(hash_))
        if not _executar(conexao, comando):
            return 1
    print(f"  {c('✓', 'verde')} {t('c.usuario_criado', usuario=nome, poderes=poderes or 'LOGIN')}")
    _guardar_senha(porta, nome, senha, padrao_sim=False)
    print(c(f"  {t('g.dica_dono', nome=nome)}", "cinza"))
    return 0


def _user_passwd(porta, usuario, nome):
    from psycopg import sql
    conexao, _ = _conectar(porta, usuario)
    with conexao:
        if not _existe(conexao, "pg_roles", "rolname", nome):
            print(c("  " + t("g.usuario_nao_existe", nome=nome), "laranja"))
            return 1
        senha = configurar._ler_senha(nome)
        hash_ = conexao.pgconn.encrypt_password(senha.encode(), nome.encode(), b"scram-sha-256").decode()
        comando = sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(nome), sql.Literal(hash_))
        if not _executar(conexao, comando):
            return 1
    print(f"  {c('✓', 'verde')} {t('g.senha_trocada', nome=nome)}")
    _guardar_senha(porta, nome, senha, padrao_sim=nome == usuario)
    return 0


def cmd_user(args, op):
    porta, usuario, _ = _contexto(op)
    if not args or args[0] == "list":
        if len(args) > 1:
            print(c(t("erro.argumento", nome=args[1]), "laranja"))
            return 2
        return _user_list(porta, usuario)
    if args[0] == "add":
        if len(args) < 2:
            print(c("  " + t("g.add_sem_nome"), "laranja"))
            return 2
        return _user_add(porta, usuario, args[1], op)
    if args[0] == "passwd":
        return _user_passwd(porta, usuario, args[1] if len(args) > 1 else usuario)
    print(c("  " + t("g.user_sub", nome=args[0]), "laranja"))
    return 2
