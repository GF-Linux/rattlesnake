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
    sistema = [p for p in m.papeis.values() if p.nome.startswith("pg_")]
    assert sistema and all(p.sistema and not p.login for p in sistema)     # ficam no modelo (contam no acesso)
    assert not any(p.sistema for p in m.papeis.values() if not p.nome.startswith("pg_"))
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
        P = catalogo_servidor.Privilegios
        assert privilegios["ana"] == P(0, 1, 0)             # deu SELECT a leitura como dona (o de si para si não conta)
        assert privilegios["leitura"] == P(2, 0, 0)         # SELECT em coisas e USAGE em dele
        assert privilegios["Bruno Silva"] == P(0, 1, 0) and privilegios[nomes[-1]] == P(0, 0, 0)
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


def test_deu_como_dono_e_deu_por_grant_option(servidor, banco_servidor):
    """x é dona de t e dá SELECT com GRANT OPTION a y, que repassa a z. O que x deu como dona o REASSIGN OWNED
    leva ao novo dono; o que y deu pela opção o DROP OWNED BY y tira (é o que some ao remover y)."""
    P = catalogo_servidor.Privilegios
    with servidor.conectar(banco_servidor) as c:
        for comando in ("CREATE ROLE x", "CREATE ROLE y", "CREATE ROLE z", "CREATE TABLE t (i integer)",
                        "ALTER TABLE t OWNER TO x", "SET ROLE x", "GRANT SELECT ON t TO y WITH GRANT OPTION",
                        "RESET ROLE", "SET ROLE y", "GRANT SELECT ON t TO z", "RESET ROLE"):
            c.execute(comando)
        assert catalogo_servidor.privilegios_no_banco(c, ["x", "y", "z"]) == {
            "x": P(0, 1, 0), "y": P(1, 0, 1), "z": P(1, 0, 0)}
        c.execute(sql.SQL("REASSIGN OWNED BY x TO {}").format(sql.Identifier(servidor.usuario)))
        assert catalogo_servidor.privilegios_no_banco(c, ["x", "y", servidor.usuario]) == {
            "x": P(0, 0, 0), "y": P(1, 0, 1), servidor.usuario: P(0, 1, 0)}      # o que x deu passou ao novo dono
        c.execute("DROP OWNED BY y")
        assert catalogo_servidor.privilegios_no_banco(c, ["y", "z", servidor.usuario]) == {
            "y": P(0, 0, 0), "z": P(0, 0, 0), servidor.usuario: P(0, 0, 0)}      # o que y deu sumiu junto


def test_acesso_e_o_que_o_postgresql_diz(servidor, banco_servidor):
    """Para cada papel × banco, `acesso` diz que entra exatamente quando `has_database_privilege(…, 'CONNECT')`
    diz (superusuário: sempre) — herança por pertença (NOINHERIT, INHERIT FALSE, corrente com o meio NOINHERIT),
    dono que tirou de si o CONNECT, grupo dono, PUBLIC e um papel `pg_*` com CONNECT."""
    from ratt.db import servidor as srv
    b1 = banco_servidor
    with servidor.conectar() as c:
        versao = int(c.execute("SHOW server_version_num").fetchone()[0])
        comandos = [
            "CREATE ROLE g_conn NOLOGIN", "CREATE ROLE u_noinh LOGIN NOINHERIT", "GRANT g_conn TO u_noinh",
            "CREATE ROLE u_inh LOGIN", "GRANT g_conn TO u_inh",
            "CREATE ROLE meio NOLOGIN NOINHERIT", "GRANT g_conn TO meio", "CREATE ROLE u_corrente LOGIN",
            "GRANT meio TO u_corrente",
            "CREATE ROLE g_dono NOLOGIN", "CREATE ROLE u_noinh_dono LOGIN NOINHERIT", "GRANT g_dono TO u_noinh_dono",
            "CREATE ROLE u_inh_dono LOGIN", "GRANT g_dono TO u_inh_dono",
            "CREATE ROLE dono_sem LOGIN", "CREATE ROLE u_pg LOGIN", "GRANT pg_monitor TO u_pg",
            "CREATE ROLE ninguem LOGIN",
            sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(b1)),
            sql.SQL("GRANT CONNECT ON DATABASE {} TO g_conn").format(sql.Identifier(b1)),
            "CREATE DATABASE ratt_do_grupo OWNER g_dono", "REVOKE CONNECT ON DATABASE ratt_do_grupo FROM PUBLIC",
            "CREATE DATABASE ratt_sem_connect OWNER dono_sem",
            "REVOKE CONNECT ON DATABASE ratt_sem_connect FROM PUBLIC",
            "REVOKE CONNECT ON DATABASE ratt_sem_connect FROM dono_sem",
            "CREATE DATABASE ratt_do_pg", "REVOKE CONNECT ON DATABASE ratt_do_pg FROM PUBLIC",
            "GRANT CONNECT ON DATABASE ratt_do_pg TO pg_monitor",
            "CREATE DATABASE ratt_aberto",
        ]
        if versao >= 160000:
            comandos += ["CREATE ROLE u_sem_herdar LOGIN", "GRANT g_conn TO u_sem_herdar WITH INHERIT FALSE, SET TRUE",
                         "CREATE ROLE u_sem_set LOGIN", "GRANT g_conn TO u_sem_set WITH INHERIT TRUE, SET FALSE"]
        for comando in comandos:
            c.execute(comando)
        m = catalogo_servidor.modelo_servidor(c)
        verdade = {(f"r{r}", f"d{d}"): pode for r, d, pode in c.execute(
            "SELECT r.oid, d.oid, has_database_privilege(r.oid, d.oid, 'CONNECT') FROM pg_roles r, pg_database d")}
    assert set(verdade) == {(p, b) for p in m.papeis for b in m.bancos}
    errados = [(m.papeis[p].nome, m.bancos[b].nome, srv.acesso(m, p, b), pode)
               for (p, b), pode in verdade.items() if (srv.acesso(m, p, b) != "nenhum") != pode]
    assert errados == []
    a = lambda papel, banco: srv.acesso(m, m.papel_por_nome(papel).id, m.banco_por_nome(banco).id)
    assert a("u_noinh", b1) == "nenhum" and a("u_inh", b1) == "herdado" and a("u_corrente", b1) == "nenhum"
    assert a("u_noinh_dono", "ratt_do_grupo") == "nenhum" and a("u_inh_dono", "ratt_do_grupo") == "herdado"
    assert a("g_dono", "ratt_do_grupo") == "dono" and a("dono_sem", "ratt_sem_connect") == "nenhum"
    assert a("u_pg", "ratt_do_pg") == "herdado" and a("ninguem", "ratt_aberto") == "herdado"
    assert a("ninguem", b1) == "nenhum" and a(servidor.usuario, "ratt_sem_connect") == "super"
    assert m.papel_por_nome("u_noinh").herda is False and m.papel_por_nome("u_noinh").nao_herda == (
        m.papel_por_nome("g_conn").id,)
    assert m.papel_por_nome("meio").nao_herda == (m.papel_por_nome("g_conn").id,)
    if versao >= 160000:
        g = m.papel_por_nome("g_conn").id
        assert a("u_sem_herdar", b1) == "nenhum" and a("u_sem_set", b1) == "herdado"
        assert m.papel_por_nome("u_sem_herdar").nao_herda == (g,) and m.papel_por_nome("u_sem_herdar").sem_set == ()
        assert m.papel_por_nome("u_sem_set").nao_herda == () and m.papel_por_nome("u_sem_set").sem_set == (g,)


_BANCOS_QUE_JA_EXISTIAM = ("SELECT datname, datacl::text, datallowconn FROM pg_database "
                           "WHERE datname IN ('postgres', 'template1') ORDER BY datname")
_ANTES = []


def test_banco_servidor_restaura_bancos_que_ja_existiam(servidor, banco_servidor):
    """Mexer no `datacl` ou no `datallowconn` de um banco que já existia (postgres, template1) é desfeito no fim."""
    with servidor.conectar() as c:
        _ANTES[:] = c.execute(_BANCOS_QUE_JA_EXISTIAM).fetchall()
        c.execute("REVOKE CONNECT ON DATABASE postgres FROM PUBLIC")
        c.execute("CREATE ROLE ana LOGIN")
        c.execute("GRANT CONNECT ON DATABASE template1 TO ana")
        c.execute("ALTER DATABASE template1 ALLOW_CONNECTIONS false")


def test_banco_servidor_limpa_o_que_o_teste_criou(servidor, banco_servidor):
    """Os papéis e bancos que um teste cria somem no fim dele (o cluster é da sessão inteira): o teste seguinte
    não vê a ana nem o ratt_b2 dos de cima."""
    with servidor.conectar() as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('ana', 'leitura', 'Bruno Silva')").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM pg_database WHERE datname = 'ratt_b2'").fetchone()[0] == 0
        if _ANTES:                                          # o que o teste de cima mudou voltou ao que era
            assert c.execute(_BANCOS_QUE_JA_EXISTIAM).fetchall() == _ANTES
