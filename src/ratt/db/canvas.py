"""A tela do canvas: o diagrama das tabelas de um schema, para ver, selecionar e arrastar.

`FonteBanco` é o único ponto onde a tela toca o banco (nos testes entra uma fonte falsa).
`Quadro` só exibe a grade de texto de `desenho.Desenho` e traduz o mouse; `TelaCanvas` guarda o estado
(posições, seleção, foco, rascunho) e redesenha quando algo muda. Os formulários que montam as operações
do rascunho moram em `formularios.py`. Nada aqui executa DDL: o rascunho só vai ao disco (`guardar`).
"""
from __future__ import annotations

import getpass
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.segment import Segment
from rich.console import Group
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.geometry import Region, Size
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from ratt.db import acesso, catalogo, checagem, desenho, formularios, guardar, modelo as _modelo, rascunho as _rascunho
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
        """`conexao` só serve para injetar uma já pronta (testes); com None o canvas abre a SUA, na primeira
        consulta, para não dividir com a tela do banco a conexão que ela usa em outras threads."""
        self._conexao, self._injetada = conexao, conexao is not None
        self._trava = threading.Lock()
        self.porta, self.usuario, self.banco = porta, usuario, banco

    @property
    def conexao(self):
        with self._trava:
            if self._conexao is None:
                self._conexao, _ = acesso.entrar(self.porta, self.usuario, self.banco)
                if self._conexao is None:
                    raise RuntimeError(t("run.sem_acesso", usuario=self.usuario, banco=self.banco))
            return self._conexao

    def fechar(self) -> None:
        """Fecha a conexão que a fonte abriu (a injetada é de quem a deu)."""
        with self._trava:
            if self._conexao is not None and not self._injetada:
                self._conexao.close()
            self._conexao = None if not self._injetada else self._conexao

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
    can_focus = False           # as teclas são da tela: o ScrollView focado tomaria as setas para rolar

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


# ── o painel do SQL ─────────────────────────────────────────────────────────

class PainelSQL(VerticalScroll):
    """O SQL do rascunho, sempre à vista. Com o foco, ↑/↓ escolhem uma operação e x/delete a tiram."""

    class Tirar(Message):
        def __init__(self, indice: int):
            super().__init__()
            self.indice = indice

    BINDINGS = [
        Binding("up", "passo(-1)", show=False),
        Binding("down", "passo(1)", show=False),
        Binding("x,delete", "tirar", t("canvas.k.tirar")),
        Binding("escape", "sair", t("canvas.k.voltar")),
    ]
    can_focus = True
    DEFAULT_CSS = """
    PainelSQL { height: auto; max-height: 10; padding: 0 1; border-top: solid $panel; }
    PainelSQL:focus { border-top: solid $accent; }
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.passos: list = []
        self.indice = 0
        self.falhou: Optional[int] = None   # a operação que o servidor recusou ao aplicar
        self._linhas: List[int] = []        # linha em que cada operação começa no texto desenhado
        self._plano = ""

    def compose(self) -> ComposeResult:
        yield Static(id="sql_conteudo")

    def texto_plano(self) -> str:
        return self._plano

    def atualizar(self, passos, falhou: Optional[int] = None) -> None:
        self.passos = passos
        self.falhou = falhou
        self.indice = max(0, min(self.indice, len(passos) - 1))
        self._desenhar()

    def _desenhar(self) -> None:
        alvo = self.query_one("#sql_conteudo", Static)
        if not self.passos:
            self._linhas, self._plano = [], t("canvas.sem_rascunho")
            alvo.update(Text(self._plano, style=COR["cinza"]))
            return
        partes, plano, y, self._linhas = [], [], 0, []
        for n, (op, valida, desc, comandos) in enumerate(self.passos):
            escolhida = self.has_focus and n == self.indice
            fundo = "#1f3550" if escolhida else None
            if n:
                partes.append(Text(""))
                plano.append("")
                y += 1
            self._linhas.append(y)
            if valida:
                codigo = f"-- {n + 1}. {desc}\n" + "\n".join(c + ";" for c in comandos)
                partes.append(Syntax(codigo, "sql", theme="ansi_dark", background_color=fundo or "default", word_wrap=True))
            else:
                codigo = f"-- {n + 1}. {t('canvas.invalida')}"
                partes.append(Text(codigo, style=Style(color=COR["laranja"], bgcolor=fundo)))
            if n == self.falhou:
                marca = t("canvas.falhou")
                partes.append(Text(marca, style=Style(color=COR["laranja"], bold=True)))
                codigo += "\n" + marca
            plano.append(codigo)
            y += len(codigo.splitlines())
        self._plano = "\n".join(plano)
        alvo.update(Group(*partes))

    def _ver_escolhida(self) -> None:
        if self._linhas:
            self.scroll_to_region(Region(0, self._linhas[self.indice], 1, 2), animate=False)

    def action_passo(self, passo: int) -> None:
        if self.passos:
            self.indice = max(0, min(self.indice + passo, len(self.passos) - 1))
            self._desenhar()
            self._ver_escolhida()

    def action_tirar(self) -> None:
        if self.passos:
            self.post_message(self.Tirar(self.indice))

    def action_sair(self) -> None:
        self.screen.set_focus(None)

    def on_focus(self) -> None:
        self._desenhar()

    def on_blur(self) -> None:
        self._desenhar()

    def on_click(self, evento: events.Click) -> None:
        y = evento.y        # já inclui a rolagem do painel
        for n in range(len(self._linhas) - 1, -1, -1):
            if self._linhas[n] <= y:
                self.indice = n
                break
        self._desenhar()


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

    def __init__(self, pergunta, valor: str = ""):
        super().__init__()
        self.pergunta, self.valor = pergunta, valor

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self.pergunta, style="bold"))
            yield Input(value=self.valor)

    def on_input_submitted(self, evento):
        self.dismiss(evento.value.strip())


# ── aplicar ─────────────────────────────────────────────────────────────────

def aplicar_rascunho(r: _rascunho.Rascunho, conexao, arquivo: Path, ensaio: bool, cabecalho: str) -> Tuple[int, Optional[int]]:
    """Grava o SQL do rascunho em `arquivo` e o roda com o mesmo executor do `ratt db run`.
    Devolve (código, índice da operação que falhou ou None). O cabeçalho entra no texto e na conta das linhas."""
    from ratt.db import rodar
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(r.sql_texto(cabecalho), encoding="utf-8")
    falhou: List[int] = []

    def ao_falhar(comando) -> None:
        indice = r.operacao_da_linha(comando.linha, cabecalho)
        if indice >= 0:
            falhou.append(indice)

    codigo = rodar.executar(arquivo, conexao, ensaio=ensaio, ao_falhar=ao_falhar)
    return codigo, (falhou[0] if falhou else None)


# ── a tela ──────────────────────────────────────────────────────────────────

class TelaCanvas(Screen):
    AUTO_FOCUS = ""         # o foco só vai ao painel do SQL quando a pessoa pede (r ou clique): as setas são do quadro
    BINDINGS = [
        Binding("tab", "tabela(1)", t("canvas.k.proxima")),
        Binding("shift+tab", "tabela(-1)", t("canvas.k.anterior"), show=False),
        Binding("up", "vertical(-1)", t("canvas.k.coluna_acima"), show=False),
        Binding("down", "vertical(1)", t("canvas.k.coluna_abaixo"), show=False),
        Binding("left", "rolar(-4,0)", show=False),
        Binding("right", "rolar(4,0)", show=False),
        Binding("pageup", "pagina(-1)", t("canvas.k.rolar")),
        Binding("pagedown", "pagina(1)", show=False),
        Binding("home", "pagina(-2)", show=False),
        Binding("end", "pagina(2)", show=False),
        Binding("shift+up", "mover(0,-1)", t("canvas.k.mover")),
        Binding("shift+down", "mover(0,1)", show=False),
        Binding("shift+left", "mover(-2,0)", show=False),
        Binding("shift+right", "mover(2,0)", show=False),
        Binding("enter", "aplicar", t("canvas.k.aplicar")),
        Binding("e", "ensaio", t("canvas.k.ensaio")),
        Binding("w", "exportar", t("canvas.k.exportar")),
        Binding("a", "nova_tabela", t("canvas.k.tabela")),
        Binding("c", "nova_coluna", t("canvas.k.coluna")),
        Binding("l", "ligar", t("canvas.k.ligar")),
        Binding("space", "menu", t("canvas.k.editar")),
        Binding("x,delete", "apagar", t("canvas.k.apagar")),
        Binding("u", "desfazer", t("canvas.k.desfazer")),
        Binding("r", "painel", t("canvas.k.rascunho")),
        Binding("s", "esquema", t("canvas.k.esquema")),
        Binding("f", "foco", t("canvas.k.foco")),
        Binding("F,shift+f", "tudo", t("canvas.k.tudo")),
        Binding("escape,q", "voltar", t("canvas.k.voltar")),
    ]
    DEFAULT_CSS = """
    #canvas_titulo { height: 1; padding: 0 1; background: $panel; }
    """

    def __init__(self, fonte, esquema: str):
        super().__init__()
        self.fonte, self.esquema = fonte, esquema
        self.selecao: Optional[tuple] = None
        self.posicoes: Dict[str, Tuple[int, int]] = {}
        self.vista: _modelo.Modelo = _modelo.Modelo(esquema)
        self.rascunho = _rascunho.Rascunho(_modelo.Modelo(esquema))
        self.todo: _modelo.Modelo = self.vista      # o schema inteiro com o rascunho; `vista` é o que se vê (o foco recorta)
        self.foco: Optional[str] = None
        self.avisos: Dict[str, str] = {}
        self.marca = None
        self.carregado = False          # já chegou o primeiro modelo deste schema?
        self.falha: Optional[int] = None    # operação que falhou ao aplicar; some na próxima mudança do rascunho
        self._aplicando = False             # entre o commit e a releitura: o vigia não mexe no rascunho
        self._ultimo = 0.0
        self._timer = None

    def compose(self) -> ComposeResult:
        yield Static(id="canvas_titulo")
        yield Quadro(id="quadro")
        yield PainelSQL(id="canvas_sql")
        yield Footer()

    def on_mount(self):
        self.redesenhar()
        self.query_one(PainelSQL).atualizar([])
        self.ler(self.esquema)
        self.set_interval(2.0, self.vigiar)

    def on_unmount(self):
        fechar = getattr(self.fonte, "fechar", None)
        if fechar:
            fechar()

    # ── o modelo e as posições ──────────────────────────────────────────────

    def carregar(self, m: _modelo.Modelo, primeira: bool = False) -> None:
        """Chegou o modelo do banco: é a base do rascunho. Na primeira vez o rascunho vem do disco; nas outras
        ele é rebaseado (nada se perde; o que deixou de valer fica marcado) e gravado."""
        if primeira:
            self.rascunho = self._abrir_rascunho(m)
        else:
            antes = self.rascunho.invalidas
            novas = self.rascunho.rebase(m) - antes
            if novas:
                self.notify(t("canvas.invalidas", n=len(novas)), severity="warning")
            self._persistir()
        self._montar(primeira)

    def _abrir_rascunho(self, m: _modelo.Modelo) -> _rascunho.Rascunho:
        try:
            dados = guardar.ler_rascunho(self.fonte.porta, self.fonte.banco, self.esquema)
            if dados is None:
                return _rascunho.Rascunho(m)
            r = _rascunho.Rascunho.de_dict(dados, m)
        except _rascunho.RascunhoCorrompido:
            try:
                caminho = guardar.separar_rascunho(self.fonte.porta, self.fonte.banco, self.esquema)
                self.notify(t("canvas.corrompido", caminho=caminho), severity="warning", timeout=15)
            except OSError as erro:
                self.notify(t("canvas.erro_gravar", erro=erro), severity="error")
            return _rascunho.Rascunho(m)
        if r.invalidas:
            self.notify(t("canvas.invalidas", n=len(r.invalidas)), severity="warning")
        return r

    def _persistir(self) -> None:
        """Grava o rascunho a cada mudança; vazio apaga o arquivo."""
        try:
            guardar.gravar_rascunho(self.fonte.porta, self.fonte.banco, self.esquema,
                                    self.rascunho.para_dict() if self.rascunho.operacoes else None)
        except OSError as erro:
            self.notify(t("canvas.erro_gravar", erro=erro), severity="error")

    def _montar(self, primeira: bool = False) -> None:
        """Refaz `todo` (base + rascunho), o recorte do foco, os avisos e o painel, e redesenha."""
        m = self.todo = self.rascunho.vista()
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
        self.query_one(PainelSQL).atualizar(self.rascunho.passos(), self.falha)
        self.redesenhar()
        if primeira and self.vista.tabelas:
            self._rolar_para_caixas()

    def _mudou(self) -> None:
        """Uma edição entrou ou saiu do rascunho: grava, redesenha e mantém a seleção à vista."""
        self.falha = None
        self._persistir()
        self._montar()
        self._mostrar_selecao()

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
                  (" · ", COR["cinza"]), (t("canvas.rascunho", n=len(self.rascunho.operacoes)), COR["laranja"] if self.rascunho.operacoes else COR["cinza"])]
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
        if not self._livre():
            return
        self.listar_esquemas()

    def action_vertical(self, passo: int) -> None:
        """↑/↓ andam pelas colunas da tabela selecionada; sem seleção de tabela ou coluna, rolam o quadro."""
        if self.selecao and self.selecao[0] in ("tabela", "coluna"):
            self.action_coluna(passo)
        else:
            self.action_rolar(0, passo * 2)

    def action_rolar(self, dx: int, dy: int) -> None:
        self.query_one(Quadro).scroll_relative(x=dx, y=dy, animate=False)

    def action_pagina(self, onde: int) -> None:
        q = self.query_one(Quadro)
        {-2: q.scroll_home, 2: q.scroll_end, -1: q.scroll_page_up, 1: q.scroll_page_down}[onde](animate=False)

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
            self._montar()
            self._rolar_para_caixas()
        self.app.push_screen(PerguntarNome(t("canvas.qual_foco")), nome_dado)

    def action_tudo(self) -> None:
        if self.foco:
            self.foco = None
            self._montar()

    # ── editar ──────────────────────────────────────────────────────────────

    def _livre(self) -> bool:
        """Entre o commit e a releitura o rascunho já foi esvaziado e o modelo ainda é o velho: nada de editar,
        aplicar de novo, exportar nem trocar de schema nesse intervalo."""
        if self._aplicando:
            self.notify(t("canvas.aplicando"), severity="warning")
        return not self._aplicando

    def _pronto(self) -> bool:
        if not self._livre():
            return False
        if not self.carregado:
            self.notify(t("canvas.carregando"))
        return self.carregado

    def _abrir(self, form) -> None:
        self.app.push_screen(form, self._depois_de_editar)

    def _depois_de_editar(self, op) -> None:
        if op is None:
            return
        criada = op.tabela.id if isinstance(op, _rascunho.CriarTabela) else op.tabela_id if isinstance(op, _rascunho.LigarNN) else None
        if criada:
            self.selecao = ("tabela", criada)
        self._mudou()

    def action_nova_tabela(self) -> None:
        if self._pronto():
            self._abrir(formularios.FormTabela(self.rascunho, self.fonte))

    def action_nova_coluna(self) -> None:
        if not self._pronto():
            return
        if not self.selecao or self.selecao[0] not in ("tabela", "coluna"):
            self.notify(t("canvas.sel_tabela_ou_coluna"), severity="warning")
            return
        self._abrir(formularios.FormColuna(self.rascunho, self.fonte, self.selecao[1]))

    def action_ligar(self) -> None:
        if not self._pronto():
            return
        if not self.selecao or self.selecao[0] not in ("tabela", "coluna"):
            self.notify(t("canvas.sel_tabela_ou_coluna"), severity="warning")
            return
        coluna = self.selecao[2] if self.selecao[0] == "coluna" else None
        self._abrir(formularios.FormLigar(self.rascunho, self.fonte, self.selecao[1], coluna))

    def action_apagar(self) -> None:
        if not self._pronto():
            return
        if not self.selecao:
            self.notify(t("canvas.sel_qualquer"), severity="warning")
            return
        self._abrir(formularios.ConfirmarApagar(self.rascunho, self.fonte, self.selecao))

    def action_menu(self) -> None:
        if not self._pronto():
            return
        if not self.selecao:
            self.notify(t("canvas.sel_qualquer"), severity="warning")
            return
        v = self.rascunho.vista()
        if self.selecao[0] == "ligacao":
            self.notify(t("canvas.sem_menu"))
            return
        if self.selecao[1] not in v.tabelas or (self.selecao[0] == "coluna" and v.tabelas[self.selecao[1]].coluna(self.selecao[2]) is None):
            self.notify(t("canvas.e.sumiu"), severity="warning")
            return
        tab = v.tabelas[self.selecao[1]]
        if self.selecao[0] == "tabela":
            nome, itens = tab.nome, [("renomear", t("canvas.m.renomear"))]
        else:
            c = tab.coluna(self.selecao[2])
            nome = f"{tab.nome}.{c.nome}"
            itens = [("renomear", t("canvas.m.renomear")),
                     ("opcional" if c.obrigatoria else "obrigatoria", t("canvas.m.opcional" if c.obrigatoria else "canvas.m.obrigatoria")),
                     ("nao_unica" if c.unica else "unica", t("canvas.m.nao_unica" if c.unica else "canvas.m.unica"))]
        sel = self.selecao
        self.app.push_screen(formularios.MenuSelecao(nome, itens), lambda chave: self._do_menu(chave, sel, nome))

    def _do_menu(self, chave, sel, nome) -> None:
        if not chave:
            return
        tid, cid = sel[1], (sel[2] if sel[0] == "coluna" else None)
        r, f = self.rascunho, self.fonte
        try:
            atual = r.vista().tabelas[tid].nome if cid is None else r.vista().tabelas[tid].coluna(cid).nome
        except (KeyError, AttributeError):
            self.notify(t("canvas.e.sumiu"), severity="warning")
            return
        if chave == "renomear":
            form = formularios.FormSimples(r, f, t("canvas.f.renomear", nome=nome),
                                           lambda novo: _rascunho.Renomear(tid, cid, novo), valor=atual)
        else:
            sim = chave in ("obrigatoria", "unica")
            classe = _rascunho.Obrigatoria if chave in ("obrigatoria", "opcional") else _rascunho.Unica
            form = formularios.FormSimples(r, f, t("canvas.f." + chave, nome=nome),
                                           lambda _: classe(tid, cid, sim))
        self._abrir(form)

    def action_desfazer(self) -> None:
        if not self._livre():
            return
        if self.rascunho.desfazer() is None:
            self.notify(t("canvas.nada_desfazer"))
            return
        self._mudou()

    def action_painel(self) -> None:
        self.query_one(PainelSQL).focus()

    def on_painel_sql_tirar(self, evento: PainelSQL.Tirar) -> None:
        if not self._livre():
            return
        i = evento.indice
        if i >= len(self.rascunho.operacoes):
            return
        dependentes = self.rascunho.dependentes(i)
        if not dependentes:
            self._tirar([i])
            return
        passos = self.rascunho.passos()
        descricoes = [passos[j][2] if passos[j][1] else passos[j][0].tipo for j in dependentes]
        self.app.push_screen(formularios.ConfirmarTirar(descricoes),
                             lambda sim: self._tirar([i] + dependentes) if sim else None)

    def _tirar(self, indices) -> None:
        self.rascunho.tirar(indices)
        self._mudou()

    # ── aplicar, ensaiar e exportar ─────────────────────────────────────────

    def _cabecalho(self) -> str:
        """A linha de comentário do topo do SQL. A mesma para o texto e para achar a operação pela linha."""
        return t("canvas.cabecalho_sql", banco=self.fonte.banco, esquema=self.esquema, n=len(self.rascunho.operacoes))

    def _pode_enviar(self) -> bool:
        """Vazio ou com operações que deixaram de valer: não vai ao banco nem ao arquivo."""
        if not self._pronto():
            return False
        if not self.rascunho.operacoes:
            self.notify(t("canvas.rascunho_vazio"), severity="warning")
            return False
        invalidas = self.rascunho.invalidas
        if invalidas:
            self.notify(t("canvas.tem_invalidas", n=len(invalidas)), severity="warning")
            return False
        return True

    def action_aplicar(self) -> None:
        if not self._pode_enviar():
            return
        passos = self.rascunho.passos()
        # foco em "cancelar": um Enter que sobrou no teclado nunca grava no banco
        self.app.push_screen(
            formularios.Confirmar(t("canvas.f.aplicar_pergunta", banco=self.fonte.banco, n=len(passos)),
                                  [], t("canvas.f.aplicar"), foco="nao",
                                  sql=self.rascunho.sql_texto(self._cabecalho())),
            lambda sim: self._rodar(False) if sim else None)

    def action_ensaio(self) -> None:
        if self._pode_enviar():
            self._rodar(True)

    def _rodar(self, ensaio: bool) -> None:
        """Entrega o rascunho ao `ratt db run`: a tela se suspende, a pessoa vê a mesma saída e volta com Enter.
        Tudo o que decide algo depois está em `_concluir`, que roda fora da suspensão (e é testável)."""
        if not self._pode_enviar():             # o banco pode ter mudado enquanto a confirmação estava aberta
            return
        cabecalho = self._cabecalho()
        texto = self.rascunho.sql_texto(cabecalho)
        arquivo = guardar.pasta(self.fonte.porta, self.fonte.banco) / f"aplicando-{self.esquema}.sql"
        conexao = self.fonte.conexao_para_aplicar()
        if conexao is None:
            self.notify(t("canvas.sem_conexao"), severity="error")
            return
        try:
            with self.app.suspend():
                resultado = self._sessao(conexao, arquivo, ensaio, cabecalho)
        except Exception as erro:             # o rascunho fica: nada foi decidido
            self.notify(t("canvas.erro_aplicar", erro=erro), severity="error")
            return
        finally:
            conexao.close()
        self._concluir(resultado[0], resultado[1], ensaio, texto)

    def _sessao(self, conexao, arquivo: Path, ensaio: bool, cabecalho: str) -> Tuple[int, Optional[int]]:
        """A parte que fala com o terminal de verdade (não roda sob `run_test`): cabeçalho, execução, Enter."""
        from ratt.cli import c
        modo = c(f" · {t('run.modo_ensaio')}", "amarelo") if ensaio else ""
        quem = f"{t('canvas.run_rascunho')} · {getattr(self.fonte, 'usuario', None) or getpass.getuser()} @ {self.fonte.banco}"
        print(f"{c('ratt db run', 'azul', 'negrito')} {c(quem, 'cinza')}{modo}")
        with conexao:
            resultado = aplicar_rascunho(self.rascunho, conexao, arquivo, ensaio, cabecalho)
        try:
            input("\n" + t("canvas.enter_volta"))
        except EOFError:
            pass
        try:                    # o Textual não descarta o que foi digitado enquanto estava suspenso
            import termios
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
        except Exception:
            pass
        return resultado

    def _concluir(self, codigo: int, indice: Optional[int], ensaio: bool, texto: str) -> None:
        if codigo != 0:
            self._marcar_falha(indice)
            self.notify(t("canvas.ensaio_falhou" if ensaio else "canvas.aplicar_falhou"), severity="error")
            return
        if ensaio:
            self.falha = None
            self.query_one(PainelSQL).atualizar(self.rascunho.passos(), None)
            self.notify(t("canvas.ensaio_ok"))
            return
        nomes = {i: tab.nome for i, tab in self.rascunho.vista().tabelas.items()
                 if i.startswith("novo-t") and tab.estado != "apagado"}
        # o banco já gravou: arquivar e esvaziar o rascunho AGORA, sem esperar a releitura. Se a pessoa sair ou
        # trocar de schema nesse intervalo, nada aplicado fica no disco como se estivesse pendente.
        try:
            caminho = guardar.guardar_aplicado(self.fonte.porta, self.fonte.banco, texto)
            self.notify(t("canvas.aplicado", caminho=caminho))
        except OSError as e:
            self.notify(t("canvas.erro_gravar", erro=e), severity="error")
        self.rascunho = _rascunho.Rascunho(self.rascunho.modelo())
        self.falha, self.selecao = None, None
        self._persistir()
        self._montar()
        self._aplicando = True
        self._reler_aplicado(self.esquema, nomes)

    def _marcar_falha(self, indice: Optional[int]) -> None:
        self.falha = indice if indice is not None and indice >= 0 else None
        self.query_one(PainelSQL).atualizar(self.rascunho.passos(), self.falha)

    @work(thread=True, exclusive=True, group="aplicar_canvas")
    def _reler_aplicado(self, esquema: str, nomes: Dict[str, str]) -> None:
        try:
            m = self.fonte.modelo(esquema)
        except Exception as erro:
            self.app.call_from_thread(self._aplicado, esquema, None, nomes, erro)
            return
        self.app.call_from_thread(self._aplicado, esquema, m, nomes)

    def _aplicado(self, esquema: str, m: Optional[_modelo.Modelo], nomes: Dict[str, str], erro=None) -> None:
        """Chegou o catálogo depois do apply: leva as posições das tabelas novas para os ids dele (pelo nome) e
        passa a mostrar o banco como ficou. Se a tela já saiu ou trocou de schema, só as posições do schema
        aplicado são acertadas, no disco."""
        self._aplicando = False
        mesma = self.is_attached and esquema == self.esquema
        if m is None:
            if mesma:
                self.marca = None               # o vigia traz o catálogo de verdade na próxima volta
                self._falhou(esquema, erro)
            return
        por_nome = {tab.nome: tab.id for tab in m.tabelas.values()}
        posicoes = self.posicoes if mesma else guardar.ler_posicoes(self.fonte.porta, self.fonte.banco, esquema)
        for velho, nome in nomes.items():
            if velho in posicoes and nome in por_nome:
                posicoes[por_nome[nome]] = posicoes.pop(velho)
        posicoes = {i: xy for i, xy in posicoes.items() if not i.startswith("novo-")}
        try:
            guardar.gravar_posicoes(self.fonte.porta, self.fonte.banco, esquema, posicoes)
        except OSError as e:
            self.notify(t("canvas.erro_gravar", erro=e), severity="error")
        if not mesma:
            return
        self.posicoes = posicoes
        self.rascunho = _rascunho.Rascunho(m)
        self._montar()

    def action_exportar(self) -> None:
        if not self._pode_enviar():
            return
        padrao = f"rascunho-{self.fonte.banco}-{self.esquema}.sql"
        # None é Escape (cancela); "" é Enter sem digitar (vale o caminho padrão)
        self.app.push_screen(PerguntarNome(t("canvas.f.caminho"), padrao),
                             lambda valor: None if valor is None else self._exportar(valor or padrao))

    def _exportar(self, valor: Optional[str]) -> None:
        if not valor:
            return
        caminho = Path(valor).expanduser()
        if caminho.exists():
            self.app.push_screen(
                formularios.Confirmar(t("canvas.f.sobrescrever", caminho=caminho), [], t("canvas.f.sobrescrever_sim"), foco="nao"),
                lambda sim: self._gravar_exportado(caminho) if sim else None)
        else:
            self._gravar_exportado(caminho)

    def _gravar_exportado(self, caminho: Path) -> None:
        if not self._pode_enviar():             # o banco pode ter mudado enquanto a pergunta estava aberta
            return
        try:
            caminho.write_text(self.rascunho.sql_texto(self._cabecalho()), encoding="utf-8")
        except OSError as erro:
            self.notify(t("canvas.erro_exportar", caminho=caminho, erro=erro), severity="error")
            return
        self.notify(t("canvas.exportado", caminho=caminho))

    # ── ler o banco (sempre fora da thread da tela) ─────────────────────────

    @work(thread=True, exclusive=True, group="vigia_canvas")
    def ler(self, esquema: str, so_se_mudou: bool = False):
        """Lê o modelo de `esquema`. Se a tela já trocou de schema ou saiu quando a resposta chega, ela é descartada."""
        avisar = not self.carregado
        try:
            marca = self.fonte.assinatura()
            if so_se_mudou and marca == self.marca:
                return
            m = self.fonte.modelo(esquema)
        except Exception as erro:
            if avisar:                  # o poller falha em silêncio; quem abre ou troca de schema precisa saber
                self.app.call_from_thread(self._falhou, esquema, erro)
            return
        self.app.call_from_thread(self._chegou, esquema, marca, m)

    def vigiar(self):
        if self.carregado:
            self.ler(self.esquema, so_se_mudou=True)

    def _chegou(self, esquema, marca, m) -> None:
        if not self.is_attached or esquema != self.esquema or self._aplicando:
            return
        self.marca = marca
        primeira, self.carregado = not self.carregado, True
        self.carregar(m, primeira=primeira)

    def _falhou(self, esquema, erro) -> None:
        if self.is_attached and esquema == self.esquema:
            linhas = str(erro).strip().splitlines()
            self.notify(t("canvas.erro_banco", erro=linhas[0] if linhas else type(erro).__name__), severity="error")

    @work(thread=True, exclusive=True, group="esquemas_canvas")
    def listar_esquemas(self):
        try:
            nomes = self.fonte.esquemas()
        except Exception as erro:
            self.app.call_from_thread(self._falhou, self.esquema, erro)
            return
        self.app.call_from_thread(self._escolher_esquema, nomes)

    def _escolher_esquema(self, nomes) -> None:
        if self.is_attached:
            self.app.push_screen(EscolherEsquema(nomes, self.esquema), self.trocar_esquema)

    def trocar_esquema(self, nome) -> None:
        if not nome or nome == self.esquema:
            return
        self.esquema, self.selecao, self.foco = nome, None, None
        self.posicoes, self.carregado, self.marca = {}, False, None
        self.falha = None
        self.todo = self.vista = _modelo.Modelo(nome)
        self.rascunho = _rascunho.Rascunho(self.todo)
        self.query_one(PainelSQL).atualizar([])
        self.redesenhar()
        self.ler(nome)
