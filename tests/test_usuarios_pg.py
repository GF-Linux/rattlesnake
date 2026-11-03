"""Testes da fase 3 com PostgreSQL — SÓ no servidor descartável (fixture `servidor`: um cluster do `initdb` numa
pasta temporária, em que o usuário dos testes é superusuário). Nada aqui toca os papéis do PostgreSQL da pessoa."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from psycopg import sql

from ratt.db import catalogo, catalogo_servidor, servidor as _servidor


def _cenario(servidor, b1):
    """ana (entra) no grupo leitura; b1 sem CONNECT para PUBLIC, com CONNECT para leitura; b2 não aceita conexão.
    Em b1, ana é dona de uma tabela e de uma função e deu SELECT na tabela a leitura."""
    admin = servidor.conectar()
    admin.execute("CREATE ROLE ana LOGIN")
    admin.execute("CREATE ROLE leitura NOLOGIN")
    admin.execute("GRANT leitura TO ana")
    admin.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(b1)))
    admin.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO leitura").format(sql.Identifier(b1)))
    admin.execute("CREATE DATABASE ratt_b2 ALLOW_CONNECTIONS false")
    admin.close()
    c = servidor.conectar(b1)
    c.execute("CREATE TABLE public.coisas (id integer)")
    c.execute("ALTER TABLE public.coisas OWNER TO ana")
    c.execute("CREATE FUNCTION public.um() RETURNS integer LANGUAGE sql AS 'SELECT 1'")
    c.execute("ALTER FUNCTION public.um() OWNER TO ana")
    c.execute("SET ROLE ana")
    c.execute("GRANT SELECT ON public.coisas TO leitura")
    c.execute("RESET ROLE")
    return c


def test_o_servidor_descartavel_e_nosso(servidor):
    with servidor.conectar() as c:
        diretorio, super_, usuario = c.execute(
            "SELECT current_setting('data_directory'), rolsuper, current_user FROM pg_roles "
            "WHERE rolname = current_user").fetchone()
    assert os.path.realpath(diretorio).startswith(os.path.realpath(servidor.socket_dir))
    assert super_ is True and usuario == servidor.usuario


def test_modelo_servidor_papeis_grupos_e_acesso(servidor, banco_servidor, monkeypatch):
    b1 = banco_servidor
    _cenario(servidor, b1).close()
    consultas = []
    original = catalogo.executar
    monkeypatch.setattr(catalogo, "executar", lambda alvo, q, params=None: consultas.append(q) or original(alvo, q, params))
    with servidor.conectar() as c:
        m = catalogo_servidor.modelo_servidor(c)
    assert 1 <= len(consultas) <= 4
    ana, leitura, eu = m.papel_por_nome("ana"), m.papel_por_nome("leitura"), m.papel_por_nome(servidor.usuario)
    assert m.eu == eu.id and eu.superusuario and eu.login and m.versao >= 130000
    assert ana.id.startswith("r") and ana.login and not ana.superusuario and ana.membro_de == (leitura.id,)
    assert not leitura.login and leitura.membro_de == ()
    assert not any(p.nome.startswith("pg_") for p in m.papeis.values())
    banco1, banco2 = m.banco_por_nome(b1), m.banco_por_nome("ratt_b2")
    assert banco1.id.startswith("d") and banco1.dono == eu.id and banco1.aceita_conexao
    assert banco1.publico is False and leitura.id in banco1.acesso_direto and ana.id not in banco1.acesso_direto
    assert banco2.aceita_conexao is False and banco2.publico is True
    assert _servidor.acesso(m, ana.id, banco1.id) == "herdado"
    assert _servidor.acesso(m, leitura.id, banco1.id) == "direto"
    assert _servidor.acesso(m, eu.id, banco1.id) == "super"
    assert _servidor.acesso(m, ana.id, banco2.id) == "herdado"          # PUBLIC (a tela marca que não aceita conexão)
    assert m.banco_por_nome("template1").template and not banco1.template
    assert _servidor.superusuarios_com_login(m) == [eu.id]


def test_posse_privilegios_e_permissoes_padrao_no_banco(servidor, banco_servidor):
    b1 = banco_servidor
    c = _cenario(servidor, b1)
    try:
        c.execute('CREATE ROLE "Bruno Silva" LOGIN')
        c.execute("CREATE SCHEMA dele")
        c.execute('ALTER SCHEMA dele OWNER TO "Bruno Silva"')
        c.execute("CREATE VIEW dele.v AS SELECT 1 AS x")
        c.execute('ALTER VIEW dele.v OWNER TO "Bruno Silva"')
        c.execute("CREATE TYPE dele.cor AS ENUM ('azul')")
        c.execute('ALTER TYPE dele.cor OWNER TO "Bruno Silva"')
        c.execute("GRANT USAGE ON SCHEMA dele TO leitura")
        c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE ana GRANT SELECT ON TABLES TO leitura")
        nomes = ["ana", "leitura", "Bruno Silva", "bruno silva", "ninguém'; DROP ROLE ana; --"]
        posse = catalogo_servidor.posse_no_banco(c, nomes)
        assert posse["ana"] == {"tabela": 1, "funcao": 1}
        assert posse["Bruno Silva"] == {"schema": 1, "view": 1, "tipo": 1}
        assert posse["leitura"] == {} and posse["bruno silva"] == {} and posse[nomes[-1]] == {}
        privilegios = catalogo_servidor.privilegios_no_banco(c, nomes)
        assert privilegios["ana"] == (0, 1)                 # deu SELECT a leitura (o que é dela, de si, não conta)
        assert privilegios["leitura"] == (2, 0)             # SELECT em coisas e USAGE em dele
        assert privilegios["Bruno Silva"] == (0, 1) and privilegios[nomes[-1]] == (0, 0)
        padrao = catalogo_servidor.permissoes_padrao(c, nomes)
        assert padrao["ana"] == 1 and padrao["leitura"] == 1 and padrao["Bruno Silva"] == 0
        assert catalogo_servidor.password_encryption(c) == "scram-sha-256"
    finally:
        c.close()
    with servidor.conectar() as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'ana'").fetchone()[0] == 1


def test_sessoes_abertas_por_papel(servidor, banco_servidor):
    b1 = banco_servidor
    _cenario(servidor, b1).close()
    import psycopg
    da_ana = psycopg.connect(host=servidor.socket_dir, port=servidor.porta, user="ana", dbname=b1, autocommit=True)
    try:
        with servidor.conectar() as c:
            assert catalogo_servidor.sessoes(c, ["ana", "leitura", "ninguém"]) == {"ana": 1, "leitura": 0, "ninguém": 0}
            assert catalogo_servidor.sessoes(c, [servidor.usuario]) == {servidor.usuario: 0}   # a própria conexão não conta
            c.execute("CREATE ROLE carla LOGIN")
        # quem não é superusuário (como o usuário da pessoa) também vê as sessões e o servidor
        da_carla = psycopg.connect(host=servidor.socket_dir, port=servidor.porta, user="carla", dbname="postgres",
                                   autocommit=True)
        try:
            assert catalogo_servidor.sessoes(da_carla, ["ana", "ana", "carla"]) == {"ana": 1, "carla": 0}
            m = catalogo_servidor.modelo_servidor(da_carla)
            assert m.papeis[m.eu].nome == "carla" and not m.papeis[m.eu].superusuario
            assert m.papel_por_nome("ana").membro_de == (m.papel_por_nome("leitura").id,)
            assert m.banco_por_nome(b1).publico is False
        finally:
            da_carla.close()
    finally:
        da_ana.close()


def test_banco_servidor_limpa_o_que_o_teste_criou(servidor, banco_servidor):
    """Os papéis e bancos que um teste cria somem no fim dele (o cluster é da sessão inteira): o teste seguinte
    não vê a ana nem o ratt_b2 dos de cima."""
    with servidor.conectar() as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('ana', 'leitura', 'Bruno Silva')").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM pg_database WHERE datname = 'ratt_b2'").fetchone()[0] == 0
