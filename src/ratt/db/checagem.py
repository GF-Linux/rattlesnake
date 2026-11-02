"""Avisos que ensinam — o canvas "avisa, não decide".

Dois tipos: os estáticos, lidos só do modelo (tabela sem PK, FK sem índice), e os de uma operação,
que contam no banco (NULLs, repetidos, órfãos, dependentes) antes de a operação entrar no rascunho.
Tudo aqui é leitura: cada consulta roda numa transação `READ ONLY` com `statement_timeout` curto (nem uma
função que escreva, chamada num WHERE escrito à mão, consegue mudar o banco). Um aviso "impede" não bloqueia
nada; diz o que o PostgreSQL recusaria e por quê, e a pessoa decide.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import psycopg
from psycopg import sql

from . import catalogo, filtro
from . import rascunho as rs
from .idioma import t
from .modelo import Ligacao, Modelo, opcional


@dataclass
class Aviso:
    alvo: str                   # id da tabela, coluna ou ligação a que o aviso se refere
    nivel: str                  # "info", "atencao" ou "impede"
    chave: str                  # chave de texto em idioma.py (`chk.*`)
    valores: Dict[str, object] = field(default_factory=dict)

    def texto(self) -> str:
        return t(self.chave, **self.valores)


# ── avisos estáticos, do modelo ──

def avisos_do_modelo(m: Modelo) -> List[Aviso]:
    avisos = []
    for tab in m.tabelas.values():
        if tab.estado != "apagado" and not tab.pk():
            avisos.append(Aviso(tab.id, "atencao", "chk.sem_pk", {"tabela": tab.nome}))
    for lig in m.ligacoes.values():
        if lig.estado == "apagado" or lig.origem not in m.tabelas or lig.destino not in m.tabelas:
            continue
        origem = m.tabelas[lig.origem]
        primeira = origem.coluna(lig.colunas[0]) if lig.colunas else None
        if primeira is None:
            continue
        valores = {"tabela": origem.nome, "coluna": primeira.nome, "ligacao": lig.nome}
        if not primeira.indexada:
            avisos.append(Aviso(lig.id, "atencao", "chk.fk_sem_indice", valores))
        if opcional(m, lig):
            avisos.append(Aviso(lig.id, "info", "chk.fk_opcional", valores))
    return avisos


@dataclass
class Dependente:
    """Algo no banco que depende de uma tabela ou coluna. O texto sai de `idioma`, não do SQL."""
    tipo: str                               # "fk" (chave estrangeira de outra tabela) ou "view"
    oid: int                                # da tabela ou view dependente
    nome: str
    oid_restricao: Optional[int] = None     # só para "fk"
    nome_restricao: Optional[str] = None

    def texto(self) -> str:
        if self.tipo == "fk":
            return t("chk.dep_fk", nome=self.nome, restricao=self.nome_restricao)
        return t("chk.dep_view", nome=self.nome)


# ── contagens no banco, só leitura ──

# Nome de tipo como o `format_type` do catálogo escreve: nomes simples ou entre aspas (com `""` dobrado), um
# schema opcional, `(n)`/`(n,m)`, as palavras fixas dos tipos de hora e de intervalo e `[]`. Só isso pode ir
# colado num CAST: qualquer outra coisa (`date); DROP …`) é recusada antes de chegar ao banco.
_NOME = r'(?:[a-z_][a-z0-9_$]*|"(?:[^"]|"")+")'
_PALAVRAS_DE_TIPO = r"(?: (?:with|without|time|zone|varying|precision|year|month|day|hour|minute|second|to))*"
_TIPO_SEGURO = re.compile(rf"(?:{_NOME}\.)?{_NOME}{_PALAVRAS_DE_TIPO}(?:\(\d+(?:,\s*\d+)?\))?"
                          rf"{_PALAVRAS_DE_TIPO}(?:\(\d+\))?(?:\[\])*")


def tipo_seguro(tipo: str) -> bool:
    return isinstance(tipo, str) and bool(_TIPO_SEGURO.fullmatch(tipo))


class Consulta:
    """Contagens pontuais. Cada uma roda numa transação somente leitura com `statement_timeout` e devolve None
    se o PostgreSQL cancelar por demora (tabela grande demais para contar de graça). As que recebem um WHERE
    (`onde`: a saída de `filtro.where`, ou o texto que a pessoa escreveu) também devolvem None se ele falhar
    no banco; o motivo fica em `erro()`."""

    def __init__(self, conexao, limite_ms: int = 2000):
        self.conexao = conexao
        self.limite_ms = int(limite_ms)
        self.forcar_cast = False            # valida valores pelo CAST mesmo no PostgreSQL 16+ (o caminho antigo)
        self._erro: Optional[str] = None

    def erro(self) -> Optional[str]:
        """Por que a última consulta devolveu None, se não foi o tempo (None quando foi o tempo, ou deu certo)."""
        return self._erro

    def _falhou(self, e: Exception) -> None:
        linhas = str(e).strip().splitlines()
        self._erro = linhas[0] if linhas else type(e).__name__

    def _rodar(self, consulta, params=None, engolir: bool = False):
        """As linhas da consulta, ou None no tempo esgotado. `engolir`: um erro do banco também vira None (com o
        motivo em `erro()`), para o WHERE que veio de fora. Sem `params`, a consulta não é lida atrás de `%s`."""
        self._erro = None
        try:
            with catalogo.somente_leitura(self.conexao, self.limite_ms):
                return self.conexao.execute(consulta, params).fetchall()
        except psycopg.errors.QueryCanceled:
            return None
        except psycopg.Error as e:
            if not engolir:
                raise
            self._falhou(e)
            return None

    def _contar(self, consulta, params=None) -> Optional[int]:
        linhas = self._rodar(consulta, params)
        return None if linhas is None else int(linhas[0][0])

    def linhas(self, esquema, tabela) -> Optional[int]:
        return self._contar(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(esquema, tabela)))

    def nulos(self, esquema, tabela, coluna) -> Optional[int]:
        return self._contar(sql.SQL("SELECT count(*) FROM {} WHERE {} IS NULL").format(
            sql.Identifier(esquema, tabela), sql.Identifier(coluna)))

    def repetidos(self, esquema, tabela, coluna) -> Optional[int]:
        """Quantos valores não nulos aparecem mais de uma vez."""
        c = sql.Identifier(coluna)
        return self._contar(sql.SQL(
            "SELECT count(*) FROM (SELECT 1 FROM {} WHERE {c} IS NOT NULL GROUP BY {c} HAVING count(*) > 1) r"
        ).format(sql.Identifier(esquema, tabela), c=c))

    def orfaos(self, esquema, tabela, coluna, esquema_pai, pai, coluna_pai) -> Optional[int]:
        """Linhas com valor que não existe na coluna do pai (NULL não é órfão)."""
        return self._contar(sql.SQL(
            "SELECT count(*) FROM {f} x WHERE x.{c} IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM {p} y WHERE y.{pc} = x.{c})"
        ).format(f=sql.Identifier(esquema, tabela), c=sql.Identifier(coluna),
                 p=sql.Identifier(esquema_pai, pai), pc=sql.Identifier(coluna_pai)))

    def dependentes(self, oid_tabela: int, attnum: Optional[int] = None) -> Optional[List[Dependente]]:
        """O que quebra ou some junto: chaves estrangeiras de outras tabelas que chegam na tabela (ou
        na coluna) e views que a leem. None se o PostgreSQL cancelou por demora."""
        so_coluna_fk = "" if attnum is None else "AND %(a)s = ANY (k.confkey) "
        so_coluna_dep = "" if attnum is None else "AND d.refobjsubid = %(a)s "
        consulta = (
            "SELECT 'fk', c.oid::bigint, c.relname::text, k.oid::bigint, k.conname::text "
            "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
            "WHERE k.contype = 'f' AND k.confrelid = %(o)s AND k.conrelid <> %(o)s " + so_coluna_fk +
            "UNION "
            "SELECT 'view', c.oid::bigint, c.relname::text, NULL::bigint, NULL::text FROM pg_depend d "
            "JOIN pg_rewrite w ON w.oid = d.objid JOIN pg_class c ON c.oid = w.ev_class "
            "WHERE d.classid = 'pg_rewrite'::regclass AND d.refclassid = 'pg_class'::regclass "
            "AND d.refobjid = %(o)s AND c.oid <> %(o)s " + so_coluna_dep +
            "ORDER BY 3, 5")
        linhas = self._rodar(consulta, {"o": oid_tabela, "a": attnum})
        return None if linhas is None else [Dependente(*l) for l in linhas]

    # ── as da tela Dados ──

    def contar_onde(self, esquema, tabela, onde: str) -> Optional[int]:
        """Quantas linhas o WHERE pega ("" = todas)."""
        linhas = self._rodar(sql.SQL("SELECT count(*) FROM {}{}").format(
            sql.Identifier(esquema, tabela), catalogo.onde_sql(onde)), engolir=True)
        return None if linhas is None else int(linhas[0][0])

    def amostra(self, esquema, tabela, onde: str, colunas: List[str], limite: int = 5) -> Optional[List[tuple]]:
        return self._rodar(sql.SQL("SELECT {} FROM {}{} LIMIT {}").format(
            sql.SQL(", ").join(map(sql.Identifier, colunas)), sql.Identifier(esquema, tabela),
            catalogo.onde_sql(onde), sql.Literal(int(limite))), engolir=True)

    def antes_depois(self, esquema, tabela, onde: str, sets: Dict[str, str],
                     limite: int = 5) -> Optional[List[Tuple[tuple, tuple]]]:
        """Até `limite` linhas como estão e como ficariam. `sets`: coluna (já por `ident`, como `Alterar.sets`)
        → a expressão SQL que ela recebe."""
        colunas = list(sets)
        linhas = self._rodar(sql.SQL("SELECT {}, {} FROM {}{} LIMIT {}").format(
            sql.SQL(", ").join(sql.SQL(c) for c in colunas), sql.SQL(", ").join(sql.SQL(sets[c]) for c in colunas),
            sql.Identifier(esquema, tabela), catalogo.onde_sql(onde), sql.Literal(int(limite))), engolir=True)
        if linhas is None:
            return None
        n = len(colunas)
        return [(tuple(l[:n]), tuple(l[n:])) for l in linhas]

    def cascata(self, esquema, tabela, onde: str, ligacoes: List[Ligacao],
                m: Modelo) -> Optional[List[Tuple[str, str, int]]]:
        """Para cada chave estrangeira que chega na tabela: (tabela filha, ação ao apagar, quantas linhas filhas
        referem as linhas que o WHERE pega). A tabela pai fica sem apelido na subconsulta: o WHERE de um filtro com
        "existe em" a cita pelo nome completo."""
        pai = sql.Identifier(esquema, tabela)
        achados = []
        for lig in ligacoes:
            filha = m.tabelas[lig.origem]
            destino = m.tabelas[lig.destino]
            minhas = [sql.SQL("_ratt_f.{}").format(sql.Identifier(filha.coluna(i).nome)) for i in lig.colunas]
            delas = [sql.SQL("{}.{}").format(pai, sql.Identifier(destino.coluna(i).nome)) for i in lig.colunas_destino]
            linhas = self._rodar(sql.SQL("SELECT count(*) FROM {} AS _ratt_f WHERE ({}) IN (SELECT {} FROM {}{})").format(
                sql.Identifier(filha.esquema, filha.nome), sql.SQL(", ").join(minhas), sql.SQL(", ").join(delas),
                pai, catalogo.onde_sql(onde)), engolir=True)
            if linhas is None:
                return None
            achados.append((f"{filha.esquema}.{filha.nome}", lig.ao_apagar, int(linhas[0][0])))
        return achados

    def versao(self) -> int:
        return catalogo.versao(self.conexao)

    def valor_valido(self, tipo: str, texto: str) -> Optional[bool]:
        """O texto serve para o tipo? `pg_input_is_valid` no PostgreSQL 16+; antes (ou com `forcar_cast`), um CAST
        num savepoint da transação somente leitura. `tipo` vem do catálogo e ainda assim tem de passar por
        `tipo_seguro`. None se não deu para saber (tempo, tipo desconhecido; o motivo em `erro()`)."""
        self._erro = None
        if not tipo_seguro(tipo):
            self._erro = t("chk.tipo_recusado", tipo=tipo)
            return None
        try:
            with catalogo.somente_leitura(self.conexao, self.limite_ms):
                if self.versao() >= 160000 and not self.forcar_cast:
                    return bool(self.conexao.execute("SELECT pg_input_is_valid(%s, %s)", (texto, tipo)).fetchone()[0])
                try:
                    with self.conexao.transaction():
                        self.conexao.execute(sql.SQL("SELECT CAST(%s::text AS {})").format(sql.SQL(tipo)), (texto,))
                    return True
                except psycopg.DataError:                   # classe 22: o valor não serve para o tipo
                    return False
        except psycopg.errors.QueryCanceled:
            return None
        except psycopg.Error as e:
            self._falhou(e)
            return None

    def ja_existe(self, esquema, tabela, coluna, literal_sql: str) -> Optional[int]:
        """Quantas linhas já têm este valor na coluna. `literal_sql` sai de `filtro.literal` (nunca texto cru)."""
        return self.contar_onde(esquema, tabela, f"{rs.ident(coluna)} = {literal_sql}")

    def grupos_repetidos(self, esquema, tabela, onde: str, por_sql: List[str]) -> Optional[Tuple[int, int]]:
        """(grupos com mais de uma linha, linhas que sairiam deixando uma por grupo)."""
        linhas = self._rodar(sql.SQL(
            "SELECT count(*), coalesce(sum(n - 1), 0) FROM (SELECT count(*) AS n FROM {}{} GROUP BY {} "
            "HAVING count(*) > 1) AS g").format(sql.Identifier(esquema, tabela), catalogo.onde_sql(onde),
                                                sql.SQL(", ").join(sql.SQL(p) for p in por_sql)), engolir=True)
        return None if linhas is None else (int(linhas[0][0]), int(linhas[0][1]))


# ── avisos de uma operação ──

# Os apelidos, as famílias e a normalização de tipos moram em filtro.py (o filtro também precisa deles).
_tipo_normal = filtro.tipo_normal
_familia = filtro.familia_fina


def _id_catalogo(id: str):
    """(oid, attnum) de um id do catálogo (`t123`, `t123.c4`); None para o que nasceu no rascunho."""
    m = re.fullmatch(r"t(\d+)(?:\.c(\d+))?", id)
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


class _Contagens:
    """Faz as contagens de uma operação com os nomes da base (o que existe no banco), junta o que
    ficou sem resposta num aviso só e deduz sem consultar o que o rascunho criou."""

    def __init__(self, r: rs.Rascunho, consulta):
        self.r, self.consulta = r, consulta
        self.avisos: List[Aviso] = []

    def _sem_tempo(self, alvo):
        self.avisos.append(Aviso(alvo, "info", "chk.sem_tempo", {}))

    def _base(self, tabela_id, coluna_id=None):
        tab = self.r.base.tabelas.get(tabela_id)
        return tab, (tab.coluna(coluna_id) if tab is not None and coluna_id else None)

    def linhas(self, tabela_id) -> Optional[int]:
        tab, _ = self._base(tabela_id)
        if tab is None:
            return 0                        # criada no rascunho: ainda não tem linha
        n = self.consulta.linhas(tab.esquema, tab.nome)
        if n is None:
            self._sem_tempo(tabela_id)
        return n

    def nulos(self, tabela_id, coluna_id) -> Optional[int]:
        tab, col = self._base(tabela_id, coluna_id)
        if tab is None:
            return 0
        if col is None:                     # coluna do rascunho: só tem NULL se não houver padrão
            vista = self.r.vista().tabelas[tabela_id].coluna(coluna_id)
            return self.linhas(tabela_id) if vista is not None and vista.padrao is None else 0
        n = self.consulta.nulos(tab.esquema, tab.nome, col.nome)
        if n is None:
            self._sem_tempo(coluna_id)
        return n

    def _deduzida(self, tabela_id, coluna_id, extra, repetidos: bool) -> Optional[int]:
        """Contagem de uma coluna que o banco ainda não tem. NULL nunca é repetido nem órfão; com
        padrão, todas as linhas ganham o mesmo valor: repetido se houver mais de uma. Sem como deduzir,
        None (e o aviso de indeterminado)."""
        if tabela_id not in self.r.base.tabelas:
            return 0                        # tabela do rascunho: sem linhas
        tab = self.r.vista().tabelas.get(tabela_id)
        col = (tab.coluna(coluna_id) if tab is not None else None) or extra
        if col is not None and col.padrao is None:
            return 0
        if col is not None and repetidos:
            k = self.linhas(tabela_id)
            return None if k is None else int(k > 1)
        self.avisos.append(Aviso(coluna_id, "info", "chk.indeterminado", {}))
        return None

    def repetidos(self, tabela_id, coluna_id, extra=None) -> Optional[int]:
        tab, col = self._base(tabela_id, coluna_id)
        if col is None:
            return self._deduzida(tabela_id, coluna_id, extra, True)
        n = self.consulta.repetidos(tab.esquema, tab.nome, col.nome)
        if n is None:
            self._sem_tempo(coluna_id)
        return n

    def orfaos(self, origem_id, coluna_id, destino_id, coluna_destino_id, extra=None) -> Optional[int]:
        tab, col = self._base(origem_id, coluna_id)
        pai, col_pai = self._base(destino_id, coluna_destino_id)
        if col is None:
            return self._deduzida(origem_id, coluna_id, extra, False)
        if col_pai is None:
            self.avisos.append(Aviso(coluna_id, "info", "chk.indeterminado", {}))
            return None
        n = self.consulta.orfaos(tab.esquema, tab.nome, col.nome, pai.esquema, pai.nome, col_pai.nome)
        if n is None:
            self._sem_tempo(coluna_id)
        return n


def _nome(v: Modelo, tabela_id, coluna_id=None, extra=None) -> str:
    tab = v.tabelas[tabela_id]
    if coluna_id is None:
        return tab.nome
    c = tab.coluna(coluna_id) or extra
    return f"{tab.nome}.{c.nome}"


def _obrigatoria(op, r, n: _Contagens, v):
    if op.sim:
        k = n.nulos(op.tabela, op.coluna)
        if k:
            n.avisos.append(Aviso(op.coluna, "impede", "chk.nulos_impedem",
                                  {"n": k, "coluna": _nome(v, op.tabela, op.coluna)}))


def _unica(op, r, n: _Contagens, v):
    if op.sim:
        k = n.repetidos(op.tabela, op.coluna)
        if k:
            n.avisos.append(Aviso(op.coluna, "impede", "chk.repetidos_impedem",
                                  {"n": k, "coluna": _nome(v, op.tabela, op.coluna)}))


def _adicionar_coluna(op, r, n: _Contagens, v):
    c = op.coluna
    if c.obrigatoria and c.padrao is None:
        k = n.linhas(op.tabela)
        if k:
            n.avisos.append(Aviso(c.id, "impede", "chk.obrigatoria_sem_padrao",
                                  {"n": k, "coluna": _nome(v, op.tabela, c.id, c)}))


def _ligar(op, r, n: _Contagens, v):
    lig = op.ligacao
    origem, destino = v.tabelas[lig.origem], v.tabelas[lig.destino]
    col = origem.coluna(lig.colunas[0]) or op.nova_coluna
    col_pai = destino.coluna(lig.colunas_destino[0])
    nome = f"{origem.nome}.{col.nome}"
    tipos = {"coluna": nome, "tipo": col.tipo, "pai": f"{destino.nome}.{col_pai.nome}", "tipo_pai": col_pai.tipo}
    if _familia(col.tipo) != _familia(col_pai.tipo):
        n.avisos.append(Aviso(lig.id, "impede", "chk.tipos", tipos))
    else:                                   # de famílias diferentes a contagem nem rodaria
        if _tipo_normal(col.tipo) != _tipo_normal(col_pai.tipo):
            n.avisos.append(Aviso(lig.id, "info", "chk.tipos_familia", tipos))
        k = n.orfaos(lig.origem, col.id, lig.destino, col_pai.id, op.nova_coluna)
        if k:
            n.avisos.append(Aviso(lig.id, "impede", "chk.orfaos_impedem", {"n": k, "coluna": nome}))
    if op.unica:
        n.avisos.append(Aviso(lig.id, "info", "chk.um_para_um", {"coluna": nome}))


def _ainda_depende(d: Dependente, r: rs.Rascunho, v: Modelo) -> bool:
    """Falso se o rascunho já apagou a tabela dependente ou a chave estrangeira (nesse caso ela não
    impede mais). O que a base não conhece (outro esquema, views) continua valendo."""
    alvo = "t%d" % d.oid
    if alvo in r.base.tabelas and (alvo not in v.tabelas or v.tabelas[alvo].estado == "apagado"):
        return False
    if d.oid_restricao is not None:
        k = "k%d" % d.oid_restricao
        if k in r.base.ligacoes and (k not in v.ligacoes or v.ligacoes[k].estado == "apagado"):
            return False
    return True


def _dependentes(op, r, n: _Contagens, v, alvo, nome, oid_attnum):
    if oid_attnum is None:                  # o que nasceu no rascunho não existe no banco: ninguém depende
        return
    deps = n.consulta.dependentes(*oid_attnum)
    if deps is None:
        n._sem_tempo(alvo)
        return
    deps = [d for d in (deps or []) if _ainda_depende(d, r, v)]
    if deps:
        n.avisos.append(Aviso(alvo, "atencao", "chk.dependentes",
                              {"nome": nome, "lista": "; ".join(d.texto() for d in deps)}))
        if not op.cascata:
            n.avisos.append(Aviso(alvo, "impede", "chk.precisa_cascata", {"nome": nome}))


def _apagar_coluna(op, r, n: _Contagens, v):
    ids = _id_catalogo(op.coluna) if op.tabela in r.base.tabelas else None
    _dependentes(op, r, n, v, op.coluna, _nome(v, op.tabela, op.coluna), ids)


def _apagar_tabela(op, r, n: _Contagens, v):
    ids = _id_catalogo(op.tabela) if op.tabela in r.base.tabelas else None
    nome = v.tabelas[op.tabela].nome
    _dependentes(op, r, n, v, op.tabela, nome, ids)
    k = n.linhas(op.tabela)
    if k:
        n.avisos.append(Aviso(op.tabela, "atencao", "chk.linhas_somem", {"n": k, "tabela": nome}))


_POR_OPERACAO = {rs.Obrigatoria: _obrigatoria, rs.Unica: _unica, rs.AdicionarColuna: _adicionar_coluna,
                 rs.Ligar: _ligar, rs.ApagarColuna: _apagar_coluna, rs.ApagarTabela: _apagar_tabela}


# ── avisos das operações de dados ──
# As contagens rodam com os nomes da BASE (o que o banco tem agora): o WHERE de cada uma é `op.onde(r.base)`, o
# mesmo texto que o comando vai rodar. O que o rascunho criou e o banco ainda não tem não é consultado.

_SOFT_DELETE = ("deleted_at", "discarded_at", "is_deleted", "ativo", "active", "removido")
_CONTAGENS = ("chk.linhas_alteradas", "chk.linhas_apagadas")


def contagem(avisos: List[Aviso]) -> Optional[int]:
    """Quantas linhas a operação pega, como os avisos contaram; None se não deu para contar."""
    return next((a.valores["n"] for a in avisos if a.chave in _CONTAGENS), None)


def _unica_sozinha(tab, c) -> bool:
    """A coluna sozinha não aceita repetido: UNIQUE dela ou a chave primária inteira."""
    return c.unica or (c.pk and len(tab.pk()) == 1)


class _Dados:
    def __init__(self, op, r: rs.Rascunho, consulta):
        self.op, self.r, self.consulta = op, r, consulta
        self.v = r.vista()
        self.tab = self.v.tabelas[op.tabela]
        self.base = r.base
        self.tab_base = r.base.tabelas.get(op.tabela)
        self.na_base = self.tab_base is not None and all(rs._nome_na_base(r.base, i) is not None for i in op.refere())
        self.avisos: List[Aviso] = []

    def avisar(self, alvo, nivel, chave, **valores) -> None:
        if not any(a.alvo == alvo and a.chave == chave and a.valores == valores for a in self.avisos):
            self.avisos.append(Aviso(alvo, nivel, chave, valores))

    def falhou(self, alvo) -> None:
        """A consulta devolveu None: o tempo acabou ou (com o WHERE escrito à mão) o banco recusou."""
        _sem_resposta(self.consulta, self.avisar, alvo)

    def contar(self, onde: str, alvo) -> Optional[int]:
        n = self.consulta.contar_onde(self.tab_base.esquema, self.tab_base.nome, onde)
        if n is None:
            self.falhou(alvo)
        return n

    def valor(self, c, texto: str) -> Optional[str]:
        """O literal SQL de um valor digitado, ou None (com o aviso) se ele não servir para o tipo da coluna."""
        if filtro.familia(c.tipo) == "texto" and texto != texto.strip():
            self.avisar(c.id, "atencao", "chk.espacos_valor", coluna=c.nome)
        try:
            literal = filtro.literal(texto, c.tipo)
        except ValueError:
            self.avisar(c.id, "impede", "chk.valor_invalido", coluna=c.nome, valor=texto, tipo=c.tipo)
            return None
        base = self.tab_base.coluna(c.id) if self.na_base else None
        if base is None or filtro.familia(c.tipo) == "booleano" or filtro.tipo_normal(c.tipo) == "text":
            return literal                  # o literal já decide (ou não há banco para perguntar)
        ok = self.consulta.valor_valido(base.tipo, texto)
        if ok is None:
            self.falhou(c.id)
        elif not ok:
            self.avisar(c.id, "impede", "chk.valor_invalido", coluna=c.nome, valor=texto, tipo=c.tipo)
            return None
        return literal

    def ja_existe(self, c, texto: str, literal: str) -> None:
        base = self.tab_base.coluna(c.id)
        k = self.consulta.ja_existe(self.tab_base.esquema, self.tab_base.nome, base.nome, literal)
        if k is None:
            self.falhou(c.id)
        elif k:
            self.avisar(c.id, "impede", "chk.ja_existe", coluna=c.nome, valor=texto, n=k)

    def pai_existe(self, c, texto: str) -> None:
        """Valor de chave estrangeira (de uma coluna só) que não existe na tabela pai."""
        for lig in self.v.ligacoes.values():
            if lig.estado == "apagado" or lig.origem != self.op.tabela or tuple(lig.colunas) != (c.id,):
                continue
            pai = self.base.tabelas.get(lig.destino)
            col_pai = pai.coluna(lig.colunas_destino[0]) if pai is not None else None
            if col_pai is None:
                continue                    # o pai ainda não existe no banco: o ensaio confere
            try:
                literal = filtro.literal(texto, col_pai.tipo)
            except ValueError:
                continue
            k = self.consulta.ja_existe(pai.esquema, pai.nome, col_pai.nome, literal)
            if k is None:
                self.falhou(c.id)
            elif k == 0:
                vista_pai = self.v.tabelas[lig.destino]
                self.avisar(c.id, "impede", "chk.fk_inexistente", coluna=c.nome, valor=texto,
                            pai=f"{vista_pai.nome}.{vista_pai.coluna(col_pai.id).nome}")

    def cascata(self, onde: str) -> None:
        ligs = [l for l in self.base.ligacoes.values() if l.destino == self.op.tabela]
        if not ligs:
            return
        achados = self.consulta.cascata(self.tab_base.esquema, self.tab_base.nome, onde, ligs, self.base)
        if achados is None:
            self.falhou(self.op.tabela)
            return
        for filha, acao, k in achados or []:
            if not k:
                continue
            chave, nivel = {"CASCADE": ("chk.cascata_apaga", "atencao"), "SET NULL": ("chk.cascata_esvazia", "atencao"),
                            "SET DEFAULT": ("chk.cascata_padrao", "atencao")}.get(acao, ("chk.impede_filhos", "impede"))
            self.avisar(self.op.tabela, nivel, chave, n=k, tabela=filha)


def _sem_resposta(consulta, avisar, alvo) -> None:
    erro = getattr(consulta, "erro", None)
    motivo = erro() if callable(erro) else None
    if motivo:
        avisar(alvo, "atencao", "chk.consulta_falhou", erro=motivo)
    else:
        avisar(alvo, "info", "chk.sem_tempo")


def _inserir(d: _Dados) -> None:
    op, tab = d.op, d.tab
    for c in tab.colunas:
        if c.estado != "apagado" and c.obrigatoria and c.padrao is None and not c.identidade \
                and op.valores.get(c.id) is None:
            d.avisar(c.id, "impede", "chk.obrigatoria_vazia", coluna=c.nome)
    for i, texto in op.valores.items():
        c = tab.coluna(i)
        if c is None or texto is None:
            continue
        literal = d.valor(c, texto)
        if literal is None or not d.na_base:
            continue
        if _unica_sozinha(tab, c):
            d.ja_existe(c, texto, literal)
        d.pai_existe(c, texto)


def _alterar(d: _Dados) -> None:
    op, tab = d.op, d.tab
    n, onde, sets = None, None, {}
    if d.na_base:
        onde, sets = op.onde(d.base), op.sets(d.base)
        n = d.contar(onde, op.tabela)
        if n is not None:
            d.avisar(op.tabela, "info", "chk.linhas_alteradas", n=n, tabela=tab.nome)
    for i, mud in op.mudancas.items():
        c = tab.coluna(i)
        if c.obrigatoria and mud.tipo == "vazio" and n != 0:
            d.avisar(c.id, "impede", "chk.obrigatoria_recebe_vazio", coluna=c.nome)
        elif c.obrigatoria and mud.tipo in ("coluna", "branco_vazio") and d.na_base and n != 0:
            expr = sets[rs.ident(d.tab_base.coluna(i).nome)]
            k = d.contar(f"({onde}) AND ({expr}) IS NULL", c.id)
            if k:
                d.avisar(c.id, "impede", "chk.obrigatoria_recebe_vazio_n", coluna=c.nome, n=k)
        if mud.tipo != "valor" or mud.valor is None:
            continue
        literal = d.valor(c, mud.valor)
        if literal is None or not d.na_base:
            continue
        if _unica_sozinha(tab, c):
            if n is not None and n > 1:
                d.avisar(c.id, "impede", "chk.unica_varias", coluna=c.nome, n=n)
            elif n:                         # quem já tem o valor fica de fora do UPDATE e continua com ele
                d.ja_existe(c, mud.valor, literal)
        d.pai_existe(c, mud.valor)


def _apagar(d: _Dados) -> None:
    op = d.op
    if d.na_base:
        onde = op.onde(d.base)
        n = d.contar(onde, op.tabela)
        if n is not None:
            d.avisar(op.tabela, "info", "chk.linhas_apagadas", n=n, tabela=d.tab.nome)
        d.cascata(onde)
    _soft_delete(d)


def _soft_delete(d: _Dados) -> None:
    for c in d.tab.colunas:
        if c.estado != "apagado" and c.nome.lower() in _SOFT_DELETE:
            d.avisar(d.op.tabela, "info", "chk.soft_delete", coluna=c.nome)
            return


def _duplicatas(d: _Dados) -> None:
    op, tab = d.op, d.tab
    if not tab.pk():
        d.avisar(op.tabela, "atencao", "chk.sem_pk_ctid", tabela=tab.nome)
    if not d.na_base:
        return
    grupos = d.consulta.grupos_repetidos(d.tab_base.esquema, d.tab_base.nome, op.onde_comparadas(d.base),
                                         op.por_sql(d.base))
    if grupos is None:
        d.falhou(op.tabela)
    else:
        d.avisar(op.tabela, "info", "chk.grupos_repetidos", grupos=(grupos or (0, 0))[0])
    onde = op.onde(d.base)
    n = d.contar(onde, op.tabela)
    if n is not None:
        d.avisar(op.tabela, "info", "chk.linhas_apagadas", n=n, tabela=tab.nome)
    fora = d.contar(op.onde_fora(d.base), op.tabela)
    if fora:
        d.avisar(op.tabela, "info", "chk.duplicatas_vazias_fora", n=fora)
    d.cascata(onde)


_DE_DADOS = {rs.Inserir: _inserir, rs.Alterar: _alterar, rs.Apagar: _apagar, rs.ApagarDuplicatas: _duplicatas}


def _avisos_de_dados(op, r: rs.Rascunho, consulta) -> List[Aviso]:
    d = _Dados(op, r, consulta)
    _DE_DADOS[type(op)](d)
    if not d.na_base:
        d.avisar(op.tabela, "info", "chk.so_depois_de_aplicar", tabela=d.tab.nome)
    else:
        antes = sum(1 for o in r.operacoes if op.tabela in (o.refere() | o.cria()))
        if antes:
            d.avisar(op.tabela, "info", "chk.aproximada", n=antes)
    return d.avisos


def avisos_da_operacao(op, r: rs.Rascunho, consulta) -> List[Aviso]:
    """O que a operação vai encontrar no banco, antes de entrar no rascunho. `consulta` é uma `Consulta`
    (ou qualquer objeto com os mesmos métodos)."""
    if type(op) in _DE_DADOS:
        return _avisos_de_dados(op, r, consulta)
    f = _POR_OPERACAO.get(type(op))
    if f is None:
        return []
    n = _Contagens(r, consulta)
    f(op, r, n, r.vista())
    return n.avisos


# ── avisos do filtro ──

def avisos_do_filtro(f: filtro.Filtro, m: Modelo, tabela_id: str, consulta) -> List[Aviso]:
    """As armadilhas do filtro, contadas no banco. `m` é o modelo com os nomes do banco (a base do rascunho): uma
    coluna que só existe no rascunho deixa as contagens para depois de aplicar."""
    avisos: List[Aviso] = []

    def avisar(alvo, nivel, chave, **valores):
        if not any(a.alvo == alvo and a.chave == chave for a in avisos):
            avisos.append(Aviso(alvo, nivel, chave, valores))

    if filtro.fora_lista_com_vazio(f):
        avisar(tabela_id, "atencao", "chk.fora_lista_vazio")
    tab = m.tabelas.get(tabela_id)
    if tab is None or any(rs._nome_na_base(m, i) is None for i in filtro.refere(f, tabela_id)):
        avisar(tabela_id, "info", "chk.so_depois_de_aplicar", tabela=tab.nome if tab is not None else tabela_id)
        return avisos

    def contar(onde, alvo):
        n = consulta.contar_onde(tab.esquema, tab.nome, onde)
        if n is None:
            _sem_resposta(consulta, avisar, alvo)
        return n

    for cid in filtro.deixa_vazios_de_fora(f, m, tabela_id):
        col = tab.coluna(cid)
        k = contar(f"{rs.ident(col.nome)} IS NULL", cid)
        if k:
            avisar(cid, "atencao", "chk.vazios_de_fora", n=k, coluna=col.nome)
    if f.texto is not None and f.texto.strip():
        return avisos
    for c in f.condicoes:
        col = tab.coluna(c.coluna) if c.existe is None and c.outra_coluna is None else None
        if col is None or c.transformacao or c.operador not in ("contem", "comeca", "termina") \
                or filtro.familia(col.tipo) != "texto":
            continue
        try:                                # quem casaria sem o espaço sobrando nas pontas e não casa com ele
            aparado = filtro.where(filtro.Filtro([filtro.Condicao(c.coluna, c.operador, c.valores, "trim",
                                                                  diferenciar=c.diferenciar)]), m, tabela_id)
            cru = filtro.where(filtro.Filtro([filtro.Condicao(c.coluna, c.operador, c.valores,
                                                              diferenciar=c.diferenciar)]), m, tabela_id)
        except ValueError:
            continue
        k = contar(f"{aparado} AND NOT ({cru})", col.id)
        if k:
            avisar(col.id, "atencao", "chk.espacos_nas_pontas", n=k, coluna=col.nome)
    return avisos
