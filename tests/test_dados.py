"""Testes da tela Dados e do que ela divide com o canvas: a sessão de rascunho e o painel."""
import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from ratt.db import filtro, guardar, rascunho, sessao
from ratt.db.modelo import Coluna, Ligacao, Modelo, Tabela
from ratt.db.idioma import t

from test_canvas import AppTeste, FonteFalsa, modelo_png, pronto


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
