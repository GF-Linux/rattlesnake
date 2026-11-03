"""O que os testes com PostgreSQL dividem: o banco descartável, o servidor descartável (para os papéis) e as
tabelas de exemplo."""
import asyncio
import getpass
import itertools
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from ratt.db import acesso

PNG_SQL = """
CREATE TABLE users (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, first_name text, last_name text,
                    email text NOT NULL, password_hash text);
CREATE TABLE roles (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name text NOT NULL);
CREATE TABLE users_roles (user_id bigint NOT NULL REFERENCES users (id), role_id bigint NOT NULL REFERENCES roles (id),
                          PRIMARY KEY (user_id, role_id));
CREATE TABLE profiles (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, bio text, description text,
                       user_id bigint REFERENCES users (id));
"""

PESSOAS_SQL = """
CREATE TABLE public.pessoas (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, nome text NOT NULL,
                             idade integer, email text UNIQUE, nascimento date, ativo boolean DEFAULT true);
CREATE TABLE public.pedidos (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                             pessoa_id bigint REFERENCES public.pessoas (id) ON DELETE CASCADE, total numeric);
INSERT INTO public.pessoas (nome, idade, email, nascimento) VALUES
  ('Gabriel', 34, 'gab@x.com', '1990-07-14'), ('Ana', 29, 'ana@x.com', '1996-03-02'),
  ('Giovana', NULL, NULL, NULL), (' Gustavo', 40, 'gus@x.com', '1985-11-20'), ('O''Brien', 51, 'ob@x.com', '1974-06-01');
INSERT INTO public.pedidos (pessoa_id, total) VALUES (1, 10), (1, 20), (2, 5);
"""


def _processo_vivo(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError):
        return True         # existe, é de outro usuário
    return True


def apagar_banco(admin, nome, limite=5.0, espera=time.sleep, relogio=time.monotonic):
    """`DROP DATABASE … WITH (FORCE)` com paciência: o PostgreSQL às vezes recusa terminar um processo que não é
    nosso (um autovacuum do superusuário: "permissão negada para terminar o processo") ou o banco ainda está em uso;
    só esses dois casos esperam.
    Tenta de novo com espera crescente por até `limite` segundos; devolve False se não conseguiu (as sobras são
    apagadas no começo da próxima rodada). Outro erro sobe na hora."""
    import psycopg
    fim = relogio() + limite
    pausa = 0.1
    while True:
        try:
            admin.execute('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % nome)
            return True
        except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ObjectInUse) as erro:
            # "permissão negada para terminar o processo" / "permission denied to terminate process": espera passar;
            # outra falta de permissão (não poder apagar o banco) não passa com o tempo
            if isinstance(erro, psycopg.errors.InsufficientPrivilege) and "termin" not in str(erro).lower():
                raise
            if relogio() + pausa > fim:
                return False
            espera(pausa)
            pausa = min(pausa * 2, 1.0)


def _limpar_sobras(admin):
    """Apaga os ratt_teste_<pid> de rodadas que morreram sem limpar (só o nome exato, só pid que não roda mais)."""
    import psycopg
    try:
        nomes = [r[0] for r in admin.execute("SELECT datname FROM pg_database WHERE datname ~ '^ratt_teste_[0-9]+$'")]
    except psycopg.Error:
        return
    for nome in nomes:
        if re.fullmatch(r"ratt_teste_\d+", nome) and not _processo_vivo(int(nome.rsplit("_", 1)[1])):
            try:
                apagar_banco(admin, nome, limite=1.0)
            except psycopg.Error:
                pass


@pytest.fixture
def banco():
    """Um banco descartável ratt_teste_<pid>; pula sem PostgreSQL acessível. Com o PostgreSQL no ar, não conseguir
    criar o banco é falha (com o erro de verdade), não um teste pulado."""
    porta = int(os.environ.get("PGPORT") or 5432)
    usuario = getpass.getuser()
    admin, _ = acesso.entrar(porta, usuario)
    if admin is None:
        pytest.skip("sem PostgreSQL acessível")
    nome = "ratt_teste_%d" % os.getpid()
    conexao = None
    criado = False
    try:
        _limpar_sobras(admin)
        apagar_banco(admin, nome)       # o nosso nome, deixado por um teste anterior desta rodada que não conseguiu apagar
        criar_banco(admin, nome)
        criado = True
        conexao, _ = acesso.entrar(porta, usuario, nome)
        if conexao is None:
            pytest.skip("não entrou no banco de teste")
        yield conexao
    finally:
        if conexao is not None:
            conexao.close()
        try:
            if criado:
                apagar_banco(admin, nome)
        finally:
            admin.close()


def criar_banco(admin, nome, tentativas=2, espera=time.sleep):
    """CREATE DATABASE com uma segunda tentativa (um DROP da rodada anterior pode estar terminando); se as duas
    falham, o teste FALHA com o erro de verdade — não vira um "pulado" que esconde o problema."""
    import psycopg
    erro = None
    for _ in range(tentativas):
        try:
            admin.execute('CREATE DATABASE "%s"' % nome)
            return
        except psycopg.Error as e:
            erro = e
            espera(0.5)
    pytest.fail("não deu para criar o banco de teste: %s: %s" % (type(erro).__name__, str(erro).strip()))


# ── o servidor descartável (fase 3: papéis são do servidor inteiro) ──
#
# Testes que criam, mudam ou removem papéis NUNCA rodam no PostgreSQL da pessoa (lá o usuário dela nem é
# superusuário, e os papéis de verdade não são de teste): rodam num cluster novo do `initdb`, numa pasta temporária,
# em que o usuário dos testes é o superusuário, escutando só num socket dentro da pasta.

class ServidorDescartavel:
    """O cluster da fixture `servidor`: `porta`, `socket_dir` (a pasta do socket, que é o host), `usuario`
    (o superusuário do cluster) e `conectar(banco)`."""

    def __init__(self, pasta, porta, usuario):
        self.pasta, self.socket_dir, self.porta, self.usuario = pasta, pasta, porta, usuario
        self.dados = os.path.join(pasta, "dados")

    def conectar(self, banco="postgres"):
        """Uma conexão nova (autocommit) a `banco` neste cluster, pelo socket da pasta — nunca pela rede nem pelo
        socket do PostgreSQL da pessoa."""
        import psycopg
        return psycopg.connect(host=self.socket_dir, port=self.porta, user=self.usuario, dbname=banco,
                               autocommit=True, connect_timeout=5)


def _porta_livre():
    """Uma porta que ninguém usa agora (só vira o número do socket: o cluster não escuta na rede)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _binario_pg(nome):
    achado = shutil.which(nome)
    if achado:
        return achado
    try:
        pasta = subprocess.run(["pg_config", "--bindir"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    caminho = os.path.join(pasta, nome) if pasta else ""
    return caminho if caminho and os.access(caminho, os.X_OK) else None


def _pasta_curta():
    """A pasta do cluster, que também guarda o socket: o caminho do socket tem limite (107 bytes no Linux), e um
    TMPDIR comprido o estouraria."""
    pasta = tempfile.mkdtemp(prefix="ratt-pg-")
    if len(pasta) > 80:
        shutil.rmtree(pasta, ignore_errors=True)
        pasta = tempfile.mkdtemp(prefix="ratt-pg-", dir="/tmp")
    return os.path.realpath(pasta)


@pytest.fixture(scope="session")
def servidor():
    """Um cluster PostgreSQL descartável para a rodada inteira: `initdb` numa pasta temporária (o usuário dos testes
    é o superusuário, `trust` só no socket da pasta), `pg_ctl start` numa porta livre sem escutar na rede; no fim,
    `pg_ctl stop -m immediate` e a pasta apagada — mesmo se algo falhar no meio. Sem `initdb`/`pg_ctl` instalados,
    pula com o motivo; com eles, não conseguir subir é falha (com o log), não um teste pulado."""
    initdb, pg_ctl = _binario_pg("initdb"), _binario_pg("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip("sem initdb/pg_ctl instalados: não dá para subir o servidor descartável dos testes de usuários")
    usuario = getpass.getuser()
    pasta = _pasta_curta()
    srv = None
    try:
        dados, log = os.path.join(pasta, "dados"), os.path.join(pasta, "log")
        r = subprocess.run([initdb, "-D", dados, "-U", usuario, "--auth=trust", "-E", "UTF8", "--locale=C",
                            "--no-instructions"], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            pytest.fail("initdb falhou: %s" % (r.stderr or r.stdout).strip())
        erro = ""
        for _ in range(3):                      # a porta livre pode ser tomada entre o bind e o start
            porta = _porta_livre()
            opcoes = "-p %d -k %s -c listen_addresses='' -c fsync=off" % (porta, shlex.quote(pasta))
            srv = ServidorDescartavel(pasta, porta, usuario)
            r = subprocess.run([pg_ctl, "-D", dados, "-l", log, "-o", opcoes, "start", "-w", "-t", "30"],
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                break
            erro = (r.stderr or r.stdout).strip()
            try:
                with open(log, encoding="utf-8", errors="replace") as f:
                    erro += "\n" + "".join(f.readlines()[-10:])
            except OSError:
                pass
        else:
            pytest.fail("o servidor descartável não subiu: %s" % erro)
        with srv.conectar() as c:              # é mesmo o nosso cluster, e somos superusuário nele
            diretorio, super_ = c.execute("SELECT current_setting('data_directory'), rolsuper FROM pg_roles "
                                          "WHERE rolname = current_user").fetchone()
        assert os.path.realpath(diretorio) == os.path.realpath(dados) and super_, "não é o servidor descartável"
        yield srv
    finally:
        if srv is not None:
            subprocess.run([pg_ctl, "-D", os.path.join(pasta, "dados"), "stop", "-m", "immediate", "-w"],
                           capture_output=True, text=True, timeout=60)
        shutil.rmtree(pasta, ignore_errors=True)


_bancos_servidor = itertools.count(1)


def _restaurar_servidor(servidor, papeis_antes, bancos_antes):
    """Volta o cluster ao que era antes do teste: os bancos que já existiam voltam a aceitar (ou não) conexão como
    antes; os bancos novos são apagados; em cada banco que sobrou, o que os papéis novos possuem passa ao
    superusuário e o que lhes foi dado sai; o `datacl` dos bancos que já existiam volta ao de antes; e os papéis
    novos são apagados. `bancos_antes`: {nome: (datacl em texto, datallowconn)}."""
    from psycopg import sql
    with servidor.conectar() as admin:
        agora = {n: (acl, aceita) for n, acl, aceita in admin.execute(
            "SELECT datname, datacl::text, datallowconn FROM pg_database").fetchall()}
        for nome, (_, aceita) in agora.items():
            if nome in bancos_antes and bancos_antes[nome][1] != aceita:
                admin.execute(sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS {}").format(
                    sql.Identifier(nome), sql.SQL("true" if bancos_antes[nome][1] else "false")))
            elif nome not in bancos_antes:
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nome)))
        novos = [n for (n,) in admin.execute("SELECT rolname FROM pg_roles").fetchall() if n not in papeis_antes]
        bancos = [n for (n,) in admin.execute("SELECT datname FROM pg_database WHERE datallowconn").fetchall()]
    if novos:
        lista = sql.SQL(", ").join(map(sql.Identifier, novos))
        with servidor.conectar() as admin:
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = ANY(%s)", (novos,))
        for banco in bancos:
            with servidor.conectar(banco) as c:
                c.execute(sql.SQL("REASSIGN OWNED BY {} TO {}").format(lista, sql.Identifier(servidor.usuario)))
                c.execute(sql.SQL("DROP OWNED BY {}").format(lista))
    with servidor.conectar() as admin:
        # o datacl de antes só cita papéis que já existiam (os novos já saíram pelo DROP OWNED): basta regravá-lo
        for nome, (acl, _) in bancos_antes.items():
            admin.execute("UPDATE pg_database SET datacl = %s::aclitem[] WHERE datname = %s "
                          "AND datacl::text IS DISTINCT FROM %s", (acl, nome, acl))
        if novos:
            admin.execute(sql.SQL("DROP ROLE {}").format(lista))


@pytest.fixture
def banco_servidor(servidor):
    """Um banco novo no servidor descartável (devolve o nome). No fim, o cluster volta ao que era: somem esse banco
    e todo papel ou banco que o teste criou, e os bancos que já existiam voltam ao `datacl`/`datallowconn` de antes."""
    from psycopg import sql
    nome = "ratt_srv_%d" % next(_bancos_servidor)
    with servidor.conectar() as admin:
        papeis_antes = {n for (n,) in admin.execute("SELECT rolname FROM pg_roles")}
        bancos_antes = {n: (acl, aceita) for n, acl, aceita in admin.execute(
            "SELECT datname, datacl::text, datallowconn FROM pg_database")}
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nome)))
    try:
        yield nome
    finally:
        _restaurar_servidor(servidor, papeis_antes, bancos_antes)


# ── esperar a tela sem sleep fixo ──

async def esperar_workers(app, limite=10.0):
    """Espera os workers no ar terminarem e o que eles mandaram para a tela ser processado. Um worker cancelado
    (trocado por um mais novo do mesmo grupo `exclusive`) não é erro: `app.workers.wait_for_complete()` levantaria
    `WorkerCancelled`. Um que falhou levanta, como antes."""
    from textual.pilot import Pilot
    from textual.worker import WorkerCancelled, WorkerState
    fim = time.monotonic() + limite
    while True:
        vivos = [w for w in list(app.workers) if w.state in (WorkerState.PENDING, WorkerState.RUNNING)]
        if not vivos:
            break
        assert time.monotonic() < fim, "os workers não terminaram: %r" % vivos
        for w in vivos:
            if w.state == WorkerState.PENDING:
                await asyncio.sleep(0.01)       # ainda não começou: wait() recusaria
                continue
            try:
                await w.wait()
            except WorkerCancelled:
                pass
    await Pilot(app).pause()


async def esperar_que(piloto, condicao, limite=5.0, mensagem="a condição não ficou verdadeira"):
    """Pergunta `condicao()` até ela valer (com prazo), dando a vez à app entre uma pergunta e outra."""
    fim = time.monotonic() + limite
    while not condicao():
        assert time.monotonic() < fim, mensagem
        await piloto.pause(0.01)


async def esperar_tela(piloto, tela, limite=5.0):
    """Antes de clicar num formulário recém-aberto: espera ele ser a tela de cima e estar montado (sob carga, o
    clique logo depois do push_screen pode cair na tela de baixo)."""
    app = piloto.app
    await esperar_que(piloto, lambda: app.screen is tela and tela.is_mounted, limite, "a tela não abriu")
    await piloto.pause()


def saida_presa():
    """`with saida_presa() as saida:` — o que o `ratt db run` imprime dentro de `run_test` (o Textual troca o
    stdout e a saída escaparia da captura do pytest, solta no meio dos pontinhos) fica em `saida.getvalue()`."""
    import contextlib
    import io

    class _Presa(contextlib.redirect_stdout):
        def __init__(self):
            self.buffer = io.StringIO()
            super().__init__(self.buffer)

        def __enter__(self):
            super().__enter__()
            return self.buffer
    return _Presa()
