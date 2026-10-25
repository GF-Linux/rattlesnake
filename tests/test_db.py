"""Os testes do ratt db que não precisam de PostgreSQL: o sistema, a sonda e o idioma.

    python -m pytest tests/test_db.py

A sonda conversa com um servidor de mentira, que responde o que o PostgreSQL responderia.
"""
import socket
import struct
import subprocess
import sys
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from ratt.db import idioma, sistema, sonda      # noqa: E402

# ── o sistema ───────────────────────────────────────────────────────────────

OS_RELEASES = {
    "ubuntu": ('NAME="Ubuntu"\nVERSION_ID="24.04"\nID=ubuntu\nID_LIKE=debian\nPRETTY_NAME="Ubuntu 24.04.1 LTS"', "debian"),
    "debian": ('PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian', "debian"),
    "mint": ('NAME="Linux Mint"\nID=linuxmint\nID_LIKE="ubuntu debian"', "debian"),
    "fedora": ("NAME=\"Fedora Linux\"\nID=fedora\nVERSION_ID=44", "redhat"),
    "rocky": ('NAME="Rocky Linux"\nID="rocky"\nID_LIKE="rhel centos fedora"', "redhat"),
    "alma": ('ID="almalinux"\nID_LIKE="rhel centos fedora"', "redhat"),
    "arch": ("NAME=\"Arch Linux\"\nID=arch", "arch"),
    "manjaro": ('ID=manjaro\nID_LIKE=arch', "arch"),
    "um derivado desconhecido do ubuntu": ('ID=minhadistro\nID_LIKE="ubuntu debian"', "debian"),
    "alpine": ("ID=alpine", None),
}


def test_familias():
    for nome, (texto, esperado) in OS_RELEASES.items():
        assert sistema.familia(sistema.ler_os_release(texto)) == esperado, nome


def test_os_release_ignora_comentarios_e_aspas():
    campos = sistema.ler_os_release("# comentário\n\nPRETTY_NAME='Fedora Linux 44'\nID=fedora\n")
    assert campos == {"PRETTY_NAME": "Fedora Linux 44", "ID": "fedora"}


def test_receita_para_cada_familia():
    for familia in ("debian", "redhat", "arch"):
        receita = sistema.RECEITAS[familia]
        assert receita.familia == familia and receita.pacotes and receita.grupos_sudo


# ── a sonda ─────────────────────────────────────────────────────────────────

def _servidor(resposta):
    """Um servidor que lê a mensagem inicial, devolve `resposta` e fecha. Devolve a porta."""
    escuta = socket.socket()
    escuta.bind(("127.0.0.1", 0))
    escuta.listen(1)
    recebido = {}

    def atender():
        conexao, _ = escuta.accept()
        with conexao:
            tamanho, = struct.unpack("!i", conexao.recv(4))
            recebido["inicial"] = conexao.recv(tamanho - 4)
            conexao.sendall(resposta)
        escuta.close()

    threading.Thread(target=atender, daemon=True).start()
    return escuta.getsockname()[1], recebido


def _autenticacao(codigo, extra=b""):
    corpo = struct.pack("!i", codigo) + extra
    return b"R" + struct.pack("!i", len(corpo) + 4) + corpo


def _erro(sqlstate, mensagem):
    corpo = b"SFATAL\0C" + sqlstate.encode() + b"\0M" + mensagem.encode() + b"\0\0"
    return b"E" + struct.pack("!i", len(corpo) + 4) + corpo


def test_sonda_scram():
    porta, recebido = _servidor(_autenticacao(10, b"SCRAM-SHA-256\0\0"))
    r = sonda.sondar(porta=porta, usuario="jared", banco="postgres")
    assert r.porta_aberta and r.metodo == "scram-sha-256" and r.pede_senha
    assert b"user\0jared\0database\0postgres\0" in recebido["inicial"]


def test_sonda_md5():
    porta, _ = _servidor(_autenticacao(5, b"salt"))
    assert sonda.sondar(porta=porta).metodo == "md5"


def test_sonda_trust_nao_pede_senha():
    porta, _ = _servidor(_autenticacao(0))
    r = sonda.sondar(porta=porta)
    assert r.metodo == "trust" and not r.pede_senha


def test_sonda_recusa_ident():
    porta, _ = _servidor(_erro("28000", 'autenticação do tipo Ident falhou para o usuário "jared"'))
    r = sonda.sondar(porta=porta)
    assert r.metodo == "recusa" and r.codigo == "28000" and "Ident" in r.mensagem


def test_sonda_porta_fechada():
    livre = socket.socket()
    livre.bind(("127.0.0.1", 0))
    porta = livre.getsockname()[1]
    livre.close()
    assert not sonda.sondar(porta=porta).porta_aberta
    assert not sonda.porta_aberta(porta=porta)


# ── o idioma ────────────────────────────────────────────────────────────────

def test_idioma_pt_e_en(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("RATT_LANG", raising=False)
    assert idioma.atual() == "pt"
    assert idioma.t("r.bancos") == "bancos"
    idioma.gravar("en")
    assert idioma.atual() == "en" and idioma.t("r.bancos") == "databases"
    monkeypatch.setenv("RATT_LANG", "pt")          # a variável vence o que está salvo
    assert idioma.t("r.bancos") == "bancos"
    monkeypatch.setenv("RATT_LANG", "klingon")     # idioma que não existe: português
    assert idioma.atual() == "pt"


def test_todo_texto_tem_os_dois_idiomas():
    faltando = [chave for chave, par in idioma.TEXTOS.items() if set(par) != set(idioma.IDIOMAS)]
    assert not faltando, faltando


# ── o comando ───────────────────────────────────────────────────────────────

def _ratt(*args, **env):
    import os
    ambiente = dict(os.environ, NO_COLOR="1", **env)
    return subprocess.run([sys.executable, "-m", "ratt", *args], capture_output=True, text=True,
                          env=dict(ambiente, PYTHONPATH=str(RAIZ / "src")))


def test_ajuda_nos_dois_idiomas():
    assert "o idioma da interface" in _ratt("db", "-h", RATT_LANG="pt").stdout
    assert "the interface language" in _ratt("db", "-h", RATT_LANG="en").stdout


def test_erros_de_uso_saem_com_2():
    p = _ratt("db", "nada-disso", RATT_LANG="pt")
    assert p.returncode == 2 and "comando desconhecido" in p.stderr
    p = _ratt("db", "status", "a-mais", RATT_LANG="en")
    assert p.returncode == 2 and "unexpected argument" in p.stderr
