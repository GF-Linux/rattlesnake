"""O desenhista do canvas: caixas de tabela, posição automática e acerto de clique — função pura.

Nada aqui conhece o Textual: `desenhar` devolve uma grade de células `(caractere, papel)` e o widget só
a exibe, trocando cada papel por uma cor. Assim o desenho inteiro se testa comparando texto.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AbstractSet, Dict, List, Optional, Tuple

from .modelo import Modelo, Tabela

LARGURA_MAX = 40
ESPACO_X = 8
ESPACO_Y = 2

_TIPOS_CURTOS = {
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
    "double precision": "float8",
}
_SINAIS = {"novo": "+", "apagado": "−", "mudado": "~"}
_ESTADOS = ("novo", "apagado", "mudado")

Celula = Tuple[str, str]


def tipo_curto(tipo: str) -> str:
    """O tipo como cabe na caixa: `character varying(n)` vira `varchar(n)` e assim por diante."""
    if tipo in _TIPOS_CURTOS:
        return _TIPOS_CURTOS[tipo]
    if tipo.startswith("character varying"):
        return "varchar" + tipo[len("character varying"):]
    return tipo


def _cortar(texto: str, n: int) -> str:
    """Corta em `n` caracteres, marcando o corte com `…`."""
    if n <= 0:
        return ""
    if len(texto) <= n:
        return texto
    return texto[:n - 1] + "…"


def _titulo(t: Tabela) -> Tuple[str, str]:
    """(nome, emblema) do título; o emblema (`▤N`) é o que nunca se corta."""
    return t.nome, (" ▤%d" % t.particoes) if t.particoes > 0 else ""


def tamanho_caixa(t: Tabela) -> Tuple[int, int]:
    """(largura, altura): a menor largura que cabe título e linhas, no máximo `LARGURA_MAX`."""
    nome, emblema = _titulo(t)
    # ┌─ + espaço + título + espaço + ao menos um ─ + ┐
    largura = 3 + len(nome) + len(emblema) + 3
    for c in t.colunas:
        # │ sinal marca(2) espaço nome ≥1 espaço tipo espaço │
        largura = max(largura, 7 + len(c.nome) + 1 + len(tipo_curto(c.tipo)))
    return min(largura, LARGURA_MAX), len(t.colunas) + 2


# ---- posição automática -----------------------------------------------------------------------------------

def _camadas(m: Modelo) -> Dict[str, int]:
    """0 para quem não aponta para ninguém, senão 1 + a maior camada dos destinos (ciclos e auto-ligação ignorados)."""
    destinos: Dict[str, List[str]] = {i: [] for i in m.tabelas}
    for k in m.ligacoes.values():
        if k.origem in m.tabelas and k.destino in m.tabelas and k.origem != k.destino:
            destinos[k.origem].append(k.destino)
    feito: Dict[str, int] = {}
    visitando: set = set()

    def camada(i: str) -> int:
        if i in feito:
            return feito[i]
        visitando.add(i)
        n = 0
        for d in destinos[i]:
            if d not in visitando:
                n = max(n, 1 + camada(d))
        visitando.discard(i)
        feito[i] = n
        return n

    for i in sorted(m.tabelas):
        camada(i)
    return feito


def _colide(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    """Duas caixas (x, y, w, h) se encostam, contando 1 de margem."""
    return not (a[0] + a[2] + 1 <= b[0] or b[0] + b[2] + 1 <= a[0] or a[1] + a[3] + 1 <= b[1] or b[1] + b[3] + 1 <= a[1])


def posicionar(m: Modelo, guardadas: Dict[str, Tuple[int, int]]) -> Dict[str, Tuple[int, int]]:
    """Posição de cada tabela: as guardadas ficam onde estão; as outras vão para camadas, da esquerda para a
    direita (quem é referenciado fica à esquerda de quem referencia) e, se esbarrarem numa guardada, descem."""
    camadas = _camadas(m)
    pos: Dict[str, Tuple[int, int]] = {i: tuple(guardadas[i]) for i in m.tabelas if i in guardadas}  # type: ignore
    ocupado = [(x, y) + tamanho_caixa(m.tabelas[i]) for i, (x, y) in pos.items()]
    por_camada: Dict[int, List[str]] = {}
    for i in sorted(m.tabelas, key=lambda i: (m.tabelas[i].nome, i)):
        por_camada.setdefault(camadas[i], []).append(i)
    x = 0
    for n in sorted(por_camada):
        y = 0
        larg = 0
        for i in por_camada[n]:
            w, h = tamanho_caixa(m.tabelas[i])
            larg = max(larg, w)
            if i in pos:
                continue
            while any(_colide((x, y, w, h), o) for o in ocupado):
                y += 1
            pos[i] = (x, y)
            ocupado.append((x, y, w, h))
            y += h + ESPACO_Y
        x += larg + ESPACO_X
    return pos


# ---- a grade ----------------------------------------------------------------------------------------------

@dataclass
class Desenho:
    largura: int
    altura: int
    caixas: Dict[str, Tuple[int, int, int, int]]
    _celulas: Dict[Tuple[int, int], Celula] = field(default_factory=dict, repr=False)
    _alvos: Dict[Tuple[int, int], tuple] = field(default_factory=dict, repr=False)

    def trechos(self, y: int) -> List[Tuple[str, str]]:
        """A linha `y` como pedaços `(texto, papel)` de papel igual; vazio fora da grade."""
        if y < 0 or y >= self.altura:
            return []
        saida: List[Tuple[str, str]] = []
        for x in range(self.largura):
            ch, papel = self._celulas.get((x, y), (" ", ""))
            if saida and saida[-1][1] == papel:
                saida[-1] = (saida[-1][0] + ch, papel)
            else:
                saida.append((ch, papel))
        return saida

    def texto(self) -> str:
        """As linhas sem espaço à direita, para os testes."""
        return "\n".join("".join(t for t, _ in self.trechos(y)).rstrip() for y in range(self.altura))

    def papel_em(self, x: int, y: int) -> str:
        return self._celulas.get((x, y), ("", ""))[1]

    def onde(self, x: int, y: int) -> Optional[tuple]:
        """O que há na célula: ("tabela", id), ("coluna", tabela, coluna), ("ligacao", id) ou None."""
        return self._alvos.get((x, y))


def desenhar(vista: Modelo, posicoes: Dict[str, Tuple[int, int]], avisos: Optional[Dict[str, str]] = None,
             selecao: Optional[tuple] = None, expandidas: AbstractSet[str] = frozenset(),
             limite_chegando: int = 8) -> Desenho:
    """Desenha as tabelas que têm posição. `expandidas` e `limite_chegando` são das setas (Task 7)."""
    avisos = avisos or {}
    cel: Dict[Tuple[int, int], Celula] = {}
    alvos: Dict[Tuple[int, int], tuple] = {}
    caixas: Dict[str, Tuple[int, int, int, int]] = {}

    def pôr(x: int, y: int, texto: str, papel: str, alvo: Optional[tuple] = None) -> None:
        for k, ch in enumerate(texto):
            cel[(x + k, y)] = (ch, papel)
            if alvo is not None:
                alvos[(x + k, y)] = alvo

    for tid in sorted(vista.tabelas):
        if tid not in posicoes:
            continue
        t = vista.tabelas[tid]
        x0, y0 = posicoes[tid]
        w, h = tamanho_caixa(t)
        caixas[tid] = (x0, y0, w, h)
        alvo_t = ("tabela", tid)
        nome, emblema = _titulo(t)
        papel_nome = t.estado if t.estado in _ESTADOS else "titulo"
        nome = _cortar(nome, w - 6 - len(emblema))
        topo = "┌─ "
        pôr(x0, y0, topo, "borda", alvo_t)
        pôr(x0 + 3, y0, nome, papel_nome, alvo_t)
        pôr(x0 + 3 + len(nome), y0, emblema, "contador", alvo_t)
        resto = 3 + len(nome) + len(emblema)
        pôr(x0 + resto, y0, " " + "─" * (w - resto - 2) + "┐", "borda", alvo_t)
        if tid in avisos:
            pôr(x0 + w, y0, "⚠", "aviso")
        interior = w - 7
        for n, c in enumerate(t.colunas):
            y = y0 + 1 + n
            alvo_c = ("coluna", tid, c.id)
            est = c.estado if c.estado in _ESTADOS else ""
            tipo = tipo_curto(c.tipo)
            nome_c = c.nome
            if len(nome_c) + 1 + len(tipo) > interior:
                nome_c = _cortar(nome_c, interior - 1 - len(tipo))
                if len(nome_c) < 2:  # nem o nome cabe com o tipo inteiro: corta o tipo também
                    tipo = _cortar(tipo, interior - 1 - 2)
                    nome_c = _cortar(c.nome, interior - 1 - len(tipo))
            pad = " " * (interior - len(nome_c) - len(tipo))
            pôr(x0, y, "│", "borda", alvo_c)
            pôr(x0 + 1, y, _SINAIS.get(est, " "), est or "marca", alvo_c)
            pôr(x0 + 2, y, "PK" if c.pk else "  ", est or "pk", alvo_c)
            pôr(x0 + 4, y, " ", est or "nome", alvo_c)
            pôr(x0 + 5, y, nome_c, est or "nome", alvo_c)
            pôr(x0 + 5 + len(nome_c), y, pad, est or "nome", alvo_c)
            pôr(x0 + 5 + len(nome_c) + len(pad), y, tipo, est or "tipo", alvo_c)
            pôr(x0 + w - 2, y, " ", est or "tipo", alvo_c)
            pôr(x0 + w - 1, y, "│", "borda", alvo_c)
            if c.id in avisos:
                pôr(x0 + w, y, "⚠", "aviso")
        yb = y0 + h - 1
        pôr(x0, yb, "└" + "─" * (w - 2) + "┘", "borda", alvo_t)

    if selecao is not None:
        for pos, alvo in alvos.items():
            if alvo == selecao or (selecao[0] == "tabela" and alvo[1] == selecao[1] and cel[pos][1] == "borda"):
                cel[pos] = (cel[pos][0], "selecao")

    largura = max((x for x, _ in cel), default=-1) + 1
    altura = max((y for _, y in cel), default=-1) + 1
    return Desenho(largura, altura, caixas, cel, alvos)
