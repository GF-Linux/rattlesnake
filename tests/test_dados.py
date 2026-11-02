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


# ── a tela Dados (Task 7) ──

import datetime

import psycopg
from textual.app import App

from ratt.db import catalogo


class FonteDadosFalsa(FonteFalsa):
    """A fonte do canvas mais o que a grade lê: página e contagem fixas (as linhas de PESSOAS_SQL)."""
    NOMES = ["id", "nome", "idade", "email", "nascimento", "ativo"]
    TIPOS = ["int8", "text", "int4", "text", "date", "bool"]
    LINHAS = [(1, "Gabriel", 34, "gab@x.com", datetime.date(1990, 7, 14), True),
              (2, "Ana", 29, "ana@x.com", datetime.date(1996, 3, 2), True),
              (3, "Giovana", None, None, None, True),
              (4, " Gustavo", 40, "gus@x.com", datetime.date(1985, 11, 20), True),
              (5, "O'Brien", 51, "ob@x.com", datetime.date(1974, 6, 1), True)]
    FILTRADAS = {"nome ILIKE 'G%'": [0, 2], "(idade > 30)": [0, 3, 4], "idade = 34": [0]}

    def __init__(self, m=None):
        super().__init__(m or modelo_pessoas())
        self.escritas = 0
        self.chamadas = []
        self.tempo_esgotado = False
        self.sem_contagem = False
        self.oids = []

    def assinaturas(self, oid=None):
        """(estrutura, escritas DESTA tabela)."""
        self.oids.append(oid)
        return self.sig, self.escritas

    def colunas(self, oid):
        self.chamadas.append(("colunas", oid))
        return [catalogo.Coluna(1, "nome", "text", False, None, False, None),
                catalogo.Coluna(2, "idade", "integer", False, None, False, None)]

    def _linhas(self, onde):
        indices = self.FILTRADAS.get(onde)
        return list(self.LINHAS) if indices is None else [self.LINHAS[i] for i in indices]

    def pagina(self, esquema, tabela, onde, ordem, deslocamento, limite):
        self.chamadas.append(("pagina", tabela, onde, tuple(ordem), deslocamento))
        if self.tempo_esgotado:
            raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
        return list(self.NOMES), list(self.TIPOS), self._linhas(onde)[deslocamento:deslocamento + limite]

    def contar(self, esquema, tabela, onde):
        self.chamadas.append(("contar", tabela, onde))
        return None if self.sem_contagem else len(self._linhas(onde))

    PAIS = [(1, "Gabriel · gab@x.com"), (2, "Ana · ana@x.com"), (3, "Giovana")]

    def linhas_pai(self, esquema, tabela, chave, busca, limite=50):
        self.chamadas.append(("linhas_pai", esquema, tabela, chave, busca))
        if getattr(self, "busca_sem_tempo", False):
            return None
        return [p for p in self.PAIS if busca.lower() in p[1].lower() or busca == str(p[0])][:limite]


class AppDados(App):
    def __init__(self, fonte, tabela_id="t1", **kw):
        super().__init__()
        self.fonte, self.tabela_id, self.kw = fonte, tabela_id, kw

    def on_mount(self):
        from ratt.db import tela_dados
        self.push_screen(tela_dados.TelaDados(self.fonte, "public", self.tabela_id, **self.kw))


async def pronto_dados(app, voltas=4):
    """As leituras da tela Dados vêm em duas etapas (o modelo, depois a grade): espera as duas."""
    for _ in range(voltas):
        await app.workers.wait_for_complete()
        await asyncio.sleep(0.05)


def _texto(tela, seletor):
    return str(tela.query_one(seletor).render())


def test_tela_dados_abre_e_filtra_comeca_com(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Input, Select
    from ratt.db import formularios_dados

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            assert tela.total == 5 and len(tela.linhas) == 5
            assert t("dados.n_de_m", n=5, m=5) in _texto(tela, "#dados_contagem")
            assert ("pagina", "pessoas", "", ("id",), 0) in fonte.chamadas
            await piloto.press("f")
            await piloto.pause()
            form = app.screen
            assert isinstance(form, formularios_dados.FormCondicao)
            form.query_one("#coluna", Select).value = "t1.c2"
            await piloto.pause()
            form.query_one("#operador", Select).value = "comeca"
            await piloto.pause()
            form.query_one("#valor", Input).focus()
            await piloto.press("G")
            await piloto.pause()
            assert "nome ILIKE 'G%'" in _texto(form, "#previa")
            await piloto.click("#ok")
            await pronto_dados(app)
            assert app.screen is tela
            assert tela.filtro.condicoes == [filtro.Condicao("t1.c2", "comeca", ["G"])]
            assert filtro.where(tela.filtro, modelo_pessoas(), "t1") == "nome ILIKE 'G%'"
            assert filtro.descricao(tela.filtro, modelo_pessoas(), "t1") in _texto(tela, "#dados_filtro")
            assert "SELECT * FROM public.pessoas WHERE nome ILIKE 'G%'" in tela.query_one("#dados_sql").texto_plano()
            assert ("pagina", "pessoas", "nome ILIKE 'G%'", ("id",), 0) in fonte.chamadas
            assert [l[1] for l in tela.linhas] == ["Gabriel", "Giovana"] and tela.total == 2
            assert t("dados.n_de_m", n=2, m=5) in _texto(tela, "#dados_contagem")
            await piloto.press("o")                     # E/OU
            assert tela.filtro.conectivo == "OU"
            await piloto.press("backspace")             # tira a última condição
            await pronto_dados(app)
            assert tela.filtro.condicoes == [] and tela.total == 5
    asyncio.run(roteiro())


def test_where_em_texto_recusado_e_aceito(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Input
    from ratt.db import tela_dados
    from ratt.db.painel import PerguntarNome
    avisos = []
    monkeypatch.setattr(tela_dados.TelaDados, "notify", lambda self, msg, **kw: avisos.append(msg))

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            await piloto.press("t")
            await piloto.pause()
            assert isinstance(app.screen, PerguntarNome)
            app.screen.query_one(Input).value = "1=1; DROP TABLE x"
            await piloto.press("enter")
            await pronto_dados(app)
            assert tela.filtro.texto is None and tela.filtro.condicoes == []
            assert avisos and t("filtro.e.texto_ponto_virgula") in avisos[-1]
            assert isinstance(app.screen, PerguntarNome)                # pergunta de novo, com o texto para corrigir
            assert app.screen.query_one(Input).value == "1=1; DROP TABLE x"
            assert not any(c[0] == "pagina" and "DROP" in c[2] for c in fonte.chamadas)
            app.screen.query_one(Input).value = "idade > 30"
            await piloto.press("enter")
            await pronto_dados(app)
            assert app.screen is tela and tela.filtro.texto == "idade > 30"
            assert ("pagina", "pessoas", "(idade > 30)", ("id",), 0) in fonte.chamadas
            assert tela.total == 3 and "idade > 30" in _texto(tela, "#dados_filtro")
            assert "WHERE (idade > 30)" in tela.query_one("#dados_sql").texto_plano()
    asyncio.run(roteiro())


def _clicar_celula(tela, linha, coluna):
    grade = tela.query_one("#dados_grade")
    x = sum(c.get_render_width(grade) for c in grade.ordered_columns[:coluna]) + 1
    return grade, (x, grade.header_height + linha)


def test_clicar_numa_celula_abre_igual_a_este_valor(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Input, Select
    from ratt.db import formularios_dados

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            _, onde = _clicar_celula(tela, 0, 2)                    # idade do Gabriel
            await piloto.click("#dados_grade", offset=onde)
            await piloto.pause()
            form = app.screen
            assert isinstance(form, formularios_dados.FormCondicao)
            assert form.query_one("#coluna", Select).value == "t1.c3"
            assert form.query_one("#operador", Select).value == "igual"
            assert form.query_one("#valor", Input).value == "34"
            await piloto.click("#ok")
            await pronto_dados(app)
            assert tela.filtro.condicoes == [filtro.Condicao("t1.c3", "igual", ["34"])]
            assert [l[0] for l in tela.linhas] == [1]
    asyncio.run(roteiro())


def test_so_ver_nao_tem_teclas_de_alterar(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    colunas = [catalogo.Coluna(1, "nome", "text", False, None, False, None),
               catalogo.Coluna(2, "idade", "integer", False, None, False, None)]

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte, "t99", so_ver=True, colunas_view=colunas, nome="maiores")
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            assert t("dados.so_ver") in _texto(tela, "#dados_filtro")
            assert ("pagina", "maiores", "", (), 0) in fonte.chamadas
            for tecla in ("i", "m", "x", "D", "u", "e", "w", "enter"):
                await piloto.press(tecla)
                await piloto.pause()
                assert app.screen is tela, tecla
            assert not tela.sessao.rascunho.operacoes
            for acao in ("aplicar", "ensaio", "exportar", "desfazer"):
                assert not tela.check_action(acao, ())
            tela.acrescentar_condicao(filtro.Condicao("t99.c2", "maior", ["30"]))
            await pronto_dados(app)
            assert ("pagina", "maiores", "idade > 30", (), 0) in fonte.chamadas
    asyncio.run(roteiro())


def test_coluna_e_tabela_do_rascunho_so_depois_de_aplicar(monkeypatch, tmp_path):
    """Review Focus 3: a grade não dá erro, diz "só depois de aplicar", e filtrar pela coluna nova é recusado."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Select
    from ratt.db import formularios_dados
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.AdicionarColuna("t1", Coluna(r.novo_id("c"), "apelido", "text")))
    r.acrescentar(rascunho.CriarTabela(Tabela(r.novo_id("t"), "public", "notas", [
        Coluna(r.novo_id("c"), "id", "bigint", obrigatoria=True, identidade=True, pk=True)])))
    guardar.gravar_rascunho(5432, "teste", "public", json.loads(json.dumps(r.para_dict())))
    nova = r.operacoes[0].coluna.id
    nova_tabela = r.operacoes[1].tabela.id

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            grade = tela.query_one("#dados_grade")
            rotulos = [str(c.label) for c in grade.ordered_columns]
            assert any("apelido" in x and t("dados.so_depois") in x for x in rotulos)
            assert t("dados.pendentes", n=1) in _texto(tela, "#dados_pendentes")
            await piloto.press("f")
            await piloto.pause()
            form = app.screen
            form.query_one("#coluna", Select).value = nova
            await piloto.pause()
            await piloto.click("#ok")
            await piloto.pause()
            assert app.screen is form
            assert t("dados.e.so_depois", nome="apelido") in _texto(form, "#erro")
            await piloto.press("escape")
            await piloto.pause()
            assert tela.filtro.condicoes == []
            tela.acrescentar_condicao(filtro.Condicao(nova, "comeca", ["G"]))       # por fora do formulário: também recusa
            await pronto_dados(app)
            assert tela.filtro.condicoes == []

        fonte2 = FonteDadosFalsa()
        app2 = AppDados(fonte2, nova_tabela)
        async with app2.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app2)
            tela = app2.screen
            assert t("dados.so_depois_tabela", tabela="notas") in _texto(tela, "#dados_contagem")
            assert not any(c[0] in ("pagina", "contar") for c in fonte2.chamadas)
            assert tela.linhas == []
    asyncio.run(roteiro())


def test_sem_tempo_ao_trocar_o_filtro_esvazia_a_grade_e_sem_contagem(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from ratt.db import tela_dados

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            fonte.tempo_esgotado = True
            tela.acrescentar_condicao(filtro.Condicao("t1.c2", "comeca", ["G"]))
            await pronto_dados(app)
            # o filtro mudou: as linhas do filtro anterior não ficam sob a barra do filtro novo
            assert t("dados.sem_tempo_filtro") in _texto(tela, "#dados_contagem")
            assert tela.linhas == [] and tela.query_one("#dados_grade").row_count == 0
            fonte.tempo_esgotado = False
            await piloto.press("n")                               # tenta de novo o filtro novo
            await pronto_dados(app)
            assert [l[1] for l in tela.linhas] == ["Gabriel", "Giovana"] and tela.pagina == 0
            fonte.tempo_esgotado, fonte.sem_contagem = False, True
            await piloto.press("F")
            fonte.escritas += 1                                   # alguém escreveu: a contagem guardada não vale mais
            tela.vigiar()
            await pronto_dados(app)
            assert tela.total is None
            assert t("dados.n_linhas", n=5) in _texto(tela, "#dados_contagem")      # página incompleta: são 5
            monkeypatch.setattr(tela_dados, "POR_PAGINA", 2)
            fonte.escritas += 1
            tela.vigiar()
            await pronto_dados(app)
            assert t("dados.n_mais", n=2) in _texto(tela, "#dados_contagem")
            assert t("dados.sem_contagem") in _texto(tela, "#dados_contagem")
            await piloto.press("n")
            await pronto_dados(app)
            assert tela.pagina == 1 and [l[0] for l in tela.linhas] == [3, 4]
            await piloto.press("N")
            await pronto_dados(app)
            assert tela.pagina == 0
            monkeypatch.setattr(tela_dados, "POR_PAGINA", 200)
            fonte.sem_contagem = False
            fonte.escritas += 1                                   # alguém escreveu NESTA tabela: a vigia relê
            antes = len([c for c in fonte.chamadas if c[0] == "pagina"])
            tela.vigiar()
            await pronto_dados(app)
            assert len([c for c in fonte.chamadas if c[0] == "pagina"]) == antes + 1 and tela.total == 5
            assert fonte.oids[-1] == 1                            # a vigia pergunta pelas escritas da tabela t1
            tela.vigiar()                                          # nada mudou: não relê
            await pronto_dados(app)
            assert len([c for c in fonte.chamadas if c[0] == "pagina"]) == antes + 1
    asyncio.run(roteiro())


def test_pagina_que_estoura_o_tempo_nao_trava_a_paginacao(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from ratt.db import tela_dados
    monkeypatch.setattr(tela_dados, "POR_PAGINA", 2)

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            assert tela.total == 5 and [l[0] for l in tela.linhas] == [1, 2]
            fonte.tempo_esgotado = True
            await piloto.press("n")
            await pronto_dados(app)
            assert tela._estado == "sem_tempo" and tela.pagina == 0 and [l[0] for l in tela.linhas] == [1, 2]
            fonte.tempo_esgotado = False
            await piloto.press("n")                                # tenta de novo a partir da que está à vista
            await pronto_dados(app)
            assert tela.pagina == 1 and [l[0] for l in tela.linhas] == [3, 4]
            await piloto.press("N")
            await pronto_dados(app)
            assert tela.pagina == 0 and [l[0] for l in tela.linhas] == [1, 2]
    asyncio.run(roteiro())


def test_paginar_so_le_a_pagina_e_contagem_vem_depois(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from ratt.db import tela_dados
    monkeypatch.setattr(tela_dados, "POR_PAGINA", 2)

    async def roteiro():
        fonte = FonteDadosFalsa()
        consultas = []
        fonte.consulta = lambda: consultas.append(1) or ConsultaFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            tela.acrescentar_condicao(filtro.Condicao("t1.c3", "maior", ["1"]))
            await pronto_dados(app)
            n0, c0 = len(fonte.chamadas), len(consultas)
            await piloto.press("n")
            await pronto_dados(app)
            novas = fonte.chamadas[n0:]
            assert [c[0] for c in novas] == ["pagina"] and novas[0][-1] == 2 and len(consultas) == c0
            assert tela.total == 5                                  # a contagem guardada continua valendo
            await piloto.press("N")
            await pronto_dados(app)
            assert [c[0] for c in fonte.chamadas[n0:]] == ["pagina", "pagina"]
    asyncio.run(roteiro())


def test_a_pagina_aparece_antes_das_contagens(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    import threading
    solta = threading.Event()

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            contar = fonte.contar
            fonte.contar = lambda *a: (solta.wait(5), contar(*a))[1]
            tela.acrescentar_condicao(filtro.Condicao("t1.c2", "comeca", ["G"]))
            for _ in range(40):
                await asyncio.sleep(0.05)
                if [l[1] for l in tela.linhas] == ["Gabriel", "Giovana"]:
                    break
            assert [l[1] for l in tela.linhas] == ["Gabriel", "Giovana"] and tela.total is None
            assert t("canvas.f.contando") in _texto(tela, "#dados_contagem")
            solta.set()
            await pronto_dados(app)
            assert tela.total == 2 and t("dados.n_de_m", n=2, m=5) in _texto(tela, "#dados_contagem")
    asyncio.run(roteiro())


def test_releitura_da_vigia_mantem_cursor_e_rolagem(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.coordinate import Coordinate

    async def roteiro():
        fonte = FonteDadosFalsa()
        fonte.LINHAS = [(i, f"p{i}", i, None, None, True) for i in range(1, 61)]
        app = AppDados(fonte)
        async with app.run_test(size=(100, 30)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            grade = tela.query_one("#dados_grade")
            grade.focus()
            grade.cursor_coordinate = Coordinate(40, 2)
            await piloto.pause()
            rolagem = grade.scroll_offset
            assert rolagem.y > 0
            fonte.LINHAS = [(i, f"p{i}", i + 100, None, None, True) for i in range(1, 61)]
            fonte.escritas += 1
            tela.vigiar()
            await pronto_dados(app)
            assert tela.linhas[40][2] == 141
            assert grade.cursor_coordinate == Coordinate(40, 2) and grade.scroll_offset == rolagem
            assert "141" in str(grade.get_cell_at(Coordinate(40, 2)))
    asyncio.run(roteiro())


def test_clicar_com_filtro_em_texto_pergunta_antes(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from ratt.db import formularios, formularios_dados

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            tela.filtro.texto = "idade > 30"
            tela._filtro_mudou()
            await pronto_dados(app)
            _, onde = _clicar_celula(tela, 0, 2)
            await piloto.click("#dados_grade", offset=onde)
            await piloto.pause()
            assert isinstance(app.screen, formularios.Confirmar) and tela.filtro.texto == "idade > 30"
            await piloto.click("#nao")
            await pronto_dados(app)
            assert app.screen is tela and tela.filtro.texto == "idade > 30"
            await piloto.click("#dados_grade", offset=onde)
            await piloto.pause()
            await piloto.click("#sim")
            await pronto_dados(app)
            assert isinstance(app.screen, formularios_dados.FormCondicao) and tela.filtro.texto is None
            await piloto.click("#ok")
            await pronto_dados(app)
            assert tela.filtro.condicoes == [filtro.Condicao("t1.c3", "igual", ["34"])]
    asyncio.run(roteiro())


def test_celula_de_json_bytea_ou_lista_e_recusada():
    from ratt.db import grade_dados
    for tipo, valor in (("jsonb", {"a": 1}), ("bytea", b"\x00"), ("integer[]", [1, 2]), ("json", "[1]")):
        with pytest.raises(ValueError) as e:
            grade_dados.condicao_da_celula(Coluna("t1.c9", "x", tipo), valor)
        assert tipo in str(e.value)
    assert grade_dados.condicao_da_celula(Coluna("t1.c3", "idade", "integer"), 34) == filtro.Condicao("t1.c3", "igual", ["34"])
    assert grade_dados.condicao_da_celula(Coluna("t1.c6", "ativo", "boolean"), False).operador == "falso"
    assert grade_dados.condicao_da_celula(Coluna("t1.c4", "email", "jsonb"), None).operador == "vazio"


def test_outra_coluna_da_mesma_linha_e_existe_preenchido(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Checkbox, Select
    from ratt.db import formularios_dados
    m = modelo_pessoas()
    m.tabelas["t1"].colunas.append(Coluna("t1.c7", "idade_minima", "integer"))

    async def roteiro():
        fonte = FonteDadosFalsa(m)
        app = AppDados(fonte)
        async with app.run_test(size=(140, 50)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            await piloto.press("f"); await piloto.pause()
            form = app.screen
            form.query_one("#coluna", Select).value = "t1.c3"; await piloto.pause()
            form.query_one("#operador", Select).value = "maior"; await piloto.pause()
            form.query_one("#usar_outra", Checkbox).value = True; await piloto.pause()
            assert form._opcoes["outra_coluna"] == ["t1.c1", "t1.c7"]  # só números, sem ela mesma
            form.query_one("#outra_coluna", Select).value = "t1.c7"; await piloto.pause()
            assert "idade > idade_minima" in _texto(form, "#previa")
            await piloto.click("#ok"); await pronto_dados(app)
            c = filtro.Condicao("t1.c3", "maior", [], outra_coluna="t1.c7")
            assert tela.filtro.condicoes == [c]
            app.push_screen(formularios_dados.FormCondicao(tela._vista(), tela._base(), "t1", c))
            await piloto.pause(); await piloto.pause()
            form = app.screen
            assert form.query_one("#usar_outra", Checkbox).value and form.query_one("#outra_coluna", Select).value == "t1.c7"
            assert form.condicao() == c
            await piloto.press("escape"); await piloto.pause()
            existe = filtro.Condicao("", "existe", existe=filtro.Existe("k1", [filtro.Condicao("t2.c3", "maior", ["15"])]))
            app.push_screen(formularios_dados.FormCondicao(tela._vista(), tela._base(), "t1", existe))
            await piloto.pause(); await piloto.pause()
            form = app.screen
            assert form.internas == [filtro.Condicao("t2.c3", "maior", ["15"])] and form.condicao() == existe
            form.query_one("#ligacao", Select).clear(); await piloto.pause()      # trocar a ligação esvazia
            assert form.internas == []
    asyncio.run(roteiro())


def test_view_le_as_colunas_na_thread_da_tela(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    import threading
    linhas_de = []

    async def roteiro():
        fonte = FonteDadosFalsa()
        colunas = fonte.colunas
        fonte.colunas = lambda oid: linhas_de.append(threading.current_thread() is threading.main_thread()) or colunas(oid)
        app = AppDados(fonte, "t99", so_ver=True, view=True, nome="maiores")
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            assert linhas_de == [False]                     # leu uma vez, fora da thread da tela
            assert ("pagina", "maiores", "", (), 0) in fonte.chamadas and tela.so_ver
            assert [c.nome for c in tela._tab_base().colunas] == ["nome", "idade"]
    asyncio.run(roteiro())


def test_form_condicao_existe_mes_lista_e_valor_ruim(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Checkbox, Input, Select
    from ratt.db import formularios_dados
    E = formularios_dados.EXISTE

    async def escolher(piloto, form, **valores):
        for chave, valor in valores.items():
            form.query_one("#" + chave, Select).value = valor
            await piloto.pause()

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 50)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            # valor que não serve: o motivo aparece e o formulário fica aberto
            await piloto.press("f"); await piloto.pause()
            form = app.screen
            await escolher(piloto, form, coluna="t1.c3", operador="igual")
            form.query_one("#valor", Input).value = "vinte"
            await piloto.pause()
            await piloto.click("#ok"); await piloto.pause()
            assert app.screen is form and t("filtro.e.numero", valor="vinte") in _texto(form, "#erro")
            # mês escolhido pelo nome, guardado como número
            await escolher(piloto, form, coluna="t1.c5", transformacao="mes", operador="maior_igual", valor_escolha="6")
            assert "extract(month FROM nascimento) >= 6" in _texto(form, "#previa")
            assert t("filtro.mes.6") in _texto(form, "#previa")
            await piloto.click("#ok"); await pronto_dados(app)
            assert tela.filtro.condicoes == [filtro.Condicao("t1.c5", "maior_igual", ["6"], "mes")]
            # lista com o vazio e NÃO
            await piloto.press("f"); await piloto.pause()
            form = app.screen
            await escolher(piloto, form, coluna="t1.c4", operador="fora_lista")
            form.query_one("#valor", Input).value = "a@x.com, b@x.com"
            form.query_one("#vazio_lista", Checkbox).value = True
            await piloto.pause()
            assert "email NOT IN ('a@x.com', 'b@x.com', NULL)" in _texto(form, "#previa")
            await piloto.click("#ok"); await pronto_dados(app)
            assert tela.filtro.condicoes[-1].valores == ["a@x.com", "b@x.com", ""]
            assert any(a.chave == "chk.fora_lista_vazio" for a in tela.avisos)
            assert t("chk.fora_lista_vazio") in _texto(tela, "#dados_avisos")
            # existe em outra tabela, com uma condição lá dentro (outro formulário)
            await piloto.press("f"); await piloto.pause()
            form = app.screen
            await escolher(piloto, form, coluna=E, ligacao="k1")
            await piloto.click("#mais_interna"); await piloto.pause()
            dentro = app.screen
            assert isinstance(dentro, formularios_dados.FormCondicao) and dentro is not form
            assert not any(v == E for _, v in dentro.query_one("#coluna", Select)._options)     # um nível só
            await escolher(piloto, dentro, coluna="t2.c3", operador="maior")
            dentro.query_one("#valor", Input).value = "15"
            await piloto.pause()
            await piloto.click("#ok"); await piloto.pause()
            assert app.screen is form
            form.query_one("#negar", Checkbox).value = True
            await piloto.pause()
            assert ("NOT EXISTS (SELECT 1 FROM public.pedidos WHERE public.pedidos.pessoa_id = public.pessoas.id "
                    "AND public.pedidos.total > 15)") in _texto(form, "#previa").replace("\n", " ")
            await piloto.click("#ok"); await pronto_dados(app)
            assert tela.filtro.condicoes[-1].existe == filtro.Existe("k1", [filtro.Condicao("t2.c3", "maior", ["15"])], True)
    asyncio.run(roteiro())


def test_tabela_estrangeira_abre_so_para_ver(monkeypatch, tmp_path):
    """Quem abre não sabe o relkind (o modelo põe estrangeiras junto das tabelas): a tela pergunta à fonte."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        fonte = FonteDadosFalsa()
        perguntas = []
        fonte.classe = lambda oid: perguntas.append(oid) or "f"
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            assert perguntas == [1] and tela.so_ver and t("dados.so_ver") in _texto(tela, "#dados_filtro")
            assert not tela.check_action("aplicar", ()) and len(tela.linhas) == 5
    asyncio.run(roteiro())


# ── Task 8: inserir ──

async def _abrir_inserir(app, piloto):
    from ratt.db import formularios_dados
    await pronto_dados(app)
    tela = app.screen
    await piloto.press("i")
    await piloto.pause()
    form = app.screen
    assert isinstance(form, formularios_dados.FormInserir)
    return tela, form


def _digitar(form, coluna, texto):
    from textual.widgets import Input
    form.campo(coluna).query_one(Input).value = texto


def test_inserir_pelo_formulario(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Button, Input, Select

    async def roteiro():
        app = AppDados(FonteDadosFalsa())
        async with app.run_test(size=(140, 50)) as piloto:
            tela, form = await _abrir_inserir(app, piloto)
            ident = form.campo("id")
            assert ident.query_one(Input).disabled and ident.valor() is None
            assert t("dados.banco_preenche") in ident.query_one(Input).placeholder
            assert form.campo("ativo").query(Select) and form.campo("ativo").valor() is None
            assert t("dados.f.data") in form.campo("nascimento").query_one(Input).placeholder
            assert "true" in form.campo("ativo").query_one(Select).prompt          # vazio: o banco põe o padrão
            assert t("dados.f.obrigatoria") in str(form.campo("nome").query_one(".rotulo").render())
            _digitar(form, "nome", "O'Brien Zé")
            _digitar(form, "idade", "30")
            _digitar(form, "email", "50%_a@x.com")
            await asyncio.sleep(0.4)                       # os avisos chegam (e o formulário cresce) antes do clique
            await pronto_dados(app)
            assert form.campo("nome").preenchido() and not form.campo("nascimento").preenchido()
            sql = "INSERT INTO public.pessoas (nome, idade, email) VALUES ('O''Brien Zé', 30, '50%_a@x.com')"
            assert sql in form.query_one("#form_sql").content.code
            assert not form.query_one("#por_no_rascunho", Button).disabled
            await piloto.click("#por_no_rascunho")
            await pronto_dados(app)
            assert app.screen is tela
            ops = tela.sessao.rascunho.operacoes
            assert len(ops) == 1 and ops[0].comandos(tela.sessao.rascunho.vista()) == [sql]
            assert t("dados.pendentes", n=1) in _texto(tela, "#dados_pendentes")
    asyncio.run(roteiro())


def test_inserir_booleano_e_data():
    from ratt.db import formularios_dados
    m = modelo_pessoas()
    campo = formularios_dados.CampoValor(m.tabelas["t1"].coluna("t1.c6"), None)
    assert campo.familia == "booleano"
    campo = formularios_dados.CampoValor(m.tabelas["t1"].coluna("t1.c5"), None)
    assert campo.dica() == t("dados.f.data")
    hora = Coluna("x", "quando", "timestamp with time zone")
    assert formularios_dados.CampoValor(hora, None).dica() == t("dados.f.data_hora")
    sempre = formularios_dados.CampoValor(Coluna("i", "id", "bigint", obrigatoria=True, identidade=True), None)
    assert sempre.desativado
    por_padrao = formularios_dados.CampoValor(Coluna("i", "id", "bigint", obrigatoria=True, identidade=True,
                                                     identidade_sempre=False), None)
    assert not por_padrao.desativado and por_padrao.dica() == t("dados.f.numero") + " · " + t("dados.f.vazio_banco")
    assert t("dados.f.obrigatoria") not in str(por_padrao._rotulo())
    gerada = formularios_dados.CampoValor(Coluna("y", "total", "numeric", padrao="(a * b)", gerada=True), None)
    assert gerada.desativado and gerada.dica() == t("dados.banco_preenche")
    numero = formularios_dados.CampoValor(Coluna("z", "n", "integer", padrao="7"), None)
    assert numero.dica() == t("dados.f.numero") + " · " + t("dados.f.padrao", padrao="7")


def test_por_e_inserir_outra(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Input, Select

    async def roteiro():
        app = AppDados(FonteDadosFalsa())
        async with app.run_test(size=(140, 50)) as piloto:
            await pronto_dados(app)
            app.screen.sessao.acrescentar(rascunho.Inserir("t1", {"t1.c2": "antes"}))     # já havia algo no rascunho
            tela, form = await _abrir_inserir(app, piloto)
            avisos = []
            form.notify = lambda msg, **kw: avisos.append(msg)
            _digitar(form, "nome", "Um")
            form.campo("ativo").query_one(Select).value = "false"
            await asyncio.sleep(0.4)                       # os avisos chegam antes do clique (o formulário cresce)
            await pronto_dados(app)
            await piloto.click("#por_e_outra")
            await pronto_dados(app)
            assert app.screen is form
            assert form.campo("nome").query_one(Input).value == "" and form.campo("ativo").valor() is None
            _digitar(form, "nome", "Dois")
            await asyncio.sleep(0.4)                       # os avisos chegam antes do clique (o formulário cresce)
            await pronto_dados(app)
            await piloto.click("#por_e_outra")
            await pronto_dados(app)
            assert app.screen is form
            vista = tela.sessao.rascunho.vista()
            assert avisos == [t("dados.f.posta", n=1), t("dados.f.posta", n=2)]      # as linhas deste formulário
            assert [op.comandos(vista)[0] for op in tela.sessao.rascunho.operacoes[1:]] == [
                "INSERT INTO public.pessoas (nome, ativo) VALUES ('Um', FALSE)",
                "INSERT INTO public.pessoas (nome) VALUES ('Dois')"]
            assert t("canvas.rascunho", n=3) in _texto(tela, "#dados_titulo")
            await piloto.press("escape")
            await piloto.pause()
            assert app.screen is tela and len(tela.sessao.rascunho.operacoes) == 3
    asyncio.run(roteiro())


def test_inserir_nome_vazio_avisa_e_nao_trava(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Button

    async def roteiro():
        app = AppDados(FonteDadosFalsa())
        async with app.run_test(size=(140, 50)) as piloto:
            tela, form = await _abrir_inserir(app, piloto)
            _digitar(form, "idade", "vinte")
            await asyncio.sleep(0.4)
            await pronto_dados(app)
            avisos = str(form.query_one("#form_avisos").render())
            assert "✖ " + t("chk.obrigatoria_vazia", coluna="nome") in avisos
            assert t("chk.valor_invalido", coluna="idade", valor="vinte", tipo="integer") in avisos
            assert not form.query_one("#por_no_rascunho", Button).disabled
            await piloto.click("#por_no_rascunho")                 # "vinte" nem vira SQL: não entra
            await pronto_dados(app)
            assert app.screen is form and str(form.query_one("#erro").render())
            _digitar(form, "idade", "20")
            await asyncio.sleep(0.4)                       # os avisos chegam antes do clique (o formulário cresce)
            await pronto_dados(app)
            await piloto.click("#por_no_rascunho")                 # nome vazio: avisado, mas a pessoa decide
            await pronto_dados(app)
            assert app.screen is tela
            assert tela.sessao.rascunho.operacoes[0].valores == {"t1.c3": "20"}
    asyncio.run(roteiro())


def test_inserir_chave_estrangeira_escolhida_na_lista(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Input, OptionList
    from ratt.db import formularios_dados

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte, "t2")
        async with app.run_test(size=(140, 50)) as piloto:
            tela, form = await _abrir_inserir(app, piloto)
            campo = form.campo("pessoa_id")
            assert "pessoas.id" in str(campo.query_one(".rotulo").render())
            await piloto.click("#" + campo.query_one(".escolher").id)
            await pronto_dados(app)
            escolher = app.screen
            assert isinstance(escolher, formularios_dados.EscolherLinha)
            assert ("linhas_pai", "public", "pessoas", "id", "") in fonte.chamadas
            lista = escolher.query_one(OptionList)
            assert lista.option_count == 3 and "Ana" in str(lista.get_option_at_index(1).prompt)
            escolher.query_one("#busca", Input).value = "gab"
            await asyncio.sleep(0.4)
            await pronto_dados(app)
            assert ("linhas_pai", "public", "pessoas", "id", "gab") in fonte.chamadas
            assert lista.option_count == 1
            escolher.query_one("#busca", Input).value = ""
            await asyncio.sleep(0.4)
            await pronto_dados(app)
            lista.focus()
            lista.highlighted = 1
            await piloto.press("enter")
            await pronto_dados(app)
            assert app.screen is form
            assert campo.valor() == "2" and "Ana" in str(campo.query_one(".escolhido").render())
            assert "INSERT INTO public.pedidos (pessoa_id) VALUES (2)" in form.query_one("#form_sql").content.code
            _digitar(form, "pessoa_id", "3")                      # também aceita digitar; o rótulo velho sai
            await piloto.pause()
            assert "Ana" not in str(campo.query_one(".escolhido").render())
            fonte.busca_sem_tempo = True
            await piloto.click("#" + campo.query_one(".escolher").id)
            await pronto_dados(app)
            assert t("dados.f.sem_tempo_busca") in str(app.screen.query_one("#estado").render())
            await piloto.press("escape")
            await piloto.pause()
            assert app.screen is form and campo.valor() == "3"
            await asyncio.sleep(0.4)
            await pronto_dados(app)
            await piloto.click("#por_no_rascunho")
            await pronto_dados(app)
            assert tela.sessao.rascunho.operacoes[0].valores == {"t2.c2": "3"}
    asyncio.run(roteiro())


def test_inserir_em_tabela_criada_no_rascunho_e_so_ver(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    r = rascunho.Rascunho(modelo_pessoas())
    r.acrescentar(rascunho.CriarTabela(Tabela(r.novo_id("t"), "public", "notas", [
        Coluna(r.novo_id("c"), "id", "bigint", obrigatoria=True, identidade=True, pk=True),
        Coluna(r.novo_id("c"), "texto", "text", obrigatoria=True),
        Coluna(r.novo_id("c"), "pessoa", "bigint")])))
    tab = r.operacoes[0].tabela
    r.acrescentar(rascunho.Ligar(Ligacao(r.novo_id("k"), tab.id, (tab.colunas[2].id,), "t1", ("t1.c1",),
                                         "notas_pessoa_fkey"), indice=False))
    guardar.gravar_rascunho(5432, "teste", "public", json.loads(json.dumps(r.para_dict())))

    async def roteiro():
        app = AppDados(FonteDadosFalsa(), tab.id)
        async with app.run_test(size=(140, 50)) as piloto:
            tela, form = await _abrir_inserir(app, piloto)
            _digitar(form, "texto", "oi")
            await asyncio.sleep(0.4)
            await pronto_dados(app)
            assert t("chk.so_depois_de_aplicar", tabela="notas") in str(form.query_one("#form_avisos").render())
            assert not form.campo("pessoa").query_one(".escolher").disabled      # o pai (pessoas) já está no banco
            await piloto.click("#por_no_rascunho")
            await pronto_dados(app)
            assert app.screen is tela and len(tela.sessao.rascunho.operacoes) == 3
            assert tela.sessao.rascunho.operacoes[-1].comandos(tela.sessao.rascunho.vista()) == [
                "INSERT INTO public.notas (texto) VALUES ('oi')"]
    asyncio.run(roteiro())


def test_so_ver_nao_insere(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        fonte = FonteDadosFalsa()
        fonte.classe = lambda oid: "v"
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            assert tela.so_ver and not tela.check_action("inserir", ())
            await piloto.press("i")
            await piloto.pause()
            assert app.screen is tela
    asyncio.run(roteiro())


# ── o banco descartável: apagar com paciência ──

class _AdminTeimoso:
    """Recusa o DROP algumas vezes (como com um autovacuum do superusuário no meio) e depois aceita."""

    def __init__(self, erros):
        self.erros, self.comandos = list(erros), []

    def execute(self, comando):
        self.comandos.append(comando)
        if self.erros:
            raise self.erros.pop(0)


def test_apagar_banco_de_teste_tenta_de_novo():
    import conftest
    esperas = []
    negado = psycopg.errors.InsufficientPrivilege("permissão negada para terminar o processo")
    admin = _AdminTeimoso([negado, psycopg.errors.ObjectInUse("em uso")])
    assert conftest.apagar_banco(admin, "ratt_teste_1", espera=esperas.append) is True
    assert admin.comandos == ['DROP DATABASE IF EXISTS "ratt_teste_1" WITH (FORCE)'] * 3
    assert len(esperas) == 2 and esperas[1] > esperas[0] > 0
    admin = _AdminTeimoso([negado] * 100)
    relogio = [0.0]

    def espera(s):
        relogio[0] += s
    assert conftest.apagar_banco(admin, "ratt_teste_1", espera=espera, relogio=lambda: relogio[0]) is False
    assert 4.0 <= relogio[0] <= 6.0                                 # desiste depois de uns 5 s
    admin = _AdminTeimoso([psycopg.errors.InsufficientPrivilege("permissão negada para remover o banco de dados")])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):       # sem permissão de verdade: não adianta esperar
        conftest.apagar_banco(admin, "ratt_teste_1", espera=esperas.append)
    admin = _AdminTeimoso([psycopg.errors.InsufficientPrivilege("permission denied to terminate process")])
    assert conftest.apagar_banco(admin, "ratt_teste_1", espera=esperas.append) is True
    admin = _AdminTeimoso([psycopg.errors.SyntaxError("outra coisa")])
    with pytest.raises(psycopg.errors.SyntaxError):                 # outro erro não é esperado: sobe na hora
        conftest.apagar_banco(admin, "ratt_teste_1", espera=esperas.append)


def test_coluna_de_identidade_e_gerada_no_json_so_quando_fogem_do_padrao():
    r = rascunho.Rascunho(Modelo("public"))
    r.acrescentar(rascunho.CriarTabela(Tabela(r.novo_id("t"), "public", "x", [
        Coluna(r.novo_id("c"), "id", "bigint", obrigatoria=True, identidade=True, pk=True)])))
    r.acrescentar(rascunho.AdicionarColuna(r.operacoes[0].tabela.id,
                                           Coluna(r.novo_id("c"), "n", "bigint", identidade=True, identidade_sempre=False)))
    d = json.loads(json.dumps(r.para_dict()))
    col_tabela = d["operacoes"][0]["tabela"]["colunas"][0]
    assert "identidade_sempre" not in col_tabela and "gerada" not in col_tabela       # um ratt mais velho lê
    assert d["operacoes"][1]["coluna"]["identidade_sempre"] is False and "gerada" not in d["operacoes"][1]["coluna"]
    de_volta = rascunho.Rascunho.de_dict(d, Modelo("public"))
    assert de_volta.operacoes[0].tabela.colunas[0].identidade_sempre is True
    assert de_volta.operacoes[1].coluna.identidade_sempre is False


def test_escolher_linha_mostra_a_destacada_em_tela_pequena():
    from textual.app import App
    from textual.containers import Vertical
    from textual.widgets import OptionList
    from ratt.db import formularios_dados

    class Fonte:
        def linhas_pai(self, esquema, tabela, chave, busca, limite=50):
            return [(i, f"pessoa {i}") for i in range(1, 51)]

    async def roteiro(largura, altura):
        class A(App):
            def on_mount(self):
                self.push_screen(formularios_dados.EscolherLinha(Fonte(), "public", "pessoas", "id"))
        app = A()
        async with app.run_test(size=(largura, altura)) as piloto:
            await app.workers.wait_for_complete()
            await piloto.pause()
            tela = app.screen
            caixa, lista = tela.query_one(Vertical), tela.query_one(OptionList)
            lista.focus()
            for _ in range(20):
                await piloto.press("down")
            await piloto.pause()
            assert lista.highlighted == 20
            assert lista.region.bottom <= caixa.region.bottom <= altura, (largura, altura)   # a lista cabe na caixa
            visiveis = lista.scrollable_content_region.height
            assert visiveis >= 1
            assert lista.scroll_offset.y <= 20 < lista.scroll_offset.y + visiveis, (altura, lista.scroll_offset, visiveis)
    asyncio.run(roteiro(80, 24))
    asyncio.run(roteiro(80, 16))
