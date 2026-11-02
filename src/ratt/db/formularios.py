"""Os formulários do canvas: cada um monta UMA operação do rascunho e mostra, a cada tecla, o SQL que ela gera
e o que o banco tem a dizer (avisos). Nada aqui executa DDL: "pôr no rascunho" só acrescenta a operação.

Os avisos pedem contagens ao banco, por isso são calculados numa thread (com "contando…" enquanto isso) e
nunca seguram o formulário. Mesmo um aviso "impede" deixa o botão ligado: a pessoa decide.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.widgets import Button, Checkbox, Input, OptionList, RadioButton, RadioSet, Select, Static
from textual.widgets.option_list import Option

from ratt.db import checagem, rascunho as rs
from ratt.db.idioma import t
from ratt.db.modelo import Coluna, Ligacao, Tabela
from ratt.db.tela import COR

_ERROS_SQL = (KeyError, ValueError, TypeError, AttributeError)
# só estas operações perguntam algo ao banco; as outras não mostram "contando…"
_COM_CONTAGEM = (rs.Obrigatoria, rs.Unica, rs.AdicionarColuna, rs.Ligar, rs.ApagarColuna, rs.ApagarTabela, rs.Inserir)
_NIVEL = {"info": ("·", COR["cinza"]), "atencao": ("!", COR["amarelo"]), "impede": ("✖", COR["laranja"])}
_ATRASO = 0.25                      # espera a pessoa parar de digitar antes de contar no banco


def sql_bonito(comandos: List[str]) -> Syntax:
    return Syntax("\n".join(c + ";" for c in comandos), "sql", theme="ansi_dark", background_color="default", word_wrap=True)


class Corpo(VerticalScroll):
    """Os campos rolam, mas o foco é dos campos: um contêiner focável roubaria o foco inicial do primeiro Input."""
    can_focus = False


class FormBase(ModalScreen):
    """Esqueleto: título, campos (da subclasse), SQL, avisos, erro e os dois botões."""

    BINDINGS = [Binding("escape", "cancelar", t("canvas.f.cancelar"))]
    DEFAULT_CSS = """
    FormBase { align: center middle; }
    FormBase > Vertical { width: 84; max-width: 100%; height: auto; max-height: 100%; border: round $primary;
                          background: $surface; padding: 0 1; }
    FormBase #form_titulo { text-style: bold; padding: 0 0 1 0; }
    FormBase #form_corpo { height: auto; }
    FormBase Input { height: 3; }
    FormBase #form_sql, FormBase #form_avisos, FormBase #erro { height: auto; margin-top: 1; }
    FormBase #form_botoes { height: 3; margin-top: 1; align-horizontal: right; }
    FormBase #form_botoes Button { margin-left: 1; }
    """

    titulo = ""
    avisa_sem_sql = False       # a operação se monta mas o SQL não (um valor que não serve): os avisos dizem o porquê

    def __init__(self, rascunho: rs.Rascunho, fonte):
        super().__init__()
        self._rascunho, self.fonte = rascunho, fonte
        self._versao = 0
        self._timer = None

    @property
    def rascunho(self) -> rs.Rascunho:
        return self._rascunho

    # ── a subclasse preenche ──
    def campos(self) -> ComposeResult:
        return iter(())

    def operacao(self) -> rs.Operacao:
        """Monta a operação com o que está nos campos; ValueError diz à pessoa o que falta."""
        raise NotImplementedError

    def depois_dos_avisos(self, avisos) -> None:
        """Gancho: a subclasse reage aos avisos que chegaram."""

    def botoes(self) -> ComposeResult:
        """Gancho: botões a mais, antes do "pôr no rascunho"."""
        return iter(())

    def acrescentar(self, op: rs.Operacao) -> None:
        """Gancho: como a operação entra no rascunho (a tela que abriu grava e avisa depois do `dismiss`)."""
        self.rascunho.acrescentar(op)

    # ── esqueleto ──
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self.titulo), id="form_titulo")
            with Corpo(id="form_corpo"):
                yield from self.campos()
                yield Static(id="form_sql")
                yield Static(id="form_avisos")
                yield Static(id="erro")
            with Horizontal(id="form_botoes"):
                yield from self.botoes()
                yield Button(t("canvas.f.pr"), id="por_no_rascunho", variant="primary")
                yield Button(t("canvas.f.cancelar"), id="cancelar")

    def on_mount(self) -> None:
        self._ajustar()
        self.atualizar()

    def on_resize(self, evento) -> None:
        self._ajustar()

    def _ajustar(self) -> None:
        """Os campos ocupam o que sobra da tela (menos título, bordas e botões) e rolam; os botões ficam à vista."""
        self.query_one("#form_corpo").styles.max_height = max(4, self.size.height - 10)

    def previa(self) -> rs.Operacao:
        """A operação como seria agora, sem gastar ids do rascunho (a prévia roda a cada tecla)."""
        guardado = self.rascunho.proximo
        try:
            return self.operacao()
        finally:
            self.rascunho.proximo = guardado

    def atualizar(self) -> None:
        self._versao += 1
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        sql, avisos, erro = self.query_one("#form_sql", Static), self.query_one("#form_avisos", Static), self.query_one("#erro", Static)
        op = None
        try:
            op = self.previa()
            comandos = rs.comandos_de(op, self.rascunho.vista())
        except ValueError as e:
            sql.update("")
            avisos.update("")
            erro.update(Text(str(e), style=COR["laranja"]))
            if op is None or not self.avisa_sem_sql:
                return
        except _ERROS_SQL as e:
            sql.update("")
            avisos.update("")
            erro.update(Text(t("canvas.e.operacao", erro=e), style=COR["laranja"]))
            return
        else:
            erro.update("")
            sql.update(sql_bonito(comandos))
        if isinstance(op, _COM_CONTAGEM):
            avisos.update(Text(t("canvas.f.contando"), style=COR["cinza"]))
            self._timer = self.set_timer(_ATRASO, lambda v=self._versao, o=op: self._contar(o, v))
        else:
            avisos.update("")

    def _contar(self, op: rs.Operacao, versao: int) -> None:
        self._timer = None

        def trabalho():
            try:
                avisos = checagem.avisos_da_operacao(op, self.rascunho, self.fonte.consulta())
            except Exception as e:                     # o banco caiu, faltou permissão...: vira uma linha, não um erro
                avisos = e
            try:
                self.app.call_from_thread(self._chegaram, versao, avisos)
            except RuntimeError:                       # a app já fechou
                pass
        self.run_worker(trabalho, thread=True, exclusive=True, group="avisos_form")

    def _chegaram(self, versao: int, avisos) -> None:
        if not self.is_attached or versao != self._versao:
            return
        alvo = self.query_one("#form_avisos", Static)
        if isinstance(avisos, Exception):
            linhas = str(avisos).strip().splitlines()
            alvo.update(Text(t("canvas.erro_banco", erro=linhas[0] if linhas else type(avisos).__name__), style=COR["amarelo"]))
            return
        if not avisos:
            alvo.update(Text(t("canvas.f.sem_avisos"), style=COR["cinza"]))
        else:
            partes = []
            for a in avisos:
                marca, cor = _NIVEL[a.nivel]
                partes += [(marca + " ", cor), (a.texto() + "\n", cor if a.nivel != "info" else "")]
            alvo.update(Text.assemble(*partes))
        self.depois_dos_avisos(avisos)

    # ── eventos ──
    def on_input_changed(self, evento) -> None:
        self.atualizar()

    def on_checkbox_changed(self, evento) -> None:
        self.atualizar()

    def on_radio_set_changed(self, evento) -> None:
        self.atualizar()

    def on_select_changed(self, evento) -> None:
        self.atualizar()

    def on_option_list_option_highlighted(self, evento) -> None:
        self.atualizar()

    def on_input_submitted(self, evento) -> None:
        self.por()

    def on_button_pressed(self, evento: Button.Pressed) -> None:
        evento.stop()
        if evento.button.id == "por_no_rascunho":
            self.por()
        elif evento.button.id == "cancelar":
            self.dismiss(None)

    def action_cancelar(self) -> None:
        self.dismiss(None)

    def por(self) -> None:
        op = self.por_sem_fechar()
        if op is not None:
            self.dismiss(op)

    def por_sem_fechar(self) -> Optional[rs.Operacao]:
        """Acrescenta a operação dos campos; None (com o motivo em `#erro`) se ela não entrou."""
        guardado = self.rascunho.proximo
        try:
            op = self.operacao()
            self.acrescentar(op)
        except ValueError as e:
            self.rascunho.proximo = guardado
            self.query_one("#erro", Static).update(Text(str(e), style=COR["laranja"]))
            return None
        except _ERROS_SQL:                  # a tabela ou coluna sumiu do banco com o formulário aberto
            self.rascunho.proximo = guardado
            self.query_one("#erro", Static).update(Text(t("canvas.e.sumiu"), style=COR["laranja"]))
            return None
        return op


def _nome_novo(texto: str, existentes) -> str:
    nome = texto.strip()
    if not nome:
        raise ValueError(t("canvas.e.nome_vazio"))
    if nome in existentes:
        raise ValueError(t("canvas.e.nome_existe", nome=nome))
    return nome


def _tipo(texto: str) -> str:
    tipo = texto.strip()
    if not rs.tipo_valido(tipo):
        raise ValueError(t("canvas.e.tipo", tipo=tipo or "''"))
    return tipo


def _nomes_vivos(tab: Tabela) -> set:
    return {c.nome for c in tab.colunas if c.estado != "apagado"}


class FormColuna(FormBase):
    def __init__(self, rascunho, fonte, tabela_id: str):
        super().__init__(rascunho, fonte)
        self.tabela_id = tabela_id
        self.titulo = t("canvas.f.nova_coluna", tabela=rascunho.vista().tabelas[tabela_id].nome)

    def campos(self) -> ComposeResult:
        yield Static(t("canvas.f.nome"))
        yield Input(id="nome")
        yield Static(t("canvas.f.tipo"))
        yield Input("text", id="tipo", suggester=SuggestFromList(rs.TIPOS, case_sensitive=True))
        yield Checkbox(t("canvas.f.obrig"), id="obrigatoria")
        yield Static(t("canvas.f.padrao"))
        yield Input(id="padrao")

    def operacao(self) -> rs.Operacao:
        tab = self.rascunho.vista().tabelas[self.tabela_id]
        nome = _nome_novo(self.query_one("#nome", Input).value, _nomes_vivos(tab))
        tipo = _tipo(self.query_one("#tipo", Input).value)
        padrao = self.query_one("#padrao", Input).value.strip() or None
        return rs.AdicionarColuna(self.tabela_id, Coluna(self.rascunho.novo_id("c"), nome, tipo,
                                                         obrigatoria=self.query_one("#obrigatoria", Checkbox).value, padrao=padrao))


class FormTabela(FormBase):
    """Nome da tabela e colunas. Começa com `id bigint` identidade e chave primária; as outras entram uma a uma."""

    titulo = t("canvas.f.nova_tabela")

    def __init__(self, rascunho, fonte):
        super().__init__(rascunho, fonte)
        # (nome, tipo, obrigatória, padrão, identidade, pk)
        self.especs = [("id", "bigint", True, None, True, True)]

    def campos(self) -> ComposeResult:
        yield Static(t("canvas.f.nome"))
        yield Input(id="nome")
        yield Static(t("canvas.f.colunas"))
        yield Static(id="lista_colunas")
        yield Static(Text(t("canvas.f.outra_coluna"), style=COR["cinza"]))
        yield Static(t("canvas.f.nome"))
        yield Input(id="col_nome")
        yield Static(t("canvas.f.tipo"))
        yield Input("text", id="col_tipo", suggester=SuggestFromList(rs.TIPOS, case_sensitive=True))
        yield Checkbox(t("canvas.f.obrig"), id="col_obrig")
        yield Static(t("canvas.f.padrao"))
        yield Input(id="col_padrao")
        with Horizontal(classes="linha_botoes", id="botoes_colunas"):
            yield Button(t("canvas.f.add_coluna"), id="add_coluna")
            yield Button(t("canvas.f.tirar_coluna"), id="tirar_coluna")

    DEFAULT_CSS = "FormTabela #botoes_colunas { height: 3; }"

    def on_mount(self) -> None:
        self._lista()
        super().on_mount()

    def _lista(self) -> None:
        linhas = []
        for nome, tipo, obrig, padrao, ident_, pk in self.especs:
            extra = [x for x, ligado in (("PK", pk), ("identity", ident_), ("NOT NULL", obrig and not pk)) if ligado]
            if padrao:
                extra.append("DEFAULT " + padrao)
            linhas.append(f"  {nome} {tipo}" + (f"  [{', '.join(extra)}]" if extra else ""))
        self.query_one("#lista_colunas", Static).update(Text("\n".join(linhas), style=COR["cinza"]))

    def _digitada(self):
        """A coluna escrita nos campos de baixo (None se o nome está vazio)."""
        nome = self.query_one("#col_nome", Input).value.strip()
        if not nome:
            return None
        if nome in {e[0] for e in self.especs}:
            raise ValueError(t("canvas.e.nome_existe", nome=nome))
        tipo = _tipo(self.query_one("#col_tipo", Input).value)
        padrao = self.query_one("#col_padrao", Input).value.strip() or None
        return (nome, tipo, self.query_one("#col_obrig", Checkbox).value, padrao, False, False)

    def on_button_pressed(self, evento: Button.Pressed) -> None:
        if evento.button.id == "add_coluna":
            evento.stop()
            try:
                nova = self._digitada()
            except ValueError as e:
                self.query_one("#erro", Static).update(Text(str(e), style=COR["laranja"]))
                return
            if nova is None:
                self.query_one("#erro", Static).update(Text(t("canvas.e.nome_vazio"), style=COR["laranja"]))
                return
            self.especs.append(nova)
            self.query_one("#col_nome", Input).value = ""
            self.query_one("#col_padrao", Input).value = ""
            self.query_one("#col_obrig", Checkbox).value = False
            self.query_one("#col_nome", Input).focus()
            self._lista()
            self.atualizar()
        elif evento.button.id == "tirar_coluna":
            evento.stop()
            if len(self.especs) > 1:
                self.especs.pop()
            self._lista()
            self.atualizar()
        else:
            super().on_button_pressed(evento)

    def on_input_submitted(self, evento) -> None:
        if evento.input.id in ("col_nome", "col_tipo", "col_padrao"):
            self.query_one("#add_coluna", Button).press()
        else:
            self.por()

    def operacao(self) -> rs.Operacao:
        v = self.rascunho.vista()
        nome = _nome_novo(self.query_one("#nome", Input).value,
                          {tb.nome for tb in v.tabelas.values() if tb.estado != "apagado"})
        especs = list(self.especs)
        digitada = self._digitada()
        if digitada:
            especs.append(digitada)
        tid = self.rascunho.novo_id("t")
        colunas = [Coluna(self.rascunho.novo_id("c"), n, tp, obrigatoria=ob, padrao=pd, identidade=idt, pk=pk)
                   for n, tp, ob, pd, idt, pk in especs]
        return rs.CriarTabela(Tabela(tid, v.esquema, nome, colunas))


class FormLigar(FormBase):
    """Liga uma coluna (ou cria a coluna que liga) a outra tabela; ou troca por uma tabela de junção N:N."""

    def __init__(self, rascunho, fonte, tabela_id: str, coluna_id: Optional[str] = None):
        super().__init__(rascunho, fonte)
        self.tabela_id, self.coluna_id = tabela_id, coluna_id
        v = rascunho.vista()
        self.origem = v.tabelas[tabela_id]
        self.opcoes = sorted((tb for tb in v.tabelas.values() if tb.estado != "apagado"), key=lambda tb: (tb.nome, tb.id))
        self.titulo = t("canvas.f.ligar", origem=self.origem.nome if coluna_id is None
                        else f"{self.origem.nome}.{self.origem.coluna(coluna_id).nome}")
        self._auto_coluna = self._auto_nn = ""

    def campos(self) -> ComposeResult:
        yield Static(t("canvas.f.destino"))
        yield OptionList(*[Option(tb.nome, id=tb.id) for tb in self.opcoes], id="destino")
        with Vertical(id="grupo_fk"):
            if self.coluna_id is None:
                yield Static(t("canvas.f.coluna_nova"))
                yield Input(id="coluna_nova")
            yield Static("", id="cada")
            with RadioSet(id="cardinal"):
                yield RadioButton(t("canvas.f.varios"), value=True, id="varios")
                yield RadioButton(t("canvas.f.um"), id="um")
            yield Static(t("canvas.f.ao_apagar"))
            yield Select([(x, x) for x in ("NO ACTION", "CASCADE", "SET NULL")], value="NO ACTION", allow_blank=False, id="ao_apagar")
            # índice na coluna da FK: o PostgreSQL não cria sozinho; mas se ela já tem um (ou é a PK), não
            yield Checkbox(t("canvas.f.indice"), value=self._indice_padrao(), id="indice")
        yield Checkbox(t("canvas.f.nn"), id="como_nn")
        with Vertical(id="grupo_nn"):
            yield Static(t("canvas.f.nome_nn"))
            yield Input(id="nome_nn")

    DEFAULT_CSS = """
    FormLigar #destino { height: auto; max-height: 8; }
    FormLigar #grupo_fk, FormLigar #grupo_nn { height: auto; }
    FormLigar #grupo_nn { display: none; }
    FormLigar RadioSet { height: auto; }
    """

    def _indice_padrao(self) -> bool:
        if self.coluna_id is None:
            return True                 # coluna nova: ainda sem índice
        c = self.origem.coluna(self.coluna_id)
        return not (c.indexada or c.pk)

    def on_radio_set_changed(self, evento) -> None:
        # "no máximo um" põe UNIQUE, que já cria um índice: outro seria repetido
        um = self.query_one("#um", RadioButton).value
        indice = self.query_one("#indice", Checkbox)
        if um:
            indice.value = False
        indice.disabled = um
        super().on_radio_set_changed(evento)

    def on_mount(self) -> None:
        self.query_one("#destino", OptionList).highlighted = None
        super().on_mount()

    def _destino(self) -> Optional[Tabela]:
        i = self.query_one("#destino", OptionList).highlighted
        return self.opcoes[i] if i is not None else None

    def atualizar(self) -> None:
        # sugestões que acompanham o destino enquanto a pessoa não as mexeu
        dest = self._destino()
        if dest is not None:
            if self.coluna_id is None:
                campo = self.query_one("#coluna_nova", Input)
                if campo.value == self._auto_coluna:
                    self._auto_coluna = campo.value = rs.singular(dest.nome) + "_id"
            nn = self.query_one("#nome_nn", Input)
            if nn.value == self._auto_nn:
                self._auto_nn = nn.value = f"{self.origem.nome}_{dest.nome}"
            self.query_one("#cada", Static).update(t("canvas.f.cada", destino=dest.nome, origem=self.origem.nome))
        nn_ligado = self.query_one("#como_nn", Checkbox).value
        self.query_one("#grupo_fk").display = not nn_ligado
        self.query_one("#grupo_nn").display = nn_ligado
        super().atualizar()

    def operacao(self) -> rs.Operacao:
        dest = self._destino()
        if dest is None:
            raise ValueError(t("canvas.e.sem_destino"))
        pks = dest.pk()
        if len(pks) != 1:
            raise ValueError(t("canvas.e.sem_pk", tabela=dest.nome))
        pk = pks[0]
        v = self.rascunho.vista()
        if self.query_one("#como_nn", Checkbox).value:
            existentes = {tb.nome for tb in v.tabelas.values() if tb.estado != "apagado"}
            nome = _nome_novo(self.query_one("#nome_nn", Input).value, existentes)
            if len(self.origem.pk()) != 1:
                raise ValueError(t("canvas.e.sem_pk", tabela=self.origem.nome))
            r = self.rascunho
            return rs.LigarNN(r.novo_id("t"), nome, self.origem.id, dest.id,
                              (r.novo_id("c"), r.novo_id("c")), (r.novo_id("k"), r.novo_id("k")))
        nova = None
        if self.coluna_id is None:
            nome = _nome_novo(self.query_one("#coluna_nova", Input).value, _nomes_vivos(self.origem))
            nova = Coluna(self.rascunho.novo_id("c"), nome, pk.tipo)
            col_id = nova.id
        else:
            col_id = self.coluna_id
        ao_apagar = self.query_one("#ao_apagar", Select).value
        lig = Ligacao(self.rascunho.novo_id("k"), self.origem.id, (col_id,), dest.id, (pk.id,), "", str(ao_apagar))
        unica = self.query_one("#um", RadioButton).value
        return rs.Ligar(lig, nova, unica=unica, indice=self.query_one("#indice", Checkbox).value and not unica)


class FormSimples(FormBase):
    """Uma operação que só precisa de confirmação (e, se `valor` vier, de um nome): mostra SQL e avisos."""

    def __init__(self, rascunho, fonte, titulo: str, construir: Callable[[Optional[str]], rs.Operacao],
                 valor: Optional[str] = None):
        super().__init__(rascunho, fonte)
        self.titulo, self.construir, self.valor = titulo, construir, valor

    def campos(self) -> ComposeResult:
        if self.valor is not None:
            yield Static(t("canvas.f.nome"))
            yield Input(self.valor, id="nome")

    def operacao(self) -> rs.Operacao:
        if self.valor is None:
            return self.construir(None)
        novo = self.query_one("#nome", Input).value.strip()
        if not novo:
            raise ValueError(t("canvas.e.nome_vazio"))
        return self.construir(novo)


class ConfirmarApagar(FormBase):
    """O que será apagado, os avisos e, se houver dependentes, a escolha do CASCADE."""

    def __init__(self, rascunho, fonte, selecao: tuple):
        super().__init__(rascunho, fonte)
        self.selecao = selecao
        v = rascunho.vista()
        if selecao[0] == "tabela":
            nome = v.tabelas[selecao[1]].nome
        elif selecao[0] == "coluna":
            nome = f"{v.tabelas[selecao[1]].nome}.{v.tabelas[selecao[1]].coluna(selecao[2]).nome}"
        else:
            lig = v.ligacoes[selecao[1]]
            nome = lig.nome or selecao[1]
        self.titulo = t("canvas.f.apagar", nome=nome)
        self.contagem: Optional[int] = None     # linhas que somem com a tabela, quando a contagem chegou
        # ligações vivas que apontam para a tabela ou coluna: já é motivo para oferecer CASCADE
        if selecao[0] == "tabela":
            self._local = any(k.destino == selecao[1] and k.estado != "apagado" for k in v.ligacoes.values())
        elif selecao[0] == "coluna":
            self._local = any(selecao[2] in k.colunas_destino and k.estado != "apagado" for k in v.ligacoes.values())
        else:
            self._local = False

    DEFAULT_CSS = "ConfirmarApagar #cascata { display: none; }"

    def campos(self) -> ComposeResult:
        yield Static(id="o_que")
        yield Checkbox(t("canvas.f.cascata"), id="cascata")

    def on_mount(self) -> None:
        if self._local:
            self.query_one("#cascata").display = True
        super().on_mount()
        # foco no cancelar: um `x` seguido de Enter nunca põe um DROP no rascunho antes de a pessoa ver a prévia
        self.query_one("#cancelar", Button).focus()

    def depois_dos_avisos(self, avisos) -> None:
        if any(a.chave in ("chk.dependentes", "chk.precisa_cascata") for a in avisos):
            self.query_one("#cascata").display = True
        if self.selecao[0] == "tabela":
            # quantas linhas somem, para a confirmação do aplicar repetir; sem aviso e sem "não deu tempo" é zero
            n = next((a.valores["n"] for a in avisos if a.chave == "chk.linhas_somem"), None)
            if n is None and not any(a.chave == "chk.sem_tempo" and a.alvo == self.selecao[1] for a in avisos):
                n = 0
            self.contagem = n

    def operacao(self) -> rs.Operacao:
        cascata = self.query_one("#cascata", Checkbox).value
        if self.selecao[0] == "tabela":
            return rs.ApagarTabela(self.selecao[1], cascata)
        if self.selecao[0] == "coluna":
            return rs.ApagarColuna(self.selecao[1], self.selecao[2], cascata)
        return rs.ApagarLigacao(self.selecao[1])

    def atualizar(self) -> None:
        try:
            self.query_one("#o_que", Static).update(Text(t("canvas.f.o_que", descricao=self.previa().descricao(self.rascunho.vista()))))
        except _ERROS_SQL:
            pass
        super().atualizar()


class MenuSelecao(ModalScreen):
    """O que dá para fazer com a seleção: renomear, obrigatória/opcional, única/não única."""

    BINDINGS = [Binding("escape", "dismiss(None)", t("canvas.f.cancelar"))]
    DEFAULT_CSS = """
    MenuSelecao { align: center middle; }
    MenuSelecao > Vertical { width: 50; height: auto; border: round $primary; background: $surface; padding: 1 2; }
    MenuSelecao OptionList { height: auto; border: none; }
    """

    def __init__(self, nome: str, itens):
        super().__init__()
        self.nome, self.itens = nome, itens

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(t("canvas.f.menu", nome=self.nome), style="bold"))
            yield OptionList(*[Option(rotulo, id=chave) for chave, rotulo in self.itens])

    def on_option_list_option_selected(self, evento) -> None:
        self.dismiss(evento.option.id)


class ConfirmarTirar(ModalScreen):
    """Tirar uma operação que outras usam: lista as dependentes e pergunta se saem todas."""

    BINDINGS = [Binding("escape", "dismiss(False)", t("canvas.f.cancelar"))]
    DEFAULT_CSS = """
    ConfirmarTirar { align: center middle; }
    ConfirmarTirar > Vertical { width: 80; max-width: 100%; height: auto; border: round $primary; background: $surface; padding: 1 2; }
    ConfirmarTirar #botoes { height: 3; margin-top: 1; align-horizontal: right; }
    ConfirmarTirar Button { margin-left: 1; }
    """

    def __init__(self, descricoes: List[str]):
        super().__init__()
        self.descricoes = descricoes

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(t("canvas.f.tirar_pergunta"), style="bold"))
            yield Static("\n".join("  • " + d for d in self.descricoes))
            with Horizontal(id="botoes"):
                yield Button(t("canvas.f.tirar_todas"), id="tirar_todas", variant="warning")
                yield Button(t("canvas.f.cancelar"), id="cancelar")

    def on_button_pressed(self, evento: Button.Pressed) -> None:
        self.dismiss(evento.button.id == "tirar_todas")


class Confirmar(ModalScreen):
    """Uma pergunta de sim/não com uma lista opcional. O foco inicial vai no botão que `foco` diz: nas perguntas
    perigosas (sobrescrever) é o cancelar, para um Enter distraído não destruir nada."""

    BINDINGS = [Binding("escape", "dismiss(False)", t("canvas.f.cancelar"))]
    DEFAULT_CSS = """
    Confirmar { align: center middle; }
    Confirmar > Vertical { width: 84; max-width: 100%; height: auto; max-height: 100%; border: round $primary;
                           background: $surface; padding: 1 2; }
    Confirmar #lista, Confirmar #sql { height: auto; max-height: 16; margin-top: 1; }
    Confirmar #botoes { height: 3; margin-top: 1; align-horizontal: right; }
    Confirmar Button { margin-left: 1; }
    """

    def __init__(self, pergunta: str, itens: List[str], rotulo: str, foco: str = "sim", sql: str = ""):
        super().__init__()
        self.pergunta, self.itens, self.rotulo, self.foco, self.sql = pergunta, itens, rotulo, foco, sql

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self.pergunta, style="bold"))
            if self.itens:
                with VerticalScroll(id="lista"):
                    yield Static(Text("\n".join("  • " + i for i in self.itens)))
            if self.sql:
                with VerticalScroll(id="sql"):
                    yield Static(Syntax(self.sql, "sql", theme="ansi_dark", background_color="default", word_wrap=True))
            with Horizontal(id="botoes"):
                yield Button(self.rotulo, id="sim", variant="warning")
                yield Button(t("canvas.f.cancelar"), id="nao")

    def on_mount(self) -> None:
        self.query_one("#" + self.foco, Button).focus()

    def on_button_pressed(self, evento: Button.Pressed) -> None:
        self.dismiss(evento.button.id == "sim")
