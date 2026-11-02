"""O que os testes com PostgreSQL dividem: o banco descartável e as tabelas de exemplo."""
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
                admin.execute('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % nome)
            except psycopg.Error:
                pass


@pytest.fixture
def banco():
    """Um banco descartável ratt_teste_<pid>; pula sem PostgreSQL ou sem permissão de criar banco."""
    import psycopg
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
        erro = None
        for tentativa in range(2):
            try:
                admin.execute('CREATE DATABASE "%s"' % nome)
                criado = True
                break
            except psycopg.Error as e:
                erro = e
                time.sleep(0.5)
        if not criado:
            pytest.skip("não deu para criar o banco de teste: %s: %s" % (type(erro).__name__, str(erro).strip()))
        conexao, _ = acesso.entrar(porta, usuario, nome)
        if conexao is None:
            pytest.skip("não entrou no banco de teste")
        yield conexao
    finally:
        if conexao is not None:
            conexao.close()
        try:
            if criado:
                admin.execute('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % nome)
        finally:
            admin.close()
