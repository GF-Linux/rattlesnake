"""Os formulários da tela Dados: `FormCondicao` (uma condição do filtro, montada sem digitar SQL), `CampoValor` (o
campo de um valor, que entende o tipo da coluna), `EscolherLinha` (a lista das linhas do pai, para uma chave
estrangeira) e `FormInserir` (uma linha nova, um campo por coluna).

O `FormCondicao` não fala com o banco: devolve uma `filtro.Condicao`; a tela é quem lê as linhas. O WHERE que a
condição gera aparece enquanto ela é montada — com os nomes do BANCO (a base do rascunho), que é onde ele roda.
Coluna ou ligação que só existe no rascunho é recusada com o motivo: o banco ainda não a tem.

O `FormInserir` é um formulário do canvas (`FormBase`): SQL à vista, avisos contados numa thread (só leitura) e o
botão sempre ligado — a pessoa decide. Ele lê o banco pela fonte da tela com a trava dela (`FonteTravada`): uma
conversa por vez na conexão da tela.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from ratt.db import filtro
from ratt.db import rascunho as rs
from ratt.db.formularios import _ATRASO, Corpo, FormBase
from ratt.db.idioma import t
from ratt.db.modelo import Coluna, Ligacao, Modelo
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
    """O valor de uma coluna, de dois jeitos.

    No filtro (`CampoValor("valor")`): texto livre num `Input` (id `nome`); com mês ou dia da semana, escolhido pelo
    nome num `Select` (id `nome_escolha`) e guardado como número — o que `filtro` espera. `valor()` é sempre texto.

    No inserir (`CampoValor(coluna, referencia)`): um campo que entende o tipo — booleano num `Select` sim/não/vazio,
    data e número num `Input` com a dica do formato, chave estrangeira (de uma coluna) num `Input` mais um botão que
    pede a lista das linhas do pai (`Escolher`; quem abre a lista é o formulário, que conhece a fonte). Identidade e
    coluna gerada: desativado, o banco preenche. `valor()` é None quando vazio — a coluna sai do INSERT (com
    `padrao`, o banco põe o padrão; sem, fica vazia). A validação de verdade é a dos avisos (no banco)."""

    class Escolher(Message):
        """O botão da chave estrangeira: o formulário abre `EscolherLinha` e devolve com `escolhido`."""

        def __init__(self, campo: "CampoValor"):
            super().__init__()
            self.campo = campo

    DEFAULT_CSS = """
    CampoValor { height: auto; }
    CampoValor .por_nome { display: none; }
    CampoValor .rotulo { margin-top: 1; }
    CampoValor .linha_fk { height: auto; }
    CampoValor .linha_fk Input { width: 1fr; }
    CampoValor .escolher { width: auto; min-width: 12; margin-left: 1; }
    CampoValor .escolhido { height: auto; }
    """

    def __init__(self, coluna, referencia: Optional[Ligacao] = None, valor: str = "", id: Optional[str] = None,
                 destino: str = "", lista: bool = True, motivo: str = ""):
        """`destino`: "tabela.coluna" do pai, para o rótulo; `lista=False` (com o `motivo`) quando o pai ainda não
        está no banco e não há linhas para listar — digitar continua valendo."""
        filtro_ = isinstance(coluna, str)
        super().__init__(id=id or (f"campo_{coluna}" if filtro_ else None))
        self.nome: Optional[str] = coluna if filtro_ else None
        self.coluna: Optional[Coluna] = None if filtro_ else coluna
        self.referencia, self.destino, self.lista, self.motivo = referencia, destino, lista, motivo
        self._inicial = valor
        self.transformacao = ""
        self._escolhas: List[str] = []      # os valores do Select (o que se pode pôr nele)
        self._escolhido: Optional[tuple] = None     # (valor, rótulo) da lista do pai
        c = self.coluna
        self.desativado = c is not None and (c.identidade or c.gerada)
        self.familia = filtro.familia(c.tipo) if c is not None else ""

    # ── o jeito do campo ──

    def _booleano(self) -> bool:
        return self.coluna is not None and self.familia == "booleano" and not self.desativado

    def _fk(self) -> bool:
        return self.coluna is not None and self.referencia is not None and not self.desativado

    def dica(self) -> str:
        """O que aparece no campo vazio: o formato esperado e o que o vazio significa."""
        c = self.coluna
        if c is None:
            return ""
        if self.desativado:
            return t("dados.banco_preenche")
        partes = []
        if self.familia == "data":
            partes.append(t("dados.f.data") if filtro.tipo_normal(c.tipo) == "date" else t("dados.f.data_hora"))
        elif self.familia == "numero":
            partes.append(t("dados.f.numero"))
        if c.padrao is not None:
            partes.append(t("dados.f.padrao", padrao=c.padrao))
        return " · ".join(partes)

    def _rotulo(self) -> Text:
        c = self.coluna
        texto = Text.assemble((c.nome, "bold"), (f"  {c.tipo}", COR["cinza"]))
        if c.obrigatoria and c.padrao is None and not self.desativado:
            texto.append(f"  * {t('dados.f.obrigatoria')}", style=COR["laranja"])
        if self.destino:
            tabela, _, coluna = self.destino.rpartition(".")
            texto.append("  " + t("dados.f.referencia", tabela=tabela, coluna=coluna), style=COR["azul"])
        return texto

    def compose(self) -> ComposeResult:
        if self.coluna is None:
            yield Input(value=self._inicial, id=self.nome)
            yield Select([], id=f"{self.nome}_escolha", prompt=t("dados.f.valor"), classes="por_nome")
            return
        yield Static(self._rotulo(), classes="rotulo")
        if self._booleano():
            vazio = t("dados.f.padrao", padrao=self.coluna.padrao) if self.coluna.padrao is not None else t("dados.f.vazio")
            yield Select([(t("dados.f.sim"), "true"), (t("dados.f.nao_bool"), "false")], prompt=vazio)
        elif self._fk():
            with Horizontal(classes="linha_fk"):
                yield Input(value=self._inicial, placeholder=self.dica())
                yield Button(t("dados.f.escolher"), classes="escolher", disabled=not self.lista,
                             id=f"{self.id}_escolher" if self.id else None)
            yield Static(Text(self.motivo, style=COR["cinza"]) if not self.lista else "", classes="escolhido")
        else:
            yield Input(value=self._inicial, placeholder=self.dica(), disabled=self.desativado)

    def configurar(self, transformacao: str, dica: str = "") -> None:
        """(Filtro) Escolhe o jeito do campo para a transformação (mês e dia da semana viram uma lista de nomes)."""
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

    # ── ler e escrever ──

    def valor(self) -> Optional[str]:
        """No filtro, o texto (vazio é ""). No inserir, None quando vazio ou desativado."""
        if self.coluna is None:
            if self.transformacao in _POR_NOME:
                escolha = self.query_one(Select)
                return "" if escolha.is_blank() else str(escolha.value)
            return self.query_one(Input).value
        if self.desativado:
            return None
        if self._booleano():
            escolha = self.query_one(Select)
            return None if escolha.is_blank() else str(escolha.value)
        texto = self.query_one(Input).value
        return None if texto == "" else texto

    def preenchido(self) -> bool:
        return bool(self.valor()) if self.coluna is None else self.valor() is not None

    def definir(self, texto: str) -> None:
        if self.coluna is None and self.transformacao in _POR_NOME:
            escolha = self.query_one(Select)
            numero = nomes_para_numeros(texto, self.transformacao)
            if numero in self._escolhas:
                escolha.value = numero
        elif self._booleano():
            self.query_one(Select).value = "true" if texto.strip().lower() in filtro._SIM else "false"
        elif not self.desativado:
            self.query_one(Input).value = texto

    def limpar(self) -> None:
        if self.desativado:
            return
        if self._booleano():
            self.query_one(Select).clear()
            return
        self._escolhido = None
        self.query_one(Input).value = ""
        if self._fk() and self.lista:
            self.query_one(".escolhido", Static).update("")

    def focar(self) -> bool:
        """Põe o foco no campo; False se ele está desativado."""
        if self.desativado:
            return False
        (self.query_one(Select) if self._booleano() else self.query_one(Input)).focus()
        return True

    def escolhido(self, valor: str, rotulo: Optional[str]) -> None:
        """Chegou a linha escolhida na lista do pai: o valor vai para o campo e o rótulo aparece ao lado."""
        self._escolhido = (valor, rotulo)
        self.query_one(".escolhido", Static).update(Text("→ " + (rotulo or valor), style=COR["verde"]))
        self.query_one(Input).value = valor

    # ── eventos ──

    def on_input_changed(self, evento: Input.Changed) -> None:
        # não para o evento: o formulário também precisa dele (prévia e avisos)
        if self._escolhido is not None and evento.value != self._escolhido[0]:
            self._escolhido = None
            self.query_one(".escolhido", Static).update("")

    def on_button_pressed(self, evento: Button.Pressed) -> None:
        if evento.button.has_class("escolher"):
            evento.stop()
            self.post_message(self.Escolher(self))


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
            self.query_one("#campo_valor", CampoValor).definir(v[0])
            if len(v) > 1:
                self.query_one("#campo_valor2", CampoValor).definir(v[1])

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
        primeiro = self.query_one("#campo_valor", CampoValor).valor()
        if op in _SEM_VALOR:
            valores: List[str] = []
        elif op in _LISTA:
            # a vírgula separa e o espaço depois dela é só costume: cada item sai aparado
            valores = [nomes_para_numeros(x, tr).strip() for x in primeiro.split(",") if x.strip()]
            if self.query_one("#vazio_lista", Checkbox).value:
                valores.append("")
        elif op == "entre":
            valores = [primeiro, self.query_one("#campo_valor2", CampoValor).valor()]
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


# ── inserir ──

class FonteTravada:
    """A fonte da tela com a trava dela: o formulário (numa thread) e a vigia da tela conversam com o banco pela
    mesma conexão, uma conversa por vez. Cada pergunta da `consulta()` e cada `linhas_pai` pega a trava."""

    def __init__(self, fonte, trava):
        self._fonte, self._trava = fonte, trava

    def __getattr__(self, nome):
        return getattr(self._fonte, nome)

    def consulta(self):
        return _ConsultaTravada(self._fonte.consulta(), self._trava)

    def linhas_pai(self, *args, **kw):
        with self._trava:
            return self._fonte.linhas_pai(*args, **kw)


class _ConsultaTravada:
    def __init__(self, consulta, trava):
        self._consulta, self._trava = consulta, trava

    def __getattr__(self, nome):
        alvo = getattr(self._consulta, nome)
        if not callable(alvo):
            return alvo

        def travado(*args, **kw):
            with self._trava:
                return alvo(*args, **kw)
        return travado


def _como_texto(valor) -> str:
    """O valor da chave como a pessoa o digitaria (vira literal pelo tipo da coluna, como o digitado)."""
    if isinstance(valor, bool):
        return "true" if valor else "false"
    iso = getattr(valor, "isoformat", None)
    return iso() if callable(iso) else str(valor)


class EscolherLinha(ModalScreen):
    """As linhas de uma tabela pai (`catalogo.linhas_pai`: a chave e as primeiras colunas de texto), com busca.
    Lê numa thread, a busca espera a pessoa parar de digitar; resposta de uma busca velha é descartada. Devolve o
    valor da chave como texto (None no esc); o rótulo da escolhida fica em `rotulo_escolhido`."""

    LIMITE = 50
    BINDINGS = [Binding("escape", "cancelar", t("canvas.f.cancelar"))]
    DEFAULT_CSS = """
    EscolherLinha { align: center middle; }
    EscolherLinha > Vertical { width: 76; max-width: 100%; height: auto; max-height: 90%; border: round $primary;
                               background: $surface; padding: 0 1; }
    EscolherLinha #escolher_titulo { text-style: bold; padding: 0 0 1 0; }
    EscolherLinha Input { height: 3; }
    EscolherLinha #estado { height: auto; }
    EscolherLinha OptionList { height: auto; max-height: 18; }
    """

    def __init__(self, fonte, esquema: str, tabela: str, chave: str):
        super().__init__()
        self.fonte, self.esquema, self.tabela, self.chave = fonte, esquema, tabela, chave
        self.linhas: List[tuple] = []
        self.rotulo_escolhido: Optional[str] = None
        self._versao = 0
        self._timer = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(t("dados.f.escolher_em", tabela=f"{self.esquema}.{self.tabela}")), id="escolher_titulo")
            yield Input(placeholder=t("dados.f.buscar"), id="busca")
            yield Static(id="estado")
            yield OptionList(id="linhas")

    def on_mount(self) -> None:
        self._buscar()

    def on_input_changed(self, evento: Input.Changed) -> None:
        evento.stop()
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_timer(_ATRASO, self._buscar)

    def on_input_submitted(self, evento: Input.Submitted) -> None:
        evento.stop()
        destacada = self.query_one(OptionList).highlighted
        self._escolher(destacada if destacada is not None else 0)

    def _buscar(self) -> None:
        self._timer = None
        self._versao += 1
        versao, busca = self._versao, self.query_one("#busca", Input).value
        self.query_one("#estado", Static).update(Text(t("dados.f.buscando"), style=COR["cinza"]))

        def trabalho():
            try:
                linhas = self.fonte.linhas_pai(self.esquema, self.tabela, self.chave, busca, limite=self.LIMITE)
            except Exception as e:                  # o banco caiu, faltou permissão...: vira uma linha, não um erro
                linhas = e
            try:
                self.app.call_from_thread(self._chegaram, versao, linhas)
            except RuntimeError:                    # a app já fechou
                pass
        self.run_worker(trabalho, thread=True, exclusive=True, group="linhas_pai")

    def _chegaram(self, versao: int, linhas) -> None:
        if not self.is_attached or versao != self._versao:
            return
        estado, lista = self.query_one("#estado", Static), self.query_one(OptionList)
        lista.clear_options()
        self.linhas = []
        if linhas is None:
            estado.update(Text(t("dados.f.sem_tempo_busca"), style=COR["amarelo"]))
            return
        if isinstance(linhas, Exception):
            primeira = (str(linhas).strip().splitlines() or [type(linhas).__name__])[0]
            estado.update(Text(t("canvas.erro_banco", erro=primeira), style=COR["laranja"]))
            return
        self.linhas = list(linhas)
        lista.add_options([Option(Text.assemble((_como_texto(v), "bold"), "  ", str(r) if r is not None else ""))
                           for v, r in self.linhas])
        if self.linhas:
            lista.highlighted = 0
        if not self.linhas:
            estado.update(Text(t("dados.f.nenhuma_linha"), style=COR["cinza"]))
        elif len(self.linhas) >= self.LIMITE:
            estado.update(Text(t("dados.f.primeiras", n=self.LIMITE), style=COR["cinza"]))
        else:
            estado.update("")

    def on_option_list_option_selected(self, evento: OptionList.OptionSelected) -> None:
        evento.stop()
        self._escolher(evento.option_index)

    def _escolher(self, i: int) -> None:
        if 0 <= i < len(self.linhas):
            valor, rotulo = self.linhas[i]
            self.rotulo_escolhido = None if rotulo is None else str(rotulo)
            self.dismiss(_como_texto(valor))

    def action_cancelar(self) -> None:
        self.dismiss(None)


class FormInserir(FormBase):
    """Uma linha nova: um `CampoValor` por coluna, na ordem da tabela (a do rascunho: inserir numa tabela criada
    nele vale). Uma linha = uma operação `Inserir` = um INSERT. "pôr e inserir outra" acrescenta, limpa os campos e
    fica aberto. A operação entra pela sessão (grava e avisa as telas na hora, também com o formulário aberto)."""

    avisa_sem_sql = True

    def __init__(self, sessao, fonte, tabela_id: str):
        self.sessao = sessao
        super().__init__(sessao.rascunho, fonte)
        self.tabela_id = tabela_id
        self.titulo = t("dados.f.inserir", tabela=self.rascunho.vista().tabelas[tabela_id].nome)
        self._campos: List[CampoValor] = []

    @property
    def rascunho(self) -> rs.Rascunho:
        return self.sessao.rascunho         # o de agora: a sessão pode trocá-lo (releitura do disco, aplicar)

    def _referencia(self, vista: Modelo, c: Coluna) -> Optional[Ligacao]:
        """A chave estrangeira de uma coluna só que sai desta coluna (a primeira, se houver mais de uma)."""
        for lig in sorted(vista.ligacoes.values(), key=lambda k: k.id):
            if lig.estado != "apagado" and lig.origem == self.tabela_id and tuple(lig.colunas) == (c.id,):
                return lig
        return None

    def campos(self) -> ComposeResult:
        vista = self.rascunho.vista()
        tab = vista.tabelas[self.tabela_id]
        for i, c in enumerate(col for col in tab.colunas if col.estado != "apagado"):
            lig = self._referencia(vista, c)
            destino, no_banco, motivo = "", True, ""
            if lig is not None:
                pai = vista.tabelas[lig.destino]
                col_pai = pai.coluna(lig.colunas_destino[0])
                destino = f"{pai.nome}.{col_pai.nome if col_pai else '?'}"
                no_banco = self._pai_no_banco(lig) is not None
                motivo = "" if no_banco else t("dados.f.pai_so_depois", tabela=pai.nome)
            campo = CampoValor(c, lig, id=f"campo_{i}", destino=destino, lista=no_banco, motivo=motivo)
            self._campos.append(campo)
            yield campo

    def _pai_no_banco(self, lig: Ligacao):
        """(esquema, tabela, coluna) do pai como o banco os chama, ou None se o pai ainda não está lá."""
        pai = self.rascunho.base.tabelas.get(lig.destino)
        col = pai.coluna(lig.colunas_destino[0]) if pai is not None else None
        return (pai.esquema, pai.nome, col.nome) if col is not None else None

    def botoes(self) -> ComposeResult:
        yield Button(t("dados.f.por_e_outra"), id="por_e_outra")

    def on_mount(self) -> None:
        # (o Textual chama também o on_mount do FormBase: prévia e avisos)
        any(c.focar() for c in self._campos)

    def campo(self, nome: str) -> CampoValor:
        """O campo da coluna `nome` (como a pessoa a vê)."""
        return next(c for c in self._campos if c.coluna.nome == nome)

    def operacao(self) -> rs.Operacao:
        valores: Dict[str, Optional[str]] = {}
        for campo in self._campos:
            v = campo.valor()
            if v is not None:
                valores[campo.coluna.id] = v
        return rs.Inserir(self.tabela_id, valores)

    def acrescentar(self, op: rs.Operacao) -> None:
        self.sessao.acrescentar(op)

    def on_button_pressed(self, evento: Button.Pressed) -> None:
        # os outros botões são do FormBase, cujo handler o Textual chama depois deste (não chamar super aqui)
        if evento.button.id != "por_e_outra":
            return
        evento.stop()
        if self.por_sem_fechar() is None:
            return
        for campo in self._campos:
            campo.limpar()
        any(c.focar() for c in self._campos)
        self.notify(t("dados.f.posta", n=len(self.rascunho.operacoes)))
        self.atualizar()

    def on_campo_valor_escolher(self, evento: CampoValor.Escolher) -> None:
        evento.stop()
        campo = evento.campo
        nomes = self._pai_no_banco(campo.referencia)
        if nomes is None:
            return
        tela = EscolherLinha(self.fonte, *nomes)

        def chegou(valor: Optional[str]) -> None:
            if valor is not None and campo.is_attached:
                campo.escolhido(valor, tela.rotulo_escolhido)
        self.app.push_screen(tela, chegou)
