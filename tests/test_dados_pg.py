"""As operações de dados do rascunho contra um PostgreSQL de verdade (o fixture `banco` cria um banco descartável)."""
from conftest import PESSOAS_SQL
from ratt.db import catalogo, filtro, rascunho


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
