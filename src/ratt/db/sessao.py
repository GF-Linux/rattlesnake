"""A sessão de rascunho: o rascunho de um banco+schema (ou do servidor), dividido por todas as telas que mexem nele.

Uma por `(porta, banco, esquema)`, guardada na aplicação (`obter`): o canvas e a tela Dados usam o MESMO objeto
em memória, de modo que uma tela coberta nunca grava uma versão velha. A sessão é a única que escreve o rascunho
no disco (`persistir`); quem muda o rascunho por ela avisa os ouvintes (as telas redesenham painel e título).

Toda mudança no rascunho avisa os ouvintes, inclusive as que vêm de `abrir`, `rebase` e `reler` (chamados pela
tela que acabou de ler o banco): essa tela se redesenha sozinha em seguida e ignora o próprio eco.
"""
from __future__ import annotations

import json
import re
import secrets
import subprocess
from dataclasses import fields
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

import psycopg

from ratt.db import catalogo_servidor, checagem_servidor, guardar, rascunho as _rascunho, rascunho_servidor as _rs
from ratt.db.idioma import t
from ratt.db.modelo import Modelo
from ratt.db.servidor import ModeloServidor, Papel


class Aviso(str):
    """Um texto a notificar, com a severidade e o tempo com que a tela o mostra (é um `str` como outro qualquer)."""

    def __new__(cls, texto: str, severidade: str = "information", tempo: Optional[float] = None):
        aviso = super().__new__(cls, texto)
        aviso.severidade, aviso.tempo = severidade, tempo
        return aviso

    def opcoes(self) -> dict:
        """Os argumentos de `notify` além do texto."""
        return {"severity": self.severidade} if self.tempo is None else {"severity": self.severidade, "timeout": self.tempo}


def executar_rascunho(r: _rascunho.Rascunho, conexao, arquivo: Path, ensaio: bool, cabecalho: str,
                      mensagens: Optional[List[str]] = None) -> Tuple[int, Optional[int]]:
    """Grava o SQL do rascunho em `arquivo` e o roda com o mesmo executor do `ratt db run`.
    Devolve (código, índice da operação que falhou ou None). O cabeçalho entra no texto e na conta das linhas.
    Cada operação de dados com `vista` confere as linhas que pegou com as que a pessoa viu: no ensaio mostra
    "pegou X (você viu Y)"; ao aplicar, se o banco mudou (e nenhuma operação anterior mexe na mesma tabela, que
    explicaria a diferença) desfaz tudo e o texto vai para `mensagens`."""
    from ratt.db import rodar
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(r.sql_texto(cabecalho), encoding="utf-8")
    falhou: List[int] = []

    def ao_falhar(comando) -> None:
        indice = r.operacao_da_linha(comando.linha, cabecalho)
        if indice >= 0:
            falhou.append(indice)

    def conferir(comando, linhas: int, ensaio_: bool) -> Optional[str]:
        i = r.operacao_da_linha(comando.linha, cabecalho)
        op = r.operacoes[i] if 0 <= i < len(r.operacoes) else None
        vista = getattr(op, "vista", None)
        if vista is None:
            return None
        if ensaio_:
            print(f"   {t('dados.pegou_viu', x=linhas, y=vista)}")
            return None
        if linhas == vista:
            return None
        tabela = getattr(op, "tabela", None)
        if any(tabela == getattr(o, "tabela", None) or tabela in o.refere() for o in r.operacoes[:i]):
            print(f"   {t('dados.pegou_viu', x=linhas, y=vista)}")     # a diferença é do próprio rascunho
            return None
        texto = t("dados.banco_mudou", x=linhas, y=vista)
        if mensagens is not None:
            mensagens.append(texto)
        return texto

    codigo = rodar.executar(arquivo, conexao, ensaio=ensaio, ao_falhar=ao_falhar, conferir=conferir)
    return codigo, (falhou[0] if falhou else None)


def _dono(dados: dict, banco: str, esquema: str, base: Modelo, outra_pasta: bool = False) -> str:
    """De quem é um rascunho gravado com o nome de antes: "meu", "outro" ou "duvida".
    Com identidade gravada (banco, esquema), ela decide. Sem ela (rascunho antigo), decide o conteúdo: os ids das
    tabelas da base são os oids, únicos no banco — se toda operação vale sobre a base DESTE schema e alguma se apoia
    nela (refere uma tabela dela) ou cria tabela neste schema, é dele; se alguma refere o que não existe aqui e
    nenhuma se apoia nesta base, é de outro schema; se umas se apoiam e outras não valem mais (uma tabela apagada
    desde então), dúvida; senão (vazio, ou ilegível como rascunho), dúvida.
    `outra_pasta`: o arquivo está na pasta de OUTRO banco (a pasta antiga de um banco de nome trocado é a pasta de
    sempre de um banco de nome seguro, como "meu_banco" para "meu banco"). Os oids só são únicos dentro de um banco
    — um banco clonado de um template tem os mesmos — e todo banco tem um `public`: o conteúdo não prova nada, e o
    arquivo nunca é "meu" (só a identidade pode dizer que é de outro)."""
    if "esquema" in dados or "banco" in dados:
        if dados.get("esquema") == esquema and dados.get("banco", banco) == banco:
            return "duvida" if outra_pasta else "meu"
        return "outro"
    if outra_pasta:
        return "duvida"
    try:
        r = _rascunho.Rascunho.de_dict(dados, base)
    except _rascunho.RascunhoCorrompido:
        return "duvida"
    if not r.operacoes:
        return "duvida"
    apoio_na_base = any(o.refere() & set(base.tabelas) for o in r.operacoes)
    if r.invalidas:
        return "duvida" if apoio_na_base else "outro"
    apoio = apoio_na_base or any(
        isinstance(o, _rascunho.CriarTabela) and o.tabela.esquema == esquema for o in r.operacoes)
    criadas_aqui = all(o.tabela.esquema == esquema for o in r.operacoes if isinstance(o, _rascunho.CriarTabela))
    return "meu" if apoio and criadas_aqui else "duvida"


class _SessaoComDisco:
    """O que toda sessão de rascunho divide: os ouvintes, o disco (ler, pôr de lado o ilegível, nunca gravar por
    cima do que não se leu, gravar só o que mudou) e as mudanças no rascunho. A subclasse diz onde o rascunho mora
    e de que tipo ele é (`_ler_disco`, `_gravar_disco`, `_separar_disco`, `_vazio`, `_de_dict`)."""

    _CHAVE_SEM_GRAVAR = "canvas.sem_gravar"

    def __init__(self, porta: int, app=None):
        self.porta = porta
        self.falha: Optional[int] = None    # operação que falhou ao aplicar; some na próxima mudança do rascunho
        self.mensagem_falha: Optional[str] = None   # o porquê; some junto com `falha`
        self.aplicando = False              # entre o commit e a releitura: ninguém mexe no rascunho
        self.sem_gravar = False             # não deu para LER o rascunho: gravar seria por cima do que não se viu
        self._gravado: Optional[dict] = None    # o rascunho como está no disco, até onde a sessão sabe
        self._aberta = False                # já leu o disco uma vez?
        self._ouvintes: List[Callable[[], None]] = []
        self._ao_aplicar: List[Callable] = []
        self._app = app

    # ── o que a subclasse diz ───────────────────────────────────────────────

    def _ler_disco(self) -> Optional[dict]:
        raise NotImplementedError

    def _gravar_disco(self, dados: Optional[dict]) -> None:
        raise NotImplementedError

    def _separar_disco(self) -> Path:
        raise NotImplementedError

    def _vazio(self, base):
        raise NotImplementedError

    def _de_dict(self, dados: dict, base):
        raise NotImplementedError

    def _migrar(self, base, avisos: List[str]) -> Optional[dict]:
        """Sem arquivo: um rascunho gravado noutro lugar que deva vir para cá (None: nenhum)."""
        return None

    def _para_dict(self) -> Optional[dict]:
        """O que vai ao disco (None: apaga o arquivo)."""
        return self.rascunho.para_dict() if self.rascunho.operacoes else None

    def _depois_de_ler(self, dados: Optional[dict]) -> None:
        """O rascunho acabou de vir do disco (`dados`, ou None sem arquivo)."""

    def _limpar_falha(self) -> None:
        self.falha = self.mensagem_falha = None

    def _preparar(self, base) -> bool:
        """Antes do rebase: acertar o rascunho com o que só o catálogo diz. True se alguma operação mudou."""
        return False

    # ── ouvintes e avisos ───────────────────────────────────────────────────

    def ouvir(self, fn: Callable[[], None]) -> None:
        if fn not in self._ouvintes:
            self._ouvintes.append(fn)

    def parar(self, fn: Callable[[], None]) -> None:
        if fn in self._ouvintes:
            self._ouvintes.remove(fn)

    def ao_aplicar(self, fn: Callable) -> None:
        """`fn(catálogo novo, {id provisório: nome})` roda quando o catálogo de depois de um aplicar chega — venha o
        aplicar de que tela vier (o canvas leva as posições das tabelas criadas para os ids do banco)."""
        if fn not in self._ao_aplicar:
            self._ao_aplicar.append(fn)

    def parar_ao_aplicar(self, fn: Callable) -> None:
        if fn in self._ao_aplicar:
            self._ao_aplicar.remove(fn)

    def _avisar_ouvintes(self) -> None:
        for fn in list(self._ouvintes):
            fn()

    def _avisar(self, aviso: Aviso) -> None:
        """Erros de disco e o "aplicado" vão direto à aplicação (é o mesmo que a tela notificar)."""
        notify = getattr(self._app, "notify", None)
        if notify is not None:
            notify(str(aviso), **aviso.opcoes())

    # ── o disco ─────────────────────────────────────────────────────────────

    def abrir(self, base) -> List[str]:
        """Chegou o modelo a uma tela que abre este rascunho. Na primeira vez (ou se a última leitura do
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

    def reler(self, base) -> Optional[List[str]]:
        """Se o rascunho no disco não é mais o que a sessão gravou ou leu (outro processo mexeu nele), vale o do
        disco. None quando nada mudou (ou não deu para ler agora: fica o da memória, nada se grava por cima). Sem
        conseguir gravar (`sem_gravar`), o que entrou na memória só existe ali: não é trocado pelo do disco."""
        if self.sem_gravar and self.rascunho.operacoes:
            return None
        try:
            dados = self._ler_disco()
        except _rascunho.RascunhoCorrompido:
            dados = {}                          # diferente de qualquer coisa: `_ler` o põe de lado
        except OSError:
            return None
        if dados == self._gravado:
            return None
        avisos = self._ler(base)
        self._avisar_ouvintes()
        return avisos

    def _ler(self, base) -> List[str]:
        avisos: List[str] = []
        self._gravado = None
        try:
            try:
                dados = self._ler_disco()
            except OSError as erro:
                # o arquivo pode ter operações que não se leram: nesta sessão o rascunho não vai ao disco
                if not self.sem_gravar:
                    avisos.append(Aviso(t(self._CHAVE_SEM_GRAVAR, erro=erro), "error", 15))
                self.sem_gravar = True
                self.rascunho = self._vazio(base)
                return avisos
            if dados is None:
                dados = self._migrar(base, avisos)
            if dados is None:
                self.rascunho = self._vazio(base)
                self._depois_de_ler(None)
                return avisos
            r = self._de_dict(dados, base)
            self._gravado = dados
        except _rascunho.RascunhoCorrompido:
            try:
                caminho = self._separar_disco()
                avisos.append(Aviso(t("canvas.corrompido", caminho=caminho), "warning", 15))
            except OSError as erro:
                avisos.append(Aviso(t("canvas.erro_gravar", erro=erro), "error"))
            self.rascunho = self._vazio(base)
            self._depois_de_ler(None)
            return avisos
        if r.invalidas:
            avisos.append(Aviso(t("canvas.invalidas", n=len(r.invalidas)), "warning"))
        self.rascunho = r
        self._depois_de_ler(dados)
        return avisos

    def persistir(self, so_se_mudou: bool = False) -> None:
        """Grava o rascunho; vazio apaga o arquivo. `so_se_mudou`: nada a fazer se o disco já tem exatamente isso
        (o rebase do vigia não reescreve o arquivo a cada volta)."""
        if self.sem_gravar:
            return
        dados = self._para_dict()
        if dados is not None:
            dados = json.loads(json.dumps(dados))   # tuplas viram listas: igual ao que se lê do disco
        if so_se_mudou and dados == self._gravado:
            return
        try:
            self._gravar_disco(dados)
        except OSError as erro:
            self._avisar(Aviso(t("canvas.erro_gravar", erro=erro), "error"))
            return
        self._gravado = dados

    # ── mudar o rascunho ────────────────────────────────────────────────────

    def rebase(self, base) -> Set[int]:
        """O banco mudou: nada se perde, o que deixou de valer fica marcado. Devolve as operações que passaram a
        não valer agora; grava (e avisa os ouvintes) só se o rascunho mudou de fato."""
        antes = self.rascunho.invalidas
        acertou = self._preparar(base)
        depois = self.rascunho.rebase(base)
        if depois != antes or acertou:
            self.persistir(so_se_mudou=True)
            self._avisar_ouvintes()
        return depois - antes

    def mudou(self) -> None:
        """Uma edição entrou ou saiu do rascunho (por estes métodos ou direto em `rascunho`): grava e avisa."""
        self._limpar_falha()
        self.persistir()
        self._avisar_ouvintes()

    def acrescentar(self, op) -> None:
        self.rascunho.acrescentar(op)
        self.mudou()

    def desfazer(self):
        op = self.rascunho.desfazer()
        if op is not None:
            self.mudou()
        return op

    def tirar(self, indices) -> None:
        self.rascunho.tirar(indices)
        self.mudou()


class SessaoRascunho(_SessaoComDisco):
    def __init__(self, porta: int, banco: str, esquema: str, app=None):
        super().__init__(porta, app)
        self.banco, self.esquema = banco, esquema
        self.rascunho = _rascunho.Rascunho(Modelo(esquema))
        self.contagens: Dict[str, int] = {}     # linhas de cada tabela apagada, contadas ao pôr no rascunho
        self._avisados: Set[Path] = set()   # rascunhos de antes sem dono certo, já avisados nesta sessão
        self._criadas: Dict[str, str] = {}  # id provisório → nome das tabelas criadas pelo rascunho aplicado

    # ── o disco ─────────────────────────────────────────────────────────────

    def _ler_disco(self) -> Optional[dict]:
        return guardar.ler_rascunho(self.porta, self.banco, self.esquema)

    def _gravar_disco(self, dados: Optional[dict]) -> None:
        guardar.gravar_rascunho(self.porta, self.banco, self.esquema, dados)

    def _separar_disco(self) -> Path:
        return guardar.separar_rascunho(self.porta, self.banco, self.esquema)

    def _vazio(self, base: Modelo) -> _rascunho.Rascunho:
        return _rascunho.Rascunho(base)

    def _de_dict(self, dados: dict, base: Modelo) -> _rascunho.Rascunho:
        return _rascunho.Rascunho.de_dict(dados, base)

    def _migrar(self, base: Modelo, avisos: List[str]) -> Optional[dict]:
        return self._migrar_de_antes(base, avisos)

    def _migrar_de_antes(self, base: Modelo, avisos: List[str]) -> Optional[dict]:
        """Sem arquivo no nome de agora: um rascunho gravado com o nome de antes (o `_seguro` não injetivo da 0.9.0)
        que seja COMPROVADAMENTE deste schema é levado para o nome novo (o antigo fica como `….migrado`). O que é de
        outro schema fica onde está, calado; o que não dá para atribuir sem dúvida fica onde está, com um aviso — e
        assim fica todo arquivo achado na pasta de outro banco (pode ser o rascunho vivo dele). Um rascunho nunca se
        perde."""
        for antigo in guardar.rascunhos_de_antes(self.porta, self.banco, self.esquema):
            try:
                dados = guardar.ler_rascunho_em(antigo)
            except (OSError, _rascunho.RascunhoCorrompido):
                dados = None
            outra_pasta = antigo.parent != guardar.pasta(self.porta, self.banco)
            dono = _dono(dados, self.banco, self.esquema, base, outra_pasta) if dados is not None else "duvida"
            if dono == "outro":
                continue
            if dono == "duvida":
                if antigo not in self._avisados:
                    self._avisados.add(antigo)
                    chave = "canvas.rascunho_outro_banco" if outra_pasta else "canvas.rascunho_antigo"
                    avisos.append(Aviso(t(chave, caminho=antigo), "warning", 15))
                continue
            limpo = {k: v for k, v in dados.items() if k not in ("banco", "esquema")}
            try:
                guardado = guardar.migrar_rascunho(antigo, self.porta, self.banco, self.esquema, limpo)
            except OSError as erro:
                avisos.append(Aviso(t("canvas.erro_gravar", erro=erro), "error"))
                return limpo                # vale nesta sessão; o arquivo antigo continua intacto
            avisos.append(Aviso(t("canvas.rascunho_migrado", caminho=guardado)))
            return limpo
        return None

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
        mensagens: List[str] = []
        codigo, indice = executar_rascunho(self.rascunho, conexao, arquivo, ensaio, cabecalho, mensagens)
        self.mensagem_falha = mensagens[0] if codigo != 0 and mensagens else None
        return codigo, indice, self.mensagem_falha

    def concluir(self, codigo: int, indice: Optional[int], ensaio: bool, texto: str) -> None:
        """O que o aplicar decidiu. Falhou: marca a operação. Ensaio que deu certo: tira a marca. Aplicado: o banco
        já gravou — arquiva o SQL e esvazia o rascunho AGORA, sem esperar a releitura (se a pessoa sair ou trocar
        de schema nesse intervalo, nada aplicado fica no disco como se estivesse pendente); `aplicando` fica ligado
        até a tela trazer o catálogo novo."""
        if codigo != 0:
            self.falha = indice if indice is not None and indice >= 0 else None
            if self.falha is None:
                self.mensagem_falha = None
            self._avisar_ouvintes()
            return
        if ensaio:
            self.falha = self.mensagem_falha = None
            self._avisar_ouvintes()
            return
        try:
            caminho = guardar.guardar_aplicado(self.porta, self.banco, texto)
            self._avisar(Aviso(t("canvas.aplicado", caminho=caminho)))
        except OSError as e:
            self._avisar(Aviso(t("canvas.erro_gravar", erro=e), "error"))
        self._criadas = {i: tab.nome for i, tab in self.rascunho.vista().tabelas.items()
                         if i.startswith("novo-t") and tab.estado != "apagado"}
        self.rascunho = _rascunho.Rascunho(self.rascunho.modelo())
        self.falha, self.mensagem_falha, self.contagens = None, None, {}
        self.persistir()
        self.aplicando = True
        self._avisar_ouvintes()

    def aplicado(self, base: Modelo) -> None:
        """Chegou o catálogo depois do aplicar: ele é a nova base. Quem pediu `ao_aplicar` fica sabendo (com as
        tabelas que o rascunho criou) antes dos ouvintes."""
        self.aplicando = False
        self.rascunho = _rascunho.Rascunho(base)
        criadas, self._criadas = self._criadas, {}
        for fn in list(self._ao_aplicar):
            fn(base, criadas)
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


# ── a sessão do servidor ────────────────────────────────────────────────────
#
# O rascunho do servidor (papéis, grupos, quem entra em que banco) com as mesmas garantias do disco do rascunho do
# schema. O aplicar é outro: os comandos vão por grupos de banco (`RascunhoServidor.lotes`), cada grupo numa conexão
# ao seu banco e numa transação, e para no primeiro erro — o que já foi gravado sai do rascunho, o resto fica, com a
# operação marcada "parcial" (no disco também). A senha é pedida só no aplicar de verdade e vira hash SCRAM aqui;
# nem a senha nem o hash vão ao disco, ao texto mostrado, a uma notificação ou a um erro (`_sem_segredos`).

Executor = Callable[[List[str], str], Tuple[int, str]]   # (argv, entrada padrão) -> (código de saída, stderr)

_HASH = re.compile(r"SCRAM-SHA-256\$[^\s'\"]+")


def _sem_segredos(texto: str, segredos: List[str]) -> str:
    """O texto sem nenhum hash (os que o aplicar gerou e qualquer coisa com cara de hash SCRAM)."""
    for s in segredos:
        if s:
            texto = texto.replace(s, "<hash>")
    return _HASH.sub("<hash>", texto)


def _erro_pg(erro) -> str:
    """O que o PostgreSQL disse, sem o trecho do comando (o `LINE 1: …` do psycopg citaria o SQL, e com ele o hash)."""
    d = getattr(erro, "diag", None)
    primeira = getattr(d, "message_primary", None)
    if not primeira:
        linhas = str(erro).strip().splitlines()
        primeira = linhas[0] if linhas else type(erro).__name__
    detalhe = getattr(d, "message_detail", None)
    return f"{primeira} — {detalhe}" if detalhe else primeira


def _erro_psql(saida: str) -> Tuple[Optional[int], str]:
    """(linha da entrada em que o psql parou, o erro) a partir do que ele escreveu; sem as linhas `LINE n:` e `^`,
    que repetiriam o comando."""
    linha: Optional[int] = None
    partes: List[str] = []
    for bruta in saida.splitlines():
        m = re.match(r"psql:<stdin>:(\d+):\s*(.*)", bruta)
        if m:
            if linha is None:
                linha = int(m.group(1))
            partes.append(m.group(2).strip())
            continue
        s = bruta.strip()
        if not s or s.startswith("^") or re.match(r"(LINE|LINHA)\s+\d+:", s):
            continue
        partes.append(s)
    return linha, " — ".join(p for p in partes if p) or "psql"


def _comando_da_linha(cmds: List[Tuple[int, str, str]], linha: Optional[int], primeira: int) -> int:
    """O comando do lote em que fica a `linha` da entrada (os comandos começam na linha `primeira`)."""
    if linha is None:
        return 0
    inicio = primeira
    for j, (_, sql, _) in enumerate(cmds):
        fim = inicio + sql.count("\n")
        if linha <= fim:
            return j
        inicio = fim + 1
    return len(cmds) - 1


def comando_psql(banco: str, ensaio: bool, porta: Optional[int] = None, pasta: Optional[str] = None) -> List[str]:
    """`sudo -u postgres psql -X -q [-h pasta] [-p porta] -d <banco> -v ON_ERROR_STOP=1 -1` (como root, `runuser`).
    A porta (e a pasta do socket, se achada) é a da tela: sem ela o psql iria ao servidor padrão. O SQL vai pela
    entrada padrão, nunca aqui: o que está nos argumentos qualquer um vê no `ps`. Um nome de banco com `=` ou `://`
    iria como texto de conexão: vai como `dbname='…'`. O ensaio não leva `-1` (a entrada traz BEGIN … ROLLBACK)."""
    from ratt.db import configurar
    alvo = banco
    if "=" in banco or "://" in banco:
        alvo = "dbname='" + banco.replace("\\", "\\\\").replace("'", "\\'") + "'"
    cmd = ["psql", "-X", "-q"] + (["-h", pasta] if pasta else []) + (["-p", str(int(porta))] if porta else [])
    cmd += ["-d", alvo, "-v", "ON_ERROR_STOP=1"] + ([] if ensaio else ["-1"])
    return configurar._postgres(cmd)


def rodar_sudo(argv: List[str], entrada: str) -> Tuple[int, str]:
    """O executor de verdade do caminho do `sudo`: o SQL pela entrada padrão (sem arquivo temporário)."""
    p = subprocess.run(argv, input=entrada, text=True, capture_output=True, cwd="/")
    return p.returncode, (p.stderr or "") + (p.stdout or "" if p.returncode else "")


def _atualizar_bancos(r: _rs.RascunhoServidor, base: ModeloServidor) -> bool:
    """Cada remoção roda nos bancos que aceitam conexão AGORA (o catálogo, não o que valia quando ela entrou no
    rascunho). True se alguma mudou."""
    nomes = tuple(b.nome for b in base.bancos.values() if b.aceita_conexao)
    mudou = False
    for op in r.operacoes:
        if isinstance(op, _rs.RemoverPapeis) and op.bancos != nomes:
            op.bancos = nomes
            mudou = True
    return mudou


def _trocar_ids(op: _rs.OperacaoServidor, troca: Dict[str, Tuple[str, str]]) -> None:
    """Um papel criado por uma parte já gravada do rascunho deixou de ser `novo-p{n}`: a operação que ficou passa a
    referi-lo pelo id do catálogo. `troca`: {id velho: (id novo, nome)}."""
    ids = {velho: novo for velho, (novo, _) in troca.items()}

    def trocar(v):
        if isinstance(v, str):
            return ids.get(v, v)
        if isinstance(v, tuple):
            return tuple(ids.get(x, x) if isinstance(x, str) else x for x in v)
        return v

    for f in fields(op):
        v = getattr(op, f.name)
        if f.name in ("nomes", "bancos"):       # `bancos` são nomes de banco, não ids
            continue
        if isinstance(v, Papel):
            v.membro_de, v.nao_herda, v.sem_set = trocar(v.membro_de), trocar(v.nao_herda), trocar(v.sem_set)
            v.concedido_por = {ids.get(g, g): {ids.get(d, d): x for d, x in por.items()}
                               for g, por in v.concedido_por.items()}
        else:
            setattr(op, f.name, trocar(v))
    op.nomes = dict(op.nomes)
    for _, (novo, nome) in troca.items():
        if novo in op.refere():
            op.nomes[novo] = nome


class SessaoServidor(_SessaoComDisco):
    """O rascunho do servidor de uma porta, dividido pelas telas (Usuários e, pelo `P`, Permissões não: essa usa o do
    schema). Mesmo contrato que as telas usam da `SessaoRascunho`; o aplicar é `aplicar(conectar, ensaio,
    pedir_senha, como_postgres)`.

    `conectar(nome)` devolve uma conexão ao banco `nome` (None: o banco atual, o da tela), ou None se não entra;
    as conexões são de quem chamou (nada aqui as fecha). Uma remoção roda sempre nos bancos que aceitam conexão
    agora: a lista é refeita pelo catálogo a cada rebase e de novo antes do pré-voo."""

    _CHAVE_SEM_GRAVAR = "usu.sem_gravar"

    def __init__(self, porta: int, app=None):
        super().__init__(porta, app)
        self.rascunho = _rs.RascunhoServidor(ModeloServidor())
        self.aviso: Optional[str] = None    # aplicado de verdade, mas algo não ficou como se pediu (um REVOKE)
        self.precisa_sudo = False           # o último aplicar parou por falta de poder: o caminho do sudo resolve
        self._parcial: Optional[Tuple[_rs.OperacaoServidor, str]] = None   # a operação que ficou pela metade
        self._arquivado: Optional[Tuple[str, object]] = None   # (chave do aviso, caminho ou OSError) para `concluir`

    # ── o disco ─────────────────────────────────────────────────────────────

    def _ler_disco(self) -> Optional[dict]:
        return guardar.ler_rascunho_servidor(self.porta)

    def _gravar_disco(self, dados: Optional[dict]) -> None:
        guardar.gravar_rascunho_servidor(self.porta, dados)

    def _separar_disco(self) -> Path:
        return guardar.separar_rascunho_servidor(self.porta)

    def _vazio(self, base: ModeloServidor) -> _rs.RascunhoServidor:
        return _rs.RascunhoServidor(base)

    def _de_dict(self, dados: dict, base: ModeloServidor) -> _rs.RascunhoServidor:
        r = _rs.RascunhoServidor.de_dict(dados, base)
        if _atualizar_bancos(r, base):
            r.rebase(base)
        return r

    def _preparar(self, base: ModeloServidor) -> bool:
        return _atualizar_bancos(self.rascunho, base)

    def _indice(self, op) -> Optional[int]:
        return next((i for i, o in enumerate(self.rascunho.operacoes) if o is op), None)

    def _para_dict(self) -> Optional[dict]:
        dados = super()._para_dict()
        if dados is not None and self._parcial is not None:
            i = self._indice(self._parcial[0])
            if i is not None:
                dados["parcial"] = {"op": i, "mensagem": self._parcial[1]}
        return dados

    def _depois_de_ler(self, dados: Optional[dict]) -> None:
        self._parcial = None
        p = (dados or {}).get("parcial")
        ops = self.rascunho.operacoes
        if (isinstance(p, dict) and isinstance(p.get("op"), int) and not isinstance(p.get("op"), bool)
                and isinstance(p.get("mensagem"), str) and 0 <= p["op"] < len(ops)):
            self._parcial = (ops[p["op"]], p["mensagem"])
        self._limpar_falha()

    def _limpar_falha(self) -> None:
        """A marca de uma falha some na próxima mudança; a de "parcial" fica enquanto a operação estiver no rascunho
        (ela foi feita só em parte no servidor)."""
        i = self._indice(self._parcial[0]) if self._parcial is not None else None
        if i is None:
            self._parcial = None
            self.falha = self.mensagem_falha = None
        else:
            self.falha, self.mensagem_falha = i, self._parcial[1]

    # ── aplicar ─────────────────────────────────────────────────────────────

    def cabecalho(self) -> str:
        return t("usu.cabecalho_sql", porta=self.porta, n=len(self.rascunho.operacoes))

    def _recusar(self, texto: str) -> Tuple[int, Optional[int], Optional[str]]:
        """Recusado antes de mudar qualquer coisa no servidor."""
        from ratt.cli import c
        print(c(f"✗ {texto}", "laranja", "negrito"))
        self.falha, self.mensagem_falha = None, texto
        return 1, None, texto

    def aplicar(self, conectar: Callable[[Optional[str]], object], ensaio: bool, pedir_senha: Callable[[str], str],
                como_postgres: bool = False, runner: Optional[Executor] = None, confirmado: Optional[str] = None,
                cabecalho: Optional[str] = None) -> Tuple[int, Optional[int], Optional[str]]:
        """Roda o rascunho (ou só ensaia). Devolve (código, índice da operação marcada, mensagem).

        1. Relê o servidor, refaz os bancos de cada remoção e passa pelo pré-voo: qualquer problema recusa sem mudar
           nada (como recusa o SQL diferente de `confirmado`, o texto que a pessoa viu, e uma remoção sem ser
           superusuário — decisão 1 da spec: aí `precisa_sudo`).
        2. Senhas: `pedir_senha(nome)` uma vez por papel, só no aplicar de verdade (o ensaio usa um hash
           descartável); a senha vira hash SCRAM aqui e só o hash vai ao servidor. Ctrl+C ou senha vazia cancela.
        3. Cada lote numa transação (ensaio: desfeita), na conexão ao seu banco — ou, `como_postgres`, pelo
           `sudo -u postgres psql` com o SQL na entrada padrão (`runner(argv, entrada)`; o comando aparece antes).
        4. Para no primeiro erro. Nada gravado: a operação fica marcada. Algo gravado: o que foi feito sai do
           rascunho (e vai para `aplicados/`), o resto fica, a operação marcada "parcial" — também no disco.
           Tudo gravado: arquiva o SQL mostrado (sem senha) e esvazia; um REVOKE que o servidor não fez (o acesso
           foi dado por outro) volta como mensagem (`aviso`), com código 0."""
        from ratt.cli import c
        self.precisa_sudo, self.aviso, self._arquivado = False, None, None
        if cabecalho is None:
            cabecalho = self.cabecalho()
        atual = conectar(None)
        if atual is None:
            return self._recusar(t("canvas.sem_conexao"))
        try:
            agora = catalogo_servidor.modelo_servidor(atual)
        except psycopg.Error as e:
            return self._recusar(t("chk.pv_falhou", erro=_erro_pg(e)))
        self._preparar(agora)
        msgs = checagem_servidor.prevoo(self.rascunho, atual, conectar)     # sobre a base de antes: o que mudou
        self.rascunho.rebase(agora)
        self.persistir(so_se_mudou=True)
        if not msgs:                    # o que já não valia antes também não vai (o servidor não roda meio rascunho)
            msgs = [t("chk.pv_invalida", n=i + 1, descricao=op.tipo, motivo=motivo)
                    for i, (op, motivo) in enumerate(zip(self.rascunho.operacoes, self.rascunho.motivos())) if motivo]
        if msgs:
            return self._recusar("\n".join(msgs))
        texto = self.rascunho.sql_texto(cabecalho)
        if confirmado is not None and texto != confirmado:
            return self._recusar(t("canvas.sql_mudou"))
        eu = agora.papeis.get(agora.eu)
        if (not como_postgres and not (eu is not None and eu.superusuario)
                and any(isinstance(op, _rs.RemoverPapeis) for op in self.rascunho.operacoes)):
            self.precisa_sudo = True
            return self._recusar(t("usu.remover_so_super"))
        segredos: List[str] = []
        hashes: Dict[str, str] = {}
        try:
            for papel, nome in self.rascunho.senhas_pedidas():
                senha = secrets.token_urlsafe(24) if ensaio else pedir_senha(nome)
                if not senha:
                    return self._recusar(t("usu.senha_cancelada"))
                hashes[papel] = atual.pgconn.encrypt_password(senha.encode(), nome.encode(),
                                                              b"scram-sha-256").decode()
                segredos.append(hashes[papel])
                senha = None
        except KeyboardInterrupt:
            return self._recusar(t("usu.senha_cancelada"))
        except psycopg.Error as e:
            return self._recusar(_sem_segredos(_erro_pg(e), segredos))
        lotes = self.rascunho.lotes(hashes)
        nome_atual = agora.bancos[agora.banco_atual].nome if agora.banco_atual in agora.bancos else atual.info.dbname
        rotulos = [destino or t("usu.lote_servidor") for destino, _ in lotes]
        if ensaio:
            print(c(t("usu.ensaio_por_banco"), "cinza"))
        feitos: List[int] = []
        falhou: Optional[Tuple[int, int, str, object]] = None   # (lote, operação, erro, exceção)
        for k, (destino, cmds) in enumerate(lotes):
            print(c(t("usu.no_lote", lote=rotulos[k], n=k + 1, total=len(lotes)), "azul", "negrito"))
            try:
                if como_postgres:
                    erro = self._lote_sudo(destino or nome_atual, cmds, ensaio, runner or rodar_sudo, segredos)
                else:
                    erro = self._lote_conexao(conectar(destino) if destino else atual, destino, cmds, ensaio,
                                              segredos)
            except KeyboardInterrupt:
                erro = (0, t("usu.interrompido"), None)
            if erro is None:
                print(c(f"   ✓ {t('usu.lote_desfeito' if ensaio else 'usu.lote_gravado')}", "verde"))
                feitos.append(k)
                continue
            j, texto_erro, excecao = erro
            op_i = cmds[j][0]
            op = self.rascunho.operacoes[op_i]
            if (ensaio and isinstance(op, _rs.RemoverPapeis) and op.bancos and cmds[j][1].startswith("DROP ROLE")
                    and (como_postgres or isinstance(excecao, psycopg.errors.DependentObjectsStillExist))):
                print(c(f"   ~ {t('usu.ensaio_drop_role', erro=texto_erro)}", "amarelo"))
                feitos.append(k)
                continue
            if isinstance(op, (_rs.TirarAcesso, _rs.TirarDoGrupo)):
                texto_erro += self._dependentes(op, atual)
            print(c(f"   ✗ {texto_erro}", "laranja", "negrito"))
            if not como_postgres and isinstance(excecao, psycopg.errors.InsufficientPrivilege):
                self.precisa_sudo = True
            falhou = (k, op_i, texto_erro, excecao)
            break
        if falhou is None:
            if ensaio:
                print(c(f"↺ {t('usu.ensaio_fim')}", "amarelo", "negrito"))
                self._limpar_falha()
                return 0, None, None
            return self._gravado_tudo(atual, texto)
        k, op_i, texto_erro, _ = falhou
        partes = []
        if feitos:
            partes.append(t("usu.r.ensaiado" if ensaio else "usu.r.feito", lotes=", ".join(rotulos[x] for x in feitos)))
        partes.append(t("usu.r.falhou", lote=rotulos[k], erro=texto_erro))
        if k + 1 < len(lotes):
            partes.append(t("usu.r.nao_rodado", lotes=", ".join(rotulos[k + 1:])))
        mensagem = _linha_unica("; ".join(partes))
        if ensaio or not feitos:
            self.falha, self.mensagem_falha = op_i, mensagem
            return 1, op_i, mensagem
        return self._gravado_em_parte(atual, lotes, feitos, op_i, mensagem, texto)

    def _lote_conexao(self, conexao, destino: Optional[str], cmds, ensaio: bool, segredos: List[str]):
        """Um lote numa transação; None se deu certo, senão (comando, erro sem segredos, exceção)."""
        from psycopg import pq
        from ratt.cli import c
        if conexao is None:
            return 0, t("usu.sem_entrar_banco", banco=destino), None
        if not conexao.autocommit and conexao.info.transaction_status != pq.TransactionStatus.IDLE:
            conexao.rollback()                  # sobra de uma leitura: nada a perder
        j = 0
        try:
            with conexao.transaction(force_rollback=ensaio):
                for j, (_, sql, mostrado) in enumerate(cmds):
                    print(c(f"   {mostrado};", "cinza"))
                    conexao.execute(sql.encode())       # bytes: um % no SQL não vira parâmetro
        except psycopg.Error as e:
            return j, _sem_segredos(_erro_pg(e), segredos), e
        return None

    def _lote_sudo(self, banco: str, cmds, ensaio: bool, runner: Executor, segredos: List[str]):
        """Um lote pelo `psql` do postgres: o comando aparece (sem o SQL), o SQL vai pela entrada padrão."""
        import shlex
        from ratt.cli import c
        from ratt.db import sistema
        argv = comando_psql(banco, ensaio, self.porta, sistema.pasta_socket(self.porta))
        corpo = "".join(sql + ";\n" for _, sql, _ in cmds)
        entrada = "BEGIN;\n" + corpo + "ROLLBACK;\n" if ensaio else corpo
        print(c(f"   $ {shlex.join(argv)}", "cinza"))
        for _, _, mostrado in cmds:
            print(c(f"   {mostrado};", "cinza"))
        codigo, saida = runner(argv, entrada)
        if codigo == 0:
            return None
        linha, texto = _erro_psql(saida)
        return _comando_da_linha(cmds, linha, 2 if ensaio else 1), _sem_segredos(texto, segredos), None

    def _dependentes(self, op, conexao) -> str:
        """O REVOKE falhou: se há quem recebeu de `op.alvo`/`op.papel` o mesmo privilégio pela opção de conceder
        (o "dependent privileges exist"), diz quem — lido do catálogo agora. "" se não há (ou não deu para ler)."""
        try:
            m = catalogo_servidor.modelo_servidor(conexao)
        except psycopg.Error:
            return ""
        if isinstance(op, _rs.TirarAcesso):
            b = m.bancos.get(op.banco)
            if b is None or op.alvo == _rs.PUBLIC or op.alvo not in m.papeis:
                return ""
            papel = m.papeis[op.alvo].nome
            quem = sorted(_rs.PUBLIC if x == _rs.PUBLIC else m.papeis[x].nome
                          for x, por in b.concedido_por.items()
                          if op.alvo in por and x != op.alvo and (x == _rs.PUBLIC or x in m.papeis))
            chave, extra = "usu.dependentes_acesso", {"banco": b.nome}
        else:
            if op.papel not in m.papeis or op.grupo not in m.papeis:
                return ""
            papel = m.papeis[op.papel].nome
            quem = sorted(q.nome for q in m.papeis.values()
                          if op.grupo in q.membro_de and op.papel in q.concedido_por.get(op.grupo, {}))
            chave, extra = "usu.dependentes_grupo", {"grupo": m.papeis[op.grupo].nome}
        return " — " + t(chave, papel=papel, lista=", ".join(quem), **extra) if quem else ""

    def _conferir_revogacoes(self, depois: ModeloServidor) -> List[str]:
        """Depois de gravar: cada REVOKE (tirar acesso, tirar do grupo) que a vista do rascunho dava como feito e o
        catálogo ainda mostra — num superusuário, o REVOKE age como o dono e não tira o que outro papel concedeu."""
        vista = self.rascunho.vista()
        avisos = []
        for op, valida, _, _ in self.rascunho.passos():
            if not valida:
                continue
            if isinstance(op, _rs.TirarAcesso):
                esperado, agora = vista.bancos.get(op.banco), depois.bancos.get(op.banco)
                if esperado is None or agora is None:
                    continue
                if op.alvo == _rs.PUBLIC:
                    ficou = agora.publico and not esperado.publico
                else:
                    ficou = op.alvo in agora.acesso_direto and op.alvo not in esperado.acesso_direto
                if ficou:
                    por = agora.concedido_por.get(op.alvo, {})
                    nomes = sorted(depois.papeis[x].nome for x in por if x in depois.papeis)
                    if not nomes and agora.dono in depois.papeis:
                        nomes = [depois.papeis[agora.dono].nome]
                    alvo = _rs.PUBLIC if op.alvo == _rs.PUBLIC else depois.papeis[op.alvo].nome
                    avisos.append(t("usu.continua_acesso", papel=alvo, banco=agora.nome, por=", ".join(nomes)))
            elif isinstance(op, _rs.TirarDoGrupo):
                esperado, agora = vista.papeis.get(op.papel), depois.papeis.get(op.papel)
                if esperado is None or agora is None or op.grupo not in depois.papeis:
                    continue
                if op.grupo in agora.membro_de and op.grupo not in esperado.membro_de:
                    por = agora.concedido_por.get(op.grupo, {})
                    nomes = sorted(depois.papeis[x].nome for x in por if x in depois.papeis)
                    avisos.append(t("usu.continua_grupo", papel=agora.nome, grupo=depois.papeis[op.grupo].nome,
                                    por=", ".join(nomes) or "?"))
        return avisos

    def _arquivar(self, chave: str, texto: str) -> None:
        try:
            self._arquivado = (chave, guardar.guardar_aplicado_servidor(self.porta, texto))
        except OSError as e:
            self._arquivado = ("canvas.erro_gravar", e)

    def _gravado_tudo(self, atual, texto: str) -> Tuple[int, Optional[int], Optional[str]]:
        from ratt.cli import c
        self._arquivar("canvas.aplicado", texto)
        try:
            depois = catalogo_servidor.modelo_servidor(atual)
        except psycopg.Error:
            depois = None
        avisos = self._conferir_revogacoes(depois) if depois is not None else []
        for aviso in avisos:
            print(c(f"! {aviso}", "amarelo", "negrito"))
        self.aviso = "\n".join(avisos) or None
        print(c(f"✓ {t('usu.gravado_tudo')}", "verde", "negrito"))
        self.rascunho = _rs.RascunhoServidor(depois if depois is not None else self.rascunho.modelo())
        self._parcial, self.falha, self.mensagem_falha = None, None, None
        self.persistir()
        self.aplicando = True
        return 0, None, self.aviso

    def _gravado_em_parte(self, atual, lotes, feitos: List[int], op_i: int, mensagem: str,
                          texto: str) -> Tuple[int, Optional[int], Optional[str]]:
        """Parte do rascunho já está no servidor: sai do rascunho (o que ficou é recalculado pelo catálogo da
        próxima vez), a operação que parou fica marcada "parcial" e o que se fez vai para `aplicados/`."""
        por_op: Dict[int, Set[int]] = {}
        for k, (_, cmds) in enumerate(lotes):
            for i, _, _ in cmds:
                por_op.setdefault(i, set()).add(k)
        prontos = set(feitos)
        ops = self.rascunho.operacoes
        feitas = {i for i in range(len(ops)) if por_op.get(i, {0}) <= prontos}
        criados = {ops[i].papel.id: ops[i].papel.nome for i in feitas if isinstance(ops[i], _rs.CriarPapel)}
        restantes = [op for i, op in enumerate(ops) if i not in feitas]
        cabeca = "".join("-- " + linha + "\n" for linha in t("usu.arquivo_parcial", mensagem=mensagem).splitlines())
        self._arquivar("usu.aplicado_parcial", cabeca + texto)
        try:
            depois = catalogo_servidor.modelo_servidor(atual)
        except psycopg.Error:
            depois = None
        if depois is not None:
            troca = {velho: (p.id, nome) for velho, nome in criados.items()
                     for p in [depois.papel_por_nome(nome)] if p is not None}
            for op in restantes:
                _trocar_ids(op, troca)
        base = depois if depois is not None else self.rascunho.base
        parou = ops[op_i]
        self.rascunho = _rs.RascunhoServidor(base, restantes, self.rascunho.proximo)
        _atualizar_bancos(self.rascunho, base)
        self.rascunho.rebase(base)
        self._parcial = (parou, mensagem)
        self._limpar_falha()
        self.persistir()
        return 1, self.falha, mensagem

    def concluir(self, codigo: int, indice: Optional[int], ensaio: bool, texto: str) -> None:
        """Fora da suspensão: o aplicar já decidiu e gravou tudo (rascunho, disco, `aplicados/`); aqui só se avisa a
        aplicação (o arquivo guardado) e as telas."""
        arquivado, self._arquivado = self._arquivado, None
        if arquivado is not None:
            chave, valor = arquivado
            if isinstance(valor, OSError):
                self._avisar(Aviso(t(chave, erro=valor), "error"))
            else:
                self._avisar(Aviso(t(chave, caminho=valor)))
        self._avisar_ouvintes()

    def aplicado(self, base: ModeloServidor) -> None:
        """Chegou o catálogo depois do aplicar: ele é a nova base."""
        self.aplicando = False
        self.rascunho = _rs.RascunhoServidor(base)
        for fn in list(self._ao_aplicar):
            fn(base, {})
        self._avisar_ouvintes()


def _linha_unica(texto: str) -> str:
    return " ".join(texto.split("\n"))


def obter_servidor(app, fonte) -> SessaoServidor:
    """A sessão do servidor de `fonte.porta`, guardada em `app._ratt_sessoes_servidor` (criada na primeira vez)."""
    sessoes = getattr(app, "_ratt_sessoes_servidor", None)
    if sessoes is None:
        sessoes = {}
        app._ratt_sessoes_servidor = sessoes
    if fonte.porta not in sessoes:
        sessoes[fonte.porta] = SessaoServidor(fonte.porta, app)
    return sessoes[fonte.porta]
