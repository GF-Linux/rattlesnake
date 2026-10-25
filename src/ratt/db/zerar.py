"""ratt db reset: apagar todos os bancos e usuários e deixar o PostgreSQL como antes do setup.

O pacote continua instalado; some a pasta de dados (no Debian/Ubuntu, o cluster inteiro, com a
configuração dele). Depois, o ratt db setup monta tudo de novo.

Numa máquina de estudo, perder tudo não importa. Numa VM do trabalho, um reset no terminal errado
é um desastre — por isso ele mostra o que vai sumir, oferece um backup (pg_dumpall) e só segue
depois que você digitar o nome da máquina.
"""
import datetime
import os
import shlex
import socket
import subprocess
import sys
from pathlib import Path

from ratt.cli import c, _casa
from ratt.db import acesso, config, configurar, sistema, sonda
from ratt.db.configurar import Falha, _executar, _postgres, _psql, _root
from ratt.db.idioma import t


def inventario():
    """[(banco, dono, tamanho, tabelas)] e [usuários], vistos pelo superusuário postgres."""
    bancos = []
    linhas = _psql("SELECT datname, pg_get_userbyid(datdba), pg_database_size(oid) FROM pg_database "
                   "WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY datname")
    for linha in filter(None, linhas.splitlines()):
        nome, dono, tamanho = linha.split("|")
        tabelas = _psql("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema')",
                        banco=nome)
        bancos.append((nome, dono, int(tamanho), int(tabelas)))
    usuarios = _psql("SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_' AND rolname <> 'postgres' ORDER BY 1")
    return bancos, [u for u in usuarios.splitlines() if u]


def fazer_backup(destino):
    """pg_dumpall do servidor inteiro (bancos e usuários) num arquivo seu. Restaura com  psql -f ."""
    cmd = _postgres(["pg_dumpall"])
    print(c(f"      $ {shlex.join(cmd)} > {_casa(destino)}", "cinza"))
    sys.stdout.flush()
    with open(destino, "w", encoding="utf-8") as saida:
        os.chmod(destino, 0o600)        # o dump tem os hashes das senhas
        p = subprocess.run(cmd, stdout=saida, stderr=subprocess.PIPE, text=True, cwd="/")
    if p.returncode != 0:
        raise Falha(shlex.join(cmd), p.stderr.strip())
    return Path(destino).stat().st_size


def _comandos_zerar(receita, tem_systemd, rodando):
    if receita.familia == "debian":
        return [_root(["pg_dropcluster", "--stop", cl.versao, cl.nome]) for cl in sistema.clusters_debian()]
    cmds = []
    if tem_systemd:
        cmds.append(_root(["systemctl", "stop", receita.unidade]))
    elif rodando:
        cmds.append(_postgres(["pg_ctl", "-D", receita.pasta_dados, "-m", "fast", "-w", "stop"]))
    # a pasta vem da receita, nunca do que o servidor responde: é ela e só ela que o rm apaga
    return cmds + [_root(["rm", "-rf", receita.pasta_dados])]


def esquecer_pgpass(porta):
    """Tira do ~/.pgpass as senhas de localhost:porta — os usuários delas acabaram de sumir."""
    arquivo = Path.home() / ".pgpass"
    if not arquivo.exists():
        return 0
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    ficam = [l for l in linhas if not l.startswith(f"localhost:{porta}:")]
    if len(ficam) != len(linhas):
        arquivo.write_text("".join(l + "\n" for l in ficam), encoding="utf-8")
    return len(linhas) - len(ficam)


def _confirmar(nome_maquina, confirmado):
    """O nome da máquina digitado (ou passado em --confirm). Aceita o nome curto ou o completo."""
    aceitos = {nome_maquina, nome_maquina.split(".")[0]}
    if confirmado is not None:
        return confirmado in aceitos
    if not sys.stdin.isatty():
        return False
    try:
        digitado = input(f"  {t('z.digite', maquina=nome_maquina.split('.')[0])} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return digitado in aceitos


def cmd_reset(args, op):
    os_release = sistema.ler_os_release()
    receita = sistema.RECEITAS.get(sistema.familia(os_release))
    porta = int(os.environ.get("PGPORT") or (config.ler().get("db") or {}).get("porta") or 5432)
    maquina = socket.gethostname()
    sempre = op["yes"]

    print(f"{c('ratt db reset', 'azul', 'negrito')} {c('· ' + (os_release.get('PRETTY_NAME') or 'Linux'), 'cinza')}")
    print()
    if not receita:
        print(c(f"  {t('s.manual')}", "laranja"))
        return 1
    versao = sistema.versao_instalada(receita)
    if not versao:
        print(c(f"  {t('z.nada')}", "cinza"))
        return 0
    if sempre and op["confirm"] is None:
        print(c(f"  {t('z.y_sem_confirm', maquina=maquina.split('.')[0])}", "laranja"))
        return 1
    if not sempre and not sys.stdin.isatty():
        print(c(f"  {t('z.sem_terminal')}", "laranja"))
        return 1
    pode, _ = sistema.pode_usar_sudo(receita)
    if not pode or not configurar.garantir_sudo():
        print(c(f"  {t('c.sem_sudo', grupos=' / '.join(receita.grupos_sudo))}", "laranja"))
        return 1

    ativo, _ = sistema.servico(receita)
    tem_systemd = ativo is not None
    rodando = sonda.porta_aberta(porta=porta)
    if not rodando and not sistema.inicializado(receita, ativo):
        print(c(f"  {t('z.nada')}", "cinza"))
        return 0

    # o que vai sumir
    try:
        if rodando:
            bancos, usuarios = inventario()
            print(c(f"  ⚠ {t('z.aviso')}", "laranja", "negrito"))
            if not bancos:
                print(c(f"     {t('z.sem_bancos')}", "cinza"))
            w = max([len(b[0]) for b in bancos] + [1])
            for nome, dono, tamanho, tabelas in bancos:
                print(f"     {c(nome.ljust(w), 'negrito')}  {t('z.tabela' if tabelas == 1 else 'z.tabelas', n=tabelas):>12}  "
                      f"{acesso.tamanho(tamanho):>8}  {c(t('s.banco', dono=dono), 'cinza')}")
            if usuarios:
                print(f"     {c(t('z.usuarios', nomes=', '.join(usuarios)), 'cinza')}")
        else:
            print(c(f"  ⚠ {t('z.aviso')}", "laranja", "negrito"))
            print(c(f"     {t('z.parado')}", "amarelo"))
        print()

        # as duas perguntas antes de qualquer coisa: cancelar não deixa nem um backup para trás
        backup = rodando and not op["no_backup"] and configurar._sim(f"  {t('z.backup')}", True, sempre)
        if not _confirmar(maquina, op["confirm"]):
            print(c(f"  {t('z.cancelado')}", "amarelo"))
            return 1
        if not sempre:
            print()

        if backup:
            agora = datetime.datetime.now()
            destino = Path.home() / f"ratt-backup-{maquina.split('.')[0]}-{agora:%Y%m%d-%H%M%S}.sql"
            tamanho = fazer_backup(destino)
            print(f"  {c('✓', 'verde')} {t('z.backup_feito', arquivo=_casa(destino), tamanho=acesso.tamanho(tamanho))}")
            print(c(f"      {t('z.restaurar', arquivo=_casa(destino))}", "cinza"))
        for cmd in _comandos_zerar(receita, tem_systemd, rodando):
            _executar(cmd)
    except Falha as e:
        print(c(f"  ✗ {t('c.falhou', cmd=e.cmd)}", "laranja", "negrito"))
        for linha in e.saida.splitlines()[-15:]:
            print(c(f"      {linha}", "cinza"))
        return 1

    print(f"  {c('✓', 'verde')} {t('z.zerado')}")
    removidas = esquecer_pgpass(porta)
    if removidas:
        print(c(f"  · {t('z.pgpass', n=removidas, porta=porta)}", "cinza"))
    config.gravar(db=None)
    print()
    if not sempre and configurar._sim(t("z.setup_agora"), True):
        print()
        opcoes_setup = dict(op, user=None, db=None, superuser=False, yes=False)
        return configurar.cmd_setup([], opcoes_setup)
    print(f"{t('s.proximo')} {c('ratt db setup', 'azul', 'negrito')}")
    return 0
