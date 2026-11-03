"""O disco do canvas: posições das tabelas, rascunho e SQL aplicado, por banco.

Tudo mora em `$XDG_DATA_HOME/ratt/canvas/{porta}-{banco}/` (ou `~/.local/share/ratt/...`). Toda escrita
é atômica — grava em `arquivo.tmp` e troca com `os.replace` — para um travamento nunca deixar meio arquivo.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Dict, Optional, Tuple


def _seguro_antigo(nome: str) -> str:
    """O `_seguro` de antes (0.9.0 e o começo da 0.10.0): não era injetivo ("my schema" e "my_schema" davam no mesmo
    nome). Só serve para achar os arquivos gravados assim."""
    return re.sub(r"[^\w.-]", "_", nome).replace("..", "_") or "_"


def _seguro(nome: str) -> str:
    """Nome de banco ou esquema como parte de nome de arquivo: nada de barras nem `..`. Um nome que já é seguro fica
    como está (os arquivos de antes continuam valendo); um que precisou de troca ganha `~` e um resumo estável do
    nome original — "my schema" e "my_schema" nunca dividem arquivo (e `~` nunca aparece num nome que já era seguro)."""
    s = re.sub(r"[^\w.-]", "_", nome).replace("..", "_") or "_"
    if s == nome:
        return s
    return s + "~" + hashlib.sha1(nome.encode("utf-8", "surrogatepass")).hexdigest()[:12]


def _raiz() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")) / "ratt" / "canvas"


def pasta(porta: int, banco: str) -> Path:
    return _raiz() / f"{porta}-{_seguro(banco)}"


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


_IDENTIDADE = ("banco", "esquema")      # gravados junto do rascunho: de quem ele é (para achar o dono sem dúvida)


def ler_rascunho(porta: int, banco: str, esquema: str) -> Optional[dict]:
    """O rascunho gravado (sem os campos de identidade), ou None se não há arquivo. Arquivo que existe mas não é um
    objeto JSON legível levanta `RascunhoCorrompido`: quem chama o põe de lado, em vez de tratá-lo como vazio e depois
    apagá-lo. Qualquer outro erro de disco (permissão, E/S) sobe como `OSError`: "não consegui ler" nunca vira "não há
    rascunho"."""
    dados = ler_rascunho_em(_arquivo_rascunho(porta, banco, esquema))
    if dados is not None:
        for chave in _IDENTIDADE:
            dados.pop(chave, None)
    return dados


def ler_rascunho_em(arquivo: Path) -> Optional[dict]:
    """O JSON de um arquivo de rascunho como está (com a identidade, se tiver); mesmos erros de `ler_rascunho`."""
    from ratt.db.rascunho import RascunhoCorrompido
    try:
        texto = arquivo.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as e:
        raise RascunhoCorrompido(f"rascunho ilegível: {e}") from e
    try:
        dados = json.loads(texto)
    except ValueError as e:
        raise RascunhoCorrompido(f"rascunho ilegível: {e}") from e
    if not isinstance(dados, dict):
        raise RascunhoCorrompido("rascunho ilegível: esperava um objeto JSON")
    return dados


def gravar_rascunho(porta: int, banco: str, esquema: str, dados: Optional[dict]) -> None:
    arquivo = _arquivo_rascunho(porta, banco, esquema)
    if dados is None:
        try:
            arquivo.unlink()
        except FileNotFoundError:
            pass
        return
    com_dono = {"banco": banco, "esquema": esquema}
    com_dono.update({k: v for k, v in dados.items() if k not in _IDENTIDADE})
    _escrever(arquivo, json.dumps(com_dono, ensure_ascii=False, indent=1))


def rascunhos_de_antes(porta: int, banco: str, esquema: str) -> list:
    """Os arquivos que o `_seguro` de antes (não injetivo) teria usado para este rascunho e que ainda existem — só
    quando o nome do banco ou do schema precisou de troca (nome seguro sempre teve o mesmo arquivo) e o arquivo de
    agora ainda não existe. Podem ser deste schema ou de outro de nome parecido: quem chama confere o dono."""
    atual = _arquivo_rascunho(porta, banco, esquema)
    if atual.exists() or (_seguro_antigo(banco) == banco and _seguro_antigo(esquema) == esquema):
        return []
    pastas = [pasta(porta, banco), _raiz() / f"{porta}-{_seguro_antigo(banco)}"]
    nomes = [atual.name, f"rascunho-{_seguro_antigo(esquema)}.json"]
    achados = []
    for p in pastas:
        for n in nomes:
            caminho = p / n
            if caminho != atual and caminho not in achados and caminho.is_file():
                achados.append(caminho)
    return achados


def migrar_rascunho(antigo: Path, porta: int, banco: str, esquema: str, dados: dict) -> Path:
    """Leva um rascunho gravado com o nome de antes para o nome de agora: grava o novo (atômico, com a identidade) e
    só então renomeia o antigo para `….migrado` (nunca o apaga). Não grava por cima de um rascunho que já exista no
    nome novo (FileExistsError). Devolve o caminho em que o antigo ficou."""
    novo = _arquivo_rascunho(porta, banco, esquema)
    if novo.exists():
        raise FileExistsError(str(novo))
    gravar_rascunho(porta, banco, esquema, dados)
    destino = antigo.with_name(antigo.name + ".migrado")
    n = 1
    while destino.exists():
        n += 1
        destino = antigo.with_name(f"{antigo.name}.migrado-{n}")
    os.replace(antigo, destino)
    return destino


def separar_rascunho(porta: int, banco: str, esquema: str) -> Path:
    """Tira de cena um rascunho ilegível sem perdê-lo: renomeia para `rascunho-{esquema}.corrompido-AAAAMMDD-HHMMSS.json`
    (com `-2`, `-3`... se o nome já existir) e devolve o novo caminho. Nunca sobrescreve."""
    origem = _arquivo_rascunho(porta, banco, esquema)
    agora = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"rascunho-{_seguro(esquema)}.corrompido-{agora}"
    destino = origem.with_name(base + ".json")
    n = 1
    while destino.exists():
        n += 1
        destino = origem.with_name(f"{base}-{n}.json")
    os.replace(origem, destino)
    return destino


def arquivo_aplicando(porta: int, banco: str, esquema: str) -> Path:
    """O `.sql` que o aplicar grava antes de rodar (o nome do schema passa por `_seguro`: nada de `../`)."""
    return pasta(porta, banco) / f"aplicando-{_seguro(esquema)}.sql"


def nome_exportado(banco: str, esquema: str) -> str:
    """O nome sugerido para exportar o rascunho, na pasta atual: `rascunho-{banco}-{esquema}.sql`, sem barras."""
    return f"rascunho-{_seguro(banco)}-{_seguro(esquema)}.sql"


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
