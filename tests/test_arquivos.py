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
