"""Testes da tela Dados e do que ela divide com o canvas: a sessão de rascunho e o painel."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ratt.db import guardar, rascunho, sessao
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
