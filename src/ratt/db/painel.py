"""O que toda tela com rascunho divide: o painel do SQL e as ações sobre o rascunho (aplicar, ensaio, exportar,
desfazer, tirar do meio).

`PainelSQL` só mostra os passos do rascunho. `AcoesRascunho` é um mixin de `Screen`: usa `self.sessao` (a
`sessao.SessaoRascunho` do schema) e `self.fonte`; a tela que o usa implementa `_pronto()` (pode mexer agora?) e
`depois_de_aplicar(esquema, nomes)` (o banco gravou: trazer o catálogo novo). Nada aqui executa DDL fora do aplicar
e do ensaio, que entregam o rascunho ao mesmo executor do `ratt db run`.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Group
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.geometry import Region
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from ratt.db import formularios, guardar, rascunho as _rascunho
from ratt.db.idioma import t
from ratt.db.tela import COR


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
        self.mensagem: Optional[str] = None # o porquê, quando foi a contagem (aparece junto da marca)
        self._linhas: List[int] = []        # linha em que cada operação começa no texto desenhado
        self._plano = ""
        self.topo: Optional[str] = None     # o SELECT do filtro (tela Dados), acima do rascunho

    def compose(self) -> ComposeResult:
        yield Static(id="sql_conteudo")

    def texto_plano(self) -> str:
        return self._plano

    def atualizar(self, passos, falhou: Optional[int] = None, mensagem: Optional[str] = None,
                  topo: Optional[str] = None) -> None:
        self.passos = passos
        self.falhou = falhou
        self.mensagem = mensagem
        self.topo = topo
        self.indice = max(0, min(self.indice, len(passos) - 1))
        self._desenhar()

    def _desenhar(self) -> None:
        alvo = self.query_one("#sql_conteudo", Static)
        if not self.passos and not self.topo:
            self._linhas, self._plano = [], t("canvas.sem_rascunho")
            alvo.update(Text(self._plano, style=COR["cinza"]))
            return
        partes, plano, y, self._linhas = [], [], 0, []
        if self.topo:
            codigo = f"-- {t('dados.o_filtro')}\n{self.topo}"
            partes.append(Syntax(codigo, "sql", theme="ansi_dark", background_color="default", word_wrap=True))
            plano.append(codigo)
            y = len(codigo.splitlines())
            if not self.passos:
                partes += [Text(""), Text(t("canvas.sem_rascunho"), style=COR["cinza"])]
                plano += ["", t("canvas.sem_rascunho")]
        for n, (op, valida, desc, comandos) in enumerate(self.passos):
            escolhida = self.has_focus and n == self.indice
            fundo = "#1f3550" if escolhida else None
            if n or self.topo:
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
                marca = t("canvas.falhou") + (f" — {self.mensagem}" if self.mensagem else "")
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


# ── perguntar um nome (ou um caminho) ───────────────────────────────────────

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



# ── as ações sobre o rascunho ───────────────────────────────────────────────

class AcoesRascunho:
    """Mixin de `Screen`: aplicar, ensaio, exportar, desfazer, o painel e tirar uma operação do meio, sobre
    `self.sessao`. A tela implementa `_pronto()` e `depois_de_aplicar(esquema, nomes)`; pode implementar
    `_depois_de_mudar()` (a tela mudou o rascunho por uma tecla: por exemplo, manter a seleção à vista)."""

    def _pronto(self) -> bool:
        raise NotImplementedError

    def depois_de_aplicar(self, esquema: str, nomes: Dict[str, str]) -> None:
        raise NotImplementedError

    def _depois_de_mudar(self) -> None:
        pass

    def _livre(self) -> bool:
        """Entre o commit e a releitura o rascunho já foi esvaziado e o modelo ainda é o velho: nada de editar,
        aplicar de novo, exportar nem trocar de schema nesse intervalo."""
        if self.sessao.aplicando:
            self.notify(t("canvas.aplicando"), severity="warning")
        return not self.sessao.aplicando

    # ── desfazer, o painel e tirar do meio ──────────────────────────────────

    def action_desfazer(self) -> None:
        if not self._livre():
            return
        if self.sessao.desfazer() is None:
            self.notify(t("canvas.nada_desfazer"))
            return
        self._depois_de_mudar()

    def action_painel(self) -> None:
        self.query_one(PainelSQL).focus()

    def on_painel_sql_tirar(self, evento: PainelSQL.Tirar) -> None:
        if not self._livre():
            return
        r = self.sessao.rascunho
        i = evento.indice
        if i >= len(r.operacoes):
            return
        dependentes = r.dependentes(i)
        if not dependentes:
            self._tirar([i])
            return
        passos = r.passos()
        descricoes = [passos[j][2] if passos[j][1] else passos[j][0].tipo for j in dependentes]
        self.app.push_screen(formularios.ConfirmarTirar(descricoes),
                             lambda sim: self._tirar([i] + dependentes) if sim else None)

    def _tirar(self, indices) -> None:
        self.sessao.tirar(indices)
        self._depois_de_mudar()

    # ── aplicar, ensaiar e exportar ─────────────────────────────────────────

    def _cabecalho(self) -> str:
        return self.sessao.cabecalho()

    def _pode_enviar(self) -> bool:
        """Vazio ou com operações que deixaram de valer: não vai ao banco nem ao arquivo."""
        if not self._pronto():
            return False
        if not self.sessao.rascunho.operacoes:
            self.notify(t("canvas.rascunho_vazio"), severity="warning")
            return False
        invalidas = self.sessao.rascunho.invalidas
        if invalidas:
            self.notify(t("canvas.tem_invalidas", n=len(invalidas)), severity="warning")
            return False
        return True

    def action_aplicar(self) -> None:
        if not self._pode_enviar():
            return
        passos = self.sessao.rascunho.passos()
        texto = self.sessao.rascunho.sql_texto(self._cabecalho())
        pergunta = t("canvas.f.aplicar_pergunta", banco=self.fonte.banco, n=len(passos))
        destrutivas = self._destrutivas(passos)
        if destrutivas:
            muda = any(valida and isinstance(op, _rascunho.Alterar) for op, valida, _, _ in passos)
            pergunta += "\n" + t("dados.d.titulo" if muda else "canvas.d.titulo")
        # foco em "cancelar": um Enter que sobrou no teclado nunca grava no banco
        self.app.push_screen(
            formularios.Confirmar(pergunta, destrutivas, t("canvas.f.aplicar"), foco="nao", sql=texto),
            lambda sim: self._rodar(False, texto) if sim else None)

    def _destrutivas(self, passos) -> List[str]:
        """Uma linha por operação que apaga (tabela, coluna, restrição, linhas, duplicatas) ou muda linhas (UPDATE),
        no alto da confirmação: o que some junto, com a contagem de linhas quando ela foi feita ao pôr a operação no
        rascunho (nas de dados, quantas a pessoa viu)."""
        linhas = []
        for op, valida, desc, _ in passos:
            if not valida:
                continue
            if isinstance(op, (_rascunho.Apagar, _rascunho.ApagarDuplicatas, _rascunho.Alterar)):
                n = op.vista
                detalhe = (t("canvas.d.nao_contado") if n is None else
                           t("dados.d.vista_uma") if n == 1 else t("dados.d.vistas", n=n))
            elif isinstance(op, _rascunho.ApagarTabela):
                n = self.sessao.contagens.get(op.tabela)
                detalhe = t("canvas.d.tabela", n=n) if n is not None else t("canvas.d.nao_contado")
            elif isinstance(op, _rascunho.ApagarColuna):
                detalhe = t("canvas.d.coluna")
            elif isinstance(op, _rascunho.ApagarLigacao) or (isinstance(op, _rascunho.Unica) and not op.sim):
                detalhe = t("canvas.d.restricao")
            else:
                continue
            if getattr(op, "cascata", False):
                detalhe += t("canvas.d.cascata")
            linhas.append(f"{desc} — {detalhe}")
        return linhas

    def action_ensaio(self) -> None:
        if self._pode_enviar():
            self._rodar(True)

    def _rodar(self, ensaio: bool, confirmado: Optional[str] = None) -> None:
        """Entrega o rascunho ao `ratt db run`: a tela se suspende, a pessoa vê a mesma saída e volta com Enter.
        Tudo o que decide algo depois está em `_concluir`, que roda fora da suspensão (e é testável).
        `confirmado` é o SQL que a pessoa viu ao confirmar: se o rascunho mudou desde então, pergunta de novo."""
        if not self._pode_enviar():             # o banco pode ter mudado enquanto a confirmação estava aberta
            return
        cabecalho = self._cabecalho()
        texto = self.sessao.rascunho.sql_texto(cabecalho)
        if confirmado is not None and texto != confirmado:
            self.notify(t("canvas.sql_mudou"), severity="warning")
            self.action_aplicar()
            return
        arquivo = guardar.arquivo_aplicando(self.sessao.porta, self.sessao.banco, self.sessao.esquema)
        conexao = self.fonte.conexao_para_aplicar()
        if conexao is None:
            self.notify(t("canvas.sem_conexao"), severity="error")
            return
        try:
            with self.app.suspend():
                resultado = self._sessao(conexao, arquivo, ensaio, cabecalho)
        except (Exception, KeyboardInterrupt) as erro:     # o rascunho fica: nada foi decidido
            self.notify(t("canvas.erro_aplicar", erro=str(erro) or type(erro).__name__), severity="error")
            return
        finally:
            conexao.close()
        self._concluir(resultado[0], resultado[1], ensaio, texto)

    def _sessao(self, conexao, arquivo: Path, ensaio: bool, cabecalho: str) -> tuple:
        """A parte que fala com o terminal de verdade (não roda sob `run_test`): cabeçalho, execução, Enter.
        Roda pela sessão, com o mesmo `cabecalho` do SQL confirmado; devolve (código, índice que falhou, mensagem)."""
        from ratt.cli import c
        modo = c(f" · {t('run.modo_ensaio')}", "amarelo") if ensaio else ""
        quem = f"{t('canvas.run_rascunho')} · {getattr(self.fonte, 'usuario', None) or getpass.getuser()} @ {self.fonte.banco}"
        print(f"{c('ratt db run', 'azul', 'negrito')} {c(quem, 'cinza')}{modo}")
        try:
            with conexao:
                resultado = self.sessao.aplicar(conexao, arquivo, ensaio, cabecalho)
        except KeyboardInterrupt:       # Ctrl+C no meio: nada fica, o rascunho continua, a tela não cai
            try:
                conexao.rollback()
            except Exception:
                pass
            print("\n" + c(f"↺ {t('canvas.interrompido')}", "amarelo"))
            resultado = (1, None, None)
        try:
            input("\n" + t("canvas.enter_volta"))
        except (EOFError, KeyboardInterrupt):   # Ctrl+C aqui é o mesmo que Enter: o que rodou já está decidido
            pass
        try:                    # o Textual não descarta o que foi digitado enquanto estava suspenso
            import termios
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
        except Exception:
            pass
        return resultado

    def _concluir(self, codigo: int, indice: Optional[int], ensaio: bool, texto: str) -> None:
        """O que o aplicar decidiu, fora da suspensão: a sessão marca, arquiva e esvazia; a tela avisa e, se o banco
        gravou, traz o catálogo novo (`depois_de_aplicar`) com os nomes das tabelas criadas pelo rascunho."""
        if codigo != 0:
            self.sessao.concluir(codigo, indice, ensaio, texto)
            chave = "canvas.ensaio_falhou" if ensaio else "canvas.aplicar_falhou"
            texto = self.sessao.mensagem_falha or t(chave if self.sessao.falha is not None else chave + "_sem_marca")
            self.notify(texto, severity="error")
            return
        if ensaio:
            self.sessao.concluir(codigo, indice, ensaio, texto)
            self.notify(t("canvas.ensaio_ok"))
            return
        nomes = {i: tab.nome for i, tab in self.sessao.rascunho.vista().tabelas.items()
                 if i.startswith("novo-t") and tab.estado != "apagado"}
        esquema = self.sessao.esquema
        self.sessao.concluir(codigo, indice, ensaio, texto)
        self.depois_de_aplicar(esquema, nomes)

    def action_exportar(self) -> None:
        if not self._pode_enviar():
            return
        padrao = guardar.nome_exportado(self.fonte.banco, self.sessao.esquema)
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
        # o arquivo exportado não leva BEGIN/COMMIT (o `ratt db run` já roda numa transação); o comentário do topo
        # diz como rodar tudo de uma vez fora do ratt
        dica = "".join("-- " + linha + "\n" for linha in t("canvas.dica_exportado", arquivo=caminho.name).splitlines())
        try:
            caminho.write_text(dica + self.sessao.rascunho.sql_texto(self._cabecalho()), encoding="utf-8")
        except OSError as erro:
            self.notify(t("canvas.erro_exportar", caminho=caminho, erro=erro), severity="error")
            return
        self.notify(t("canvas.exportado", caminho=caminho))
