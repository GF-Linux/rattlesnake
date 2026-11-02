"""Avisos que ensinam — o canvas "avisa, não decide".

Dois tipos: os estáticos, lidos só do modelo (tabela sem PK, FK sem índice), e os de uma operação,
que contam no banco (NULLs, repetidos, órfãos, dependentes) antes de a operação entrar no rascunho.
Tudo aqui é leitura: nunca DDL, sempre com `statement_timeout` curto. Um aviso "impede" não bloqueia
nada; diz o que o PostgreSQL recusaria e por quê, e a pessoa decide.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import psycopg
from psycopg import sql

from . import rascunho as rs
from .idioma import t
from .modelo import Modelo, opcional


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


# ── contagens no banco, só leitura ──

class Consulta:
    """Contagens pontuais. Cada uma roda numa transação com `SET LOCAL statement_timeout` e devolve
    None se o PostgreSQL cancelar por demora (tabela grande demais para contar de graça)."""

    def __init__(self, conexao, limite_ms: int = 2000):
        self.conexao = conexao
        self.limite_ms = int(limite_ms)

    def _rodar(self, consulta, params=()):
        try:
            with self.conexao.transaction():
                self.conexao.execute("SET LOCAL statement_timeout = %d" % self.limite_ms)
                return self.conexao.execute(consulta, params).fetchall()
        except psycopg.errors.QueryCanceled:
            return None

    def _contar(self, consulta, params=()) -> Optional[int]:
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

    def dependentes(self, oid_tabela: int, attnum: Optional[int] = None) -> List[str]:
        """O que quebra ou some junto: chaves estrangeiras de outras tabelas que chegam na tabela (ou
        na coluna) e views que a leem. Vazio se nada depende, ou se o PostgreSQL demorou demais."""
        so_coluna_fk = "" if attnum is None else "AND %(a)s = ANY (k.confkey) "
        so_coluna_dep = "" if attnum is None else "AND d.refobjsubid = %(a)s "
        consulta = (
            "SELECT 'tabela ' || c.relname || ' (chave estrangeira ' || k.conname || ')' "
            "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
            "WHERE k.contype = 'f' AND k.confrelid = %(o)s AND k.conrelid <> %(o)s " + so_coluna_fk +
            "UNION "
            "SELECT 'view ' || c.relname FROM pg_depend d "
            "JOIN pg_rewrite w ON w.oid = d.objid JOIN pg_class c ON c.oid = w.ev_class "
            "WHERE d.classid = 'pg_rewrite'::regclass AND d.refclassid = 'pg_class'::regclass "
            "AND d.refobjid = %(o)s AND c.oid <> %(o)s " + so_coluna_dep +
            "ORDER BY 1")
        linhas = self._rodar(consulta, {"o": oid_tabela, "a": attnum})
        return [] if linhas is None else [l[0] for l in linhas]


# ── avisos de uma operação ──

_SINONIMOS = {"int8": "bigint", "int4": "integer", "int": "integer", "int2": "smallint",
              "varchar": "character varying", "timestamptz": "timestamp with time zone", "bool": "boolean"}


def _tipo_normal(tipo: str) -> str:
    """O tipo sem modificador `(255)` e com os apelidos do PostgreSQL unidos: o que importa para ligar."""
    base = re.sub(r"\(.*?\)", "", tipo).strip().lower()
    return _SINONIMOS.get(base, base)


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

    def repetidos(self, tabela_id, coluna_id) -> Optional[int]:
        tab, col = self._base(tabela_id, coluna_id)
        if col is None:
            self.avisos.append(Aviso(coluna_id, "info", "chk.indeterminado", {}))
            return None
        n = self.consulta.repetidos(tab.esquema, tab.nome, col.nome)
        if n is None:
            self._sem_tempo(coluna_id)
        return n

    def orfaos(self, origem_id, coluna_id, destino_id, coluna_destino_id) -> Optional[int]:
        tab, col = self._base(origem_id, coluna_id)
        pai, col_pai = self._base(destino_id, coluna_destino_id)
        if col is None or col_pai is None:
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
    if _tipo_normal(col.tipo) != _tipo_normal(col_pai.tipo):
        n.avisos.append(Aviso(lig.id, "impede", "chk.tipos", {
            "coluna": nome, "tipo": col.tipo, "pai": f"{destino.nome}.{col_pai.nome}", "tipo_pai": col_pai.tipo}))
    else:                                   # com tipos diferentes a contagem nem rodaria
        k = n.orfaos(lig.origem, col.id, lig.destino, col_pai.id)
        if k:
            n.avisos.append(Aviso(lig.id, "impede", "chk.orfaos_impedem", {"n": k, "coluna": nome}))
    if op.unica:
        n.avisos.append(Aviso(lig.id, "info", "chk.um_para_um", {"coluna": nome}))


def _dependentes(op, r, n: _Contagens, alvo, nome, oid_attnum):
    deps = []
    if oid_attnum is not None:              # o que nasceu no rascunho não existe no banco: ninguém depende
        deps = n.consulta.dependentes(*oid_attnum) or []
    if deps:
        n.avisos.append(Aviso(alvo, "atencao", "chk.dependentes", {"nome": nome, "lista": "; ".join(deps)}))
        if not op.cascata:
            n.avisos.append(Aviso(alvo, "impede", "chk.precisa_cascata", {"nome": nome}))


def _apagar_coluna(op, r, n: _Contagens, v):
    ids = _id_catalogo(op.coluna) if op.tabela in r.base.tabelas else None
    _dependentes(op, r, n, op.coluna, _nome(v, op.tabela, op.coluna), ids)


def _apagar_tabela(op, r, n: _Contagens, v):
    ids = _id_catalogo(op.tabela) if op.tabela in r.base.tabelas else None
    nome = v.tabelas[op.tabela].nome
    _dependentes(op, r, n, op.tabela, nome, ids)
    k = n.linhas(op.tabela)
    if k:
        n.avisos.append(Aviso(op.tabela, "atencao", "chk.linhas_somem", {"n": k, "tabela": nome}))


_POR_OPERACAO = {rs.Obrigatoria: _obrigatoria, rs.Unica: _unica, rs.AdicionarColuna: _adicionar_coluna,
                 rs.Ligar: _ligar, rs.ApagarColuna: _apagar_coluna, rs.ApagarTabela: _apagar_tabela}


def avisos_da_operacao(op, r: rs.Rascunho, consulta) -> List[Aviso]:
    """O que a operação vai encontrar no banco, antes de entrar no rascunho. `consulta` é uma `Consulta`
    (ou qualquer objeto com os mesmos métodos)."""
    f = _POR_OPERACAO.get(type(op))
    if f is None:
        return []
    n = _Contagens(r, consulta)
    f(op, r, n, r.vista())
    return n.avisos
