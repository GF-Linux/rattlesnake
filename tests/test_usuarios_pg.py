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
        atual = c.info.dbname
    assert 1 <= len(consultas) <= 4
    assert m.bancos[m.banco_atual].nome == atual                        # o banco desta conexão (para as travas)
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
        assert privilegios["leitura"] == P(3, 0, 0)         # SELECT em coisas, USAGE em dele e CONNECT no banco
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


# ── o rascunho do servidor aplicado de verdade (grupos) ──

def _conectar_como(servidor, usuario, banco):
    import psycopg
    return psycopg.connect(host=servidor.socket_dir, port=servidor.porta, user=usuario, dbname=banco,
                           autocommit=True, connect_timeout=5)


def _aplicar(servidor, r, banco, senhas=None):
    """Roda `r.grupos(senhas)` como o aplicar fará: cada grupo numa transação, no banco de destino (None: `banco`)."""
    for destino, texto in r.grupos(senhas):
        with servidor.conectar(destino or banco) as c:
            with c.transaction():
                c.execute(texto)


def _por_nome(m):
    """O modelo por nomes (os ids de papéis novos não batem com os `r{oid}` relidos): papéis com poderes e grupos,
    bancos com dono, PUBLIC e quem tem CONNECT direto. Sem os `pg_*`."""
    papeis = {p.nome: (p.login, p.superusuario, p.cria_bancos, p.cria_papeis,
                       frozenset(m.papeis[g].nome for g in p.membro_de),
                       frozenset(m.papeis[g].nome for g in p.nao_herda))
              for p in m.papeis.values() if not p.sistema}
    bancos = {b.nome: (m.papeis[b.dono].nome, b.publico, frozenset(m.papeis[i].nome for i in b.acesso_direto))
              for b in m.bancos.values()}
    return papeis, bancos


def _confere_com_postgresql(servidor, banco, previsto):
    """Depois de aplicar: o acesso que o modelo previsto dá a cada papel em cada banco é o que o
    `has_database_privilege` diz, e o catálogo relido (por nomes) é o previsto."""
    with servidor.conectar(banco) as c:
        depois = catalogo_servidor.modelo_servidor(c)
        for papel in previsto.papeis.values():
            for b in previsto.bancos.values():
                pode = c.execute("SELECT has_database_privilege(%s, %s, 'CONNECT')", (papel.nome, b.nome)).fetchone()[0]
                assert (_servidor.acesso(previsto, papel.id, b.id) != "nenhum") == pode, (papel.nome, b.nome)
    assert _por_nome(previsto) == _por_nome(depois)


def _remover_e_conferir(servidor, banco, remover, quem_fica_sem):
    """Remove `remover` (nomes) pelo rascunho, como superusuário, em todos os bancos que aceitam conexão; antes,
    conectado como `quem_fica_sem`, confere que a trava recusa. Depois confere o previsto com o PostgreSQL."""
    import pytest
    from ratt.db.rascunho_servidor import RascunhoServidor, RemoverPapeis
    with _conectar_como(servidor, quem_fica_sem, banco) as c:
        m = catalogo_servidor.modelo_servidor(c)
    bancos = tuple(x.nome for x in m.bancos.values() if x.aceita_conexao)
    su = m.papel_por_nome(servidor.usuario).id
    with pytest.raises(ValueError):
        RascunhoServidor(m).acrescentar(RemoverPapeis(tuple(m.papel_por_nome(n).id for n in remover), su, bancos))
    with servidor.conectar(banco) as c:
        m = catalogo_servidor.modelo_servidor(c)
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(tuple(m.papel_por_nome(n).id for n in remover), m.eu, bancos))
    previsto = r.modelo()
    assert _servidor.acesso(previsto, previsto.papel_por_nome(quem_fica_sem).id, previsto.banco_atual) == "nenhum"
    _aplicar(servidor, r, banco)
    _confere_com_postgresql(servidor, banco, previsto)
    return m, previsto


def test_s3_quem_so_tinha_a_opcao_do_removido_perde_o_que_deu(servidor, banco_servidor):
    """x3 tem c*/y3 e c/dono e deu a v3. Remover y3: x3 continua entrando, sem a opção — o de v3 cai."""
    b = banco_servidor
    with servidor.conectar() as c:
        for cmd in ("CREATE ROLE y3 LOGIN", "CREATE ROLE x3 LOGIN", "CREATE ROLE v3 LOGIN",
                    f"REVOKE CONNECT ON DATABASE {b} FROM PUBLIC",
                    f"GRANT CONNECT ON DATABASE {b} TO y3 WITH GRANT OPTION", f"GRANT CONNECT ON DATABASE {b} TO x3"):
            c.execute(cmd)
        c.execute("SET ROLE y3")
        c.execute(f"GRANT CONNECT ON DATABASE {b} TO x3 WITH GRANT OPTION")
        c.execute("SET ROLE x3")
        c.execute(f"GRANT CONNECT ON DATABASE {b} TO v3")
        c.execute("RESET ROLE")
    m, previsto = _remover_e_conferir(servidor, b, ["y3"], "v3")
    x3, y3, su = (m.papel_por_nome(n).id for n in ("x3", "y3", servidor.usuario))
    assert m.banco_por_nome(b).concedido_por[x3] == {su: False, y3: True}
    assert _servidor.acesso(previsto, x3, previsto.banco_atual) == "direto"


def test_s4_pertenca_dada_pelo_removido_cai_sem_cascata_e_inherit_das_linhas(servidor, banco_servidor):
    """g4 → b4 (ADMIN) → a4 (ADMIN) → v4; w4 está em g4 por b4 (INHERIT) e pelo superusuário (INHERIT FALSE).
    Remover b4: a4 sai de g4 (só tinha a linha de b4); v4 fica (a linha é de a4: o PostgreSQL não leva adiante);
    w4 fica membro sem herdar — e perde o CONNECT que g4 tem."""
    import pytest
    b = banco_servidor
    with servidor.conectar() as c:
        if c.info.server_version < 160000:
            pytest.skip("pertenças por concedente só no PostgreSQL 16+")
        for cmd in ("CREATE ROLE g4 NOLOGIN", "CREATE ROLE b4 LOGIN", "CREATE ROLE a4 LOGIN", "CREATE ROLE v4 LOGIN",
                    "CREATE ROLE w4 LOGIN", f"REVOKE CONNECT ON DATABASE {b} FROM PUBLIC",
                    f"GRANT CONNECT ON DATABASE {b} TO g4", "GRANT g4 TO b4 WITH ADMIN OPTION",
                    "GRANT g4 TO w4 WITH INHERIT FALSE"):
            c.execute(cmd)
        c.execute("SET ROLE b4")
        c.execute("GRANT g4 TO a4 WITH ADMIN OPTION")
        c.execute("GRANT g4 TO w4")
        c.execute("SET ROLE a4")
        c.execute("GRANT g4 TO v4")
        c.execute("RESET ROLE")
    m, previsto = _remover_e_conferir(servidor, b, ["b4"], "w4")
    g4, b4, su = (m.papel_por_nome(n).id for n in ("g4", "b4", servidor.usuario))
    assert m.papel_por_nome("w4").concedido_por == {g4: {b4: (True, True), su: (False, True)}}
    assert previsto.papel_por_nome("a4").membro_de == ()
    assert previsto.papel_por_nome("v4").membro_de == (g4,)
    w4 = previsto.papel_por_nome("w4")
    assert w4.membro_de == (g4,) and w4.nao_herda == (g4,)
    assert _servidor.acesso(previsto, previsto.papel_por_nome("v4").id, previsto.banco_atual) == "herdado"


def test_remover_quem_deu_connect_e_pertenca_igual_ao_postgresql(servidor, banco_servidor):
    """adm tem CONNECT com opção de conceder e ADMIN em grp; deu CONNECT a eu2 e ao PUBLIC e grp a outro. Remover
    adm leva tudo isso junto (DROP OWNED em cascata) — o rascunho prevê, a trava vê e o PostgreSQL confirma."""
    b = banco_servidor
    with servidor.conectar() as c:
        for cmd in ("CREATE ROLE adm LOGIN", "CREATE ROLE eu2 LOGIN", "CREATE ROLE grp NOLOGIN",
                    "CREATE ROLE outro LOGIN", f"REVOKE CONNECT ON DATABASE {b} FROM PUBLIC",
                    f"GRANT CONNECT ON DATABASE {b} TO adm WITH GRANT OPTION", "GRANT grp TO adm WITH ADMIN OPTION"):
            c.execute(cmd)
        c.execute("SET ROLE adm")
        c.execute(f"GRANT CONNECT ON DATABASE {b} TO eu2")
        c.execute(f"GRANT CONNECT ON DATABASE {b} TO PUBLIC")
        c.execute("GRANT grp TO outro")
        c.execute("RESET ROLE")
    from ratt.db.rascunho_servidor import RascunhoServidor, RemoverPapeis
    with _conectar_como(servidor, "eu2", b) as c:              # eu2 entra só pelo que adm deu: a trava recusa
        m = catalogo_servidor.modelo_servidor(c)
    adm = m.papel_por_nome("adm")
    assert m.bancos[m.banco_atual].concedido_por[m.eu] == {adm.id: False}
    bancos = tuple(x.nome for x in m.bancos.values() if x.aceita_conexao)
    import pytest
    with pytest.raises(ValueError):
        RascunhoServidor(m).acrescentar(RemoverPapeis((adm.id,), m.papel_por_nome(servidor.usuario).id, bancos))
    with servidor.conectar(b) as c:
        m = catalogo_servidor.modelo_servidor(c)
    if m.versao >= 160000:
        assert m.papel_por_nome("outro").concedido_por == {m.papel_por_nome("grp").id: {adm.id: (True, True)}}
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis((adm.id,), m.eu, bancos))
    previsto = r.modelo()
    _aplicar(servidor, r, b)
    _confere_com_postgresql(servidor, b, previsto)
    assert _servidor.acesso(previsto, previsto.papel_por_nome("eu2").id, previsto.banco_atual) == "nenhum"


def test_grupos_de_cada_operacao_aplicados_no_servidor(servidor, banco_servidor):
    """Cada tipo de operação, com nomes hostis, e uma remoção de dois papéis — um com permissão padrão que dá algo
    ao outro (`DROP OWNED BY a, b` falharia) — rodados pelo `grupos(senhas=…)`: o catálogo fica como a vista."""
    b = banco_servidor
    with servidor.conectar() as c:
        for cmd in ('CREATE DATABASE "z ;--\'"', 'CREATE ROLE "c""x; --" LOGIN', 'CREATE ROLE "PUBLIC" NOLOGIN',
                    "CREATE ROLE da LOGIN", "CREATE ROLE db LOGIN", "CREATE ROLE herdeiro LOGIN",
                    "CREATE DATABASE da_banco OWNER da"):
            c.execute(cmd)
    with servidor.conectar(b) as c:
        for cmd in ("CREATE TABLE public.de_da (i int)", "ALTER TABLE public.de_da OWNER TO da",
                    "CREATE TABLE public.de_db (i int)", "ALTER TABLE public.de_db OWNER TO db",
                    "ALTER DEFAULT PRIVILEGES FOR ROLE da GRANT SELECT ON TABLES TO db",
                    "GRANT SELECT ON public.de_da TO db"):
            c.execute(cmd)
    from ratt.db.rascunho_servidor import (CriarPapel, DarAcesso, MudarPoderes, PorNoGrupo, RascunhoServidor,
                                           RemoverPapeis, TirarAcesso, TirarDoGrupo, TrocarDonoDoBanco, TrocarSenha)
    with servidor.conectar(b) as c:
        m = catalogo_servidor.modelo_servidor(c)
        h1 = c.pgconn.encrypt_password(b"segredo1", 'nó"vo'.encode(), b"scram-sha-256").decode()
        h2 = c.pgconn.encrypt_password(b"segredo2", 'c"x; --'.encode(), b"scram-sha-256").decode()
    p = {n: m.papel_por_nome(n).id for n in ('c"x; --', "PUBLIC", "da", "db", "herdeiro")}
    z = m.banco_por_nome("z ;--'").id
    r = RascunhoServidor(m)
    n1, n2 = r.novo_id("p"), r.novo_id("p")
    cx = p['c"x; --']
    for op in (CriarPapel(_servidor.Papel(n1, 'nó"vo', True, False, True, True, membro_de=(p["PUBLIC"],))),
               CriarPapel(_servidor.Papel(n2, "grupo novo", False, False, False, False)),
               MudarPoderes(cx, True, False, True, False), TrocarSenha(cx), PorNoGrupo(cx, p["PUBLIC"]),
               PorNoGrupo(n1, n2), TirarDoGrupo(n1, p["PUBLIC"]), DarAcesso(cx, z), DarAcesso(n1, z),
               TirarAcesso("PUBLIC", z), TirarAcesso(cx, z), TrocarDonoDoBanco(z, n1),
               RemoverPapeis((p["da"], p["db"]), p["herdeiro"],
                             tuple(x.nome for x in m.bancos.values() if x.aceita_conexao))):
        r.acrescentar(op)
    assert r.invalidas == set()
    previsto = r.modelo()
    _aplicar(servidor, r, b, senhas={n1: h1, cx: h2})
    with servidor.conectar(b) as c:
        depois = catalogo_servidor.modelo_servidor(c)
        senhas = dict(c.execute("SELECT rolname, rolpassword FROM pg_authid WHERE rolname = ANY(%s)",
                                (['nó"vo', 'c"x; --'],)).fetchall())
        donos = dict(c.execute("SELECT relname, relowner::regrole::text FROM pg_class "
                               "WHERE relname IN ('de_da', 'de_db')").fetchall())
        padrao = c.execute("SELECT count(*) FROM pg_default_acl").fetchone()[0]
    assert senhas == {'nó"vo': h1, 'c"x; --': h2}
    assert _por_nome(previsto) == _por_nome(depois)
    assert depois.papel_por_nome("da") is None and depois.papel_por_nome("db") is None
    assert depois.banco_por_nome("da_banco").dono == depois.papel_por_nome("herdeiro").id
    assert donos == {"de_da": "herdeiro", "de_db": "herdeiro"} and padrao == 0
    zz = depois.banco_por_nome("z ;--'")
    novo = depois.papel_por_nome('nó"vo')
    assert zz.dono == novo.id and not zz.publico and depois.papel_por_nome('c"x; --').id not in zz.acesso_direto
    assert novo.login and novo.cria_bancos and novo.cria_papeis
    assert novo.membro_de == (depois.papel_por_nome("grupo novo").id,)


# ── o aviso de remoção e o pré-voo, no servidor de verdade ──

class _Conexoes:
    """`conectar(nome)` dos avisos e do pré-voo: uma conexão por banco (guardada, fechada no fim); None para os
    bancos `fora` (como um banco em que a pessoa não consegue entrar)."""

    def __init__(self, servidor, fora=()):
        self.servidor, self.fora, self.abertas, self.pedidos = servidor, set(fora), {}, []

    def __call__(self, nome):
        self.pedidos.append(nome)
        if nome in self.fora:
            return None
        if nome not in self.abertas:
            self.abertas[nome] = self.servidor.conectar(nome)
        return self.abertas[nome]

    def fechar(self):
        for c in self.abertas.values():
            c.close()


def test_aviso_de_remocao_conta_o_que_passa_e_o_que_some_em_cada_banco(servidor, banco_servidor):
    """ana: dona de uma tabela em b1 (e deu SELECT nela a leitura), com SELECT numa tabela do admin, uma permissão
    padrão, dona do banco ratt_da_ana; deu o CONNECT de b1 a carla pela opção de conceder. b4: `conectar` falha."""
    from ratt.db import checagem_servidor as cs
    from ratt.db.rascunho_servidor import RascunhoServidor, RemoverPapeis
    b1 = banco_servidor
    c = _cenario(servidor, b1)
    try:
        for comando in ("CREATE TABLE public.do_admin (i integer)", "GRANT SELECT ON public.do_admin TO ana",
                        "ALTER DEFAULT PRIVILEGES FOR ROLE ana GRANT SELECT ON TABLES TO leitura"):
            c.execute(comando)
    finally:
        c.close()
    with servidor.conectar() as admin:
        for comando in ("CREATE ROLE carla LOGIN", "CREATE DATABASE ratt_da_ana OWNER ana", "CREATE DATABASE ratt_b4",
                        sql.SQL("GRANT CONNECT ON DATABASE {} TO ana WITH GRANT OPTION").format(sql.Identifier(b1)),
                        "SET ROLE ana",
                        sql.SQL("GRANT CONNECT ON DATABASE {} TO carla").format(sql.Identifier(b1)),
                        "RESET ROLE"):
            admin.execute(comando)
        m = catalogo_servidor.modelo_servidor(admin)
    ana, carla, eu = (m.papel_por_nome(n).id for n in ("ana", "carla", servidor.usuario))
    bancos = tuple(b.nome for b in m.bancos.values() if b.aceita_conexao)
    op = RemoverPapeis((ana,), eu, bancos)
    conectar = _Conexoes(servidor, fora={"ratt_b4"})
    try:
        avisos = cs.avisos_da_operacao_servidor(op, RascunhoServidor(m), conectar)
    finally:
        conectar.fechar()
    assert "ratt_b2" not in conectar.pedidos                       # não aceita conexão
    do = lambda chave: [a for a in avisos if a.chave == chave and b1 in a.texto()]
    posse, = do("chk.posse_passa")
    assert "1 tabela" in posse.texto() and "1 função" in posse.texto() and servidor.usuario in posse.texto()
    tem, = do("chk.priv_tem_somem")
    assert tem.nivel == "atencao" and tem.valores["n"] == 2       # o SELECT em do_admin e o CONNECT em b1
    opcao, = do("chk.priv_deu_opcao_somem")
    assert opcao.valores["n"] == 1                                  # o CONNECT que deu a carla
    dono, = do("chk.priv_deu_dono_fica")
    assert dono.nivel == "info" and " 1 " in dono.texto()         # o SELECT em coisas dado a leitura
    padrao, = do("chk.padrao_somem")
    assert padrao.nivel == "atencao" and " 1 " in padrao.texto()
    nao, = [a for a in avisos if a.chave == "chk.nao_verificado"]
    assert "ratt_b4" in nao.texto() and nao.nivel == "atencao"
    banco, = [a for a in avisos if a.chave == "chk.banco_passa"]
    assert "ratt_da_ana" in banco.texto()
    perde, = [a for a in avisos if a.chave == "chk.perde_acesso"]
    assert perde.alvo == carla and b1 in perde.texto()
    # a contagem da cascata é a do PostgreSQL: removida ana de verdade, carla deixa de entrar em b1
    with servidor.conectar(b1) as c1:
        c1.execute(sql.SQL("REASSIGN OWNED BY ana TO {}").format(sql.Identifier(servidor.usuario)))
        c1.execute("DROP OWNED BY ana")
    with servidor.conectar() as admin:
        admin.execute("DROP DATABASE ratt_b2")
        for nome in bancos:
            if nome != b1:
                with servidor.conectar(nome) as cx:
                    cx.execute(sql.SQL("REASSIGN OWNED BY ana TO {}").format(sql.Identifier(servidor.usuario)))
                    cx.execute("DROP OWNED BY ana")
        admin.execute("DROP ROLE ana")
        assert admin.execute("SELECT has_database_privilege('carla', %s, 'CONNECT')", (b1,)).fetchone()[0] is False


def test_prevoo_recusa_antes_de_mudar_e_libera_depois(servidor, banco_servidor):
    """Sessão aberta de ana, b3 com datallowconn=false e novo dono sem LOGIN: o pré-voo recusa (e nada muda);
    resolvido cada um, a lista fica vazia."""
    import psycopg
    from ratt.db import checagem_servidor as cs
    from ratt.db.rascunho_servidor import RascunhoServidor, RemoverPapeis
    b1 = banco_servidor
    _cenario(servidor, b1).close()
    with servidor.conectar() as admin:                             # ratt_b2 (fechado) não tem nada de ana: não pesa
        admin.execute("CREATE DATABASE ratt_b3")
    with servidor.conectar("ratt_b3") as c3:
        c3.execute("CREATE TABLE da_ana (i integer)")
        c3.execute("ALTER TABLE da_ana OWNER TO ana")
    with servidor.conectar() as admin:
        admin.execute("ALTER DATABASE ratt_b3 ALLOW_CONNECTIONS false")
        m = catalogo_servidor.modelo_servidor(admin)
    ana, leitura, eu = (m.papel_por_nome(n).id for n in ("ana", "leitura", servidor.usuario))
    bancos = tuple(b.nome for b in m.bancos.values() if b.aceita_conexao)
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis((ana,), leitura, bancos))
    conectar = _Conexoes(servidor)          # fechada no fim, falhe o que falhar (senão segura o template1)
    try:
        da_ana = psycopg.connect(host=servidor.socket_dir, port=servidor.porta, user="ana", dbname=b1,
                                 autocommit=True)
        try:
            with servidor.conectar() as c:
                msgs = cs.prevoo(r, c, conectar)
            assert len(msgs) == 3
            assert any("ana" in x and "1" in x for x in msgs)
            assert any("ALTER DATABASE ratt_b3 ALLOW_CONNECTIONS true;" in x for x in msgs)
            assert not any("ratt_b2" in x for x in msgs)
            assert any("leitura" in x and "LOGIN" in x for x in msgs)
            with servidor.conectar() as c:                         # nada foi mudado
                assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'ana'").fetchone()[0] == 1
                assert c.execute("SELECT datallowconn FROM pg_database "
                                 "WHERE datname = 'ratt_b3'").fetchone()[0] is False
        finally:
            da_ana.close()
        r = RascunhoServidor(m)
        r.acrescentar(RemoverPapeis((ana,), eu, bancos))
        with servidor.conectar() as admin:
            admin.execute("DROP DATABASE ratt_b3")
            # a sessão de ana pode levar um instante para sair do pg_stat_activity
            for _ in range(50):
                if catalogo_servidor.sessoes(admin, ["ana"])["ana"] == 0:
                    break
                admin.execute("SELECT pg_sleep(0.1)")
            assert cs.prevoo(r, admin, conectar) == []
            admin.execute("ALTER ROLE ana RENAME TO ana2")         # mudou depois do rascunho
            msgs = cs.prevoo(r, admin, conectar)
            assert len(msgs) == 1 and "1" in msgs[0]               # o nome mudou: a remoção deixou de valer
    finally:
        conectar.fechar()


_ACLS = """
    SELECT 'rel', c.oid::bigint, 0, c.relowner, x.grantor, x.grantee, x.privilege_type
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) x
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
    UNION ALL
    SELECT 'col', a.attrelid::bigint, a.attnum, c.relowner, x.grantor, x.grantee, x.privilege_type
    FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace,
         aclexplode(a.attacl) x
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
    UNION ALL
    SELECT 'proc', p.oid::bigint, 0, p.proowner, x.grantor, x.grantee, x.privilege_type
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace, aclexplode(p.proacl) x
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
    UNION ALL
    SELECT 'type', t.oid::bigint, 0, t.typowner, x.grantor, x.grantee, x.privilege_type
    FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace, aclexplode(t.typacl) x
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
    UNION ALL
    SELECT 'nsp', n.oid::bigint, 0, n.nspowner, x.grantor, x.grantee, x.privilege_type
    FROM pg_namespace n, aclexplode(n.nspacl) x
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
    UNION ALL
    SELECT 'db', d.oid::bigint, 0, d.datdba, x.grantor, x.grantee, x.privilege_type
    FROM pg_database d, aclexplode(d.datacl) x WHERE d.datname = current_database()
    UNION ALL
    SELECT 'lo', l.oid::bigint, 0, l.lomowner, x.grantor, x.grantee, x.privilege_type
    FROM pg_largeobject_metadata l, aclexplode(l.lomacl) x
    UNION ALL
    SELECT 'fdw', w.oid::bigint, 0, w.fdwowner, x.grantor, x.grantee, x.privilege_type
    FROM pg_foreign_data_wrapper w, aclexplode(w.fdwacl) x
    UNION ALL
    SELECT 'srv', s.oid::bigint, 0, s.srvowner, x.grantor, x.grantee, x.privilege_type
    FROM pg_foreign_server s, aclexplode(s.srvacl) x"""


def _acls(servidor, bancos, nomes):
    """{banco: {(fonte, objeto, coluna, dono, concedente, quem recebe, privilégio)}} com os papéis por nome."""
    nome = lambda oid: "PUBLIC" if oid == 0 else nomes.get(oid, "?%d" % oid)
    saida = {}
    for b in bancos:
        with servidor.conectar(b) as c:
            saida[b] = {(f, o, s, nome(d), nome(g), nome(r), p) for f, o, s, d, g, r, p in c.execute(_ACLS)}
    return saida


def test_aviso_de_remocao_bate_com_o_que_o_postgresql_faz(servidor, banco_servidor):
    """Remover dora e fabio (novo dono: o superusuário). dora tem privilégios em coluna, função, schema, no banco
    (CONNECT/TEMP), em large object e em foreign data wrapper; repassou parte pela opção de conceder; é dona de uma
    tabela, de um tipo, de um foreign server e de um large object e deu neles a eva, a fabio (também removido) e ao
    novo dono; deu a pertença em g2 a eva e o CONNECT do banco. Cada número do aviso é o que a remoção de verdade
    tira ou mantém no catálogo; quem perde o acesso e quem sai do grupo, também."""
    from ratt.db import checagem_servidor as cs
    from ratt.db.rascunho_servidor import RemoverPapeis, RascunhoServidor
    b6, eu_nome = banco_servidor, servidor.usuario
    q = lambda x: sql.Identifier(x)
    with servidor.conectar() as admin:
        for cmd in ("CREATE ROLE dora LOGIN", "CREATE ROLE fabio LOGIN", "CREATE ROLE eva LOGIN",
                    "CREATE ROLE g2 NOLOGIN", "GRANT g2 TO dora WITH ADMIN OPTION",
                    "SET ROLE dora", "GRANT g2 TO eva", "RESET ROLE",
                    sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(q(b6)),
                    sql.SQL("GRANT CONNECT, TEMP ON DATABASE {} TO dora WITH GRANT OPTION").format(q(b6)),
                    "SET ROLE dora", sql.SQL("GRANT CONNECT ON DATABASE {} TO eva").format(q(b6)), "RESET ROLE"):
            admin.execute(cmd)
    with servidor.conectar(b6) as c:
        lo1 = c.execute("SELECT lo_create(0)").fetchone()[0]
        for cmd in ("CREATE SCHEMA s", "GRANT USAGE ON SCHEMA s TO dora", "GRANT CREATE ON SCHEMA public TO dora",
                    "CREATE TABLE tt (i integer, j integer)", "GRANT SELECT (i) ON tt TO dora WITH GRANT OPTION",
                    "CREATE FUNCTION ff() RETURNS integer LANGUAGE sql AS 'SELECT 1'",
                    "REVOKE EXECUTE ON FUNCTION ff() FROM PUBLIC",
                    "GRANT EXECUTE ON FUNCTION ff() TO dora WITH GRANT OPTION",
                    "CREATE FOREIGN DATA WRAPPER ratt_fdw",
                    "GRANT USAGE ON FOREIGN DATA WRAPPER ratt_fdw TO dora WITH GRANT OPTION",
                    "CREATE SERVER ratt_srv FOREIGN DATA WRAPPER ratt_fdw", "ALTER SERVER ratt_srv OWNER TO dora",
                    "GRANT SELECT ON LARGE OBJECT %d TO dora" % lo1,
                    "SET ROLE dora",
                    "GRANT SELECT (i) ON tt TO eva", "GRANT EXECUTE ON FUNCTION ff() TO eva",
                    "GRANT USAGE ON FOREIGN DATA WRAPPER ratt_fdw TO eva",
                    "CREATE TABLE td (x integer)", "GRANT SELECT ON td TO eva", "GRANT SELECT ON td TO fabio",
                    sql.SQL("GRANT SELECT ON td TO {}").format(q(eu_nome)),
                    "CREATE TYPE cor_d AS ENUM ('a')", "GRANT USAGE ON TYPE cor_d TO eva",
                    "GRANT USAGE ON FOREIGN SERVER ratt_srv TO eva"):
            c.execute(cmd)
        lo2 = c.execute("SELECT lo_create(0)").fetchone()[0]
        c.execute("GRANT SELECT ON LARGE OBJECT %d TO eva" % lo2)
        c.execute("RESET ROLE")
    with servidor.conectar() as admin:
        m = catalogo_servidor.modelo_servidor(admin)
        nomes = {int(o): n for o, n in admin.execute("SELECT oid, rolname FROM pg_roles")}
        membros_antes = set(admin.execute("SELECT member::regrole::text, roleid::regrole::text FROM pg_auth_members"))
        entra_antes = set(admin.execute("SELECT r.rolname, d.datname FROM pg_roles r, pg_database d "
                                        "WHERE d.datallowconn AND r.rolname !~ '^pg_' "
                                        "AND has_database_privilege(r.oid, d.oid, 'CONNECT')"))
    bancos = tuple(b.nome for b in m.bancos.values() if b.aceita_conexao)
    removidos = ("dora", "fabio")
    op = RemoverPapeis(tuple(m.papel_por_nome(n).id for n in removidos), m.eu, bancos)
    conectar = _Conexoes(servidor)
    try:
        avisos = cs.avisos_da_operacao_servidor(op, RascunhoServidor(m), conectar)
    finally:
        conectar.fechar()
    antes = _acls(servidor, bancos, nomes)
    for destino, cmd in op.comandos(m):                            # a remoção de verdade, como o ratt a gera
        with servidor.conectar(destino or "postgres") as c:
            c.execute(cmd)
    with servidor.conectar() as admin:
        membros_depois = set(admin.execute("SELECT member::regrole::text, roleid::regrole::text FROM pg_auth_members"))
        entra_depois = set(admin.execute("SELECT r.rolname, d.datname FROM pg_roles r, pg_database d "
                                         "WHERE d.datallowconn AND r.rolname !~ '^pg_' "
                                         "AND has_database_privilege(r.oid, d.oid, 'CONNECT')"))
    depois = _acls(servidor, bancos, {o: n for o, n in nomes.items() if n not in removidos})
    numero = lambda chave, papel, banco: sum(a.valores["n"] for a in avisos if a.chave == chave
                                             and a.valores["papel"] == papel and a.valores["banco"] == banco)
    fontes = {"tem": set(), "opcao": set(), "dono": set()}
    for b in bancos:
        sumiram = antes[b] - depois[b]
        for r in removidos:
            tem = {e for e in sumiram if e[5] == r and e[4] != r}
            opcao = {e for e in sumiram if e[4] == r and e[5] != r and e[3] != r}
            dados = {e for e in antes[b] if e[4] == r and e[3] == r and e[5] not in removidos + (eu_nome,)}
            ficaram = {e for e in dados if (e[0], e[1], e[2], eu_nome, eu_nome, e[5], e[6]) in depois[b]}
            assert ficaram == dados, (b, r, dados - ficaram)       # o que deu como dono fica, vindo do novo dono
            assert {e for e in antes[b] if e[5] == r and e[4] != r} == tem      # o que tinha sumiu todo
            assert numero("chk.priv_tem_somem", r, b) == len(tem), (b, r)
            assert numero("chk.priv_deu_opcao_somem", r, b) == len(opcao), (b, r)
            assert numero("chk.priv_deu_dono_fica", r, b) == len(ficaram), (b, r)
            fontes["tem"] |= {e[0] for e in tem}
            fontes["opcao"] |= {e[0] for e in opcao}
            fontes["dono"] |= {e[0] for e in ficaram}
    # o cenário passou por cada tipo de acl
    assert fontes["tem"] >= {"col", "proc", "nsp", "db", "lo", "fdw"}
    assert fontes["opcao"] >= {"col", "proc", "fdw", "db"}
    assert fontes["dono"] >= {"rel", "type", "srv", "lo"}
    assert numero("chk.priv_tem_somem", "fabio", b6) == 1          # o SELECT em td, dado por dora
    posse, = [a for a in avisos if a.chave == "chk.posse_passa" and a.valores["banco"] == b6]
    assert posse.valores["papel"] == "dora" and posse.valores["lista"] == "1 tabela, 1 tipo"
    perde = {(a.valores["papel"], a.valores["banco"]) for a in avisos if a.chave == "chk.perde_acesso"}
    assert perde == {x for x in entra_antes - entra_depois if x[0] not in removidos} == {("eva", b6)}
    sai = {(a.valores["papel"], a.valores["grupo"]) for a in avisos if a.chave == "chk.sai_do_grupo"}
    assert sai == {x for x in membros_antes - membros_depois if x[0] not in removidos}
    if m.versao >= 160000:                                         # a pertença que dora deu cai com ela
        assert sai == {("eva", "g2")}


# ── a sessão do servidor: aplicar banco a banco, senha só ao aplicar, sudo ──

import contextlib
import re
import time

SENHA = "s3nh@-de-teste-ratt"


def _sessao_servidor(servidor, b1, monkeypatch, tmp_path):
    """Uma `SessaoServidor` com os dados do ratt em `tmp_path` e o catálogo lido de `b1`."""
    from ratt.db.sessao import SessaoServidor
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "dados"))
    s = SessaoServidor(servidor.porta)
    with servidor.conectar(b1) as c:
        assert s.abrir(catalogo_servidor.modelo_servidor(c)) == []
    return s


def _reabrir(s, servidor, b1):
    with servidor.conectar(b1) as c:
        return s.abrir(catalogo_servidor.modelo_servidor(c))


def _conectar_de(conexoes, b1):
    """O `conectar` da sessão: None é o banco atual (`b1`)."""
    return lambda nome: conexoes(nome or b1)


def _sem_segredo_no_disco(raiz, *segredos):
    """Nenhum arquivo do ratt (rascunho, aplicados, o que for) tem a senha, um hash SCRAM ou os segredos dados."""
    vistos = 0
    for pasta, _, arquivos in os.walk(raiz):
        for nome in arquivos:
            with open(os.path.join(pasta, nome), "rb") as f:
                texto = f.read().decode("utf-8", "replace")
            vistos += 1
            assert SENHA not in texto and "SCRAM-SHA-256$" not in texto, nome
            for s in segredos:
                assert s not in texto, nome
    return vistos


@contextlib.contextmanager
def _senha_obrigatoria(servidor, papel):
    """O `pg_hba.conf` do servidor descartável passa a pedir senha (SCRAM) a `papel` pelo socket, até o fim do
    bloco (o resto continua `trust`). Espera o reload valer nos dois sentidos."""
    import psycopg
    hba = os.path.join(servidor.dados, "pg_hba.conf")
    with open(hba, encoding="utf-8") as f:
        antes = f.read()

    def sem_senha_entra():
        try:
            psycopg.connect(host=servidor.socket_dir, port=servidor.porta, user=papel, dbname="postgres",
                            connect_timeout=5).close()
            return True
        except psycopg.OperationalError:
            return False

    def esperar(condicao):
        for _ in range(100):
            if condicao():
                return
            time.sleep(0.05)
        raise AssertionError("o pg_hba.conf novo não passou a valer")

    with open(hba, "w", encoding="utf-8") as f:
        f.write(f"local all {papel} scram-sha-256\n" + antes)
    with servidor.conectar() as c:
        c.execute("SELECT pg_reload_conf()")
    try:
        esperar(lambda: not sem_senha_entra())
        yield
    finally:
        with open(hba, "w", encoding="utf-8") as f:
            f.write(antes)
        with servidor.conectar() as c:
            c.execute("SELECT pg_reload_conf()")
        esperar(sem_senha_entra)


def test_sessao_servidor_cria_ana_com_senha_e_nada_vai_ao_disco(servidor, banco_servidor, monkeypatch, tmp_path):
    """Ensaio: não pede senha e não cria nada. Aplicar: pede a senha de ana uma vez, ana entra com ela (o servidor
    pede SCRAM a ana) e não entra com outra; nenhum arquivo do ratt tem a senha nem o hash."""
    import psycopg
    import pytest
    from ratt.db.rascunho_servidor import CriarPapel
    b1 = banco_servidor
    s = _sessao_servidor(servidor, b1, monkeypatch, tmp_path)
    n = s.rascunho.novo_id("p")
    s.acrescentar(CriarPapel(_servidor.Papel(n, "ana", True, False, False, False)))
    assert (tmp_path / "dados" / "ratt" / "canvas" / f"{servidor.porta}-servidor" / "rascunho.json").is_file()
    pedidos = []

    def nao_pede(nome):
        raise AssertionError("o ensaio pediu senha")

    conexoes = _Conexoes(servidor)
    try:
        assert s.aplicar(_conectar_de(conexoes, b1), True, nao_pede)[0] == 0
        with servidor.conectar() as c:
            assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'ana'").fetchone()[0] == 0
        assert len(s.rascunho.operacoes) == 1
        texto = s.rascunho.sql_texto(s.cabecalho())
        codigo, indice, msg = s.aplicar(_conectar_de(conexoes, b1), False,
                                        lambda nome: pedidos.append(nome) or SENHA, confirmado=texto)
    finally:
        conexoes.fechar()
    assert (codigo, indice, msg) == (0, None, None) and pedidos == ["ana"]
    s.concluir(codigo, indice, False, texto)
    assert s.rascunho.operacoes == [] and s.aplicando
    with servidor.conectar() as c:
        hash_, = c.execute("SELECT rolpassword FROM pg_authid WHERE rolname = 'ana'").fetchone()
    assert hash_.startswith("SCRAM-SHA-256$")
    with _senha_obrigatoria(servidor, "ana"):
        with psycopg.connect(host=servidor.socket_dir, port=servidor.porta, user="ana", dbname=b1,
                             password=SENHA, connect_timeout=5) as c:
            assert c.execute("SELECT current_user").fetchone()[0] == "ana"
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(host=servidor.socket_dir, port=servidor.porta, user="ana", dbname=b1,
                            password="outra", connect_timeout=5)
    aplicados = list((tmp_path / "dados" / "ratt" / "canvas" / f"{servidor.porta}-servidor" / "aplicados").iterdir())
    assert len(aplicados) == 1 and aplicados[0].name.startswith("servidor-")
    assert "CREATE ROLE ana LOGIN" in aplicados[0].read_text(encoding="utf-8")
    assert _sem_segredo_no_disco(tmp_path / "dados", hash_) == 1        # o rascunho vazio sumiu; fica o aplicado


def _objetos_em(servidor, banco, dono):
    with servidor.conectar(banco) as c:
        c.execute(sql.SQL("CREATE TABLE {} (i integer)").format(sql.Identifier(f"t_{dono}")))
        c.execute(sql.SQL("ALTER TABLE {} OWNER TO {}").format(sql.Identifier(f"t_{dono}"), sql.Identifier(dono)))


def _donos(servidor, banco):
    with servidor.conectar(banco) as c:
        return dict(c.execute("SELECT relname, relowner::regrole::text FROM pg_class "
                              "WHERE relname LIKE 't\\_%' AND relkind = 'r'").fetchall())


def test_sessao_servidor_remove_em_todos_os_bancos_e_termina_depois_de_parcial(servidor, banco_servidor,
                                                                               monkeypatch, tmp_path):
    """carlos (novo, com senha) recebe o que ana e bruno possuem em ratt_x1 e ratt_x2. Um gatilho de evento em ratt_x2
    faz o DROP OWNED falhar: o servidor (carlos) e ratt_x1 ficam feitos, a remoção fica "parcial" — no disco também —
    e o que foi feito sai do rascunho (a remoção passa a referir carlos pelo id do catálogo). Sem o gatilho, aplicar de
    novo termina: tudo é de carlos, ana e bruno somem. A remoção entra com bancos velhos: o aplicar refaz a lista."""
    import json
    from ratt.db.rascunho_servidor import CriarPapel, RemoverPapeis
    from ratt.db.sessao import SessaoServidor
    b1 = banco_servidor
    with servidor.conectar() as c:
        for cmd in ("CREATE ROLE ana LOGIN", "CREATE ROLE bruno LOGIN", "CREATE DATABASE ratt_x1",
                    "CREATE DATABASE ratt_x2"):
            c.execute(cmd)
    _objetos_em(servidor, "ratt_x1", "ana")
    _objetos_em(servidor, "ratt_x2", "ana")
    _objetos_em(servidor, "ratt_x2", "bruno")
    with servidor.conectar("ratt_x2") as c:
        c.execute("CREATE FUNCTION public.barra() RETURNS event_trigger LANGUAGE plpgsql AS "
                  "$$ BEGIN RAISE EXCEPTION 'barrado de propósito'; END $$")
        c.execute("CREATE EVENT TRIGGER barra ON ddl_command_start WHEN TAG IN ('DROP OWNED') "
                  "EXECUTE FUNCTION public.barra()")
    s = _sessao_servidor(servidor, b1, monkeypatch, tmp_path)
    m = s.rascunho.base
    ana, bruno = m.papel_por_nome("ana").id, m.papel_por_nome("bruno").id
    n = s.rascunho.novo_id("p")
    s.acrescentar(CriarPapel(_servidor.Papel(n, "carlos", True, False, False, False)))
    s.acrescentar(RemoverPapeis((ana, bruno), n, (b1,)))       # bancos velhos: o rebase/aplicar refaz
    remocao = s.rascunho.operacoes[1]
    conexoes = _Conexoes(servidor)
    try:
        _reabrir(s, servidor, b1)
        assert {"ratt_x1", "ratt_x2", "postgres", b1} <= set(remocao.bancos)
        codigo, indice, msg = s.aplicar(_conectar_de(conexoes, b1), False, lambda nome: SENHA)
        assert codigo == 1 and indice == 0
        assert "ratt_x1" in msg.split("falhou")[0] and "falhou em ratt_x2" in msg and "barrado de propósito" in msg
        assert "não rodado:" in msg and "template1" in msg.split("não rodado:")[1]
        assert s.rascunho.operacoes == [remocao] and s.falha == 0 and s.mensagem_falha == msg
        with servidor.conectar() as c:
            carlos = c.execute("SELECT 'r' || oid FROM pg_roles WHERE rolname = 'carlos'").fetchone()[0]
        assert remocao.novo_dono == carlos and s.rascunho.invalidas == set()
        assert _donos(servidor, "ratt_x1") == {"t_ana": "carlos"}
        assert _donos(servidor, "ratt_x2") == {"t_ana": "ana", "t_bruno": "bruno"}
        pasta = tmp_path / "dados" / "ratt" / "canvas" / f"{servidor.porta}-servidor"
        gravado = json.loads((pasta / "rascunho.json").read_text(encoding="utf-8"))
        assert gravado["parcial"] == {"op": 0, "mensagem": msg}
        outra = SessaoServidor(servidor.porta)                     # outra sessão (outro processo) vê a marca
        _reabrir(outra, servidor, b1)
        assert outra.falha == 0 and outra.mensagem_falha == msg
        aplicado, = (pasta / "aplicados").iterdir()
        assert aplicado.read_text(encoding="utf-8").startswith("-- PARCIAL: ")
        with servidor.conectar("ratt_x2") as c:
            c.execute("DROP EVENT TRIGGER barra")
        codigo, indice, msg = s.aplicar(_conectar_de(conexoes, b1), False, lambda nome: SENHA)
    finally:
        conexoes.fechar()
    assert (codigo, indice, msg) == (0, None, None)
    assert _donos(servidor, "ratt_x1") == {"t_ana": "carlos"}
    assert _donos(servidor, "ratt_x2") == {"t_ana": "carlos", "t_bruno": "carlos"}
    with servidor.conectar() as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('ana', 'bruno')").fetchone()[0] == 0
    assert not (pasta / "rascunho.json").exists()
    _sem_segredo_no_disco(tmp_path / "dados")


def test_sessao_servidor_remover_sem_ser_superusuario_oferece_sudo(servidor, banco_servidor, monkeypatch, tmp_path):
    """maria (CREATEROLE, não superusuária) cria um papel e remove ana: o aplicar recusa antes de mudar nada e oferece
    o sudo; aceito, cada lote vai por `sudo -u postgres psql … -1` com o SQL pela entrada padrão (o executor falso
    roda a entrada no servidor descartável como superusuário). A senha é pedida uma vez só; o SQL e o hash nunca
    aparecem nos argumentos."""
    from ratt.db import painel
    from ratt.db.rascunho_servidor import CriarPapel, RemoverPapeis
    b1 = banco_servidor
    with servidor.conectar() as c:
        for cmd in ("CREATE ROLE maria LOGIN CREATEROLE", "CREATE ROLE ana LOGIN"):
            c.execute(cmd)
    _objetos_em(servidor, b1, "ana")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "dados"))
    from ratt.db.sessao import SessaoServidor
    s = SessaoServidor(servidor.porta)
    with _conectar_como(servidor, "maria", b1) as c:
        s.abrir(catalogo_servidor.modelo_servidor(c))
    m = s.rascunho.base
    n = s.rascunho.novo_id("p")
    s.acrescentar(CriarPapel(_servidor.Papel(n, "dani", True, False, False, False)))
    s.acrescentar(RemoverPapeis((m.papel_por_nome("ana").id,), m.papel_por_nome(servidor.usuario).id, ()))
    with _conectar_como(servidor, "maria", b1) as c:                 # o vigia da tela: os bancos são refeitos
        s.rebase(catalogo_servidor.modelo_servidor(c))
    assert "postgres" in s.rascunho.operacoes[1].bancos
    chamadas, perguntas, pedidos, sudo = [], [], [], []

    def executor(argv, entrada):
        chamadas.append((argv, entrada))
        banco = argv[argv.index("-d") + 1]
        try:
            with servidor.conectar(banco) as c:
                if "-1" in argv:
                    with c.transaction():
                        c.execute(entrada)
                else:
                    c.execute(entrada)
        except Exception as e:               # como o psql: o erro na saída de erro, código 3
            return 3, f"psql:<stdin>:1: ERROR:  {e}\nLINE 1: {entrada}"
        return 0, ""

    abertas = {}

    def conectar(nome):
        nome = nome or b1
        if nome not in abertas:
            abertas[nome] = _conectar_como(servidor, "maria", nome)
        return abertas[nome]

    try:
        texto = s.rascunho.sql_texto(s.cabecalho())
        codigo, indice, msg = painel.aplicar_servidor(
            s, conectar, False, lambda nome: pedidos.append(nome) or SENHA,
            lambda pergunta: perguntas.append(pergunta) or True, runner=executor,
            garantir_sudo=lambda: sudo.append(1) or True, confirmado=texto)
    finally:
        for c in abertas.values():
            c.close()
    assert (codigo, indice, msg) == (0, None, None), msg
    assert len(perguntas) == 1 and "sudo" in perguntas[0] and sudo == [1] and pedidos == ["dani"]
    destinos = [argv[argv.index("-d") + 1] for argv, _ in chamadas]
    assert destinos[0] == b1 and destinos[-1] == b1 and "postgres" in destinos
    for argv, entrada in chamadas:
        if os.geteuid() != 0:
            assert argv[:4] == ["sudo", "-u", "postgres", "psql"]
        assert argv[-3:] == ["-v", "ON_ERROR_STOP=1", "-1"]
        assert "-X" in argv and "-q" in argv and argv[argv.index("-p") + 1] == str(servidor.porta)
        assert not any(";" in a or "ROLE" in a or "SCRAM" in a for a in argv)     # nada de SQL nos argumentos
    assert "CREATE ROLE dani LOGIN;" in chamadas[0][1] and "PASSWORD 'SCRAM-SHA-256$" in chamadas[0][1]
    assert "DROP ROLE ana;" in chamadas[-1][1]
    with servidor.conectar() as c:
        assert c.execute("SELECT rolpassword LIKE 'SCRAM-SHA-256$%' FROM pg_authid "
                         "WHERE rolname = 'dani'").fetchone()[0] is True
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'ana'").fetchone()[0] == 0
    assert _donos(servidor, b1) == {"t_ana": servidor.usuario}
    _sem_segredo_no_disco(tmp_path / "dados")


def test_sessao_servidor_erro_de_psql_pelo_sudo_nao_mostra_o_hash(servidor, banco_servidor, monkeypatch, tmp_path):
    """Pelo sudo, o psql que falha repete o comando (`LINE 1: …`): a mensagem e a marca não levam o hash, e a
    operação marcada é a do comando da linha em que ele parou."""
    from ratt.db.rascunho_servidor import CriarPapel, PorNoGrupo
    b1 = banco_servidor
    with servidor.conectar() as c:
        c.execute("CREATE ROLE grp NOLOGIN")
    s = _sessao_servidor(servidor, b1, monkeypatch, tmp_path)
    n = s.rascunho.novo_id("p")
    s.acrescentar(CriarPapel(_servidor.Papel(n, "eva", True, False, False, False)))
    s.acrescentar(PorNoGrupo(n, s.rascunho.base.papel_por_nome("grp").id))
    with servidor.conectar() as c:
        c.execute("DROP ROLE grp")
        c.execute("CREATE ROLE grp NOLOGIN")               # mesmo nome, outro oid: o pré-voo recusa
    conexoes = _Conexoes(servidor)
    entradas = []

    def executor(argv, entrada):
        entradas.append(entrada)
        hash_ = re.search(r"'(SCRAM-SHA-256\$[^']+)'", entrada).group(1)
        return 3, f"psql:<stdin>:2: ERROR:  syntax error at or near \"{hash_}\"\nLINE 1: ALTER ROLE eva PASSWORD '{hash_}'\n    ^"

    try:
        codigo, indice, msg = s.aplicar(_conectar_de(conexoes, b1), False, lambda nome: SENHA, como_postgres=True,
                                        runner=executor)
        assert codigo == 1 and indice is None and "grp" in msg and entradas == []     # recusado antes
        s.tirar([1])
        codigo, indice, msg = s.aplicar(_conectar_de(conexoes, b1), False, lambda nome: SENHA, como_postgres=True,
                                        runner=executor)
    finally:
        conexoes.fechar()
    assert codigo == 1 and indice == 0 and len(entradas) == 1
    assert "SCRAM" not in msg and "<hash>" in msg and "LINE" not in msg and SENHA not in msg
    assert "SCRAM" not in (s.mensagem_falha or "")
    _sem_segredo_no_disco(tmp_path / "dados")


def test_sessao_servidor_revoke_com_dependentes_e_revoke_de_quem_nao_concedeu(servidor, banco_servidor,
                                                                              monkeypatch, tmp_path):
    """y tem o CONNECT com a opção de conceder e o deu a x. Tirar o de y: "dependent privileges exist" — a mensagem
    diz que x depende, nada muda e a operação fica marcada. Tirar o de x (dado por y): o superusuário revoga como o
    dono e o de y fica — o aplicar não diz que deu certo: "o acesso continua: foi concedido por y". No 16+, o
    mesmo com a pertença em g dada por y."""
    from ratt.db.rascunho_servidor import TirarAcesso, TirarDoGrupo
    b1 = banco_servidor
    with servidor.conectar() as c:
        v16 = c.info.server_version >= 160000
        for cmd in ("CREATE ROLE y LOGIN", "CREATE ROLE x LOGIN", "CREATE ROLE g NOLOGIN",
                    f"REVOKE CONNECT ON DATABASE {b1} FROM PUBLIC",
                    f"GRANT CONNECT ON DATABASE {b1} TO y WITH GRANT OPTION", "GRANT g TO y WITH ADMIN OPTION"):
            c.execute(cmd)
        c.execute("SET ROLE y")
        c.execute(f"GRANT CONNECT ON DATABASE {b1} TO x")
        c.execute("GRANT g TO x")
        c.execute("RESET ROLE")
    s = _sessao_servidor(servidor, b1, monkeypatch, tmp_path)
    m = s.rascunho.base
    x, y, g = (m.papel_por_nome(nome).id for nome in ("x", "y", "g"))
    d = m.banco_por_nome(b1).id
    conexoes = _Conexoes(servidor)
    try:
        s.acrescentar(TirarAcesso(y, d))
        codigo, indice, msg = s.aplicar(_conectar_de(conexoes, b1), False, lambda nome: SENHA)
        assert codigo == 1 and indice == 0 and s.falha == 0
        assert "x" in msg.split("—")[-1] and "CASCADE" in msg
        with servidor.conectar() as c:
            assert c.execute("SELECT has_database_privilege('y', %s, 'CONNECT')", (b1,)).fetchone()[0]
        s.tirar([0])
        s.acrescentar(TirarAcesso(x, d))
        if v16:
            s.acrescentar(TirarDoGrupo(x, g))
        codigo, indice, msg = s.aplicar(_conectar_de(conexoes, b1), False, lambda nome: SENHA)
    finally:
        conexoes.fechar()
    assert codigo == 0 and indice is None and msg == s.aviso
    assert f"o acesso de x ao banco {b1} continua: foi concedido por y" in msg
    if v16:
        assert "x continua no grupo g: a pertença foi concedida por y" in msg
    with servidor.conectar() as c:
        assert c.execute("SELECT has_database_privilege('x', %s, 'CONNECT')", (b1,)).fetchone()[0]


def test_sessao_servidor_ensaio_da_remocao_nao_muda_nada(servidor, banco_servidor, monkeypatch, tmp_path):
    """O ensaio roda cada banco e desfaz; o DROP ROLE do fim, que ainda vê os objetos (os bancos foram desfeitos), é
    o esperado do ensaio, não uma falha. Nada muda no servidor nem no rascunho."""
    from ratt.db.rascunho_servidor import RemoverPapeis
    b1 = banco_servidor
    with servidor.conectar() as c:
        c.execute("CREATE ROLE ana LOGIN")
    _objetos_em(servidor, b1, "ana")
    s = _sessao_servidor(servidor, b1, monkeypatch, tmp_path)
    m = s.rascunho.base
    s.acrescentar(RemoverPapeis((m.papel_por_nome("ana").id,), m.eu, ()))
    _reabrir(s, servidor, b1)
    antes = s.rascunho.sql_texto(s.cabecalho())
    conexoes = _Conexoes(servidor)
    import io
    from contextlib import redirect_stdout
    saida = io.StringIO()
    try:
        with redirect_stdout(saida):
            assert s.aplicar(_conectar_de(conexoes, b1), True, lambda nome: SENHA) == (0, None, None)
    finally:
        conexoes.fechar()
    assert "esperado no ensaio" in saida.getvalue() and "conferido no estado de agora" in saida.getvalue()
    assert _donos(servidor, b1) == {"t_ana": "ana"} and s.rascunho.sql_texto(s.cabecalho()) == antes
    with servidor.conectar() as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'ana'").fetchone()[0] == 1


def test_aplicar_o_rascunho_do_servidor_pela_tela(servidor, banco_servidor, monkeypatch, tmp_path):
    """Uma tela com `AcoesRascunhoServidor` e o painel: o painel mostra os passos por banco; Enter abre a confirmação
    com o SQL; confirmado, a parte suspensa (trocada por uma sem terminal que chama `aplicar_servidor` com a senha
    falsa) aplica, a sessão esvazia e a tela é avisada para reler."""
    import asyncio
    from contextlib import contextmanager
    from textual.app import App
    from textual.binding import Binding
    from textual.screen import Screen
    from conftest import esperar_que, esperar_tela, saida_presa
    from ratt.db import formularios, painel
    from ratt.db.rascunho_servidor import CriarPapel, RemoverPapeis
    b1 = banco_servidor
    with servidor.conectar() as c:
        c.execute("CREATE ROLE bruno LOGIN")
    _objetos_em(servidor, b1, "bruno")
    s = _sessao_servidor(servidor, b1, monkeypatch, tmp_path)
    m = s.rascunho.base
    n = s.rascunho.novo_id("p")
    s.acrescentar(CriarPapel(_servidor.Papel(n, "ana", True, False, False, False)))
    s.acrescentar(RemoverPapeis((m.papel_por_nome("bruno").id,), m.eu, ()))
    _reabrir(s, servidor, b1)
    relidos = []

    class Fonte:
        porta, usuario, banco = servidor.porta, servidor.usuario, b1

    class Tela(painel.AcoesRascunhoServidor, Screen):
        BINDINGS = [Binding("enter", "aplicar"), Binding("e", "ensaio")]

        def __init__(self):
            super().__init__()
            self.fonte, self.sessao = Fonte(), s

        def compose(self):
            yield painel.PainelSQL()

        def on_mount(self):
            self.query_one(painel.PainelSQL).atualizar(self.sessao.rascunho.passos())

        def _pronto(self):
            return True

        def _conectar(self):
            return painel.ConexoesServidor(servidor.porta, servidor.usuario, b1,
                                           entrar=lambda porta, usuario, nome: (servidor.conectar(nome), None))

        def _sessao_servidor(self, conectar, ensaio, cabecalho, confirmado):
            return painel.aplicar_servidor(self.sessao, conectar, ensaio, lambda nome: SENHA, lambda p: False,
                                           confirmado=confirmado, cabecalho=cabecalho)

        def depois_de_aplicar(self, esquema, nomes):
            with servidor.conectar(b1) as c:
                self.sessao.aplicado(catalogo_servidor.modelo_servidor(c))
            relidos.append(esquema)

    class AppTeste(App):
        def on_mount(self):
            self.push_screen(Tela())

    @contextmanager
    def sem_suspender(self):
        yield

    monkeypatch.setattr(App, "suspend", sem_suspender)

    async def roteiro():
        app = AppTeste()
        async with app.run_test(size=(100, 40)) as piloto:
            await esperar_que(piloto, lambda: isinstance(app.screen, Tela))
            plano = app.screen.query_one(painel.PainelSQL).texto_plano()
            assert "-- no banco postgres:" in plano and "DROP ROLE bruno;" in plano and "CREATE ROLE ana LOGIN;" in plano
            await piloto.press("enter")
            await esperar_que(piloto, lambda: isinstance(app.screen, formularios.Confirmar))
            await esperar_tela(piloto, app.screen)
            with saida_presa():
                await piloto.click("#sim")
                await esperar_que(piloto, lambda: relidos == [None])
            assert not s.aplicando and s.rascunho.operacoes == []
    asyncio.run(roteiro())
    with servidor.conectar() as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'ana'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'bruno'").fetchone()[0] == 0
    assert _donos(servidor, b1) == {"t_bruno": servidor.usuario}
    _sem_segredo_no_disco(tmp_path / "dados")


# ── Task 5: permissões de tabela e de schema (rascunho do schema), aplicadas pelo caminho de sempre ──

def _aplicar_schema(servidor, banco, r, tmp_path):
    """Aplica o rascunho do schema como a tela aplica: `SessaoRascunho.aplicar` (o mesmo executor do `ratt db run`)."""
    from ratt.db import sessao as _sessao_mod
    s = _sessao_mod.SessaoRascunho(servidor.porta, banco, r.base.esquema)
    s.rascunho = r
    con = servidor.conectar(banco)
    try:
        codigo, indice, msg = s.aplicar(con, tmp_path / "perm.sql", ensaio=False)
    finally:
        con.close()
    assert (codigo, indice, msg) == (0, None, None)


def _ids(m):
    return {nome: i for i, nome in m.papeis.items()}, {tab.nome: i for i, tab in m.tabelas.items()}


def test_conceder_aplicado_da_select_nas_tabelas_da_lista(servidor, banco_servidor, tmp_path):
    from ratt.db import rascunho
    admin = servidor.conectar()
    admin.execute('CREATE ROLE leitura NOLOGIN')
    admin.execute('CREATE ROLE "Leitura Ü" NOLOGIN')
    admin.close()
    c = servidor.conectar(banco_servidor)
    c.execute('CREATE TABLE public.a (id int); CREATE TABLE public.b (id int); CREATE TABLE public."C x" (id int)')
    m = catalogo.modelo(c, "public")
    papeis, tabelas = _ids(m)
    assert m.eu == papeis[servidor.usuario]
    r = rascunho.Rascunho(m)
    r.acrescentar(rascunho.Conceder((papeis["leitura"], papeis["Leitura Ü"]), (tabelas["a"], tabelas["b"]), ("S",)))
    r.acrescentar(rascunho.Conceder(("PUBLIC",), (tabelas["C x"],), ("I", "S")))
    _aplicar_schema(servidor, banco_servidor, r, tmp_path)
    tem = lambda papel, tab, priv: c.execute("SELECT has_table_privilege(%s, %s, %s)", (papel, tab, priv)).fetchone()[0]
    assert tem("leitura", "public.a", "SELECT") and tem("leitura", "public.b", "SELECT")
    assert tem("Leitura Ü", "public.b", "SELECT")
    assert not tem("leitura", "public.a", "INSERT")
    assert tem("leitura", 'public."C x"', "INSERT")                    # pelo PUBLIC
    assert tem("public", 'public."C x"', "INSERT")
    # revogar pelo mesmo caminho
    r = rascunho.Rascunho(catalogo.modelo(c, "public"))
    r.acrescentar(rascunho.Revogar((papeis["leitura"],), (tabelas["b"],), ("S",)))
    _aplicar_schema(servidor, banco_servidor, r, tmp_path)
    assert tem("leitura", "public.a", "SELECT") and not tem("leitura", "public.b", "SELECT")
    c.close()


def test_futuras_vale_para_a_tabela_que_eu_criar_depois(servidor, banco_servidor, tmp_path):
    from ratt.db import rascunho
    admin = servidor.conectar()
    admin.execute("CREATE ROLE leitura NOLOGIN")
    admin.close()
    c = servidor.conectar(banco_servidor)
    c.execute('CREATE SCHEMA "Vendas"; CREATE TABLE "Vendas".velha (id int)')
    m = catalogo.modelo(c, "Vendas")
    papeis, tabelas = _ids(m)
    eu = m.papeis[m.eu]
    r = rascunho.Rascunho(m)
    r.acrescentar(rascunho.Conceder((papeis["leitura"],), (tabelas["velha"],), ("S",), futuras=True, por=eu))
    r.acrescentar(rascunho.PermissaoNoSchema(papeis["leitura"], True, None))
    _aplicar_schema(servidor, banco_servidor, r, tmp_path)
    c.execute('CREATE TABLE "Vendas".nova (id int)')
    tem = lambda tab: c.execute("SELECT has_table_privilege('leitura', %s, 'SELECT')", (tab,)).fetchone()[0]
    assert tem('"Vendas".velha') and tem('"Vendas".nova')
    assert c.execute("SELECT has_schema_privilege('leitura', 'Vendas', 'USAGE')").fetchone()[0]
    p = catalogo.permissoes(c, "Vendas")
    assert p.padrao == {papeis["leitura"]: frozenset({"S"})}
    # revogar as futuras: a próxima tabela já não vem com o SELECT
    r = rascunho.Rascunho(catalogo.modelo(c, "Vendas"))
    r.acrescentar(rascunho.Revogar((papeis["leitura"],), (), ("S",), futuras=True, por=eu))
    _aplicar_schema(servidor, banco_servidor, r, tmp_path)
    c.execute('CREATE TABLE "Vendas".outra (id int)')
    assert not tem('"Vendas".outra') and catalogo.permissoes(c, "Vendas").padrao == {}
    c.close()


def test_permissoes_le_public_dono_schema_e_padrao(servidor, banco_servidor):
    admin = servidor.conectar()
    admin.execute("CREATE ROLE ana LOGIN")
    admin.execute("CREATE ROLE leitura NOLOGIN")
    admin.close()
    c = servidor.conectar(banco_servidor)
    c.execute("CREATE TABLE public.a (id int); CREATE TABLE public.b (id int); CREATE TABLE public.vazia (id int)")
    c.execute("CREATE TABLE public.mae (id int) PARTITION BY RANGE (id)")
    c.execute("CREATE TABLE public.filha PARTITION OF public.mae FOR VALUES FROM (0) TO (10)")
    c.execute("CREATE VIEW public.v AS SELECT 1 AS x")
    c.execute("ALTER TABLE public.b OWNER TO ana")
    c.execute("GRANT SELECT ON public.a TO PUBLIC")
    c.execute("GRANT INSERT, TRIGGER ON public.a TO leitura")
    c.execute("REVOKE ALL ON public.vazia FROM CURRENT_USER")      # o dono tirou tudo de si: a ACL fica vazia
    c.execute("GRANT CREATE ON SCHEMA public TO leitura")
    c.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, UPDATE ON TABLES TO leitura")
    c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE ana IN SCHEMA public GRANT SELECT ON TABLES TO leitura")
    m = catalogo.modelo(c, "public")
    papeis, tabelas = _ids(m)
    eu = papeis[servidor.usuario]
    p = catalogo.permissoes(c, "public")
    tudo = frozenset("SIUDTRt")
    assert p.por_tabela[("PUBLIC", tabelas["a"])] == frozenset("S")
    assert p.por_tabela[(papeis["leitura"], tabelas["a"])] == frozenset("It")
    assert p.por_tabela[(eu, tabelas["a"])] == tudo                      # sem MAINTAIN, mesmo no 17+
    assert p.por_tabela[(papeis["ana"], tabelas["b"])] == tudo           # ACL padrão (NULL): o dono tem tudo
    assert not any(k[1] == tabelas["vazia"] for k in p.por_tabela)
    assert p.dono == {tabelas["a"]: eu, tabelas["b"]: papeis["ana"], tabelas["vazia"]: eu, tabelas["mae"]: eu}
    assert "v" not in tabelas and "filha" not in tabelas and set(p.dono) == set(m.tabelas)
    assert p.schema["PUBLIC"] == frozenset({"USAGE"}) and p.schema[papeis["leitura"]] == frozenset({"CREATE"})
    assert p.padrao == {papeis["leitura"]: frozenset({"S", "U"})}       # só os FOR ROLE da pessoa
    assert {papeis["leitura"], papeis["ana"], eu} <= set(m.papeis)
    c.close()


def test_avisos_de_permissao_com_o_banco(servidor, banco_servidor):
    from ratt.db import checagem, rascunho
    admin = servidor.conectar()
    admin.execute("CREATE ROLE leitura NOLOGIN")
    admin.execute("CREATE ROLE ana LOGIN")
    admin.close()
    c = servidor.conectar(banco_servidor)
    c.execute("CREATE SCHEMA fechado; CREATE TABLE fechado.a (id int); ALTER TABLE fechado.a OWNER TO ana")
    m = catalogo.modelo(c, "fechado")
    papeis, tabelas = _ids(m)
    r = rascunho.Rascunho(m)
    avisos = checagem.avisos_da_operacao(rascunho.Conceder((papeis["leitura"], papeis["ana"]), (tabelas["a"],), ("S",)),
                                         r, checagem.Consulta(c))
    assert sorted((a.nivel, a.chave, a.alvo) for a in avisos) == sorted([
        ("atencao", "chk.perm_sem_usage", papeis["ana"]), ("atencao", "chk.perm_sem_usage", papeis["leitura"]),
        ("info", "chk.perm_dono", papeis["ana"])])
    assert all(a.oferta == rascunho.PermissaoNoSchema(a.alvo, True, None) for a in avisos if a.nivel == "atencao")
    c.close()


def test_papel_removido_no_servidor_invalida_a_operacao_no_rebase(servidor, banco_servidor):
    from ratt.db import rascunho
    admin = servidor.conectar()
    admin.execute("CREATE ROLE leitura NOLOGIN")
    admin.execute("CREATE ROLE ana NOLOGIN")
    c = servidor.conectar(banco_servidor)
    c.execute("CREATE TABLE public.a (id int)")
    antes = catalogo.assinatura(c, escritas=False)
    m = catalogo.modelo(c, "public")
    papeis, tabelas = _ids(m)
    r = rascunho.Rascunho(m)
    r.acrescentar(rascunho.Conceder((papeis["leitura"],), (tabelas["a"],), ("S",)))
    r.acrescentar(rascunho.Conceder((papeis["ana"],), (tabelas["a"],), ("S",)))
    admin.execute("DROP ROLE leitura")
    admin.execute("ALTER ROLE ana RENAME TO bia")
    assert catalogo.assinatura(c, escritas=False) != antes             # a tela relê o schema e faz o rebase
    assert r.rebase(catalogo.modelo(c, "public")) == {0, 1}
    admin.close()
    c.close()


# ── Task 6: a tela Usuários ──

def _entrar_no_descartavel(monkeypatch, servidor):
    """`acesso.entrar` vai SÓ ao servidor descartável (pelo socket da pasta dele): nada da tela toca o PostgreSQL
    da pessoa."""
    from ratt.db import acesso

    def entrar(porta, usuario, banco="postgres"):
        assert porta == servidor.porta
        try:
            return servidor.conectar(banco), "s.como_socket"
        except Exception:
            return None, None
    monkeypatch.setattr(acesso, "entrar", entrar)


def test_assinatura_do_servidor_muda_com_papel_pertenca_e_banco(servidor, banco_servidor):
    b1 = banco_servidor
    with servidor.conectar(b1) as c:
        a0 = catalogo_servidor.assinatura(c)
        assert catalogo_servidor.assinatura(c) == a0
        with servidor.conectar() as admin:
            admin.execute("CREATE ROLE ratt_sig_a LOGIN")
        a1 = catalogo_servidor.assinatura(c)
        with servidor.conectar() as admin:
            admin.execute("CREATE ROLE ratt_sig_g; GRANT ratt_sig_g TO ratt_sig_a")
        a2 = catalogo_servidor.assinatura(c)
        with servidor.conectar() as admin:
            admin.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(b1)))
        a3 = catalogo_servidor.assinatura(c)
        assert len({a0, a1, a2, a3}) == 4


def test_assinatura_do_banco_sem_papeis_nao_muda_com_um_papel_novo(servidor, banco_servidor):
    """A tela do `ratt db` não relê nem avisa "o banco mudou" quando só os papéis mudam (o canvas e a tela Dados
    continuam vendo: um papel renomeado invalida permissões no rascunho do schema)."""
    with servidor.conectar(banco_servidor) as c:
        sem, com = catalogo.assinatura(c, papeis=False), catalogo.assinatura(c)
        with servidor.conectar() as admin:
            admin.execute("CREATE ROLE ratt_sig_b")
        assert catalogo.assinatura(c, papeis=False) == sem and catalogo.assinatura(c) != com


def test_u_na_tela_do_banco_abre_usuarios_e_papel_novo_nao_avisa(servidor, banco_servidor, monkeypatch, tmp_path):
    import asyncio
    from conftest import esperar_que, esperar_workers
    from ratt.db import tela_usuarios
    from ratt.db.tela import TelaBanco
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "dados"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    _entrar_no_descartavel(monkeypatch, servidor)
    with servidor.conectar() as admin:
        admin.execute("CREATE ROLE ratt_u_ana LOGIN")

    async def roteiro():
        app = TelaBanco(servidor.porta, servidor.usuario, banco_servidor)
        async with app.run_test(size=(140, 44)) as piloto:
            await esperar_que(piloto, lambda: app.marca is not None)
            await esperar_workers(app)
            marca = app.marca
            with servidor.conectar() as admin:
                admin.execute("CREATE ROLE ratt_u_bruno")
            app.vigiar()
            await esperar_workers(app)
            assert app.marca == marca                   # um papel novo não é "o banco mudou"
            await piloto.press("u")
            await esperar_que(piloto, lambda: isinstance(app.screen, tela_usuarios.TelaUsuarios) and app.screen.carregado)
            tela = app.screen
            await esperar_workers(app)
            nomes = [tela.linha_id(i) for i in range(tela.query_one("#usu_grade").row_count)]
            m = tela.sessao.rascunho.base
            assert m.papel_por_nome("ratt_u_ana").id in nomes and m.papel_por_nome("ratt_u_bruno").id in nomes
            assert nomes[0] == "PUBLIC"
            await piloto.press("u")                     # dentro da tela, u é desfazer: não abre outra
            await piloto.pause()
            assert sum(isinstance(s, tela_usuarios.TelaUsuarios) for s in app.screen_stack) == 1
            await piloto.press("escape")
            await esperar_que(piloto, lambda: app.screen is app.screen_stack[0])
    asyncio.run(roteiro())


def test_tela_usuarios_cria_ana_e_aplica_pela_tela(servidor, banco_servidor, monkeypatch, tmp_path):
    """Pela tela: a cria ana no formulário, Enter abre a confirmação, sim aplica (a parte suspensa trocada por uma
    sem terminal, com a senha falsa); ana existe no servidor e a grade passa a mostrá-la como papel de verdade."""
    import asyncio
    from contextlib import contextmanager
    from textual.app import App
    from conftest import esperar_que, esperar_tela, esperar_workers, saida_presa
    from ratt.db import formularios, formularios_usuarios as fu, painel, tela_usuarios
    b1 = banco_servidor
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "dados"))
    _entrar_no_descartavel(monkeypatch, servidor)
    pedidas = []

    def sessao_sem_terminal(self, conectar, ensaio, cabecalho, confirmado):
        def senha(nome):
            pedidas.append(nome)
            return SENHA
        return painel.aplicar_servidor(self.sessao, conectar, ensaio, senha, lambda p: False,
                                       confirmado=confirmado, cabecalho=cabecalho)

    @contextmanager
    def sem_suspender(self):
        yield

    monkeypatch.setattr(App, "suspend", sem_suspender)
    monkeypatch.setattr(tela_usuarios.TelaUsuarios, "_sessao_servidor", sessao_sem_terminal)

    class AppTeste(App):
        def on_mount(self):
            tela = tela_usuarios.TelaUsuarios(tela_usuarios.FonteServidor(servidor.porta, servidor.usuario, b1))
            tela.VIGIA_SEGUNDOS = 0
            self.push_screen(tela)

    async def roteiro():
        app = AppTeste()
        async with app.run_test(size=(140, 44)) as piloto:
            await esperar_que(piloto, lambda: isinstance(app.screen, tela_usuarios.TelaUsuarios) and app.screen.carregado)
            tela = app.screen
            await esperar_workers(app)
            await piloto.press("a")
            await esperar_que(piloto, lambda: isinstance(app.screen, fu.FormNovoUsuario))
            await esperar_tela(piloto, app.screen)
            for tecla in "ana":
                await piloto.press(tecla)
            await esperar_workers(app)
            await piloto.click("#por_no_rascunho")
            await esperar_que(piloto, lambda: app.screen is tela)
            plano = tela.query_one(painel.PainelSQL).texto_plano()
            assert "CREATE ROLE ana LOGIN;" in plano and "PASSWORD" not in plano
            await piloto.press("enter")
            await esperar_que(piloto, lambda: isinstance(app.screen, formularios.Confirmar))
            await esperar_tela(piloto, app.screen)
            with saida_presa():
                await piloto.click("#sim")
                await esperar_que(piloto, lambda: not tela.sessao.aplicando and tela.sessao.rascunho.operacoes == []
                                  and tela.sessao.rascunho.base.papel_por_nome("ana") is not None, limite=10)
            await esperar_workers(app)
            ana = tela.sessao.rascunho.base.papel_por_nome("ana")
            assert ana.id.startswith("r")
            i = [tela.linha_id(k) for k in range(tela.query_one("#usu_grade").row_count)].index(ana.id)
            rotulo = tela.query_one("#usu_grade").get_row_at(i)[0].plain
            assert "ana" in rotulo and not rotulo.startswith("+")
            assert pedidas == ["ana"]
    asyncio.run(roteiro())
    with servidor.conectar() as c:
        assert c.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'ana'").fetchone() == (True,)
    _sem_segredo_no_disco(tmp_path / "dados")


def test_sql_exportado_do_servidor_roda_inteiro_num_psql_so(servidor, banco_servidor, tmp_path):
    """O `.sql` exportado roda como está, com um `psql -f`: os `\\connect` trocam de banco (nomes hostis, um com
    `=`), sem senha nenhuma no arquivo."""
    import shutil
    import subprocess
    import pytest
    from conftest import _binario_pg
    from ratt.db.rascunho_servidor import CriarPapel, RascunhoServidor, RemoverPapeis
    psql = _binario_pg("psql")
    if not psql:
        pytest.skip("sem psql instalado")
    b1 = banco_servidor
    hostis = ["z ;--'", "a=b \\x"]
    with servidor.conectar() as admin:
        for nome in hostis:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nome)))
        admin.execute("CREATE ROLE bruno LOGIN")
    for nome in hostis + [b1]:
        _objetos_em(servidor, nome, "bruno")
    with servidor.conectar(b1) as c:
        m = catalogo_servidor.modelo_servidor(c)
    r = RascunhoServidor(m)
    n = r.novo_id("p")
    r.acrescentar(CriarPapel(_servidor.Papel(n, "ana", True, False, False, False)))
    bancos = tuple(b.nome for b in m.bancos.values() if b.aceita_conexao)
    r.acrescentar(RemoverPapeis((m.papel_por_nome("bruno").id,), m.eu, bancos))
    texto = r.sql_psql("-- teste", b1)
    assert "PASSWORD" not in texto
    arquivo = tmp_path / "servidor.sql"
    arquivo.write_text(texto, encoding="utf-8")
    p = subprocess.run([psql, "-X", "-q", "-h", servidor.socket_dir, "-p", str(servidor.porta), "-U", servidor.usuario,
                        "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-f", str(arquivo)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    with servidor.conectar() as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('ana', 'bruno')").fetchone()[0] == 1
        assert c.execute("SELECT rolname FROM pg_roles WHERE rolname = 'ana'").fetchone() == ("ana",)
    for nome in hostis + [b1]:
        assert _donos(servidor, nome) == {"t_bruno": servidor.usuario}
