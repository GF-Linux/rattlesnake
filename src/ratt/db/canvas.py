"""A tela do canvas: o diagrama das tabelas de um schema, para ver, selecionar e arrastar.

`FonteBanco` é o único ponto onde a tela toca o banco (nos testes entra uma fonte falsa).
`Quadro` só exibe a grade de texto de `desenho.Desenho` e traduz o mouse; `TelaCanvas` guarda o estado
(posições, seleção, foco) e redesenha quando algo muda. Nesta etapa a tela é só de leitura.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.geometry import Region, Size
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.containers import Vertical
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from ratt.db import acesso, catalogo, checagem, desenho, guardar, modelo as _modelo
from ratt.db.idioma import t
from ratt.db.tela import COR

# o renderizador não pinta células negativas e as setas saem até 4 células das caixas
MIN_X, MIN_Y = 4, 2
LIMITE_FOCO = 25                # schema com mais tabelas que isso abre já em foco
INTERVALO_ARRASTO = 0.05        # no máximo um redesenho a cada 50 ms enquanto a caixa é arrastada
_NIVEIS = {"info": 0, "atencao": 1, "impede": 2}


class FonteBanco:
    """O que a tela precisa do banco: esquemas, o modelo, a assinatura para notar mudanças e as consultas."""

    def __init__(self, conexao, porta: int, usuario: str, banco: str):
        self.conexao, self.porta, self.usuario, self.banco = conexao, porta, usuario, banco

    def esquemas(self) -> List[str]:
        return catalogo.esquemas_com_tabelas(self.conexao)

    def modelo(self, esquema: str) -> _modelo.Modelo:
        return catalogo.modelo(self.conexao, esquema)

    def assinatura(self):
        return catalogo.assinatura(self.conexao)

    def consulta(self) -> checagem.Consulta:
        return checagem.Consulta(self.conexao)

    def conexao_para_aplicar(self):
        """Uma conexão só para aplicar o rascunho: a da tela segue livre para olhar o banco."""
        conexao, _ = acesso.entrar(self.porta, self.usuario, self.banco)
        return conexao


# ── o Quadro ────────────────────────────────────────────────────────────────

_ESTILOS = {
    "borda": Style(color=COR["cinza"]),
    "tipo": Style(color=COR["cinza"]),
    "contador": Style(color=COR["cinza"]),
    "titulo": Style(bold=True),
    "pk": Style(color=COR["amarelo"], bold=True),
    "seta": Style(color=COR["azul"]),
    "marca": Style(color=COR["azul"]),
    "novo": Style(color=COR["verde"]),
    "apagado": Style(color=COR["laranja"], strike=True),
    "mudado": Style(color=COR["amarelo"]),
    "aviso": Style(color=COR["amarelo"]),
    "selecao": Style(bgcolor="#1f3550"),
}


class Quadro(ScrollView):
    """Exibe um `Desenho`. O desenho só é refeito por quem o entrega (`definir`); rolar não redesenha."""

    class Selecionou(Message):
        def __init__(self, alvo: Optional[tuple]):
            super().__init__()
            self.alvo = alvo

    class Arrastando(Message):
        """A caixa está sendo arrastada (a tela decide quando redesenhar)."""
        def __init__(self, tabela_id: str, x: int, y: int):
            super().__init__()
            self.tabela_id, self.x, self.y = tabela_id, x, y

    class Moveu(Message):
        """Soltou o arrasto: é aqui que a posição se guarda."""
        def __init__(self, tabela_id: str, x: int, y: int):
            super().__init__()
            self.tabela_id, self.x, self.y = tabela_id, x, y

    DEFAULT_CSS = "Quadro { height: 1fr; }"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.desenho = desenho.Desenho(0, 0, {})
        self.aviso_vazio = ""
        self._linhas: Dict[int, Strip] = {}
        self._pressionado = None        # (alvo, tabela_id, caixa, sx, sy, scroll_x, scroll_y)
        self._arrastou = False

    def definir(self, d: desenho.Desenho, aviso_vazio: str = "") -> None:
        self.desenho, self.aviso_vazio = d, aviso_vazio
        self._linhas = {}
        self.virtual_size = Size(d.largura, d.altura)
        self.refresh()

    def _linha(self, y: int) -> Strip:
        if y not in self._linhas:
            segs = [Segment(texto, _ESTILOS.get(papel)) for texto, papel in self.desenho.trechos(y)]
            self._linhas[y] = Strip(segs)
        return self._linhas[y]

    def render_line(self, y: int) -> Strip:
        largura = self.size.width
        if self.aviso_vazio:
            msg = self.aviso_vazio
            if y == self.size.height // 2:
                msg = msg[:largura]
                esq = max(0, (largura - len(msg)) // 2)
                return Strip([Segment(" " * esq), Segment(msg, Style(color=COR["cinza"]))]).adjust_cell_length(largura)
            return Strip.blank(largura)
        sx, sy = self.scroll_offset
        return self._linha(y + sy).crop(sx, sx + largura).adjust_cell_length(largura)

    # ── o mouse ─────────────────────────────────────────────────────────────

    def _celula(self, evento) -> Tuple[int, int]:
        sx, sy = self.scroll_offset
        return evento.x + sx, evento.y + sy

    def on_mouse_down(self, evento: events.MouseDown) -> None:
        if evento.button != 1:
            return
        x, y = self._celula(evento)
        alvo = self.desenho.onde(x, y)
        tabela = None
        if alvo is not None and alvo[0] in ("tabela", "coluna") and self.desenho.papel_em(x, y) in ("borda", "titulo", "contador", "selecao"):
            # só se agarra a caixa pela borda e pelo título; o meio é para clicar nas colunas
            if alvo[0] == "tabela" or self.desenho.papel_em(x, y) in ("borda", "selecao"):
                tabela = alvo[1]
        caixa = self.desenho.caixas.get(tabela) if tabela else None
        self._pressionado = (alvo, tabela, caixa, evento.screen_x, evento.screen_y, *self.scroll_offset)
        self._arrastou = False
        self.capture_mouse()
        self.post_message(self.Selecionou(alvo))

    def on_mouse_move(self, evento: events.MouseMove) -> None:
        if not self._pressionado:
            return
        _, tabela, caixa, sx, sy, rolx, roly = self._pressionado
        dx, dy = evento.screen_x - sx, evento.screen_y - sy
        if dx == 0 and dy == 0:
            return
        self._arrastou = True
        if tabela and caixa:
            self.post_message(self.Arrastando(tabela, max(MIN_X, caixa[0] + dx), max(MIN_Y, caixa[1] + dy)))
        else:
            self.scroll_to(max(0, rolx - dx), max(0, roly - dy), animate=False)

    def on_mouse_up(self, evento: events.MouseUp) -> None:
        if not self._pressionado:
            return
        _, tabela, caixa, sx, sy, _, _ = self._pressionado
        self._pressionado = None
        self.release_mouse()
        if self._arrastou and tabela and caixa:
            dx, dy = evento.screen_x - sx, evento.screen_y - sy
            self.post_message(self.Moveu(tabela, max(MIN_X, caixa[0] + dx), max(MIN_Y, caixa[1] + dy)))


# ── telas pequenas de perguntar ─────────────────────────────────────────────

class EscolherEsquema(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss(None)", t("canvas.k.voltar"))]
    DEFAULT_CSS = """
    EscolherEsquema { align: center middle; }
    EscolherEsquema > Vertical { width: 50; height: auto; max-height: 80%; border: round $primary; background: $surface; padding: 1 2; }
    EscolherEsquema OptionList { height: auto; max-height: 20; border: none; }
    """

    def __init__(self, esquemas, atual):
        super().__init__()
        self.esquemas, self.atual = esquemas, atual

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(t("canvas.qual_esquema"), style="bold"))
            yield OptionList(*[Option(n, id=n) for n in self.esquemas])

    def on_mount(self):
        if self.atual in self.esquemas:
            self.query_one(OptionList).highlighted = self.esquemas.index(self.atual)

    def on_option_list_option_selected(self, evento):
        self.dismiss(evento.option.id)


class PerguntarNome(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss(None)", t("canvas.k.voltar"))]
    DEFAULT_CSS = """
    PerguntarNome { align: center middle; }
    PerguntarNome > Vertical { width: 60; height: auto; border: round $primary; background: $surface; padding: 1 2; }
    """

    def __init__(self, pergunta):
        super().__init__()
        self.pergunta = pergunta

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self.pergunta, style="bold"))
            yield Input()

    def on_input_submitted(self, evento):
        self.dismiss(evento.value.strip())


# ── a tela ──────────────────────────────────────────────────────────────────

class TelaCanvas(Screen):
    BINDINGS = [
        Binding("tab", "tabela(1)", t("canvas.k.proxima")),
        Binding("shift+tab", "tabela(-1)", t("canvas.k.anterior"), show=False),
        Binding("up", "coluna(-1)", t("canvas.k.coluna_acima"), show=False),
        Binding("down", "coluna(1)", t("canvas.k.coluna_abaixo"), show=False),
        Binding("shift+up", "mover(0,-1)", t("canvas.k.mover")),
        Binding("shift+down", "mover(0,1)", show=False),
        Binding("shift+left", "mover(-2,0)", show=False),
        Binding("shift+right", "mover(2,0)", show=False),
        Binding("s", "esquema", t("canvas.k.esquema")),
        Binding("f", "foco", t("canvas.k.foco")),
        Binding("F,shift+f", "tudo", t("canvas.k.tudo")),
        Binding("escape,q", "voltar", t("canvas.k.voltar")),
    ]
    DEFAULT_CSS = """
    #canvas_titulo { height: 1; padding: 0 1; background: $panel; }
    #canvas_sql { height: auto; max-height: 10; padding: 0 1; border-top: solid $panel; color: $text-muted; }
    """

    def __init__(self, fonte, esquema: str):
        super().__init__()
        self.fonte, self.esquema = fonte, esquema
        self.selecao: Optional[tuple] = None
        self.posicoes: Dict[str, Tuple[int, int]] = {}
        self.vista: _modelo.Modelo = _modelo.Modelo(esquema)
        self.todo: _modelo.Modelo = self.vista      # o schema inteiro; `vista` é o que se vê (o foco recorta)
        self.foco: Optional[str] = None
        self.avisos: Dict[str, str] = {}
        self.marca = None
        self._ultimo = 0.0
        self._timer = None

    def compose(self) -> ComposeResult:
        yield Static(id="canvas_titulo")
        yield Quadro(id="quadro")
        yield Static(Text(t("canvas.sem_rascunho"), style=COR["cinza"]), id="canvas_sql")
        yield Footer()

    def on_mount(self):
        self.marca = self.fonte.assinatura()
        self.carregar(self.fonte.modelo(self.esquema), primeira=True)
        self.set_interval(2.0, self.vigiar)

    # ── o modelo e as posições ──────────────────────────────────────────────

    def carregar(self, m: _modelo.Modelo, primeira: bool = False) -> None:
        """Troca o modelo mantendo posições e seleção; posiciona o que não tem lugar."""
        self.todo = m
        if primeira:
            self.posicoes = guardar.ler_posicoes(self.fonte.porta, self.fonte.banco, self.esquema)
            self.foco = None
            if len(m.tabelas) > LIMITE_FOCO:
                self.foco = self._ordem(m)[0]
        self.posicoes = self._posicionar(m, self.posicoes)
        if self.foco not in m.tabelas:
            self.foco = None
        self.vista = _modelo.recortar(m, self.foco, 1) if self.foco else m
        self.avisos = self._avisos(self.vista)
        if self.selecao and not self._existe(self.selecao):
            self.selecao = None
        self.redesenhar()
        if primeira and self.vista.tabelas:
            self._rolar_para_caixas()

    @staticmethod
    def _ordem(m: _modelo.Modelo) -> List[str]:
        return sorted(m.tabelas, key=lambda i: (m.tabelas[i].nome, i))

    @staticmethod
    def _posicionar(m, guardadas):
        """`posicionar` completa o que falta; o desenho começa em (MIN_X, MIN_Y) porque não pinta negativos."""
        firmes = {i: (max(MIN_X, x) - MIN_X, max(MIN_Y, y) - MIN_Y) for i, (x, y) in guardadas.items() if i in m.tabelas}
        return {i: (x + MIN_X, y + MIN_Y) for i, (x, y) in desenho.posicionar(m, firmes).items()}

    @staticmethod
    def _avisos(vista) -> Dict[str, str]:
        saida: Dict[str, str] = {}
        for a in checagem.avisos_do_modelo(vista):
            if a.alvo not in saida or _NIVEIS[a.nivel] > _NIVEIS[saida[a.alvo]]:
                saida[a.alvo] = a.nivel
        return saida

    def _existe(self, alvo) -> bool:
        if alvo[0] == "tabela":
            return alvo[1] in self.vista.tabelas
        if alvo[0] == "coluna":
            return alvo[1] in self.vista.tabelas and self.vista.tabelas[alvo[1]].coluna(alvo[2]) is not None
        return alvo[1] in self.vista.ligacoes

    # ── desenhar ────────────────────────────────────────────────────────────

    def redesenhar(self) -> None:
        self._ultimo = time.monotonic()
        d = desenho.desenhar(self.vista, self.posicoes, self.avisos, self.selecao)
        self.query_one(Quadro).definir(d, t("canvas.vazio") if not self.vista.tabelas else "")
        self._titulo()

    def _titulo(self) -> None:
        partes = [("ratt db", f"bold {COR['azul']}"), (" · ", COR["cinza"]), (self.fonte.banco, "bold"),
                  (" › ", COR["cinza"]), (t("canvas.titulo"), ""), (" · ", COR["cinza"]), (self.esquema, ""),
                  (" · ", COR["cinza"]), (t("canvas.rascunho", n=0), COR["cinza"])]
        if self.foco:
            partes += [(" · ", COR["cinza"]), (t("canvas.em_foco", tabela=self.todo.tabelas[self.foco].nome), COR["amarelo"])]
        self.query_one("#canvas_titulo", Static).update(Text.assemble(*partes))

    def _agendar(self) -> None:
        """Enquanto arrasta, no máximo um redesenho por intervalo; o último movimento sempre chega."""
        if time.monotonic() - self._ultimo >= INTERVALO_ARRASTO:
            self.redesenhar()
        elif self._timer is None:
            self._timer = self.set_timer(INTERVALO_ARRASTO, self._tique)

    def _tique(self) -> None:
        self._timer = None
        self.redesenhar()

    def _rolar_para_caixas(self) -> None:
        caixas = self.query_one(Quadro).desenho.caixas
        if caixas:
            self.query_one(Quadro).scroll_to(min(c[0] for c in caixas.values()) - MIN_X,
                                             min(c[1] for c in caixas.values()) - MIN_Y, animate=False)

    def _mostrar_selecao(self) -> None:
        d = self.query_one(Quadro).desenho
        if not self.selecao or self.selecao[0] == "ligacao" or self.selecao[1] not in d.caixas:
            return
        x, y, w, h = d.caixas[self.selecao[1]]
        if self.selecao[0] == "coluna":
            tabela = self.vista.tabelas[self.selecao[1]]
            n = next((k for k, c in enumerate(tabela.colunas) if c.id == self.selecao[2]), 0)
            self.query_one(Quadro).scroll_to_region(Region(x, y + 1 + n, w, 1), animate=False)
        else:
            self.query_one(Quadro).scroll_to_region(Region(x, y, w, h), animate=False)

    def _selecionar(self, alvo: Optional[tuple]) -> None:
        self.selecao = alvo
        self.redesenhar()
        self._mostrar_selecao()

    # ── o mouse ─────────────────────────────────────────────────────────────

    def on_quadro_selecionou(self, evento: Quadro.Selecionou) -> None:
        if evento.alvo != self.selecao:
            self.selecao = evento.alvo
            self._agendar()

    def on_quadro_arrastando(self, evento: Quadro.Arrastando) -> None:
        if self.posicoes.get(evento.tabela_id) != (evento.x, evento.y):
            self.posicoes[evento.tabela_id] = (evento.x, evento.y)
            self._agendar()

    def on_quadro_moveu(self, evento: Quadro.Moveu) -> None:
        self.posicoes[evento.tabela_id] = (evento.x, evento.y)
        self.redesenhar()
        self._guardar()

    def _guardar(self) -> None:
        guardar.gravar_posicoes(self.fonte.porta, self.fonte.banco, self.esquema, self.posicoes)

    # ── as teclas ───────────────────────────────────────────────────────────

    def action_tabela(self, passo: int) -> None:
        ordem = sorted(self.vista.tabelas, key=lambda i: (self.vista.tabelas[i].nome, i))
        if not ordem:
            return
        if self.selecao and self.selecao[0] != "ligacao" and self.selecao[1] in ordem:
            novo = ordem[(ordem.index(self.selecao[1]) + passo) % len(ordem)]
        else:
            novo = ordem[0] if passo > 0 else ordem[-1]
        self._selecionar(("tabela", novo))

    def action_coluna(self, passo: int) -> None:
        if not self.selecao or self.selecao[0] == "ligacao" or self.selecao[1] not in self.vista.tabelas:
            return
        colunas = [c.id for c in self.vista.tabelas[self.selecao[1]].colunas]
        if not colunas:
            return
        if self.selecao[0] == "tabela":
            if passo > 0:
                self._selecionar(("coluna", self.selecao[1], colunas[0]))
            return
        k = colunas.index(self.selecao[2]) + passo if self.selecao[2] in colunas else 0
        if k < 0:
            self._selecionar(("tabela", self.selecao[1]))
        else:
            self._selecionar(("coluna", self.selecao[1], colunas[min(k, len(colunas) - 1)]))

    def action_mover(self, dx: int, dy: int) -> None:
        if not self.selecao or self.selecao[0] == "ligacao" or self.selecao[1] not in self.posicoes:
            return
        x, y = self.posicoes[self.selecao[1]]
        self.posicoes[self.selecao[1]] = (max(MIN_X, x + dx), max(MIN_Y, y + dy))
        self.redesenhar()
        self._mostrar_selecao()
        self._guardar()

    def action_voltar(self) -> None:
        self.app.pop_screen()

    def action_esquema(self) -> None:
        def escolhido(nome):
            if nome and nome != self.esquema:
                self.esquema, self.selecao = nome, None
                self.marca = self.fonte.assinatura()
                self.carregar(self.fonte.modelo(nome), primeira=True)
        self.app.push_screen(EscolherEsquema(self.fonte.esquemas(), self.esquema), escolhido)

    def action_foco(self) -> None:
        def nome_dado(nome):
            if not nome:
                return
            achadas = [i for i in self._ordem(self.todo) if nome.lower() in self.todo.tabelas[i].nome.lower()]
            exata = [i for i in achadas if self.todo.tabelas[i].nome == nome]
            if not achadas:
                self.notify(t("canvas.foco_nao_achou", nome=nome), severity="warning")
                return
            self.foco = (exata or achadas)[0]
            self.selecao = ("tabela", self.foco)
            self.carregar(self.todo)
            self._rolar_para_caixas()
        self.app.push_screen(PerguntarNome(t("canvas.qual_foco")), nome_dado)

    def action_tudo(self) -> None:
        if self.foco:
            self.foco = None
            self.carregar(self.todo)

    # ── olhar o banco de tempos em tempos ───────────────────────────────────

    @work(thread=True, exclusive=True, group="vigia_canvas")
    def vigiar(self):
        try:
            marca = self.fonte.assinatura()
            if marca == self.marca:
                return
            m = self.fonte.modelo(self.esquema)
        except Exception:
            return
        self.app.call_from_thread(self._atualizar, marca, m)

    def _atualizar(self, marca, m) -> None:
        self.marca = marca
        self.carregar(m)
