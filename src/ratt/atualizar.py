"""check-update e upgrade, no espírito do  dnf check-update  /  dnf upgrade --refresh.

A fonte é o repositório no GitHub: a versão vem de src/ratt/__init__.py e o que mudou vem do
CHANGELOG.md. Nada é instalado sem você responder s.
"""
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from ratt import REPOSITORIO, __version__


def _repositorio():
    return os.environ.get("RATT_REPO") or REPOSITORIO


def _baixar(caminho):
    url = f"https://raw.githubusercontent.com/{_repositorio()}/main/{caminho}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.read().decode("utf-8")


def _tupla(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def versao_remota():
    texto = _baixar("src/ratt/__init__.py")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', texto)
    if not m:
        raise RuntimeError("o repositório não declara __version__")
    return m.group(1)


def novidades(desde):
    """As seções do CHANGELOG mais novas que  desde , na ordem em que estão no arquivo."""
    try:
        texto = _baixar("CHANGELOG.md")
    except (urllib.error.URLError, OSError):
        return []
    secoes, atual = [], None
    for linha in texto.splitlines():
        m = re.match(r"^##\s+\[?(\d+\.\d+\.\d+)\]?", linha)
        if m:
            atual = [m.group(1), []]
            secoes.append(atual)
        elif atual is not None:
            atual[1].append(linha)
    return [(v, "\n".join(l).strip()) for v, l in secoes if _tupla(v) > _tupla(desde)]


def como_foi_instalado():
    """pipx, pip ou uma cópia de desenvolvimento (pip install -e) — cada um se atualiza de um jeito."""
    prefixo = sys.prefix
    if os.sep + "pipx" + os.sep in prefixo or "/pipx/venvs/" in prefixo:
        return "pipx"
    try:
        import json
        from importlib.metadata import distribution
        direto = json.loads(distribution("ratt").read_text("direct_url.json") or "{}")
        if direto.get("dir_info", {}).get("editable"):
            return "desenvolvimento"
    except Exception:
        pass
    return "pip"


def comando_de_atualizacao(forma):
    if forma == "pipx":
        return ["pipx", "upgrade", "ratt"]
    if forma == "pip":
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade"]
        if sys.prefix == sys.base_prefix:            # fora de ambiente virtual: foi instalado com --user
            cmd.append("--user")
        return cmd + [f"git+https://github.com/{_repositorio()}.git"]
    return None


def reaplicar_com_a_versao_nova():
    """O processo que está rodando ainda é o ratt velho: quem reaplica é um ratt novo."""
    ratt = shutil.which("ratt")
    cmd = [ratt, "sync"] if ratt else [sys.executable, "-m", "ratt", "sync"]
    return subprocess.run(cmd).returncode
