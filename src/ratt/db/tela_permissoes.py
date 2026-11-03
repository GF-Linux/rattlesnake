"""A tela Permissões: quem pode o quê em cada tabela de um schema, e o rascunho do schema (o mesmo do canvas e da
tela Dados).

    ratt db · estudos › public › Permissões · rascunho: 2
    schema public: PUBLIC USAGE · app USAGE · leitura USAGE
    papel          itens        pedidos      pessoas
    PUBLIC         ·······      ·······      ·······
    app            SIUD···      SIUD···      SIUD···
    jared ●        dono         dono         dono
    leitura        S······      S······      S······
    ┌ SQL ─────────────────────────────────────────────────────────────────────┐

Cada célula mostra os 7 privilégios de tabela por letra (`S`elect `I`nsert `U`pdate `D`elete `T`runcate
`R`eferences `t`rigger); o dono da tabela aparece como `dono`. A grade é o catálogo (`catalogo.permissoes`) com o
rascunho por cima (as operações de permissão não mudam a `vista()`: a tela soma os GRANT e tira os REVOKE, na ordem):
uma letra que o rascunho põe fica amarela, uma que ele tira vira `·` laranja. Linhas: o `PUBLIC` (fixo no alto) e os
papéis que já existem no servidor (sem os `pg_*`); um papel criado no rascunho do servidor só aparece depois de
aplicado. Colunas: as tabelas comuns e particionadas (as que `catalogo.permissoes` lê; nunca views nem estrangeiras).

Seleção: a célula do cursor; `l` (ou shift+clique, ou clique no nome) a linha; `k` (ou clique no cabeçalho) a
coluna; `*` todas — sempre a lista explícita das tabelas da grade, nunca `ALL TABLES`. `espaço` abre o formulário
de privilégios; `s`, o USAGE/CREATE do papel da linha no schema.

Toda leitura roda numa thread, pela `fonte` (`canvas.FonteBanco`, ou uma falsa nos testes); a vigia olha a
assinatura do schema COM as permissões (`assinatura_permissoes`: um GRANT dado pelo psql relê a grade).
"""
from __future__ import annotations

import threading
from typing import Dict, FrozenSet, List, Optional, Tuple

from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from ratt.db import catalogo, checagem, rascunho as _rascunho, sessao as _sessao
from ratt.db.formularios_usuarios import FormPrivilegios, FormSchema, nome_papel, texto_avisos
from ratt.db.grade_dados import Grade
from ratt.db.idioma import t
from ratt.db.painel import AcoesRascunho, PainelSQL
from ratt.db.rascunho import PRIVILEGIOS, PUBLIC
from ratt.db.tela import COR

_SELECAO = "on #1f3550"             # o fundo das células selecionadas (o mesmo da seleção do canvas)
_NO_SCHEMA = ("USAGE", "CREATE")


# ── o catálogo com o rascunho por cima (puro) ──

def _mudar(d: dict, chave, letras: FrozenSet[str], tirar: bool) -> None:
    atual = d.get(chave, frozenset())
    novo = atual - letras if tirar else atual | letras
    if novo:
        d[chave] = frozenset(novo)
    else:
        d.pop(chave, None)


def efetivas(perms: catalogo.Permissoes, r: _rascunho.Rascunho) -> catalogo.Permissoes:
    """As permissões como ficariam: as lidas do catálogo mais as operações VÁLIDAS do rascunho, na ordem (Conceder
    soma letras, Revogar tira; "futuras" mexe só nas permissões padrão, nunca numa célula; PermissaoNoSchema muda o
    USAGE/CREATE). `perms` não muda."""
    por_tabela, schema, padrao = dict(perms.por_tabela), dict(perms.schema), dict(perms.padrao)
    for op, valida, _, _ in r.passos():
        if not valida:
            continue
        if isinstance(op, _rascunho.Conceder):          # Revogar é um Conceder com o outro verbo
            tirar = isinstance(op, _rascunho.Revogar)
            letras = frozenset(op.privilegios)
            for papel in dict.fromkeys(op.papeis):
                for tabela in dict.fromkeys(op.tabelas):
                    _mudar(por_tabela, (papel, tabela), letras, tirar)
                if op.futuras:
                    _mudar(padrao, papel, letras, tirar)
        elif isinstance(op, _rascunho.PermissaoNoSchema):
            for nome, valor in (("USAGE", op.usage), ("CREATE", op.create)):
                if valor is not None:
                    _mudar(schema, op.papel, frozenset({nome}), not valor)
    return catalogo.Permissoes(por_tabela, dict(perms.dono), schema, padrao)


def celula(antes: FrozenSet[str], agora: FrozenSet[str], dono: bool, selecionada: bool = False) -> Text:
    """As 7 letras (`·` onde falta), com o que o rascunho muda marcado; `dono` na tabela do papel."""
    if dono:
        texto = Text(t("perm.dono"), style=f"bold {COR['cinza']}")
    else:
        texto = Text()
        for letra in PRIVILEGIOS:
            tem, tinha = letra in agora, letra in antes
            if tem and tinha:
                texto.append(letra)
            elif tem:
                texto.append(letra, style=f"bold {COR['amarelo']}")
            elif tinha:
                texto.append("·", style=f"bold {COR['laranja']}")
            else:
                texto.append("·", style=COR["cinza"])
    if selecionada:
        texto.stylize(_SELECAO)
    return texto


class _Avisos:
    """O que os formulários pedem para contar os avisos (`fonte.avisos(op, rascunho)`): a checagem do schema pela
    conexão da tela, sob a trava dela (a vigia usa a mesma conexão)."""

    def __init__(self, fonte, trava: threading.Lock):
        self.fonte, self.trava = fonte, trava

    def avisos(self, op, r):
        with self.trava:
            return checagem.avisos_da_operacao(op, r, self.fonte.consulta())


class GradePermissoes(Grade):
    """A grade, lembrando se o último clique veio com shift (shift+clique seleciona a linha)."""

    shift = False

    def _on_click(self, evento: events.Click) -> None:
        self.shift = bool(getattr(evento, "shift", False))
        super()._on_click(evento)


class TelaPermissoes(AcoesRascunho, Screen):
    BINDINGS = [
        Binding("space", "privilegios", t("perm.k.privilegios")),
        Binding("s", "schema", t("perm.k.schema")),
        Binding("l", "linha", t("perm.k.linha")),
        Binding("k", "coluna", t("perm.k.coluna")),
        Binding("asterisk", "todas", t("perm.k.todas")),
        Binding("enter", "aplicar", t("canvas.k.aplicar")),
        Binding("e", "ensaio", t("canvas.k.ensaio")),
        Binding("w", "exportar", t("canvas.k.exportar")),
        Binding("u", "desfazer", t("canvas.k.desfazer")),
        Binding("r", "painel", t("canvas.k.rascunho")),
        Binding("escape,q", "voltar", t("canvas.k.voltar")),     # com uma seleção, escape a desfaz primeiro
    ]
    VIGIA_SEGUNDOS = 2.0                    # de quanto em quanto tempo a vigia olha o banco (0: só à mão)
    DEFAULT_CSS = """
    #perm_titulo { height: 1; padding: 0 1; background: $panel; }
    #perm_schema { height: auto; max-height: 2; padding: 0 1; }
    #perm_grade { height: 1fr; }
    #perm_avisos { height: auto; max-height: 3; padding: 0 1; }
    """

    def __init__(self, fonte, esquema: str):
        super().__init__()
        self.fonte, self.esquema = fonte, esquema
        self.sessao: Optional[_sessao.SessaoRascunho] = None
        self.carregado = False
        self.permissoes: Optional[catalogo.Permissoes] = None   # o catálogo, na última leitura
        self.selecao: Optional[Tuple[Tuple[str, ...], Tuple[str, ...]]] = None     # (papéis, tabelas)
        self._linhas: List[str] = []        # o papel de cada linha da grade (PUBLIC na primeira)
        self._tabelas: List[str] = []       # a tabela de cada coluna, a partir da segunda
        self._estrutura = None              # (linhas, tabelas, rótulos): igual, a grade só troca as células
        self._avisos_op: list = []          # os avisos da última operação posta por esta tela
        self._marca = None                  # a assinatura (com as permissões) na última leitura
        self._trava = threading.Lock()      # uma conversa com o banco por vez nesta tela
        self.fonte_avisos = _Avisos(fonte, self._trava)
        self._olhando = False               # uma olhada da vigia está no ar (posto e tirado na thread da tela)
        self._olhada = 0
        self._avisou_erro = False
        self._calado = False
        self._relendo: Optional[_sessao.SessaoRascunho] = None

    def compose(self) -> ComposeResult:
        yield Static(id="perm_titulo")
        yield Static(id="perm_schema")
        # a linha fixa (PUBLIC) só depois que ela existe: o DataTable não desenha uma linha fixa que não há
        yield GradePermissoes(id="perm_grade", cursor_type="cell", fixed_columns=1, zebra_stripes=True)
        yield Static(id="perm_avisos")
        yield PainelSQL(id="perm_sql")
        yield Footer()

    def on_mount(self) -> None:
        self.sessao = _sessao.obter(self.app, self.fonte, self.esquema)
        self.sessao.ouvir(self._rascunho_mudou)
        self._montar()
        self._comecar_olhada(True)
        if self.VIGIA_SEGUNDOS:
            self.set_interval(self.VIGIA_SEGUNDOS, self.vigiar)

    def on_unmount(self) -> None:
        self.sessao.parar(self._rascunho_mudou)
        if self._relendo is not None:
            self._relendo.aplicando, self._relendo = False, None
        fechar = getattr(self.fonte, "fechar", None)
        if fechar:
            fechar()

    def _de_volta(self, fn, *args) -> None:
        try:
            self.app.call_from_thread(fn, *args)
        except RuntimeError:                # a app já fechou
            pass

    def _calada(self, fn, *args):
        self._calado = True
        try:
            return fn(*args)
        finally:
            self._calado = False

    def _rascunho_mudou(self) -> None:
        if self.is_attached and self.carregado and not self._calado:
            self._montar()

    def on_screen_resume(self) -> None:
        if not self.carregado or self.sessao.aplicando:
            return
        avisos = self._calada(self.sessao.reler, self.sessao.rascunho.base)
        if avisos is not None:
            for aviso in avisos:
                self.notify(str(aviso), **aviso.opcoes())
            self._montar()

    # ── olhar o banco ───────────────────────────────────────────────────────

    def vigiar(self) -> None:
        if not self._olhando:              # banco lento: uma olhada por vez, sem empilhar threads
            self._comecar_olhada(not self.carregado)

    def _comecar_olhada(self, primeira: bool) -> None:
        """A marca de "no ar" é posta aqui, na thread da tela, antes da thread começar: o timer que vem logo depois
        de um pedido à mão não começa outra (que cancelaria esta, `exclusive`)."""
        self._olhada += 1
        self._olhando = True
        self._olhar(primeira, self._olhada)

    def _olhou(self, olhada: int) -> None:
        if olhada == self._olhada:
            self._olhando = False

    @work(thread=True, exclusive=True, group="vigia_permissoes")
    def _olhar(self, primeira: bool, olhada: int = 0) -> None:
        try:
            try:
                with self._trava:
                    marca = self.fonte.assinatura_permissoes(self.esquema)
                    if primeira or marca != self._marca:
                        m, p = self.fonte.modelo(self.esquema), self.fonte.permissoes(self.esquema)
                    else:
                        m = p = None
            except Exception as erro:
                if primeira:
                    self._de_volta(self._nao_leu, erro)
                return
            if m is not None:
                self._de_volta(self._chegou, marca, m, p)
        finally:
            self._de_volta(self._olhou, olhada)

    def _nao_leu(self, erro) -> None:
        if self.is_attached and not self.carregado and not self._avisou_erro:
            self._avisou_erro = True        # a vigia segue tentando; o aviso sai uma vez
            linhas = str(erro).strip().splitlines()
            self.notify(t("canvas.erro_banco", erro=linhas[0] if linhas else type(erro).__name__), severity="error")

    def _chegou(self, marca, m, p: catalogo.Permissoes) -> None:
        if not self.is_attached or self.sessao.aplicando:
            return
        self._marca, self.permissoes = marca, p
        if not self.carregado:
            self.carregado = True
            for aviso in self._calada(self.sessao.abrir, m):
                self.notify(str(aviso), **aviso.opcoes())
        else:
            novas = self._calada(self.sessao.rebase, m)
            if novas:
                self.notify(t("canvas.invalidas", n=len(novas)), severity="warning")
        self._montar()

    # ── desenhar ────────────────────────────────────────────────────────────

    def _montar(self) -> None:
        r = self.sessao.rascunho
        self._titulo()
        if self.permissoes is not None:
            v = r.vista()
            ef = efetivas(self.permissoes, r)
            self._desenhar_grade(v, self.permissoes, ef)
            self._linha_schema(v, self.permissoes, ef)
            self._linha_avisos(v, ef)
        else:
            self.query_one("#perm_schema", Static).update(Text(t("perm.lendo"), style=COR["cinza"]))
        self.query_one(PainelSQL).atualizar(r.passos(), self.sessao.falha, self.sessao.mensagem_falha)

    def _titulo(self) -> None:
        ops = len(self.sessao.rascunho.operacoes)
        self.query_one("#perm_titulo", Static).update(Text.assemble(
            ("ratt db", f"bold {COR['azul']}"), (" · ", COR["cinza"]), (str(self.fonte.banco), "bold"),
            (" › ", COR["cinza"]), (self.esquema, "bold"), (" › ", COR["cinza"]), (t("perm.titulo"), "bold"),
            (" · ", COR["cinza"]), (t("canvas.rascunho", n=ops), COR["laranja"] if ops else COR["cinza"])))

    def _papeis(self, v) -> List[str]:
        """As linhas: PUBLIC e os papéis que existem no servidor (sem os do PostgreSQL), por nome."""
        return [PUBLIC] + sorted((i for i, nome in v.papeis.items() if not nome.startswith("pg_")),
                                 key=lambda i: v.papeis[i])

    def _colunas(self, v, perms: catalogo.Permissoes) -> List[str]:
        """As colunas: as tabelas que o catálogo de permissões lê (comuns e particionadas) e que a vista ainda tem."""
        return sorted((i for i in perms.dono if i in v.tabelas and v.tabelas[i].estado != "apagado"),
                      key=lambda i: v.tabelas[i].nome)

    def _rotulo(self, v, papel: str) -> Text:
        if papel == PUBLIC:
            return Text(PUBLIC, style=f"bold {COR['azul']}")
        rotulo = Text(v.papeis[papel], style="bold")
        if papel == v.eu:
            rotulo.append(" ●", style=COR["verde"])
        return rotulo

    def _selecionada(self, papel: str, tabela: str) -> bool:
        return self.selecao is not None and papel in self.selecao[0] and tabela in self.selecao[1]

    def _celula(self, base: catalogo.Permissoes, ef: catalogo.Permissoes, papel: str, tabela: str) -> Text:
        return celula(base.por_tabela.get((papel, tabela), frozenset()), ef.por_tabela.get((papel, tabela), frozenset()),
                      base.dono.get(tabela) == papel, self._selecionada(papel, tabela))

    def _desenhar_grade(self, v, base: catalogo.Permissoes, ef: catalogo.Permissoes) -> None:
        grade = self.query_one(Grade)
        linhas, tabelas = self._papeis(v), self._colunas(v, base)
        if self.selecao is not None:        # o que a seleção tinha e sumiu sai dela
            papeis = tuple(p for p in self.selecao[0] if p in linhas)
            tabs = tuple(i for i in self.selecao[1] if i in tabelas)
            self.selecao = (papeis, tabs) if papeis and tabs else None
        rotulos = tuple(self._rotulo(v, p).plain for p in linhas) + tuple(v.tabelas[i].nome for i in tabelas)
        estrutura = (tuple(linhas), tuple(tabelas), rotulos)
        if estrutura == self._estrutura:    # só as células mudam: o cursor e a rolagem ficam onde estão
            for i, papel in enumerate(linhas):
                for j, tabela in enumerate(tabelas, 1):
                    grade.update_cell_at(Coordinate(i, j), self._celula(base, ef, papel, tabela))
            return
        cursor = grade.cursor_coordinate
        linha_antes = self._linhas[cursor.row] if 0 <= cursor.row < len(self._linhas) else None
        k = cursor.column - 1
        tabela_antes = self._tabelas[k] if 0 <= k < len(self._tabelas) else None
        self._linhas, self._tabelas, self._estrutura = linhas, tabelas, estrutura
        grade.clear(columns=True)
        grade.add_column(Text(t("perm.c.papel"), style="bold"))
        for i in tabelas:
            grade.add_column(Text(v.tabelas[i].nome, style="bold"))
        for papel in linhas:
            grade.add_row(self._rotulo(v, papel), *[self._celula(base, ef, papel, i) for i in tabelas])
        grade.fixed_rows = 1                # o PUBLIC fica à vista quando a grade rola
        linha = linhas.index(linha_antes) if linha_antes in linhas else min(cursor.row, len(linhas) - 1)
        coluna = (tabelas.index(tabela_antes) + 1 if tabela_antes in tabelas
                  else min(max(cursor.column, 1 if tabelas else 0), len(tabelas)))
        grade.cursor_coordinate = Coordinate(max(linha, 0), max(coluna, 0))

    def _linha_schema(self, v, base: catalogo.Permissoes, ef: catalogo.Permissoes) -> None:
        """`schema public: PUBLIC USAGE · app USAGE, CREATE` — o que o rascunho dá em amarelo, o que tira com `−`.
        Depois das linhas da grade, os papéis do PostgreSQL que têm algo no schema (a grade não os mostra): no 15+, o
        `public` é de `pg_database_owner`, o dono do banco."""
        texto = Text(t("perm.schema", esquema=v.esquema) + " ", style="bold")
        itens: List[Text] = []
        fora = sorted((p for p in set(base.schema) | set(ef.schema) if p not in self._linhas and p in v.papeis),
                      key=lambda p: nome_papel(v, p))
        for papel in list(self._linhas) + fora:
            agora, antes = ef.schema.get(papel, frozenset()), base.schema.get(papel, frozenset())
            if not agora and not antes:
                continue
            nome = nome_papel(v, papel)
            item = Text(nome + " ")
            if nome == "pg_database_owner":
                item.append(t("perm.dono_do_banco") + " ", style=COR["cinza"])
            partes = [Text(n, style="" if n in antes else f"bold {COR['amarelo']}") for n in _NO_SCHEMA if n in agora]
            partes += [Text("−" + n, style=f"strike {COR['laranja']}") for n in _NO_SCHEMA if n in antes and n not in agora]
            item.append_text(Text(", ").join(partes))
            itens.append(item)
        if itens:
            texto.append_text(Text(" · ", style=COR["cinza"]).join(itens))
        else:
            texto.append(t("perm.schema_ninguem"), style=COR["laranja"])
        self.query_one("#perm_schema", Static).update(texto)

    def _linha_avisos(self, v, ef: catalogo.Permissoes) -> None:
        partes: List[Text] = []
        if not self._tabelas:
            partes.append(Text(t("perm.vazio"), style=COR["amarelo"]))
        if self.selecao is not None:
            partes.append(Text(self._descrever_selecao(v), style=COR["azul"]))
        padrao = [f"{nome_papel(v, p)} {''.join(l for l in PRIVILEGIOS if l in letras)}"
                  for p, letras in sorted(ef.padrao.items(), key=lambda x: nome_papel(v, x[0]))]
        if padrao:
            partes.append(Text(t("perm.padrao", papel=v.papeis.get(v.eu, "?"), lista=", ".join(padrao)),
                               style=COR["cinza"]))
        if self._avisos_op:
            avisos = texto_avisos(self._avisos_op)
            avisos.rstrip()
            partes.append(avisos)
        partes.append(Text(t("perm.legenda"), style=COR["cinza"]))     # por último: é o que some primeiro sem espaço
        self.query_one("#perm_avisos", Static).update(Text("\n").join(partes))

    def _descrever_selecao(self, v) -> str:
        papeis, tabelas = self.selecao
        if len(papeis) == 1 and len(tabelas) == len(self._tabelas):
            return t("perm.sel_linha", papel=nome_papel(v, papeis[0]), n=len(tabelas))
        if len(tabelas) == 1 and len(papeis) == len(self._linhas):
            return t("perm.sel_coluna", tabela=v.tabelas[tabelas[0]].nome, n=len(papeis))
        return t("perm.sel_todas", papeis=len(papeis), tabelas=len(tabelas))

    # ── a seleção ───────────────────────────────────────────────────────────

    def linha_id(self, i: int) -> str:
        """O papel (ou PUBLIC) da linha `i` da grade."""
        return self._linhas[i]

    def ir_para(self, papel: str, tabela: Optional[str] = None) -> None:
        """Põe o cursor na linha do papel (e na coluna da tabela, se vier)."""
        coluna = self._tabelas.index(tabela) + 1 if tabela in self._tabelas else 0
        self.query_one(Grade).cursor_coordinate = Coordinate(self._linhas.index(papel), coluna)

    def alvo(self) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
        """(papéis, tabelas) em que o formulário age: a seleção, ou a célula do cursor (no nome: a linha inteira).
        Sempre na ordem da grade."""
        if self.selecao is not None:
            return self.selecao
        cursor = self.query_one(Grade).cursor_coordinate
        if not 0 <= cursor.row < len(self._linhas):
            return (), ()
        papel = self._linhas[cursor.row]
        if cursor.column == 0:
            return (papel,), tuple(self._tabelas)
        k = cursor.column - 1
        return ((papel,), (self._tabelas[k],)) if 0 <= k < len(self._tabelas) else ((papel,), ())

    def _selecionar(self, selecao) -> None:
        """Troca a seleção (a mesma de novo desfaz) e redesenha."""
        self.selecao = None if selecao == self.selecao else selecao
        if self.carregado:
            self._montar()

    def action_linha(self) -> None:
        if not self._carregada():
            return
        linha = self.query_one(Grade).cursor_coordinate.row
        if 0 <= linha < len(self._linhas) and self._tabelas:
            self._selecionar(((self._linhas[linha],), tuple(self._tabelas)))

    def action_coluna(self) -> None:
        if not self._carregada():
            return
        k = self.query_one(Grade).cursor_coordinate.column - 1
        if not 0 <= k < len(self._tabelas):
            self.notify(t("perm.e.coluna"), severity="warning")
            return
        self._selecionar((tuple(self._linhas), (self._tabelas[k],)))

    def action_todas(self) -> None:
        if self._carregada() and self._tabelas:
            self._selecionar((tuple(self._linhas), tuple(self._tabelas)))

    def escolher_celula(self, linha: int, coluna: int, shift: bool = False) -> None:
        """Um clique numa célula: o cursor vai para ela e a seleção se desfaz; com shift (ou no nome do papel), a
        linha inteira fica selecionada."""
        if not 0 <= linha < len(self._linhas):
            return
        self.query_one(Grade).cursor_coordinate = Coordinate(linha, coluna)
        if (shift or coluna == 0) and self._tabelas:
            self.selecao = None
            self._selecionar(((self._linhas[linha],), tuple(self._tabelas)))
        elif self.selecao is not None:
            self._selecionar(None)

    def on_data_table_cell_selected(self, evento: DataTable.CellSelected) -> None:
        evento.stop()
        self.escolher_celula(evento.coordinate.row, evento.coordinate.column,
                             getattr(evento.data_table, "shift", False))

    def on_data_table_header_selected(self, evento: DataTable.HeaderSelected) -> None:
        """Clique no nome de uma tabela: a coluna inteira."""
        evento.stop()
        k = evento.column_index - 1
        if self.carregado and 0 <= k < len(self._tabelas):
            self.selecao = None
            self._selecionar((tuple(self._linhas), (self._tabelas[k],)))

    def action_voltar(self) -> None:
        if self.selecao is not None:
            self._selecionar(None)
            return
        self.app.pop_screen()

    # ── o rascunho ──────────────────────────────────────────────────────────

    def _carregada(self) -> bool:
        if not self.carregado or self.permissoes is None:
            self.notify(t("canvas.carregando"))
            return False
        return True

    def _pronto(self) -> bool:
        return self._livre() and self._carregada()

    def _depois_de_mudar(self) -> None:
        self._avisos_op = []
        if self.permissoes is not None:
            r = self.sessao.rascunho
            self._linha_avisos(r.vista(), efetivas(self.permissoes, r))

    def _abrir(self, form) -> None:
        """Abre um formulário; o que ele puser no rascunho a sessão grava e as telas ficam sabendo (a seleção se
        desfaz). Os avisos que o formulário contou vão para a linha de avisos."""
        def depois(ops) -> None:
            if not ops:
                return
            self._avisos_op = list(form.avisos_chegados or [])
            self.selecao = None
            self.sessao.mudou()
        self.app.push_screen(form, depois)

    def action_privilegios(self) -> None:
        if not self._pronto():
            return
        papeis, tabelas = self.alvo()
        if not papeis or not tabelas:
            self.notify(t("perm.vazio") if not self._tabelas else t("perm.e.coluna"), severity="warning")
            return
        self._abrir(FormPrivilegios(self.sessao, self.fonte_avisos, papeis, tabelas))

    def action_schema(self) -> None:
        if not self._pronto():
            return
        linha = self.query_one(Grade).cursor_coordinate.row
        if not 0 <= linha < len(self._linhas):
            return
        papel = self._linhas[linha]
        atuais = efetivas(self.permissoes, self.sessao.rascunho).schema.get(papel, frozenset())
        self._abrir(FormSchema(self.sessao, self.fonte_avisos, papel, atuais))

    # ── depois de aplicar ───────────────────────────────────────────────────

    def depois_de_aplicar(self, esquema: str, nomes: Dict[str, str]) -> None:
        """O banco gravou: relê o catálogo e as permissões (a grade passa a mostrar o que ficou) e os entrega à
        sessão (`aplicado`: o rascunho volta a ser editável)."""
        self._relendo = self.sessao
        self._reler_aplicado()

    @work(thread=True, exclusive=True, group="aplicar_permissoes")
    def _reler_aplicado(self) -> None:
        try:
            with self._trava:
                marca = self.fonte.assinatura_permissoes(self.esquema)
                m, p = self.fonte.modelo(self.esquema), self.fonte.permissoes(self.esquema)
        except Exception as erro:
            self._de_volta(self._aplicado, None, None, None, erro)
            return
        self._de_volta(self._aplicado, marca, m, p, None)

    def _aplicado(self, marca, m, p: Optional[catalogo.Permissoes], erro) -> None:
        s, self._relendo = self._relendo, None
        if m is None:
            if s is not None:
                s.aplicando = False
            if self.is_attached:
                self._marca = None          # a vigia traz o banco na próxima volta
                linhas = str(erro).strip().splitlines()
                self.notify(t("canvas.erro_banco", erro=linhas[0] if linhas else type(erro).__name__),
                            severity="error")
            return
        if s is not None:
            self._calada(s.aplicado, m)
        if not self.is_attached:
            return
        self._marca, self.permissoes = marca, p
        self._avisos_op = []
        self._montar()
