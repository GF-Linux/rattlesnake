"""A tela Usuários: o servidor inteiro — quem entra em que banco, os poderes, os grupos — e o rascunho do servidor.

    ratt db · localhost:5432 › Usuários · rascunho: 1
    usuário        poderes                 grupos        estudos  sistema  lhv_setup
    PUBLIC                                               —        C        —
    jared ●        superusuário            —             D        c        D
    app            entra, cria bancos      leitura       c        D        —
    leitura        não entra (grupo)       —             C        c        —
    + ana          entra                   leitura       c        c        —
    ┌ SQL ─────────────────────────────────────────────────────────────────────┐

Em cada banco: `D` dono, `C` o CONNECT dado direto ao papel, `c` herdado (pelo PUBLIC, por um grupo ou por ser
superusuário), `—` não entra. A linha `PUBLIC` (todo mundo) fica fixa no alto e a coluna do nome fixa à esquerda:
com muitos bancos a grade rola e as duas continuam à vista. A grade mostra o servidor com o rascunho por cima (o
que muda fica amarelo; o papel novo, com `+`; o removido, com `−`).

Toda leitura roda numa thread, pela `fonte` (`FonteServidor`, ou uma falsa nos testes); a vigia olha a assinatura
do servidor (`pg_roles`, `pg_auth_members`, `pg_database`) e, se mudou, relê o modelo e refaz o rascunho por cima
(`rebase`: a lista de bancos de cada remoção fica a de agora). O aplicar é o do rascunho do servidor
(`painel.AcoesRascunhoServidor`): banco a banco, a senha pedida só ali, o sudo quando falta poder.
"""
from __future__ import annotations

import threading
from typing import List, Optional, Set

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from ratt.db import acesso, catalogo_servidor, checagem_servidor, servidor as _srv, sessao as _sessao
from ratt.db.formularios_usuarios import (ConfirmarRemocao, EscolherPapel, ExplicarAcesso, FormGrupos,
                                          FormNovoUsuario, FormPoderes, texto_avisos)
from ratt.db.grade_dados import Grade
from ratt.db.idioma import t
from ratt.db.painel import AcoesRascunhoServidor, ConexoesServidor, PainelSQL
from ratt.db.rascunho_servidor import PUBLIC, DarAcesso, RascunhoServidor, TirarAcesso
from ratt.db.servidor import ModeloServidor, Papel
from ratt.db.tela import COR

_COL_BANCOS = 3                     # usuário, poderes, grupos; depois um banco por coluna


class FonteServidor:
    """O que a tela Usuários lê do servidor, pela SUA conexão ao banco da tela (aberta na primeira leitura): a
    assinatura, o modelo e os avisos de uma operação (estes entram em outros bancos, por conexões só deles)."""

    def __init__(self, porta: int, usuario: str, banco: str):
        self.porta, self.usuario, self.banco = porta, usuario, banco
        self._conexao = None
        self._fechada = False
        self._trava = threading.Lock()
        self._conexoes: Optional[ConexoesServidor] = None
        self._trava_avisos = threading.Lock()   # os avisos usam as conexões numa transação: um por vez

    def _aberta(self):
        with self._trava:
            if self._fechada:
                raise RuntimeError(t("canvas.fonte_fechada"))
            if self._conexao is None:
                self._conexao, _ = acesso.entrar(self.porta, self.usuario, self.banco)
                if self._conexao is None:
                    raise RuntimeError(t("run.sem_acesso", usuario=self.usuario, banco=self.banco))
            return self._conexao

    def assinatura(self):
        return catalogo_servidor.assinatura(self._aberta())

    def modelo(self) -> ModeloServidor:
        return catalogo_servidor.modelo_servidor(self._aberta())

    def avisos(self, op, r: RascunhoServidor):
        with self._trava_avisos:
            if self._fechada:
                raise RuntimeError(t("canvas.fonte_fechada"))
            if self._conexoes is None:
                self._conexoes = ConexoesServidor(self.porta, self.usuario, self.banco)
            return checagem_servidor.avisos_da_operacao_servidor(op, r, self._conexoes)

    def fonte_banco(self):
        """Uma fonte do schema (a do canvas) no banco desta tela, com a sua própria conexão: a tela Permissões (`P`)."""
        from ratt.db import canvas
        return canvas.FonteBanco(None, self.porta, self.usuario, self.banco)

    def fechar(self) -> None:
        with self._trava:
            if self._conexao is not None:
                self._conexao.close()
            self._conexao, self._fechada = None, True
        with self._trava_avisos:
            if self._conexoes is not None:
                self._conexoes.fechar()


# ── o que a grade mostra (puro) ──

def celula_acesso(m: ModeloServidor, papel_id: str, banco_id: str) -> str:
    """"D" dono, "C" CONNECT dado direto, "c" herdado (PUBLIC, grupo, superusuário), "—" não entra. `papel_id`
    PUBLIC: "C" se o PUBLIC tem o CONNECT."""
    b = m.bancos[banco_id]
    if papel_id == PUBLIC:
        return "C" if b.publico else "—"
    if b.dono == papel_id:
        return "D"
    if papel_id in b.acesso_direto:
        return "C"
    return "c" if _srv.acesso(m, papel_id, banco_id) != "nenhum" else "—"


def poderes_texto(p: Papel) -> str:
    if p.superusuario:
        partes = [t("usu.p.super")] + ([] if p.login else [t("usu.p.nao_entra")])
    else:
        partes = [t("usu.p.entra") if p.login else t("usu.p.nao_entra_grupo")]
        partes += [t(chave) for sim, chave in ((p.cria_bancos, "usu.p.cria_bancos"), (p.cria_papeis, "usu.p.cria_papeis"))
                   if sim]
    return ", ".join(partes)


def grupos_texto(m: ModeloServidor, p: Papel) -> str:
    nomes = sorted(m.papeis[g].nome for g in p.membro_de if g in m.papeis)
    return ", ".join(nomes) or "—"


_ESTILO_CELULA = {"D": "bold", "C": "", "c": COR["cinza"], "—": COR["cinza"]}


class TelaUsuarios(AcoesRascunhoServidor, Screen):
    BINDINGS = [
        Binding("a", "novo", t("usu.k.novo")),
        Binding("space", "poderes", t("usu.k.poderes")),
        Binding("g", "grupos", t("usu.k.grupos")),
        Binding("c", "acesso", t("usu.k.acesso")),
        Binding("o", "dono", t("usu.k.dono")),
        Binding("m", "marcar", t("usu.k.marcar")),
        Binding("x", "remover", t("usu.k.remover")),       # com o foco no painel, o x dele (tirar) vale antes
        Binding("enter", "aplicar", t("canvas.k.aplicar")),
        Binding("e", "ensaio", t("canvas.k.ensaio")),
        Binding("w", "exportar", t("canvas.k.exportar")),
        Binding("u", "desfazer", t("canvas.k.desfazer")),  # aqui dentro, u desfaz (na tela do banco, abre esta)
        Binding("r", "painel", t("canvas.k.rascunho")),
        Binding("P,shift+p", "permissoes", t("perm.k.abrir")),
        Binding("escape,q", "voltar", t("canvas.k.voltar")),
    ]
    VIGIA_SEGUNDOS = 2.0                    # de quanto em quanto tempo a vigia olha o servidor (0: só à mão)
    DEFAULT_CSS = """
    #usu_titulo { height: 1; padding: 0 1; background: $panel; }
    #usu_grade { height: 1fr; }
    #usu_avisos { height: auto; max-height: 3; padding: 0 1; }
    """

    def __init__(self, fonte):
        super().__init__()
        self.fonte = fonte
        self.sessao: Optional[_sessao.SessaoServidor] = None
        self.carregado = False
        self.marcados: Set[str] = set()     # papéis marcados (m) para remover juntos
        self._linhas: List[str] = []        # o id de cada linha da grade (PUBLIC na primeira)
        self._bancos: List[str] = []        # o id do banco de cada coluna, a partir de _COL_BANCOS
        self._avisos_op: list = []          # os avisos da última operação posta por esta tela
        self._marca = None                  # a assinatura do servidor na última leitura do modelo
        self._trava = threading.Lock()      # uma conversa com o servidor por vez nesta tela
        self._olhando = False               # uma olhada da vigia está no ar (posto e tirado na thread da tela)
        self._olhada = 0
        self._calado = False
        self._relendo: Optional[_sessao.SessaoServidor] = None
        self._listando = False              # o P está listando os schemas (numa thread)
        self._avisou_erro = False           # a primeira leitura falhou e já disse (uma vez só)

    def compose(self) -> ComposeResult:
        yield Static(id="usu_titulo")
        yield Grade(id="usu_grade", cursor_type="cell", fixed_rows=1, fixed_columns=1, zebra_stripes=True)
        yield Static(id="usu_avisos")
        yield PainelSQL(id="usu_sql")
        yield Footer()

    def on_mount(self) -> None:
        self.sessao = _sessao.obter_servidor(self.app, self.fonte)
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

    # ── olhar o servidor ────────────────────────────────────────────────────

    def vigiar(self) -> None:
        if not self._olhando:              # servidor lento: uma olhada por vez, sem empilhar threads
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

    @work(thread=True, exclusive=True, group="vigia_usuarios")
    def _olhar(self, primeira: bool, olhada: int = 0) -> None:
        try:
            try:
                with self._trava:
                    marca = self.fonte.assinatura()
                    m = self.fonte.modelo() if primeira or marca != self._marca else None
            except Exception as erro:
                if primeira:
                    self._de_volta(self._nao_leu, erro)
                return
            if m is not None:
                self._de_volta(self._modelo_chegou, marca, m)
        finally:
            self._de_volta(self._olhou, olhada)

    def _nao_leu(self, erro) -> None:
        if self.is_attached and not self.carregado and not self._avisou_erro:
            self._avisou_erro = True        # a vigia segue tentando; o aviso sai uma vez
            linhas = str(erro).strip().splitlines()
            self.notify(t("canvas.erro_banco", erro=linhas[0] if linhas else type(erro).__name__), severity="error")

    def _modelo_chegou(self, marca, m: ModeloServidor) -> None:
        if not self.is_attached or self.sessao.aplicando:
            return
        self._marca = marca
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
        v = r.vista()
        self._titulo()
        self._desenhar_grade(r.base, v)
        self._linha_avisos(v)
        self.query_one(PainelSQL).atualizar(r.passos(), self.sessao.falha, self.sessao.mensagem_falha,
                                            notas=r.notas())

    def _titulo(self) -> None:
        ops = len(self.sessao.rascunho.operacoes)
        self.query_one("#usu_titulo", Static).update(Text.assemble(
            ("ratt db", f"bold {COR['azul']}"), (" · ", COR["cinza"]), (f"localhost:{self.fonte.porta}", "bold"),
            (" › ", COR["cinza"]), (t("usu.titulo"), "bold"), (" · ", COR["cinza"]),
            (t("canvas.rascunho", n=ops), COR["laranja"] if ops else COR["cinza"])))

    @staticmethod
    def _bancos_visiveis(m: ModeloServidor):
        return sorted((b for b in m.bancos.values() if not b.template and b.estado != "apagado"), key=lambda b: b.nome)

    def _rotulo(self, p: Papel, eu: str) -> Text:
        nome = Text()
        if p.id in self.marcados:
            nome.append("▪ ", style=f"bold {COR['laranja']}")
        if p.estado == "novo":
            nome.append("+ ", style=COR["verde"])
            nome.append(p.nome, style=f"bold {COR['verde']}")
        elif p.estado == "apagado":
            nome.append("− ", style=COR["laranja"])
            nome.append(p.nome, style=f"strike {COR['laranja']}")
        else:
            nome.append(p.nome, style=f"bold {COR['amarelo']}" if p.estado == "mudado" else "bold")
        if p.id == eu:
            nome.append(" ●", style=COR["verde"])
        return nome

    def _celula(self, base: ModeloServidor, v: ModeloServidor, papel_id: str, banco_id: str) -> Text:
        p = v.papeis.get(papel_id)
        if p is not None and p.estado == "apagado":
            return Text("·", style=COR["cinza"])
        agora = celula_acesso(v, papel_id, banco_id)
        tinha = (papel_id == PUBLIC or papel_id in base.papeis) and banco_id in base.bancos
        if not tinha or celula_acesso(base, papel_id, banco_id) != agora:
            return Text(agora, style=f"bold {COR['amarelo']}")
        return Text(agora, style=_ESTILO_CELULA[agora])

    def _desenhar_grade(self, base: ModeloServidor, v: ModeloServidor) -> None:
        grade = self.query_one(Grade)
        bancos = self._bancos_visiveis(v)
        papeis = sorted((p for p in v.papeis.values() if not p.sistema), key=lambda p: p.nome)
        self.marcados &= {p.id for p in papeis if p.estado != "apagado" and not p.id.startswith("novo-")}
        cursor = grade.cursor_coordinate
        linha_antes = self._linhas[cursor.row] if 0 <= cursor.row < len(self._linhas) else None
        k = cursor.column - _COL_BANCOS
        banco_antes = self._bancos[k] if 0 <= k < len(self._bancos) else None
        self._linhas = [PUBLIC] + [p.id for p in papeis]
        self._bancos = [b.id for b in bancos]
        grade.clear(columns=True)
        for chave in ("usu.c.usuario", "usu.c.poderes", "usu.c.grupos"):
            grade.add_column(Text(t(chave), style="bold"))
        for b in bancos:
            rotulo = Text(b.nome, style="bold")
            if not b.aceita_conexao:
                rotulo.append(f" ({t('usu.c.fechado')})", style=COR["cinza"])
            grade.add_column(rotulo)
        grade.add_row(Text("PUBLIC", style=f"bold {COR['azul']}"), Text(""), Text(""),
                      *[self._celula(base, v, PUBLIC, b.id) for b in bancos])
        for p in papeis:
            grade.add_row(self._rotulo(p, v.eu), Text(poderes_texto(p), style=COR["cinza"] if not p.login else ""),
                          Text(grupos_texto(v, p)), *[self._celula(base, v, p.id, b.id) for b in bancos])
        linha = self._linhas.index(linha_antes) if linha_antes in self._linhas else min(cursor.row, len(self._linhas) - 1)
        coluna = (self._bancos.index(banco_antes) + _COL_BANCOS if banco_antes in self._bancos
                  else min(cursor.column, len(grade.columns) - 1))
        grade.cursor_coordinate = Coordinate(max(linha, 0), max(coluna, 0))

    def _linha_avisos(self, v: ModeloServidor) -> None:
        partes: List[Text] = []
        if self.marcados:
            nomes = sorted(v.papeis[p].nome for p in self.marcados if p in v.papeis)
            partes.append(Text(t("usu.marcados", papeis=", ".join(nomes)), style=COR["laranja"]))
        if self._avisos_op:
            avisos = texto_avisos(self._avisos_op)
            avisos.rstrip()                 # (no lugar: o Text.rstrip não devolve nada)
            partes.append(avisos)
        bancos = self._bancos_visiveis(v)
        publicos = [b.nome for b in bancos if b.publico and b.aceita_conexao]
        if publicos:
            partes.append(Text("· " + t("usu.public_info", bancos=", ".join(publicos)), style=COR["cinza"]))
        fechados = [b.nome for b in bancos if not b.aceita_conexao]
        if fechados:
            partes.append(Text("· " + t("usu.fechados_info", bancos=", ".join(fechados)), style=COR["cinza"]))
        self.query_one("#usu_avisos", Static).update(Text("\n").join(partes))

    # ── onde está o cursor ──────────────────────────────────────────────────

    def linha_id(self, i: int) -> str:
        """O id do papel (ou PUBLIC) da linha `i` da grade."""
        return self._linhas[i]

    def ir_para(self, papel_id: str, banco_id: Optional[str] = None) -> None:
        """Põe o cursor na linha do papel (e na coluna do banco, se vier)."""
        grade = self.query_one(Grade)
        coluna = self._bancos.index(banco_id) + _COL_BANCOS if banco_id in self._bancos else 0
        grade.cursor_coordinate = Coordinate(self._linhas.index(papel_id), coluna)

    def _papel_da_linha(self) -> Optional[str]:
        """O papel da linha do cursor, se é um papel que ainda existe na vista; senão diz o porquê e devolve None."""
        i = self.query_one(Grade).cursor_coordinate.row
        if not 0 <= i < len(self._linhas) or self._linhas[i] == PUBLIC:
            self.notify(t("usu.e.linha_papel"), severity="warning")
            return None
        return self._linhas[i] if self._existe(self._linhas[i]) else None

    def _existe(self, papel_id: str) -> bool:
        p = self.sessao.rascunho.vista().papeis.get(papel_id)
        if p is None:
            self.notify(t("canvas.e.sumiu"), severity="warning")
            return False
        if p.estado == "apagado":
            self.notify(t("usu.e.removido", papel=p.nome), severity="warning")
            return False
        return True

    def _banco_da_coluna(self, coluna: int) -> Optional[str]:
        k = coluna - _COL_BANCOS
        if not 0 <= k < len(self._bancos):
            self.notify(t("usu.e.coluna_banco"), severity="warning")
            return None
        return self._bancos[k]

    # ── o rascunho ──────────────────────────────────────────────────────────

    def _pronto(self) -> bool:
        if not self._livre():
            return False
        if not self.carregado:
            self.notify(t("canvas.carregando"))
        return self.carregado

    def _depois_de_mudar(self) -> None:
        self._avisos_op = []
        self._linha_avisos(self.sessao.rascunho.vista())

    def _abrir(self, form) -> None:
        """Abre um formulário; o que ele puser no rascunho a sessão grava e as telas ficam sabendo. Os avisos que o
        formulário contou vão para a linha de avisos."""
        def depois(ops) -> None:
            if not ops:
                return
            self._avisos_op = list(form.avisos_chegados or [])
            self.sessao.mudou()
        self.app.push_screen(form, depois)

    def _por(self, op) -> None:
        """Uma operação direto no rascunho (as células de acesso); recusada, diz o motivo (a trava, por exemplo).
        Os avisos dela chegam depois, de uma thread, à linha de avisos."""
        try:
            self.sessao.acrescentar(op)
        except ValueError as e:
            self.notify(str(e), severity="warning", timeout=8)
            return
        r = self.sessao.rascunho
        self._avisos_op = []
        self._linha_avisos(r.vista())
        self._avisar(op, RascunhoServidor(r.base, list(r.operacoes), r.proximo))

    @work(thread=True, exclusive=True, group="avisos_usuarios")
    def _avisar(self, op, r: RascunhoServidor) -> None:
        try:
            avisos = self.fonte.avisos(op, r)
        except Exception:
            avisos = []
        self._de_volta(self._avisos_chegaram, op, avisos)

    def _avisos_chegaram(self, op, avisos) -> None:
        if not self.is_attached or not any(o is op for o in self.sessao.rascunho.operacoes):
            return
        self._avisos_op = list(avisos)
        self._linha_avisos(self.sessao.rascunho.vista())

    def action_novo(self) -> None:
        if self._pronto():
            self._abrir(FormNovoUsuario(self.sessao, self.fonte))

    def action_poderes(self) -> None:
        if self._pronto():
            papel = self._papel_da_linha()
            if papel is not None:
                self._abrir(FormPoderes(self.sessao, self.fonte, papel))

    def action_grupos(self) -> None:
        if self._pronto():
            papel = self._papel_da_linha()
            if papel is not None:
                self._abrir(FormGrupos(self.sessao, self.fonte, papel))

    def action_dono(self) -> None:
        if not self._pronto():
            return
        banco = self._banco_da_coluna(self.query_one(Grade).cursor_coordinate.column)
        if banco is not None:
            self._abrir(EscolherPapel(self.sessao, self.fonte, banco))

    def action_acesso(self) -> None:
        cursor = self.query_one(Grade).cursor_coordinate
        self._acesso(cursor.row, cursor.column)

    def on_data_table_cell_selected(self, evento: DataTable.CellSelected) -> None:
        """Clicou numa célula de banco: o mesmo que `c` nela."""
        evento.stop()
        if evento.coordinate.column >= _COL_BANCOS:
            self._acesso(evento.coordinate.row, evento.coordinate.column)

    def _acesso(self, linha: int, coluna: int) -> None:
        """`C` tira o CONNECT, `—` dá; no PUBLIC, alterna o dele. `c` explica de onde vem (e oferece tirar do PUBLIC
        ou do grupo); `D` só explica (o dono troca-se com `o`)."""
        if not self._pronto():
            return
        banco = self._banco_da_coluna(coluna)
        if banco is None or not 0 <= linha < len(self._linhas):
            return
        papel = self._linhas[linha]
        r = self.sessao.rascunho
        v = r.vista()
        if papel == PUBLIC:
            self._por(TirarAcesso(PUBLIC, banco) if v.bancos[banco].publico else DarAcesso(PUBLIC, banco))
            return
        if not self._existe(papel):
            return
        celula = celula_acesso(v, papel, banco)
        if celula == "D":
            self.notify(t("usu.explica_dono", papel=v.papeis[papel].nome, banco=v.bancos[banco].nome))
        elif celula == "C":
            self._por(TirarAcesso(papel, banco))
        elif celula == "—":
            self._por(DarAcesso(papel, banco))
        else:
            self.app.push_screen(ExplicarAcesso(r, papel, banco), lambda op: self._por(op) if op else None)

    def action_marcar(self) -> None:
        papel = self._papel_da_linha()
        if papel is None:
            return
        if papel.startswith("novo-"):
            self.notify(t("usu.e.pendente", papel=self.sessao.rascunho.vista().papeis[papel].nome), severity="warning")
            return
        self.marcados ^= {papel}
        self._montar()

    def action_remover(self) -> None:
        if not self._pronto():
            return
        if self.marcados:
            v = self.sessao.rascunho.vista()
            papeis = sorted(self.marcados, key=lambda p: v.papeis[p].nome)
        else:
            papel = self._papel_da_linha()
            if papel is None:
                return
            papeis = [papel]
        v = self.sessao.rascunho.vista()
        for p in papeis:
            if p.startswith("novo-"):
                self.notify(t("usu.e.pendente", papel=v.papeis[p].nome), severity="warning")
                return
            if not self._existe(p):
                return
        form = ConfirmarRemocao(self.sessao, self.fonte, tuple(papeis))

        def depois(ops) -> None:
            if not ops:
                return
            self.marcados -= set(papeis)
            self._avisos_op = list(form.avisos_chegados or [])
            self.sessao.mudou()
        self.app.push_screen(form, depois)

    def action_voltar(self) -> None:
        self.app.pop_screen()

    # ── P: as permissões de um schema do banco desta tela ───────────────────

    def action_permissoes(self) -> None:
        """`P`: escolher um schema do banco desta tela e abrir a tela Permissões dele (com uma conexão só dela, que
        também lista os schemas; o rascunho é o do schema, o mesmo do canvas)."""
        criar = getattr(self.fonte, "fonte_banco", None)
        if not callable(criar) or self._listando:   # um P por vez: cada um abre uma conexão
            return
        self._listando = True
        self._listar_esquemas(criar())

    @work(thread=True, group="esquemas_usuarios")
    def _listar_esquemas(self, banco) -> None:
        try:
            nomes = banco.esquemas()
        except Exception as erro:
            banco.fechar()
            self._de_volta(self._nao_listou, erro)
            return
        self._de_volta(self._escolher_esquema, banco, nomes)

    def _nao_listou(self, erro) -> None:
        self._listando = False
        if self.is_attached:
            linhas = str(erro).strip().splitlines()
            self.notify(t("canvas.erro_banco", erro=linhas[0] if linhas else type(erro).__name__), severity="error")

    def _escolher_esquema(self, banco, nomes: List[str]) -> None:
        from ratt.db import tela_permissoes
        from ratt.db.canvas import EscolherEsquema
        self._listando = False
        if not self.is_attached:
            banco.fechar()
            return

        def escolhido(nome) -> None:
            if not nome:
                banco.fechar()
                return
            self.app.push_screen(tela_permissoes.TelaPermissoes(banco, nome))
        self.app.push_screen(EscolherEsquema(nomes, "public"), escolhido)

    # ── depois de aplicar ───────────────────────────────────────────────────

    def depois_de_aplicar(self, esquema, nomes) -> None:
        """O servidor gravou: relê o modelo (o papel criado passa a ter o id do catálogo) e o entrega à sessão
        (`aplicado`: o rascunho volta a ser editável)."""
        self._relendo = self.sessao
        self._reler_aplicado()

    @work(thread=True, exclusive=True, group="aplicar_usuarios")
    def _reler_aplicado(self) -> None:
        try:
            with self._trava:
                marca = self.fonte.assinatura()
                m = self.fonte.modelo()
        except Exception as erro:
            self._de_volta(self._aplicado, None, None, erro)
            return
        self._de_volta(self._aplicado, marca, m, None)

    def _aplicado(self, marca, m: Optional[ModeloServidor], erro) -> None:
        s, self._relendo = self._relendo, None
        if m is None:
            if s is not None:
                s.aplicando = False
            if self.is_attached:
                self._marca = None          # a vigia traz o servidor na próxima volta
                linhas = str(erro).strip().splitlines()
                self.notify(t("canvas.erro_banco", erro=linhas[0] if linhas else type(erro).__name__),
                            severity="error")
            return
        if s is not None:
            self._calada(s.aplicado, m)
        if not self.is_attached:
            return
        self._marca = marca
        self._avisos_op = []
        self._montar()
