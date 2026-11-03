"""O que os testes com PostgreSQL dividem: o banco descartável e as tabelas de exemplo."""
import asyncio
import getpass
import os
import re
import sys
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
