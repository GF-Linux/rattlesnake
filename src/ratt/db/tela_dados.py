"""A tela Dados de uma tabela: as linhas do banco com um filtro montado sem digitar SQL (ou um WHERE escrito à
mão), paginadas, com os avisos do filtro e o mesmo painel do rascunho do canvas.

    ratt db · estudos › public.pessoas · Dados · rascunho: 3
    filtro: nome começa com G e mês(nascimento) é maior ou igual a junho   (f · F limpa · t texto · o E/OU)
    ── 37 de 454 linhas ──
     id    nome      nascimento ...
    ! 3 linhas com nascimento vazio (∅) ficam de fora ...
    ┌ SQL: o SELECT do filtro e o rascunho inteiro ┐

A grade mostra o banco como ele está (não as mudanças pendentes do rascunho: uma linha diz quantas há nesta
tabela). Toda leitura roda numa thread, somente leitura e com tempo limite, pela `fonte` (a mesma do canvas, ou
uma falsa nos testes); resposta de um pedido velho é descartada. O WHERE sai sempre com os nomes do BANCO (a base
do rascunho): coluna ou tabela que só existe no rascunho fica para depois de aplicar, com o motivo dito.
Views, materializadas e tabelas estrangeiras abrem só para ver e filtrar (`so_ver`).
"""
from __future__ import annotations

import copy
import re
import threading
from typing import Dict, List, Optional

import psycopg
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from ratt.db import catalogo, checagem, filtro, formularios, modelo as _modelo, rascunho as _rascunho, sessao
from ratt.db.formularios_dados import FormCondicao
from ratt.db.grade_dados import Grade, condicao_da_celula, modelo_da_view, select_texto, texto_contagem
from ratt.db.idioma import t
from ratt.db.painel import AcoesRascunho, PainelSQL, PerguntarNome
from ratt.db.tela import COR, POR_PAGINA, _celula

_NIVEL = {"info": ("·", COR["cinza"]), "atencao": ("!", COR["amarelo"]), "impede": ("✖", COR["laranja"])}
_SO_VER = ("f", "v", "m")               # relkind das relações que só se veem: estrangeira, view, materializada
_ACOES_DO_RASCUNHO = ("aplicar", "ensaio", "exportar", "desfazer")


def _primeira_linha(erro: Exception) -> str:
    linhas = str(erro).strip().splitlines()
    return linhas[0] if linhas else type(erro).__name__


class TelaDados(AcoesRascunho, Screen):
    BINDINGS = [
        Binding("f", "filtro", t("dados.k.filtro")),
        Binding("F,shift+f", "limpar", t("dados.k.limpar")),
        Binding("t", "texto", t("dados.k.texto")),
        Binding("o", "conectivo", t("dados.k.conectivo")),
        Binding("backspace", "tirar_condicao", t("dados.k.tirar"), show=False),
        Binding("n", "pagina(1)", t("tui.proxima")),
        Binding("N,shift+n", "pagina(-1)", t("tui.anterior"), show=False),
        Binding("enter", "aplicar", t("canvas.k.aplicar")),
        Binding("e", "ensaio", t("canvas.k.ensaio")),
        Binding("w", "exportar", t("canvas.k.exportar")),
        Binding("u", "desfazer", t("canvas.k.desfazer")),
        Binding("r", "painel", t("canvas.k.rascunho")),
        Binding("escape,q", "voltar", t("canvas.k.voltar")),
    ]
    DEFAULT_CSS = """
    #dados_titulo { height: 1; padding: 0 1; background: $panel; }
    #dados_filtro { height: auto; max-height: 3; padding: 0 1; }
    #dados_contagem { height: 1; padding: 0 1; }
    #dados_grade { height: 1fr; }
    #dados_avisos, #dados_pendentes { height: auto; max-height: 4; padding: 0 1; }
    """

    def __init__(self, fonte, esquema: str, tabela_id: str, so_ver: bool = False,
                 colunas_view: Optional[List[catalogo.Coluna]] = None, nome: Optional[str] = None,
                 view: bool = False):
        """`view=True`: as colunas vêm do catálogo na primeira leitura (na thread, pela conexão desta tela);
        `colunas_view` as dá prontas."""
        super().__init__()
        self.fonte, self.esquema, self.tabela_id, self.so_ver = fonte, esquema, tabela_id, so_ver
        self.nome = nome
        self.filtro = filtro.Filtro()
        self.pagina = 0
        self.total: Optional[int] = None            # quantas linhas o filtro pega (None: não deu para contar)
        self.total_tabela: Optional[int] = None     # quantas a tabela tem
        self.linhas: List[tuple] = []
        self.nomes: List[str] = []                  # as colunas da página, como o banco as devolveu
        self.tipos: List[str] = []
        self.avisos: List[checagem.Aviso] = []
        self.sessao: Optional[sessao.SessaoRascunho] = None
        self.vista: _modelo.Modelo = _modelo.Modelo(esquema)     # o schema com o rascunho (os nomes que se veem)
        self.carregado = False
        self._view = view or colunas_view is not None
        self._modelo_view: Optional[_modelo.Modelo] = None      # a view como um modelo de uma tabela só
        if colunas_view is not None:
            self._modelo_view = modelo_da_view(esquema, tabela_id, nome or tabela_id, colunas_view)
        self._estado = "lendo"              # lendo, ok, sem_tempo, erro, so_depois, sumiu, sem_leitura
        self._erro = ""
        self._token = 0                     # sobe a cada pedido de página: a resposta de outro é descartada
        self._pagina_mostrada = 0           # a página que está na grade (volta a ela se a próxima estourar o tempo)
        self._forma = None                  # as colunas desenhadas: iguais, a releitura só troca as linhas
        self._contando = False              # contagens e avisos a caminho (pedido separado da página)
        self._chave_pedida = None           # (tabela, WHERE, estrutura, escritas) das contagens pedidas
        self._chave_contada = None          # e das que estão guardadas: paginar não conta de novo
        self._contadas = (None, None, [])   # (total, total da tabela, avisos) da chave contada
        self._marca = None                  # a assinatura da estrutura na última leitura do modelo
        self._marca_escritas = None         # e a das escritas: mudou, a grade relê
        self._trava = threading.Lock()      # uma conversa com o banco por vez nesta tela
        self._calado = False
        self._olhando = False               # a vigia está no ar
        self._relendo: Optional[sessao.SessaoRascunho] = None
        self._criada: Optional[str] = None  # nome da tabela criada pelo rascunho que acabou de ser aplicado

    def compose(self) -> ComposeResult:
        yield Static(id="dados_titulo")
        yield Static(id="dados_filtro")
        yield Static(id="dados_contagem")
        yield Grade(id="dados_grade", header_height=2, zebra_stripes=True, cursor_type="cell")
        yield Static(id="dados_avisos")
        yield Static(id="dados_pendentes")
        yield PainelSQL(id="dados_sql")
        yield Footer()

    def on_mount(self) -> None:
        self.sessao = sessao.obter(self.app, self.fonte, self.esquema)
        self.sessao.ouvir(self._rascunho_mudou)
        self._montar()
        self._linha_contagem()
        self._olhar(True)
        self.set_interval(2.0, self.vigiar)

    def on_unmount(self) -> None:
        self.sessao.parar(self._rascunho_mudou)
        if self._relendo is not None:
            self._relendo.aplicando, self._relendo = False, None
        fechar = getattr(self.fonte, "fechar", None)
        if fechar:
            fechar()

    # ── o modelo ────────────────────────────────────────────────────────────

    @property
    def rascunho(self) -> _rascunho.Rascunho:
        return self.sessao.rascunho

    def _base(self) -> _modelo.Modelo:
        return self._modelo_view if self._modelo_view is not None else self.rascunho.base

    def _tab_base(self) -> Optional[_modelo.Tabela]:
        return self._base().tabelas.get(self.tabela_id)

    def _tab_vista(self) -> Optional[_modelo.Tabela]:
        m = self._modelo_view if self._modelo_view is not None else self.vista
        return m.tabelas.get(self.tabela_id)

    def _vista(self) -> _modelo.Modelo:
        return self._modelo_view if self._modelo_view is not None else self.vista

    def _nome_tabela(self) -> str:
        tab = self._tab_vista() or self._tab_base()
        return tab.nome if tab is not None else (self.nome or self.tabela_id)

    def _rascunho_mudou(self) -> None:
        if self.is_attached and self.carregado and not self._calado:
            self._montar()

    def _calada(self, fn, *args):
        self._calado = True
        try:
            return fn(*args)
        finally:
            self._calado = False

    def on_screen_resume(self) -> None:
        if not self.carregado or self.sessao.aplicando:
            return
        avisos = self._calada(self.sessao.reler, self.rascunho.base)
        if avisos is not None:
            for aviso in avisos:
                self.notify(str(aviso), **aviso.opcoes())
            self._montar()

    def _montar(self) -> None:
        """Título, barra do filtro, mudanças pendentes e o painel (com o SELECT do filtro no topo)."""
        if self._modelo_view is None:
            self.vista = self.rascunho.vista()
        self._titulo()
        self._barra()
        n = sum(1 for o in self.rascunho.operacoes if self.tabela_id in (o.refere() | o.cria()))
        self.query_one("#dados_pendentes", Static).update(
            Text(t("dados.pendentes", n=n), style=COR["amarelo"]) if n and self._modelo_view is None else "")
        self.query_one(PainelSQL).atualizar(self.rascunho.passos(), self.sessao.falha, self.sessao.mensagem_falha,
                                            topo=self._select_texto())

    def _titulo(self) -> None:
        ops = len(self.rascunho.operacoes)
        self.query_one("#dados_titulo", Static).update(Text.assemble(
            ("ratt db", f"bold {COR['azul']}"), (" · ", COR["cinza"]), (str(self.fonte.banco), "bold"),
            (" › ", COR["cinza"]), (f"{self.esquema}.{self._nome_tabela()}", ""), (" · ", COR["cinza"]),
            (t("dados.titulo"), "bold"), (" · ", COR["cinza"]),
            (t("canvas.rascunho", n=ops), COR["laranja"] if ops else COR["cinza"])))

    def _barra(self) -> None:
        em_texto = self.filtro.texto is not None and bool(self.filtro.texto.strip())
        try:
            descricao = filtro.descricao(self.filtro, self._vista(), self.tabela_id)
        except KeyError:
            descricao = t("filtro.sem_condicoes")
        texto = Text.assemble((t("dados.filtro_texto" if em_texto else "dados.filtro") + " ", COR["cinza"]),
                              (descricao, "bold" if not self.filtro.vazio() else COR["cinza"]),
                              ("   " + t("dados.dicas"), COR["cinza"]))
        if self.so_ver:
            texto.append("\n" + t("dados.so_ver"), style=COR["amarelo"])
        self.query_one("#dados_filtro", Static).update(texto)

    def _onde(self) -> str:
        """O WHERE (sem a palavra) com os nomes do banco; ValueError se o filtro não se monta sobre o banco."""
        for i in sorted(filtro.refere(self.filtro, self.tabela_id) - {self.tabela_id}):
            if _rascunho._nome_na_base(self._base(), i) is None:
                raise ValueError(t("dados.e.so_depois", nome=_rascunho._nome_de(i, self._vista(), self._base())))
        return filtro.where(self.filtro, self._base(), self.tabela_id)

    def _select_texto(self) -> Optional[str]:
        tab = self._tab_base()
        try:
            return select_texto(tab, self._onde()) if tab is not None else None
        except ValueError:
            return None

    # ── a grade ─────────────────────────────────────────────────────────────

    def _pedir_grade(self, manter: bool = False) -> None:
        """Pede a página. `manter`: releitura da vigia — com as mesmas colunas, o cursor e a rolagem ficam."""
        if not self.carregado:
            return
        self._token += 1
        tab = self._tab_base()
        if tab is None:
            self.linhas, self.nomes, self.tipos, self.avisos = [], [], [], []
            self.total = self.total_tabela = None
            self._estado = "so_depois" if self._tab_vista() is not None else "sumiu"
            self._mostrar()
            return
        try:
            onde = self._onde()
        except ValueError as e:
            self.linhas, self.avisos, self.total = [], [], None
            self._estado, self._erro = "erro", str(e)
            self._mostrar()
            return
        if self._estado not in ("ok", "sem_tempo"):
            self._estado = "lendo"
            self._linha_contagem()
        self._ler_pagina(self._token, tab.esquema, tab.nome, onde, [c.nome for c in tab.pk()], self.pagina, manter)

    @work(thread=True, exclusive=True, group="grade_dados")
    def _ler_pagina(self, token, esquema, nome, onde, ordem, pagina, manter) -> None:
        """Só a página: ela aparece logo; contagens e avisos vêm num segundo pedido (`_contar`)."""
        pacote: Dict[str, object] = {"pagina_pedida": pagina, "onde": onde, "manter": manter}
        with self._trava:
            try:
                pacote["pagina"] = self.fonte.pagina(esquema, nome, onde, ordem, pagina * POR_PAGINA, POR_PAGINA)
            except psycopg.errors.QueryCanceled:
                pacote["sem_tempo"] = True
            except Exception as erro:
                pacote["erro"] = _primeira_linha(erro)
        self._de_volta(self._pagina_chegou, token, pacote)

    def _de_volta(self, fn, *args) -> None:
        try:
            self.app.call_from_thread(fn, *args)
        except RuntimeError:                # a app já fechou
            pass

    def _pagina_chegou(self, token: int, pacote: dict) -> None:
        if not self.is_attached or token != self._token:
            return
        if pacote.get("sem_tempo"):         # a página anterior fica à vista, e o número dela também
            self.pagina = self._pagina_mostrada
            self._estado = "sem_tempo"
            self._linha_contagem()
            return
        if "erro" in pacote:
            self.linhas, self.avisos, self.total = [], [], None
            self._estado, self._erro = "erro", str(pacote["erro"])
            self._mostrar()
            return
        nomes, tipos, linhas = pacote["pagina"]
        self.nomes, self.tipos, self.linhas = list(nomes), list(tipos), list(linhas)
        self._pagina_mostrada = pacote["pagina_pedida"]
        self._estado = "ok"
        self._contagens(str(pacote["onde"]))
        self._mostrar(bool(pacote["manter"]))

    def _contagens(self, onde: str) -> None:
        """Contagens e avisos do filtro: guardados por (tabela, WHERE, estrutura, escritas desta tabela) —
        paginar não conta de novo; mudar o filtro, ou alguém escrever na tabela, sim."""
        chave = (self.tabela_id, onde, self._marca, self._marca_escritas)
        if chave == self._chave_contada:
            self.total, self.total_tabela, self.avisos = self._contadas[0], self._contadas[1], list(self._contadas[2])
            self._contando = False
            return
        self.total = self.total_tabela = None
        if chave == self._chave_pedida and self._contando:
            return                          # já está a caminho
        tab = self._tab_base()
        self._chave_pedida, self._contando, self.avisos = chave, True, []
        self._contar(chave, tab.esquema, tab.nome, onde, copy.deepcopy(self.filtro),
                     _rascunho.Rascunho(self._base()), self.tabela_id)

    @work(thread=True, exclusive=True, group="contagem_dados")
    def _contar(self, chave, esquema, nome, onde, f, r, tabela_id) -> None:
        total = total_tabela = None
        avisos: list = []
        with self._trava:
            try:
                total = self.fonte.contar(esquema, nome, onde)
                total_tabela = total if not onde else self.fonte.contar(esquema, nome, "")
            except Exception:
                pass
            try:
                avisos = checagem.avisos_do_filtro(f, r, tabela_id, self.fonte.consulta())
            except Exception:
                avisos = []
        self._de_volta(self._contagens_chegaram, chave, total, total_tabela, avisos)

    def _contagens_chegaram(self, chave, total, total_tabela, avisos) -> None:
        if not self.is_attached or chave != self._chave_pedida:
            return
        self._chave_contada, self._contadas, self._contando = chave, (total, total_tabela, list(avisos)), False
        if self._estado == "ok":
            self.total, self.total_tabela, self.avisos = total, total_tabela, list(avisos)
            self._linha_contagem()
            self._linha_avisos()

    def _mostrar(self, manter: bool = False) -> None:
        self._desenhar_grade(manter)
        self._linha_contagem()
        self._linha_avisos()

    def _colunas_so_depois(self) -> List[_modelo.Coluna]:
        tab = self._tab_vista()
        if tab is None or self._modelo_view is not None:
            return []
        return [c for c in tab.colunas if c.estado != "apagado" and _rascunho._nome_na_base(self._base(), c.id) is None]

    def _desenhar_grade(self, manter: bool = False) -> None:
        grade = self.query_one(Grade)
        renomeadas: Dict[str, str] = {}
        tab_b, tab_v = self._tab_base(), self._tab_vista()
        if tab_b is not None and tab_v is not None and self._modelo_view is None:
            for c in tab_b.colunas:
                cv = tab_v.coluna(c.id)
                if cv is not None and cv.nome != c.nome:
                    renomeadas[c.nome] = cv.nome
        extras = self._colunas_so_depois()
        vazias = [Text("·", style=COR["cinza"])] * len(extras)
        linhas = [[_celula(v, tipo) for v, tipo in zip(linha, self.tipos)] + vazias for linha in self.linhas]
        forma = (tuple(self.nomes), tuple(self.tipos), tuple(sorted(renomeadas.items())), tuple(c.id for c in extras))
        if manter and forma == self._forma:
            grade.trocar_linhas(linhas)
            return
        self._forma = forma
        grade.clear(columns=True)
        for nome, tipo in zip(self.nomes, self.tipos):
            rotulo = Text(nome, style="bold")
            if nome in renomeadas:
                rotulo.append(f" → {renomeadas[nome]}", style=COR["amarelo"])
            rotulo.append("\n")
            rotulo.append(tipo, style=COR["cinza"])
            grade.add_column(rotulo)
        for c in extras:
            grade.add_column(Text.assemble((c.nome, f"bold {COR['amarelo']}"), "\n", (t("dados.so_depois"), COR["amarelo"])))
        for celulas in linhas:
            grade.add_row(*celulas)

    def _linha_contagem(self) -> None:
        tab = self._tab_base()
        texto, cor = texto_contagem(self._estado, por_pagina=POR_PAGINA, pagina=self.pagina, n_linhas=len(self.linhas),
                                    total=self.total, total_tabela=self.total_tabela, contando=self._contando,
                                    sem_pk=tab is not None and not tab.pk(), tabela=self._nome_tabela(), erro=self._erro)
        self.query_one("#dados_contagem", Static).update(Text(f"── {texto} ──", style=cor))

    def _linha_avisos(self) -> None:
        partes = []
        for a in self.avisos:
            marca, cor = _NIVEL.get(a.nivel, ("·", COR["cinza"]))
            partes.append(Text.assemble((marca + " ", cor), (a.texto(), cor if a.nivel != "info" else "")))
        self.query_one("#dados_avisos", Static).update(Text("\n").join(partes))

    # ── olhar o banco ───────────────────────────────────────────────────────

    def vigiar(self) -> None:
        if not self._olhando:              # banco lento: uma olhada por vez, sem empilhar threads
            self._olhar(not self.carregado)

    @work(thread=True, exclusive=True, group="vigia_dados")
    def _olhar(self, primeira: bool) -> None:
        """A estrutura do banco mudou: relê o modelo (e a grade). Só as escritas DESTA tabela (ou das que a view
        lê) mudaram: relê a grade. Na primeira vez, as colunas da view e o relkind vêm junto."""
        self._olhando = True
        oid = self._oid()
        try:
            with self._trava:
                estrutura, escritas = self.fonte.assinaturas(oid)
                m = self.fonte.modelo(self.esquema) if primeira or estrutura != self._marca else None
                classe = self.fonte.classe(oid) if primeira and not self.so_ver and oid is not None \
                    and hasattr(self.fonte, "classe") else None
                colunas = self.fonte.colunas(oid) if primeira and self._view and self._modelo_view is None \
                    and oid is not None else None
        except Exception as erro:
            if primeira:
                self._de_volta(self._nao_leu, erro)
            return
        finally:
            self._olhando = False
        if m is not None:
            self._de_volta(self._modelo_chegou, estrutura, escritas, m, classe, colunas)
        elif escritas != self._marca_escritas:
            self._de_volta(self._escritas_mudaram, escritas)

    def _oid(self) -> Optional[int]:
        achado = re.fullmatch(r"t(\d+)", self.tabela_id)
        return int(achado.group(1)) if achado else None

    def _nao_leu(self, erro) -> None:
        if not self.is_attached or self.carregado:
            return
        if self._estado != "sem_leitura":
            self.notify(t("canvas.erro_banco", erro=_primeira_linha(erro)), severity="error")
        self._estado = "sem_leitura"
        self._linha_contagem()

    def _modelo_chegou(self, estrutura, escritas, m: _modelo.Modelo, classe: Optional[str], colunas=None) -> None:
        if not self.is_attached or self.sessao.aplicando:
            return
        manter = self.carregado
        self._marca, self._marca_escritas = estrutura, escritas
        if colunas is not None:
            self._modelo_view = modelo_da_view(self.esquema, self.tabela_id, self.nome or self.tabela_id, colunas)
        if classe in _SO_VER and not self.so_ver:
            self.so_ver = True
            self.refresh_bindings()
        if not self.carregado:
            self.carregado = True
            for aviso in self._calada(self.sessao.abrir, m):
                self.notify(str(aviso), **aviso.opcoes())
        else:
            novas = self._calada(self.sessao.rebase, m)
            if novas:
                self.notify(t("canvas.invalidas", n=len(novas)), severity="warning")
        self._montar()
        self._pedir_grade(manter)

    def _escritas_mudaram(self, escritas) -> None:
        if self.is_attached:
            self._marca_escritas = escritas
            self._pedir_grade(manter=True)

    # ── o filtro ────────────────────────────────────────────────────────────

    def _pode_filtrar(self) -> bool:
        if not self.carregado:
            self.notify(t("canvas.carregando"))
            return False
        if self._tab_base() is None:
            self.notify(t("dados.so_depois_tabela", tabela=self._nome_tabela()), severity="warning")
            return False
        return True

    def _em_texto(self) -> bool:
        return self.filtro.texto is not None and bool(self.filtro.texto.strip())

    def _filtro_mudou(self) -> None:
        self.pagina = 0
        self._montar()
        self._pedir_grade()

    def acrescentar_condicao(self, c: Optional[filtro.Condicao]) -> None:
        """Junta uma condição montada ao filtro (e sai do modo texto). Recusa, com o motivo, o que não se monta
        sobre o banco — uma coluna que só existe no rascunho, por exemplo."""
        if c is None:
            return
        novo = filtro.Filtro(self.filtro.condicoes + [c], self.filtro.conectivo)
        antes, self.filtro = self.filtro, novo
        try:
            self._onde()
        except ValueError as e:
            self.filtro = antes
            self.notify(str(e), severity="warning")
            return
        self._filtro_mudou()

    def _abrir_form(self, inicial: Optional[filtro.Condicao] = None) -> None:
        self.app.push_screen(FormCondicao(self._vista(), self._base(), self.tabela_id, inicial), self.acrescentar_condicao)

    def _voltar_ao_montado(self, depois=None) -> None:
        """Pergunta antes de descartar o WHERE escrito à mão; `depois` roda se a pessoa aceitar."""
        def resposta(sim) -> None:
            if not sim:
                return
            self.filtro.texto = None
            self._filtro_mudou()
            if depois is not None:
                depois()
        self.app.push_screen(formularios.Confirmar(t("dados.f.voltar_montado"), [], t("dados.f.voltar_sim"), foco="nao"),
                             resposta)

    def action_filtro(self) -> None:
        if not self._pode_filtrar():
            return
        if self._em_texto():
            self._voltar_ao_montado(self._abrir_form)
        else:
            self._abrir_form()

    def action_limpar(self) -> None:
        self.filtro = filtro.Filtro()
        self._filtro_mudou()

    def action_texto(self) -> None:
        if not self._pode_filtrar():
            return
        atual = self.filtro.texto or ""
        if not atual and self.filtro.condicoes:
            try:
                atual = self._onde()        # o WHERE montado vira o ponto de partida do texto
            except ValueError:
                atual = ""
        self._perguntar_texto(atual)

    def _perguntar_texto(self, valor: str) -> None:
        self.app.push_screen(PerguntarNome(t("dados.f.texto"), valor), self._texto_chegou)

    def _texto_chegou(self, valor: Optional[str]) -> None:
        if valor is None:
            return
        if not valor:
            if self._em_texto():
                self._voltar_ao_montado()
            return
        try:
            filtro.validar_texto(valor)
        except ValueError as e:
            self.notify(str(e), severity="error")
            self._perguntar_texto(valor)            # de novo, com o texto, para corrigir
            return
        self.filtro.texto = valor
        self._filtro_mudou()

    def action_conectivo(self) -> None:
        if self._em_texto():
            self.notify(t("dados.e.em_texto"), severity="warning")
            return
        self.filtro.conectivo = "OU" if self.filtro.conectivo == "E" else "E"
        self._filtro_mudou()

    def action_tirar_condicao(self) -> None:
        if self._em_texto():
            self.notify(t("dados.e.em_texto"), severity="warning")
            return
        if not self.filtro.condicoes:
            self.notify(t("dados.e.sem_condicoes"))
            return
        self.filtro.condicoes.pop()
        self._filtro_mudou()

    def on_data_table_cell_selected(self, evento: DataTable.CellSelected) -> None:
        """Clicou numa célula: "igual a este valor" (o formulário já preenchido, para conferir ou mudar). Com o
        filtro em texto, pergunta antes de descartar o texto."""
        evento.stop()
        if not self._pode_filtrar():
            return
        linha, coluna = evento.coordinate.row, evento.coordinate.column
        if coluna >= len(self.nomes):
            extras = self._colunas_so_depois()
            k = coluna - len(self.nomes)
            if 0 <= k < len(extras):
                self.notify(t("dados.e.so_depois", nome=extras[k].nome), severity="warning")
            return
        if linha >= len(self.linhas):
            return
        col = next((c for c in self._tab_base().colunas if c.nome == self.nomes[coluna]), None)
        if col is None:
            self.notify(t("dados.e.coluna_grade"), severity="warning")
            return
        try:
            c = condicao_da_celula(col, self.linhas[linha][coluna])
        except ValueError as e:
            self.notify(str(e), severity="warning")
            return
        if self._em_texto():
            self._voltar_ao_montado(lambda: self._abrir_form(c))
        else:
            self._abrir_form(c)

    def action_pagina(self, direcao: int) -> None:
        """n/N. Depois de uma página que estourou o tempo, tenta de novo a partir da que está à vista."""
        if self._estado not in ("ok", "sem_tempo"):
            return
        nova = self.pagina + direcao
        if nova < 0:
            return
        if direcao > 0:
            if self.total is not None and nova * POR_PAGINA >= self.total:
                return
            if self.total is None and len(self.linhas) < POR_PAGINA:
                return
        self.pagina = nova
        self._pedir_grade()

    def action_voltar(self) -> None:
        self.app.pop_screen()

    # ── o rascunho ──────────────────────────────────────────────────────────

    def check_action(self, action: str, parameters) -> Optional[bool]:
        if self.so_ver and action in _ACOES_DO_RASCUNHO:
            return False
        return True

    def on_painel_sql_tirar(self, evento: PainelSQL.Tirar) -> None:
        if self.so_ver:                     # só para ver: o painel mostra o rascunho, mas não mexe nele
            evento.prevent_default()

    def _pronto(self) -> bool:
        if not self._livre() or self.so_ver:
            return False
        if not self.carregado:
            self.notify(t("canvas.carregando"))
        return self.carregado

    def depois_de_aplicar(self, esquema: str, nomes: Dict[str, str]) -> None:
        """O banco gravou: relê o catálogo (uma tabela criada pelo rascunho passa a ter o id do banco)."""
        self._relendo = self.sessao
        self._criada = nomes.get(self.tabela_id)
        self._reler_aplicado(esquema)

    @work(thread=True, exclusive=True, group="aplicar_dados")
    def _reler_aplicado(self, esquema: str) -> None:
        try:
            with self._trava:
                m = self.fonte.modelo(esquema)
        except Exception as erro:
            self._de_volta(self._aplicado, None, erro)
            return
        self._de_volta(self._aplicado, m, None)

    def _aplicado(self, m: Optional[_modelo.Modelo], erro) -> None:
        s, self._relendo = self._relendo, None
        if s is not None:
            s.aplicando = False
        if not self.is_attached:
            return
        if m is None:
            self._marca = None              # a vigia traz o catálogo na próxima volta
            self.notify(t("canvas.erro_banco", erro=_primeira_linha(erro)), severity="error")
            return
        if self._criada is not None and m.por_nome(self._criada) is not None:
            self.tabela_id, self.filtro = m.por_nome(self._criada).id, filtro.Filtro()
        self._criada = None
        self._calada((s or self.sessao).aplicado, m)
        self._montar()
        self._pedir_grade()
