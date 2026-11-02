"""Testes da tela Dados e do que ela divide com o canvas: a sessão de rascunho e o painel."""
import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from ratt.db import checagem, filtro, guardar, rascunho, sessao
from ratt.db.modelo import Coluna, Ligacao, Modelo, Tabela
from ratt.db.idioma import t

from test_canvas import AppTeste, ConsultaFalsa, FonteFalsa, modelo_png, pronto


# ── a sessão de rascunho ──

def test_mesma_sessao_para_duas_telas(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    app = types.SimpleNamespace(); f = FonteFalsa()
    assert sessao.obter(app, f, "public") is sessao.obter(app, f, "public")
    assert sessao.obter(app, f, "public") is not sessao.obter(app, f, "outro")


def test_ouvintes_e_disco(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    s = sessao.obter(types.SimpleNamespace(), FonteFalsa(), "public"); s.abrir(modelo_png())
    chamadas = []; s.ouvir(lambda: chamadas.append(1))
    s.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    assert chamadas == [1] and guardar.ler_rascunho(5432, "teste", "public")["operacoes"]
    s.desfazer(); assert chamadas == [1, 1] and guardar.ler_rascunho(5432, "teste", "public") is None


def test_canvas_e_outra_tela_dividem_o_rascunho(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        fonte = FonteFalsa()
        app = AppTeste(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            tela = app.screen
            s = sessao.obter(app, fonte, "public")
            assert s is tela.sessao
            s.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
            await piloto.pause()
            assert [op.tipo for op in tela.rascunho.operacoes] == ["renomear"]
            assert t("canvas.rascunho", n=1) in str(tela.query_one("#canvas_titulo").render())
            assert "pessoas" in {tb.nome for tb in tela.vista.tabelas.values()}     # o canvas redesenhou
    asyncio.run(roteiro())


def test_sem_gravar_nao_perde_o_rascunho_da_memoria(monkeypatch, tmp_path):
    """A primeira leitura do disco falha; o que entrou depois na memória sobrevive a outra tela abrir a sessão
    (e a uma releitura), mesmo que o disco já dê para ler."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    r = rascunho.Rascunho(modelo_png()); r.acrescentar(rascunho.Renomear("t2", None, "papeis"))
    guardar.gravar_rascunho(5432, "teste", "public", r.para_dict())
    from pathlib import Path
    original = Path.read_text

    def quebrada(self, *a, **k):
        if self.name == "rascunho-public.json":
            raise PermissionError(13, "Permission denied", str(self))
        return original(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", quebrada)
    s = sessao.obter(types.SimpleNamespace(), FonteFalsa(), "public")
    avisos = s.abrir(modelo_png())
    assert s.sem_gravar and avisos and avisos[0].severidade == "error"
    s.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    monkeypatch.setattr(Path, "read_text", original)            # o disco voltou
    assert s.abrir(modelo_png()) == [] and [op.novo for op in s.rascunho.operacoes] == ["pessoas"]
    assert s.reler(modelo_png()) is None and [op.novo for op in s.rascunho.operacoes] == ["pessoas"]
    assert [op["novo"] for op in guardar.ler_rascunho(5432, "teste", "public")["operacoes"]] == ["papeis"]  # intocado


def test_sem_gravar_e_vazio_tenta_ler_de_novo(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    r = rascunho.Rascunho(modelo_png()); r.acrescentar(rascunho.Renomear("t2", None, "papeis"))
    guardar.gravar_rascunho(5432, "teste", "public", r.para_dict())
    from pathlib import Path
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError(13, "x"))
                        if self.name == "rascunho-public.json" else original(self, *a, **k))
    s = sessao.obter(types.SimpleNamespace(), FonteFalsa(), "public")
    s.abrir(modelo_png())
    assert s.sem_gravar and s.rascunho.operacoes == []
    monkeypatch.setattr(Path, "read_text", original)
    s.abrir(modelo_png())
    assert not s.sem_gravar and [op.novo for op in s.rascunho.operacoes] == ["papeis"]


def test_sessao_aplicar_no_postgres(banco, tmp_path, monkeypatch):
    import psycopg
    from conftest import PNG_SQL
    from ratt.db import catalogo
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    banco.execute(PNG_SQL)
    m = catalogo.modelo(banco, "public")
    s = sessao.obter(types.SimpleNamespace(), FonteFalsa(), "public"); s.abrir(m)
    s.acrescentar(rascunho.Renomear(m.por_nome("users").id, None, "pessoas"))
    con2 = psycopg.connect(banco.info.dsn)
    try:
        assert s.aplicar(con2, tmp_path / "a.sql", True) == (0, None, None)
        assert catalogo.modelo(banco, "public").por_nome("pessoas") is None          # o ensaio não muda nada
        assert s.aplicar(con2, tmp_path / "a.sql", False) == (0, None, None)
    finally:
        con2.close()
    nomes = {tb.nome for tb in catalogo.modelo(banco, "public").tabelas.values()}
    assert "pessoas" in nomes and "users" not in nomes


# ── o filtro ──

def modelo_pessoas():
    """O modelo de PESSOAS_SQL (tests/conftest.py) montado à mão."""
    pessoas = Tabela("t1", "public", "pessoas", [
        Coluna("t1.c1", "id", "bigint", obrigatoria=True, identidade=True, pk=True),
        Coluna("t1.c2", "nome", "text", obrigatoria=True),
        Coluna("t1.c3", "idade", "integer"),
        Coluna("t1.c4", "email", "text", unica=True),
        Coluna("t1.c5", "nascimento", "date"),
        Coluna("t1.c6", "ativo", "boolean", padrao="true")])
    pedidos = Tabela("t2", "public", "pedidos", [
        Coluna("t2.c1", "id", "bigint", obrigatoria=True, identidade=True, pk=True),
        Coluna("t2.c2", "pessoa_id", "bigint"),
        Coluna("t2.c3", "total", "numeric")])
    m = Modelo("public", {"t1": pessoas, "t2": pedidos})
    m.ligacoes["k1"] = Ligacao("k1", "t2", ("t2.c2",), "t1", ("t1.c1",), "pedidos_pessoa_id_fkey", "CASCADE")
    return m


def test_comeca_com_e_mes():
    m = modelo_pessoas()
    f = filtro.Filtro([filtro.Condicao("t1.c2", "comeca", ["G"]),
                       filtro.Condicao("t1.c5", "maior_igual", ["6"], transformacao="mes")])
    assert filtro.where(f, m, "t1") == "(nome ILIKE 'G%') AND (extract(month FROM nascimento) >= 6)"


def test_literal_e_like_com_caracteres_dificeis():
    assert filtro.literal("O'Brien", "text") == "'O''Brien'"
    assert filtro.literal("29", "integer") == "29" and filtro.literal("sim", "boolean") == "TRUE"
    assert filtro.padrao_like("50%_a\\b", "contem") == "E'%50\\\\%\\\\_a\\\\\\\\b%'"
    with pytest.raises(ValueError):
        filtro.literal("vinte", "integer")


def test_vazio_em_texto_e_em_numero():
    m = modelo_pessoas()
    assert filtro.where(filtro.Filtro([filtro.Condicao("t1.c4", "vazio")]), m, "t1") == "(email IS NULL OR trim(email) = '')"
    assert filtro.where(filtro.Filtro([filtro.Condicao("t1.c3", "vazio")]), m, "t1") == "idade IS NULL"


def test_data_relativa_e_ou_e_nao():
    m = modelo_pessoas()
    f = filtro.Filtro([filtro.Condicao("t1.c5", "ha_mais_de", ["90", "dias"]),
                       filtro.Condicao("t1.c6", "verdadeiro", negar=True)], conectivo="OU")
    assert filtro.where(f, m, "t1") == "(nascimento < now() - interval '90 days') OR (NOT (ativo IS TRUE))"


def test_existe_em_outra_tabela():
    m = modelo_pessoas()
    f = filtro.Filtro([filtro.Condicao("", "existe", existe=filtro.Existe("k1", nao=True))])
    assert filtro.where(f, m, "t1") == (
        "NOT EXISTS (SELECT 1 FROM public.pedidos WHERE public.pedidos.pessoa_id = public.pessoas.id)")


def test_avisos_de_vazio():
    m = modelo_pessoas()
    f = filtro.Filtro([filtro.Condicao("t1.c3", "diferente", ["30"]), filtro.Condicao("t1.c2", "contem", ["a"])])
    assert filtro.deixa_vazios_de_fora(f, m, "t1") == ["t1.c3"]          # nome é obrigatória
    assert filtro.fora_lista_com_vazio(filtro.Filtro([filtro.Condicao("t1.c4", "fora_lista", ["a@x", ""])]))


def test_operadores_por_tipo():
    assert filtro.operadores("boolean") == ["verdadeiro", "falso", "vazio", "preenchido"]
    assert "ha_mais_de" in filtro.operadores("date") and "contem" not in filtro.operadores("date")
    assert filtro.operadores("date", "mes")[-1] == "entre" and "menor" in filtro.operadores("date", "mes")


def test_texto_livre():
    filtro.validar_texto("idade > 30 AND nome ILIKE 'a%'")
    for ruim in ("1=1; DROP TABLE x", "1=1 -- x", "1=1 /* x */"):
        with pytest.raises(ValueError):
            filtro.validar_texto(ruim)
    assert filtro.where(filtro.Filtro(texto="idade > 30"), modelo_pessoas(), "t1") == "(idade > 30)"


def test_json_ida_e_volta():
    f = filtro.Filtro([filtro.Condicao("", "existe", existe=filtro.Existe("k1", [filtro.Condicao("t2.c3", "maior", ["15"])]))])
    assert filtro.de_dict(json.loads(json.dumps(filtro.para_dict(f)))) == f
    assert filtro.refere(f, "t1") == {"t1", "k1", "t2.c3"}


def _w(*conds, **kw):
    return filtro.where(filtro.Filtro(list(conds), **kw), modelo_pessoas(), "t1")


def test_operadores_de_texto_com_valores_dificeis():
    assert _w(filtro.Condicao("t1.c2", "contem", ["O'Brien"])) == "nome ILIKE '%O''Brien%'"
    assert _w(filtro.Condicao("t1.c2", "comeca", ["100%"])) == "nome ILIKE E'100\\\\%%'"
    assert _w(filtro.Condicao("t1.c2", "termina", ["a_b"])) == "nome ILIKE E'%a\\\\_b'"
    assert _w(filtro.Condicao("t1.c2", "contem", ["a\\b"])) == "nome ILIKE E'%a\\\\\\\\b%'"
    assert _w(filtro.Condicao("t1.c2", "contem", ["João"], diferenciar=True)) == "nome LIKE '%João%'"
    assert _w(filtro.Condicao("t1.c2", "igual_sem_caixa", ["ÁGUA'"])) == "lower(nome) = lower('ÁGUA''')"
    assert _w(filtro.Condicao("t1.c2", "igual", ["a'; DROP TABLE x; --"])) == "nome = 'a''; DROP TABLE x; --'"
    assert _w(filtro.Condicao("t1.c2", "diferente", ["José"])) == "nome <> 'José'"


def test_entre_lista_e_transformacoes():
    assert _w(filtro.Condicao("t1.c3", "entre", ["18", "65"])) == "idade BETWEEN 18 AND 65"
    assert _w(filtro.Condicao("t1.c2", "na_lista", ["a'b", "c"])) == "nome IN ('a''b', 'c')"
    assert _w(filtro.Condicao("t1.c4", "fora_lista", ["x@y", ""])) == "email NOT IN ('x@y', NULL)"
    assert _w(filtro.Condicao("t1.c2", "maior", ["5"], transformacao="tamanho")) == "length(nome) > 5"
    assert _w(filtro.Condicao("t1.c2", "contem", ["a"], transformacao="trim")) == "trim(nome) ILIKE '%a%'"
    assert _w(filtro.Condicao("t1.c2", "igual", ["ana"], transformacao="minusculas")) == "lower(nome) = 'ana'"
    assert _w(filtro.Condicao("t1.c5", "igual", ["3"], transformacao="dia_semana")) == "extract(isodow FROM nascimento) = 3"
    assert _w(filtro.Condicao("t1.c5", "entre", ["1990", "1999"], transformacao="ano")) == \
        "extract(year FROM nascimento) BETWEEN 1990 AND 1999"
    assert _w(filtro.Condicao("t1.c5", "antes", ["2000-01-01"])) == "nascimento < '2000-01-01'"
    assert _w(filtro.Condicao("t1.c5", "nos_ultimos", ["2", "meses"])) == "nascimento >= now() - interval '2 months'"
    assert _w(filtro.Condicao("t1.c4", "preenchido")) == "(email IS NOT NULL AND trim(email) <> '')"
    assert _w(filtro.Condicao("t1.c3", "preenchido")) == "idade IS NOT NULL"
    assert _w(filtro.Condicao("t1.c6", "falso")) == "ativo IS FALSE"
    assert _w(filtro.Condicao("t1.c4", "vazio", negar=True), filtro.Condicao("t1.c3", "menor", ["3"])) == \
        "(NOT (email IS NULL OR trim(email) = '')) AND (idade < 3)"
    assert _w(filtro.Condicao("t1.c4", "vazio"), filtro.Condicao("t1.c3", "menor", ["3"])) == \
        "(email IS NULL OR trim(email) = '') AND (idade < 3)"
    assert _w() == "" and _w(filtro.Condicao("t1.c3", "maior", ["1"]), conectivo="OU") == "idade > 1"


def test_outra_coluna_e_existe_com_condicoes():
    assert _w(filtro.Condicao("t1.c3", "maior", outra_coluna="t1.c1")) == "idade > id"
    f = filtro.Condicao("", "existe", existe=filtro.Existe("k1", [filtro.Condicao("t2.c3", "maior", ["15"]),
                                                                 filtro.Condicao("t2.c3", "menor", ["99"])]))
    assert _w(f) == ("EXISTS (SELECT 1 FROM public.pedidos WHERE public.pedidos.pessoa_id = public.pessoas.id "
                     "AND (public.pedidos.total > 15) AND (public.pedidos.total < 99))")
    g = filtro.Filtro([filtro.Condicao("", "existe", existe=filtro.Existe("k1", [filtro.Condicao("t1.c2", "comeca", ["A"])]))])
    assert filtro.where(g, modelo_pessoas(), "t2") == (
        "EXISTS (SELECT 1 FROM public.pessoas WHERE public.pessoas.id = public.pedidos.pessoa_id "
        "AND public.pessoas.nome ILIKE 'A%')")
    assert _w(f, filtro.Condicao("t1.c3", "maior", ["1"])).startswith("(EXISTS (SELECT 1")


def test_erros_viram_value_error():
    ruins = [filtro.Condicao("t1.c3", "contem", ["a"]), filtro.Condicao("t1.zz", "igual", ["a"]),
             filtro.Condicao("t2.c3", "igual", ["1"]),
             filtro.Condicao("t1.c3", "igual", ["1,5"]), filtro.Condicao("t1.c3", "igual"),
             filtro.Condicao("t1.c3", "entre", ["1"]), filtro.Condicao("t1.c3", "na_lista"),
             filtro.Condicao("t1.c5", "igual", ["6"], transformacao="trim"),
             filtro.Condicao("t1.c5", "igual", ["13"], transformacao="mes"),
             filtro.Condicao("t1.c5", "igual", ["8"], transformacao="dia_semana"),
             filtro.Condicao("t1.c5", "ha_mais_de", ["-3", "dias"]), filtro.Condicao("t1.c5", "ha_mais_de", ["3", "luas"]),
             filtro.Condicao("t1.c5", "ha_mais_de", ["3", "days'"]), filtro.Condicao("t1.c6", "igual", ["talvez"]),
             filtro.Condicao("t1.c2", "igual", ["a\x00b"]), filtro.Condicao("t1.c2", "contem", ["a"], outra_coluna="t1.c4"),
             filtro.Condicao("", "existe", existe=filtro.Existe("k9")), filtro.Condicao("t1.c2", "existe")]
    for c in ruins:
        with pytest.raises(ValueError):
            _w(c)
    with pytest.raises(ValueError):
        _w(filtro.Condicao("t1.c3", "maior", ["1"]), conectivo="XOR")
    with pytest.raises(ValueError):
        filtro.de_dict({"condicoes": [], "estranho": 1})
    with pytest.raises(ValueError):
        filtro.de_dict({"condicoes": [{"coluna": "t1.c1", "operador": "igual", "xis": 1}]})


def test_texto_livre_rigoroso():
    for ok in ("nome = 'a;b'", "nome = 'a--b'", "nome = 'it''s /* ok'", "(idade > 1 OR idade < 0)", "nome = $$a;b$$"):
        filtro.validar_texto(ok)
    for ruim in ("", "   ", "nome = 'a", "1=1) OR (1=1", "(1=1", "true) UNION SELECT 1 WHERE (true",
                 "nome = $$a", "1=1\n--x", "1=1;"):
        with pytest.raises(ValueError):
            filtro.validar_texto(ruim)


def test_tipos_familia_e_transformacoes():
    assert filtro.familia("character varying(20)") == "texto" and filtro.familia("bigserial") == "numero"
    assert filtro.familia("numeric(10,2)") == "numero" and filtro.familia("timestamptz") == "data"
    assert filtro.familia("date") == "data" and filtro.familia("bool") == "booleano"
    assert filtro.familia("uuid") == "outro" and filtro.familia("integer[]") == "outro" and filtro.familia("meu_enum") == "outro"
    assert filtro.transformacoes("text") == ["minusculas", "trim", "tamanho"]
    assert filtro.transformacoes("date") == ["mes", "ano", "dia_semana", "dia"]
    assert filtro.transformacoes("timestamp") == ["mes", "ano", "dia_semana", "dia", "so_data"]
    assert filtro.transformacoes("integer") == [] and filtro.transformacoes("uuid") == []
    assert filtro.operadores("text")[-4:] == ["contem", "comeca", "termina", "igual_sem_caixa"]
    assert filtro.operadores("timestamp", "so_data")[-2:] == ["ha_mais_de", "nos_ultimos"]
    assert filtro.operadores("text", "tamanho")[-1] == "entre"
    assert filtro.operadores("uuid") == ["igual", "diferente", "vazio", "preenchido", "na_lista", "fora_lista"]
    assert filtro.literal("3.5", "numeric") == "3.5" and filtro.literal("-2", "bigint") == "-2"
    assert filtro.literal("não", "boolean") == "FALSE" and filtro.literal("550e8400", "uuid") == "'550e8400'"
    assert filtro.literal("2020-01-02", "date") == "'2020-01-02'"


def test_descricao_refere_e_vazio():
    m = modelo_pessoas()
    f = filtro.Filtro([filtro.Condicao("t1.c2", "comeca", ["G"]),
                       filtro.Condicao("t1.c5", "igual", ["6"], transformacao="mes", negar=True)])
    d = filtro.descricao(f, m, "t1")
    assert "nome" in d and "G" in d and "junho" in d
    assert filtro.Filtro().vazio() and not f.vazio() and not filtro.Filtro(texto="a=1").vazio()
    assert filtro.refere(f, "t1") == {"t1", "t1.c2", "t1.c5"} and filtro.refere(filtro.Filtro(texto="x"), "t1") == {"t1"}
    assert filtro.deixa_vazios_de_fora(filtro.Filtro(texto="x"), m, "t1") == []
    ex = filtro.Condicao("", "existe", existe=filtro.Existe("k1", nao=True))
    assert "pedidos" in filtro.descricao_condicao(ex, m, "t1")


def test_barra_invertida_sai_em_string_e():
    assert filtro.literal("C:\\x", "text") == "E'C:\\\\x'" and filtro.literal("a'b\\", "text") == "E'a''b\\\\'"
    assert filtro.literal("sem barra", "text") == "'sem barra'"
    assert _w(filtro.Condicao("t1.c2", "igual", ["C:\\x"])) == "nome = E'C:\\\\x'"
    for op, esperado in (("contem", "E'%50\\\\%%'"), ("comeca", "E'50\\\\%%'"), ("termina", "E'%50\\\\%'")):
        assert _w(filtro.Condicao("t1.c2", op, ["50%"])) == f"nome ILIKE {esperado}"
    assert filtro.padrao_like("C:\\x", "termina") == "E'%C:\\\\\\\\x'"
    assert _w(filtro.Condicao("t1.c2", "na_lista", ["a\\", "b"])) == "nome IN (E'a\\\\', 'b')"


def test_e_string_depois_de_nome_com_dolar_ou_acento():
    from ratt.db import rodar
    for nome in ("a$", "café", "x$y"):
        sql = f"SELECT {nome}E'\\'; SELECT 2"       # identificador + string comum: a barra não escapa a aspa
        assert [c.texto for c in rodar.dividir(sql)] == [f"SELECT {nome}E'\\'", "SELECT 2"], nome
        with pytest.raises(ValueError):                 # barra numa string comum: o sentido depende do servidor
            filtro.validar_texto(f"{nome}E'\\' = 'x'")
    assert [c.texto for c in rodar.dividir("SELECT E'\\'; SELECT 2")] == ["SELECT E'\\'; SELECT 2"]   # E de verdade
    with pytest.raises(ValueError):
        filtro.validar_texto("E'\\'; DROP TABLE x")


def test_so_digitos_ascii():
    for ruim in ("٣", "１２"):
        with pytest.raises(ValueError):
            filtro.literal(ruim, "integer")
        with pytest.raises(ValueError):
            _w(filtro.Condicao("t1.c5", "igual", [ruim], transformacao="mes"))
        with pytest.raises(ValueError):
            _w(filtro.Condicao("t1.c5", "ha_mais_de", [ruim, "dias"]))


def test_vazio_na_lista_e_avisos_revistos():
    assert _w(filtro.Condicao("t1.c3", "na_lista", ["30", ""])) == "idade IN (30, NULL)"
    assert _w(filtro.Condicao("t1.c3", "fora_lista", ["", "7"])) == "idade NOT IN (NULL, 7)"
    m = modelo_pessoas()
    for op in ("vazio", "preenchido"):
        assert filtro.deixa_vazios_de_fora(filtro.Filtro([filtro.Condicao("t1.c3", op, negar=True)]), m, "t1") == []
    assert filtro.deixa_vazios_de_fora(filtro.Filtro([filtro.Condicao("t1.c6", "verdadeiro", negar=True)]), m, "t1") == []
    f = filtro.Filtro([filtro.Condicao("t1.c2", "igual", outra_coluna="t1.c4")])      # email pode ser NULL
    assert filtro.deixa_vazios_de_fora(f, m, "t1") == ["t1.c4"]
    f = filtro.Filtro([filtro.Condicao("t1.c3", "menor", outra_coluna="t1.c1")])
    assert filtro.deixa_vazios_de_fora(f, m, "t1") == ["t1.c3"]


# ── as operações de dados no rascunho ──

def test_dolar_antes_de_dolar_tag_nao_abre_texto():
    from ratt.db import rodar
    assert [c.texto for c in rodar.dividir("SELECT a$$b$; SELECT 2")] == ["SELECT a$$b$", "SELECT 2"]
    assert [c.texto for c in rodar.dividir("SELECT $b$;$b$; SELECT 2")] == ["SELECT $b$;$b$", "SELECT 2"]


def test_inserir_sql():
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.Inserir("t1", {"t1.c2": "O'Brien", "t1.c3": "51", "t1.c4": None}))
    assert "INSERT INTO public.pessoas (nome, idade, email) VALUES ('O''Brien', 51, NULL);" in r.sql_texto()


def test_inserir_sem_valores_e_so_com_nome():
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.Inserir("t1", {}))
    r.acrescentar(rascunho.Inserir("t1", {"t1.c2": "Zé"}))
    sql = r.sql_texto()
    assert "INSERT INTO public.pessoas DEFAULT VALUES;" in sql
    assert "INSERT INTO public.pessoas (nome) VALUES ('Zé');" in sql


def test_alterar_limpeza_pula_quem_nao_muda():
    r = rascunho.Rascunho(modelo_pessoas())
    f = filtro.Filtro([filtro.Condicao("t1.c4", "preenchido")])
    r.acrescentar(rascunho.Alterar("t1", f, {"t1.c4": rascunho.Mudanca("minusculas"), "t1.c2": rascunho.Mudanca("trim")}))
    assert ("UPDATE public.pessoas SET email = lower(email), nome = trim(nome) WHERE (email IS NOT NULL AND trim(email) <> '') "
            "AND (email IS DISTINCT FROM lower(email) OR nome IS DISTINCT FROM trim(nome));") in r.sql_texto()


def test_alterar_filtro_ou_vai_entre_parenteses_com_a_limpeza():
    r = rascunho.Rascunho(modelo_pessoas())
    f = filtro.Filtro([filtro.Condicao("t1.c2", "comeca", ["G"]), filtro.Condicao("t1.c3", "maior", ["40"])], conectivo="OU")
    r.acrescentar(rascunho.Alterar("t1", f, {"t1.c2": rascunho.Mudanca("trim")}))
    assert ("WHERE ((nome ILIKE 'G%') OR (idade > 40)) AND (nome IS DISTINCT FROM trim(nome));") in r.sql_texto()


def test_alterar_outras_mudancas():
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.Alterar("t1", filtro.Filtro(), {
        "t1.c3": rascunho.Mudanca("valor", "50"), "t1.c4": rascunho.Mudanca("vazio"),
        "t1.c2": rascunho.Mudanca("coluna", coluna="t1.c4")}))
    r.acrescentar(rascunho.Alterar("t1", filtro.Filtro(), {"t1.c4": rascunho.Mudanca("branco_vazio"),
                                                            "t1.c2": rascunho.Mudanca("maiusculas")}))
    sql = r.sql_texto()
    assert ("UPDATE public.pessoas SET idade = 50, email = NULL, nome = email WHERE idade IS DISTINCT FROM 50 "
            "OR email IS DISTINCT FROM NULL OR nome IS DISTINCT FROM email;") in sql
    bv = "CASE WHEN trim(email) = '' THEN NULL ELSE email END"
    assert (f"UPDATE public.pessoas SET email = {bv}, nome = upper(nome) WHERE "
            f"email IS DISTINCT FROM {bv} OR nome IS DISTINCT FROM upper(nome);") in sql


def test_alterar_misto_pula_quem_nao_muda_nenhuma_coluna():
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.Alterar("t1", filtro.Filtro(), {"t1.c3": rascunho.Mudanca("valor", "99"),
                                                            "t1.c2": rascunho.Mudanca("trim")}))
    assert ("UPDATE public.pessoas SET idade = 99, nome = trim(nome) WHERE idade IS DISTINCT FROM 99 "
            "OR nome IS DISTINCT FROM trim(nome);") in r.sql_texto()


def test_alterar_sem_mudancas_ou_tipo_ruim_nao_entra():
    r = rascunho.Rascunho(modelo_pessoas())
    with pytest.raises(ValueError):
        r.acrescentar(rascunho.Alterar("t1", filtro.Filtro(), {}))
    with pytest.raises(ValueError):
        r.acrescentar(rascunho.Alterar("t1", filtro.Filtro(), {"t1.c3": rascunho.Mudanca("valor", "vinte")}))
    with pytest.raises(ValueError):
        r.acrescentar(rascunho.Alterar("t1", filtro.Filtro(), {"t1.c3": rascunho.Mudanca("magia")}))
    with pytest.raises(ValueError):
        r.acrescentar(rascunho.Alterar("t1", filtro.Filtro(), {"t1.c3": rascunho.Mudanca("trim")}))   # trim em número
    assert r.operacoes == []


def test_apagar_e_duplicatas():
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.Apagar("t1", filtro.Filtro([filtro.Condicao("t1.c2", "comeca", ["G"])]), vista=3))
    r.acrescentar(rascunho.ApagarDuplicatas("t1", filtro.Filtro(), ("t1.c4",), normalizar=True))
    sql = r.sql_texto()
    assert "DELETE FROM public.pessoas WHERE nome ILIKE 'G%';" in sql
    assert ("DELETE FROM public.pessoas WHERE (id) IN (SELECT id FROM (SELECT id, row_number() OVER "
            "(PARTITION BY lower(trim(email)) ORDER BY id) AS _ratt_rn FROM public.pessoas WHERE email IS NOT NULL "
            "AND trim(email) <> '') AS d WHERE d._ratt_rn > 1);") in sql


def test_apagar_sem_filtro():
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.Apagar("t1", filtro.Filtro()))
    assert "DELETE FROM public.pessoas;" in r.sql_texto()


def test_duplicatas_maior_recente_n_e_ctid():
    r = rascunho.Rascunho(modelo_pessoas())
    f = filtro.Filtro([filtro.Condicao("t1.c6", "verdadeiro")])
    r.acrescentar(rascunho.ApagarDuplicatas("t1", f, ("t1.c2", "t1.c3"), manter="maior"))
    r.acrescentar(rascunho.ApagarDuplicatas("t1", filtro.Filtro(), ("t1.c2",), manter="recente", ordem="t1.c5", n=2))
    m = modelo_pessoas()
    m.tabelas["t1"].colunas[0].pk = False
    r2 = rascunho.Rascunho(m)
    r2.acrescentar(rascunho.ApagarDuplicatas("t1", filtro.Filtro(), ("t1.c2",)))
    sql = r.sql_texto()
    assert ("DELETE FROM public.pessoas WHERE (id) IN (SELECT id FROM (SELECT id, row_number() OVER "
            "(PARTITION BY nome, idade ORDER BY id DESC) AS _ratt_rn FROM public.pessoas WHERE nome IS NOT NULL AND "
            "idade IS NOT NULL AND ativo IS TRUE) AS d WHERE d._ratt_rn > 1);") in sql
    assert "(PARTITION BY nome ORDER BY nascimento DESC NULLS LAST, id) AS _ratt_rn" in sql and "d._ratt_rn > 2);" in sql
    assert ("DELETE FROM public.pessoas WHERE (tableoid, ctid) IN (SELECT tableoid, ctid FROM (SELECT tableoid, ctid, "
            "row_number() OVER (PARTITION BY nome ORDER BY tableoid, ctid) AS _ratt_rn FROM public.pessoas WHERE "
            "nome IS NOT NULL) AS d WHERE d._ratt_rn > 1);") in r2.sql_texto()


def test_duplicatas_filtro_ou_vai_entre_parenteses():
    r = rascunho.Rascunho(modelo_pessoas())
    f = filtro.Filtro([filtro.Condicao("t1.c2", "comeca", ["G"]), filtro.Condicao("t1.c3", "maior", ["40"])], conectivo="OU")
    r.acrescentar(rascunho.ApagarDuplicatas("t1", f, ("t1.c2",)))
    assert "WHERE nome IS NOT NULL AND ((nome ILIKE 'G%') OR (idade > 40))) AS d" in r.sql_texto()


def test_duplicatas_refere_a_ordem_so_quando_recente_e_pk_apagada_invalida():
    f = filtro.Filtro()
    assert "t1.c5" not in rascunho.ApagarDuplicatas("t1", f, ("t1.c4",), ordem="t1.c5").refere()
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.ApagarDuplicatas("t1", f, ("t1.c4",)))
    r.base.tabelas["t1"].colunas[0].estado = "apagado"
    assert r.invalidas == {0}


def test_json_com_campos_de_tipo_errado_e_rejeitado():
    for extra in ({"vista": -1}, {"vista": True}, {"vista": "3"}, {"normalizar": "sim"}, {"manter": "xis"},
                  {"n": 0}, {"manter": "recente"}):
        op = {"tipo": "apagar_duplicatas", "tabela": "t1", "filtro": {}, "por": ["t1.c4"]}
        op.update(extra)
        with pytest.raises(rascunho.RascunhoCorrompido):
            rascunho.Rascunho.de_dict({"operacoes": [op]}, modelo_pessoas())


def test_descricao_do_inserir_no_singular():
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.Inserir("t1", {"t1.c2": "x"}))
    r.acrescentar(rascunho.Inserir("t1", {"t1.c2": "x", "t1.c3": "1"}))
    d = [p[2] for p in r.passos()]
    assert "(1 valor)" in d[0] and "(2 valores)" in d[1]


def test_duplicatas_argumentos_ruins():
    r = rascunho.Rascunho(modelo_pessoas())
    for kw in ({"manter": "menor", "n": 0}, {"manter": "recente"}, {"manter": "xis"}):
        with pytest.raises(ValueError):
            r.acrescentar(rascunho.ApagarDuplicatas("t1", filtro.Filtro(), ("t1.c4",), **kw))
    with pytest.raises(ValueError):
        r.acrescentar(rascunho.ApagarDuplicatas("t1", filtro.Filtro(), ()))
    assert r.operacoes == []


def test_dados_seguem_renomeacao_e_invalidam_coluna_apagada():
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.Renomear("t1", "t1.c4", "e_mail"))
    r.acrescentar(rascunho.Apagar("t1", filtro.Filtro([filtro.Condicao("t1.c4", "vazio")])))
    assert "DELETE FROM public.pessoas WHERE (e_mail IS NULL OR trim(e_mail) = '');" in r.sql_texto()
    r2 = rascunho.Rascunho(modelo_pessoas())
    r2.acrescentar(rascunho.ApagarColuna("t1", "t1.c4"))
    with pytest.raises(ValueError):                                   # email já não existe na vista
        r2.acrescentar(rascunho.Apagar("t1", filtro.Filtro([filtro.Condicao("t1.c4", "vazio")])))


def test_refere_e_dependencias_das_operacoes_de_dados():
    f = filtro.Filtro([filtro.Condicao("t1.c3", "maior", ["1"])])
    assert rascunho.Inserir("t1", {"t1.c2": "x"}).refere() == {"t1", "t1.c2"}
    assert rascunho.Alterar("t1", f, {"t1.c2": rascunho.Mudanca("coluna", coluna="t1.c4")}).refere() == \
        {"t1", "t1.c2", "t1.c4", "t1.c3"}
    assert rascunho.ApagarDuplicatas("t1", f, ("t1.c4",), manter="recente", ordem="t1.c5").refere() == \
        {"t1", "t1.c4", "t1.c5", "t1.c3"}
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.AdicionarColuna("t1", Coluna("novo-c1", "apelido", "text")))
    r.acrescentar(rascunho.Inserir("t1", {"novo-c1": "Gabi"}))
    assert r.dependentes(0) == [1]
    assert "INSERT INTO public.pessoas (apelido) VALUES ('Gabi');" in r.sql_texto()
    r.tirar([0])                                    # sem a coluna, a inserção deixa de valer
    assert r.invalidas == {0}


def test_descricoes_de_uma_linha():
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.Inserir("t1", {"t1.c2": "x"}))
    r.acrescentar(rascunho.Alterar("t1", filtro.Filtro(), {"t1.c2": rascunho.Mudanca("trim")}))
    r.acrescentar(rascunho.Apagar("t1", filtro.Filtro()))
    r.acrescentar(rascunho.ApagarDuplicatas("t1", filtro.Filtro(), ("t1.c4",)))
    for _, valida, desc, _ in r.passos():
        assert valida and desc and "\n" not in desc and "op." not in desc


def test_json_das_operacoes_de_dados():
    r = rascunho.Rascunho(modelo_pessoas())
    f = filtro.Filtro([filtro.Condicao("t1.c2", "comeca", ["G"]), filtro.Condicao("t1.c3", "maior", ["1"])], conectivo="OU")
    r.acrescentar(rascunho.Inserir("t1", {"t1.c2": "O'Brien", "t1.c4": None}))
    r.acrescentar(rascunho.Alterar("t1", f, {"t1.c2": rascunho.Mudanca("trim"), "t1.c3": rascunho.Mudanca("valor", "5"),
                                             "t1.c4": rascunho.Mudanca("coluna", coluna="t1.c2")}, vista=4))
    r.acrescentar(rascunho.Apagar("t1", f, vista=2))
    r.acrescentar(rascunho.ApagarDuplicatas("t1", f, ("t1.c4", "t1.c2"), True, "recente", "t1.c5", 2, vista=7))
    antes = r.sql_texto()
    lido = rascunho.Rascunho.de_dict(json.loads(json.dumps(r.para_dict())), modelo_pessoas())
    assert lido.sql_texto() == antes and not lido.invalidas
    assert [op.tipo for op in lido.operacoes] == ["inserir", "alterar", "apagar", "apagar_duplicatas"]
    assert lido.operacoes[1].vista == 4 and lido.operacoes[3].por == ("t1.c4", "t1.c2")
    assert lido.operacoes[1].mudancas["t1.c4"] == rascunho.Mudanca("coluna", coluna="t1.c2")
    assert lido.operacoes[0].nomes == r.operacoes[0].nomes


def test_json_de_dados_estragado_e_rascunho_antigo():
    base = rascunho.Rascunho(modelo_pessoas())
    base.acrescentar(rascunho.Renomear("t1", None, "gente"))
    assert rascunho.Rascunho.de_dict(base.para_dict(), modelo_pessoas()).sql_texto() == base.sql_texto()
    ruins = [{"tipo": "inserir", "tabela": "t1", "valores": [1]},
             {"tipo": "alterar", "tabela": "t1", "filtro": {"coisa": 1}, "mudancas": {}},
             {"tipo": "alterar", "tabela": "t1", "filtro": {}, "mudancas": {"t1.c2": 5}},
             {"tipo": "apagar", "tabela": "t1", "filtro": 3},
             {"tipo": "apagar_duplicatas", "tabela": "t1", "filtro": {}, "por": 7}]
    for op in ruins:
        with pytest.raises(rascunho.RascunhoCorrompido):
            rascunho.Rascunho.de_dict({"operacoes": [op]}, modelo_pessoas())


# ── contagens e avisos das operações de dados (só leitura) ──

def _chaves(avisos):
    return [(a.nivel, a.chave) for a in avisos]


def _G():
    return filtro.Filtro([filtro.Condicao("t1.c2", "comeca", ["G"])])


def test_onde_e_o_mesmo_texto_do_comando():
    m = modelo_pessoas()
    f = filtro.Filtro([filtro.Condicao("t1.c2", "comeca", ["G"]), filtro.Condicao("t1.c3", "maior", ["40"])], conectivo="OU")
    alt = rascunho.Alterar("t1", f, {"t1.c2": rascunho.Mudanca("trim")})
    assert alt.onde(m) == "((nome ILIKE 'G%') OR (idade > 40)) AND (nome IS DISTINCT FROM trim(nome))"
    assert alt.comandos(m) == [f"UPDATE public.pessoas SET nome = trim(nome) WHERE {alt.onde(m)}"]
    assert alt.sets(m) == {"nome": "trim(nome)"}
    ap = rascunho.Apagar("t1", f)
    assert ap.comandos(m) == [f"DELETE FROM public.pessoas WHERE {ap.onde(m)}"]
    assert rascunho.Apagar("t1", filtro.Filtro()).onde(m) == ""
    dup = rascunho.ApagarDuplicatas("t1", f, ("t1.c4",), normalizar=True)
    assert dup.onde(m).startswith("(id) IN (SELECT id FROM (SELECT id, row_number() OVER (PARTITION BY lower(trim(email))")
    assert dup.comandos(m) == [f"DELETE FROM public.pessoas WHERE {dup.onde(m)}"]
    assert dup.por_sql(m) == ["lower(trim(email))"]
    assert dup.onde_comparadas(m) == "email IS NOT NULL AND trim(email) <> '' AND ((nome ILIKE 'G%') OR (idade > 40))"
    assert dup.onde_fora(m) == "NOT (email IS NOT NULL AND trim(email) <> '') AND ((nome ILIKE 'G%') OR (idade > 40))"


def test_avisos_contam_com_o_onde_da_operacao():
    r = rascunho.Rascunho(modelo_pessoas())
    for op in (rascunho.Alterar("t1", _G(), {"t1.c3": rascunho.Mudanca("valor", "7")}), rascunho.Apagar("t1", _G()),
               rascunho.ApagarDuplicatas("t1", _G(), ("t1.c4",))):
        falsa = ConsultaFalsa(contar_onde=4, grupos_repetidos=(1, 1))
        avisos = checagem.avisos_da_operacao(op, r, falsa)
        assert ("contar_onde", ("public", "pessoas", op.onde(r.base))) in falsa.chamadas
        assert checagem.contagem(avisos) == 4
        assert all(nome != "contar_onde" or args[2] in (op.onde(r.base), getattr(op, "onde_fora", lambda m: None)(r.base))
                   or args[2].startswith("(" + op.onde(r.base) + ")") for nome, args in falsa.chamadas)


def test_apagar_com_filhos_que_impedem_e_sugestao_de_soft_delete():
    r = rascunho.Rascunho(modelo_pessoas())
    falsa = ConsultaFalsa(contar_onde=1, cascata=[("public.pedidos", "NO ACTION", 2)])
    avisos = checagem.avisos_da_operacao(rascunho.Apagar("t1", _G()), r, falsa)
    impede = [a for a in avisos if a.chave == "chk.impede_filhos"]
    assert [(a.nivel, a.valores["n"], a.valores["tabela"]) for a in impede] == [("impede", 2, "public.pedidos")]
    assert ("info", "chk.soft_delete") in _chaves(avisos)                 # pessoas tem `ativo`
    nome, args = [c for c in falsa.chamadas if c[0] == "cascata"][0]
    assert args == ("public", "pessoas", "nome ILIKE 'G%'")
    for acao, nivel, chave in (("CASCADE", "atencao", "chk.cascata_apaga"), ("SET NULL", "atencao", "chk.cascata_esvazia"),
                               ("SET DEFAULT", "atencao", "chk.cascata_padrao"), ("RESTRICT", "impede", "chk.impede_filhos"),
                               (checagem.NULO_EM_OBRIGATORIA, "impede", "chk.cascata_nulo_impede")):
        avisos = checagem.avisos_da_operacao(rascunho.Apagar("t1", _G()), r,
                                             ConsultaFalsa(contar_onde=1, cascata=[("public.pedidos", acao, 3)]))
        assert (nivel, chave) in _chaves(avisos), acao
    incompleta = checagem.Cascata([("public.pedidos", "CASCADE", 3)], incompleta=True, sem_permissao=["outro.x"])
    avisos = checagem.avisos_da_operacao(rascunho.Apagar("t1", _G()), r, ConsultaFalsa(contar_onde=1, cascata=incompleta))
    assert {("atencao", "chk.cascata_apaga_pelo_menos"), ("atencao", "chk.cascata_incompleta"),
            ("atencao", "chk.sem_permissao_ver")} <= set(_chaves(avisos))
    avisos = checagem.avisos_da_operacao(rascunho.Apagar("t1", _G()), r,
                                         ConsultaFalsa(contar_onde=1, cascata=[("public.pedidos", "RESTRICT", 0)]))
    assert not any(a.chave == "chk.impede_filhos" for a in avisos)
    for a in avisos:
        assert a.texto() and "chk." not in a.texto()


def test_inserir_obrigatoria_vazia_e_valores():
    r = rascunho.Rascunho(modelo_pessoas())
    avisos = checagem.avisos_da_operacao(rascunho.Inserir("t1", {"t1.c3": "30"}), r, ConsultaFalsa(valor_valido=True))
    assert ("impede", "chk.obrigatoria_vazia") in _chaves(avisos) and avisos[0].valores["coluna"] == "nome"
    avisos = checagem.avisos_da_operacao(rascunho.Inserir("t1", {"t1.c2": None}), r, ConsultaFalsa())
    assert ("impede", "chk.obrigatoria_vazia") in _chaves(avisos)        # NULL dito também é vazio
    falsa = ConsultaFalsa(valor_valido=False, ja_existe=1)
    avisos = checagem.avisos_da_operacao(rascunho.Inserir("t1", {"t1.c2": " Zé ", "t1.c5": "2026-02-30",
                                                                 "t1.c4": "ana@x.com"}), r, falsa)
    ch = _chaves(avisos)
    assert ("atencao", "chk.espacos_valor") in ch and ("impede", "chk.valor_invalido") in ch
    assert ("impede", "chk.ja_existe") in ch and ("impede", "chk.obrigatoria_vazia") not in ch
    assert ("valor_valido", ("date", "2026-02-30")) in falsa.chamadas
    assert ("ja_existe", ("public", "pessoas", "email", "'ana@x.com'")) in falsa.chamadas
    avisos = checagem.avisos_da_operacao(rascunho.Inserir("t1", {"t1.c2": "x", "t1.c3": "vinte"}), r, ConsultaFalsa())
    assert ("impede", "chk.valor_invalido") in _chaves(avisos)          # o número nem chega ao banco


def test_inserir_fk_que_nao_existe_no_pai():
    r = rascunho.Rascunho(modelo_pessoas())
    falsa = ConsultaFalsa(valor_valido=True, ja_existe=0)
    avisos = checagem.avisos_da_operacao(rascunho.Inserir("t2", {"t2.c2": "99"}), r, falsa)
    assert ("impede", "chk.fk_inexistente") in _chaves(avisos)
    assert ("ja_existe", ("public", "pessoas", "id", "99")) in falsa.chamadas
    avisos = checagem.avisos_da_operacao(rascunho.Inserir("t2", {"t2.c2": "1"}), r, ConsultaFalsa(valor_valido=True, ja_existe=1))
    assert not any(a.nivel == "impede" for a in avisos)


def test_alterar_contagem_obrigatoria_e_unica():
    r = rascunho.Rascunho(modelo_pessoas())
    op = rascunho.Alterar("t1", _G(), {"t1.c4": rascunho.Mudanca("valor", "a@x.com"), "t1.c2": rascunho.Mudanca("vazio")})
    avisos = checagem.avisos_da_operacao(op, r, ConsultaFalsa(contar_onde=3, valor_valido=True))
    ch = _chaves(avisos)
    assert ("info", "chk.linhas_alteradas") in ch and checagem.contagem(avisos) == 3
    assert ("impede", "chk.unica_varias") in ch and ("impede", "chk.obrigatoria_recebe_vazio") in ch
    op = rascunho.Alterar("t1", _G(), {"t1.c4": rascunho.Mudanca("valor", "a@x.com")})
    falsa = ConsultaFalsa(contar_onde=1, valor_valido=True)
    avisos = checagem.avisos_da_operacao(op, r, falsa)
    assert ("impede", "chk.ja_existe") in _chaves(avisos) and ("impede", "chk.unica_varias") not in _chaves(avisos)
    # quem o próprio UPDATE pega não conta como "já existe": só as linhas fora do WHERE dele
    assert ("contar_onde", ("public", "pessoas", f"email = 'a@x.com' AND ({op.onde(r.base)}) IS NOT TRUE")) in falsa.chamadas
    op = rascunho.Alterar("t2", filtro.Filtro(), {"t2.c2": rascunho.Mudanca("valor", "42")})
    avisos = checagem.avisos_da_operacao(op, r, ConsultaFalsa(contar_onde=2, valor_valido=True, ja_existe=0))
    assert ("impede", "chk.fk_inexistente") in _chaves(avisos)
    op = rascunho.Alterar("t1", _G(), {"t1.c2": rascunho.Mudanca("coluna", coluna="t1.c4")})
    falsa = ConsultaFalsa(contar_onde=2)
    avisos = checagem.avisos_da_operacao(op, r, falsa)
    assert ("impede", "chk.obrigatoria_recebe_vazio_n") in _chaves(avisos)
    assert ("contar_onde", ("public", "pessoas", f"({op.onde(r.base)}) AND (email) IS NULL")) in falsa.chamadas


def test_duplicatas_grupos_ctid_e_vazias_fora():
    m = modelo_pessoas()
    m.tabelas["t1"].colunas[0].pk = False
    r = rascunho.Rascunho(m)
    op = rascunho.ApagarDuplicatas("t1", _G(), ("t1.c4",))
    falsa = ConsultaFalsa(contar_onde=2, grupos_repetidos=(1, 2), cascata=[])
    avisos = checagem.avisos_da_operacao(op, r, falsa)
    ch = _chaves(avisos)
    assert ("atencao", "chk.sem_pk_ctid") in ch and ("info", "chk.grupos_repetidos") in ch
    assert ("info", "chk.duplicatas_vazias_fora") in ch and checagem.contagem(avisos) == 2
    assert ("grupos_repetidos", ("public", "pessoas", op.onde_comparadas(r.base), ["email"])) in falsa.chamadas
    assert ("contar_onde", ("public", "pessoas", op.onde_fora(r.base))) in falsa.chamadas
    assert any(n == "cascata" for n, _ in falsa.chamadas)


def test_operacao_anterior_na_mesma_tabela_deixa_tudo_aproximado():
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.Inserir("t1", {"t1.c2": "Zé"}))
    ops = [rascunho.Inserir("t1", {"t1.c2": "Ana"}), rascunho.Alterar("t1", _G(), {"t1.c3": rascunho.Mudanca("valor", "1")}),
           rascunho.Apagar("t1", _G()), rascunho.ApagarDuplicatas("t1", _G(), ("t1.c4",))]
    for op in ops:
        avisos = checagem.avisos_da_operacao(op, r, ConsultaFalsa(valor_valido=True))
        aprox = [a for a in avisos if a.chave == "chk.aproximada"]
        assert len(aprox) == 1 and aprox[0].valores["n"] == 1 and aprox[0].texto()
    avisos = checagem.avisos_da_operacao(rascunho.Apagar("t2", filtro.Filtro()), r, ConsultaFalsa())
    assert not any(a.chave == "chk.aproximada" for a in avisos)          # outra tabela


def test_coluna_do_rascunho_so_depois_de_aplicar_sem_consultar():
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.AdicionarColuna("t1", Coluna("novo-c1", "apelido", "text")))
    f = filtro.Filtro([filtro.Condicao("novo-c1", "comeca", ["G"])])
    for op in (rascunho.Apagar("t1", f), rascunho.Alterar("t1", f, {"t1.c3": rascunho.Mudanca("valor", "1")}),
               rascunho.ApagarDuplicatas("t1", filtro.Filtro(), ("novo-c1",)), rascunho.Inserir("t1", {"t1.c2": "a", "novo-c1": "b"})):
        falsa = ConsultaFalsa()
        avisos = checagem.avisos_da_operacao(op, r, falsa)
        assert ("info", "chk.so_depois_de_aplicar") in _chaves(avisos)
        assert not any(a.chave == "chk.aproximada" for a in avisos)
        assert falsa.chamadas == []
    falsa = ConsultaFalsa()
    assert _chaves(checagem.avisos_do_filtro(f, r, "t1", falsa)) == [("info", "chk.so_depois_de_aplicar")]
    assert falsa.chamadas == []


def test_tabela_do_rascunho_so_depois_de_aplicar():
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.CriarTabela(Tabela("novo-t1", "public", "notas", [
        Coluna("novo-c2", "id", "bigint", obrigatoria=True, identidade=True, pk=True),
        Coluna("novo-c3", "texto", "text", obrigatoria=True)])))
    falsa = ConsultaFalsa()
    avisos = checagem.avisos_da_operacao(rascunho.Inserir("novo-t1", {}), r, falsa)
    assert _chaves(avisos) == [("impede", "chk.obrigatoria_vazia"), ("info", "chk.so_depois_de_aplicar")]
    assert falsa.chamadas == []


def test_sem_tempo_e_consulta_que_falhou():
    r = rascunho.Rascunho(modelo_pessoas())
    avisos = checagem.avisos_da_operacao(rascunho.Apagar("t1", _G()), r, ConsultaFalsa(contar_onde=None, cascata=None))
    assert ("info", "chk.sem_tempo") in _chaves(avisos) and checagem.contagem(avisos) is None
    avisos = checagem.avisos_da_operacao(rascunho.Apagar("t1", filtro.Filtro(texto="xis > 1")), r,
                                         ConsultaFalsa(contar_onde=None, cascata=[], erro="column \"xis\" does not exist"))
    falhou = [a for a in avisos if a.chave == "chk.consulta_falhou"]
    assert falhou and "xis" in falhou[0].texto() and falhou[0].nivel == "impede"   # o aplicar falharia igual


def test_avisos_do_filtro_sem_banco():
    r = rascunho.Rascunho(modelo_pessoas())
    f = filtro.Filtro([filtro.Condicao("t1.c3", "fora_lista", ["", "7"])])
    falsa = ConsultaFalsa(contar_onde=1)
    avisos = checagem.avisos_do_filtro(f, r, "t1", falsa)
    assert ("atencao", "chk.fora_lista_vazio") in _chaves(avisos)
    vazios = [a for a in avisos if a.chave == "chk.vazios_de_fora"]
    assert [(a.valores["n"], a.valores["coluna"]) for a in vazios] == [(1, "idade")]
    assert ("contar_onde", ("public", "pessoas", "idade IS NULL AND (idade NOT IN (NULL, 7)) IS NOT TRUE")) in falsa.chamadas
    falsa = ConsultaFalsa(contar_onde=1)
    avisos = checagem.avisos_do_filtro(_G(), r, "t1", falsa)
    assert _chaves(avisos) == [("atencao", "chk.espacos_nas_pontas")]
    assert falsa.chamadas == [("contar_onde", ("public", "pessoas",
                                              "(trim(nome) ILIKE 'G%') IS TRUE AND (nome ILIKE 'G%') IS NOT TRUE"))]
    assert checagem.avisos_do_filtro(_G(), r, "t1", ConsultaFalsa(contar_onde=0)) == []
    assert checagem.avisos_do_filtro(filtro.Filtro(texto="idade > 1"), r, "t1", ConsultaFalsa()) == []


def test_vazios_de_fora_so_conta_quem_sai_por_causa_da_condicao():
    r = rascunho.Rascunho(modelo_pessoas())
    idade, gi = filtro.Condicao("t1.c3", "diferente", ["30"]), filtro.Condicao("t1.c2", "comeca", ["Gi"])
    falsa = ConsultaFalsa(contar_onde=0)
    checagem.avisos_do_filtro(filtro.Filtro([idade, gi]), r, "t1", falsa)          # E: as outras condições valendo
    assert ("contar_onde", ("public", "pessoas", "(nome ILIKE 'Gi%') AND idade IS NULL AND "
                                                 "((idade <> 30) AND (nome ILIKE 'Gi%')) IS NOT TRUE")) in falsa.chamadas
    falsa = ConsultaFalsa(contar_onde=0)
    checagem.avisos_do_filtro(filtro.Filtro([idade, gi], conectivo="OU"), r, "t1", falsa)   # OU: a condição sozinha bastaria
    assert ("contar_onde", ("public", "pessoas", "idade IS NULL AND ((idade <> 30) OR (nome ILIKE 'Gi%')) IS NOT TRUE")) \
        in falsa.chamadas
    falsa = ConsultaFalsa(contar_onde=0)
    checagem.avisos_do_filtro(filtro.Filtro([_G().condicoes[0], idade], conectivo="OU"), r, "t1", falsa)
    assert ("contar_onde", ("public", "pessoas", "((trim(nome) ILIKE 'G%') OR (idade <> 30)) IS TRUE AND "
                                                 "((nome ILIKE 'G%') OR (idade <> 30)) IS NOT TRUE")) in falsa.chamadas


def test_barra_em_string_comum_e_recusada_no_texto_livre():
    for ruim in ("nome = 'a\\b'", "nome = '\\'' OR true); COMMIT; DELETE FROM public.pedidos; SELECT ('\\''",
                 "nome = U&'d\\0061t'"):
        with pytest.raises(ValueError):
            filtro.validar_texto(ruim)
    for bom in ("nome = E'a\\\\b'", "nome = E'it\\'s'", "nome = $$a\\b$$", '"co\\l" = 1', "nome = 'sem barra'"):
        filtro.validar_texto(bom)


def test_tipo_seguro_para_o_cast():
    for bom in ("date", "integer", "character varying(255)", "timestamp(3) with time zone", "numeric(10,2)[]",
                "double precision", '"MeuTipo"', "public.status", 'outro."Tipo ""x"""', "interval year to month", "bit(1)"):
        assert checagem.tipo_seguro(bom), bom
    for ruim in ("date); DROP TABLE x; --", "int'", '"a" b', "text, now()", "", "date -- x", "Date"):
        assert not checagem.tipo_seguro(ruim), ruim
