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

from conftest import esperar_que, esperar_tela, esperar_workers
from test_canvas import AppTeste, ConsultaFalsa, FonteFalsa, contado, modelo_png, pronto


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


def _schema_de_uma_tabela(esquema, tid, nome):
    return Modelo(esquema, {tid: Tabela(tid, esquema, nome, [Coluna(tid + ".c1", "id", "bigint", pk=True)])})


def _rascunho_antigo(pasta, nome_arquivo, *ops):
    """Um rascunho como a 0.9.0 o gravava: sem identidade, no nome do `_seguro` não injetivo."""
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / nome_arquivo
    caminho.write_text(json.dumps(rascunho.Rascunho(Modelo("x")).para_dict() | {"operacoes": [
        rascunho._op_para_dict(op) for op in ops]}), encoding="utf-8")
    return caminho


def test_rascunho_gravado_no_nome_antigo_vai_para_o_dono_certo(monkeypatch, tmp_path):
    """Um rascunho nunca se perde com a troca de nome dos arquivos: o de "my schema", gravado no nome antigo
    (rascunho-my_schema.json), é achado e levado para o nome novo (o antigo fica como .migrado); o de "my_schema", no
    mesmo nome, fica com "my_schema" — "my schema" não o toma."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    pasta = guardar.pasta(5432, "teste")
    meu = _schema_de_uma_tabela("my schema", "t10", "pessoas")
    deles = _schema_de_uma_tabela("my_schema", "t20", "carros")
    antigo = _rascunho_antigo(pasta, "rascunho-my_schema.json", rascunho.Renomear("t10", None, "gente"))
    s = sessao.SessaoRascunho(5432, "teste", "my schema")
    avisos = s.abrir(meu)
    assert [op.tipo for op in s.rascunho.operacoes] == ["renomear"] and s.rascunho.invalidas == set()
    assert not antigo.exists() and (pasta / "rascunho-my_schema.json.migrado").exists()
    assert [str(a) for a in avisos] == [t("canvas.rascunho_migrado", caminho=pasta / "rascunho-my_schema.json.migrado")]
    gravado = json.loads(guardar._arquivo_rascunho(5432, "teste", "my schema").read_text(encoding="utf-8"))
    assert gravado["esquema"] == "my schema" and len(gravado["operacoes"]) == 1
    assert guardar.ler_rascunho(5432, "teste", "my schema")["operacoes"] == gravado["operacoes"]
    s.persistir()                                         # gravar de novo não muda nada (a identidade não conta)
    assert s.reler(meu) is None

    # o de "my_schema" no mesmo nome antigo: "my schema" (sem rascunho no nome novo) não o toma, e calado
    guardar.gravar_rascunho(5432, "teste", "my schema", None)
    antigo = _rascunho_antigo(pasta, "rascunho-my_schema.json", rascunho.Renomear("t20", None, "autos"))
    s2 = sessao.SessaoRascunho(5432, "teste", "my schema")
    assert s2.abrir(meu) == [] and s2.rascunho.operacoes == [] and antigo.exists()
    s3 = sessao.SessaoRascunho(5432, "teste", "my_schema")
    s3.abrir(deles)
    assert [op.tipo for op in s3.rascunho.operacoes] == ["renomear"] and s3.rascunho.invalidas == set()
    assert antigo.exists()                                # o nome dele é o mesmo de sempre: nada a migrar

    # com identidade gravada, ela decide; sem dono certo (só cria tabela em outro schema), fica e avisa uma vez
    guardar.gravar_rascunho(5432, "teste", "my_schema", {"operacoes": [], "proximo": 1})
    s4 = sessao.SessaoRascunho(5432, "teste", "my schema")
    assert s4.abrir(meu) == [] and antigo.exists()
    nova = Tabela("novo-t1", "outro", "x", [Coluna("novo-c1", "id", "bigint", pk=True)])
    _rascunho_antigo(pasta, "rascunho-my_schema.json", rascunho.CriarTabela(nova))
    s5 = sessao.SessaoRascunho(5432, "teste", "my schema")
    avisos = s5.abrir(meu)
    assert [str(a) for a in avisos] == [t("canvas.rascunho_antigo", caminho=antigo)] and antigo.exists()
    assert s5.rascunho.operacoes == [] and s5.reler(meu) is None


def test_rascunho_de_banco_com_nome_trocado_na_pasta_nova(monkeypatch, tmp_path):
    """Banco de nome trocado: o arquivo de nome antigo na pasta NOVA dele (só ele a usa) é migrado; banco de nome
    seguro nunca olha outra pasta."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    pasta = guardar.pasta(5432, "meu banco")
    antigo = _rascunho_antigo(pasta, "rascunho-my_schema.json", rascunho.Renomear("t10", None, "gente"))
    s = sessao.SessaoRascunho(5432, "meu banco", "my schema")
    s.abrir(_schema_de_uma_tabela("my schema", "t10", "pessoas"))
    assert len(s.rascunho.operacoes) == 1 and not antigo.exists()
    assert guardar.ler_rascunho(5432, "meu banco", "my schema") is not None
    assert guardar.rascunhos_de_antes(5432, "teste", "public") == []


def test_rascunho_na_pasta_de_outro_banco_nunca_e_levado(monkeypatch, tmp_path):
    """A pasta antiga de "meu banco" (5432-meu_banco, do `_seguro` não injetivo) é a pasta de sempre de "meu_banco":
    um rascunho achado ali pode ser de qualquer um dos dois (os oids só são únicos dentro de um banco, e um banco
    clonado de um template tem os mesmos). Nunca é levado nem renomeado: fica, com um aviso (uma vez), e "meu_banco"
    continua a abri-lo como sempre."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    pasta_antiga = guardar._raiz() / "5432-meu_banco"
    assert pasta_antiga == guardar.pasta(5432, "meu_banco") != guardar.pasta(5432, "meu banco")
    base = _schema_de_uma_tabela("public", "t10", "pessoas")
    nova = Tabela("novo-t1", "public", "x", [Coluna("novo-c1", "id", "bigint", pk=True)])
    # A: só cria tabela em public; B: renomeia uma tabela cujo oid existe nos dois (clone de template)
    for op in (rascunho.CriarTabela(nova), rascunho.Renomear("t10", None, "gente")):
        guardar.gravar_rascunho(5432, "meu banco", "public", None)
        antigo = _rascunho_antigo(pasta_antiga, "rascunho-public.json", op)
        conteudo = antigo.read_text(encoding="utf-8")
        s = sessao.SessaoRascunho(5432, "meu banco", "public")
        avisos = s.abrir(base)
        assert [str(a) for a in avisos] == [t("canvas.rascunho_outro_banco", caminho=antigo)]
        assert s.rascunho.operacoes == [] and antigo.read_text(encoding="utf-8") == conteudo
        assert not guardar._arquivo_rascunho(5432, "meu banco", "public").exists()
        assert sorted(p.name for p in pasta_antiga.iterdir()) == ["rascunho-public.json"]     # nada de .migrado
        assert s.abrir(base) == []                       # avisa uma vez só
        s.acrescentar(rascunho.Renomear("t10", None, "outra"))   # "meu banco" edita o dele: o de meu_banco fica
        assert antigo.read_text(encoding="utf-8") == conteudo
        dono = sessao.SessaoRascunho(5432, "meu_banco", "public")
        assert dono.abrir(_schema_de_uma_tabela("public", "t10", "pessoas")) == []
        assert [o.tipo for o in dono.rascunho.operacoes] == [op.tipo]

    from ratt.db import idioma
    assert "banco" in idioma.TEXTOS["canvas.rascunho_outro_banco"]["pt"]
    assert "database" in idioma.TEXTOS["canvas.rascunho_outro_banco"]["en"]

    # schema também de nome trocado, arquivo na pasta antiga: o mesmo
    guardar.gravar_rascunho(5432, "meu banco", "my schema", None)
    antigo = _rascunho_antigo(pasta_antiga, "rascunho-my_schema.json", rascunho.Renomear("t10", None, "gente"))
    s = sessao.SessaoRascunho(5432, "meu banco", "my schema")
    assert [str(a) for a in s.abrir(_schema_de_uma_tabela("my schema", "t10", "pessoas"))] == [
        t("canvas.rascunho_outro_banco", caminho=antigo)]
    assert s.rascunho.operacoes == [] and antigo.exists()

    # com identidade gravada de meu_banco: é dele, sem dúvida — calado
    guardar.gravar_rascunho(5432, "meu_banco", "public", {"operacoes": [rascunho._op_para_dict(
        rascunho.Renomear("t10", None, "dele"))], "proximo": 1})
    guardar.gravar_rascunho(5432, "meu banco", "public", None)
    s = sessao.SessaoRascunho(5432, "meu banco", "public")
    assert s.abrir(base) == [] and s.rascunho.operacoes == []
    assert guardar.ler_rascunho(5432, "meu_banco", "public")["operacoes"][0]["novo"] == "dele"


def test_rascunho_antigo_com_operacao_que_nao_vale_mais_fica_em_duvida(monkeypatch, tmp_path):
    """Um rascunho de antes que se apoia em tabelas DESTE schema mas também refere uma que sumiu (apagada desde
    então) não é "de outro" calado: fica onde está, com um aviso."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    pasta = guardar.pasta(5432, "teste")
    antigo = _rascunho_antigo(pasta, "rascunho-my_schema.json", rascunho.Renomear("t10", None, "gente"),
                              rascunho.Renomear("t11", None, "sumiu"))
    s = sessao.SessaoRascunho(5432, "teste", "my schema")
    avisos = s.abrir(_schema_de_uma_tabela("my schema", "t10", "pessoas"))
    assert [str(a) for a in avisos] == [t("canvas.rascunho_antigo", caminho=antigo)]
    assert s.rascunho.operacoes == [] and antigo.exists()
    # só o que não existe aqui: é de outro schema, calado
    guardar.gravar_rascunho(5432, "teste", "my schema", None)
    s2 = sessao.SessaoRascunho(5432, "teste", "my schema")
    assert s2.abrir(_schema_de_uma_tabela("my schema", "t12", "outra")) == [] and antigo.exists()


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
    # o vazio (∅) na lista pega as linhas vazias de verdade: `IN (…, NULL)` nunca casaria com NULL
    assert _w(filtro.Condicao("t1.c3", "na_lista", ["30", ""])) == "(idade IN (30) OR idade IS NULL)"
    assert _w(filtro.Condicao("t1.c3", "na_lista", [""])) == "idade IS NULL"
    assert _w(filtro.Condicao("t1.c4", "na_lista", ["a@x", ""])) == \
        "(email IN ('a@x') OR email IS NULL OR trim(email) = '')"                # texto: o vazio inclui o ''
    assert _w(filtro.Condicao("t1.c4", "na_lista", ["a@x", ""], negar=True)) == \
        "NOT (email IN ('a@x') OR email IS NULL OR trim(email) = '')"
    assert _w(filtro.Condicao("t1.c4", "na_lista", ["a@x", ""]), filtro.Condicao("t1.c3", "igual", ["3"])) == \
        "(email IN ('a@x') OR email IS NULL OR trim(email) = '') AND (idade = 3)"
    # "não está na lista" com o vazio continua a armadilha que se ensina (o aviso diz que não pega nada)
    assert _w(filtro.Condicao("t1.c3", "fora_lista", ["", "7"])) == "idade NOT IN (NULL, 7)"
    m = modelo_pessoas()
    for negar in (False, True):                 # o vazio está na lista: as linhas vazias não saem sem a pessoa pedir
        f = filtro.Filtro([filtro.Condicao("t1.c3", "na_lista", ["30", ""], negar=negar)])
        assert filtro.deixa_vazios_de_fora(f, m, "t1") == []
    assert filtro.deixa_vazios_de_fora(filtro.Filtro([filtro.Condicao("t1.c3", "na_lista", ["30"])]), m, "t1") == ["t1.c3"]
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


def test_painel_marca_where_escrito_a_mao():
    """Uma operação com o filtro em texto: as colunas citadas nele não são acompanhadas — o painel diz isso."""
    r = rascunho.Rascunho(modelo_pessoas())
    texto = filtro.Filtro(texto="idade > 30")
    r.acrescentar(rascunho.Alterar("t1", texto, {"t1.c2": rascunho.Mudanca("trim")}))
    r.acrescentar(rascunho.Apagar("t1", texto))
    r.acrescentar(rascunho.ApagarDuplicatas("t1", texto, ("t1.c4",)))
    r.acrescentar(rascunho.Apagar("t1", filtro.Filtro([filtro.Condicao("t1.c3", "maior", ["30"])])))
    descricoes = [desc for _, _, desc, _ in r.passos()]
    marca = t("op.where_a_mao", texto="idade > 30")
    assert all(marca in d for d in descricoes[:3]) and "idade > 30" in marca
    assert t("op.where_a_mao", texto="").split(":")[0] not in descricoes[3]


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
        await esperar_workers(app)
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


def test_vigia_nao_se_sobrepoe_ao_timer(monkeypatch, tmp_path):
    """Uma olhada pedida à mão e o timer logo em seguida: a segunda não sai enquanto a primeira está no ar (a marca
    de "no ar" é posta na thread da tela, antes da thread começar) — nada é cancelado, o banco é lido uma vez só."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.worker import WorkerCancelled

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)):
            await pronto_dados(app)
            tela = app.screen
            antes = len(fonte.oids)
            fonte.escritas += 1
            tela.vigiar()
            tela.vigiar()                                          # o timer, logo depois do pedido à mão
            assert tela._olhando
            try:
                await app.workers.wait_for_complete()
            except WorkerCancelled:
                pytest.fail("uma olhada cancelou a outra")
            await pronto_dados(app)
            assert len(fonte.oids) == antes + 1 and not tela._olhando
            assert len([c for c in fonte.chamadas if c[0] == "pagina"]) == 2       # a abertura e a releitura
    asyncio.run(roteiro())


def _sem_suspender(monkeypatch, fonte):
    """Aplicar sob `run_test`: sem suspender o terminal, com uma conexão falsa."""
    from contextlib import contextmanager
    from textual.app import App

    @contextmanager
    def sem_suspender(self):
        yield
    monkeypatch.setattr(App, "suspend", sem_suspender)

    class Con:
        def close(self):
            pass
    fonte.conexao_para_aplicar = lambda: Con()


def test_contagem_logo_depois_de_aplicar_e_a_nova(monkeypatch, tmp_path):
    """Aplicou pela tela Dados (um INSERT nesta tabela): a linha "N de M" já mostra o total novo — não a contagem
    guardada de antes do aplicar, à espera da vigia."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from ratt.db import tela_dados
    monkeypatch.setattr(tela_dados.TelaDados, "VIGIA_SEGUNDOS", 0)       # nada de vigia: só o aplicar relê
    fonte = FonteDadosFalsa()
    _sem_suspender(monkeypatch, fonte)
    gil = (6, "Gil", None, None, None, True)

    def rodou(self, con, arq, ensaio, cab):
        fonte.LINHAS = FonteDadosFalsa.LINHAS + [gil]                   # o banco gravou a linha nova
        return 0, None, None
    monkeypatch.setattr(tela_dados.TelaDados, "_sessao", rodou)

    async def roteiro():
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)):
            await pronto_dados(app)
            tela = app.screen
            assert tela.total == 5 and t("dados.n_de_m", n=5, m=5) in _texto(tela, "#dados_contagem")
            tela.sessao.acrescentar(rascunho.Inserir("t1", {"t1.c2": "Gil"}))
            tela._rodar(False)
            await pronto_dados(app)
            assert len(tela.linhas) == 6 and tela.total == 6 and tela.total_tabela == 6
            assert t("dados.n_de_m", n=6, m=6) in _texto(tela, "#dados_contagem")
    asyncio.run(roteiro())


def test_aplicar_pela_tela_dados_leva_as_posicoes_das_tabelas_novas_no_canvas(monkeypatch, tmp_path):
    """Uma tabela criada no rascunho tem posição no canvas pelo id provisório (novo-t…). Aplicado pela tela Dados
    (com o canvas embaixo), a posição vai para o id do banco, pelo nome — na memória do canvas e no disco."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from ratt.db import modelo as _modelo, tela_dados
    monkeypatch.setattr(tela_dados.TelaDados, "VIGIA_SEGUNDOS", 0)
    fonte = FonteDadosFalsa()
    _sem_suspender(monkeypatch, fonte)
    fresco = modelo_pessoas()
    fresco.tabelas["t9"] = Tabela("t9", "public", "pets", [Coluna("t9.c1", "id", "bigint", pk=True)])

    def rodou(self, con, arq, ensaio, cab):
        fonte.m = fresco                                          # o banco agora tem a tabela pets
        return 0, None, None
    monkeypatch.setattr(tela_dados.TelaDados, "_sessao", rodou)

    async def roteiro():
        app = AppTeste(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            nova = Tabela(tela.rascunho.novo_id("t"), "public", "pets", [Coluna("x", "id", "bigint", pk=True)])
            tela.sessao.acrescentar(rascunho.CriarTabela(nova))
            await piloto.pause()
            assert nova.id in tela._pos_todo
            antes = tela._pos_todo[nova.id] = (77, 33)                 # a pessoa a pôs ali (longe da automática)
            guardar.gravar_posicoes(5432, "teste", "public", tela._pos_todo)
            dados = tela_dados.TelaDados(fonte, "public", "t1")
            app.push_screen(dados)
            await pronto_dados(app)
            dados._rodar(False)
            await pronto_dados(app)
            assert dados.sessao.rascunho.operacoes == [] and "t9" in dados.sessao.rascunho.base.tabelas
            assert tela._pos_todo.get("t9") == antes and nova.id not in tela._pos_todo
            assert guardar.ler_posicoes(5432, "teste", "public").get("t9") == antes
            app.pop_screen()
            await pronto_dados(app)
            assert tela.posicoes.get("t9") == antes
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


def test_limpar_com_filtro_em_texto_pergunta_antes(monkeypatch, tmp_path):
    """F com um WHERE escrito à mão: pergunta antes de descartar o texto (o foco no não); sim limpa tudo."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from ratt.db import formularios

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            tela.filtro = filtro.Filtro([filtro.Condicao("t1.c3", "maior", ["30"])], texto="idade > 30")
            tela._filtro_mudou()
            await pronto_dados(app)
            await piloto.press("F")
            await esperar_que(piloto, lambda: isinstance(app.screen, formularios.Confirmar))
            assert tela.filtro.texto == "idade > 30"
            await piloto.press("enter")                       # o foco está no não: nada se perde
            await esperar_que(piloto, lambda: app.screen is tela)
            assert tela.filtro.texto == "idade > 30" and tela.filtro.condicoes
            await piloto.press("F")
            await esperar_que(piloto, lambda: isinstance(app.screen, formularios.Confirmar))
            await esperar_tela(piloto, app.screen)
            await piloto.click("#sim")
            await esperar_que(piloto, lambda: app.screen is tela)
            await pronto_dados(app)
            assert tela.filtro == filtro.Filtro() and len(tela.linhas) == 5
            await piloto.press("F")                           # sem texto: limpa sem perguntar
            await piloto.pause()
            assert app.screen is tela
    asyncio.run(roteiro())


def test_confirmacao_de_aplicar_lista_as_operacoes_de_dados_com_a_contagem_vista(monkeypatch, tmp_path):
    """DELETE, duplicatas e UPDATE aparecem no alto da confirmação do aplicar, com quantas linhas a pessoa viu (ou
    "não contado"); o INSERT não apaga nada e não aparece."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from ratt.db import formularios

    async def roteiro():
        app = AppDados(FonteDadosFalsa())
        async with app.run_test(size=(140, 50)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            g = filtro.Filtro([filtro.Condicao("t1.c2", "comeca", ["G"])])
            tela.sessao.acrescentar(rascunho.Inserir("t1", {"t1.c2": "Gil"}))
            tela.sessao.acrescentar(rascunho.Apagar("t1", g, vista=2))
            tela.sessao.acrescentar(rascunho.ApagarDuplicatas("t1", filtro.Filtro(), ("t1.c4",), vista=1))
            tela.sessao.acrescentar(rascunho.Alterar("t1", g, {"t1.c2": rascunho.Mudanca("trim")}))
            await piloto.press("enter")
            await esperar_que(piloto, lambda: isinstance(app.screen, formularios.Confirmar))
            conf = app.screen
            itens = list(conf.itens)
            vista = tela.sessao.rascunho.vista()
            descricoes = [op.descricao(vista) for op in tela.sessao.rascunho.operacoes]
            assert itens == [descricoes[1] + " — " + t("dados.d.vistas", n=2),
                             descricoes[2] + " — " + t("dados.d.vista_uma"),
                             descricoes[3] + " — " + t("canvas.d.nao_contado")]
            assert t("dados.d.titulo") in conf.pergunta
            await piloto.press("escape")
            await esperar_que(piloto, lambda: app.screen is tela)
            tela.sessao.tirar([3])                                 # sem o UPDATE: o título é o de sempre
            await piloto.press("enter")
            await esperar_que(piloto, lambda: isinstance(app.screen, formularios.Confirmar))
            assert t("canvas.d.titulo") in app.screen.pergunta and len(app.screen.itens) == 2
    asyncio.run(roteiro())


def test_celula_de_real_double_ou_interval_e_recusada_com_o_motivo():
    """O valor de um real/double precision/interval, como aparece na grade, não volta exato como "igual a": a
    célula recusa (com o motivo), em vez de montar um filtro que não pegaria a própria linha."""
    import datetime as dt
    from ratt.db import grade_dados
    for tipo, valor in (("real", 0.1), ("float4", 0.1), ("double precision", 1 / 3), ("float8", 2.5),
                        ("interval", dt.timedelta(days=1, hours=2)),
                        ("interval day to second", dt.timedelta(days=1)), ("interval year to month", dt.timedelta(days=30)),
                        ("interval hour", dt.timedelta(hours=3)), ("interval minute to second(3)", dt.timedelta(seconds=1)),
                        ("interval(6)", dt.timedelta(days=1)), ("INTERVAL  Day To  Second (2)", dt.timedelta(days=1))):
        with pytest.raises(ValueError) as e:
            grade_dados.condicao_da_celula(Coluna("t1.c9", "medida", tipo), valor)
        assert str(e.value) == t("dados.e.celula_aprox", coluna="medida", tipo=tipo)
    assert grade_dados.condicao_da_celula(Coluna("t1.c9", "medida", "real"), None).operador == "vazio"
    for tipo in ("interval year", "interval month", "interval day", "interval hour", "interval minute",
                 "interval second", "interval year to month", "interval day to hour", "interval day to minute",
                 "interval day to second", "interval hour to minute", "interval hour to second",
                 "interval minute to second", "interval second(3)", "interval day to second(3)"):
        assert filtro.tipo_normal(tipo) == "interval", tipo
        assert filtro.familia_fina(tipo) == "tempo", tipo
    assert filtro.tipo_normal("interval day to second[]") == "interval[]"
    assert filtro.tipo_normal("intervalo") == "intervalo"                       # um tipo da pessoa, não o interval
    assert grade_dados.condicao_da_celula(Coluna("t1.c9", "v", "numeric(10,2)"), 2.5) == \
        filtro.Condicao("t1.c9", "igual", ["2.5"])                                # numeric volta exato


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
            await contado(app, form)
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
            await pronto_dados(app)
            await piloto.click("#por_e_outra")
            await pronto_dados(app)
            assert app.screen is form
            assert form.campo("nome").query_one(Input).value == "" and form.campo("ativo").valor() is None
            _digitar(form, "nome", "Dois")
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
            await contado(app, form)
            await pronto_dados(app)
            avisos = str(form.query_one("#form_avisos").render())
            assert "✖ " + t("chk.obrigatoria_vazia", coluna="nome") in avisos
            assert t("chk.valor_invalido", coluna="idade", valor="vinte", tipo="integer") in avisos
            assert not form.query_one("#por_no_rascunho", Button).disabled
            await piloto.click("#por_no_rascunho")                 # "vinte" nem vira SQL: não entra
            await pronto_dados(app)
            assert app.screen is form and str(form.query_one("#erro").render())
            _digitar(form, "idade", "20")
            await pronto_dados(app)
            await piloto.click("#por_no_rascunho")                 # nome vazio: avisado, mas a pessoa decide
            await pronto_dados(app)
            assert app.screen is tela
            assert tela.sessao.rascunho.operacoes[0].valores == {"t1.c3": "20"}
    asyncio.run(roteiro())


def test_escolher_linha_enter_durante_a_busca_espera_a_lista_nova(monkeypatch, tmp_path):
    """Digitou e deu Enter antes de a busca sair (o atraso da digitação) ou chegar: o Enter espera a lista da busca
    nova — nunca escolhe da lista velha que ainda está à vista."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.app import App
    from textual.widgets import Input
    from ratt.db import formularios_dados

    fonte = FonteDadosFalsa()
    escolhas = []

    class A(App):
        def on_mount(self):
            self.push_screen(formularios_dados.EscolherLinha(fonte, "public", "pessoas", "id"), escolhas.append)

    async def roteiro():
        app = A()
        async with app.run_test(size=(100, 30)) as piloto:
            await esperar_workers(app)
            tela = app.screen
            assert len(tela.linhas) == 3                       # Gabriel, Ana, Giovana: Gabriel destacado
            busca = tela.query_one("#busca", Input)
            busca.focus()
            await piloto.press(*"ana")
            await piloto.press("enter")                         # dentro do atraso: a busca "ana" nem saiu
            await esperar_que(piloto, lambda: escolhas, mensagem="não escolheu")
            assert escolhas == ["2"] and tela.rotulo_escolhido == "Ana · ana@x.com"
            assert ("linhas_pai", "public", "pessoas", "id", "ana") in fonte.chamadas
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
            await contado(app, form)
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


def test_criar_banco_de_teste_que_falha_duas_vezes_falha_com_o_erro():
    import conftest
    esperas = []
    admin = _AdminTeimoso([psycopg.errors.ObjectInUse("o banco modelo está em uso")])
    conftest.criar_banco(admin, "ratt_teste_1", espera=esperas.append)          # a segunda tentativa dá certo
    assert admin.comandos == ['CREATE DATABASE "ratt_teste_1"'] * 2 and esperas == [0.5]
    admin = _AdminTeimoso([psycopg.errors.InsufficientPrivilege("permissão negada para criar banco de dados")] * 2)
    with pytest.raises(pytest.fail.Exception) as falha:                         # falha — não "pulado"
        conftest.criar_banco(admin, "ratt_teste_1", espera=esperas.append)
    assert "InsufficientPrivilege: permissão negada para criar banco de dados" in str(falha.value)


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
            await esperar_workers(app)
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


# ── Task 9: alterar, apagar e apagar duplicatas ──

import threading


class ConsultaPresa(ConsultaFalsa):
    """Uma consulta falsa cuja contagem (`contar_onde`) devolve `n` e, armada (`presa`), espera `solta` antes."""

    def __init__(self, n=2, **respostas):
        super().__init__(**respostas)
        self.n, self.presa, self.solta = n, False, threading.Event()

    def contar_onde(self, *args):
        if self.presa:
            assert self.solta.wait(5)
        self.chamadas.append(("contar_onde", args))
        return self.n


def _botao(form):
    from textual.widgets import Button
    return form.query_one("#por_no_rascunho", Button)


async def _com_filtro_g(app):
    await pronto_dados(app)
    tela = app.screen
    tela.acrescentar_condicao(filtro.Condicao("t1.c2", "comeca", ["G"]))
    await pronto_dados(app)
    assert tela.total == 2
    return tela


async def _escolher(piloto, select, valor):
    select.value = valor
    await piloto.pause()


def test_botoes_do_inserir_nao_andam_quando_os_avisos_chegam(monkeypatch, tmp_path):
    """Os avisos chegam numa área de altura fixa (que rola): o botão está no mesmo lugar desde a abertura — um clique
    nunca cai fora dele porque o formulário cresceu no meio."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppDados(FonteDadosFalsa())
        async with app.run_test(size=(140, 50)) as piloto:
            tela, form = await _abrir_inserir(app, piloto)
            await piloto.pause()
            regiao = _botao(form).region
            await contado(app, form)
            await piloto.pause()
            assert _botao(form).region == regiao
            _digitar(form, "idade", "vinte")             # quatro avisos onde havia um "contando…"
            _digitar(form, "nascimento", "ontem")
            _digitar(form, "email", " a@x.com ")
            await piloto.pause()
            assert _botao(form).region == regiao
            await contado(app, form)
            await piloto.pause()
            assert len(str(form.query_one("#form_avisos").render()).splitlines()) >= 4
            assert _botao(form).region == regiao
    asyncio.run(roteiro())


@pytest.mark.parametrize("tecla", ["m", "x", "D"])
def test_sem_filtro_pede_confirmacao_de_todas_as_linhas(monkeypatch, tmp_path, tecla):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from ratt.db import formularios, formularios_dados
    formulario = {"m": formularios_dados.FormAlterar, "x": formularios_dados.ConfirmarApagarLinhas,
                  "D": formularios_dados.FormDuplicatas}[tecla]

    async def roteiro():
        app = AppDados(FonteDadosFalsa())
        async with app.run_test(size=(140, 50)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            await piloto.press(tecla)
            await piloto.pause()
            conf = app.screen
            assert isinstance(conf, formularios.Confirmar)
            assert conf.pergunta == t("dados.todas", n=5, tabela="pessoas")
            assert app.focused.id == "nao"                   # um enter distraído não abre nada
            await piloto.press("enter")
            await piloto.pause()
            assert app.screen is tela
            await piloto.press(tecla)
            await piloto.pause()
            await piloto.click("#sim")
            await piloto.pause()
            assert isinstance(app.screen, formulario)
            assert app.screen.filtro.vazio()
            await piloto.press("escape")
            await pronto_dados(app)
            assert app.screen is tela and tela.sessao.rascunho.operacoes == []
    asyncio.run(roteiro())


def test_alterar_minusculas_espera_a_contagem_e_guarda_a_vista(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Select
    from ratt.db import formularios_dados

    async def roteiro():
        fonte = FonteDadosFalsa()
        consulta = ConsultaPresa(2, antes_depois=[(("GAB@X.COM",), ("gab@x.com",)), ((" Gi@X.com",), (" gi@x.com",))])
        fonte.consulta = lambda: consulta
        app = AppDados(fonte)
        async with app.run_test(size=(140, 50)) as piloto:
            tela = await _com_filtro_g(app)
            consulta.presa = True
            await piloto.press("m")
            await piloto.pause()
            form = app.screen
            assert isinstance(form, formularios_dados.FormAlterar)
            assert _botao(form).disabled                     # nada escolhido: nada para contar nem pôr
            regiao = _botao(form).region
            linha = form.linhas()[0]
            await _escolher(piloto, linha.query_one(".coluna", Select), "t1.c4")
            await _escolher(piloto, linha.query_one(".tipo", Select), "minusculas")
            sql = ("UPDATE public.pessoas SET email = lower(email) WHERE nome ILIKE 'G%' "
                   "AND (email IS DISTINCT FROM lower(email))")
            assert sql in form.query_one("#form_sql").content.code
            assert form.contando and _botao(form).disabled   # contando: o botão espera
            form.por()                                        # nem o enter num campo passa na frente da contagem
            await piloto.pause()
            assert app.screen is form and tela.sessao.rascunho.operacoes == []
            assert t("dados.f.esperar") in str(form.query_one("#erro").render())
            consulta.solta.set()
            await contado(app, form)
            await piloto.pause()
            assert not _botao(form).disabled and _botao(form).region == regiao
            assert str(form.query_one("#erro").render()) == ""          # o "espere" sai quando a contagem chega
            assert t("chk.linhas_alteradas", n=2, tabela="pessoas") in str(form.query_one("#form_avisos").render())
            amostra = str(form.query_one("#form_amostra").render())
            assert t("dados.f.antes_depois") in amostra
            assert "'GAB@X.COM' → 'gab@x.com'" in amostra and "' Gi@X.com' → ' gi@x.com'" in amostra
            # qualquer mudança desliga o botão até a contagem nova chegar; a vista é a da contagem nova
            consulta.solta.clear()
            consulta.n = 3
            await _escolher(piloto, linha.query_one(".tipo", Select), "maiusculas")
            assert _botao(form).disabled
            consulta.solta.set()
            await contado(app, form)
            assert not _botao(form).disabled
            # um clique que chega antes da mensagem da última mudança não leva a contagem da operação anterior
            linha.query_one(".tipo", Select).value = "trim"
            form.por()
            assert tela.sessao.rascunho.operacoes == [] and app.screen is form
            await piloto.pause()
            await _escolher(piloto, linha.query_one(".tipo", Select), "maiusculas")
            await contado(app, form)
            await piloto.click("#por_no_rascunho")
            await pronto_dados(app)
            assert app.screen is tela
            op, = tela.sessao.rascunho.operacoes
            assert isinstance(op, rascunho.Alterar) and op.vista == 3
            assert op.mudancas == {"t1.c4": rascunho.Mudanca("maiusculas")}
            assert op.filtro == tela.filtro and op.filtro is not tela.filtro
            assert op.comandos(tela.sessao.rascunho.vista()) == [sql.replace("lower", "upper")]
    asyncio.run(roteiro())


def test_alterar_sem_contagem_entra_sem_vista_e_avisa(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Input, Select

    async def roteiro():
        fonte = FonteDadosFalsa()
        consulta = ConsultaPresa(None)                    # o tempo acabou
        fonte.consulta = lambda: consulta
        app = AppDados(fonte)
        async with app.run_test(size=(140, 50)) as piloto:
            tela = await _com_filtro_g(app)
            await piloto.press("m")
            await piloto.pause()
            form = app.screen
            linha = form.linhas()[0]
            await _escolher(piloto, linha.query_one(".coluna", Select), "t1.c3")
            await _escolher(piloto, linha.query_one(".tipo", Select), "valor")
            linha.query_one(Input).value = "30"
            await contado(app, form)
            texto = str(form.query_one("#form_avisos").render()) + str(form.query_one("#form_amostra").render())
            assert t("chk.sem_tempo") in texto and t("dados.f.sem_contagem_vista") in texto
            assert not _botao(form).disabled
            await piloto.click("#por_no_rascunho")
            await pronto_dados(app)
            op, = tela.sessao.rascunho.operacoes
            assert op.vista is None and op.mudancas == {"t1.c3": rascunho.Mudanca("valor", "30")}
    asyncio.run(roteiro())


def test_alterar_mudancas_por_familia_outra_coluna_e_fk(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Button, Select
    from ratt.db import formularios_dados
    from ratt.db.formularios_dados import CampoValor, FonteTravada, FormAlterar

    def opcoes(select):
        return [v for _, v in select._options if isinstance(v, str)]

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 50)) as piloto:
            tela = await _com_filtro_g(app)
            app.push_screen(FormAlterar(tela.sessao, FonteTravada(fonte, tela._trava), "t1", tela.filtro))
            await piloto.pause()
            form = app.screen
            linha = form.linhas()[0]
            colunas = opcoes(linha.query_one(".coluna", Select))
            assert "t1.c1" not in colunas and "t1.c4" in colunas      # identidade ALWAYS não recebe valor
            await _escolher(piloto, linha.query_one(".coluna", Select), "t1.c3")       # idade: número
            assert opcoes(linha.query_one(".tipo", Select)) == ["valor", "vazio", "coluna"]
            await _escolher(piloto, linha.query_one(".tipo", Select), "coluna")
            assert opcoes(linha.query_one(".outra", Select)) == ["t1.c1"]            # só a mesma família
            await _escolher(piloto, linha.query_one(".outra", Select), "t1.c1")
            await _escolher(piloto, linha.query_one(".coluna", Select), "t1.c2")       # nome: texto
            assert opcoes(linha.query_one(".tipo", Select)) == ["valor", "vazio", "coluna", "trim", "minusculas",
                                                                 "maiusculas", "branco_vazio"]
            await _escolher(piloto, linha.query_one(".tipo", Select), "trim")
            await piloto.click("#mais_coluna")
            await piloto.pause()
            assert len(form.linhas()) == 2
            segunda = form.linhas()[1]
            await _escolher(piloto, segunda.query_one(".coluna", Select), "t1.c6")
            await _escolher(piloto, segunda.query_one(".tipo", Select), "valor")
            segunda.query_one(CampoValor).definir("não")
            await piloto.pause()
            assert ("UPDATE public.pessoas SET nome = trim(nome), ativo = FALSE WHERE nome ILIKE 'G%'"
                    in form.query_one("#form_sql").content.code)
            await _escolher(piloto, segunda.query_one(".coluna", Select), "t1.c2")
            assert t("dados.e.coluna_repetida", coluna="nome") in str(form.query_one("#erro").render())
            await piloto.click("#menos_coluna")
            await piloto.pause()
            assert len(form.linhas()) == 1
            assert form.query_one("#form_sql").content.code.startswith("UPDATE public.pessoas SET nome = trim(nome) WHERE")
            await piloto.press("escape")
            await piloto.pause()
            # a chave estrangeira recebe o valor pela lista do pai, como no inserir
            app.push_screen(FormAlterar(tela.sessao, FonteTravada(fonte, tela._trava), "t2", filtro.Filtro()))
            await piloto.pause()
            form = app.screen
            linha = form.linhas()[0]
            await _escolher(piloto, linha.query_one(".coluna", Select), "t2.c2")
            await _escolher(piloto, linha.query_one(".tipo", Select), "valor")
            campo = linha.query_one(CampoValor)
            await piloto.click("#" + campo.query_one(".escolher", Button).id)
            await pronto_dados(app)
            assert isinstance(app.screen, formularios_dados.EscolherLinha)
            await piloto.press("enter")
            await pronto_dados(app)
            assert app.screen is form and campo.valor() == "1"
            assert "UPDATE public.pedidos SET pessoa_id = 1 WHERE pessoa_id IS DISTINCT FROM 1" in \
                form.query_one("#form_sql").content.code
    asyncio.run(roteiro())


def test_apagar_com_filhos_que_impedem_foco_no_cancelar(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from ratt.db import formularios_dados

    async def roteiro():
        fonte = FonteDadosFalsa()
        consulta = ConsultaPresa(2, cascata=[("public.pedidos", "NO ACTION", 2)],
                                 amostra=[FonteDadosFalsa.LINHAS[0], FonteDadosFalsa.LINHAS[2]])
        fonte.consulta = lambda: consulta
        app = AppDados(fonte)
        async with app.run_test(size=(140, 50)) as piloto:
            tela = await _com_filtro_g(app)
            await piloto.press("x")
            await piloto.pause()
            form = app.screen
            assert isinstance(form, formularios_dados.ConfirmarApagarLinhas)
            assert app.focused.id == "cancelar"
            regiao = _botao(form).region
            await contado(app, form)
            await piloto.pause()
            assert _botao(form).region == regiao
            avisos = str(form.query_one("#form_avisos").render())
            assert "✖ " + t("chk.impede_filhos", n=2, tabela="public.pedidos") in avisos
            assert t("chk.linhas_apagadas", n=2, tabela="pessoas") in avisos
            assert t("chk.soft_delete", coluna="ativo") in avisos
            amostra = str(form.query_one("#form_amostra").render())
            assert "Gabriel" in amostra and "Giovana" in amostra and "∅" in amostra
            assert ("amostra", ("public", "pessoas", "nome ILIKE 'G%'", FonteDadosFalsa.NOMES, 5)) in consulta.chamadas
            assert app.focused.id == "cancelar"              # os avisos não tiram o foco do cancelar
            await piloto.press("enter")
            await pronto_dados(app)
            assert app.screen is tela and tela.sessao.rascunho.operacoes == []
            await piloto.press("x")
            await piloto.pause()
            form = app.screen
            await contado(app, form)
            await piloto.click("#por_no_rascunho")           # o aviso impede, mas a pessoa decide
            await pronto_dados(app)
            op, = tela.sessao.rascunho.operacoes
            assert isinstance(op, rascunho.Apagar) and op.vista == 2
            assert op.comandos(tela.sessao.rascunho.vista()) == ["DELETE FROM public.pessoas WHERE nome ILIKE 'G%'"]
    asyncio.run(roteiro())


def test_duplicatas_por_email_normalizado(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Checkbox, Input, Select, SelectionList
    from ratt.db import formularios_dados

    async def roteiro():
        fonte = FonteDadosFalsa()
        grupo = [(1, "Gabriel", 34, "gab@x.com", None, True, False, "gab@x.com"),
                 (7, "Gabi", None, "GAB@x.com ", None, True, True, "gab@x.com"),
                 (9, "Outro", None, "zz@x.com", None, True, False, "zz@x.com")]
        consulta = ConsultaPresa(1, grupos_repetidos=(1, 2), cascata=[], selecionar=grupo)
        fonte.consulta = lambda: consulta
        app = AppDados(fonte)
        async with app.run_test(size=(140, 50)) as piloto:
            tela = await _com_filtro_g(app)
            await piloto.press("D")
            await piloto.pause()
            form = app.screen
            assert isinstance(form, formularios_dados.FormDuplicatas)
            assert _botao(form).disabled
            regiao = _botao(form).region
            form.query_one("#por", SelectionList).select("t1.c4")
            await piloto.pause()
            await piloto.click("#normalizar")                # pela tela: só liga depois de escolher uma coluna de texto
            await piloto.pause()
            assert form.query_one("#normalizar", Checkbox).value and _botao(form).disabled
            m = tela.sessao.rascunho.vista()
            esperado = rascunho.ApagarDuplicatas("t1", tela.filtro, ("t1.c4",), normalizar=True)
            assert esperado.comandos(m)[0] in form.query_one("#form_sql").content.code
            await contado(app, form)
            await piloto.pause()
            assert not _botao(form).disabled and _botao(form).region == regiao     # os botões não andaram
            avisos = str(form.query_one("#form_avisos").render())
            assert t("chk.grupos_repetidos", grupos=1) in avisos and t("chk.linhas_apagadas", n=1, tabela="pessoas") in avisos
            amostra = str(form.query_one("#form_amostra").render())
            assert "Gabriel" in amostra and "Gabi" in amostra and "Outro" not in amostra    # um grupo só
            assert t("dados.f.fica") in amostra and t("dados.f.sai") in amostra
            # manter as N mais recentes por uma data
            await _escolher(piloto, form.query_one("#manter", Select), "recente")
            assert t("op.e.sem_ordem") in str(form.query_one("#erro").render()) and _botao(form).disabled
            await _escolher(piloto, form.query_one("#ordem", Select), "t1.c5")
            form.query_one("#n", Input).value = "2"
            await contado(app, form)
            assert "nascimento DESC NULLS LAST" in form.query_one("#form_sql").content.code
            await piloto.click("#por_no_rascunho")
            await pronto_dados(app)
            op, = tela.sessao.rascunho.operacoes
            assert isinstance(op, rascunho.ApagarDuplicatas) and op.vista == 1
            assert (op.por, op.normalizar, op.manter, op.ordem, op.n) == (("t1.c4",), True, "recente", "t1.c5", 2)
    asyncio.run(roteiro())


def test_contagens_abandonadas_param_e_nao_atrasam_a_nova(monkeypatch, tmp_path):
    """Várias mudanças enquanto uma contagem lenta roda: as contagens dos estados que passaram desistem antes de
    perguntar qualquer coisa ao banco (também as que esperavam a trava da tela); só a do estado atual roda."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Input, Select

    async def roteiro():
        fonte = FonteDadosFalsa()
        consulta = ConsultaPresa(2)
        fonte.consulta = lambda: consulta
        app = AppDados(fonte)
        async with app.run_test(size=(140, 50)) as piloto:
            tela = await _com_filtro_g(app)
            await piloto.press("m")
            await piloto.pause()
            form = app.screen
            linha = form.linhas()[0]
            await _escolher(piloto, linha.query_one(".coluna", Select), "t1.c3")
            await _escolher(piloto, linha.query_one(".tipo", Select), "valor")
            consulta.chamadas.clear()
            consulta.presa = True
            campo = linha.query_one(Input)
            for valor in ("11", "22", "33", "44"):
                campo.value = valor
                await asyncio.sleep(0.35)                 # passa o atraso de digitação: cada estado pede a sua
            consulta.solta.set()
            await contado(app, form)
            assert not _botao(form).disabled
            primeira, *resto = consulta.chamadas
            assert "11" in str(primeira)                  # a que já estava no banco termina
            assert resto and all("44" in str(c) for c in resto), consulta.chamadas
            await piloto.click("#por_no_rascunho")
            await pronto_dados(app)
            op, = tela.sessao.rascunho.operacoes
            assert op.mudancas["t1.c3"].valor == "44" and op.vista == 2
    asyncio.run(roteiro())


def test_banco_muda_por_baixo_do_formulario_reconta(monkeypatch, tmp_path):
    """A vigia releu o banco com o formulário aberto (a coluna do filtro foi renomeada): o SQL dos campos não é mais
    o contado. O "pôr" não entra com a contagem velha: reconta, diz isso e, quando a contagem nova chega, entra."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Select

    async def roteiro():
        fonte = FonteDadosFalsa()
        consulta = ConsultaPresa(2)
        fonte.consulta = lambda: consulta
        app = AppDados(fonte)
        async with app.run_test(size=(140, 50)) as piloto:
            tela = await _com_filtro_g(app)
            await piloto.press("m")
            await piloto.pause()
            form = app.screen
            linha = form.linhas()[0]
            await _escolher(piloto, linha.query_one(".coluna", Select), "t1.c4")
            await _escolher(piloto, linha.query_one(".tipo", Select), "minusculas")
            await contado(app, form)
            assert not _botao(form).disabled
            tela.sessao.rascunho.base.tabelas["t1"].colunas[1].nome = "nome2"     # renomeada por fora
            consulta.n = 3
            consulta.presa = True                           # a recontagem espera: dá para ver o "recontando…"
            await piloto.click("#por_no_rascunho")
            await esperar_que(piloto, lambda: form.contando, mensagem="não recontou")
            assert app.screen is form and tela.sessao.rascunho.operacoes == []
            assert t("dados.f.recontando") in str(form.query_one("#erro").render())
            assert form.contando and _botao(form).disabled
            consulta.solta.set()
            await contado(app, form)
            assert str(form.query_one("#erro").render()) == "" and not _botao(form).disabled
            await piloto.click("#por_no_rascunho")
            await pronto_dados(app)
            op, = tela.sessao.rascunho.operacoes
            assert op.vista == 3
            assert "nome2 ILIKE 'G%'" in op.comandos(tela.sessao.rascunho.vista())[0]
    asyncio.run(roteiro())


def test_duplicatas_sem_data_nao_oferece_recente_e_normalizar_so_com_texto(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Checkbox, Select, SelectionList
    from ratt.db.formularios_dados import FonteTravada, FormDuplicatas

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(140, 50)) as piloto:
            await pronto_dados(app)
            tela = app.screen
            app.push_screen(FormDuplicatas(tela.sessao, FonteTravada(fonte, tela._trava), "t2", filtro.Filtro()))
            await piloto.pause()
            form = app.screen
            manter = [v for _, v in form.query_one("#manter", Select)._options if isinstance(v, str)]
            assert manter == ["menor", "maior"] and not form.query_one("#grupo_ordem").display   # pedidos não tem data
            await piloto.press("escape")
            await piloto.pause()
            app.push_screen(FormDuplicatas(tela.sessao, FonteTravada(fonte, tela._trava), "t1", filtro.Filtro()))
            await piloto.pause()
            form = app.screen
            assert "recente" in [v for _, v in form.query_one("#manter", Select)._options if isinstance(v, str)]
            por, normalizar = form.query_one("#por", SelectionList), form.query_one("#normalizar", Checkbox)
            assert normalizar.disabled
            por.select("t1.c4")
            await piloto.pause()
            await piloto.click("#normalizar")
            await piloto.pause()
            assert normalizar.value and not normalizar.disabled
            por.deselect("t1.c4")
            por.select("t1.c3")                               # idade: não é texto
            await piloto.pause()
            assert normalizar.disabled and not normalizar.value
    asyncio.run(roteiro())


# ── arquivos: a grade mostra o tamanho e `s` salva ──

def test_celula_arquivo():
    from ratt.db.arquivos import TamanhoArquivo
    from ratt.db.tela import _celula
    assert _celula(TamanhoArquivo(2048), "bytea").plain == "‹arquivo · 2 KB›"


ABIF = b"ABIF" + bytes(range(256)) * 3


class FonteArquivos(FonteDadosFalsa):
    """Uma tabela `amostras (id pk, f bytea, nome text)`; o `bytea` da grade é só o tamanho."""
    NOMES = ["id", "f", "nome"]
    TIPOS = ["int8", "bytea", "text"]

    def __init__(self, pk=True):
        from ratt.db.arquivos import TamanhoArquivo
        m = Modelo("public", {"t1": Tabela("t1", "public", "amostras", [
            Coluna("t1.c1", "id", "text", pk=pk), Coluna("t1.c2", "f", "bytea"), Coluna("t1.c3", "nome", "text")])})
        super().__init__(m)
        self.LINHAS = [("a b/c", TamanhoArquivo(len(ABIF)), "texto da grade"), ("2", None, None),
                       ("3", TamanhoArquivo(0), "")]
        self.NOMES, self.TIPOS = ["id", "f", "nome"], ["text", "bytea", "text"]
        self.lidos = []

    def valor_da_linha(self, esquema, tabela, coluna, chave):
        self.lidos.append((tabela, coluna, dict(chave)))
        return ABIF if chave["id"] == "a b/c" else b""


async def _s_em(app, piloto, linha, coluna):
    from ratt.db.grade_dados import Grade
    await pronto_dados(app)
    app.screen.query_one(Grade).move_cursor(row=linha, column=coluna)
    await piloto.press("s")
    await pronto_dados(app)


def test_s_salva_bytea(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from textual.widgets import Input
    from ratt.db import formularios_dados
    avisos = []
    monkeypatch.setattr(tela_dados_mod(), "notify", lambda self, msg, **kw: avisos.append(msg))

    async def roteiro():
        fonte = FonteArquivos()
        app = AppDados(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await _s_em(app, piloto, 0, 1)
            modal = app.screen
            assert isinstance(modal, formularios_dados.SalvarArquivo)
            assert fonte.lidos == [("amostras", "f", {"id": "a b/c"})]
            assert modal.query_one(Input).value == "~/amostras_a_b_c_f.ab1"
            await piloto.press("enter")
            await pronto_dados(app)
            assert (tmp_path / "amostras_a_b_c_f.ab1").read_bytes() == ABIF
            assert any("amostras_a_b_c_f.ab1" in a for a in avisos)
    asyncio.run(roteiro())


def tela_dados_mod():
    from ratt.db import tela_dados
    return tela_dados.TelaDados


def test_s_texto(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from textual.widgets import Input

    async def roteiro():
        fonte = FonteArquivos()
        app = AppDados(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await _s_em(app, piloto, 0, 2)
            assert app.screen.query_one(Input).value == "~/amostras_a_b_c_nome.txt"
            await piloto.press("enter")
            await pronto_dados(app)
            assert (tmp_path / "amostras_a_b_c_nome.txt").read_bytes() == "texto da grade".encode()
            assert fonte.lidos == []
    asyncio.run(roteiro())


def _avisando(monkeypatch):
    avisos = []
    monkeypatch.setattr(tela_dados_mod(), "notify", lambda self, msg, **kw: avisos.append(msg))
    return avisos


def test_s_sem_pk_recusa_bytea(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    avisos = _avisando(monkeypatch)

    async def roteiro():
        app = AppDados(FonteArquivos(pk=False))
        async with app.run_test(size=(120, 40)) as piloto:
            await _s_em(app, piloto, 0, 1)
            assert avisos[-1] == t("arq.s_sem_pk") and type(app.screen).__name__ == "TelaDados"
            await _s_em(app, piloto, 0, 2)                       # o texto não precisa de chave
            assert type(app.screen).__name__ == "SalvarArquivo"
    asyncio.run(roteiro())


def test_s_vazio_e_tipo_avisam(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    avisos = _avisando(monkeypatch)

    async def roteiro():
        app = AppDados(FonteArquivos())
        async with app.run_test(size=(120, 40)) as piloto:
            await _s_em(app, piloto, 1, 1)                       # bytea ∅
            await _s_em(app, piloto, 2, 1)                       # bytea de 0 bytes
            await _s_em(app, piloto, 1, 2)                       # texto ∅
            assert avisos == [t("arq.s_vazio")] * 3
            assert type(app.screen).__name__ == "TelaDados"
    asyncio.run(roteiro())


def test_s_coluna_numero_avisa(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    avisos = _avisando(monkeypatch)

    async def roteiro():
        fonte = FonteDadosFalsa()
        app = AppDados(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await _s_em(app, piloto, 0, 2)                       # idade
            assert avisos == [t("arq.s_tipo", tipo="int4")]
    asyncio.run(roteiro())


def test_s_sobrescrever_pergunta_e_cancelar_nao_escreve(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from textual.widgets import Button
    from ratt.db import formularios
    alvo = tmp_path / "amostras_a_b_c_f.ab1"
    alvo.write_bytes(b"antigo")

    async def roteiro():
        app = AppDados(FonteArquivos())
        async with app.run_test(size=(120, 40)) as piloto:
            await _s_em(app, piloto, 0, 1)
            await piloto.press("enter")
            await piloto.pause(0.1)
            conf = app.screen
            assert isinstance(conf, formularios.Confirmar)
            assert app.focused is conf.query_one("#nao", Button)
            await piloto.press("enter")                          # o enter distraído cancela
            await piloto.pause(0.1)
            assert alvo.read_bytes() == b"antigo"
            assert type(app.screen).__name__ == "SalvarArquivo"
            await piloto.press("enter")
            await piloto.pause(0.1)
            await piloto.click("#sim")
            await pronto_dados(app)
            assert alvo.read_bytes() == ABIF
    asyncio.run(roteiro())


def test_s_pasta_inexistente_erro(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Input, Static

    async def roteiro():
        app = AppDados(FonteArquivos())
        async with app.run_test(size=(120, 40)) as piloto:
            await _s_em(app, piloto, 0, 1)
            modal = app.screen
            modal.query_one(Input).value = str(tmp_path / "nao" / "existe.bin")
            await piloto.press("enter")
            await piloto.pause(0.1)
            assert str(tmp_path / "nao") in str(modal.query_one("#erro", Static).render())
            assert type(app.screen).__name__ == "SalvarArquivo" and not (tmp_path / "nao").exists()
    asyncio.run(roteiro())


def test_salvar_cabe_80x24(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from textual.widgets import Input

    async def roteiro():
        app = AppDados(FonteArquivos())
        async with app.run_test(size=(80, 24)) as piloto:
            await _s_em(app, piloto, 0, 1)
            modal = app.screen
            modal.query_one(Input).value = str(tmp_path / "nao" / "existe.bin")
            await piloto.press("enter")
            await piloto.pause(0.1)
            for seletor in ("#caminho", "#salvar", "#cancelar", "#erro"):
                w = modal.query_one(seletor)
                assert w.region.height >= 1 and modal.screen.region.contains_region(w.region), seletor
    asyncio.run(roteiro())


def test_s_nome_sugerido_sem_barra_nem_espaco(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from textual.widgets import Input

    async def roteiro():
        fonte = FonteArquivos()
        fonte.m.tabelas["t1"].nome = "a/b c"
        fonte.NOMES = ["id", "f\\x y", "nome"]
        app = AppDados(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await _s_em(app, piloto, 0, 1)
            assert app.screen.query_one(Input).value == "~/a_b_c_a_b_c_f_x_y.ab1"
    asyncio.run(roteiro())


def test_s_ignora_segundo_s_com_leitura_no_ar(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        fonte = FonteArquivos()
        app = AppDados(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto_dados(app)
            app.screen._salvando = True
            await _s_em(app, piloto, 0, 1)
            assert fonte.lidos == [] and type(app.screen).__name__ == "TelaDados"
    asyncio.run(roteiro())


def test_s_com_bytea_na_chave_recusa(monkeypatch, tmp_path):
    from ratt.db.arquivos import TamanhoArquivo
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    avisos = _avisando(monkeypatch)

    async def roteiro():
        fonte = FonteArquivos()
        fonte.LINHAS = [(TamanhoArquivo(3), TamanhoArquivo(4), "x")]
        fonte.TIPOS = ["bytea", "bytea", "text"]
        app = AppDados(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await _s_em(app, piloto, 0, 1)
            assert avisos == [t("arq.s_sem_pk")] and fonte.lidos == []
    asyncio.run(roteiro())
