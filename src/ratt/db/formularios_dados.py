"""Os formulários da tela Dados. Nesta etapa: `FormCondicao` (uma condição do filtro, montada sem digitar SQL) e
`CampoValor` (o campo de um valor, que entende o tipo da coluna).

Nada aqui fala com o banco: o formulário devolve uma `filtro.Condicao`; a tela é quem lê as linhas. O WHERE que
a condição gera aparece enquanto ela é montada — com os nomes do BANCO (a base do rascunho), que é onde ele roda.
Coluna ou ligação que só existe no rascunho é recusada com o motivo: o banco ainda não a tem.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Select, Static

from ratt.db import filtro
from ratt.db import rascunho as rs
from ratt.db.formularios import Corpo
from ratt.db.idioma import t
from ratt.db.modelo import Modelo
from ratt.db.tela import COR

EXISTE = "__existe__"           # a "coluna" que abre o "existe em outra tabela"
_SEM_VALOR = {"vazio", "preenchido", "verdadeiro", "falso"}
_LISTA = {"na_lista", "fora_lista"}
_INTERVALO = {"ha_mais_de", "nos_ultimos"}
_LIKE = {"contem", "comeca", "termina"}
_COMPARA = {"igual", "diferente", "menor", "menor_igual", "maior", "maior_igual", "antes", "depois"}  # aceitam outra coluna
_POR_NOME = {"mes": ("filtro.mes", 12), "dia_semana": ("filtro.dsem", 7)}


def nomes_para_numeros(texto: str, transformacao: str) -> str:
    """Com mês ou dia da semana, aceita o nome ("junho") e devolve o número ("6"); o resto passa como está."""
    if transformacao not in _POR_NOME:
        return texto
    prefixo, n = _POR_NOME[transformacao]
    limpo = texto.strip().lower()
    for i in range(1, n + 1):
        if t(f"{prefixo}.{i}").lower() == limpo:
            return str(i)
    return texto.strip()


class CampoValor(Vertical):
    """Um valor para uma coluna: texto livre num `Input` (id `nome`); com mês ou dia da semana, escolhido pelo nome
    num `Select` (id `nome_escolha`) e guardado como número — o que `filtro` espera."""

    DEFAULT_CSS = """
    CampoValor { height: auto; }
    CampoValor Select { display: none; }
    """

    def __init__(self, nome: str, valor: str = ""):
        super().__init__(id=f"campo_{nome}")
        self.nome, self._inicial = nome, valor
        self.transformacao = ""
        self._escolhas: List[str] = []      # os valores do Select (o que se pode pôr nele)

    def compose(self) -> ComposeResult:
        yield Input(value=self._inicial, id=self.nome)
        yield Select([], id=f"{self.nome}_escolha", prompt=t("dados.f.valor"))

    def configurar(self, transformacao: str, dica: str = "") -> None:
        """Escolhe o jeito do campo para a transformação (mês e dia da semana viram uma lista de nomes)."""
        entrada, escolha = self.query_one(Input), self.query_one(Select)
        entrada.placeholder = dica
        if transformacao == self.transformacao:
            return
        self.transformacao = transformacao
        por_nome = transformacao in _POR_NOME
        if por_nome:
            prefixo, n = _POR_NOME[transformacao]
            self._escolhas = [str(i) for i in range(1, n + 1)]
            escolha.set_options([(t(f"{prefixo}.{i}"), str(i)) for i in range(1, n + 1)])
        entrada.display, escolha.display = not por_nome, por_nome

    @property
    def valor(self) -> str:
        if self.transformacao in _POR_NOME:
            escolha = self.query_one(Select)
            return "" if escolha.is_blank() else str(escolha.value)
        return self.query_one(Input).value

    @valor.setter
    def valor(self, texto: str) -> None:
        if self.transformacao in _POR_NOME:
            escolha = self.query_one(Select)
            numero = nomes_para_numeros(texto, self.transformacao)
            if numero in self._escolhas:
                escolha.value = numero
        else:
            self.query_one(Input).value = texto


class FormCondicao(ModalScreen):
    """coluna → transformação (opcional) → operador → valor(es), NÃO, diferenciar maiúsculas; ou "existe em outra
    tabela" por uma ligação, com condições sobre a outra tabela (outro `FormCondicao`, um nível só). Devolve a
    `filtro.Condicao` (None no cancelar). `vista` dá os nomes que a pessoa vê; `base`, os do banco."""

    BINDINGS = [Binding("escape", "cancelar", t("canvas.f.cancelar"))]
    DEFAULT_CSS = """
    FormCondicao { align: center middle; }
    FormCondicao > Vertical { width: 84; max-width: 100%; height: auto; max-height: 100%; border: round $primary;
                              background: $surface; padding: 0 1; }
    FormCondicao #form_titulo { text-style: bold; padding: 0 0 1 0; }
    FormCondicao #form_corpo, FormCondicao #grupo_comum, FormCondicao #grupo_existe { height: auto; }
    FormCondicao #grupo_existe { display: none; }
    FormCondicao Input { height: 3; }
    FormCondicao #previa, FormCondicao #erro, FormCondicao #internas { height: auto; margin-top: 1; }
    FormCondicao #form_botoes, FormCondicao #botoes_internas { height: 3; margin-top: 1; }
    FormCondicao #form_botoes { align-horizontal: right; }
    FormCondicao Button { margin-left: 1; }
    """

    def __init__(self, vista: Modelo, base: Modelo, tabela_id: str, inicial: Optional[filtro.Condicao] = None,
                 nivel: int = 0):
        super().__init__()
        self.vista, self.base, self.tabela_id, self.inicial, self.nivel = vista, base, tabela_id, inicial, nivel
        self.tab = vista.tabelas[tabela_id]
        self.internas: List[filtro.Condicao] = []
        self._coluna: Optional[str] = None      # a coluna para a qual as listas de transformação/operador valem
        self._tr: Optional[str] = None
        self._ligacao: Optional[str] = None     # trocar a ligação esvazia as condições de dentro (pôr a mesma, não)
        self._opcoes: Dict[str, List[str]] = {}   # os valores de cada Select, como foram postos

    # ── as listas ──

    def _na_base(self, id: str) -> bool:
        return rs._nome_na_base(self.base, id) is not None

    def _opcoes_coluna(self):
        opcoes = []
        for c in self.tab.colunas:
            rotulo = Text.assemble((c.nome, "bold"), (f"  {c.tipo}", COR["cinza"]))
            if not self._na_base(c.id):
                rotulo.append(t("dados.f.so_depois"), style=COR["amarelo"])
            opcoes.append((rotulo, c.id))
        if self.nivel == 0 and self._ligacoes():
            opcoes.append((t("dados.f.existe"), EXISTE))
        return opcoes

    def _ligacoes(self):
        saida = []
        for lig in sorted(self.vista.ligacoes.values(), key=lambda k: (k.nome, k.id)):
            if lig.estado == "apagado" or self.tabela_id not in (lig.origem, lig.destino):
                continue
            sai = lig.origem == self.tabela_id
            outra = self.vista.tabelas.get(lig.destino if sai else lig.origem)
            if outra is None:
                continue
            dela = self.vista.tabelas.get(lig.origem)
            colunas = ", ".join(dela.coluna(i).nome if dela and dela.coluna(i) else i for i in lig.colunas)
            rotulo = t("dados.f.sai" if sai else "dados.f.chega", tabela=outra.nome, colunas=colunas)
            if not self._na_base(lig.id):
                rotulo += t("dados.f.so_depois")
            saida.append((rotulo, lig.id))
        return saida

    def _outra(self, ligacao_id: str) -> Optional[str]:
        lig = self.vista.ligacoes.get(ligacao_id)
        if lig is None:
            return None
        return lig.destino if lig.origem == self.tabela_id else lig.origem

    # ── montar ──

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(t("dados.f.condicao", tabela=self.tab.nome)), id="form_titulo")
            with Corpo(id="form_corpo"):
                yield Static(t("dados.f.coluna"))
                yield Select(self._lembrar("coluna", self._opcoes_coluna()), id="coluna", prompt=t("dados.f.coluna"))
                with Vertical(id="grupo_comum"):
                    yield Static(t("dados.f.transformacao"))
                    yield Select([], id="transformacao", prompt=t("dados.f.sem_transformacao"))
                    yield Static(t("dados.f.operador"))
                    yield Select([], id="operador", prompt=t("dados.f.operador"))
                    yield Static(t("dados.f.valor"), id="rotulo_valor")
                    yield Checkbox(t("dados.f.usar_outra"), id="usar_outra")
                    yield Select([], id="outra_coluna", prompt=t("dados.f.outra_coluna"))
                    yield CampoValor("valor")
                    yield CampoValor("valor2")
                    yield Select([(t(f"filtro.un.{u}"), u) for u in ("dias", "meses", "anos")], value="dias",
                                 allow_blank=False, id="unidade")
                    yield Checkbox(t("dados.f.vazio_lista"), id="vazio_lista")
                    yield Checkbox(t("dados.f.diferenciar"), id="diferenciar")
                with Vertical(id="grupo_existe"):
                    yield Static(t("dados.f.ligacao"))
                    yield Select(self._lembrar("ligacao", self._ligacoes()), id="ligacao", prompt=t("dados.f.ligacao"))
                    yield Static(id="internas")
                    with Horizontal(id="botoes_internas"):
                        yield Button(t("dados.f.mais_interna"), id="mais_interna")
                        yield Button(t("dados.f.menos_interna"), id="menos_interna")
                yield Checkbox(t("dados.f.nao"), id="negar")
                yield Static(id="previa")
                yield Static(id="erro")
            with Horizontal(id="form_botoes"):
                yield Button(t("dados.f.usar"), id="ok", variant="primary")
                yield Button(t("canvas.f.cancelar"), id="cancelar")

    def on_mount(self) -> None:
        self.query_one("#form_corpo").styles.max_height = max(4, self.size.height - 10)
        c = self.inicial
        if c is not None:
            if c.existe is not None and self.nivel == 0:
                self.query_one("#coluna", Select).value = EXISTE
                if self._tem("#ligacao", c.existe.ligacao):
                    self._ligacao = c.existe.ligacao        # a mudança programada não esvazia as de dentro
                    self.query_one("#ligacao", Select).value = c.existe.ligacao
                self.internas = list(c.existe.condicoes)
                self.query_one("#negar", Checkbox).value = c.existe.nao
            elif self.tab.coluna(c.coluna) is not None:
                self.query_one("#coluna", Select).value = c.coluna
                self._trocar_coluna(c.coluna)
                if c.transformacao and self._tem("#transformacao", c.transformacao):
                    self.query_one("#transformacao", Select).value = c.transformacao
                    self._trocar_tr(c.transformacao)
                if self._tem("#operador", c.operador):
                    self.query_one("#operador", Select).value = c.operador
                self._preencher(c)
                if c.outra_coluna is not None and self._tem("#outra_coluna", c.outra_coluna):
                    self.query_one("#usar_outra", Checkbox).value = True
                    self.query_one("#outra_coluna", Select).value = c.outra_coluna
                self.query_one("#negar", Checkbox).value = c.negar
                self.query_one("#diferenciar", Checkbox).value = c.diferenciar
        self._atualizar()

    def _lembrar(self, nome: str, opcoes):
        self._opcoes[nome] = [v for _, v in opcoes]
        return opcoes

    def _por_opcoes(self, nome: str, opcoes) -> None:
        self.query_one("#" + nome, Select).set_options(self._lembrar(nome, opcoes))

    def _tem(self, seletor: str, valor: str) -> bool:
        return valor in self._opcoes.get(seletor.lstrip("#"), [])

    def _preencher(self, c: filtro.Condicao) -> None:
        tr = c.transformacao
        for campo in (self.query_one("#campo_valor", CampoValor), self.query_one("#campo_valor2", CampoValor)):
            campo.configurar(tr)
        v = list(c.valores)
        if c.operador in _LISTA:
            self.query_one("#vazio_lista", Checkbox).value = "" in v
            self.query_one("#valor", Input).value = ", ".join(x for x in v if x != "")
        elif c.operador in _INTERVALO and len(v) == 2:
            self.query_one("#valor", Input).value = v[0]
            if v[1] in ("dias", "meses", "anos"):
                self.query_one("#unidade", Select).value = v[1]
        elif v:
            self.query_one("#campo_valor", CampoValor).valor = v[0]
            if len(v) > 1:
                self.query_one("#campo_valor2", CampoValor).valor = v[1]

    def _trocar_coluna(self, coluna_id) -> None:
        if coluna_id == self._coluna:
            return
        self._coluna, self._tr = coluna_id, None
        col = self.tab.coluna(coluna_id) if isinstance(coluna_id, str) else None
        trs = filtro.transformacoes(col.tipo) if col is not None else []
        self._por_opcoes("transformacao", [(t(f"filtro.tr.{x}"), x) for x in trs])
        self._trocar_tr("")

    def _trocar_tr(self, transformacao: str) -> None:
        if transformacao == self._tr:
            return
        self._tr = transformacao
        col = self.tab.coluna(self._coluna) if isinstance(self._coluna, str) else None
        ops = filtro.operadores(col.tipo, transformacao) if col is not None else []
        seletor = self.query_one("#operador", Select)
        antes = None if seletor.is_blank() else seletor.value
        self._por_opcoes("operador", [(t(f"filtro.op.{o}"), o) for o in ops])
        if antes in ops:
            seletor.value = antes
        self._por_opcoes("outra_coluna", self._opcoes_outra(col, transformacao))

    def _opcoes_outra(self, col, transformacao: str):
        """As outras colunas da mesma família (depois da transformação) — "valor: outra coluna da mesma linha"."""
        if col is None:
            return []
        familia = filtro._familia_depois(col.tipo, transformacao)
        saida = []
        for c in self.tab.colunas:
            if c.estado == "apagado" or (c.id == col.id and not transformacao) or filtro.familia(c.tipo) != familia:
                continue
            rotulo = Text.assemble((c.nome, "bold"), (f"  {c.tipo}", COR["cinza"]))
            if not self._na_base(c.id):
                rotulo.append(t("dados.f.so_depois"), style=COR["amarelo"])
            saida.append((rotulo, c.id))
        return saida

    # ── ler os campos ──

    def _escolha(self, seletor: str) -> Optional[str]:
        s = self.query_one(seletor, Select)
        return None if s.is_blank() else str(s.value)

    def condicao(self) -> filtro.Condicao:
        """A condição como está nos campos; ValueError diz o que falta ou o que não serve."""
        coluna = self._escolha("#coluna")
        if coluna is None:
            raise ValueError(t("dados.e.coluna"))
        negar = self.query_one("#negar", Checkbox).value
        if coluna == EXISTE:
            lig = self._escolha("#ligacao")
            if lig is None:
                raise ValueError(t("dados.e.ligacao"))
            return filtro.Condicao("", "existe", existe=filtro.Existe(lig, list(self.internas), negar))
        if not self._na_base(coluna):           # o motivo vem já ao escolher, antes de pedir o resto
            raise ValueError(t("dados.e.so_depois", nome=rs._nome_de(coluna, self.vista, self.base)))
        op = self._escolha("#operador")
        if op is None:
            raise ValueError(t("dados.e.operador"))
        tr = self._escolha("#transformacao") or ""
        if op in _COMPARA and self.query_one("#usar_outra", Checkbox).value:
            outra = self._escolha("#outra_coluna")
            if outra is None:
                raise ValueError(t("dados.e.outra_coluna"))
            return filtro.Condicao(coluna, op, [], tr, negar, outra_coluna=outra)
        primeiro = self.query_one("#campo_valor", CampoValor).valor
        if op in _SEM_VALOR:
            valores: List[str] = []
        elif op in _LISTA:
            # a vírgula separa e o espaço depois dela é só costume: cada item sai aparado
            valores = [nomes_para_numeros(x, tr).strip() for x in primeiro.split(",") if x.strip()]
            if self.query_one("#vazio_lista", Checkbox).value:
                valores.append("")
        elif op == "entre":
            valores = [primeiro, self.query_one("#campo_valor2", CampoValor).valor]
        elif op in _INTERVALO:
            valores = [primeiro.strip(), str(self.query_one("#unidade", Select).value)]
        else:
            valores = [primeiro]
        diferenciar = op in _LIKE and self.query_one("#diferenciar", Checkbox).value
        return filtro.Condicao(coluna, op, valores, tr, negar, diferenciar)

    def validar(self, c: filtro.Condicao) -> str:
        """Recusa o que o banco ainda não tem e devolve o fragmento do WHERE (com os nomes do banco)."""
        for i in sorted(filtro.refere(filtro.Filtro([c]), self.tabela_id) - {self.tabela_id}):
            if not self._na_base(i):
                raise ValueError(t("dados.e.so_depois", nome=rs._nome_de(i, self.vista, self.base)))
        return filtro.where(filtro.Filtro([c]), self.base, self.tabela_id)

    # ── mostrar ──

    def _atualizar(self) -> None:
        coluna = self._escolha("#coluna")
        existe = coluna == EXISTE
        self.query_one("#grupo_comum").display = not existe
        self.query_one("#grupo_existe").display = existe
        if not existe:
            self._trocar_coluna(coluna)
            self._trocar_tr(self._escolha("#transformacao") or "")
            self._mostrar_campos()
        else:
            self._mostrar_internas()
        previa, erro = self.query_one("#previa", Static), self.query_one("#erro", Static)
        try:
            c = self.condicao()
            fragmento = self.validar(c)
        except ValueError as e:
            previa.update("")
            erro.update(Text(str(e), style=COR["laranja"]))
            return
        erro.update("")
        previa.update(Text.assemble((filtro.descricao_condicao(c, self.vista, self.tabela_id), "bold"), "\n",
                                    ("WHERE ", COR["azul"]), fragmento))

    def _mostrar_campos(self) -> None:
        op = self._escolha("#operador") or ""
        tr = self._tr or ""
        um, dois = self.query_one("#campo_valor", CampoValor), self.query_one("#campo_valor2", CampoValor)
        lista, intervalo = op in _LISTA, op in _INTERVALO
        # a lista é digitada (nomes de mês servem); o intervalo é um número de dias/meses/anos
        um.configurar("" if (lista or intervalo) else tr,
                      t("dados.f.lista") if lista else t("dados.f.quantos") if intervalo else "")
        dois.configurar(tr)
        outra = op in _COMPARA and self.query_one("#usar_outra", Checkbox).value
        self.query_one("#usar_outra").display = op in _COMPARA
        self.query_one("#outra_coluna").display = outra
        um.display = bool(op) and op not in _SEM_VALOR and not outra
        dois.display = op == "entre"
        self.query_one("#rotulo_valor").display = um.display
        self.query_one("#unidade").display = intervalo
        self.query_one("#vazio_lista").display = lista
        self.query_one("#diferenciar").display = op in _LIKE

    def _mostrar_internas(self) -> None:
        lig = self._escolha("#ligacao")
        outra = self._outra(lig) if lig else None
        alvo = self.query_one("#internas", Static)
        self.query_one("#botoes_internas").display = outra is not None
        if outra is None:
            alvo.update("")
            return
        if not self.internas:
            texto = Text(f"{t('dados.f.internas')} {t('dados.f.nenhuma_interna')}", style=COR["cinza"])
        else:
            texto = Text(t("dados.f.internas") + "\n")
            for c in self.internas:
                texto.append("  • " + filtro.descricao_condicao(c, self.vista, outra) + "\n")
        alvo.update(texto)

    # ── eventos ──

    def on_select_changed(self, evento: Select.Changed) -> None:
        if evento.select.id == "ligacao":
            nova = None if evento.select.is_blank() else str(evento.value)
            if nova != self._ligacao:
                self._ligacao, self.internas = nova, []
        self._atualizar()

    def on_input_changed(self, evento) -> None:
        self._atualizar()

    def on_checkbox_changed(self, evento) -> None:
        self._atualizar()

    def on_input_submitted(self, evento) -> None:
        self._usar()

    def on_button_pressed(self, evento: Button.Pressed) -> None:
        evento.stop()
        botao = evento.button.id
        if botao == "ok":
            self._usar()
        elif botao == "cancelar":
            self.dismiss(None)
        elif botao == "mais_interna":
            outra = self._outra(self._escolha("#ligacao") or "")
            if outra is not None:
                self.app.push_screen(FormCondicao(self.vista, self.base, outra, nivel=self.nivel + 1),
                                     self._interna_chegou)
        elif botao == "menos_interna" and self.internas:
            self.internas.pop()
            self._atualizar()

    def _interna_chegou(self, c: Optional[filtro.Condicao]) -> None:
        if c is not None:
            self.internas.append(c)
        self._atualizar()

    def action_cancelar(self) -> None:
        self.dismiss(None)

    def _usar(self) -> None:
        try:
            c = self.condicao()
            self.validar(c)
        except ValueError as e:
            self.query_one("#erro", Static).update(Text(str(e), style=COR["laranja"]))
            return
        self.dismiss(c)
