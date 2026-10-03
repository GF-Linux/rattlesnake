"""Os testes do ratt. Precisam de um Python com pandas:

    python -m pytest tests          (ou)          python tests/test_ratt.py

A releitura só age num terminal, então o exemplo roda dentro de um pty: um terminal de mentira.
"""
import os
import pty
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
EXEMPLO = RAIZ / "tests" / "exemplos" / "tudo.py"
LIGA = ("import sys, runpy; sys.path.insert(0, %r); import ratt.releitura as r; r.ligar(); "
        "runpy.run_path(%r, run_name='__main__')") % (str(RAIZ / "src"), str(EXEMPLO))
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _no_terminal(cmd, env=None):
    mestre, escravo = pty.openpty()
    os.set_blocking(mestre, False)
    ambiente = dict(os.environ, COLUMNS="200", LINES="50", **(env or {}))
    p = subprocess.Popen(cmd, stdout=escravo, stderr=escravo, stdin=escravo, env=ambiente, cwd=RAIZ)
    os.close(escravo)
    saida = b""
    while True:
        try:
            pedaco = os.read(mestre, 65536)
            if pedaco:
                saida += pedaco
                continue
        except BlockingIOError:
            pass
        except OSError:
            break
        if p.poll() is not None:
            try:
                while True:
                    pedaco = os.read(mestre, 65536)
                    if not pedaco:
                        break
                    saida += pedaco
            except OSError:
                pass
            break
    os.close(mestre)
    return p.wait(), saida.decode("utf-8", "replace")


def test_no_terminal_sai_relido():
    codigo, saida = _no_terminal([sys.executable, "-c", LIGA])
    texto = ANSI.sub("", saida)
    assert codigo == 0, texto[-2000:]
    assert "Traceback" not in texto, texto[-2000:]
    for marca in ["Li/Co", "Resumo", "head ·", "dtypes ·", "columns ·", "index ·", "isna().sum()",
                  "nunique()", "value_counts ·", "value_counts(normalize=True)", "unique()",
                  'describe(include="all")', "filtro ·", ".sort_values(", ".dropna()", ".merge(",
                  ".str.strip()", "groupby ·", "groupby · agg", "groupby · duas chaves", "pivot_table ·",
                  "crosstab ·", "pd.cut", "to_datetime ·", ".dt ·", "to_period ·", "DataFrame ·",
                  "Series ·", "mean() ·", "corr() ·", "DatetimeIndex ·", "date_range ·", "resample ·",
                  ".rolling(7).mean()", ".diff()", "tupla · 4 itens", "tupla · 2 itens", "── 2/4 · tabela.shape"]:
        assert marca in texto, f"faltou {marca!r} na saída"


def test_fora_do_terminal_sai_o_pandas():
    r = subprocess.run([sys.executable, "-c", LIGA], capture_output=True, text=True, cwd=RAIZ)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "\x1b[" not in r.stdout
    assert "Li/Co" not in r.stdout


def test_ratt_zero_desliga():
    codigo, saida = _no_terminal([sys.executable, "-c", LIGA], env={"RATT": "0"})
    assert codigo == 0
    assert "Li/Co" not in ANSI.sub("", saida)


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
            print("ok ", nome)
