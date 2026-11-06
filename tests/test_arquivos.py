"""Testes de arquivos.py: ler, rotular e abreviar o SQL mostrado."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from ratt.db import arquivos
from ratt.db.arquivos import ArquivoRecusado, ler, sql_para_ver, tamanho_legivel, extensao, TETO
from ratt.db.formularios_dados import _mostra


@pytest.fixture(autouse=True)
def _pt(monkeypatch):
    monkeypatch.setenv("RATT_LANG", "pt")


def test_ler_bytea(tmp_path):
    f = tmp_path / "x.ab1"
    f.write_bytes(b"ABIF\x00\x01")
    assert ler(str(f), "bytea") == ("\\x414249460001", "‹x.ab1 · 6 B›")


def test_ler_texto_utf8_com_bom(tmp_path):
    f = tmp_path / "s.txt"
    f.write_bytes("﻿ACGT\n".encode("utf-8"))
    assert ler(str(f), "text")[0] == "ACGT\n"


def test_recusas(tmp_path):
    (tmp_path / "nul").write_bytes(b"a\x00b")
    (tmp_path / "lat").write_bytes("é".encode("latin-1"))
    (tmp_path / "grande").write_bytes(b"x" * (TETO + 1))
    for caminho, tipo in [(tmp_path / "nao", "bytea"), (tmp_path, "bytea"), (tmp_path / "nul", "text"),
                          (tmp_path / "lat", "text"), (tmp_path / "grande", "bytea")]:
        with pytest.raises(ArquivoRecusado):
            ler(str(caminho), tipo)


def test_recusa_arquivo_especial_e_tipo_antes_de_ler(tmp_path):
    with pytest.raises(ArquivoRecusado):
        ler("/dev/null", "bytea")
    (tmp_path / "g").write_bytes(b"x" * (TETO + 1))
    with pytest.raises(ArquivoRecusado, match="integer"):
        ler(str(tmp_path / "g"), "integer")


def test_vazio(tmp_path):
    f = tmp_path / "v"
    f.write_bytes(b"")
    valor, rotulo = ler(str(f), "bytea")
    assert valor == "\\x" and "0 B" in rotulo


def test_tamanho_legivel():
    assert tamanho_legivel(0) == "0 B"
    assert tamanho_legivel(1023) == "1023 B"
    assert tamanho_legivel(1024) == "1 KB"
    assert tamanho_legivel(218_000) == "213 KB"
    assert tamanho_legivel(1048575) == "1,0 MB"
    assert tamanho_legivel(5 * 1024 * 1024) == "5,0 MB"


def test_extensao():
    for dados, ext in [(b"ABIF..", ".ab1"), (b"%PDF-", ".pdf"), (b"\x89PNG..", ".png"), (b"\xff\xd8\xff\xe0", ".jpg"),
                       (b"\x1f\x8b\x08", ".gz"), (b"PK\x03\x04", ".zip"), (b"xyz", ".bin")]:
        assert extensao(dados) == ext


def _aspas_balanceadas(sql):
    sem = re.sub(r'"(?:[^"]|"")*"', "", sql)
    return sem.count("'") % 2 == 0


def test_sql_para_ver():
    hexa = "INSERT INTO t VALUES (E'\\\\x" + "ab" * 500 + "');"
    saida = sql_para_ver(hexa)
    assert "/* arquivo · 500 B */" in saida and len(saida) < 120 and saida.count("'") == 2 and "E'" in saida
    longo = "UPDATE t SET c = 'it''s " + "a" * 200 + "';"
    saida = sql_para_ver(longo)
    assert "/* texto · " in saida and _aspas_balanceadas(saida)
    curto = "SELECT \"a'b\", 'x';"
    assert sql_para_ver(curto) == curto
    assert sql_para_ver("SELECT '" + "a" * 120 + "'") == "SELECT '" + "a" * 120 + "'"
    assert "/* texto" in sql_para_ver("SELECT '" + "a" * 121 + "'")
    assert sql_para_ver("-- it's\nSELECT 1;") == "-- it's\nSELECT 1;"
    # não corta no meio de um par de escape
    cortado = sql_para_ver("SELECT '" + "a" * 39 + "''" + "b" * 200 + "'")
    assert _aspas_balanceadas(cortado)
    cortado = sql_para_ver("SELECT E'" + "a" * 38 + "\\\\" + "b" * 200 + "'")
    assert _aspas_balanceadas(cortado) and "\\\\…" in cortado


def test_mostra_bytes():
    assert "‹arquivo · 2 KB›" in _mostra(b"\x00" * 2048)
    assert "‹arquivo · 2 KB›" in _mostra(memoryview(b"\x00" * 2048))
    assert len(_mostra("a" * 500)) < 100


# ── arquivos grandes: tempo linear e mensagens curtas ──

def _amostras():
    from ratt.db import rascunho as rs
    from ratt.db.modelo import Coluna, Modelo, Tabela
    m = Modelo("public", {"t1": Tabela("t1", "public", "amostras", [
        Coluna("t1.c1", "id", "text", pk=True), Coluna("t1.c2", "f", "bytea"), Coluna("t1.c3", "seq", "varchar(50)")])})
    return rs, m


def test_literal_de_10_mb_nao_trava():
    """`comandos_de` (a cada tecla e redesenho) e `sql_para_ver` andam em C sobre um literal de 10 MB. O teto é
    folgado: antes levavam ~1,6 s e ~1,2 s; agora ~0,05 s."""
    import time
    rs, m = _amostras()
    dados = bytes(range(256)) * (10 * 1024 * 1024 // 256)
    casos = [{"t1.c1": "a", "t1.c2": "\\x" + dados.hex()},              # bytea: E'\\x…'
             {"t1.c1": "b", "t1.c3": "ACGT\n" * (2 * 1024 * 1024)},       # texto sem aspas
             {"t1.c1": "c", "t1.c3": "it's a \\ path\n" * 700_000}]      # texto com aspas e barras (vai para o `re`)
    for valores in casos:
        op = rs.Inserir("t1", valores)
        inicio = time.monotonic()
        (cmd,) = rs.comandos_de(op, m)
        visto = sql_para_ver(cmd + ";")
        assert time.monotonic() - inicio < 2.0, list(valores)
        assert len(visto) < 300 and _aspas_balanceadas(visto)


def test_vinte_arquivos_no_rascunho_tecla_rapida():
    """20 × 300 KB no rascunho: a vista (que cada tecla de um formulário refaz) e o SQL do painel."""
    import time
    rs, m = _amostras()
    r = rs.Rascunho(m)
    hexa = "\\x" + (bytes(range(256)) * 1200)[:300_000].hex()
    for k in range(20):
        r.acrescentar(rs.Inserir("t1", {"t1.c1": str(k), "t1.c2": hexa}))
    inicio = time.monotonic()
    r.vista()
    texto = sql_para_ver(r.sql_texto())
    assert time.monotonic() - inicio < 1.0
    assert texto.count("/* arquivo · 293 KB */") == 20


def test_texto_longo_com_muitos_escapes_divide_igual():
    """Mais aspas dobradas e barras do que o `str.find` pula: o resto vai para o `re`, com o mesmo resultado."""
    from ratt.db import rodar
    com_e, sem_e = "a'';b\\\\c\\';d" * 200, "a'';b\\" * 200       # o ; dentro do texto não separa
    sql = f"insert into t values (E'{com_e}', '{sem_e}'); select ';'; select E'x\\';y'"
    assert [c.texto for c in rodar.dividir(sql)] == [
        f"insert into t values (E'{com_e}', '{sem_e}')", "select ';'", "select E'x\\';y'"]
    assert len(rodar.dividir("select E'" + "\\\\" * 100 + "\\';")) == 1         # a última barra leva a aspa: não fecha


def test_valor_curto():
    from ratt.db.arquivos import valor_curto
    assert valor_curto("Ana") == "Ana"
    assert valor_curto("x" * 60) == "x" * 60
    assert valor_curto("\\x" + "ab" * 300_000) == "‹arquivo · 293 KB›"
    curto = valor_curto(">seq1\nACGT" + "A" * 500_000)
    assert curto.startswith(">seq1↵ACGT") and "…" in curto and "488 KB de texto" in curto and len(curto) < 80
