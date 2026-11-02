"""A tela do canvas: o diagrama das tabelas de um schema, para ver, selecionar e arrastar.

`FonteBanco` é o único ponto onde a tela toca o banco (nos testes entra uma fonte falsa).
`Quadro` só exibe a grade de texto de `desenho.Desenho` e traduz o mouse; `TelaCanvas` guarda o estado
(posições, seleção, foco) e redesenha quando algo muda. O rascunho é da `sessao.SessaoRascunho` do schema,
dividida com as outras telas; o painel do SQL e as ações sobre o rascunho (aplicar, ensaio, exportar, desfazer)
vêm de `painel.py`. Os formulários que montam as operações moram em `formularios.py`. Nada aqui executa DDL.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
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
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from ratt.db import acesso, catalogo, checagem, desenho, formularios, guardar, modelo as _modelo, rascunho as _rascunho, sessao
from ratt.db.idioma import t
from ratt.db.painel import AcoesRascunho, PainelSQL, PerguntarNome      # noqa: F401 (PerguntarNome: importável daqui)
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
        self._fechada = False
        self._trava = threading.Lock()
        self.porta, self.usuario, self.banco = porta, usuario, banco

    @property
    def conexao(self):
        """A conexão (aberta na primeira vez); None depois de `fechar` — a fonte não reabre sozinha."""
        with self._trava:
            if self._fechada:
                return None
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
            self._fechada = True

    def _aberta(self):
        conexao = self.conexao
        if conexao is None:             # quem chama roda numa thread que trata erro: a tela já saiu
            raise RuntimeError(t("canvas.fonte_fechada"))
        return conexao

    def esquemas(self) -> List[str]:
        return catalogo.esquemas_com_tabelas(self._aberta())

    def modelo(self, esquema: str) -> _modelo.Modelo:
        return catalogo.modelo(self._aberta(), esquema)

    def assinatura(self):
        return catalogo.assinatura(self._aberta(), escritas=False)

    def consulta(self) -> checagem.Consulta:
        return checagem.Consulta(self._aberta())

    # ── o que a tela Dados lê ──

    def assinaturas(self, oid: Optional[int]) -> Tuple[tuple, object]:
        """(a estrutura do banco, as escritas na tabela `oid`): a tela Dados relê a grade quando alguém escreve
        NESTA tabela (não em qualquer uma), e o modelo só quando a estrutura muda."""
        conexao = self._aberta()
        estrutura = tuple(catalogo.assinatura(conexao, escritas=False))
        return estrutura, (catalogo.escritas_da_tabela(conexao, oid) if oid is not None else None)

    def colunas(self, oid: int) -> List[catalogo.Coluna]:
        return catalogo.colunas(self._aberta(), oid)

    def pagina(self, esquema: str, tabela: str, onde: str, ordem: List[str], deslocamento: int, limite: int):
        return catalogo.pagina(self._aberta(), esquema, tabela, onde, ordem, deslocamento, limite)

    def contar(self, esquema: str, tabela: str, onde: str) -> Optional[int]:
        return catalogo.contar_filtrado(self._aberta(), esquema, tabela, onde)

    def linhas_pai(self, esquema: str, tabela: str, chave: str, busca: str, limite: int = 50):
        return catalogo.linhas_pai(self._aberta(), esquema, tabela, chave, busca, limite)

    def classe(self, oid: int) -> Optional[str]:
        """O relkind (r, p, f, v, m...) da relação; None se ela não existe mais."""
        linha = self._aberta().execute("SELECT relkind FROM pg_class WHERE oid = %s", (oid,)).fetchone()
        return str(linha[0]) if linha else None

    def outra(self) -> "FonteBanco":
        """Uma fonte igual, com a sua própria conexão (a injetada é dividida): a tela Dados aberta do canvas
        conversa com o banco sem disputar a conexão da vigia do canvas."""
        return FonteBanco(self._conexao if self._injetada else None, self.porta, self.usuario, self.banco)

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


# ── aplicar ─────────────────────────────────────────────────────────────────

def aplicar_rascunho(r: _rascunho.Rascunho, conexao, arquivo: Path, ensaio: bool, cabecalho: str) -> Tuple[int, Optional[int]]:
    """Grava o SQL do rascunho em `arquivo` e o roda com o mesmo executor do `ratt db run` (é
    `sessao.executar_rascunho`; a tela aplica por `SessaoRascunho.aplicar`, este nome fica para quem já o usa)."""
    return sessao.executar_rascunho(r, conexao, arquivo, ensaio, cabecalho)


# ── a tela ──────────────────────────────────────────────────────────────────

class TelaCanvas(AcoesRascunho, Screen):
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
        Binding("plus", "expandir", t("canvas.k.expandir")),
        Binding("v", "dados", t("dados.abrir")),
        Binding("escape,q", "voltar", t("canvas.k.voltar")),
    ]
    LIMITE_TUDO = 150               # `F` em schema com mais tabelas que isso pergunta antes e desenha fora da thread da tela
    DEFAULT_CSS = """
    #canvas_titulo { height: 1; padding: 0 1; background: $panel; }
    #canvas_avisos { height: 1; padding: 0 1; }
    """

    def __init__(self, fonte, esquema: str):
        super().__init__()
        self.fonte, self.esquema = fonte, esquema
        self.selecao: Optional[tuple] = None
        self.posicoes: Dict[str, Tuple[int, int]] = {}     # as do que se vê: `_pos_todo` ou, em foco, `_pos_foco`
        self._pos_todo: Dict[str, Tuple[int, int]] = {}    # as do schema inteiro (as que vão ao disco)
        self._pos_foco: Dict[str, Tuple[int, int]] = {}    # as do recorte do foco, desde a origem (só na memória)
        self._foco_base: Dict[str, Tuple[int, int]] = {}   # `_pos_foco` como estava na última vez que foi gravada ou posta
        self._desenhando = False            # o schema inteiro está sendo desenhado numa thread
        self._marca_desenho = 0             # sobe a cada nova vista, troca de schema e `F`: o desenho de outra marca é descartado
        self._grade_pronta = None           # (posições, Desenho) vindo dessa thread
        self._foco_total = 0                # tabelas na vizinhança do foco (o recorte mostra no máximo LIMITE_FOCO)
        self.expandidas: set = set()        # tabelas recolhidas (`←N`) que a pessoa abriu com `+`
        self._versao = 0                    # sobe a cada nova vista: a grade em cache vale para uma versão só
        self._grade = None                  # (chave, Desenho sem seleção): trocar a seleção não roteia de novo
        self._lista_avisos: list = []       # os avisos estáticos da vista, para a linha de avisos
        self.vista: _modelo.Modelo = _modelo.Modelo(esquema)
        self.sessao: Optional[sessao.SessaoRascunho] = None    # a do schema; vem da aplicação ao montar a tela
        self.todo: _modelo.Modelo = self.vista      # o schema inteiro com o rascunho; `vista` é o que se vê (o foco recorta)
        self.foco: Optional[str] = None
        self.avisos: Dict[str, str] = {}
        self.marca = None
        self.carregado = False          # já chegou o primeiro modelo deste schema?
        self._calado = False                # a tela está chamando a sessão e se redesenha sozinha: ignora o eco
        self._relendo: Optional[sessao.SessaoRascunho] = None   # aplicada, à espera do catálogo novo
        self._sem_leitura = False           # a leitura do schema falhou e ainda não deu certo: o vigia tenta de novo
        self._tentando = False              # uma nova tentativa está no ar (o vigia não empilha outra)
        self._avisou_posicoes = False       # já disse que não deu para gravar as posições
        self._ultimo = 0.0
        self._timer = None

    def compose(self) -> ComposeResult:
        yield Static(id="canvas_titulo")
        yield Quadro(id="quadro")
        yield Static(id="canvas_avisos")
        yield PainelSQL(id="canvas_sql")
        yield Footer()

    def on_mount(self):
        self._ligar_sessao(self.esquema)
        self.redesenhar()
        self.query_one(PainelSQL).atualizar([])
        self.ler(self.esquema)
        self.set_interval(2.0, self.vigiar)

    def on_unmount(self):
        self.sessao.parar(self._rascunho_mudou)
        if self._relendo is not None:
            # a releitura do catálogo não vai mais chegar: a sessão não pode ficar travada em "aplicando"
            self._relendo.aplicando, self._relendo = False, None
        fechar = getattr(self.fonte, "fechar", None)
        if fechar:
            fechar()

    # ── o modelo e as posições ──────────────────────────────────────────────

    # ── o rascunho (da sessão) ──────────────────────────────────────────────

    @property
    def rascunho(self) -> _rascunho.Rascunho:
        return self.sessao.rascunho

    @property
    def _aplicando(self) -> bool:
        return self.sessao.aplicando

    @property
    def _contagens(self) -> Dict[str, int]:
        return self.sessao.contagens

    def _ligar_sessao(self, esquema: str) -> None:
        """Passa a ouvir a sessão de `esquema` (e deixa de ouvir a do schema anterior)."""
        if self.sessao is not None:
            self.sessao.parar(self._rascunho_mudou)
        self.sessao = sessao.obter(self.app, self.fonte, esquema)
        self.sessao.ouvir(self._rascunho_mudou)

    def _rascunho_mudou(self) -> None:
        """A sessão mudou o rascunho (por esta tela ou por outra): refaz a vista e o painel. Antes do primeiro
        modelo não há o que montar; e quando é a própria tela que chamou a sessão, ela se redesenha em seguida."""
        if self.is_attached and self.carregado and not self._calado:
            self._montar()

    def _calada(self, fn, *args):
        """Chama a sessão sem redesenhar pelo aviso dela: quem chama redesenha logo depois."""
        self._calado = True
        try:
            return fn(*args)
        finally:
            self._calado = False

    def _notificar(self, avisos) -> None:
        for aviso in avisos:
            self.notify(str(aviso), **aviso.opcoes())

    def carregar(self, m: _modelo.Modelo, primeira: bool = False) -> None:
        """Chegou o modelo do banco: é a base do rascunho. Na primeira vez a sessão abre o rascunho (do disco, se
        ninguém o abriu ainda); nas outras ele é rebaseado (nada se perde; o que deixou de valer fica marcado)."""
        if primeira:
            self._notificar(self._calada(self.sessao.abrir, m))
        else:
            novas = self._calada(self.sessao.rebase, m)
            if novas:
                self.notify(t("canvas.invalidas", n=len(novas)), severity="warning")
        self._montar(primeira)

    def on_screen_resume(self) -> None:
        """A tela voltou a ser a de cima: se o rascunho no disco não é mais o que a sessão gravou ou leu (outro
        processo mexeu nele), vale o do disco — um rascunho velho na memória nunca o apaga."""
        if not self.carregado or self.sessao.aplicando:
            return
        avisos = self._calada(self.sessao.reler, self.rascunho.base)
        if avisos is None:
            return
        self._notificar(avisos)
        self._montar()

    def _montar(self, primeira: bool = False) -> None:
        """Refaz `todo` (base + rascunho), o recorte do foco, os avisos e o painel, e redesenha."""
        m = self.todo = self.rascunho.vista()
        self._marca_desenho += 1            # um desenho do schema inteiro em andamento ficou velho
        self._desenhando = False
        if primeira:
            self._pos_todo = guardar.ler_posicoes(self.fonte.porta, self.fonte.banco, self.esquema)
            self._pos_foco = {}
            self.foco = None
            if len(m.tabelas) > LIMITE_FOCO:
                self.foco = self._ordem(m)[0]
        if self.foco not in m.tabelas:
            self.foco = None
        if self.foco:
            # o recorte tem a sua própria posição automática, a partir da origem: com as posições do schema
            # inteiro ele ficaria espalhado por uma grade enorme (e cada seta roteada nela)
            self.vista, self._foco_total = self._recorte(m, self.foco)
            self._pos_foco = self._posicionar(self.vista, self._pos_foco)
            self._foco_base = dict(self._pos_foco)
            self.posicoes = self._pos_foco
        else:
            self.vista = m
            self._pos_todo = self._posicionar(m, self._pos_todo)
            self.posicoes = self._pos_todo
        self._lista_avisos = checagem.avisos_do_modelo(self.vista)
        self.avisos = self._avisos(self.vista, self._lista_avisos)
        self._versao += 1
        if self.selecao and not self._existe(self.selecao):
            self.selecao = None
        self.query_one(PainelSQL).atualizar(self.rascunho.passos(), self.sessao.falha, self.sessao.mensagem_falha)
        self.redesenhar()
        if primeira and self.vista.tabelas:
            self._rolar_para_caixas()

    def _mudou(self) -> None:
        """Uma edição entrou ou saiu do rascunho: a sessão grava (e esta tela, ouvinte dela, redesenha); a seleção
        fica à vista."""
        self.sessao.mudou()
        self._mostrar_selecao()

    def _depois_de_mudar(self) -> None:
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
    def _recorte(m: _modelo.Modelo, foco: str) -> Tuple[_modelo.Modelo, int]:
        """O foco e as vizinhas a uma ligação — no máximo LIMITE_FOCO tabelas: o foco e as primeiras vizinhas pela
        ordem do nome. Devolve também quantas tabelas a vizinhança tem (o título diz que cortou)."""
        viz = _modelo.recortar(m, foco, 1)
        total = len(viz.tabelas)
        if total <= LIMITE_FOCO:
            return viz, total
        outras = sorted((i for i in viz.tabelas if i != foco), key=lambda i: (viz.tabelas[i].nome, i))
        dentro = {foco, *outras[:LIMITE_FOCO - 1]}
        return _modelo.Modelo(m.esquema, {i: tb for i, tb in viz.tabelas.items() if i in dentro},
                              {i: k for i, k in viz.ligacoes.items() if k.origem in dentro and k.destino in dentro}), total

    @staticmethod
    def _avisos(vista, lista=None) -> Dict[str, str]:
        """Id -> o nível mais alto dos avisos dele. Um aviso de ligação (FK sem índice, FK opcional) vale também
        para a coluna de origem dela: é ali que o ⚠ aparece no desenho."""
        saida: Dict[str, str] = {}
        for a in (checagem.avisos_do_modelo(vista) if lista is None else lista):
            alvos = [a.alvo]
            lig = vista.ligacoes.get(a.alvo)
            if lig is not None and lig.colunas:
                alvos.append(lig.colunas[0])
            for alvo in alvos:
                if alvo not in saida or _NIVEIS[a.nivel] > _NIVEIS[saida[alvo]]:
                    saida[alvo] = a.nivel
        return saida

    def _avisos_da_selecao(self) -> list:
        """Os avisos do que está selecionado: numa tabela, os dela, das colunas e das ligações que saem dela;
        numa coluna, os dela e das ligações que saem dela; numa ligação, os da ligação."""
        sel = self.selecao
        if not sel:
            return []
        saida = []
        for a in self._lista_avisos:
            lig = self.vista.ligacoes.get(a.alvo)
            if sel[0] == "ligacao":
                ok = a.alvo == sel[1]
            elif sel[0] == "tabela":
                tab = self.vista.tabelas.get(sel[1])
                ok = (a.alvo == sel[1] or (tab is not None and tab.coluna(a.alvo) is not None)
                      or (lig is not None and lig.origem == sel[1]))
            else:
                ok = a.alvo == sel[2] or (lig is not None and lig.colunas[:1] == (sel[2],))
            if ok:
                saida.append(a)
        return saida

    def _linha_avisos(self) -> None:
        partes = []
        for a in self._avisos_da_selecao():
            marca, cor = ("!", COR["amarelo"]) if a.nivel != "info" else ("·", COR["cinza"])
            if partes:
                partes.append(("   ", ""))
            partes += [(marca + " ", cor), (a.texto(), cor if a.nivel != "info" else "")]
        self.query_one("#canvas_avisos", Static).update(Text.assemble(*partes, no_wrap=True, overflow="ellipsis"))

    def _existe(self, alvo) -> bool:
        if alvo[0] == "tabela":
            return alvo[1] in self.vista.tabelas
        if alvo[0] == "coluna":
            return alvo[1] in self.vista.tabelas and self.vista.tabelas[alvo[1]].coluna(alvo[2]) is not None
        return alvo[1] in self.vista.ligacoes

    # ── desenhar ────────────────────────────────────────────────────────────

    def redesenhar(self) -> None:
        """Refaz a grade (e as setas) só quando a vista, as posições ou as expandidas mudaram; trocar a seleção
        só repinta por cima da grade guardada."""
        self._ultimo = time.monotonic()
        chave = (self._versao, tuple(sorted(self.posicoes.items())), frozenset(self.expandidas))
        if self._grade is None or self._grade[0] != chave:
            contagem = None
            if self.foco:
                contagem = {i: n for i, n in _modelo.chegando(self.todo).items() if i in self.vista.tabelas}
            pronta, self._grade_pronta = self._grade_pronta, None
            if (pronta is not None and not self.foco and not self.expandidas
                    and pronta[0] == tuple(sorted(self.posicoes.items()))):
                grade = pronta[1]       # a que a thread já desenhou para estas mesmas posições
            else:
                grade = desenho.desenhar_grade(self.vista, self.posicoes, self.avisos, self.expandidas,
                                               contagem=contagem)
            self._grade = (chave, grade)
        self.query_one(Quadro).definir(self._grade[1].com_selecao(self.selecao), self._aviso_vazio())
        self._titulo()
        self._linha_avisos()

    def _aviso_vazio(self) -> str:
        """O que o quadro diz sem tabela nenhuma: lendo, não deu para ler, ou o schema está vazio mesmo."""
        if self._desenhando:
            return t("canvas.desenhando")
        if self.vista.tabelas:
            return ""
        if not self.carregado:
            return t("canvas.sem_leitura") if self._sem_leitura else t("canvas.lendo")
        return t("canvas.vazio")

    def _titulo(self) -> None:
        partes = [("ratt db", f"bold {COR['azul']}"), (" · ", COR["cinza"]), (self.fonte.banco, "bold"),
                  (" › ", COR["cinza"]), (t("canvas.titulo"), ""), (" · ", COR["cinza"]), (self.esquema, ""),
                  (" · ", COR["cinza"]), (t("canvas.rascunho", n=len(self.rascunho.operacoes)), COR["laranja"] if self.rascunho.operacoes else COR["cinza"])]
        if self.foco:
            nome = self.todo.tabelas[self.foco].nome
            texto = (t("canvas.em_foco", tabela=nome) if self._foco_total <= len(self.vista.tabelas)
                     else t("canvas.em_foco_corte", tabela=nome, n=len(self.vista.tabelas), total=self._foco_total))
            partes += [(" · ", COR["cinza"]), (texto, COR["amarelo"])]
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
        """Só as posições do schema inteiro vão ao disco. Em foco, as caixas movidas passam do sistema do recorte
        (que recomeça na origem) para o do schema inteiro: o deslocamento desde que foram postas; as demais
        posições guardadas ficam como estão."""
        if self.foco:
            ordem = sorted(self.posicoes, key=lambda i: i != self.foco)     # a tabela do foco primeiro: é a âncora
            for i in ordem:
                x, y = self.posicoes[i]
                antes = self._foco_base.get(i)
                if antes is None or antes == (x, y):
                    continue
                if i in self._pos_todo:
                    bx, by = self._pos_todo[i]
                    self._pos_todo[i] = (max(MIN_X, bx + x - antes[0]), max(MIN_Y, by + y - antes[1]))
                elif self.foco in self._pos_todo and i != self.foco:
                    # sem posição guardada: relativa à tabela do foco, se ela tem; senão não se grava nada
                    fx, fy = self._pos_todo[self.foco]
                    ax, ay = self.posicoes[self.foco]
                    self._pos_todo[i] = (max(MIN_X, fx + x - ax), max(MIN_Y, fy + y - ay))
                else:
                    continue
                self._foco_base[i] = (x, y)
            dados = self._pos_todo
        else:
            dados = self.posicoes
        try:
            guardar.gravar_posicoes(self.fonte.porta, self.fonte.banco, self.esquema, dados)
        except OSError as erro:         # disco cheio, só leitura: a caixa se move do mesmo jeito; avisa uma vez
            if not self._avisou_posicoes:
                self.notify(t("canvas.erro_posicoes", erro=erro), severity="error")
            self._avisou_posicoes = True

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
            self.foco, self._pos_foco = (exata or achadas)[0], {}
            self.selecao = ("tabela", self.foco)
            self._montar()
            self._rolar_para_caixas()
        self.app.push_screen(PerguntarNome(t("canvas.qual_foco")), nome_dado)

    def action_tudo(self) -> None:
        if not self.foco or self._desenhando:
            return
        if len(self.todo.tabelas) <= self.LIMITE_TUDO:
            self.foco = None
            self._montar()
            return
        self.app.push_screen(
            formularios.Confirmar(t("canvas.tudo_pergunta", n=len(self.todo.tabelas)), [], t("canvas.tudo_sim"), foco="nao"),
            self._tudo_confirmado)

    def _tudo_confirmado(self, sim) -> None:
        if not sim or not self.foco or self._desenhando:
            return
        self._marca_desenho += 1
        self._desenhando = True
        self.redesenhar()
        self.desenhar_tudo(self.esquema, self._marca_desenho, self.todo, dict(self._pos_todo), frozenset(self.expandidas))

    @work(thread=True, exclusive=True, group="desenho_tudo")
    def desenhar_tudo(self, esquema, marca, todo, guardadas, expandidas):
        """O layout e a grade do schema inteiro, fora da thread da tela (centenas de tabelas demoram)."""
        try:
            pos = self._posicionar(todo, guardadas)
            avisos = self._avisos(todo, checagem.avisos_do_modelo(todo))
            grade = desenho.desenhar_grade(todo, pos, avisos, set(expandidas))
        except Exception as erro:
            self.app.call_from_thread(self._tudo_falhou, esquema, marca, erro)
            return
        self.app.call_from_thread(self._tudo_chegou, esquema, marca, guardadas, pos, grade)

    def _tudo_falhou(self, esquema, marca, erro) -> None:
        if not self.is_attached or esquema != self.esquema or marca != self._marca_desenho:
            return
        self._desenhando = False
        self.redesenhar()
        self.notify(t("canvas.e.operacao", erro=erro), severity="error")

    def _tudo_chegou(self, esquema, marca, guardadas, pos, grade) -> None:
        """O resultado só vale se nada mudou desde que o desenho começou (tela, schema, modelo, foco escolhido,
        posições movidas): senão é descartado, sem desenhar nada na thread da tela e sem desfazer o que a pessoa
        fez depois. Quem fez a vista ficar velha já tirou o "desenhando…"."""
        if not self.is_attached or esquema != self.esquema or marca != self._marca_desenho or not self._desenhando:
            return
        self._desenhando = False
        if not self.foco or self._pos_todo != guardadas:
            self.redesenhar()           # outra caixa foi movida no meio: a grade pronta está velha; o foco segue
            return
        self.foco = None
        self._pos_todo = pos
        self._grade_pronta = (tuple(sorted(pos.items())), grade)
        self._montar()

    def action_expandir(self) -> None:
        """`+` abre (ou recolhe de novo) a tabela selecionada que está recolhida com `←N`."""
        if not self.selecao or self.selecao[0] not in ("tabela", "coluna"):
            self.notify(t("canvas.sel_tabela_ou_coluna"), severity="warning")
            return
        self.expandidas ^= {self.selecao[1]}
        self.redesenhar()

    def action_dados(self) -> None:
        """`v`: a tela Dados da tabela selecionada (a criada no rascunho também abre: a grade espera o aplicar)."""
        if not self.carregado:
            self.notify(t("canvas.carregando"))
            return
        if not self.selecao or self.selecao[0] not in ("tabela", "coluna"):
            self.notify(t("canvas.sel_tabela_ou_coluna"), severity="warning")
            return
        if self.selecao[1] not in self.todo.tabelas:
            self.notify(t("canvas.e.sumiu"), severity="warning")
            return
        from ratt.db import tela_dados
        outra = getattr(self.fonte, "outra", None)
        self.app.push_screen(tela_dados.TelaDados(outra() if callable(outra) else self.fonte, self.esquema,
                                                  self.selecao[1]))

    # ── editar ──────────────────────────────────────────────────────────────

    def _pronto(self) -> bool:
        if not self._livre():
            return False
        if not self.carregado:
            self.notify(t("canvas.carregando"))
        return self.carregado

    def _abrir(self, form) -> None:
        self.app.push_screen(form, lambda op: self._depois_de_editar(op, form))

    def _depois_de_editar(self, op, form=None) -> None:
        if op is None:
            return
        if isinstance(op, _rascunho.ApagarTabela) and getattr(form, "contagem", None) is not None:
            self._contagens[op.tabela] = form.contagem
        criada = op.tabela.id if isinstance(op, _rascunho.CriarTabela) else op.tabela_id if isinstance(op, _rascunho.LigarNN) else None
        if criada:
            self.selecao = ("tabela", criada)
            if self.foco:               # em foco, a tabela nova (ainda sem ligação) ficaria fora do recorte
                self.foco, self._pos_foco = criada, {}
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
            itens = [("renomear", t("canvas.m.renomear"))]
            # só o que o PostgreSQL aceita: coluna da PK não fica opcional; a unicidade só sai se é uma restrição
            # UNIQUE com nome (um índice único não sai com DROP CONSTRAINT)
            if not c.obrigatoria:
                itens.append(("obrigatoria", t("canvas.m.obrigatoria")))
            elif not c.pk:
                itens.append(("opcional", t("canvas.m.opcional")))
            if not c.unica:
                itens.append(("unica", t("canvas.m.unica")))
            elif c.nome_unica:
                itens.append(("nao_unica", t("canvas.m.nao_unica")))
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
            def renomear(novo):
                """Nome livre: nem o mesmo, nem um já usado (no schema, para tabela; na tabela, para coluna)."""
                v = r.vista()
                if cid is None:
                    usados = {tb.nome for i, tb in v.tabelas.items() if tb.estado != "apagado" and i != tid}
                    igual = v.tabelas[tid].nome == novo
                else:
                    usados = {c.nome for c in v.tabelas[tid].colunas if c.estado != "apagado" and c.id != cid}
                    igual = v.tabelas[tid].coluna(cid).nome == novo
                if igual:
                    raise ValueError(t("canvas.e.nome_igual"))
                if novo in usados:
                    raise ValueError(t("canvas.e.nome_existe", nome=novo))
                return _rascunho.Renomear(tid, cid, novo)
            form = formularios.FormSimples(r, f, t("canvas.f.renomear", nome=nome), renomear, valor=atual)
        else:
            sim = chave in ("obrigatoria", "unica")
            classe = _rascunho.Obrigatoria if chave in ("obrigatoria", "opcional") else _rascunho.Unica
            form = formularios.FormSimples(r, f, t("canvas.f." + chave, nome=nome),
                                           lambda _: classe(tid, cid, sim))
        self._abrir(form)

    # ── depois de aplicar ───────────────────────────────────────────────────

    def depois_de_aplicar(self, esquema: str, nomes: Dict[str, str]) -> None:
        """O banco gravou e a sessão já esvaziou o rascunho: traz o catálogo novo (as posições das tabelas criadas
        vão para os ids dele pelo nome)."""
        self.selecao = None
        self.redesenhar()
        self._relendo = self.sessao
        self._reler_aplicado(esquema, nomes)

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
        s, self._relendo = self._relendo, None
        if s is not None:           # None: a tela saiu e já soltou a sessão (que pode até estar aplicando de novo)
            s.aplicando = False
        mesma = self.is_attached and esquema == self.esquema
        if m is None:
            if mesma:
                self.marca = None               # o vigia traz o catálogo de verdade na próxima volta
                self._falhou(esquema, erro)
            return
        por_nome = {tab.nome: tab.id for tab in m.tabelas.values()}
        posicoes = self._pos_todo if mesma else guardar.ler_posicoes(self.fonte.porta, self.fonte.banco, esquema)
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
        self._pos_todo = posicoes
        self._calada((s or self.sessao).aplicado, m)
        self._montar()

    # ── ler o banco (sempre fora da thread da tela) ─────────────────────────

    @work(thread=True, exclusive=True, group="vigia_canvas")
    def ler(self, esquema: str, so_se_mudou: bool = False):
        """Lê o modelo de `esquema`. Se a tela já trocou de schema ou saiu quando a resposta chega, ela é descartada."""
        primeira = not self.carregado
        try:
            marca = self.fonte.assinatura()
            if so_se_mudou and marca == self.marca:
                return
            m = self.fonte.modelo(esquema)
        except Exception as erro:
            if primeira:                # o poller falha em silêncio; quem abre ou troca de schema precisa saber
                self.app.call_from_thread(self._nao_leu, esquema, erro)
            return
        self.app.call_from_thread(self._chegou, esquema, marca, m)

    def vigiar(self):
        if self.carregado:
            self.ler(self.esquema, so_se_mudou=True)
        elif self._sem_leitura and not self._tentando:     # a primeira leitura falhou: tenta de novo
            self._tentando = True
            self.ler(self.esquema)

    def _nao_leu(self, esquema, erro) -> None:
        """A leitura que abriria o schema falhou: o quadro diz isso (não "schema vazio"), o aviso sai uma vez só
        e o vigia segue tentando."""
        if not self.is_attached or esquema != self.esquema or self.carregado:
            return
        self._tentando = False
        if not self._sem_leitura:
            self._falhou(esquema, erro)
        self._sem_leitura = True
        self.redesenhar()

    def _chegou(self, esquema, marca, m) -> None:
        if not self.is_attached or esquema != self.esquema or self.sessao.aplicando:
            return
        self._sem_leitura = self._tentando = False
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
        self._desenhando, self._grade_pronta = False, None
        self._marca_desenho += 1
        self.posicoes, self.carregado, self.marca = {}, False, None
        self._pos_todo, self._pos_foco, self.expandidas = {}, {}, set()
        self._sem_leitura = self._tentando = False
        self.todo = self.vista = _modelo.Modelo(nome)
        self._lista_avisos, self.avisos = [], {}
        self._versao += 1
        self._ligar_sessao(nome)
        self.query_one(PainelSQL).atualizar([])
        self.redesenhar()
        self.ler(nome)
