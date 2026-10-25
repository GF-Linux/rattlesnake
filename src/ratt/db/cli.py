"""ratt db: o comando do PostgreSQL. Por enquanto, o status e o idioma da interface."""
import getpass
import os
import sys

from ratt import __version__
from ratt.cli import c, _casa
from ratt.db import idioma, sistema, sonda
from ratt.db.idioma import t

COMANDOS = [("status", "ajuda.status"), ("lang [pt|en]", "ajuda.lang")]


def ajuda():
    w = max(len(n) for n in [n for n, _ in COMANDOS] + ["RATT_LANG=pt|en"]) + 3
    linhas = [
        f"{c('ratt db', 'azul', 'negrito')} {__version__} — {t('slogan')}",
        "",
        f"{c(t('uso'), 'negrito')} {t('uso.linha')}",
        "",
        c(t("comandos"), "negrito"),
    ]
    linhas += [f"  {n:<{w}}{c(t(d), 'cinza')}" for n, d in COMANDOS]
    linhas += [
        "",
        c(t("opcoes"), "negrito"),
        f"  {'-h, --help':<{w}}{c(t('ajuda.h'), 'cinza')}",
        "",
        c(t("variaveis"), "negrito"),
        f"  {'RATT_LANG=pt|en':<{w}}{c(t('ajuda.RATT_LANG'), 'cinza')}",
        f"  {'NO_COLOR':<{w}}{c(t('ajuda.NO_COLOR'), 'cinza')}",
    ]
    return "\n".join(linhas)


# ── o status ────────────────────────────────────────────────────────────────

_MARCA = {"ok": ("✓", "verde"), "falta": ("✗", "laranja"), "aviso": ("⚠", "amarelo"),
          "duvida": ("?", "amarelo"), "info": ("·", "cinza"), "depois": ("·", "cinza")}


def _linha(rotulo, estado, texto, w=16):
    marca, cor = _MARCA[estado]
    corpo = c(texto, "cinza") if estado == "depois" else texto
    print(f"  {c(marca, cor)} {rotulo:<{w}}{corpo}")


def _formatar_tamanho(n):
    for unidade in ("B", "kB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unidade}"
        n /= 1024
    return f"{n:.1f} GB"


def _entrar(pasta_socket, porta, usuario):
    """Uma conexão aberta e como ela entrou, ou (None, None). Primeiro o socket, depois a rede."""
    import psycopg
    tentativas = []
    if pasta_socket:
        tentativas.append((dict(host=pasta_socket), "s.como_socket"))
    tentativas.append((dict(host="localhost"), "s.como_pgpass"))
    for destino, como in tentativas:
        try:
            return psycopg.connect(port=porta, user=usuario, dbname="postgres", connect_timeout=3,
                                   autocommit=True, **destino), como
        except psycopg.Error:
            continue
    return None, None


def _bancos(conexao):
    return conexao.execute("""
        SELECT datname, pg_get_userbyid(datdba),
               CASE WHEN has_database_privilege(oid, 'CONNECT') THEN pg_database_size(oid) END
        FROM pg_database
        WHERE NOT datistemplate AND datname <> 'postgres'
        ORDER BY datname""").fetchall()


def cmd_status(args):
    os_release = sistema.ler_os_release()
    nome_sistema = os_release.get("PRETTY_NAME") or os_release.get("NAME") or "Linux"
    familia = sistema.familia(os_release)
    receita = sistema.RECEITAS.get(familia)
    porta = int(os.environ.get("PGPORT") or 5432)
    usuario = getpass.getuser()
    proximo = None

    print(f"{c('ratt db status', 'azul', 'negrito')} {c('· ' + nome_sistema, 'cinza')}")
    print()

    # o sistema e o sudo
    if receita:
        _linha(t("r.sistema"), "ok", t("s.familia", nome=receita.nome, gerenciador=receita.gerenciador))
        pode, grupo = sistema.pode_usar_sudo(receita)
        if grupo == "root":
            _linha(t("r.sudo"), "ok", t("s.sudo_root"))
        elif pode:
            _linha(t("r.sudo"), "ok", t("s.sudo_sim", grupo=grupo))
        else:
            _linha(t("r.sudo"), "aviso", t("s.sudo_nao", grupos=" / ".join(receita.grupos_sudo)))
    else:
        _linha(t("r.sistema"), "aviso", t("s.sistema_desconhecido"))

    # instalado, inicializado, serviço — só quando há receita para este sistema
    versao = sistema.versao_instalada(receita) if receita else None
    if receita:
        if versao:
            _linha(t("r.postgres"), "ok", t("s.instalado", versao=versao))
        else:
            _linha(t("r.postgres"), "falta", t("s.nao_instalado"))
            proximo = "ratt db setup"
        if versao:
            ativo, no_boot = sistema.servico(receita)
            pronto = sistema.inicializado(receita, ativo)
            pasta = receita.pasta_dados
            if familia == "debian" and sistema.clusters_debian():
                pasta = ", ".join(cl.pasta for cl in sistema.clusters_debian())
            if pronto:
                _linha(t("r.dados"), "ok", t("s.inicializado", pasta=pasta))
            elif pronto is None:
                _linha(t("r.dados"), "duvida", t("s.inicializado_duvida", pasta=pasta))
            else:
                _linha(t("r.dados"), "falta", t("s.nao_inicializado"))
                proximo = proximo or "ratt db setup"
            if ativo is None:
                _linha(t("r.servico"), "info", t("s.sem_systemd"))
            else:
                texto = f"{t('s.rodando') if ativo else t('s.parado')} · " \
                        f"{t('s.liga_boot') if no_boot else t('s.nao_liga_boot')}"
                _linha(t("r.servico"), "ok" if ativo and no_boot else ("aviso" if ativo else "falta"), texto)
                if not (ativo and no_boot):
                    proximo = proximo or "ratt db setup"
        else:
            for rotulo in ("r.dados", "r.servico"):
                _linha(t(rotulo), "depois", t("s.depois"))

    # a porta e o login: valem em qualquer sistema, mesmo sem receita
    resposta = sonda.sondar(porta=porta, usuario=usuario)
    if resposta.porta_aberta:
        _linha(t("r.porta"), "ok", t("s.porta_aberta", porta=porta))
        if resposta.pede_senha:
            _linha(t("r.login"), "ok", t("s.pede_senha", metodo=resposta.metodo))
        elif resposta.metodo == "trust":
            _linha(t("r.login"), "aviso", t("s.trust"))
            proximo = proximo or "ratt db setup"
        elif resposta.metodo == "recusa":
            if familia == "redhat":
                _linha(t("r.login"), "falta", t("s.recusa_ident", nome=nome_sistema.split(" (")[0]))
            else:
                _linha(t("r.login"), "falta", t("s.recusa"))
            proximo = proximo or "ratt db setup"
        else:
            _linha(t("r.login"), "duvida", t("s.metodo_outro", metodo=resposta.metodo or "?"))
    else:
        if versao or not receita:
            _linha(t("r.porta"), "falta", t("s.porta_fechada", porta=porta))
            proximo = proximo or "ratt db setup"
        else:
            _linha(t("r.porta"), "depois", t("s.depois"))
        _linha(t("r.login"), "depois", t("s.depois"))

    # o seu acesso e os bancos
    conexao, como = (None, None)
    if resposta.porta_aberta:
        conexao, como = _entrar(sistema.pasta_socket(porta), porta, usuario)
    if conexao:
        with conexao:
            _linha(t("r.acesso"), "ok", t("s.entra", usuario=usuario, como=t(como)))
            bancos = _bancos(conexao)
        if not bancos:
            _linha(t("r.bancos"), "info", t("s.nenhum_banco"))
        for i, (nome, dono, tamanho) in enumerate(bancos):
            tam = _formatar_tamanho(tamanho) if tamanho is not None else t("s.sem_permissao")
            _linha(t("r.bancos") if i == 0 else "", "info",
                   f"{c(nome, 'negrito')}  {c(t('s.banco', dono=dono) + ' · ' + tam, 'cinza')}")
    elif resposta.porta_aberta:
        _linha(t("r.acesso"), "falta", t("s.sem_acesso", usuario=usuario))
        proximo = proximo or "ratt db setup"
    else:
        _linha(t("r.acesso"), "depois", t("s.depois"))

    print()
    if not receita and not resposta.porta_aberta:
        print(c(t("s.manual"), "cinza"))
    elif proximo:
        print(f"{t('s.proximo')} {c(proximo, 'azul', 'negrito')}")
    else:
        print(c(t("s.tudo_certo"), "verde"))
    return 0


# ── o idioma ────────────────────────────────────────────────────────────────

def cmd_lang(args):
    if not args:
        print(t("lang.atual", idioma=idioma.atual()))
    elif args[0] not in idioma.IDIOMAS:
        print(c(t("lang.invalido", idioma=args[0]), "laranja"))
        return 1
    else:
        idioma.gravar(args[0])
        print(t("lang.trocado", idioma=args[0]))
    if os.environ.get("RATT_LANG"):
        print(c(t("lang.env", idioma=os.environ["RATT_LANG"]), "cinza"))
    return 0


EXECUTA = {"status": (cmd_status, 0), "lang": (cmd_lang, 1)}     # comando: (função, máximo de argumentos)


# ── a entrada ───────────────────────────────────────────────────────────────

def _erro_de_uso(msg):
    print(f"ratt db: {msg} ({t('erro.veja')})", file=sys.stderr)
    raise SystemExit(2)


def main(args):
    comando, resto, pediu_ajuda = None, [], False
    for a in args:
        if a in ("-h", "--help"):
            pediu_ajuda = True
        elif a.startswith("-"):
            _erro_de_uso(t("erro.opcao", nome=a))
        elif comando is None:
            comando = a
        else:
            resto.append(a)
    if comando is None:
        print(ajuda())
        return 0
    if comando not in EXECUTA:
        _erro_de_uso(t("erro.comando", nome=comando))
    if pediu_ajuda:
        print(t(f"ajuda_cmd.{comando}"))
        return 0
    funcao, maximo = EXECUTA[comando]
    if len(resto) > maximo:
        _erro_de_uso(t("erro.argumento", nome=resto[maximo]))
    return funcao(resto)
