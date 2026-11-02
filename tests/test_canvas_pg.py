"""Testes do canvas com PostgreSQL de verdade; pulam sozinhos sem servidor ou sem permissão de criar banco."""
import getpass
import os

import pytest

from ratt.db import acesso, canvas, catalogo, modelo, rascunho, rodar

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
    depois = catalogo.assinatura(banco)
    assert antes != meio != depois and antes != depois


def test_assinatura_ve_indices(banco):
    banco.execute(PNG_SQL)
    antes = catalogo.assinatura(banco)
    banco.execute("CREATE INDEX ON profiles (bio)")
    assert catalogo.assinatura(banco) != antes


def test_sql_do_rascunho_roda_no_banco(banco):
    """O SQL gerado pelo rascunho é aceito pelo PostgreSQL de verdade, inclusive com nomes hostis."""
    banco.execute(PNG_SQL)
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    users = r.base.por_nome("users")
    r.acrescentar(rascunho.Renomear(users.id, None, "user"))
    r.acrescentar(rascunho.AdicionarColuna(users.id, modelo.Coluna(r.novo_id("c"), "Data Nasc", "date")))
    banco.execute(r.sql_texto())
    assert catalogo.modelo(banco, "public").por_nome("user").colunas[-1].nome == "Data Nasc"


def test_modelo_regras_do_catalogo(banco):
    banco.execute("""
        CREATE TABLE pai (id int PRIMARY KEY, a int, b int, c int, UNIQUE (a, b), CONSTRAINT pai_c_key UNIQUE (c));
        CREATE TABLE filho (id int CONSTRAINT filho_pkey_nome PRIMARY KEY, a int, b int, p1 int, p2 int, u int, x int,
            CONSTRAINT fk_casc FOREIGN KEY (p1) REFERENCES pai (id) ON DELETE CASCADE,
            CONSTRAINT fk_nulo FOREIGN KEY (p2) REFERENCES pai (id) ON DELETE SET NULL,
            CONSTRAINT fk_comp FOREIGN KEY (b, a) REFERENCES pai (b, a));
        CREATE UNIQUE INDEX filho_u_idx ON filho (u);
        CREATE INDEX filho_x_idx ON filho (x, a);
        CREATE TABLE medida (dia date, v int) PARTITION BY RANGE (dia);
        CREATE TABLE medida_1 PARTITION OF medida FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
        CREATE TABLE medida_2 PARTITION OF medida FOR VALUES FROM ('2021-01-01') TO ('2022-01-01');
        CREATE SCHEMA outro;
        CREATE TABLE outro.t (pid int REFERENCES public.pai (id));
        ANALYZE pai;""")
    m = catalogo.modelo(banco, "public")
    assert sorted(t.nome for t in m.tabelas.values()) == ["filho", "medida", "pai"]
    assert m.por_nome("medida").particoes == 2 and m.por_nome("pai").particoes == 0
    assert m.por_nome("pai").linhas == 0 and m.por_nome("filho").linhas is None
    pai, filho = m.por_nome("pai"), m.por_nome("filho")
    assert filho.nome_pk == "filho_pkey_nome" and pai.nome_pk == "pai_pkey"
    assert len(m.ligacoes) == 3
    por_nome = {l.nome: l for l in m.ligacoes.values()}
    assert por_nome["fk_casc"].ao_apagar == "CASCADE" and por_nome["fk_nulo"].ao_apagar == "SET NULL"
    comp = por_nome["fk_comp"]
    nome = lambda cid: next(c.nome for t in m.tabelas.values() for c in t.colunas if c.id == cid)
    assert [nome(c) for c in comp.colunas] == ["b", "a"]
    assert [nome(c) for c in comp.colunas_destino] == ["b", "a"]
    assert comp.ao_apagar == "NO ACTION"
    pa = {c.nome: c for c in pai.colunas}
    assert not pa["a"].unica and not pa["b"].unica
    assert pa["c"].unica and pa["c"].nome_unica == "pai_c_key" and not pa["id"].unica
    fc = {c.nome: c for c in filho.colunas}
    assert fc["u"].unica and fc["u"].nome_unica is None and fc["u"].indexada
    assert fc["x"].indexada and not fc["x"].unica and not fc["a"].indexada
    assert not fc["p1"].indexada and fc["id"].indexada
    assert catalogo.modelo(banco, "outro").ligacoes == {}


def test_indice_unico_da_cardinalidade_um_para_um(banco):
    banco.execute("""CREATE TABLE a (id int PRIMARY KEY);
                     CREATE TABLE b (id int PRIMARY KEY, a_id int REFERENCES a (id));
                     CREATE UNIQUE INDEX ON b (a_id);""")
    m = catalogo.modelo(banco, "public")
    (lig,) = m.ligacoes.values()
    assert modelo.cardinalidade(m, lig) == "1:1"


# ── Task 5: checagens só de leitura ──

from ratt.db import checagem


def test_consulta_real(banco):
    banco.execute(PNG_SQL)
    banco.execute("INSERT INTO users (email) VALUES ('a'), ('a'), ('b')")
    banco.execute("INSERT INTO profiles (user_id) VALUES (1)")
    c = checagem.Consulta(banco)
    assert c.linhas("public", "users") == 3 and c.nulos("public", "users", "first_name") == 3
    assert c.repetidos("public", "users", "email") == 1
    assert c.orfaos("public", "profiles", "user_id", "public", "users", "id") == 0
    oid = banco.execute("SELECT 'users'::regclass::oid").fetchone()[0]
    assert any(d.tipo == "fk" and d.nome == "profiles" for d in c.dependentes(oid))


def test_consulta_dependentes_de_coluna_e_view(banco):
    banco.execute(PNG_SQL)
    banco.execute("CREATE VIEW v_x AS SELECT email FROM users")
    c = checagem.Consulta(banco)
    oid = banco.execute("SELECT 'users'::regclass::oid").fetchone()[0]
    assert any(d.tipo == "view" and d.nome == "v_x" for d in c.dependentes(oid))
    attnum = banco.execute("SELECT attnum FROM pg_attribute WHERE attrelid = %s AND attname = 'email'",
                           (oid,)).fetchone()[0]
    assert [(d.tipo, d.nome) for d in c.dependentes(oid, attnum)] == [("view", "v_x")]
    attnum = banco.execute("SELECT attnum FROM pg_attribute WHERE attrelid = %s AND attname = 'id'",
                           (oid,)).fetchone()[0]
    assert any(d.tipo == "fk" and d.nome_restricao for d in c.dependentes(oid, attnum))


def test_consulta_orfaos_e_nome_com_aspas(banco):
    banco.execute('CREATE TABLE "Pai" (id int)')
    banco.execute('CREATE TABLE "order" (p int)')
    banco.execute('INSERT INTO "order" VALUES (1), (2), (NULL)')
    banco.execute('INSERT INTO "Pai" VALUES (1)')
    assert checagem.Consulta(banco).orfaos("public", "order", "p", "public", "Pai", "id") == 1


def test_consulta_respeita_tempo_limite(banco):
    banco.execute("CREATE TABLE grande AS SELECT g AS x FROM generate_series(1, 3000000) g")
    assert checagem.Consulta(banco, limite_ms=1).repetidos("public", "grande", "x") is None
    assert checagem.Consulta(banco).linhas("public", "grande") == 3000000      # a conexão segue de pé


def test_aplicar_rascunho(banco, tmp_path):
    import psycopg
    banco.execute(PNG_SQL)
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    users = r.base.por_nome("users")
    r.acrescentar(rascunho.AdicionarColuna(users.id, modelo.Coluna(r.novo_id("c"), "birth", "date")))
    con2 = psycopg.connect(banco.info.dsn)
    assert canvas.aplicar_rascunho(r, con2, tmp_path / "a.sql", ensaio=True, cabecalho="") == (0, None)
    assert "birth" not in [c.nome for c in catalogo.modelo(banco, "public").por_nome("users").colunas]
    assert canvas.aplicar_rascunho(r, con2, tmp_path / "a.sql", ensaio=False, cabecalho="") == (0, None)
    con2.close()
    assert "birth" in [c.nome for c in catalogo.modelo(banco, "public").por_nome("users").colunas]


def test_aplicar_aponta_a_operacao_que_falhou(banco, tmp_path):
    import psycopg
    banco.execute(PNG_SQL)
    banco.execute("INSERT INTO users (email) VALUES ('a')")
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    users = r.base.por_nome("users")
    r.acrescentar(rascunho.Renomear(users.id, None, "pessoas"))
    r.acrescentar(rascunho.Obrigatoria(users.id, users.colunas[1].id, True))   # first_name tem NULL: falha
    con2 = psycopg.connect(banco.info.dsn)
    codigo, indice = canvas.aplicar_rascunho(r, con2, tmp_path / "a.sql", False, "")
    con2.close()
    assert (codigo, indice) == (1, 1)
    assert catalogo.modelo(banco, "public").por_nome("users") is not None            # tudo desfeito


def test_aplicar_aponta_a_operacao_com_cabecalho(banco, tmp_path):
    import psycopg
    banco.execute(PNG_SQL)
    banco.execute("INSERT INTO users (email) VALUES ('a')")
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    users = r.base.por_nome("users")
    r.acrescentar(rascunho.Renomear(users.id, None, "pessoas"))
    r.acrescentar(rascunho.Obrigatoria(users.id, users.colunas[1].id, True))
    con2 = psycopg.connect(banco.info.dsn)
    assert canvas.aplicar_rascunho(r, con2, tmp_path / "a.sql", False, "ratt db · x") == (1, 1)
    con2.close()


def test_executar_nao_acumula_tratador_de_avisos(banco, tmp_path):
    import psycopg
    arquivo = tmp_path / "n.sql"
    arquivo.write_text("DO $$ BEGIN RAISE NOTICE 'oi'; END $$;")
    con = psycopg.connect(banco.info.dsn)
    rodar.executar(arquivo, con); rodar.executar(arquivo, con)
    assert len(con._notice_handlers) == 0
    con.close()


def test_sql_do_public_nao_segue_o_search_path(banco, tmp_path):
    """Com um schema do mesmo nome do usuário ("$user" vem antes de public no search_path), o DROP pensado para
    public.users não pode apagar <usuário>.users, e a tabela nova tem de nascer no public."""
    import psycopg
    from psycopg import sql
    banco.execute(PNG_SQL)
    usuario = banco.execute("SELECT current_user").fetchone()[0]
    banco.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(usuario)))
    banco.execute(sql.SQL("CREATE TABLE {}.users (x int)").format(sql.Identifier(usuario)))
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    users = r.base.por_nome("users")
    r.acrescentar(rascunho.ApagarTabela(users.id, cascata=True))
    tid = r.novo_id("t")
    r.acrescentar(rascunho.CriarTabela(modelo.Tabela(tid, "public", "novidade", [
        modelo.Coluna(f"{tid}.c1", "id", "bigint", obrigatoria=True, identidade=True, pk=True)])))
    con2 = psycopg.connect(banco.info.dsn)
    assert canvas.aplicar_rascunho(r, con2, tmp_path / "a.sql", False, "") == (0, None)
    con2.close()
    assert catalogo.modelo(banco, "public").por_nome("users") is None
    assert catalogo.modelo(banco, usuario).por_nome("users") is not None
    assert catalogo.modelo(banco, "public").por_nome("novidade") is not None
    assert catalogo.modelo(banco, usuario).por_nome("novidade") is None


def test_assinatura_sem_escritas_nao_muda_com_insert(banco):
    import time
    banco.execute(PNG_SQL)
    antes = catalogo.assinatura(banco, escritas=False)
    banco.execute("INSERT INTO users (email) VALUES ('a'), ('b')")
    time.sleep(0.7)             # as estatísticas de escrita chegam ao pg_stat com algum atraso
    banco.execute("SELECT pg_stat_clear_snapshot()")
    assert catalogo.assinatura(banco, escritas=False) == antes
    banco.execute("ALTER TABLE users ADD COLUMN x int")
    assert catalogo.assinatura(banco, escritas=False) != antes


def test_ligar_com_coluna_nova_de_pk_com_dominio(banco):
    """A PK de tipo próprio (domínio): a coluna nova nasce do mesmo tipo e o SQL do rascunho roda no banco."""
    banco.execute("""
        CREATE DOMAIN email_t AS text;
        CREATE TABLE contas (email email_t PRIMARY KEY);
        CREATE TABLE avisos (id int PRIMARY KEY);
    """)
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    contas, avisos = r.base.por_nome("contas"), r.base.por_nome("avisos")
    tipo = contas.colunas[0].tipo
    col = modelo.Coluna(r.novo_id("c"), "conta_email", tipo)
    lig = modelo.Ligacao(r.novo_id("k"), avisos.id, (col.id,), contas.id, (contas.colunas[0].id,), "")
    r.acrescentar(rascunho.Ligar(lig, nova_coluna=col))
    r.acrescentar(rascunho.LigarNN(r.novo_id("t"), "contas_avisos", contas.id, avisos.id,
                                   (r.novo_id("c"), r.novo_id("c")), (r.novo_id("k"), r.novo_id("k"))))
    banco.execute(r.sql_texto())
    m = catalogo.modelo(banco, "public")
    assert m.por_nome("avisos").colunas[-1].nome == "conta_email" and m.por_nome("avisos").colunas[-1].tipo == tipo
    assert len(m.ligacoes) == 3
