"""A sessão de rascunho: o rascunho de um banco+schema, dividido por todas as telas que mexem nele.

Uma por `(porta, banco, esquema)`, guardada na aplicação (`obter`): o canvas e a tela Dados usam o MESMO objeto
em memória, de modo que uma tela coberta nunca grava uma versão velha. A sessão é a única que escreve o rascunho
no disco (`persistir`); quem muda o rascunho por ela avisa os ouvintes (as telas redesenham painel e título).

Toda mudança no rascunho avisa os ouvintes, inclusive as que vêm de `abrir`, `rebase` e `reler` (chamados pela
tela que acabou de ler o banco): essa tela se redesenha sozinha em seguida e ignora o próprio eco.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from ratt.db import guardar, rascunho as _rascunho
from ratt.db.idioma import t
from ratt.db.modelo import Modelo


class Aviso(str):
    """Um texto a notificar, com a severidade e o tempo com que a tela o mostra (é um `str` como outro qualquer)."""

    def __new__(cls, texto: str, severidade: str = "information", tempo: Optional[float] = None):
        aviso = super().__new__(cls, texto)
        aviso.severidade, aviso.tempo = severidade, tempo
        return aviso

    def opcoes(self) -> dict:
        """Os argumentos de `notify` além do texto."""
        return {"severity": self.severidade} if self.tempo is None else {"severity": self.severidade, "timeout": self.tempo}


def executar_rascunho(r: _rascunho.Rascunho, conexao, arquivo: Path, ensaio: bool, cabecalho: str) -> Tuple[int, Optional[int]]:
    """Grava o SQL do rascunho em `arquivo` e o roda com o mesmo executor do `ratt db run`.
    Devolve (código, índice da operação que falhou ou None). O cabeçalho entra no texto e na conta das linhas."""
    from ratt.db import rodar
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(r.sql_texto(cabecalho), encoding="utf-8")
    falhou: List[int] = []

    def ao_falhar(comando) -> None:
        indice = r.operacao_da_linha(comando.linha, cabecalho)
        if indice >= 0:
            falhou.append(indice)

    codigo = rodar.executar(arquivo, conexao, ensaio=ensaio, ao_falhar=ao_falhar)
    return codigo, (falhou[0] if falhou else None)


class SessaoRascunho:
    def __init__(self, porta: int, banco: str, esquema: str, app=None):
        self.porta, self.banco, self.esquema = porta, banco, esquema
        self.rascunho = _rascunho.Rascunho(Modelo(esquema))
        self.falha: Optional[int] = None    # operação que falhou ao aplicar; some na próxima mudança do rascunho
        self.aplicando = False              # entre o commit e a releitura: ninguém mexe no rascunho
        self.sem_gravar = False             # não deu para LER o rascunho deste schema: gravar seria por cima do que não se viu
        self.contagens: Dict[str, int] = {}     # linhas de cada tabela apagada, contadas ao pôr no rascunho
        self._gravado: Optional[dict] = None    # o rascunho como está no disco, até onde a sessão sabe
        self._aberta = False                # já leu o disco uma vez?
        self._ouvintes: List[Callable[[], None]] = []
        self._app = app

    # ── ouvintes e avisos ───────────────────────────────────────────────────

    def ouvir(self, fn: Callable[[], None]) -> None:
        if fn not in self._ouvintes:
            self._ouvintes.append(fn)

    def parar(self, fn: Callable[[], None]) -> None:
        if fn in self._ouvintes:
            self._ouvintes.remove(fn)

    def _avisar_ouvintes(self) -> None:
        for fn in list(self._ouvintes):
            fn()

    def _avisar(self, aviso: Aviso) -> None:
        """Erros de disco e o "aplicado" vão direto à aplicação (é o mesmo que a tela notificar)."""
        notify = getattr(self._app, "notify", None)
        if notify is not None:
            notify(str(aviso), **aviso.opcoes())

    # ── o disco ─────────────────────────────────────────────────────────────

    def abrir(self, base: Modelo) -> List[str]:
        """Chegou o modelo do banco a uma tela que abre este schema. Na primeira vez (ou se a última leitura do
        disco falhou e nada entrou no rascunho desde então) o rascunho vem do disco; nas seguintes, se o disco
        mudou por fora, vale o do disco; senão o da memória é rebaseado. Um rascunho com operações na memória
        nunca é trocado pelo do disco enquanto `sem_gravar`. Devolve os avisos que a tela deve mostrar."""
        if not self._aberta or (self.sem_gravar and not self.rascunho.operacoes):
            self._aberta, self.sem_gravar = True, False
            avisos = self._ler(base)
            self._avisar_ouvintes()
            return avisos
        avisos = self.reler(base)
        if avisos is not None:
            return avisos
        novas = self.rebase(base)
        return [Aviso(t("canvas.invalidas", n=len(novas)), "warning")] if novas else []

    def reler(self, base: Modelo) -> Optional[List[str]]:
        """Se o rascunho no disco não é mais o que a sessão gravou ou leu (outro processo mexeu nele), vale o do
        disco. None quando nada mudou (ou não deu para ler agora: fica o da memória, nada se grava por cima). Sem
        conseguir gravar (`sem_gravar`), o que entrou na memória só existe ali: não é trocado pelo do disco."""
        if self.sem_gravar and self.rascunho.operacoes:
            return None
        try:
            dados = guardar.ler_rascunho(self.porta, self.banco, self.esquema)
        except _rascunho.RascunhoCorrompido:
            dados = {}                          # diferente de qualquer coisa: `_ler` o põe de lado
        except OSError:
            return None
        if dados == self._gravado:
            return None
        avisos = self._ler(base)
        self._avisar_ouvintes()
        return avisos

    def _ler(self, base: Modelo) -> List[str]:
        avisos: List[str] = []
        self._gravado = None
        try:
            try:
                dados = guardar.ler_rascunho(self.porta, self.banco, self.esquema)
            except OSError as erro:
                # o arquivo pode ter operações que não se leram: nesta sessão o rascunho deste schema não vai ao disco
                if not self.sem_gravar:
                    avisos.append(Aviso(t("canvas.sem_gravar", erro=erro), "error", 15))
                self.sem_gravar = True
                self.rascunho = _rascunho.Rascunho(base)
                return avisos
            if dados is None:
                self.rascunho = _rascunho.Rascunho(base)
                return avisos
            r = _rascunho.Rascunho.de_dict(dados, base)
            self._gravado = dados
        except _rascunho.RascunhoCorrompido:
            try:
                caminho = guardar.separar_rascunho(self.porta, self.banco, self.esquema)
                avisos.append(Aviso(t("canvas.corrompido", caminho=caminho), "warning", 15))
            except OSError as erro:
                avisos.append(Aviso(t("canvas.erro_gravar", erro=erro), "error"))
            self.rascunho = _rascunho.Rascunho(base)
            return avisos
        if r.invalidas:
            avisos.append(Aviso(t("canvas.invalidas", n=len(r.invalidas)), "warning"))
        self.rascunho = r
        return avisos

    def persistir(self, so_se_mudou: bool = False) -> None:
        """Grava o rascunho; vazio apaga o arquivo. `so_se_mudou`: nada a fazer se o disco já tem exatamente isso
        (o rebase do vigia não reescreve o arquivo a cada volta)."""
        if self.sem_gravar:
            return
        dados = self.rascunho.para_dict() if self.rascunho.operacoes else None
        if dados is not None:
            dados = json.loads(json.dumps(dados))   # tuplas viram listas: igual ao que se lê do disco
        if so_se_mudou and dados == self._gravado:
            return
        try:
            guardar.gravar_rascunho(self.porta, self.banco, self.esquema, dados)
        except OSError as erro:
            self._avisar(Aviso(t("canvas.erro_gravar", erro=erro), "error"))
            return
        self._gravado = dados

    # ── mudar o rascunho ────────────────────────────────────────────────────

    def rebase(self, base: Modelo) -> Set[int]:
        """O banco mudou: nada se perde, o que deixou de valer fica marcado. Devolve as operações que passaram a
        não valer agora; grava (e avisa os ouvintes) só se o rascunho mudou de fato."""
        antes = self.rascunho.invalidas
        depois = self.rascunho.rebase(base)
        if depois != antes:
            self.persistir(so_se_mudou=True)
            self._avisar_ouvintes()
        return depois - antes

    def mudou(self) -> None:
        """Uma edição entrou ou saiu do rascunho (por estes métodos ou direto em `rascunho`): grava e avisa."""
        self.falha = None
        self.persistir()
        self._avisar_ouvintes()

    def acrescentar(self, op: _rascunho.Operacao) -> None:
        self.rascunho.acrescentar(op)
        self.mudou()

    def desfazer(self) -> Optional[_rascunho.Operacao]:
        op = self.rascunho.desfazer()
        if op is not None:
            self.mudou()
        return op

    def tirar(self, indices) -> None:
        self.rascunho.tirar(indices)
        self.mudou()

    # ── aplicar ─────────────────────────────────────────────────────────────

    def cabecalho(self) -> str:
        """A linha de comentário do topo do SQL. A mesma para o texto e para achar a operação pela linha."""
        return t("canvas.cabecalho_sql", banco=self.banco, esquema=self.esquema, n=len(self.rascunho.operacoes))

    def aplicar(self, conexao, arquivo: Path, ensaio: bool,
                cabecalho: Optional[str] = None) -> Tuple[int, Optional[int], Optional[str]]:
        """Roda o rascunho (ou só ensaia). Devolve (código, índice da operação que falhou, mensagem). `cabecalho`:
        o mesmo que entrou no SQL que a pessoa confirmou (sem ele, o de agora)."""
        if cabecalho is None:
            cabecalho = self.cabecalho()
        codigo, indice = executar_rascunho(self.rascunho, conexao, arquivo, ensaio, cabecalho)
        return codigo, indice, None

    def concluir(self, codigo: int, indice: Optional[int], ensaio: bool, texto: str) -> None:
        """O que o aplicar decidiu. Falhou: marca a operação. Ensaio que deu certo: tira a marca. Aplicado: o banco
        já gravou — arquiva o SQL e esvazia o rascunho AGORA, sem esperar a releitura (se a pessoa sair ou trocar
        de schema nesse intervalo, nada aplicado fica no disco como se estivesse pendente); `aplicando` fica ligado
        até a tela trazer o catálogo novo."""
        if codigo != 0:
            self.falha = indice if indice is not None and indice >= 0 else None
            self._avisar_ouvintes()
            return
        if ensaio:
            self.falha = None
            self._avisar_ouvintes()
            return
        try:
            caminho = guardar.guardar_aplicado(self.porta, self.banco, texto)
            self._avisar(Aviso(t("canvas.aplicado", caminho=caminho)))
        except OSError as e:
            self._avisar(Aviso(t("canvas.erro_gravar", erro=e), "error"))
        self.rascunho = _rascunho.Rascunho(self.rascunho.modelo())
        self.falha, self.contagens = None, {}
        self.persistir()
        self.aplicando = True
        self._avisar_ouvintes()

    def aplicado(self, base: Modelo) -> None:
        """Chegou o catálogo depois do aplicar: ele é a nova base."""
        self.aplicando = False
        self.rascunho = _rascunho.Rascunho(base)
        self._avisar_ouvintes()


def obter(app, fonte, esquema: str) -> SessaoRascunho:
    """A sessão de `(fonte.porta, fonte.banco, esquema)` guardada em `app._ratt_sessoes` (criada na primeira vez)."""
    sessoes = getattr(app, "_ratt_sessoes", None)
    if sessoes is None:
        sessoes = {}
        app._ratt_sessoes = sessoes
    chave = (fonte.porta, fonte.banco, esquema)
    if chave not in sessoes:
        sessoes[chave] = SessaoRascunho(fonte.porta, fonte.banco, esquema, app)
    return sessoes[chave]
