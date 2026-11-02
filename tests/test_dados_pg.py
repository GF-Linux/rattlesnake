"""As operações de dados do rascunho contra um PostgreSQL de verdade (o fixture `banco` cria um banco descartável)."""
from conftest import PESSOAS_SQL
import psycopg
import pytest

from ratt.db import catalogo, checagem, filtro, rascunho
from ratt.db.idioma import t
from test_dados import modelo_pessoas


def preparar(banco):
    banco.execute(PESSOAS_SQL)
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    tab = r.base.por_nome("pessoas")
    return r, tab.id, {c.nome: c.id for c in tab.colunas}


def nomes(banco):
    return [x[0] for x in banco.execute("SELECT nome FROM public.pessoas ORDER BY id")]


def test_inserir_com_valores_dificeis_e_so_com_nome(banco):
    r, t, c = preparar(banco)
    r.acrescentar(rascunho.Inserir(t, {c["nome"]: "O'Brien", c["idade"]: "51", c["email"]: None}))
    r.acrescentar(rascunho.Inserir(t, {c["nome"]: "50%"}))
    r.acrescentar(rascunho.Inserir(t, {c["nome"]: "C:\\x"}))
    r.acrescentar(rascunho.Inserir(t, {c["nome"]: "ação_é"}))
    banco.execute(r.sql_texto())
    assert nomes(banco)[-4:] == ["O'Brien", "50%", "C:\\x", "ação_é"]
    ultimo = banco.execute("SELECT idade, email, ativo FROM public.pessoas WHERE nome = 'ação_é'").fetchone()
    assert ultimo == (None, None, True)                       # o banco preencheu o padrão de `ativo`
    assert banco.execute("SELECT idade FROM public.pessoas WHERE id = 6").fetchone() == (51,)


def test_inserir_sem_valores_usa_default_values(banco):
    banco.execute("CREATE TABLE public.livre (a int DEFAULT 7, b text)")
    m = catalogo.modelo(banco, "public")
    r = rascunho.Rascunho(m)
    r.acrescentar(rascunho.Inserir(m.por_nome("livre").id, {}))
    banco.execute(r.sql_texto())
    assert banco.execute("SELECT a, b FROM public.livre").fetchall() == [(7, None)]


def test_barra_invertida_com_standard_conforming_strings_desligado(banco):
    r, t, c = preparar(banco)
    r.acrescentar(rascunho.Inserir(t, {c["nome"]: "C:\\x"}))
    r.acrescentar(rascunho.Apagar(t, filtro.Filtro([filtro.Condicao(c["nome"], "contem", ["C:\\x"])])))
    banco.execute("SET standard_conforming_strings = off")
    banco.execute(r.sql_texto().split("DELETE")[0])
    assert "C:\\x" in nomes(banco)
    banco.execute("DELETE" + r.sql_texto().split("DELETE")[1])
    assert nomes(banco) == ["Gabriel", "Ana", "Giovana", " Gustavo", "O'Brien"]      # só ela saiu
    banco.execute("RESET standard_conforming_strings")


def test_alterar_limpeza_so_toca_quem_muda(banco):
    r, t, c = preparar(banco)
    banco.execute("UPDATE public.pessoas SET email = ' GAB@X.com ' WHERE id = 1")
    f = filtro.Filtro([filtro.Condicao(c["email"], "preenchido")])
    r.acrescentar(rascunho.Alterar(t, f, {c["email"]: rascunho.Mudanca("trim"), c["nome"]: rascunho.Mudanca("trim")}))
    r.acrescentar(rascunho.Alterar(t, f, {c["email"]: rascunho.Mudanca("minusculas")}))
    banco.execute(r.sql_texto())
    assert banco.execute("SELECT email FROM public.pessoas WHERE id = 1").fetchone() == ("gab@x.com",)
    assert nomes(banco)[3] == "Gustavo"
    xmin = banco.execute("SELECT xmin::text FROM public.pessoas ORDER BY id").fetchall()
    banco.execute(r.sql_texto())                              # segunda vez: nada a mudar, nenhuma linha reescrita
    assert banco.execute("SELECT xmin::text FROM public.pessoas ORDER BY id").fetchall() == xmin


def test_alterar_com_filtro_ou_e_limpeza_respeita_a_precedencia(banco):
    r, t, c = preparar(banco)
    banco.execute("UPDATE public.pessoas SET nome = nome || '  '")      # todos com espaço sobrando
    f = filtro.Filtro([filtro.Condicao(c["nome"], "comeca", ["Gab"]), filtro.Condicao(c["idade"], "igual", ["29"])],
                      conectivo="OU")
    r.acrescentar(rascunho.Alterar(t, f, {c["nome"]: rascunho.Mudanca("trim")}))
    banco.execute(r.sql_texto())
    limpos = [n for n in nomes(banco) if n == n.strip()]
    assert sorted(limpos) == ["Ana", "Gabriel"]               # só as duas do filtro; as outras seguem com espaço


def test_alterar_valor_coluna_vazio_e_branco_vazio(banco):
    r, t, c = preparar(banco)
    banco.execute("UPDATE public.pessoas SET email = '   ' WHERE id = 3")
    r.acrescentar(rascunho.Alterar(t, filtro.Filtro([filtro.Condicao(c["nome"], "igual", ["O'Brien"])]),
                                   {c["nome"]: rascunho.Mudanca("valor", "50%"), c["idade"]: rascunho.Mudanca("valor", "1"),
                                    c["email"]: rascunho.Mudanca("vazio")}))
    r.acrescentar(rascunho.Alterar(t, filtro.Filtro(), {c["email"]: rascunho.Mudanca("branco_vazio")}))
    r.acrescentar(rascunho.Alterar(t, filtro.Filtro([filtro.Condicao(c["nome"], "igual", ["Ana"])]),
                                   {c["nome"]: rascunho.Mudanca("coluna", coluna=c["email"])}))
    banco.execute(r.sql_texto())
    assert banco.execute("SELECT nome, idade, email FROM public.pessoas WHERE id = 5").fetchone() == ("50%", 1, None)
    assert banco.execute("SELECT email FROM public.pessoas WHERE id = 3").fetchone() == (None,)
    assert banco.execute("SELECT nome FROM public.pessoas WHERE id = 2").fetchone() == ("ana@x.com",)


def test_apagar_com_e_sem_filtro_e_cascata(banco):
    r, t, c = preparar(banco)
    r.acrescentar(rascunho.Apagar(t, filtro.Filtro([filtro.Condicao(c["nome"], "comeca", ["G"])]), vista=2))
    banco.execute(r.sql_texto())
    assert nomes(banco) == ["Ana", " Gustavo", "O'Brien"]
    assert banco.execute("SELECT count(*) FROM public.pedidos").fetchone() == (1,)   # os de Gabriel caíram em cascata
    r.acrescentar(rascunho.Apagar(t, filtro.Filtro()))
    banco.execute(r.sql_texto())
    assert nomes(banco) == []


def test_apagar_com_cinquenta_por_cento_e_underscore(banco):
    r, t, c = preparar(banco)
    banco.execute("INSERT INTO public.pessoas (nome) VALUES ('50% off'), ('500 off'), ('a_b'), ('axb')")
    r.acrescentar(rascunho.Apagar(t, filtro.Filtro([filtro.Condicao(c["nome"], "contem", ["50%"]),
                                                    filtro.Condicao(c["nome"], "contem", ["a_b"])], conectivo="OU")))
    banco.execute(r.sql_texto())
    resto = nomes(banco)
    assert "500 off" in resto and "axb" in resto and "50% off" not in resto and "a_b" not in resto


def test_duplicatas_com_chave_primaria(banco):
    r, t, c = preparar(banco)
    banco.execute("INSERT INTO public.pessoas (nome, email) VALUES ('Gab2', ' GAB@x.com'), ('Gab3', 'gab@x.com ')")
    r.acrescentar(rascunho.ApagarDuplicatas(t, filtro.Filtro([filtro.Condicao(c["email"], "preenchido")]),
                                            (c["email"],), normalizar=True))
    banco.execute(r.sql_texto())
    assert nomes(banco) == ["Gabriel", "Ana", "Giovana", " Gustavo", "O'Brien"]   # ficou a de menor id de cada grupo


def test_duplicatas_maior_e_recente_com_n(banco):
    r, t, c = preparar(banco)
    banco.execute("INSERT INTO public.pessoas (nome, nascimento) VALUES "
                  "('Rep', '2000-01-01'), ('Rep', '2020-01-01'), ('Rep', NULL), ('Rep', '2010-01-01')")
    r.acrescentar(rascunho.ApagarDuplicatas(t, filtro.Filtro([filtro.Condicao(c["nome"], "igual", ["Rep"])]),
                                            (c["nome"],), manter="recente", ordem=c["nascimento"], n=2))
    banco.execute(r.sql_texto())
    assert banco.execute("SELECT nascimento::text FROM public.pessoas WHERE nome = 'Rep' ORDER BY id").fetchall() == \
        [("2020-01-01",), ("2010-01-01",)]
    r2 = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    r2.acrescentar(rascunho.ApagarDuplicatas(t, filtro.Filtro([filtro.Condicao(c["nome"], "igual", ["Rep"])]),
                                             (c["nome"],), manter="maior"))
    banco.execute(r2.sql_texto())
    assert banco.execute("SELECT nascimento::text FROM public.pessoas WHERE nome = 'Rep'").fetchall() == [("2010-01-01",)]


def test_duplicatas_em_tabela_sem_chave_primaria_via_ctid(banco):
    banco.execute("CREATE TABLE public.log (quem text, quando int)")
    banco.execute("INSERT INTO public.log VALUES ('a', 1), ('a', 1), ('a', 2), ('b', 1), ('b', 1), ('b', 1)")
    m = catalogo.modelo(banco, "public")
    tab = m.por_nome("log")
    r = rascunho.Rascunho(m)
    r.acrescentar(rascunho.ApagarDuplicatas(tab.id, filtro.Filtro(), tuple(c.id for c in tab.colunas)))
    assert "ctid" in r.sql_texto()
    banco.execute(r.sql_texto())
    assert banco.execute("SELECT quem, quando FROM public.log ORDER BY 1, 2").fetchall() == [("a", 1), ("a", 2), ("b", 1)]


def test_o_rascunho_inteiro_roda_em_ordem(banco):
    r, t, c = preparar(banco)
    r.acrescentar(rascunho.Renomear(t, c["email"], "e_mail"))
    r.acrescentar(rascunho.Inserir(t, {c["nome"]: "Zé", c["email"]: "ze@x.com"}))
    r.acrescentar(rascunho.Apagar(t, filtro.Filtro([filtro.Condicao(c["email"], "vazio")])))
    banco.execute(r.sql_texto())
    assert nomes(banco) == ["Gabriel", "Ana", " Gustavo", "O'Brien", "Zé"]


def test_alterar_misto_atualiza_quem_muda_qualquer_coluna(banco):
    r, t, c = preparar(banco)
    banco.execute("UPDATE public.pessoas SET idade = 99 WHERE id IN (1, 2, 3)")
    banco.execute("UPDATE public.pessoas SET nome = nome || ' ' WHERE id = 1")
    r.acrescentar(rascunho.Alterar(t, filtro.Filtro(), {c["idade"]: rascunho.Mudanca("valor", "99"),
                                                        c["nome"]: rascunho.Mudanca("trim")}))
    cur = banco.execute(r.sql_texto())
    assert cur.rowcount == 3                                  # 1 (nome com espaço), 4 e 5 (idade diferente de 99)
    assert banco.execute("SELECT count(*) FROM public.pessoas WHERE idade IS DISTINCT FROM 99").fetchone() == (0,)
    assert nomes(banco)[0] == "Gabriel"


def test_branco_vazio_nao_apara_o_que_nao_e_branco(banco):
    r, t, c = preparar(banco)
    banco.execute("UPDATE public.pessoas SET email = '  ' WHERE id = 3")
    banco.execute("UPDATE public.pessoas SET email = ' ab@x ' WHERE id = 1")
    r.acrescentar(rascunho.Alterar(t, filtro.Filtro(), {c["email"]: rascunho.Mudanca("branco_vazio")}))
    banco.execute(r.sql_texto())
    assert banco.execute("SELECT email FROM public.pessoas WHERE id IN (1, 3) ORDER BY id").fetchall() == [(" ab@x ",), (None,)]


def test_duplicatas_nao_tratam_vazio_como_igual(banco):
    r, t, c = preparar(banco)
    banco.execute("TRUNCATE public.pessoas CASCADE")
    banco.execute("INSERT INTO public.pessoas (nome, email) VALUES ('n1', NULL), ('n2', NULL), ('v1', ''), "
                  "('v2', '  '), ('a1', 'a@x'), ('a2', ' A@X')")
    r.acrescentar(rascunho.ApagarDuplicatas(t, filtro.Filtro(), (c["email"],), normalizar=True))
    banco.execute(r.sql_texto())
    assert nomes(banco) == ["n1", "n2", "v1", "v2", "a1"]
    r2 = rascunho.Rascunho(catalogo.modelo(banco, "public"))       # sem normalizar também deixa os NULL em paz
    r2.acrescentar(rascunho.ApagarDuplicatas(t, filtro.Filtro(), (c["email"],)))
    banco.execute(r2.sql_texto())
    assert nomes(banco) == ["n1", "n2", "v1", "v2", "a1"]


def test_duplicatas_particionada_sem_chave_so_apaga_dentro_do_filtro(banco):
    banco.execute("CREATE TABLE public.ev (regiao text, k int) PARTITION BY LIST (regiao)")
    banco.execute("CREATE TABLE public.ev_n PARTITION OF public.ev FOR VALUES IN ('n')")
    banco.execute("CREATE TABLE public.ev_s PARTITION OF public.ev FOR VALUES IN ('s')")
    banco.execute("INSERT INTO public.ev VALUES ('n', 1), ('n', 1), ('s', 7), ('s', 8)")
    m = catalogo.modelo(banco, "public")
    tab = m.por_nome("ev")
    cid = {x.nome: x.id for x in tab.colunas}
    r = rascunho.Rascunho(m)
    r.acrescentar(rascunho.ApagarDuplicatas(tab.id, filtro.Filtro([filtro.Condicao(cid["regiao"], "igual", ["n"])]),
                                            (cid["k"],)))
    assert banco.execute(r.sql_texto()).rowcount == 1
    assert banco.execute("SELECT regiao, k FROM public.ev ORDER BY 1, 2").fetchall() == [("n", 1), ("s", 7), ("s", 8)]


def test_duplicatas_com_chave_chamada_rn(banco):
    banco.execute("CREATE TABLE public.q (rn int PRIMARY KEY, v text)")
    banco.execute("INSERT INTO public.q VALUES (1, 'a'), (2, 'a'), (3, 'b')")
    m = catalogo.modelo(banco, "public")
    tab = m.por_nome("q")
    r = rascunho.Rascunho(m)
    r.acrescentar(rascunho.ApagarDuplicatas(tab.id, filtro.Filtro(), (tab.colunas[1].id,)))
    banco.execute(r.sql_texto())
    assert banco.execute("SELECT rn FROM public.q ORDER BY 1").fetchall() == [(1,), (3,)]


# ── contagens, amostras e avisos: só leitura ──

def _chaves(avisos):
    return [(a.nivel, a.chave) for a in avisos]


def test_contagens_reais(banco):
    banco.execute(PESSOAS_SQL); c = checagem.Consulta(banco)
    m = catalogo.modelo(banco, "public")
    onde = filtro.where(filtro.Filtro([filtro.Condicao("t1.c2", "comeca", ["G"])]), modelo_pessoas(), "t1")
    assert c.contar_onde("public", "pessoas", onde) == 2              # ' Gustavo' não começa com G
    assert c.contar_onde("public", "pessoas", "") == 5
    assert c.cascata("public", "pessoas", "id = 1") == [("public.pedidos", "CASCADE", 2)]
    assert c.valor_valido("date", "2026-02-30") is False and c.valor_valido("date", "2026-02-28") is True
    c.forcar_cast = True; assert c.valor_valido("integer", "x") is False
    assert c.valor_valido("date", "2026-02-30") is False and c.valor_valido("integer", "12") is True
    assert c.ja_existe("public", "pessoas", "email", "'ana@x.com'") == 1


def test_valor_valido_tipo_ruim_nao_vira_sql(banco):
    banco.execute(PESSOAS_SQL)
    for forcar in (False, True):
        c = checagem.Consulta(banco)
        c.forcar_cast = forcar
        assert c.valor_valido("date); DELETE FROM public.pedidos; --", "1") is None and c.erro()
        assert c.valor_valido("tipo_que_nao_existe", "1") is None and c.erro()
    assert banco.execute("SELECT count(*) FROM public.pedidos").fetchone()[0] == 3


def test_consulta_nunca_escreve(banco):
    banco.execute(PESSOAS_SQL)
    assert checagem.Consulta(banco).contar_onde("public", "pessoas", "(SELECT count(*) FROM (DELETE FROM public.pedidos RETURNING 1) d) > 0") is None
    assert banco.execute("SELECT count(*) FROM public.pedidos").fetchone()[0] == 3


def test_funcao_que_escreve_no_where_e_barrada(banco):
    banco.execute(PESSOAS_SQL)
    banco.execute("CREATE FUNCTION apaga() RETURNS bigint LANGUAGE sql AS 'DELETE FROM public.pedidos RETURNING 1'")
    banco.execute("CREATE SEQUENCE seq")
    c = checagem.Consulta(banco)
    assert c.contar_onde("public", "pessoas", "apaga() > 0") is None
    assert "read-only" in c.erro() or "leitura" in c.erro()          # a mensagem vem no idioma do servidor
    assert c.contar_onde("public", "pessoas", "nextval('seq') > 0") is None
    assert c.amostra("public", "pessoas", "apaga() > 0", ["nome"]) is None
    assert banco.execute("SELECT count(*) FROM public.pedidos").fetchone()[0] == 3
    assert banco.execute("SELECT last_value, is_called FROM seq").fetchone() == (1, False)
    with pytest.raises(psycopg.Error):
        catalogo.pagina(banco, "public", "pessoas", "apaga() > 0", ["id"], 0, 10)
    with pytest.raises(psycopg.Error):
        catalogo.contar_filtrado(banco, "public", "pessoas", "apaga() > 0")
    assert banco.execute("SELECT count(*) FROM public.pedidos").fetchone()[0] == 3


def test_dentro_de_transacao_aberta_continua_so_leitura(banco):
    """Conexão sem autocommit, já no meio de uma transação: a checagem vira savepoint e continua somente leitura,
    e nada dela (nem o READ ONLY, nem o tempo limite) vaza para a transação de quem chamou."""
    banco.execute(PESSOAS_SQL)
    banco.execute("CREATE FUNCTION apaga() RETURNS bigint LANGUAGE sql AS 'DELETE FROM public.pedidos RETURNING 1'")
    con = psycopg.connect(banco.info.dsn)
    try:
        con.execute("INSERT INTO public.pedidos (pessoa_id, total) VALUES (2, 1)")
        c = checagem.Consulta(con)
        assert c.contar_onde("public", "pessoas", "apaga() > 0") is None
        assert c.contar_onde("public", "pessoas", "") == 5
        assert con.execute("SELECT current_setting('transaction_read_only'), current_setting('statement_timeout')"
                           ).fetchone() == ("off", "0")
        assert con.execute("SELECT count(*) FROM public.pedidos").fetchone()[0] == 4
        con.rollback()
    finally:
        con.close()
    assert banco.execute("SELECT count(*) FROM public.pedidos").fetchone()[0] == 3


def test_amostra_antes_depois_grupos(banco):
    banco.execute(PESSOAS_SQL)
    banco.execute("INSERT INTO public.pessoas (nome, email) VALUES ('Ana', NULL), ('ana ', NULL), ('Bia', NULL)")
    c = checagem.Consulta(banco)
    assert c.amostra("public", "pessoas", "nome ILIKE 'g%'", ["nome", "idade"], 1) in ([("Gabriel", 34)], [("Giovana", None)])
    assert len(c.amostra("public", "pessoas", "", ["id"])) == 5
    pares = c.antes_depois("public", "pessoas", "id = 4", {"nome": "trim(nome)", "idade": "41"})
    assert pares == [((" Gustavo", 40), ("Gustavo", 41))]
    assert c.grupos_repetidos("public", "pessoas", "nome IS NOT NULL", ["lower(trim(nome))"]) == (1, 2)
    assert c.grupos_repetidos("public", "pessoas", "", ["email"]) == (1, 3)      # GROUP BY junta os NULL


def test_espacos_e_vazios_de_fora(banco):
    banco.execute(PESSOAS_SQL)
    m = catalogo.modelo(banco, "public")
    t = m.por_nome("pessoas")
    c = {col.nome: col.id for col in t.colunas}
    consulta = checagem.Consulta(banco)
    g = filtro.Filtro([filtro.Condicao(c["nome"], "comeca", ["G"])])
    r = rascunho.Rascunho(m)
    assert [(a.chave, a.valores["n"]) for a in checagem.avisos_do_filtro(g, r, t.id, consulta)] == [("chk.espacos_nas_pontas", 1)]
    d = filtro.Filtro([filtro.Condicao(c["idade"], "diferente", ["30"])])
    assert [(a.chave, a.valores["n"], a.valores["coluna"]) for a in checagem.avisos_do_filtro(d, r, t.id, consulta)] == \
        [("chk.vazios_de_fora", 1, "idade")]


def test_vazios_e_espacos_contam_so_quem_o_filtro_inteiro_deixa_de_fora(banco):
    """Os exemplos da revisão: com OU, Giovana (idade vazia) entra pela outra condição; com E, só conta quem passaria
    pelas outras condições."""
    banco.execute(PESSOAS_SQL)
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    t = r.base.por_nome("pessoas")
    c = {col.nome: col.id for col in t.colunas}
    consulta = checagem.Consulta(banco)
    idade = filtro.Condicao(c["idade"], "diferente", ["30"])

    def chaves(*conds, conectivo="E"):
        return [(a.chave, a.valores["n"]) for a in checagem.avisos_do_filtro(filtro.Filtro(list(conds), conectivo), r, t.id, consulta)]
    assert chaves(idade, filtro.Condicao(c["nome"], "comeca", ["Gi"]), conectivo="OU") == []
    assert chaves(idade, filtro.Condicao(c["nome"], "comeca", ["A"])) == []
    assert chaves(idade, filtro.Condicao(c["nome"], "comeca", ["Gi"])) == [("chk.vazios_de_fora", 1)]
    g = filtro.Condicao(c["nome"], "comeca", ["G"])
    # ' Gustavo' (40) sairia de qualquer jeito pela idade; Giovana (G, idade vazia) sai só pelo vazio
    assert chaves(g, filtro.Condicao(c["idade"], "maior", ["100"])) == [("chk.vazios_de_fora", 1)]
    assert chaves(g, filtro.Condicao(c["idade"], "maior", ["35"])) == [("chk.vazios_de_fora", 1), ("chk.espacos_nas_pontas", 1)]
    assert chaves(g, filtro.Condicao(c["idade"], "igual", ["40"]), conectivo="OU") == []   # entra pela idade


def test_contagem_e_o_que_o_comando_pega(banco):
    """A contagem que a pessoa vê sai do mesmo WHERE que o comando roda: bate com o rowcount de verdade."""
    r, t, c = preparar(banco)
    banco.execute("INSERT INTO public.pessoas (nome, email) VALUES ('ANA@x.com', NULL), ('x', ' ana@x.com'), ('y', '')")
    consulta = checagem.Consulta(banco)
    f = filtro.Filtro([filtro.Condicao(c["nome"], "comeca", ["G"]), filtro.Condicao(c["idade"], "maior_igual", ["40"])],
                      conectivo="OU")
    ops = [rascunho.Alterar(t, f, {c["nome"]: rascunho.Mudanca("trim")}),
           rascunho.ApagarDuplicatas(t, filtro.Filtro(), (c["email"],), normalizar=True),
           rascunho.Apagar(t, f)]
    for op in ops:
        avisos = checagem.avisos_da_operacao(op, r, consulta)
        visto = checagem.contagem(avisos)
        cur = banco.execute(op.comandos(r.base)[0])
        assert visto == cur.rowcount, (op.tipo, visto, cur.rowcount, avisos)
    dup = checagem.avisos_da_operacao(rascunho.ApagarDuplicatas(t, filtro.Filtro(), (c["email"],), normalizar=True), r, consulta)
    assert [a.valores["n"] for a in dup if a.chave == "chk.duplicatas_vazias_fora"] == [2]   # o NULL e o '' ficam fora


def test_avisos_reais_de_apagar_e_inserir(banco):
    r, t, c = preparar(banco)
    consulta = checagem.Consulta(banco)
    avisos = checagem.avisos_da_operacao(rascunho.Apagar(t, filtro.Filtro(texto="id = 1")), r, consulta)
    cascata = [a for a in avisos if a.chave == "chk.cascata_apaga"]
    assert [(a.valores["n"], a.valores["tabela"]) for a in cascata] == [(2, "public.pedidos")]
    assert checagem.contagem(avisos) == 1 and ("info", "chk.soft_delete") in _chaves(avisos)
    avisos = checagem.avisos_da_operacao(rascunho.Inserir(t, {c["nome"]: "Zé", c["email"]: "ana@x.com",
                                                              c["nascimento"]: "2026-02-30"}), r, consulta)
    assert ("impede", "chk.ja_existe") in _chaves(avisos) and ("impede", "chk.valor_invalido") in _chaves(avisos)
    pedidos = r.base.por_nome("pedidos")
    pid = next(col.id for col in pedidos.colunas if col.nome == "pessoa_id")
    avisos = checagem.avisos_da_operacao(rascunho.Inserir(pedidos.id, {pid: "99"}), r, consulta)
    assert ("impede", "chk.fk_inexistente") in _chaves(avisos)
    avisos = checagem.avisos_da_operacao(rascunho.Apagar(t, filtro.Filtro(texto="xis > 1")), r, consulta)
    assert ("impede", "chk.consulta_falhou") in _chaves(avisos) and "xis" in [a for a in avisos if a.chave == "chk.consulta_falhou"][0].texto()


def test_renomeada_no_rascunho_conta_com_o_nome_do_banco(banco):
    r, t, c = preparar(banco)
    r.acrescentar(rascunho.Renomear(t, c["nome"], "nome_completo"))
    avisos = checagem.avisos_da_operacao(rascunho.Apagar(t, filtro.Filtro([filtro.Condicao(c["nome"], "comeca", ["G"])])),
                                         r, checagem.Consulta(banco))
    assert checagem.contagem(avisos) == 2 and ("info", "chk.aproximada") in _chaves(avisos)


def test_tabela_grande_estoura_o_tempo(banco):
    banco.execute("CREATE TABLE grande AS SELECT g AS x FROM generate_series(1, 3000000) g")
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    t = r.base.por_nome("grande")
    consulta = checagem.Consulta(banco, limite_ms=1)
    assert consulta.contar_onde("public", "grande", "x % 7 = 3") is None and not consulta.erro()
    avisos = checagem.avisos_da_operacao(rascunho.Apagar(t.id, filtro.Filtro(texto="x % 7 = 3")), r, consulta)
    assert ("info", "chk.sem_tempo") in _chaves(avisos) and checagem.contagem(avisos) is None
    assert checagem.Consulta(banco).contar_onde("public", "grande", "x <= 10") == 10       # a conexão segue de pé


def test_pagina_contar_linhas_pai_e_versao(banco):
    banco.execute(PESSOAS_SQL)
    banco.execute("INSERT INTO public.pessoas (nome, email) VALUES ('50%_a', 'p@x')")
    nomes, tipos, linhas = catalogo.pagina(banco, "public", "pessoas", "nome ILIKE 'g%'", ["id"], 0, 10)
    assert nomes[:2] == ["id", "nome"] and tipos[0] == "int8" and [l[1] for l in linhas] == ["Gabriel", "Giovana"]
    _, _, linhas = catalogo.pagina(banco, "public", "pessoas", "", ["id"], 1, 2)
    assert [l[0] for l in linhas] == [2, 3]
    _, _, linhas = catalogo.pagina(banco, "public", "pessoas", "", [], 0, 100)
    assert len(linhas) == 6
    assert catalogo.contar_filtrado(banco, "public", "pessoas", "nome ILIKE '%a%'") == 5
    assert catalogo.contar_filtrado(banco, "public", "pessoas", "") == 6
    pais = catalogo.linhas_pai(banco, "public", "pessoas", "id", "")
    assert pais[0] == (1, "Gabriel · gab@x.com") and pais[2] == (3, "Giovana") and len(pais) == 6
    assert catalogo.linhas_pai(banco, "public", "pessoas", "id", "o'b") == [(5, "O'Brien · ob@x.com")]
    assert catalogo.linhas_pai(banco, "public", "pessoas", "id", "%_") == [(6, "50%_a · p@x")]
    assert catalogo.linhas_pai(banco, "public", "pessoas", "id", "", limite=2) == pais[:2]
    assert [v for v, _ in catalogo.linhas_pai(banco, "public", "pedidos", "id", "")] == [1, 2, 3]
    assert catalogo.linhas_pai(banco, "public", "pedidos", "id", "2") == [(2, "2")]
    assert catalogo.versao(banco) == banco.info.server_version >= 130000


# ── revisão: nada escapa do somente leitura, cascata em profundidade, tempo na grade ──

_ESCAPES = ("true; COMMIT; DELETE FROM public.pedidos WHERE id = 1; SELECT 1",
            "true; ROLLBACK; DELETE FROM public.pedidos WHERE id = 2; SELECT 1",
            "true; COMMIT; DELETE FROM public.pedidos; SELECT 1 --")


def _intacto(banco):
    assert banco.execute("SELECT count(*) FROM public.pedidos").fetchone()[0] == 3
    assert banco.execute("SELECT current_setting('transaction_read_only'), current_setting('statement_timeout')"
                         ).fetchone() == ("off", "0")


def test_varios_comandos_no_where_nao_escapam(banco):
    banco.execute(PESSOAS_SQL)
    c = checagem.Consulta(banco)
    for onde in _ESCAPES:
        assert c.contar_onde("public", "pessoas", onde) is None and c.erro()
        assert c.amostra("public", "pessoas", onde, ["nome"]) is None
        assert c.antes_depois("public", "pessoas", onde, {"nome": "trim(nome)"}) is None
        assert c.grupos_repetidos("public", "pessoas", onde, ["nome"]) is None
        assert c.cascata("public", "pessoas", onde) is None
        with pytest.raises(psycopg.Error):
            catalogo.pagina(banco, "public", "pessoas", onde, ["id"], 0, 10)
        with pytest.raises(psycopg.Error):
            catalogo.contar_filtrado(banco, "public", "pessoas", onde)
        _intacto(banco)
    assert c.contar_onde("public", "pessoas", "nome ILIKE 'G%'") == 2              # o `%` segue sem escape


@pytest.mark.parametrize("conforme", ["on", "off"])
def test_barra_em_string_comum_nao_engana_o_validador(banco, conforme):
    banco.execute(PESSOAS_SQL)
    banco.execute(f"SET standard_conforming_strings = {conforme}")
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    t = r.base.por_nome("pessoas").id
    texto = "nome = '\\'' OR true); COMMIT; DELETE FROM public.pedidos; SELECT ('\\''"
    with pytest.raises(ValueError):
        filtro.validar_texto(texto)
    with pytest.raises(ValueError):
        checagem.avisos_da_operacao(rascunho.Apagar(t, filtro.Filtro(texto=texto)), r, checagem.Consulta(banco))
    assert checagem.Consulta(banco).contar_onde("public", "pessoas", "(" + texto + ")") is None   # mesmo sem o validador
    banco.execute("SET standard_conforming_strings = on")
    _intacto(banco)


def _cascata_sql(banco):
    banco.execute(PESSOAS_SQL)
    banco.execute("""
        CREATE TABLE public.itens (id int PRIMARY KEY, pedido_id bigint REFERENCES public.pedidos (id) ON DELETE CASCADE);
        CREATE TABLE public.notas_item (id int PRIMARY KEY, item_id int REFERENCES public.itens (id) ON DELETE RESTRICT);
        CREATE SCHEMA outro;
        CREATE TABLE outro.cartas (id int, pessoa_id bigint REFERENCES public.pessoas (id));
        CREATE TABLE public.apelidos (id int, pessoa_id bigint NOT NULL REFERENCES public.pessoas (id) ON DELETE SET NULL);
        CREATE TABLE public.chefes (id int PRIMARY KEY, chefe int REFERENCES public.chefes (id) ON DELETE CASCADE,
                                    pessoa_id bigint REFERENCES public.pessoas (id) ON DELETE CASCADE);
        INSERT INTO public.itens VALUES (1, 1), (2, 1), (3, 2), (4, 3);
        INSERT INTO public.notas_item VALUES (1, 4);
        INSERT INTO outro.cartas VALUES (1, 3);
        INSERT INTO public.apelidos VALUES (1, 4);
        INSERT INTO public.chefes VALUES (1, NULL, 5), (2, 1, NULL), (3, 2, NULL);
    """)


def test_cascata_segue_netos_outro_schema_e_set_null_em_obrigatoria(banco):
    _cascata_sql(banco)
    c = checagem.Consulta(banco)
    assert sorted(c.cascata("public", "pessoas", "id = 1")) == [("public.itens", "CASCADE", 3),
                                                                 ("public.pedidos", "CASCADE", 2)]
    assert ("public.notas_item", "RESTRICT", 1) in c.cascata("public", "pessoas", "id = 2")      # neto que impede
    assert ("outro.cartas", "NO ACTION", 1) in c.cascata("public", "pessoas", "id = 3")         # outro schema
    assert ("public.apelidos", checagem.NULO_EM_OBRIGATORIA, 1) in c.cascata("public", "pessoas", "id = 4")
    assert ("public.chefes", "CASCADE", 3) in c.cascata("public", "pessoas", "id = 5")          # o ciclo termina
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    t = r.base.por_nome("pessoas").id
    for onde, chave in (("id = 2", "chk.impede_filhos"), ("id = 3", "chk.impede_filhos"), ("id = 4", "chk.cascata_nulo_impede")):
        avisos = checagem.avisos_da_operacao(rascunho.Apagar(t, filtro.Filtro(texto=onde)), r, c)
        assert ("impede", chave) in _chaves(avisos), onde
        with pytest.raises(psycopg.Error):                       # e o PostgreSQL recusa de verdade
            with banco.transaction(force_rollback=True):
                banco.execute(f"DELETE FROM public.pessoas WHERE {onde}")
    avisos = checagem.avisos_da_operacao(rascunho.Apagar(t, filtro.Filtro(texto="id = 1")), r, c)
    assert not any(a.nivel == "impede" for a in avisos)
    assert [(a.valores["tabela"], a.valores["n"]) for a in avisos if a.chave == "chk.cascata_apaga"] == \
        [("public.pedidos", 2), ("public.itens", 3)]


def test_alterar_unica_nao_se_acusa_a_si_mesma(banco):
    r, t, c = preparar(banco)
    consulta = checagem.Consulta(banco)
    op = rascunho.Alterar(t, filtro.Filtro(texto="id = 2"), {c["email"]: rascunho.Mudanca("valor", "ana@x.com"),
                                                            c["idade"]: rascunho.Mudanca("valor", "99")})
    avisos = checagem.avisos_da_operacao(op, r, consulta)
    assert checagem.contagem(avisos) == 1 and not any(a.nivel == "impede" for a in avisos)
    op = rascunho.Alterar(t, filtro.Filtro(texto="id = 1"), {c["email"]: rascunho.Mudanca("valor", "ana@x.com")})
    assert ("impede", "chk.ja_existe") in _chaves(checagem.avisos_da_operacao(op, r, consulta))


def test_grade_com_tempo_limite(banco):
    banco.execute("CREATE TABLE grande AS SELECT g AS x, g::text AS t FROM generate_series(1, 3000000) g")
    assert catalogo.contar_filtrado(banco, "public", "grande", "x % 7 = 3", limite_ms=1) is None
    with pytest.raises(psycopg.errors.QueryCanceled):
        catalogo.pagina(banco, "public", "grande", "x % 7 = 3", ["t"], 0, 10, limite_ms=1)
    assert catalogo.linhas_pai(banco, "public", "grande", "x", "77", limite_ms=1) is None
    assert catalogo.contar_filtrado(banco, "public", "grande", "x <= 10") == 10        # a conexão segue de pé
    assert catalogo.linhas_pai(banco, "public", "grande", "x", "", limite=2) == [(1, "1"), (2, "2")]
    assert banco.execute("SELECT current_setting('statement_timeout')").fetchone()[0] == "0"


# ── revisão 2: a cascata conta linhas distintas que saem de verdade ──

def _real(banco, tabela, onde, tabelas):
    """O DELETE de verdade numa transação desfeita: {tabela: linhas que saem além das do WHERE} ou o erro."""
    antes = {t: banco.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tabelas}
    try:
        with banco.transaction(force_rollback=True):
            n = banco.execute(f"DELETE FROM {tabela} WHERE {onde or 'true'}").rowcount
            depois = {t: banco.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tabelas}
    except psycopg.Error:
        return "falha"
    sairam = {t: antes[t] - depois[t] - (n if t == tabela else 0) for t in tabelas}
    return {t: k for t, k in sairam.items() if k}


def _cascatas(res):
    return {t: n for t, a, n in res if a == "CASCADE"}


def _impede(res):
    return any(a not in ("CASCADE", "SET NULL", "SET DEFAULT") for _, a, _ in res)


def test_cascata_autorreferente_bate_com_o_delete_real(banco):
    banco.execute("""
      CREATE TABLE public.arv (id int PRIMARY KEY, pai int REFERENCES public.arv (id) ON DELETE CASCADE);
      INSERT INTO public.arv SELECT g, NULLIF(g - 1, 0) FROM generate_series(1, 12) g;
      CREATE TABLE public.nota (id int, arv_id int REFERENCES public.arv (id) ON DELETE RESTRICT);
      INSERT INTO public.nota VALUES (1, 11);
      CREATE TABLE public.dois (id int PRIMARY KEY, pai int REFERENCES public.dois (id) ON DELETE CASCADE,
                                raiz int REFERENCES public.dois (id) ON DELETE CASCADE);
      INSERT INTO public.dois SELECT g, NULLIF(g - 1, 0), CASE WHEN g = 1 THEN NULL ELSE 1 END FROM generate_series(1, 10) g;
      CREATE TABLE public.ciclo (id int PRIMARY KEY, outro int);
      INSERT INTO public.ciclo VALUES (1, 2), (2, 1), (3, NULL);
      ALTER TABLE public.ciclo ADD FOREIGN KEY (outro) REFERENCES public.ciclo (id) ON DELETE CASCADE;
    """)
    c = checagem.Consulta(banco)
    res = c.cascata("public", "arv", "id = 1")                   # a nota presa no nível 10 impede
    assert ("public.nota", "RESTRICT", 1) in res and not res.incompleta
    assert _real(banco, "public.arv", "id = 1", ["public.arv"]) == "falha"
    banco.execute("DELETE FROM public.nota")
    casos = [("arv", "id = 1"), ("arv", "id IN (1, 2, 3)"), ("arv", ""), ("arv", "id = 12"), ("ciclo", "id = 1"),
             ("ciclo", "id = 3"), ("dois", "id = 1"), ("dois", "id = 5")]
    for tabela, onde in casos:
        res = c.cascata("public", tabela, onde)
        assert not _impede(res) and not res.incompleta, (tabela, onde, res)
        assert _cascatas(res) == _real(banco, f"public.{tabela}", onde, [f"public.{tabela}"]), (tabela, onde, res)
    assert _cascatas(c.cascata("public", "arv", "id = 1")) == {"public.arv": 11}
    assert _cascatas(c.cascata("public", "arv", "id IN (1, 2, 3)")) == {"public.arv": 9}
    assert c.cascata("public", "arv", "") == [] and c.cascata("public", "ciclo", "id = 1") == [("public.ciclo", "CASCADE", 1)]


def test_cascata_entre_tabelas_bate_com_o_delete_real(banco):
    _cascata_sql(banco)
    c = checagem.Consulta(banco)
    tabelas = ["public.pedidos", "public.itens", "public.chefes", "public.pessoas"]
    for onde in ("id = 1", "id = 5", "id IN (1, 5)", "nome ILIKE 'g%'"):
        res = c.cascata("public", "pessoas", onde)
        real = _real(banco, "public.pessoas", onde, tabelas)
        if _impede(res):
            assert real == "falha", (onde, res)
        else:
            assert _cascatas(res) == real, (onde, res, real)


def test_cascata_alem_do_teto_avisa_que_ficou_incompleta(banco, monkeypatch):
    banco.execute("CREATE TABLE public.t0 (id int PRIMARY KEY); INSERT INTO public.t0 VALUES (1)")
    for i in range(1, 6):
        banco.execute(f"CREATE TABLE public.t{i} (id int PRIMARY KEY, p int REFERENCES public.t{i - 1} (id) ON DELETE CASCADE);"
                      f"INSERT INTO public.t{i} VALUES (1, 1)")
    banco.execute("CREATE TABLE public.trava (id int, p int REFERENCES public.t5 (id) ON DELETE RESTRICT);"
                  "INSERT INTO public.trava VALUES (1, 1)")
    c = checagem.Consulta(banco)
    res = c.cascata("public", "t0", "id = 1")
    assert not res.incompleta and ("public.trava", "RESTRICT", 1) in res and _cascatas(res) == {f"public.t{i}": 1 for i in range(1, 6)}
    monkeypatch.setattr(checagem, "_PROFUNDIDADE", 3)
    res = c.cascata("public", "t0", "id = 1")
    assert res.incompleta and ("public.trava", "RESTRICT", 1) not in res     # não dá para ver: diz que ficou incompleta
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    avisos = checagem.avisos_da_operacao(rascunho.Apagar(r.base.por_nome("t0").id, filtro.Filtro(texto="id = 1")), r, c)
    assert ("atencao", "chk.cascata_incompleta") in _chaves(avisos)
    assert ("atencao", "chk.cascata_apaga_pelo_menos") in _chaves(avisos) and "chk.cascata_apaga" not in [a.chave for a in avisos]
    assert all(a.texto() for a in avisos)
    monkeypatch.setattr(checagem, "_PROFUNDIDADE", 16)
    monkeypatch.setattr(checagem, "_ORCAMENTO_S", 0.0)               # o tempo total da cascata acabou
    res = c.cascata("public", "t0", "id = 1")
    assert res.incompleta and res == []


def test_cascata_ciclo_entre_tabelas_diferentes_fica_incompleta(banco):
    banco.execute("""
      CREATE TABLE public.a (id int PRIMARY KEY, b_id int);
      CREATE TABLE public.b (id int PRIMARY KEY, a_id int REFERENCES public.a (id) ON DELETE CASCADE);
      ALTER TABLE public.a ADD FOREIGN KEY (b_id) REFERENCES public.b (id) ON DELETE CASCADE;
      INSERT INTO public.a VALUES (1, NULL), (2, NULL); INSERT INTO public.b VALUES (1, 1);
      UPDATE public.a SET b_id = 1 WHERE id = 2;
    """)
    res = checagem.Consulta(banco).cascata("public", "a", "id = 1")
    assert res.incompleta and ("public.b", "CASCADE", 1) in res          # o mínimo: b(1); a(2) viria depois


def test_cascata_sem_permissao_de_ler_uma_filha(banco):
    banco.execute(PESSOAS_SQL)
    banco.execute("CREATE TABLE public.segredo (id int, pessoa_id bigint REFERENCES public.pessoas (id) ON DELETE CASCADE);"
                  "INSERT INTO public.segredo VALUES (1, 1)")
    if banco.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0]:
        pytest.skip("superusuário lê tudo")
    banco.execute("REVOKE SELECT ON public.segredo FROM CURRENT_USER")      # o dono tira de si mesmo
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    c = checagem.Consulta(banco)
    res = c.cascata("public", "pessoas", "id = 1")
    assert res.sem_permissao == ["public.segredo"] and res.incompleta and ("public.pedidos", "CASCADE", 2) in res
    avisos = checagem.avisos_da_operacao(rascunho.Apagar(r.base.por_nome("pessoas").id, filtro.Filtro(texto="id = 1")), r, c)
    aviso = [a for a in avisos if a.chave == "chk.sem_permissao_ver"]
    assert [(a.nivel, a.valores["tabela"]) for a in aviso] == [("atencao", "public.segredo")]
    assert not any(a.chave == "chk.consulta_falhou" for a in avisos) and ("atencao", "chk.cascata_incompleta") in _chaves(avisos)


def test_cascata_apagando_direto_numa_particao(banco):
    banco.execute("""
      CREATE TABLE public.ev (id int, ano int, PRIMARY KEY (id, ano)) PARTITION BY LIST (ano);
      CREATE TABLE public.ev_2025 PARTITION OF public.ev FOR VALUES IN (2025);
      CREATE TABLE public.ev_2026 PARTITION OF public.ev FOR VALUES IN (2026);
      CREATE TABLE public.anexo (id int, ev_id int, ev_ano int,
                                 FOREIGN KEY (ev_id, ev_ano) REFERENCES public.ev (id, ano) ON DELETE CASCADE);
      INSERT INTO public.ev VALUES (1, 2025), (2, 2025), (1, 2026);
      INSERT INTO public.anexo VALUES (1, 1, 2025), (2, 1, 2025), (3, 1, 2026), (4, 2, 2025);
    """)
    c = checagem.Consulta(banco)
    for tabela, onde in (("ev_2025", "id = 1"), ("ev", "id = 1"), ("ev_2026", ""), ("ev", "")):
        res = c.cascata("public", tabela, onde)
        assert _cascatas(res) == _real(banco, f"public.{tabela}", onde, ["public.anexo"]), (tabela, onde, res)
    assert c.cascata("public", "ev_2025", "id = 1") == [("public.anexo", "CASCADE", 2)]


# ── Task 6: o aplicar confere as contagens ──

def sessao_de_teste(r):
    from ratt.db import sessao
    s = sessao.SessaoRascunho(5432, "teste", "public")
    s.rascunho = r
    return s


def _apagar_g(r, vista):
    t = r.base.por_nome("pessoas")
    nome = t.colunas[1].id
    r.acrescentar(rascunho.Apagar(t.id, filtro.Filtro([filtro.Condicao(nome, "comeca", ["G"])]), vista=vista))
    return t, nome


def test_aplicar_desfaz_quando_o_banco_mudou(banco, tmp_path):
    banco.execute(PESSOAS_SQL)
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    _apagar_g(r, 2)
    banco.execute("INSERT INTO public.pessoas (nome) VALUES ('Gil')")          # o banco mudou depois
    s = sessao_de_teste(r)
    con = psycopg.connect(banco.info.dsn)
    codigo, indice, msg = s.aplicar(con, tmp_path / "a.sql", ensaio=False)
    con.close()
    assert (codigo, indice) == (1, 0) and "3" in msg and "2" in msg
    assert banco.execute("SELECT count(*) FROM public.pessoas WHERE nome LIKE 'G%'").fetchone()[0] == 3


def test_com_operacao_anterior_na_mesma_tabela_nao_bloqueia(banco, tmp_path):
    banco.execute(PESSOAS_SQL)
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    t = r.base.por_nome("pessoas")
    r.acrescentar(rascunho.Inserir(t.id, {t.colunas[1].id: "Gil"}))
    _apagar_g(r, 2)
    s = sessao_de_teste(r)
    con = psycopg.connect(banco.info.dsn)
    codigo, indice, msg = s.aplicar(con, tmp_path / "a.sql", ensaio=False)
    con.close()
    assert codigo == 0 and msg is None
    assert banco.execute("SELECT count(*) FROM public.pessoas WHERE nome LIKE 'G%'").fetchone()[0] == 0


def test_ensaio_mostra_pegou_viu_e_nao_aborta(banco, tmp_path, capsys):
    banco.execute(PESSOAS_SQL)
    r = rascunho.Rascunho(catalogo.modelo(banco, "public"))
    _apagar_g(r, 1)
    s = sessao_de_teste(r)
    con = psycopg.connect(banco.info.dsn)
    codigo, indice, msg = s.aplicar(con, tmp_path / "a.sql", ensaio=True)
    con.close()
    saida = capsys.readouterr().out
    assert codigo == 0 and "pegou 2 (você viu 1)" in saida


def test_executar_sem_conferir_igual(banco, tmp_path, capsys):
    from ratt.db import rodar
    arq = tmp_path / "s.sql"
    arq.write_text("DELETE FROM public.pessoas WHERE nome LIKE 'G%';")
    con = psycopg.connect(banco.info.dsn)
    banco.execute(PESSOAS_SQL)
    import re
    sem_ms = lambda x: re.sub(r"\d+ ms", "N ms", x)
    assert rodar.executar(arq, con, ensaio=True) == 0
    antes = sem_ms(capsys.readouterr().out)
    assert "pegou" not in antes
    chamadas = []
    assert rodar.executar(arq, con, ensaio=True, conferir=lambda c, n, e: chamadas.append(n)) == 0
    assert sem_ms(capsys.readouterr().out) == antes and chamadas == [2]
    con.close()


# ── a tela Dados (Task 7) ──

def _apps_isolados(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "dados"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


async def _pronto(app, voltas=6):
    import asyncio
    for _ in range(voltas):
        await app.workers.wait_for_complete()
        await asyncio.sleep(0.1)


def test_e_na_tela_do_banco_abre_a_tela_dados(banco, tmp_path, monkeypatch):
    import asyncio
    import getpass
    from textual.widgets import Tree
    from ratt.db import tela_dados
    from ratt.db.tela import TelaBanco
    _apps_isolados(monkeypatch, tmp_path)
    banco.execute(PESSOAS_SQL)

    async def roteiro():
        app = TelaBanco(banco.info.port, getpass.getuser(), banco.info.dbname)
        async with app.run_test(size=(140, 44)) as piloto:
            await _pronto(app)
            arvore = app.query_one("#arvore", Tree)
            no = next(n for n in app._nos(arvore.root) if isinstance(n.data, catalogo.Objeto) and n.data.nome == "pessoas")
            arvore.move_cursor(no)
            await _pronto(app)
            assert app.atual.nome == "pessoas"
            await piloto.press("e")
            await _pronto(app)
            tela = app.screen
            assert isinstance(tela, tela_dados.TelaDados) and not tela.so_ver
            assert len(tela.linhas) == 5 and tela.total == 5
            tela.acrescentar_condicao(filtro.Condicao(tela.tabela_id + ".c2", "comeca", ["G"]))
            await _pronto(app)
            assert sorted(l[1] for l in tela.linhas) == ["Gabriel", "Giovana"]
            assert [a.chave for a in tela.avisos] == ["chk.espacos_nas_pontas"]      # o " Gustavo"
            await piloto.press("escape")
            await _pronto(app)
            assert app.screen is app.screen_stack[0]
    asyncio.run(roteiro())


def test_e_numa_view_abre_so_para_ver(banco, tmp_path, monkeypatch):
    import asyncio
    import getpass
    from textual.widgets import Tree
    from ratt.db import tela_dados
    from ratt.db.tela import TelaBanco
    _apps_isolados(monkeypatch, tmp_path)
    banco.execute(PESSOAS_SQL + "CREATE VIEW public.maiores AS SELECT nome, idade FROM public.pessoas WHERE idade > 30;")

    async def roteiro():
        app = TelaBanco(banco.info.port, getpass.getuser(), banco.info.dbname)
        async with app.run_test(size=(140, 44)) as piloto:
            await _pronto(app)
            arvore = app.query_one("#arvore", Tree)
            for n in app._nos(arvore.root):
                n.expand()
            await _pronto(app)
            no = next(n for n in app._nos(arvore.root) if isinstance(n.data, catalogo.Objeto) and n.data.nome == "maiores")
            arvore.move_cursor(no)
            await _pronto(app)
            await piloto.press("e")
            await _pronto(app)
            tela = app.screen
            assert isinstance(tela, tela_dados.TelaDados) and tela.so_ver and len(tela.linhas) == 3
    asyncio.run(roteiro())


def test_v_no_canvas_abre_a_tela_dados(banco, tmp_path, monkeypatch):
    import asyncio
    import getpass
    from ratt.db import canvas, tela_dados
    from test_canvas import AppTeste
    _apps_isolados(monkeypatch, tmp_path)
    banco.execute(PESSOAS_SQL)

    async def roteiro():
        app = AppTeste(canvas.FonteBanco(None, banco.info.port, getpass.getuser(), banco.info.dbname))
        async with app.run_test(size=(140, 44)) as piloto:
            await _pronto(app)
            cv = app.screen
            pessoas = cv.todo.por_nome("pessoas").id
            while not cv.selecao or cv.selecao[1] != pessoas:
                await piloto.press("tab")
            await piloto.press("v")
            await _pronto(app)
            tela = app.screen
            assert isinstance(tela, tela_dados.TelaDados) and tela.tabela_id == pessoas
            assert len(tela.linhas) == 5 and tela.total == 5
            assert tela.sessao is cv.sessao                    # o mesmo rascunho do canvas
            await piloto.press("q")
            await _pronto(app)
            assert app.screen is cv
    asyncio.run(roteiro())


# ── revisão da Task 7 ──

def _espera_escrita(banco, tabela, antes, limite=10.0):
    """As estatísticas do PostgreSQL chegam um pouco depois do commit: espera a contagem da tabela mudar."""
    import time
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        agora = catalogo.escritas_da_tabela(banco, banco.execute("SELECT %s::regclass::oid", (tabela,)).fetchone()[0])
        if agora != antes:
            return agora
        time.sleep(0.2)
    raise AssertionError(f"as estatísticas de {tabela} não mudaram")


def test_vigia_so_rele_com_escrita_nesta_tabela(banco, tmp_path, monkeypatch):
    import asyncio
    import getpass
    from ratt.db import canvas, tela_dados
    from test_dados import AppDados  # noqa: F401  (o mesmo App dos testes sem banco)
    _apps_isolados(monkeypatch, tmp_path)
    banco.execute(PESSOAS_SQL)
    oid = lambda nome: banco.execute("SELECT %s::regclass::oid", (nome,)).fetchone()[0]

    async def roteiro():
        fonte = canvas.FonteBanco(None, banco.info.port, getpass.getuser(), banco.info.dbname)
        app = AppDados(fonte, "t%d" % oid("public.pessoas"))
        async with app.run_test(size=(140, 44)):
            await _pronto(app)
            tela = app.screen
            assert len(tela.linhas) == 5
            paginas = []
            ler = fonte.pagina
            fonte.pagina = lambda *a: paginas.append(a) or ler(*a)
            antes = catalogo.escritas_da_tabela(banco, oid("public.pedidos"))
            outra = psycopg.connect(banco.info.dsn, autocommit=True)
            outra.execute("INSERT INTO public.pedidos (pessoa_id, total) VALUES (2, 99)")
            outra.close()
            _espera_escrita(banco, "public.pedidos", antes)
            tela.vigiar()
            await _pronto(app)
            assert paginas == []                                   # escreveram em outra tabela: nada a reler
            antes = catalogo.escritas_da_tabela(banco, oid("public.pessoas"))
            outra = psycopg.connect(banco.info.dsn, autocommit=True)
            outra.execute("INSERT INTO public.pessoas (nome) VALUES ('Gil')")
            outra.close()
            _espera_escrita(banco, "public.pessoas", antes)
            tela.vigiar()
            await _pronto(app)
            assert len(paginas) == 1 and len(tela.linhas) == 6 and tela.total == 6
    asyncio.run(roteiro())


def test_escritas_de_particionada_e_de_view(banco):
    banco.execute("""
        CREATE TABLE public.medida (dia date, v int) PARTITION BY RANGE (dia);
        CREATE TABLE public.medida_1 PARTITION OF public.medida FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
        CREATE VIEW public.vm AS SELECT * FROM public.medida_1;""")
    oid = lambda nome: banco.execute("SELECT %s::regclass::oid", (nome,)).fetchone()[0]
    antes = (catalogo.escritas_da_tabela(banco, oid("public.medida")), catalogo.escritas_da_tabela(banco, oid("public.vm")))
    outra = psycopg.connect(banco.info.dsn, autocommit=True)
    outra.execute("INSERT INTO public.medida VALUES ('2020-05-01', 1)")
    outra.close()
    _espera_escrita(banco, "public.medida", antes[0])
    _espera_escrita(banco, "public.vm", antes[1])


def test_e_numa_view_nao_le_o_banco_na_thread_da_tela(banco, tmp_path, monkeypatch):
    import asyncio
    import getpass
    import threading
    from textual.widgets import Tree
    from ratt.db import tela_dados
    from ratt.db.tela import TelaBanco
    _apps_isolados(monkeypatch, tmp_path)
    banco.execute(PESSOAS_SQL + "CREATE VIEW public.maiores AS SELECT nome, idade FROM public.pessoas WHERE idade > 30;")
    leituras = []
    original = catalogo.colunas
    monkeypatch.setattr(catalogo, "colunas", lambda *a: leituras.append(threading.current_thread() is threading.main_thread())
                        or original(*a))

    class Espiao:
        def __init__(self):
            self.usos = []

        def __getattr__(self, nome):
            self.usos.append(nome)
            raise AssertionError(f"conexao.{nome} na thread da tela")

    async def roteiro():
        app = TelaBanco(banco.info.port, getpass.getuser(), banco.info.dbname)
        async with app.run_test(size=(140, 44)):
            await _pronto(app)
            arvore = app.query_one("#arvore", Tree)
            for n in app._nos(arvore.root):
                n.expand()
            await _pronto(app)
            no = next(n for n in app._nos(arvore.root) if isinstance(n.data, catalogo.Objeto) and n.data.nome == "maiores")
            arvore.move_cursor(no)
            await _pronto(app)
            del leituras[:]
            espiao, real = Espiao(), app.conexao
            app.conexao = espiao
            try:
                app.action_dados()                 # síncrono, na thread da tela: não pode tocar no banco
            finally:
                app.conexao = real
            assert espiao.usos == [] and leituras == []
            await _pronto(app)
            tela = app.screen
            assert isinstance(tela, tela_dados.TelaDados) and tela.so_ver and len(tela.linhas) == 3
            assert leituras == [False]             # as colunas vieram na thread da tela Dados
    asyncio.run(roteiro())


# ── Task 8: inserir pelo formulário ──

def test_inserir_pelo_formulario_e_aplicar(banco, tmp_path, monkeypatch):
    import asyncio
    import getpass
    from textual.widgets import Input, OptionList
    from ratt.db import canvas, formularios_dados
    from test_dados import AppDados
    _apps_isolados(monkeypatch, tmp_path)
    banco.execute(PESSOAS_SQL)
    oid = lambda nome: banco.execute("SELECT %s::regclass::oid", (nome,)).fetchone()[0]

    def digitar(form, coluna, texto):
        form.campo(coluna).query_one(Input).value = texto

    async def roteiro():
        fonte = canvas.FonteBanco(None, banco.info.port, getpass.getuser(), banco.info.dbname)
        app = AppDados(fonte, "t%d" % oid("public.pessoas"))
        async with app.run_test(size=(140, 50)) as piloto:
            await _pronto(app)
            tela = app.screen
            await piloto.press("i")
            await piloto.pause()
            form = app.screen
            assert isinstance(form, formularios_dados.FormInserir)
            digitar(form, "nome", "Zé d'Ávila")
            digitar(form, "idade", "vinte")
            await asyncio.sleep(0.4)
            await _pronto(app)
            assert t("chk.valor_invalido", coluna="idade", valor="vinte", tipo="integer") in \
                str(form.query_one("#form_avisos").render())
            digitar(form, "idade", "99999999999")                 # é número, mas não cabe num integer: o banco diz
            await asyncio.sleep(0.4)
            await _pronto(app)
            assert t("chk.valor_invalido", coluna="idade", valor="99999999999", tipo="integer") in \
                str(form.query_one("#form_avisos").render())
            digitar(form, "idade", "33")
            digitar(form, "email", "ana@x.com")
            await asyncio.sleep(0.4)
            await _pronto(app)
            assert t("chk.ja_existe", coluna="email", valor="ana@x.com", n=1) in str(form.query_one("#form_avisos").render())
            digitar(form, "email", "ze@x.com")
            digitar(form, "nascimento", "2001-02-03")
            await asyncio.sleep(0.4)                       # os avisos chegam antes do clique (o formulário cresce)
            await _pronto(app)
            await piloto.click("#por_no_rascunho")
            await _pronto(app)
            assert app.screen is tela and len(tela.sessao.rascunho.operacoes) == 1
            await piloto.press("escape")
            await _pronto(app)

        app = AppDados(canvas.FonteBanco(None, banco.info.port, getpass.getuser(), banco.info.dbname),
                       "t%d" % oid("public.pedidos"))
        async with app.run_test(size=(140, 50)) as piloto:
            await _pronto(app)
            tela = app.screen
            assert len(tela.sessao.rascunho.operacoes) == 1                 # o mesmo rascunho do schema
            await piloto.press("i")
            await piloto.pause()
            form = app.screen
            campo = form.campo("pessoa_id")
            await piloto.click("#" + campo.query_one(".escolher").id)
            await _pronto(app)
            escolher = app.screen
            escolher.query_one("#busca", Input).value = "o'b"
            await asyncio.sleep(0.4)
            await _pronto(app)
            lista = escolher.query_one(OptionList)
            assert lista.option_count == 1 and "O'Brien" in str(lista.get_option_at_index(0).prompt)
            lista.focus()
            lista.highlighted = 0
            await piloto.press("enter")
            await _pronto(app)
            assert app.screen is form and campo.valor() == "5"
            digitar(form, "total", "12.50")
            await asyncio.sleep(0.4)                       # os avisos chegam antes do clique (o formulário cresce)
            await _pronto(app)
            await piloto.click("#por_no_rascunho")
            await _pronto(app)
            s = tela.sessao
            con = psycopg.connect(banco.info.dsn)
            try:
                codigo, indice, msg = s.aplicar(con, tmp_path / "a.sql", ensaio=False)
            finally:
                con.close()
            assert (codigo, indice, msg) == (0, None, None)
    asyncio.run(roteiro())
    assert banco.execute("SELECT nome, idade, email, nascimento::text, ativo FROM public.pessoas WHERE id = 6").fetchone() \
        == ("Zé d'Ávila", 33, "ze@x.com", "2001-02-03", True)
    assert banco.execute("SELECT pessoa_id, total::text FROM public.pedidos WHERE id = 4").fetchone() == (5, "12.50")


class _Espiao:
    """Uma conexão de verdade que anota cada `execute` (com `prepare`)."""

    def __init__(self, conexao):
        self._c, self.comandos = conexao, []

    def execute(self, consulta, params=None, **kw):
        texto = consulta if isinstance(consulta, str) else consulta.as_string(self._c)
        self.comandos.append((" ".join(texto.split()), params, kw.get("prepare")))
        return self._c.execute(consulta, params, **kw)

    def __getattr__(self, nome):
        return getattr(self._c, nome)


def test_escritas_da_tabela_so_leitura_com_tempo_limite(banco):
    banco.execute(PESSOAS_SQL)
    oid = banco.execute("SELECT 'public.pessoas'::regclass::oid").fetchone()[0]
    espiao = _Espiao(banco)
    n = catalogo.escritas_da_tabela(espiao, oid, limite_ms=1234)
    assert n == catalogo.escritas_da_tabela(banco, oid) and n >= 0
    textos = [c[0] for c in espiao.comandos]
    assert textos[0] == "SET TRANSACTION READ ONLY"
    assert espiao.comandos[1][1] == ("1234",) and "statement_timeout" in textos[1]
    assert "pg_stat_user_tables" in textos[2] and espiao.comandos[2][2] is True         # um comando só
    assert banco.execute("SELECT current_setting('statement_timeout'), current_setting('transaction_read_only')"
                         ).fetchone() == ("0", "off")


def test_coluna_gerada_vem_marcada_do_catalogo(banco):
    banco.execute("CREATE TABLE public.conta (a int, b int, c int GENERATED ALWAYS AS (a * b) STORED, "
                  "d int GENERATED BY DEFAULT AS IDENTITY)")
    cols = {c.nome: c for c in catalogo.modelo(banco, "public").por_nome("conta").colunas}
    assert cols["c"].gerada and not cols["a"].gerada and not cols["d"].gerada and cols["d"].identidade
