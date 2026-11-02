"""O disco do canvas: posições das tabelas, rascunho e SQL aplicado, por banco.

Tudo mora em `$XDG_DATA_HOME/ratt/canvas/{porta}-{banco}/` (ou `~/.local/share/ratt/...`). Toda escrita
é atômica — grava em `arquivo.tmp` e troca com `os.replace` — para um travamento nunca deixar meio arquivo.
"""
from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path
from typing import Dict, Optional, Tuple


def _seguro(nome: str) -> str:
    """Nome de banco ou esquema como parte de nome de arquivo: nada de barras nem `..`."""
    s = re.sub(r"[^\w.-]", "_", nome)
    return s.replace("..", "_") or "_"


def pasta(porta: int, banco: str) -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "ratt" / "canvas" / f"{porta}-{_seguro(banco)}"


def _escrever(caminho: Path, texto: str) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_name(caminho.name + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.replace(tmp, caminho)


def _ler_json(caminho: Path):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def ler_posicoes(porta: int, banco: str, esquema: str) -> Dict[str, Tuple[int, int]]:
    todas = _ler_json(pasta(porta, banco) / "posicoes.json")
    if not isinstance(todas, dict) or not isinstance(todas.get(esquema), dict):
        return {}
    posicoes = {}
    for id, xy in todas[esquema].items():
        if isinstance(xy, list) and len(xy) == 2 and all(isinstance(v, int) for v in xy):
            posicoes[id] = (xy[0], xy[1])
    return posicoes


def gravar_posicoes(porta: int, banco: str, esquema: str, posicoes: Dict[str, Tuple[int, int]]) -> None:
    arquivo = pasta(porta, banco) / "posicoes.json"
    todas = _ler_json(arquivo)
    if not isinstance(todas, dict):
        todas = {}
    todas[esquema] = {id: [xy[0], xy[1]] for id, xy in posicoes.items()}
    _escrever(arquivo, json.dumps(todas, ensure_ascii=False, indent=1))


def _arquivo_rascunho(porta: int, banco: str, esquema: str) -> Path:
    return pasta(porta, banco) / f"rascunho-{_seguro(esquema)}.json"


def ler_rascunho(porta: int, banco: str, esquema: str) -> Optional[dict]:
    dados = _ler_json(_arquivo_rascunho(porta, banco, esquema))
    return dados if isinstance(dados, dict) else None


def gravar_rascunho(porta: int, banco: str, esquema: str, dados: Optional[dict]) -> None:
    arquivo = _arquivo_rascunho(porta, banco, esquema)
    if dados is None:
        try:
            arquivo.unlink()
        except FileNotFoundError:
            pass
        return
    _escrever(arquivo, json.dumps(dados, ensure_ascii=False, indent=1))


def guardar_aplicado(porta: int, banco: str, texto: str) -> Path:
    """Guarda o SQL que acabou de ser aplicado em `aplicados/AAAAMMDD-HHMMSS.sql` (nunca sobrescreve)."""
    agora = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    pasta_aplicados = pasta(porta, banco) / "aplicados"
    caminho = pasta_aplicados / f"{agora}.sql"
    n = 1
    while caminho.exists():
        n += 1
        caminho = pasta_aplicados / f"{agora}-{n}.sql"
    _escrever(caminho, texto)
    return caminho
