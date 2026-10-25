"""ratt db setup: do zero até o primeiro banco, em qualquer família que o ratt conhece.

Cada passo confere antes de fazer: rodar de novo não estraga nada, só pula o que já está pronto.
O que precisa de root passa pelo sudo, e o comando aparece antes de rodar — para quem quiser
aprender o que está por baixo, ou repetir à mão em outra máquina.
"""
import datetime
import getpass
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from ratt.cli import c, _casa
from ratt.db import acesso, config, sistema, sonda
from ratt.db.idioma import t

NOME_VALIDO = re.compile(r"[a-z_][a-z0-9_]{0,62}")

# os métodos que podem aparecer numa linha do pg_hba.conf
_METODOS_HBA = {"trust", "reject", "scram-sha-256", "md5", "password", "gss", "sspi", "ident", "peer",
                "ldap", "radius", "cert", "pam", "bsd", "oauth"}


class Falha(Exception):
    """Um passo que não deu certo: o comando e o fim do que ele escreveu."""

    def __init__(self, cmd, saida):
        super().__init__(cmd)
        self.cmd, self.saida = cmd, saida


# ── rodar como root e como postgres ─────────────────────────────────────────

def _root(cmd):
    return list(cmd) if os.geteuid() == 0 else ["sudo", *cmd]


def _postgres(cmd):
    return ["runuser", "-u", "postgres", "--", *cmd] if os.geteuid() == 0 else ["sudo", "-u", "postgres", *cmd]


def _executar(cmd, entrada=None, mostrar=True):
    """Roda e devolve a saída. O comando aparece antes (cinza), como quem digitou."""
    if mostrar:
        print(c(f"      $ {shlex.join(cmd)}", "cinza"))
    sys.stdout.flush()
    # cwd="/": o postgres não entra na pasta pessoal de ninguém, e o psql reclamaria
    p = subprocess.run(cmd, input=entrada, capture_output=True, text=True, cwd="/")
    if p.returncode != 0:
        raise Falha(shlex.join(cmd), (p.stdout + p.stderr).strip())
    return p.stdout.strip()


def _psql(sql, banco="postgres"):
    """SQL como o superusuário postgres, pelo socket local. O SQL vai pela entrada, não pela linha de
    comando: assim uma senha dentro dele não aparece para quem olhar o ps."""
    cmd = _postgres(["psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-d", banco])
    return _executar(cmd, entrada=sql, mostrar=False)


def garantir_sudo():
    """Pede a senha do sudo uma vez, se precisar. sudo -n true passa com NOPASSWD ou com a senha
    ainda lembrada; só então o sudo -v pergunta."""
    if os.geteuid() == 0:
        return True
    sys.stdout.flush()
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
        return True
    return subprocess.run(["sudo", "-v"]).returncode == 0


def identificador(nome):
    return '"' + nome.replace('"', '""') + '"'


def literal(texto):
    return "'" + texto.replace("'", "''") + "'"


# ── o pg_hba.conf ───────────────────────────────────────────────────────────

def ajustar_hba(texto):
    """(texto novo, mudanças). Pela rede, ident e trust viram scram-sha-256; no socket, trust vira peer.

    md5 fica como está: ele aceita as senhas SCRAM, e trocá-lo quebraria quem ainda tem senha
    guardada em md5 — numa máquina de trabalho, alguém pode ter.
    """
    linhas, mudancas = [], []
    for linha in texto.splitlines(keepends=True):
        pedacos = list(re.finditer(r"\S+", linha.split("#", 1)[0]))
        tipo = pedacos[0].group(0) if pedacos else ""
        novo = None
        for p in pedacos[3:]:
            metodo = p.group(0)
            if metodo in _METODOS_HBA:
                if tipo in ("host", "hostssl", "hostnossl") and metodo in ("ident", "trust"):
                    novo = "scram-sha-256"
                elif tipo == "local" and metodo == "trust":
                    novo = "peer"
                if novo:
                    mudancas.append((linha.strip(), metodo, novo))
                    linha = linha[:p.start()] + novo + linha[p.end():]
                break
        linhas.append(linha)
    return "".join(linhas), mudancas


# ── nomes e senha ───────────────────────────────────────────────────────────

def _perguntar(pergunta, padrao=""):
    try:
        resposta = input(f"  {pergunta} " + (c(f"[{padrao}] ", "cinza") if padrao else "")).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(1)
    return resposta or padrao


def _sim(pergunta, padrao_sim=True, sempre=False):
    if sempre:
        return True
    try:
        r = input(f"{pergunta} {'[S/n]' if padrao_sim else '[s/N]'} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return padrao_sim if not r else r in ("s", "sim", "y", "yes")


def normalizar(nome, sempre_sim):
    """O nome como o PostgreSQL guarda (minúsculas), ou None se o nome não serve."""
    if not NOME_VALIDO.fullmatch(nome.lower()):
        print(c(f"  ✗ {t('c.nome_invalido', nome=nome)}", "laranja"))
        return None
    if nome != nome.lower():
        if sempre_sim:
            print(c(f"  ⚠ {t('c.minusculas')}: {nome} → {nome.lower()}", "amarelo"))
        elif not _sim(c(f"  ⚠ {t('c.minusculas')}: ", "amarelo") + t("c.usar", nome=nome.lower()), True):
            return None
    return nome.lower()


def _nome(dado, pergunta, padrao, sempre):
    """O nome dado na linha de comando, ou perguntado até vir um que sirva. "" = pulou (só o banco)."""
    if dado is not None or sempre:
        nome = dado if dado is not None else padrao
        return normalizar(nome, sempre) if nome else ""
    while True:
        nome = _perguntar(pergunta, padrao)
        if not nome:
            return ""
        nome = normalizar(nome, sempre)
        if nome:
            return nome


def usuario_padrao():
    return re.sub(r"[^a-z0-9_]", "_", getpass.getuser().lower())


def _ler_senha(usuario):
    if not sys.stdin.isatty():
        # ratt db setup -y --user app < arquivo_com_a_senha   (ou echo … |)
        return sys.stdin.readline().rstrip("\n")
    while True:
        senha = getpass.getpass(f"  {t('c.senha', usuario=usuario)} ")
        if not senha:
            print(c(f"  {t('c.senha_vazia')}", "laranja"))
            continue
        if getpass.getpass(f"  {t('c.senha_de_novo')} ") == senha:
            return senha
        print(c(f"  {t('c.senha_diferente')}", "laranja"))


def gravar_pgpass(porta, usuario, senha):
    """A linha  localhost:porta:*:usuario:senha  no ~/.pgpass, que o psql e o psycopg leem sozinhos."""
    def escapar(s):
        return s.replace("\\", "\\\\").replace(":", "\\:")
    arquivo = Path.home() / ".pgpass"
    prefixo = f"localhost:{porta}:*:{escapar(usuario)}:"
    linhas = arquivo.read_text(encoding="utf-8").splitlines() if arquivo.exists() else []
    linhas = [l for l in linhas if not l.startswith(prefixo)] + [prefixo + escapar(senha)]
    arquivo.touch(mode=0o600, exist_ok=True)
    arquivo.chmod(0o600)        # o libpq ignora o arquivo se outros puderem ler
    arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return arquivo


# ── os passos de cada família ───────────────────────────────────────────────

def _comandos_instalar(receita):
    if receita.familia == "redhat":
        return [_root(["dnf", "install", "-y", *receita.pacotes])]
    if receita.familia == "debian":
        apt = ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get"]
        return [_root([*apt, "update"]), _root([*apt, "install", "-y", *receita.pacotes])]
    return [_root(["pacman", "-S", "--noconfirm", "--needed", *receita.pacotes])]


def _locale_para_initdb():
    """O idioma do sistema se ele existir na máquina (ordena com acento); senão C.UTF-8."""
    lang = os.environ.get("LANG", "")
    try:
        disponiveis = subprocess.run(["locale", "-a"], capture_output=True, text=True).stdout.split()
    except OSError:
        disponiveis = []
    simples = {d.lower().replace("-", ""): d for d in disponiveis}
    if lang and lang.lower().replace("-", "") in simples and "utf8" in lang.lower().replace("-", ""):
        return lang
    return "C.UTF-8"


def _comandos_inicializar(receita, versao):
    if receita.familia == "redhat":
        return [_root(["postgresql-setup", "--initdb"])]
    if receita.familia == "debian":
        return [_root(["pg_createcluster", versao.split(".")[0], "main"])]
    return [_postgres(["initdb", "-D", receita.pasta_dados, "--locale", _locale_para_initdb(), "-E", "UTF8",
                       "--auth-local=peer", "--auth-host=scram-sha-256"])]


def _comandos_ligar(receita, tem_systemd):
    if receita.familia == "debian":
        cmds = [_root(["pg_ctlcluster", cl.versao, cl.nome, "start"])
                for cl in sistema.clusters_debian() if not cl.online]
        if tem_systemd:
            cmds.insert(0, _root(["systemctl", "enable", receita.unidade]))
        return cmds
    if tem_systemd:
        return [_root(["systemctl", "enable", "--now", receita.unidade])]
    # sem systemd (um container): ninguém cria a pasta do socket nem liga o servidor no boot
    return [_root(["install", "-d", "-o", "postgres", "-g", "postgres", "-m", "2775", "/run/postgresql"]),
            _postgres(["pg_ctl", "-D", receita.pasta_dados, "-l", f"{receita.pasta_dados}/ratt-pg_ctl.log",
                       "-w", "start"])]


def _esperar_porta(porta, segundos=15):
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        if sonda.porta_aberta(porta=porta):
            return True
        time.sleep(0.5)
    return False


# ── o comando ───────────────────────────────────────────────────────────────

_ROTULOS = ("c.p.instalar", "c.p.inicializar", "c.p.ligar", "c.p.senha", "c.p.usuario", "c.p.banco")


def _ja_feito(receita, porta, usuario, banco, versao, tem_systemd):
    """{passo: True/False} — o que dá para saber sem sudo. False é "não está, ou não deu para ver":
    na dúvida o passo roda, e ele mesmo confere de novo antes de fazer."""
    porta_ok = bool(versao) and sonda.porta_aberta(porta=porta)
    ativo, no_boot = sistema.servico(receita) if versao else (None, None)
    feito = {1: bool(versao), 2: bool(versao) and (bool(ativo) or porta_ok)}
    feito[3] = feito[2] and (bool(ativo and no_boot) if tem_systemd else porta_ok)
    feito[4] = porta_ok and sonda.sondar(porta=porta, usuario=usuario).pede_senha
    feito[5] = feito[6] = False
    if porta_ok:
        conexao, _ = acesso.entrar(porta, usuario)
        if conexao:
            with conexao:
                feito[5] = True
                feito[6] = conexao.execute("SELECT 1 FROM pg_database WHERE datname = %s", (banco,)).fetchone() is not None
    if not banco:
        feito[6] = True
    return feito


def _passo(numero, rotulo, estado, texto):
    marca, cor = {"ok": ("✓", "verde"), "feito": ("✓", "verde"), "vai": ("→", "azul"),
                  "falta": ("✗", "laranja"), "pula": ("·", "cinza")}[estado]
    print(f"  {c(marca, cor)} {numero} {rotulo:<18}{c(texto, 'cinza') if estado in ('feito', 'pula') else texto}")


def cmd_setup(args, op):
    os_release = sistema.ler_os_release()
    nome_sistema = os_release.get("PRETTY_NAME") or "Linux"
    receita = sistema.RECEITAS.get(sistema.familia(os_release))
    porta = int(os.environ.get("PGPORT") or 5432)
    sempre = op["yes"]

    print(f"{c('ratt db setup', 'azul', 'negrito')} {c('· ' + nome_sistema, 'cinza')}")
    if not receita:
        print(c(f"  {t('s.manual')}", "laranja"))
        return 1
    pode, _ = sistema.pode_usar_sudo(receita)
    if not pode:
        print(c(f"  {t('c.sem_sudo', grupos=' / '.join(receita.grupos_sudo))}", "laranja"))
        return 1
    if not sempre and not sys.stdin.isatty():
        print(c(f"  {t('c.sem_terminal')}", "laranja"))
        return 1

    # os nomes: perguntados antes, para o resto correr sem parar
    if not sempre:
        print()
    usuario = _nome(op["user"], t("c.qual_usuario"), usuario_padrao(), sempre)
    banco = _nome(op["db"], t("c.qual_banco"), "", sempre)
    if usuario is None or banco is None:          # nome inválido dado na linha de comando
        return 1

    # o plano: confere sem sudo o que já está pronto, e mostra o que falta
    versao = sistema.versao_instalada(receita)
    tem_systemd = sistema.servico(receita)[0] is not None
    feito = _ja_feito(receita, porta, usuario, banco, versao, tem_systemd)
    descricao = {
        1: " ".join(_comandos_instalar(receita)[-1]),
        2: t("c.confere_e_faz"),
        3: t("c.p.ligar_boot") if tem_systemd else t("c.p.ligar_sem_systemd"),
        4: t("c.p.senha_o_que"),
        5: t("c.p.usuario_o_que", usuario=usuario),
        6: t("c.p.banco_o_que", banco=banco, usuario=usuario) if banco else t("c.p.banco_pula"),
    }
    print()
    print(t("c.plano", nome=receita.nome, gerenciador=receita.gerenciador))
    for n, rotulo in enumerate(_ROTULOS, 1):
        if n == 6 and not banco:
            _passo(n, t(rotulo), "pula", descricao[n])
        else:
            _passo(n, t(rotulo), "feito" if feito[n] else "vai", t("c.pronto") if feito[n] else descricao[n])
    print()
    if all(feito.values()):
        config.gravar(db={"usuario": usuario, "porta": porta, "banco": banco or None})
        print(c(t("c.nada_a_fazer"), "verde"))
        return 0
    if not _sim(t("c.continuar"), True, sempre):
        return 1
    if not garantir_sudo():
        print(c(t("c.sudo_falhou"), "laranja"))
        return 1
    if not sempre:
        print()

    try:
        # 1 instalar
        if versao:
            _passo(1, t("c.p.instalar"), "feito", t("c.ja", o=f"PostgreSQL {versao}"))
        else:
            for cmd in _comandos_instalar(receita):
                _executar(cmd)
            versao = sistema.versao_instalada(receita)
            if not versao:
                raise Falha(t("c.p.instalar"), t("c.nao_achei_instalado"))
            _passo(1, t("c.p.instalar"), "ok", f"PostgreSQL {versao}")

        # 2 inicializar
        ativo, no_boot = sistema.servico(receita)
        if sistema.inicializado(receita, ativo):
            _passo(2, t("c.p.inicializar"), "feito", t("c.ja", o=t("c.inicializado")))
        else:
            for cmd in _comandos_inicializar(receita, versao):
                _executar(cmd)
            _passo(2, t("c.p.inicializar"), "ok", t("c.inicializado"))

        # 3 ligar
        ativo, no_boot = sistema.servico(receita)
        pronto = (ativo and no_boot) if tem_systemd else sonda.porta_aberta(porta=porta)
        if pronto:
            _passo(3, t("c.p.ligar"), "feito", t("c.ja", o=t("c.rodando")))
        else:
            for cmd in _comandos_ligar(receita, tem_systemd):
                _executar(cmd)
            if not _esperar_porta(porta):
                raise Falha(t("c.p.ligar"), t("c.porta_nao_abriu", porta=porta))
            _passo(3, t("c.p.ligar"), "ok", t("c.rodando_boot") if tem_systemd else t("c.rodando_sem_boot"))

        # 4 login por senha
        hba = _psql("SHOW hba_file")
        texto = _executar(_root(["cat", hba]), mostrar=False)
        novo, mudancas = ajustar_hba(texto)
        if not mudancas:
            _passo(4, t("c.p.senha"), "feito", t("c.ja", o=t("c.hba_ok")))
        else:
            copia = f"{hba}.ratt-{datetime.datetime.now():%Y%m%d-%H%M%S}.bak"
            _executar(_root(["cp", "-p", hba, copia]))
            _executar(_root(["tee", hba]), entrada=novo)
            _psql("SELECT pg_reload_conf()")
            _passo(4, t("c.p.senha"), "ok", t("c.hba_trocado", arquivo=hba))
            for linha, antes, depois in mudancas:
                print(c(f"      {linha}   {antes} → {depois}", "cinza"))
            print(c(f"      {t('c.copia', arquivo=copia)}", "cinza"))

        # 5 o usuário
        if _psql(f"SELECT 1 FROM pg_roles WHERE rolname = {literal(usuario)}") == "1":
            _passo(5, t("c.p.usuario"), "feito", t("c.ja", o=usuario))
        else:
            senha = _ler_senha(usuario)
            poderes = "SUPERUSER" if op["superuser"] else "CREATEDB"
            _psql(f"CREATE ROLE {identificador(usuario)} LOGIN {poderes} PASSWORD {literal(senha)}")
            _passo(5, t("c.p.usuario"), "ok", t("c.usuario_criado", usuario=usuario, poderes=poderes))
            if sempre or _sim(f"  {t('c.guardar_senha')}", True):
                arquivo = gravar_pgpass(porta, usuario, senha)
                print(c(f"      {t('c.senha_guardada', arquivo=_casa(arquivo))}", "cinza"))

        # 6 o banco
        if not banco:
            _passo(6, t("c.p.banco"), "pula", t("c.p.banco_pula"))
        elif _psql(f"SELECT 1 FROM pg_database WHERE datname = {literal(banco)}") == "1":
            _passo(6, t("c.p.banco"), "feito", t("c.ja", o=banco))
        else:
            _psql(f"CREATE DATABASE {identificador(banco)} OWNER {identificador(usuario)}")
            _passo(6, t("c.p.banco"), "ok", t("c.p.banco_o_que", banco=banco, usuario=usuario))
    except Falha as e:
        print(c(f"  ✗ {t('c.falhou', cmd=e.cmd)}", "laranja", "negrito"))
        for linha in e.saida.splitlines()[-15:]:
            print(c(f"      {linha}", "cinza"))
        print(c(f"  {t('c.de_novo')}", "cinza"))
        return 1

    config.gravar(db={"usuario": usuario, "porta": porta, "banco": banco or None})
    print()
    print(c(t("s.tudo_certo"), "verde"))
    alvo = banco or "postgres"
    print(f"  {t('c.conectar')}  {c(f'psql -h localhost -U {usuario} {alvo}', 'azul')}")
    print(f"  {t('c.conferir')}  {c('ratt db status', 'azul')}")
    return 0
