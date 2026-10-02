"""Onde a releitura mora: descobrir o Python certo, instalar, remover e lembrar.

Dois escopos, e eles não se misturam:

  local   o ambiente virtual da pasta (.venv, venv, env) ou o que está ativo ($VIRTUAL_ENV).
          Vale só para quem usa aquele ambiente.
  global  a pasta de pacotes do USUÁRIO de um Python (site.getusersitepackages()).
          Vale para aquele Python em qualquer pasta — mas um ambiente virtual não enxerga
          essa pasta (é assim que o venv isola), então o global não entra num .venv.

O pyenv merece um aviso: um  .python-version  escolhe o INTERPRETADOR da pasta, não isola
pacotes. Instalar "na pasta" de quem só tem .python-version seria instalar para todo projeto
que usa a mesma versão. Por isso o ratt recusa e explica, a menos que o --python seja explícito.

Em cada lugar o ratt grava dois arquivos e mais nada:
  ratt_releitura.py   uma cópia da releitura, com a versão no topo
  ratt.pth            uma linha que o Python lê ao começar: import ratt_releitura; ...ligar()
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

from ratt import __version__

MODULO = "ratt_releitura.py"
PTH = "ratt.pth"
LINHA_PTH = "import ratt_releitura; ratt_releitura.ligar()\n"

# o que o Python-alvo responde sobre si mesmo — roda DENTRO dele
_PERGUNTA = r"""
import json, site, sys, sysconfig, importlib.util
print(json.dumps({
    "executavel": sys.executable,
    "versao": "%d.%d.%d" % sys.version_info[:3],
    "maior_menor": [sys.version_info[0], sys.version_info[1]],
    "pacotes": sysconfig.get_paths()["purelib"],
    "usuario": site.getusersitepackages(),
    "usuario_ligado": bool(site.ENABLE_USER_SITE),
    "em_venv": sys.prefix != sys.base_prefix,
    "prefixo": sys.prefix,
    "pandas": importlib.util.find_spec("pandas") is not None,
}))
"""


class Recusa(Exception):
    """O ratt não instala ali, e a mensagem diz por quê e o que fazer."""


# ── o registro: onde a releitura foi instalada ─────────────────────────────

def _pasta_dados():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "ratt"


def _arquivo_registro():
    return _pasta_dados() / "instalacoes.json"


def ler_registro():
    try:
        return json.loads(_arquivo_registro().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _gravar_registro(itens):
    arq = _arquivo_registro()
    arq.parent.mkdir(parents=True, exist_ok=True)
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(itens, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, arq)


# ── perguntar a um Python quem ele é ───────────────────────────────────────

def perguntar(python):
    try:
        r = subprocess.run([str(python), "-c", _PERGUNTA], capture_output=True, text=True, timeout=60,
                           env={k: v for k, v in os.environ.items() if k not in ("RATT", "RELEITURA")})
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Recusa(f"não consegui rodar {python}: {e}") from e
    if r.returncode != 0:
        raise Recusa(f"{python} não respondeu:\n{r.stderr.strip()}")
    info = json.loads(r.stdout.strip().splitlines()[-1])
    if tuple(info["maior_menor"]) < (3, 9):
        raise Recusa(f"{python} é o Python {info['versao']}; a releitura precisa do 3.9 ou mais novo")
    return info


def _venv_da_pasta(pasta):
    for nome in (".venv", "venv", "env", ".env"):
        cfg = pasta / nome / "pyvenv.cfg"
        if cfg.is_file():
            py = pasta / nome / "bin" / "python"
            if py.exists():
                return py
    return None


def achar_local(pasta, python=None):
    """O Python do escopo local, e uma frase que diz como ele foi escolhido."""
    pasta = Path(pasta).resolve()
    if python:
        return Path(python), f"o Python que você indicou ({python})"
    py = _venv_da_pasta(pasta)
    if py:
        return py, f"o ambiente virtual da pasta ({py.parent.parent.name}/)"
    ativo = os.environ.get("VIRTUAL_ENV")
    if ativo and (Path(ativo) / "bin" / "python").exists():
        return Path(ativo) / "bin" / "python", f"o ambiente virtual ativo ({ativo})"
    if (pasta / ".python-version").is_file():
        versao = (pasta / ".python-version").read_text().strip()
        raise Recusa(
            f"esta pasta só tem um .python-version ({versao}), sem ambiente virtual.\n"
            f"  O pyenv escolhe o INTERPRETADOR da pasta, mas os pacotes dele são compartilhados por\n"
            f"  todo projeto que usa o {versao}. Instalar aqui ligaria a releitura em todos eles.\n\n"
            f"  Para ficar só nesta pasta:   python -m venv .venv   e depois   ratt install\n"
            f"  Para ligar no {versao} inteiro:   ratt install --global\n"
            f"  Se é isso mesmo que você quer:   ratt install --python \"$(pyenv which python)\"")
    raise Recusa(
        "não achei um ambiente virtual nesta pasta (.venv, venv, env) nem um ativo.\n\n"
        "  Para criar um aqui:          python -m venv .venv   e depois   ratt install\n"
        "  Para ligar no seu usuário:   ratt install --global")


def achar_global(python=None):
    if python:
        return Path(python), f"o Python que você indicou ({python})"
    if os.environ.get("VIRTUAL_ENV"):
        raise Recusa(
            "há um ambiente virtual ativo, e o global não mora dentro de ambiente virtual.\n\n"
            "  Saia dele primeiro:          deactivate   e depois   ratt install --global\n"
            "  Ou diga qual Python:         ratt install --global --python /caminho/do/python")
    for nome in ("python3", "python"):
        achado = shutil.which(nome)
        if achado:
            return Path(achado), f"o {nome} do seu PATH"
    raise Recusa("não achei python3 nem python no PATH")


# ── instalar, remover, reaplicar ───────────────────────────────────────────

def _conteudo_modulo():
    fonte = resources.files("ratt").joinpath("releitura.py").read_text(encoding="utf-8")
    cabeca = (f"# instalado pelo ratt {__version__} — não edite: o  ratt upgrade  reescreve este arquivo\n"
              f"__versao_ratt__ = {__version__!r}\n")
    return cabeca + fonte


def versao_instalada(pasta_pacotes):
    arq = Path(pasta_pacotes) / MODULO
    try:
        with open(arq, encoding="utf-8") as fh:
            for linha in fh:
                if linha.startswith("__versao_ratt__"):
                    return linha.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        return None
    return None


def _confirmar_ligada(python):
    r = subprocess.run([str(python), "-c",
                        "import builtins, ratt_releitura; print(builtins.print is ratt_releitura._print_relido)"],
                       capture_output=True, text=True, timeout=60,
                       env={k: v for k, v in os.environ.items() if k not in ("RATT", "RELEITURA")})
    return r.returncode == 0 and r.stdout.strip() == "True"


def instalar(escopo, python, como):
    info = perguntar(python)
    if escopo == "global":
        if info["em_venv"]:
            raise Recusa(f"{python} é de um ambiente virtual ({info['prefixo']}); o global é para um Python "
                         f"fora de ambiente. Use  ratt install  (local) ali dentro.")
        if not info["usuario_ligado"]:
            raise Recusa(f"o Python {info['versao']} está com a pasta de pacotes do usuário desligada "
                         f"(site.ENABLE_USER_SITE = False); o global não teria efeito")
        destino = Path(info["usuario"])
    else:
        destino = Path(info["pacotes"])
    destino.mkdir(parents=True, exist_ok=True)
    (destino / MODULO).write_text(_conteudo_modulo(), encoding="utf-8")
    (destino / PTH).write_text(LINHA_PTH, encoding="utf-8")

    itens = [i for i in ler_registro() if i.get("pasta_pacotes") != str(destino)]
    itens.append({"escopo": escopo, "python": info["executavel"], "versao_python": info["versao"],
                  "pasta_pacotes": str(destino), "como": como, "versao_ratt": __version__,
                  "quando": datetime.datetime.now().isoformat(timespec="seconds")})
    _gravar_registro(itens)
    return {"destino": destino, "info": info, "ligada": _confirmar_ligada(info["executavel"])}


def remover(pasta_pacotes):
    pasta = Path(pasta_pacotes)
    apagados = []
    for nome in (PTH, MODULO):
        arq = pasta / nome
        if arq.exists():
            arq.unlink()
            apagados.append(arq)
    cache = pasta / "__pycache__"
    if cache.is_dir():
        for arq in cache.glob("ratt_releitura.*.pyc"):
            arq.unlink()
    _gravar_registro([i for i in ler_registro() if i.get("pasta_pacotes") != str(pasta)])
    return apagados


def reaplicar():
    """Reescreve a releitura em todo lugar registrado, com a versão deste ratt."""
    feitos, sumidos = [], []
    for item in ler_registro():
        pasta = Path(item["pasta_pacotes"])
        if not (pasta / PTH).exists():
            sumidos.append(item)
            continue
        (pasta / MODULO).write_text(_conteudo_modulo(), encoding="utf-8")
        item["versao_ratt"] = __version__
        feitos.append(item)
    _gravar_registro(feitos)
    return feitos, sumidos
