"""ratt db: o comando do PostgreSQL — a ajuda, o status, o idioma, e a entrada de todos os outros."""
import getpass
import os
import sys

from ratt import __version__
from ratt.cli import c, _casa
from ratt.db import acesso, config, configurar, gerenciar, idioma, rodar, sistema, sonda, zerar
from ratt.db.idioma import t

COMANDOS = [     # (comando, o argumento na ajuda, o que faz)
    ("status", "", "ajuda.status"),
    ("setup", "", "ajuda.setup"),
    ("list", "", "ajuda.list"),
    ("connect", "arg.connect", "ajuda.connect"),
    ("run", "arg.run", "ajuda.run"),
    ("new", "arg.new", "ajuda.new"),
    ("user", "arg.user", "ajuda.user"),
    ("reset", "", "ajuda.reset"),
    ("lang", "arg.lang", "ajuda.lang"),
]


def ajuda():
    nomes = [f"{cmd} {t(arg)}" if arg else cmd for cmd, arg, _ in COMANDOS]
    w = max(len(n) for n in nomes + ["RATT_LANG=pt|en"]) + 3
    linhas = [
        f"{c('ratt db', 'azul', 'negrito')} {__version__} — {t('slogan')}",
        "",
        f"{c(t('uso'), 'negrito')} {t('uso.linha')}",
        f"     ratt db{' ' * 12}{c(t('ajuda.tela'), 'cinza')}",
        "",
        c(t("comandos"), "negrito"),
    ]
    linhas += [f"  {n:<{w}}{c(t(d), 'cinza')}" for n, (_, _, d) in zip(nomes, COMANDOS)]
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


def cmd_status(args, op):
    os_release = sistema.ler_os_release()
    nome_sistema = os_release.get("PRETTY_NAME") or os_release.get("NAME") or "Linux"
    familia = sistema.familia(os_release)
    receita = sistema.RECEITAS.get(familia)
    lembrado = config.ler().get("db") or {}
    porta = int(os.environ.get("PGPORT") or lembrado.get("porta") or 5432)
    usuario = lembrado.get("usuario") or getpass.getuser()
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
        conexao, como = acesso.entrar(porta, usuario)
    if conexao:
        with conexao:
            _linha(t("r.acesso"), "ok", t("s.entra", usuario=usuario, como=t(como)))
            bancos = acesso.bancos(conexao)
        if not bancos:
            _linha(t("r.bancos"), "info", t("s.nenhum_banco"))
        for i, (nome, dono, tamanho) in enumerate(bancos):
            tam = acesso.tamanho(tamanho) if tamanho is not None else t("s.sem_permissao")
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

def cmd_lang(args, op):
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


EXECUTA = {     # comando: (função, máximo de argumentos)
    "status": (cmd_status, 0),
    "setup": (configurar.cmd_setup, 0),
    "list": (gerenciar.cmd_list, 0),
    "connect": (gerenciar.cmd_connect, 1),
    "run": (rodar.cmd_run, 1),
    "new": (gerenciar.cmd_new, 1),
    "user": (gerenciar.cmd_user, 2),
    "reset": (zerar.cmd_reset, 0),
    "lang": (cmd_lang, 1),
}


# ── a entrada ───────────────────────────────────────────────────────────────

_OPCOES_PADRAO = {"user": None, "db": None, "owner": None, "confirm": None, "superuser": False, "createdb": False,
                  "no_backup": False, "dry_run": False, "watch": False, "autocommit": False, "yes": False}
_COM_VALOR = {"--user": "user", "--db": "db", "--owner": "owner", "--confirm": "confirm"}


def _erro_de_uso(msg):
    print(f"ratt db: {msg} ({t('erro.veja')})", file=sys.stderr)
    raise SystemExit(2)


def _ler_argumentos(args):
    comando, resto, op, pediu_ajuda = None, [], dict(_OPCOES_PADRAO), False
    i = 0
    while i < len(args):
        a = args[i]
        nome, _, valor = a.partition("=")
        if a in ("-h", "--help"):
            pediu_ajuda = True
        elif a in ("-y", "--yes"):
            op["yes"] = True
        elif a in ("--superuser", "--createdb", "--no-backup", "--dry-run", "--watch", "--autocommit"):
            op[a[2:].replace("-", "_")] = True
        elif nome in _COM_VALOR:
            if not valor:
                i += 1
                if i >= len(args):
                    _erro_de_uso(t("erro.precisa_valor", nome=nome))
                valor = args[i]
            op[_COM_VALOR[nome]] = valor
        elif a.startswith("-"):
            _erro_de_uso(t("erro.opcao", nome=a))
        elif comando is None:
            comando = a
        else:
            resto.append(a)
        i += 1
    return comando, resto, op, pediu_ajuda


def main(args):
    comando, resto, op, pediu_ajuda = _ler_argumentos(args)
    if comando is None:
        if pediu_ajuda or not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(ajuda())
            return 0
        from ratt.db import tela          # o textual só carrega quando a tela abre
        return tela.abrir(op)
    if comando not in EXECUTA:
        _erro_de_uso(t("erro.comando", nome=comando))
    if pediu_ajuda:
        print(t(f"ajuda_cmd.{comando}"))
        return 0
    funcao, maximo = EXECUTA[comando]
    if len(resto) > maximo:
        _erro_de_uso(t("erro.argumento", nome=resto[maximo]))
    return funcao(resto, op)
