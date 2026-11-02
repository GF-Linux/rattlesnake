"""Testes do canvas com PostgreSQL de verdade; pulam sozinhos sem servidor ou sem permissão de criar banco."""
import getpass
import os

import pytest

from ratt.db import acesso, catalogo, modelo, rascunho

PNG_SQL = """
CREATE TABLE users (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, first_name text, last_name text,
                    email text NOT NULL, password_hash text);
CREATE TABLE roles (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name text NOT NULL);
CREATE TABLE users_roles (user_id bigint NOT NULL REFERENCES users (id), role_id bigint NOT NULL REFERENCES roles (id),
                          PRIMARY KEY (user_id, role_id));
CREATE TABLE profiles (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, bio text, description text,
                       user_id bigint REFERENCES users (id));
"""


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
    try:
        admin.execute('CREATE DATABASE "%s"' % nome)
    except psycopg.Error:
        admin.close()
        pytest.skip("sem permissão de criar banco")
    conexao = None
    try:
        conexao, _ = acesso.entrar(porta, usuario, nome)
        if conexao is None:
            pytest.skip("não entrou no banco de teste")
        yield conexao
    finally:
        if conexao is not None:
            conexao.close()
        admin.execute('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % nome)
        admin.close()


def test_modelo_do_png(banco):
    banco.execute(PNG_SQL)
    m = catalogo.modelo(banco, "public")
    users = m.por_nome("users"); ur = m.por_nome("users_roles"); prof = m.por_nome("profiles")
    assert [c.nome for c in users.colunas] == ["id", "first_name", "last_name", "email", "password_hash"]
    assert users.colunas[0].pk and users.colunas[0].identidade and users.colunas[3].obrigatoria
    assert len(m.ligacoes) == 3
    assert modelo.juncao(m, ur.id)
    lig = next(l for l in m.ligacoes.values() if l.origem == prof.id)
    assert modelo.cardinalidade(m, lig) == "1:N" and modelo.opcional(m, lig)


def test_modelo_com_nomes_hostis(banco):
    banco.execute('CREATE TABLE "user" ("Nome Completo" text, "order" int UNIQUE)')
    t = catalogo.modelo(banco, "public").por_nome("user")
    assert [c.nome for c in t.colunas] == ["Nome Completo", "order"] and t.colunas[1].unica


def test_schema_vazio(banco):
    assert catalogo.modelo(banco, "public").tabelas == {}


def test_assinatura_ve_restricoes_e_not_null(banco):
    banco.execute(PNG_SQL)
    antes = catalogo.assinatura(banco)
    banco.execute("ALTER TABLE profiles ADD CONSTRAINT profiles_user_id_key UNIQUE (user_id)")
    meio = catalogo.assinatura(banco)
    banco.execute("ALTER TABLE profiles ALTER COLUMN bio SET NOT NULL")
    assert antes != meio != catalogo.assinatura(banco)


def test_sql_do_rascunho_roda_no_banco(banco):
    """O SQL gerado pelo rascunho é aceito pelo PostgreSQL de verdade, inclusive com nomes hostis."""
    banco.execute(PNG_SQL)
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    users = r.base.por_nome("users")
    r.acrescentar(rascunho.Renomear(users.id, None, "user"))
    r.acrescentar(rascunho.AdicionarColuna(users.id, modelo.Coluna(r.novo_id("c"), "Data Nasc", "date")))
    banco.execute(r.sql_texto())
    assert catalogo.modelo(banco, "public").por_nome("user").colunas[-1].nome == "Data Nasc"
