"""Arquivos em colunas: ler um arquivo do disco como valor de `bytea`/texto, rotular o tamanho e abreviar os
literais longos do SQL que a tela mostra. Puro: sem Textual, sem banco. O que é executado ou exportado nunca passa
por aqui; só o que é mostrado."""
from __future__ import annotations

import os
import re
from typing import Tuple

from ratt.db import filtro, idioma
from ratt.db.idioma import t

TETO = 10 * 1024 * 1024         # bytes por arquivo
_LIMITE_LITERAL = 120           # caracteres do conteúdo (como escrito) acima dos quais o literal é abreviado
_FICA = 40                      # caracteres do começo que ficam


class ArquivoRecusado(ValueError):
    """O arquivo não pode virar valor da coluna; a mensagem já está traduzida."""


class TamanhoArquivo(int):
    """O tamanho em bytes de um `bytea` que a grade não trouxe (vem de `octet_length`)."""


def aceita(tipo: str) -> bool:
    return filtro.tipo_normal(tipo) == "bytea" or filtro.familia(tipo) == "texto"


def tamanho_legivel(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{max(1, round(n / 1024))} KB"
    mb = f"{n / (1024 * 1024):.1f} MB"
    return mb.replace(".", ",") if idioma.atual() == "pt" else mb


def rotulo_tamanho(n: int) -> str:
    return f"‹{t('arq.arquivo')} · {tamanho_legivel(n)}›"


_ASSINATURAS = ((b"ABIF", ".ab1"), (b"%PDF", ".pdf"), (b"\x89PNG", ".png"), (b"\xff\xd8\xff", ".jpg"),
                (b"\x1f\x8b", ".gz"), (b"PK\x03\x04", ".zip"))


def extensao(dados: bytes) -> str:
    return next((ext for assinatura, ext in _ASSINATURAS if dados.startswith(assinatura)), ".bin")


def ler(caminho: str, tipo: str) -> Tuple[str, str]:
    """`(valor, rótulo)` do arquivo para uma coluna de `tipo`. Recusa com `ArquivoRecusado`."""
    caminho = os.path.expanduser(caminho.strip())
    if os.path.isdir(caminho):
        raise ArquivoRecusado(t("arq.e_pasta", caminho=caminho))
    if not os.path.exists(caminho):
        raise ArquivoRecusado(t("arq.nao_existe", caminho=caminho))
    try:
        with open(caminho, "rb") as f:
            dados = f.read(TETO + 1)
    except PermissionError:
        raise ArquivoRecusado(t("arq.sem_permissao", caminho=caminho))
    except OSError as erro:
        raise ArquivoRecusado(t("arq.erro", caminho=caminho, erro=erro.strerror or str(erro)))
    if len(dados) > TETO:
        raise ArquivoRecusado(t("arq.grande", teto=tamanho_legivel(TETO)))
    rotulo = f"‹{os.path.basename(caminho)} · {tamanho_legivel(len(dados))}›"
    if filtro.tipo_normal(tipo) == "bytea":
        return "\\x" + dados.hex(), rotulo
    if filtro.familia(tipo) != "texto":
        raise ArquivoRecusado(t("arq.tipo", tipo=tipo))
    if b"\x00" in dados:
        raise ArquivoRecusado(t("arq.binario"))
    try:
        texto = dados.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ArquivoRecusado(t("arq.binario"))
    return texto, rotulo


# ── o SQL para ver ──

def _desfaz(inner: str, com_e: bool) -> str:
    texto = inner.replace("''", "'")
    return re.sub(r"\\(.)", r"\1", texto, flags=re.S) if com_e else texto


def _unidades(inner: str, com_e: bool):
    """O conteúdo quebrado em pedaços que não se cortam: `''`, `\\x` (no E'') ou um caractere."""
    i = 0
    while i < len(inner):
        n = 2 if (inner[i] == "'" or (com_e and inner[i] == "\\")) and i + 1 < len(inner) else 1
        yield inner[i:i + n]
        i += n


def _abrevia(inner: str, com_e: bool, prefixo: str) -> str:
    real = _desfaz(inner, com_e)
    if re.fullmatch(r"\\x[0-9a-fA-F]*", real) and len(real) % 2 == 0:
        marca = f"{t('arq.arquivo')} · {tamanho_legivel((len(real) - 2) // 2)}"
    else:
        marca = f"{t('arq.texto')} · {tamanho_legivel(len(real.encode('utf-8')))}"
    inicio, usado = [], 0
    for u in _unidades(inner, com_e):
        if usado + len(u) > _FICA:
            break
        inicio.append(u)
        usado += len(u)
    return f"{prefixo}'{''.join(inicio)}…' /* {marca} */"


def sql_para_ver(texto: str) -> str:
    """O SQL com cada literal `'…'`/`E'…'` de mais de 120 caracteres abreviado. Respeita `"identificador"`,
    `-- comentário` e `/* comentário */`."""
    saida, i, n = [], 0, len(texto)
    while i < n:
        c = texto[i]
        if c == '"':
            fim = texto.find('"', i + 1)
            while fim != -1 and texto[fim + 1:fim + 2] == '"':
                fim = texto.find('"', fim + 2)
            fim = n if fim == -1 else fim + 1
            saida.append(texto[i:fim]); i = fim
        elif texto.startswith("--", i):
            fim = texto.find("\n", i)
            fim = n if fim == -1 else fim
            saida.append(texto[i:fim]); i = fim
        elif texto.startswith("/*", i):
            fim = texto.find("*/", i + 2)
            fim = n if fim == -1 else fim + 2
            saida.append(texto[i:fim]); i = fim
        elif c == "'":
            com_e = bool(saida) and saida[-1] in ("e", "E") and (
                len(saida) == 1 or not (saida[-2][-1].isalnum() or saida[-2][-1] in "_$"))
            j = i + 1
            while j < n:
                if com_e and texto[j] == "\\":
                    j += 2
                elif texto[j] == "'":
                    if texto[j + 1:j + 2] == "'":
                        j += 2
                    else:
                        break
                else:
                    j += 1
            if j >= n:                                  # literal aberto: deixa como está
                saida.append(texto[i:]); i = n
                continue
            inner = texto[i + 1:j]
            if len(inner) > _LIMITE_LITERAL:
                prefixo = saida.pop() if com_e else ""
                saida.append(_abrevia(inner, com_e, prefixo))
            else:
                saida.append(texto[i:j + 1])
            i = j + 1
        else:
            saida.append(c); i += 1
    return "".join(saida)
