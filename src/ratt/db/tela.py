"""ratt db, sem comando: a tela do banco. A árvore à esquerda, o objeto escolhido à direita.

    ▾ lhv_estudos              public.alunos · tabela · 454 linhas · 136 kB
      ▾ public                 [Dados] Estrutura  Relações  SQL
        ▾ Tabelas (3)          id     nome     nota      curso_id
          › alunos   454       int4   text     numeric   int4
          › cursos     2       1      Ana         8.5    1
        › Views (1)            …

A tela olha o banco a cada dois segundos: quando um ratt db run (ou o psql) cria uma tabela ou
muda dados, ela se atualiza sozinha. Deixe aberta ao lado do VS Code.
"""
import os
import subprocess

from rich.syntax import Syntax
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import DataTable, Footer, OptionList, Static, TabbedContent, TabPane, Tree
from textual.widgets.option_list import Option

from ratt.db import acesso, catalogo, config, rodar
from ratt.db.idioma import t

POR_PAGINA = 200
LARGURA_CELULA = 60

# as cores do ratt (as mesmas do terminal), como tema da tela
TEMA = Theme(name="ratt", primary="#4aa3ef", secondary="#7d8592", accent="#f0b43c", warning="#f0b43c",
             error="#f08a3c", success="#6ec88c", dark=True)
COR = {"laranja": "#f08a3c", "amarelo": "#f0b43c", "cinza": "#7d8592", "verde": "#6ec88c", "azul": "#4aa3ef"}

_GRUPOS = (("tabelas", "tui.tabelas"), ("views", "tui.views"), ("funcoes", "tui.funcoes"), ("sequencias", "tui.sequencias"))


def _celula(valor, tipo):
    texto = rodar._cortar(rodar._formatar(valor), LARGURA_CELULA)
    if valor is None:
        return Text(texto, style=COR["laranja"])
    if isinstance(valor, str) and (valor == "" or valor != valor.strip()):
        return Text(texto, style=COR["amarelo"])
    return Text(texto, justify="right" if rodar._numerico(tipo) else "left")


class EscolherBanco(ModalScreen):
    """A lista dos bancos, para trocar sem sair da tela."""

    BINDINGS = [Binding("escape", "dismiss(None)", t("tui.voltar"))]
    DEFAULT_CSS = """
    EscolherBanco { align: center middle; }
    EscolherBanco > Vertical { width: 64; height: auto; max-height: 80%; border: round $primary; background: $surface; padding: 1 2; }
    EscolherBanco OptionList { height: auto; max-height: 20; border: none; }
    """

    def __init__(self, bancos, atual):
        super().__init__()
        self.bancos, self.atual = bancos, atual

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(t("tui.qual_banco"), style="bold"))
            opcoes = []
            for nome, dono, tamanho in self.bancos:
                rotulo = Text.assemble((nome, "bold"), (f"   {t('s.banco', dono=dono)} · {acesso.tamanho(tamanho)}", COR["cinza"]))
                if nome == self.atual:
                    rotulo.append(f"   ● {t('g.atual')}", style=COR["verde"])
                opcoes.append(Option(rotulo, id=nome))
            yield OptionList(*opcoes)

    def on_mount(self):
        lista = self.query_one(OptionList)
        nomes = [n for n, _, _ in self.bancos]
        if self.atual in nomes:
            lista.highlighted = nomes.index(self.atual)

    def on_option_list_option_selected(self, evento):
        self.dismiss(evento.option.id)


class TelaBanco(App):
    TITLE = "ratt db"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    #titulo { height: 1; padding: 0 1; background: $panel; }
    #corpo { height: 1fr; }
    #arvore { width: 38; border-right: solid $panel; padding-right: 1; }
    #painel { width: 1fr; }
    #info { height: auto; max-height: 3; padding: 0 1; margin-bottom: 1; }
    #abas, #abas > ContentSwitcher, #abas TabPane { height: 1fr; }
    #tabela_dados { height: 1fr; }
    #avisos { height: auto; max-height: 8; padding: 0 1; }
    #tabela_colunas { height: auto; max-height: 60%; }
    #detalhes, #texto_relacoes, #texto_sql { padding: 1 1; }
    """
    BINDINGS = [
        Binding("q", "quit", t("tui.sair")),
        Binding("r", "recarregar", t("tui.recarregar")),
        Binding("p", "psql", "psql"),
        Binding("b", "trocar_banco", t("tui.banco")),
        Binding("n", "pagina(1)", t("tui.proxima")),
        Binding("N,shift+n", "pagina(-1)", t("tui.anterior")),
        Binding("1", "aba('dados')", t("tui.dados"), show=False),
        Binding("2", "aba('estrutura')", t("tui.estrutura"), show=False),
        Binding("3", "aba('relacoes')", t("tui.relacoes"), show=False),
        Binding("4", "aba('sql')", "SQL", show=False),
    ]

    def __init__(self, porta, usuario, banco):
        super().__init__()
        self.porta, self.usuario, self.banco = porta, usuario, banco
        self.conexao, self.como = None, None
        self.atual = None               # o Objeto aberto à direita
        self.total = None               # quantas linhas ele tem
        self.pagina = 0
        self.marca = None               # a assinatura do banco na última olhada

    # ── montar ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static(id="titulo")
        with Horizontal(id="corpo"):
            yield Tree("", id="arvore")
            with Vertical(id="painel"):
                yield Static(id="info")
                with TabbedContent(id="abas"):
                    with TabPane(t("tui.dados"), id="dados"):
                        yield DataTable(id="tabela_dados", header_height=2, zebra_stripes=True, cursor_type="row")
                        yield Static(id="avisos")
                    with TabPane(t("tui.estrutura"), id="estrutura"):
                        with VerticalScroll():
                            yield DataTable(id="tabela_colunas", cursor_type="row")
                            yield Static(id="detalhes")
                    with TabPane(t("tui.relacoes"), id="relacoes"):
                        yield VerticalScroll(Static(id="texto_relacoes"))
                    with TabPane("SQL", id="sql"):
                        yield VerticalScroll(Static(id="texto_sql"))
        yield Footer()

    def on_mount(self):
        self.register_theme(TEMA)
        self.theme = "ratt"
        self.query_one("#arvore", Tree).show_root = True
        if self.banco:
            self.conectar(self.banco)
        else:
            self.action_trocar_banco()
        self.set_interval(2.0, self.vigiar)

    def conectar(self, banco):
        if self.conexao:
            self.conexao.close()
        self.conexao, self.como = acesso.entrar(self.porta, self.usuario, banco)
        if not self.conexao:
            self.notify(t("run.sem_acesso", usuario=self.usuario, banco=banco), severity="error")
            return
        self.banco, self.atual, self.marca = banco, None, None
        self.query_one("#arvore", Tree).clear()      # árvore vazia: a próxima montagem é a primeira
        self.limpar()
        lembrado = config.ler().get("db") or {}
        config.gravar(db=dict(lembrado, banco=banco))
        self.query_one("#titulo", Static).update(Text.assemble(
            ("ratt db", f"bold {COR['azul']}"), ("  ·  ", COR["cinza"]), (banco, "bold"),
            (f" @ localhost:{self.porta}  ·  {self.usuario}", COR["cinza"])))
        self.query_one("#info", Static).update("")
        self.vigiar()

    # ── a árvore ────────────────────────────────────────────────────────────

    def montar_arvore(self, esquemas):
        arvore = self.query_one("#arvore", Tree)
        abertos = {no.data for no in self._nos(arvore.root) if no.is_expanded and isinstance(no.data, tuple)}
        primeira_vez = not arvore.root.children
        arvore.clear()
        arvore.root.set_label(Text(self.banco, style="bold"))
        arvore.root.data = ("banco",)
        arvore.root.expand()
        alvo = None
        for esquema in esquemas:
            chave = ("esquema", esquema.nome)
            no_e = arvore.root.add(Text(esquema.nome), data=chave,
                                   expand=chave in abertos or (primeira_vez and esquema.nome == "public") or len(esquemas) == 1)
            for grupo, rotulo in _GRUPOS:
                itens = getattr(esquema, grupo)
                chave_g = ("grupo", esquema.nome, grupo)
                no_g = no_e.add(Text.assemble(t(rotulo), (f" ({len(itens)})", COR["cinza"])), data=chave_g,
                                expand=chave_g in abertos or (primeira_vez and grupo == "tabelas"), allow_expand=bool(itens))
                for objeto in itens:
                    rotulo_o = Text(objeto.nome)
                    if objeto.tipo == "tabela" and objeto.linhas is not None:
                        rotulo_o.append(f"  {objeto.linhas}", style=COR["cinza"])
                    elif objeto.tipo == "funcao":
                        rotulo_o.append(f"({objeto.argumentos})", style=COR["cinza"])
                    no = no_g.add_leaf(rotulo_o, data=objeto)
                    if self.atual and (objeto.esquema, objeto.nome, objeto.tipo) == \
                            (self.atual.esquema, self.atual.nome, self.atual.tipo):
                        alvo = no
        if self.atual and not alvo:          # o objeto aberto sumiu (um DROP TABLE, por exemplo)
            self.atual = None
            self.limpar()
        # o cursor só anda depois que a árvore nova foi desenhada
        if alvo:
            self.call_after_refresh(arvore.move_cursor, alvo)
        elif primeira_vez or not self.atual:
            primeira = next((e.tabelas[0] for e in esquemas if e.tabelas), None)
            no = next((n for n in self._nos(arvore.root) if n.data is primeira), None) if primeira else None
            if no:
                self.call_after_refresh(arvore.move_cursor, no)
            else:
                self.mostrar_vazio()

    def _nos(self, no):
        yield no
        for filho in no.children:
            yield from self._nos(filho)

    def on_tree_node_highlighted(self, evento):
        dado = evento.node.data
        if isinstance(dado, catalogo.Objeto):
            if self.atual is not dado:
                self.atual, self.pagina = dado, 0
                self.carregar(dado, 0)

    def mostrar_vazio(self):
        self.limpar()
        self.query_one("#info", Static).update(Text(t("tui.vazio"), style=COR["cinza"]))

    def limpar(self):
        """O painel da direita em branco: nada aberto."""
        for id_ in ("#tabela_dados", "#tabela_colunas"):
            tabela = self.query_one(id_, DataTable)
            tabela.clear(columns=True)
            tabela.display = False          # sem colunas, sobraria só uma faixa de cabeçalho vazia
        for id_ in ("#info", "#avisos", "#detalhes", "#texto_relacoes", "#texto_sql"):
            self.query_one(id_, Static).update("")

    # ── o objeto à direita ──────────────────────────────────────────────────

    @work(thread=True, exclusive=True, group="objeto")
    def carregar(self, objeto, pagina):
        pacote = {"objeto": objeto, "pagina": pagina}
        try:
            if objeto.tipo in ("tabela", "view"):
                pacote["total"] = catalogo.contar(self.conexao, objeto)
                pacote["dados"] = catalogo.dados(self.conexao, objeto, pagina * POR_PAGINA, POR_PAGINA)
                pacote["colunas"] = catalogo.colunas(self.conexao, objeto.oid)
            if objeto.tipo == "tabela":
                pacote["tamanho"] = catalogo.tamanho(self.conexao, objeto.oid)
                pacote["indices"] = catalogo.indices(self.conexao, objeto.oid)
                pacote["restricoes"] = catalogo.restricoes(self.conexao, objeto.oid)
                pacote["relacoes"] = catalogo.relacoes(self.conexao, objeto.oid)
            pacote["ddl"] = catalogo.ddl(self.conexao, objeto)
        except Exception as erro:         # o banco mudou no meio (a tabela sumiu, por exemplo)
            pacote["erro"] = str(erro).strip().splitlines()[0]
        self.call_from_thread(self.mostrar, pacote)

    def mostrar(self, pacote):
        objeto = pacote["objeto"]
        if objeto is not self.atual:
            return
        if "erro" in pacote:
            self.query_one("#info", Static).update(Text(pacote["erro"], style=COR["laranja"]))
            return
        self.total = pacote.get("total")
        self._mostrar_info(objeto, pacote)
        self._mostrar_dados(pacote)
        self._mostrar_estrutura(pacote)
        self._mostrar_relacoes(objeto, pacote)
        self.query_one("#texto_sql", Static).update(
            Syntax(pacote["ddl"] or "", "sql", theme="ansi_dark", word_wrap=True, background_color="default"))
        abas = self.query_one("#abas", TabbedContent)
        if objeto.tipo in ("funcao", "sequencia"):
            abas.active = "sql"
        elif objeto.tipo == "view" and abas.active == "relacoes":
            abas.active = "dados"

    def _mostrar_info(self, objeto, pacote):
        partes = [(f"{objeto.esquema}.{objeto.nome}", "bold"), ("  ·  ", COR["cinza"]), (t(f"tui.tipo.{objeto.tipo}"), "")]
        if self.total is not None:
            partes += [("  ·  ", COR["cinza"]), (t("tui.linhas", n=f"{self.total:n}"), "")]
            if self.total > POR_PAGINA:
                inicio = pacote["pagina"] * POR_PAGINA + 1
                fim = min(inicio + POR_PAGINA - 1, self.total)
                partes += [(f"  ({t('tui.mostrando', inicio=inicio, fim=fim)} · n/N)", COR["cinza"])]
        if pacote.get("tamanho") is not None:
            partes += [("  ·  ", COR["cinza"]), (acesso.tamanho(pacote["tamanho"]), "")]
        self.query_one("#info", Static).update(Text.assemble(*partes))

    def _mostrar_dados(self, pacote):
        tabela = self.query_one("#tabela_dados", DataTable)
        avisos = self.query_one("#avisos", Static)
        tabela.clear(columns=True)
        if "dados" not in pacote:
            avisos.update(Text(t("tui.sem_dados"), style=COR["cinza"]))
            return
        nomes, tipos, linhas = pacote["dados"]
        tabela.display = True
        for nome, tipo in zip(nomes, tipos):
            tabela.add_column(Text.assemble((nome, "bold"), "\n", (tipo, COR["cinza"])))
        for linha in linhas:
            tabela.add_row(*(_celula(v, tipo) for v, tipo in zip(linha, tipos)))
        achados = rodar.avisos(nomes, linhas)
        if not linhas:
            avisos.update(Text(t("run.nenhuma_linha"), style=COR["cinza"]))
        else:
            avisos.update(Text("\n").join(Text(f"{marca} {texto}", style=COR[cor]) for marca, cor, texto in achados))

    def _mostrar_estrutura(self, pacote):
        tabela = self.query_one("#tabela_colunas", DataTable)
        tabela.clear(columns=True)
        detalhes = self.query_one("#detalhes", Static)
        if "colunas" not in pacote:
            detalhes.update(Text(t("tui.sem_estrutura"), style=COR["cinza"]))
            return
        tabela.display = True
        for chave in ("tui.c.num", "tui.c.coluna", "tui.c.tipo", "tui.c.vazio", "tui.c.padrao", "tui.c.chave"):
            tabela.add_column(Text(t(chave), style="bold"))
        for col in pacote["colunas"]:
            chave = Text("PK", style=f"bold {COR['amarelo']}") if col.chave else \
                Text(f"→ {col.aponta}", style=COR["azul"]) if col.aponta else Text("")
            tabela.add_row(Text(str(col.numero), style=COR["cinza"]), Text(col.nome, style="bold"), col.tipo,
                           Text(t("tui.nao"), style=COR["cinza"]) if col.obrigatoria else t("tui.pode"),
                           Text(col.padrao or "", style=COR["cinza"]), chave)
        texto = Text()
        if pacote.get("indices"):
            texto.append(t("tui.indices") + "\n", style="bold")
            for nome, definicao in pacote["indices"]:
                texto.append(f"  {nome}  ", style="bold").append(definicao.split(" USING ")[-1] + "\n", style=COR["cinza"])
        if pacote.get("restricoes"):
            texto.append("\n" + t("tui.restricoes") + "\n", style="bold")
            for nome, tipo, definicao in pacote["restricoes"]:
                texto.append(f"  {nome}  ", style="bold").append(definicao + "\n", style=COR["cinza"])
        detalhes.update(texto)

    def _mostrar_relacoes(self, objeto, pacote):
        alvo = self.query_one("#texto_relacoes", Static)
        if "relacoes" not in pacote:
            alvo.update(Text(t("tui.sem_relacoes_tipo"), style=COR["cinza"]))
            return
        saem, chegam = pacote["relacoes"]
        texto = Text()
        for titulo, lista in ((t("tui.saem"), saem), (t("tui.chegam"), chegam)):
            texto.append(titulo + "\n", style="bold")
            if not lista:
                texto.append(f"  {t('tui.nenhuma')}\n", style=COR["cinza"])
            for tabela, cols, alvo_t, alvo_cols in lista:
                texto.append(f"  {tabela}", style="bold").append(f".{cols}").append("  →  ", style=COR["azul"]) \
                     .append(alvo_t, style="bold").append(f".{alvo_cols}\n")
            texto.append("\n")
        alvo.update(texto)

    # ── olhar o banco de tempos em tempos ───────────────────────────────────

    @work(thread=True, exclusive=True, group="vigia")
    def vigiar(self):
        if not self.conexao:
            return
        try:
            marca = catalogo.assinatura(self.conexao)
            if marca == self.marca:
                return
            esquemas = catalogo.esquemas(self.conexao)
        except Exception:
            return
        avisar = self.marca is not None
        self.marca = marca
        self.call_from_thread(self._atualizar, esquemas, avisar)

    def _atualizar(self, esquemas, avisar):
        self.montar_arvore(esquemas)
        if self.atual:
            self.carregar(self.atual, self.pagina)
        if avisar:
            self.notify(t("tui.mudou"), timeout=3)

    # ── as teclas ───────────────────────────────────────────────────────────

    def action_recarregar(self):
        self.marca = None
        self.vigiar()

    def action_pagina(self, direcao):
        if not self.atual or self.total is None:
            return
        nova = self.pagina + direcao
        if 0 <= nova * POR_PAGINA < max(self.total, 1):
            self.pagina = nova
            self.carregar(self.atual, nova)

    def action_aba(self, aba):
        self.query_one("#abas", TabbedContent).active = aba

    def action_psql(self):
        cmd = ["psql"] + (["-h", "localhost"] if self.como == "s.como_pgpass" else [])
        cmd += (["-p", str(self.porta)] if self.porta != 5432 else []) + ["-U", self.usuario, self.banco]
        with self.suspend():
            print(f"$ {' '.join(cmd)}   ({t('tui.volta_com_q')})", flush=True)
            subprocess.run(cmd)
        self.action_recarregar()

    def action_trocar_banco(self):
        conexao, _ = acesso.entrar(self.porta, self.usuario)
        if not conexao:
            self.notify(t("g.sem_acesso", usuario=self.usuario), severity="error")
            return
        with conexao:
            bancos = acesso.bancos(conexao)
        if not bancos:
            self.notify(t("g.nenhum_banco"), severity="warning")
            return

        def escolhido(nome):
            if nome and nome != self.banco:
                self.conectar(nome)
            elif not self.banco:
                self.exit()
        self.push_screen(EscolherBanco(bancos, self.banco), escolhido)

    def on_unmount(self):
        if self.conexao:
            self.conexao.close()


def abrir(op):
    """Abre a tela no banco atual (ou --db). Sem nenhum banco, diz como criar um."""
    import getpass
    lembrado = config.ler().get("db") or {}
    porta = int(os.environ.get("PGPORT") or lembrado.get("porta") or 5432)
    usuario = op.get("user") or lembrado.get("usuario") or getpass.getuser()
    banco = op.get("db") or lembrado.get("banco")
    conexao, _ = acesso.entrar(porta, usuario, banco or "postgres")
    if not conexao:
        print(t("g.sem_acesso", usuario=usuario))
        return 1
    with conexao:
        bancos = acesso.bancos(conexao)
    if not bancos and not op.get("db"):
        print(t("g.nenhum_banco"))
        return 1
    if banco and banco not in [n for n, _, _ in bancos] and banco != "postgres":
        banco = None
    TelaBanco(porta, usuario, banco).run()
    return 0
