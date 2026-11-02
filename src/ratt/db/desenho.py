"""O desenhista do canvas: caixas de tabela, posição automática e acerto de clique — função pura.

Nada aqui conhece o Textual: `desenhar` devolve uma grade de células `(caractere, papel)` e o widget só
a exibe, trocando cada papel por uma cor. Assim o desenho inteiro se testa comparando texto.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import AbstractSet, Dict, List, Optional, Set, Tuple

from .modelo import Modelo, Tabela, cardinalidade, chegando, opcional

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


# ---- roteamento -------------------------------------------------------------------------------------------

_VIZINHOS = ((1, 0), (0, 1), (-1, 0), (0, -1))  # direita, baixo, esquerda, cima: a ordem fixa que desempata


def _reta(inicio: Tuple[int, int], fim: Tuple[int, int]) -> List[Tuple[int, int]]:
    """Caminho em L (primeiro na horizontal, depois na vertical) ignorando tudo: o último recurso."""
    caminho = [inicio]
    x, y = inicio
    while x != fim[0]:
        x += 1 if fim[0] > x else -1
        caminho.append((x, y))
    while y != fim[1]:
        y += 1 if fim[1] > y else -1
        caminho.append((x, y))
    return caminho


def rotear(inicio: Tuple[int, int], fim: Tuple[int, int], bloqueadas: Set[Tuple[int, int]],
           limites: Tuple[int, int, int, int], ocupadas: AbstractSet[Tuple[int, int]] = frozenset(),
           dir_inicio: Optional[Tuple[int, int]] = None,
           dir_fim: Optional[Tuple[int, int]] = None) -> List[Tuple[int, int]]:
    """Caminho ortogonal de `inicio` a `fim` (A* na grade de células); `limites` = (x0, y0, x1, y1), inclusive.

    Custo: 1 por passo, +4 por curva, +2 por célula em `ocupadas` (de outra ligação), para as setas
    preferirem trechos retos e livres. `dir_inicio`/`dir_fim` dizem para onde a linha sai e por onde chega
    (a curva logo na saída ou na chegada também paga). Início e fim valem mesmo se estiverem em `bloqueadas`.
    Sem caminho, devolve a reta em L ignorando os bloqueios: desenhar nunca falha."""
    if inicio == fim:
        return [inicio]
    x0, y0, x1, y1 = limites

    def h(c: Tuple[int, int]) -> int:
        return abs(c[0] - fim[0]) + abs(c[1] - fim[1])

    ordem = 0
    aberta = [(h(inicio), ordem, 0, inicio, dir_inicio)]
    melhor: Dict[tuple, int] = {(inicio, dir_inicio): 0}
    pai: Dict[tuple, tuple] = {}
    while aberta:
        _, _, g, cel, d = heapq.heappop(aberta)
        if cel == fim:
            caminho = [cel]
            estado = (cel, d)
            while estado in pai:
                estado = pai[estado]
                caminho.append(estado[0])
            return caminho[::-1]
        if g > melhor.get((cel, d), g):
            continue
        for nd in _VIZINHOS:
            n = (cel[0] + nd[0], cel[1] + nd[1])
            if not (x0 <= n[0] <= x1 and y0 <= n[1] <= y1):
                continue
            if n in bloqueadas and n != fim:
                continue
            custo = g + 1
            if d is not None and nd != d:
                custo += 4
            if n == fim and dir_fim is not None and nd != dir_fim:
                custo += 4
            if n in ocupadas:
                custo += 2
            if custo < melhor.get((n, nd), 1 << 30):
                melhor[(n, nd)] = custo
                pai[(n, nd)] = (cel, d)
                ordem += 1
                heapq.heappush(aberta, (custo + h(n), ordem, custo, n, nd))
    return _reta(inicio, fim)


_CANTOS = {
    frozenset([(1, 0), (-1, 0)]): "─", frozenset([(0, 1), (0, -1)]): "│",
    frozenset([(1, 0), (0, 1)]): "┌", frozenset([(-1, 0), (0, 1)]): "┐",
    frozenset([(1, 0), (0, -1)]): "└", frozenset([(-1, 0), (0, -1)]): "┘",
}


def _caracter(antes: Tuple[int, int], c: Tuple[int, int], depois: Tuple[int, int]) -> str:
    """O traço da célula `c` conforme de onde a linha vem e para onde vai (nunca `┼`: esse é a marca "um")."""
    dirs = frozenset([(antes[0] - c[0], antes[1] - c[1]), (depois[0] - c[0], depois[1] - c[1])])
    if dirs in _CANTOS:
        return _CANTOS[dirs]
    horizontal = any(d[1] == 0 and d[0] != 0 for d in dirs)
    return "─" if horizontal else "│"


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


def _ligacoes(vista: Modelo, caixas: Dict[str, Tuple[int, int, int, int]], recolhidas: AbstractSet[str],
              cel: Dict[Tuple[int, int], Celula], alvos: Dict[Tuple[int, int], tuple]) -> None:
    """Roteia e pinta as setas em `cel`/`alvos`. Primeiro todas as linhas (quem vem depois vence num
    cruzamento), depois bordas e marcas por cima, para uma linha nunca apagar um `o` ou um `<`."""
    proprias = []  # (ligação, lado_o, lado_d, y_o, y_d, opcional, 1:1, extras)
    reservadas: Set[Tuple[int, int]] = set()
    # pontas que dividem a mesma célula da borda: as marcas se empilham para fora, e as opcionais (com `o`)
    # ficam por dentro, para o `├┼o` continuar legível
    usos: Dict[Tuple[int, int], list] = {}

    for lid in sorted(vista.ligacoes):
        lig = vista.ligacoes[lid]
        if lig.origem not in caixas or lig.destino not in caixas or lig.destino in recolhidas:
            continue
        to, td = vista.tabelas[lig.origem], vista.tabelas[lig.destino]
        linhas_o = [i for i, c in enumerate(to.colunas) if c.id in lig.colunas]
        ordem_o = [next((i for i, c in enumerate(to.colunas) if c.id == cid), None) for cid in lig.colunas]
        ordem_d = [next((i for i, c in enumerate(td.colunas) if c.id == cid), None) for cid in lig.colunas_destino]
        if not lig.colunas or not lig.colunas_destino or None in ordem_o or None in ordem_d or not linhas_o:
            continue
        xo, yo, wo, _ = caixas[lig.origem]
        xd, yd, wd, _ = caixas[lig.destino]
        dif = (2 * xd + wd) - (2 * xo + wo)
        lado_o = 1 if dif > 0 else -1 if dif < 0 else 1
        lado_d = -lado_o if dif != 0 else 1
        if lig.origem == lig.destino:
            lado_o = lado_d = 1
        bo = xo + wo - 1 if lado_o > 0 else xo
        bd = xd + wd - 1 if lado_d > 0 else xd
        opc = opcional(vista, lig)
        um = cardinalidade(vista, lig) == "1:1"
        extras = [(bo, yo + 1 + i) for i in ordem_o[1:]]
        info = dict(lig=lig, so=lado_o, sd=lado_d, bo=bo, bd=bd, yo=yo + 1 + ordem_o[0], yd=yd + 1 + ordem_d[0],
                    opc=opc, um=um, extras=extras)
        proprias.append(info)
        usos.setdefault((bo, info["yo"]), []).append(((1, lid), info, "ko"))
        usos.setdefault((bd, info["yd"]), []).append(((0 if opc else 1, lid), info, "kd"))
        for bx, y in extras:
            reservadas.add((bx + lado_o, y))
    if not proprias:
        return
    for fila in usos.values():
        gasto = 0  # células já gastas por quem está mais por dentro
        for _, info, campo in sorted(fila, key=lambda u: u[0]):
            info[campo] = gasto
            gasto += 2 if campo == "kd" and info["opc"] else 1
    for info in proprias:
        reservadas.add((info["bo"] + info["so"] * (1 + info["ko"]), info["yo"]))
        reservadas.add((info["bd"] + info["sd"] * (1 + info["kd"]), info["yd"]))
        if info["opc"]:
            reservadas.add((info["bd"] + info["sd"] * (2 + info["kd"]), info["yd"]))

    interditas: Set[Tuple[int, int]] = set(reservadas)
    dentro: Set[Tuple[int, int]] = set()
    for x, y, w, h in caixas.values():
        for yy in range(y - 1, y + h + 1):
            for xx in range(x - 1, x + w + 1):
                interditas.add((xx, yy))
                if x <= xx < x + w and y <= yy < y + h:
                    dentro.add((xx, yy))
    lx0 = max(0, min(x for x, _, _, _ in caixas.values()) - 4)
    ly0 = max(0, min(y for _, y, _, _ in caixas.values()) - 4)
    lx1 = max(x + w for x, _, w, _ in caixas.values()) + 4
    ly1 = max(y + h for _, y, _, h in caixas.values()) + 4

    ocupadas: Set[Tuple[int, int]] = set()
    for i in proprias:
        lig = i["lig"]
        so, sd = i["so"], i["sd"]
        inicio = (i["bo"] + (2 + i["ko"]) * so, i["yo"])
        fim = (i["bd"] + (3 + i["kd"] if i["opc"] else 2 + i["kd"]) * sd, i["yd"])
        limites = (min(lx0, inicio[0], fim[0]), min(ly0, inicio[1], fim[1]),
                   max(lx1, inicio[0], fim[0]), max(ly1, inicio[1], fim[1]))
        caminho = rotear(inicio, fim, interditas, limites, ocupadas, (so, 0), (-sd, 0))
        i["inicio"], i["fim"] = inicio, fim
        papel = lig.estado if lig.estado in _ESTADOS else "seta"
        alvo = ("ligacao", lig.id)
        for n, c in enumerate(caminho):
            antes = caminho[n - 1] if n > 0 else (c[0] - so, c[1])
            depois = caminho[n + 1] if n + 1 < len(caminho) else (c[0] - sd, c[1])
            ocupadas.add(c)
            if c in dentro or c[0] < 0 or c[1] < 0:
                continue
            cel[c] = (_caracter(antes, c, depois), papel)
            alvos[c] = alvo

    for i in proprias:
        lig = i["lig"]
        papel = lig.estado if lig.estado in _ESTADOS else "marca"
        alvo = ("ligacao", lig.id)
        so, sd = i["so"], i["sd"]

        def marca(x: int, y: int, ch: str) -> None:
            if x >= 0:
                cel[(x, y)] = (ch, papel)
                alvos[(x, y)] = alvo

        def borda(x: int, y: int, lado: int) -> None:
            if (x, y) in cel:  # a borda da coluna: só o caractere muda, o papel e o alvo (a coluna) ficam
                cel[(x, y)] = ("├" if lado > 0 else "┤", cel[(x, y)][1])

        borda(i["bo"], i["yo"], so)
        marca(i["bo"] + so * (1 + i["ko"]), i["yo"], "┼" if i["um"] else (">" if so > 0 else "<"))
        for bx, y in i["extras"]:
            borda(bx, y, so)
            marca(bx + so, y, "⋯")
        borda(i["bd"], i["yd"], sd)
        marca(i["bd"] + sd * (1 + i["kd"]), i["yd"], "┼")
        if i["opc"]:
            marca(i["bd"] + sd * (2 + i["kd"]), i["yd"], "o")


def desenhar(vista: Modelo, posicoes: Dict[str, Tuple[int, int]], avisos: Optional[Dict[str, str]] = None,
             selecao: Optional[tuple] = None, expandidas: AbstractSet[str] = frozenset(),
             limite_chegando: int = 8) -> Desenho:
    """Desenha as tabelas que têm posição e as ligações entre elas.

    Uma tabela com mais de `limite_chegando` ligações chegando, e fora de `expandidas`, fica recolhida: não
    recebe as setas e o título mostra `←N`. Alargar o título por causa disso é aceitável porque a posição vem
    de quem chama; `caixas` guarda a largura realmente desenhada."""
    avisos = avisos or {}
    cheg = chegando(vista)
    cel: Dict[Tuple[int, int], Celula] = {}
    alvos: Dict[Tuple[int, int], tuple] = {}
    caixas: Dict[str, Tuple[int, int, int, int]] = {}
    recolhidas: Set[str] = set()

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
        nome, emblema = _titulo(t)
        if cheg.get(tid, 0) > limite_chegando and tid not in expandidas:
            recolhidas.add(tid)
            emblema += " ←%d" % cheg[tid]
            w = max(w, min(3 + len(nome) + len(emblema) + 3, LARGURA_MAX))
        caixas[tid] = (x0, y0, w, h)
        alvo_t = ("tabela", tid)
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
            pôr(x0 + 1, y, _SINAIS.get(est, " "), est or "nome", alvo_c)
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

    _ligacoes(vista, caixas, recolhidas, cel, alvos)

    if selecao is not None:
        for pos, alvo in alvos.items():
            if alvo == selecao or (selecao[0] == "tabela" and alvo[0] != "ligacao" and alvo[1] == selecao[1]
                                   and cel[pos][1] == "borda"):
                cel[pos] = (cel[pos][0], "selecao")

    largura = max((x for x, _ in cel), default=-1) + 1
    altura = max((y for _, y in cel), default=-1) + 1
    return Desenho(largura, altura, caixas, cel, alvos)
