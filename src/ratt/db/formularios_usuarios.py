"""Os formulários da tela Usuários: cada um monta operações do rascunho do servidor e mostra, a cada mudança, o SQL
que elas geram (nunca uma senha: só o comentário de que ela é pedida ao aplicar) e os avisos do servidor (contados
numa thread, como no canvas).

Diferente dos formulários do canvas, aqui o que o rascunho recusa — a trava "nunca se trancar para fora"
(`rascunho_servidor.trava`) ou uma operação que não vale — deixa o botão DESLIGADO, com o motivo à vista: não é um
aviso que a pessoa pesa, é uma regra. Os avisos (`checagem_servidor`) continuam só avisando; a remoção espera eles
chegarem antes de deixar pôr (a pessoa vê o que passa ao novo dono e o que some).
"""
from __future__ import annotations

import copy
from typing import List, Optional, Tuple

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, OptionList, Select, SelectionList, Static
from textual.widgets.option_list import Option

from ratt.db import servidor as _srv
from ratt.db.formularios import Corpo
from ratt.db.idioma import t
from ratt.db.rascunho import ERROS_OP
from ratt.db.rascunho_servidor import (PUBLIC, CriarPapel, MudarPoderes, OperacaoServidor, PorNoGrupo,
                                       RascunhoServidor, RemoverPapeis, TirarAcesso, TirarDoGrupo, TrocarDonoDoBanco,
                                       TrocarSenha, linhas_dos_comandos)
from ratt.db.servidor import ModeloServidor, Papel
from ratt.db.tela import COR

_NIVEL = {"info": ("·", COR["cinza"]), "atencao": ("!", COR["amarelo"]), "impede": ("✖", COR["laranja"])}
_ATRASO = 0.25                      # espera a pessoa parar de mexer antes de contar no servidor


def recusa(r: RascunhoServidor, ops: List[OperacaoServidor]) -> Optional[str]:
    """Por que `ops` (nessa ordem) não entrariam no rascunho `r`: o texto da trava, ou o motivo do rascunho. None se
    entram. Tudo numa cópia: `r` e as operações não mudam."""
    copia = RascunhoServidor(r.base, list(r.operacoes), r.proximo)
    for op in ops:
        try:
            copia.acrescentar(copy.deepcopy(op))
        except ValueError as e:
            return str(e) or type(e).__name__
        except ERROS_OP as e:
            return t("canvas.e.operacao", erro=e)
    return None


def sql_das(r: RascunhoServidor, ops: List[OperacaoServidor]) -> str:
    """O SQL que `ops` acrescentariam ao rascunho `r`, como o painel o mostra (com o comentário da senha)."""
    tmp = RascunhoServidor(r.base, list(r.operacoes) + [copy.deepcopy(o) for o in ops], r.proximo)
    passos, notas = tmp.passos(), tmp.notas()
    linhas: List[str] = []
    for k in range(len(r.operacoes), len(passos)):
        _, valida, _, cmds = passos[k]
        if valida:
            linhas += linhas_dos_comandos(cmds)
        if notas[k]:
            linhas.append("-- " + notas[k])
    return "\n".join(linhas)


def papeis_visiveis(m: ModeloServidor) -> List[Papel]:
    """Os papéis que as telas mostram: sem os do PostgreSQL (`pg_*`) e sem os que o rascunho remove, por nome."""
    return sorted((p for p in m.papeis.values() if not p.sistema and p.estado != "apagado"), key=lambda p: p.nome)


def _rotulo(p: Papel) -> str:
    return p.nome if p.login else f"{p.nome} {t('usu.f.grupo')}"


def texto_avisos(avisos) -> Text:
    partes = []
    for a in avisos:
        marca, cor = _NIVEL.get(a.nivel, ("·", COR["cinza"]))
        partes += [(marca + " ", cor), (a.texto() + "\n", cor if a.nivel != "info" else "")]
    return Text.assemble(*partes)


class FormServidor(ModalScreen):
    """Esqueleto: título, campos (da subclasse), SQL, erro (ou o motivo da recusa), avisos e os dois botões. A
    subclasse dá `campos()` e `operacoes()` (as operações dos campos agora; ValueError diz o que falta). Fecha com
    a lista das operações que entraram no rascunho (a tela grava e avisa), ou None."""

    BINDINGS = [Binding("escape", "cancelar", t("canvas.f.cancelar"))]
    DEFAULT_CSS = """
    FormServidor { align: center middle; }
    FormServidor > Vertical { width: 84; max-width: 100%; height: auto; max-height: 100%; border: round $primary;
                              background: $surface; padding: 0 1; }
    FormServidor #form_titulo { text-style: bold; padding: 0 0 1 0; }
    FormServidor #form_corpo { height: auto; }
    FormServidor Input { height: 3; }
    FormServidor SelectionList, FormServidor OptionList { height: auto; max-height: 8; }
    FormServidor #form_sql, FormServidor #erro { height: auto; margin-top: 1; }
    FormServidor #form_avisos_area { height: 6; margin-top: 1; }
    FormServidor #form_avisos { height: auto; }
    FormServidor #form_botoes { height: 3; margin-top: 1; align-horizontal: right; }
    FormServidor #form_botoes Button { margin-left: 1; }
    """

    titulo = ""
    exige_avisos = False        # o botão só liga quando os avisos do estado atual chegaram (a remoção)
    foco_no_cancelar = False    # um Enter que sobrou no teclado não põe nada no rascunho
    altura_avisos = 6

    def __init__(self, sessao, fonte):
        super().__init__()
        self.sessao, self.fonte = sessao, fonte
        self._versao = 0
        self._timer = None
        self._esperando = False     # os avisos do estado atual estão a caminho
        self._chegou = False        # e chegaram
        self._ok = False            # as operações dos campos se montam e o rascunho as aceita
        self.avisos_chegados: Optional[list] = None

    @property
    def rascunho(self) -> RascunhoServidor:
        return self.sessao.rascunho

    @property
    def contando(self) -> bool:
        return self._esperando

    # ── a subclasse preenche ──
    def campos(self) -> ComposeResult:
        return iter(())

    def operacoes(self) -> List[OperacaoServidor]:
        raise NotImplementedError

    # ── esqueleto ──
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self.titulo), id="form_titulo")
            with Corpo(id="form_corpo"):
                yield from self.campos()
                yield Static(id="form_sql")
                yield Static(id="erro")
            with VerticalScroll(id="form_avisos_area"):
                yield Static(id="form_avisos")
            with Horizontal(id="form_botoes"):
                yield Button(t("canvas.f.pr"), id="por_no_rascunho", variant="primary", disabled=True)
                yield Button(t("canvas.f.cancelar"), id="cancelar")

    def on_mount(self) -> None:
        self.query_one("#form_avisos_area").styles.height = self.altura_avisos
        if self.foco_no_cancelar:
            self.query_one("#cancelar", Button).focus()
        self.atualizar()

    def previa(self) -> List[OperacaoServidor]:
        """As operações como seriam agora, sem gastar ids do rascunho (roda a cada mudança)."""
        guardado = self.rascunho.proximo
        try:
            return self.operacoes()
        finally:
            self.rascunho.proximo = guardado

    def atualizar(self) -> None:
        self._versao += 1
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._esperando, self._chegou, self._ok, self.avisos_chegados = False, False, False, None
        sql, erro, avisos = (self.query_one("#form_sql", Static), self.query_one("#erro", Static),
                             self.query_one("#form_avisos", Static))
        try:
            ops = self.previa()
            motivo = recusa(self.rascunho, ops)
        except ValueError as e:
            ops, motivo = [], str(e)
        except ERROS_OP as e:                       # um papel ou banco sumiu com o formulário aberto
            ops, motivo = [], t("canvas.e.operacao", erro=e)
        if motivo is not None:
            sql.update("")
            avisos.update("")
            erro.update(Text(motivo, style=COR["laranja"]))
            self._botao()
            return
        erro.update("")
        sql.update(Syntax(sql_das(self.rascunho, ops), "sql", theme="ansi_dark", background_color="default",
                          word_wrap=True))
        self._ok = True
        avisos.update(Text(t("canvas.f.contando"), style=COR["cinza"]))
        self._esperando = True
        self._timer = self.set_timer(_ATRASO, lambda v=self._versao, o=ops: self._contar(o, v))
        self._botao()

    def _botao(self) -> None:
        ligado = self._ok and (self._chegou or not self.exige_avisos)
        self.query_one("#por_no_rascunho", Button).disabled = not ligado

    def _contar(self, ops: List[OperacaoServidor], versao: int) -> None:
        self._timer = None
        r = self.rascunho
        base, antes, proximo = r.base, list(r.operacoes), r.proximo

        def trabalho():
            try:
                avisos: list = []
                for k, op in enumerate(ops):
                    junto = RascunhoServidor(base, antes + [copy.deepcopy(o) for o in ops[:k]], proximo)
                    avisos += self.fonte.avisos(op, junto)
            except Exception as e:                  # o banco caiu, faltou permissão…: vira uma linha, não um erro
                avisos = e
            try:
                self.app.call_from_thread(self._chegaram, versao, avisos)
            except RuntimeError:                    # a app já fechou
                pass
        self.run_worker(trabalho, thread=True, exclusive=True, group="avisos_usuarios_form")

    def _chegaram(self, versao: int, avisos) -> None:
        if not self.is_attached or versao != self._versao:
            return
        self._esperando, self._chegou = False, True
        alvo = self.query_one("#form_avisos", Static)
        if isinstance(avisos, Exception):
            linhas = str(avisos).strip().splitlines()
            alvo.update(Text(t("canvas.erro_banco", erro=linhas[0] if linhas else type(avisos).__name__),
                             style=COR["amarelo"]))
            self.avisos_chegados = []
        else:
            self.avisos_chegados = list(avisos)
            alvo.update(texto_avisos(avisos) if avisos else Text(t("canvas.f.sem_avisos"), style=COR["cinza"]))
        if self.query_one("#erro", Static).content and self._ok:
            self.query_one("#erro", Static).update("")
        self._botao()

    # ── eventos ──
    def on_input_changed(self, evento) -> None:
        self.atualizar()

    def on_checkbox_changed(self, evento) -> None:
        self.atualizar()

    def on_select_changed(self, evento) -> None:
        self.atualizar()

    def on_selection_list_selected_changed(self, evento) -> None:
        self.atualizar()

    def on_option_list_option_highlighted(self, evento) -> None:
        self.atualizar()

    def on_option_list_option_selected(self, evento) -> None:
        self.por()

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
        """Põe as operações no rascunho e fecha; com o botão desligado (recusada, ou os avisos não chegaram), não."""
        erro = self.query_one("#erro", Static)
        if self.query_one("#por_no_rascunho", Button).disabled:
            if self._ok and self.exige_avisos and not self._chegou:
                erro.update(Text(t("usu.f.esperar_avisos"), style=COR["laranja"]))
            return
        r = self.rascunho
        guardado, n = r.proximo, len(r.operacoes)
        try:
            ops = self.operacoes()
            for op in ops:
                r.acrescentar(op)
        except ERROS_OP as e:                       # o servidor mudou por baixo (a vigia refez o rascunho)
            del r.operacoes[n:]
            r.proximo = guardado
            erro.update(Text(str(e) if isinstance(e, ValueError) else t("canvas.e.operacao", erro=e),
                             style=COR["laranja"]))
            self.atualizar()
            return
        self.dismiss(ops)


# ── os formulários ──

class FormNovoUsuario(FormServidor):
    """`a`: nome, se entra (LOGIN; sem isso é um grupo), os poderes e os grupos de que faz parte. A senha não se
    digita aqui: é pedida ao aplicar (e nunca guardada)."""

    def __init__(self, sessao, fonte):
        super().__init__(sessao, fonte)
        self.titulo = t("usu.f.novo")
        self.grupos = papeis_visiveis(sessao.rascunho.vista())

    def campos(self) -> ComposeResult:
        yield Static(t("usu.f.nome"))
        yield Input(id="nome")
        yield Checkbox(t("usu.f.login"), True, id="login")
        yield Checkbox(t("usu.f.super"), id="super")
        yield Checkbox(t("usu.f.cria_bancos"), id="cria_bancos")
        yield Checkbox(t("usu.f.cria_papeis"), id="cria_papeis")
        if self.grupos:
            yield Static(t("usu.f.grupos_de"))
            yield SelectionList[str](*[(_rotulo(p), p.id) for p in self.grupos], id="grupos")

    def operacoes(self) -> List[OperacaoServidor]:
        nome = self.query_one("#nome", Input).value.strip()
        if not nome:
            raise ValueError(t("canvas.e.nome_vazio"))
        escolhidos = set(self.query_one("#grupos", SelectionList).selected) if self.grupos else set()
        valor = lambda campo: self.query_one("#" + campo, Checkbox).value
        papel = Papel(self.rascunho.novo_id("p"), nome, valor("login"), valor("super"), valor("cria_bancos"),
                      valor("cria_papeis"), membro_de=tuple(p.id for p in self.grupos if p.id in escolhidos))
        return [CriarPapel(papel)]


_PODERES = (("login", "usu.f.login"), ("superusuario", "usu.f.super"), ("cria_bancos", "usu.f.cria_bancos"),
            ("cria_papeis", "usu.f.cria_papeis"))
_ID_PODER = {"login": "login", "superusuario": "super", "cria_bancos": "cria_bancos", "cria_papeis": "cria_papeis"}


class FormPoderes(FormServidor):
    """`espaço`: os poderes do papel (LOGIN, SUPERUSER, CREATEDB, CREATEROLE) e trocar a senha (pedida ao aplicar)."""

    def __init__(self, sessao, fonte, papel_id: str):
        super().__init__(sessao, fonte)
        self.papel_id = papel_id
        self._papel = sessao.rascunho.vista().papeis[papel_id]
        self.titulo = t("usu.f.poderes", papel=self._papel.nome)

    def campos(self) -> ComposeResult:
        for campo, chave in _PODERES:
            yield Checkbox(t(chave), getattr(self._papel, campo), id=_ID_PODER[campo])
        yield Checkbox(t("usu.f.trocar_senha"), id="senha")

    def operacoes(self) -> List[OperacaoServidor]:
        p = self.rascunho.vista().papeis.get(self.papel_id)
        if p is None or p.estado == "apagado":
            raise ValueError(t("canvas.e.sumiu"))
        valores = {campo: self.query_one("#" + _ID_PODER[campo], Checkbox).value for campo, _ in _PODERES}
        ops: List[OperacaoServidor] = []
        if any(valores[campo] != getattr(p, campo) for campo in valores):
            ops.append(MudarPoderes(self.papel_id, **valores))
        if self.query_one("#senha", Checkbox).value:
            ops.append(TrocarSenha(self.papel_id))
        if not ops:
            raise ValueError(t("usu.e.nada_muda", papel=p.nome))
        return ops


class FormGrupos(FormServidor):
    """`g`: marcar de quais grupos o papel faz parte (entrar num: `GRANT grupo TO papel`; sair: `REVOKE`)."""

    def __init__(self, sessao, fonte, papel_id: str):
        super().__init__(sessao, fonte)
        v = sessao.rascunho.vista()
        p = v.papeis[papel_id]
        self.papel_id, self._nome = papel_id, p.nome
        self.atuais = set(p.membro_de)
        # os papéis do PostgreSQL (`pg_*`) só aparecem se ele já está num deles: dá para sair
        self.candidatos = [g for g in papeis_visiveis(v) if g.id != papel_id] + \
            sorted((v.papeis[g] for g in p.membro_de if g in v.papeis and v.papeis[g].sistema), key=lambda g: g.nome)
        self.titulo = t("usu.f.grupos", papel=p.nome)

    def campos(self) -> ComposeResult:
        yield SelectionList[str](*[(_rotulo(g), g.id, g.id in self.atuais) for g in self.candidatos], id="grupos")

    def operacoes(self) -> List[OperacaoServidor]:
        escolhidos = set(self.query_one("#grupos", SelectionList).selected)
        ops: List[OperacaoServidor] = [PorNoGrupo(self.papel_id, g.id) for g in self.candidatos
                                       if g.id in escolhidos and g.id not in self.atuais]
        ops += [TirarDoGrupo(self.papel_id, g.id) for g in self.candidatos
                if g.id in self.atuais and g.id not in escolhidos]
        if not ops:
            raise ValueError(t("usu.e.grupos_iguais", papel=self._nome))
        return ops


class EscolherPapel(FormServidor):
    """`o` numa coluna de banco: o novo dono do banco (`ALTER DATABASE … OWNER TO`)."""

    def __init__(self, sessao, fonte, banco_id: str):
        super().__init__(sessao, fonte)
        v = sessao.rascunho.vista()
        b = v.bancos[banco_id]
        self.banco_id, self._dono = banco_id, b.dono
        self.opcoes = papeis_visiveis(v)
        self.titulo = t("usu.f.dono", banco=b.nome)

    def campos(self) -> ComposeResult:
        yield OptionList(*[Option(_rotulo(p), id=p.id) for p in self.opcoes], id="papeis")

    def on_mount(self) -> None:
        ids = [p.id for p in self.opcoes]
        if self._dono in ids:
            self.query_one("#papeis", OptionList).highlighted = ids.index(self._dono)
        # (o on_mount do FormServidor o Textual chama em seguida)

    def operacoes(self) -> List[OperacaoServidor]:
        i = self.query_one("#papeis", OptionList).highlighted
        if i is None or not 0 <= i < len(self.opcoes):
            raise ValueError(t("usu.e.escolha_papel"))
        return [TrocarDonoDoBanco(self.banco_id, self.opcoes[i].id)]


class ConfirmarRemocao(FormServidor):
    """`x`: remover um ou vários papéis numa operação só. O novo dono do que eles possuem é, por padrão, o papel da
    pessoa (dá para escolher outro). Os avisos da remoção (por banco: o que passa, o que some, as sessões abertas)
    chegam antes de o botão ligar; o foco começa no cancelar."""

    exige_avisos = True
    foco_no_cancelar = True
    altura_avisos = 10

    def __init__(self, sessao, fonte, papeis: Tuple[str, ...]):
        super().__init__(sessao, fonte)
        v = sessao.rascunho.vista()
        self.papeis = tuple(papeis)
        self.titulo = t("usu.f.remover", papeis=", ".join(v.papeis[p].nome for p in self.papeis))
        self.opcoes = [p for p in papeis_visiveis(v) if p.id not in self.papeis]
        ids = [p.id for p in self.opcoes]
        self._padrao = v.eu if v.eu in ids else (ids[0] if ids else None)

    def campos(self) -> ComposeResult:
        yield Static(t("usu.f.novo_dono"))
        if self._padrao is None:
            yield Select([], id="novo_dono")
        else:
            yield Select([(p.nome, p.id) for p in self.opcoes], value=self._padrao, allow_blank=False, id="novo_dono")

    def operacoes(self) -> List[OperacaoServidor]:
        novo = self.query_one("#novo_dono", Select).value
        if not isinstance(novo, str):
            raise ValueError(t("usu.e.escolha_papel"))
        # os bancos que aceitam conexão agora (a sessão refaz a lista pelo catálogo a cada rebase e ao aplicar)
        bancos = tuple(b.nome for b in self.rascunho.base.bancos.values() if b.aceita_conexao)
        return [RemoverPapeis(self.papeis, novo, bancos)]


# ── o `c` herdado: de onde vem ──

class ExplicarAcesso(ModalScreen):
    """`c` (ou um clique) numa célula `c`: o acesso não é dado direto ao papel. Diz de onde vem (superusuário, o
    PUBLIC, um grupo — e por qual grupo do meio) e oferece tirar do PUBLIC ou tirar o papel do grupo. Uma oferta que
    o rascunho recusaria (a trava, por exemplo) fica com o botão desligado e o motivo escrito. Fecha com a operação
    escolhida, ou None."""

    BINDINGS = [Binding("escape", "dismiss(None)", t("canvas.f.cancelar"))]
    DEFAULT_CSS = """
    ExplicarAcesso { align: center middle; }
    ExplicarAcesso > Vertical { width: 84; max-width: 100%; height: auto; max-height: 100%; border: round $primary;
                                background: $surface; padding: 1 2; }
    ExplicarAcesso #explica_origens { height: auto; margin: 1 0; }
    ExplicarAcesso #ofertas { height: auto; }
    ExplicarAcesso #ofertas Button { width: 100%; margin-bottom: 1; }
    ExplicarAcesso #botoes { height: 3; align-horizontal: right; }
    """

    def __init__(self, rascunho: RascunhoServidor, papel_id: str, banco_id: str):
        super().__init__()
        v = rascunho.vista()
        p, b = v.papeis[papel_id], v.bancos[banco_id]
        self.titulo = t("usu.f.explica", papel=p.nome, banco=b.nome)
        self.origens: List[str] = []
        self.ofertas: List[Tuple[str, OperacaoServidor, Optional[str]]] = []
        if p.superusuario:
            self.origens.append(t("usu.f.origem_super"))
        if b.publico:
            self.origens.append(t("usu.f.origem_public"))
            op = TirarAcesso(PUBLIC, banco_id)
            self.ofertas.append((t("usu.f.tirar_public"), op, recusa(rascunho, [op])))
        for g in p.membro_de:
            if g in p.nao_herda or g not in v.papeis:
                continue
            dao = [x for x in [g] + sorted(_srv.grupos(v, g, herdados=True)) if x in b.acesso_direto]
            if not dao:
                continue
            nome = v.papeis[g].nome
            if g in b.acesso_direto:
                self.origens.append(t("usu.f.origem_grupo", grupo=nome))
            else:
                self.origens.append(t("usu.f.origem_grupo_por", grupo=nome,
                                      por=", ".join(sorted(v.papeis[x].nome for x in dao))))
            op = TirarDoGrupo(papel_id, g)
            self.ofertas.append((t("usu.f.tirar_grupo", papel=p.nome, grupo=nome), op, recusa(rascunho, [op])))

    def compose(self) -> ComposeResult:
        texto = Text("\n".join("• " + o for o in self.origens))
        for rotulo, _, motivo in self.ofertas:
            if motivo is not None:
                texto.append(f"\n✖ {rotulo}: {motivo}", style=COR["laranja"])
        with Vertical():
            yield Static(Text(self.titulo, style="bold"))
            yield Static(texto, id="explica_origens")
            with Vertical(id="ofertas"):
                for k, (rotulo, _, motivo) in enumerate(self.ofertas):
                    yield Button(rotulo, id=f"oferta_{k}", variant="warning", disabled=motivo is not None)
            with Horizontal(id="botoes"):
                yield Button(t("canvas.f.cancelar"), id="cancelar")

    def on_mount(self) -> None:
        self.query_one("#cancelar", Button).focus()

    def on_button_pressed(self, evento: Button.Pressed) -> None:
        evento.stop()
        ident = evento.button.id or ""
        if ident.startswith("oferta_"):
            self.dismiss(self.ofertas[int(ident.split("_")[1])][1])
        else:
            self.dismiss(None)
