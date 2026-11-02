"""O rascunho do canvas: cada edição vira uma operação, e a lista de operações vira SQL legível.

Tudo é puro (sem banco, sem tela). O rascunho nunca mexe no modelo que veio do banco: a `vista()` é uma
cópia dele com as operações aplicadas uma a uma, e os itens tocados levam uma marca em `estado`
("novo", "mudado", "apagado") para o desenho poder pintá-los. O SQL de cada operação é gerado com os
nomes que valem *antes* dela, por isso uma renomeação vale para as operações seguintes.
"""
from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from typing import ClassVar, Dict, Iterable, List, Optional, Set, Tuple

from .idioma import t
from .modelo import Coluna, Ligacao, Modelo, Tabela

# ── identificadores ──

# Palavras que o quote_ident do PostgreSQL cita: tudo que não é "unreserved" no Apêndice C
# (reservadas, reservadas como função/tipo e as de nome de coluna).
_RESERVADAS = frozenset("""
all analyse analyze and any array as asc asymmetric authorization between bigint binary bit boolean both
case cast char character check coalesce collate collation column concurrently constraint create cross
current_catalog current_date current_role current_schema current_time current_timestamp current_user
dec decimal default deferrable desc distinct do else end except exists extract false fetch float for
foreign freeze from full grant greatest group grouping having ilike in initially inner inout int integer
intersect interval into is isnull join json json_array json_arrayagg json_exists json_object
json_objectagg json_query json_scalar json_serialize json_table json_value lateral leading least left
like limit localtime localtimestamp merge_action national natural nchar none normalize not notnull null
nullif numeric offset on only or order out outer overlaps overlay placing position precision primary real
references returning right row select session_user setof similar smallint some substring symmetric
system_user table tablesample then time timestamp to trailing treat trim true union unique user using
values variadic varchar verbose when where window with xmlattributes xmlconcat xmlelement xmlexists
xmlforest xmlnamespaces xmlparse xmlpi xmlroot xmlserialize xmltable
""".split())

_SEGURO = re.compile(r"^[a-z_][a-z0-9_]*$")
# Só nomes de tipo conhecidos (com `(n)` / `(n,m)` e `[]` opcionais): texto livre aqui iria cru para o SQL
# (`bigint references users`, `text); drop table x; --`).
_PALAVRAS_TIPO = """
smallint integer int bigint int2 int4 int8 numeric decimal real float float4 float8 serial serial2 serial4 serial8
smallserial bigserial money text varchar char character bpchar name citext bit varbit boolean bool date time timetz
timestamp timestamptz interval uuid json jsonb bytea inet cidr macaddr macaddr8 xml point line lseg box path polygon
circle tsvector tsquery oid
""".split()
_VARIAS_PALAVRAS = ["double precision", "character varying", "bit varying", "timestamp with time zone",
                    "timestamp without time zone", "time with time zone", "time without time zone"]
_TIPO = re.compile(
    r"^(?:(?:timestamp|time)\(\d+\) with(?:out)? time zone|(?:"
    + "|".join(re.escape(p) for p in sorted(_VARIAS_PALAVRAS + _PALAVRAS_TIPO, key=len, reverse=True))
    + r")(?:\(\d+(?:,\s*\d+)?\))?)(?:\[\])?$")

_AO_APAGAR = frozenset({"NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"})

TIPOS = ("bigint", "integer", "numeric", "text", "boolean", "date", "timestamptz", "uuid", "jsonb")


def ident(nome: str) -> str:
    """O nome como o PostgreSQL o exige no SQL: sem aspas só se casar `^[a-z_][a-z0-9_]*$` e não for
    palavra reservada; senão entre aspas, com `"` dobrado."""
    if nome == "":
        raise ValueError(t("canvas.e.nome_vazio"))
    if _SEGURO.fullmatch(nome) and nome not in _RESERVADAS:
        return nome
    return '"' + nome.replace('"', '""') + '"'


def nome_tabela(tab: Tabela) -> str:
    """`esquema.tabela`, sempre — cada parte por `ident`. Sem o schema, o nome seguiria o `search_path`
    (`"$user", public`): um `DROP TABLE users` pensado para `public.users` apagaria `jared.users` se ela existir."""
    return ident(tab.esquema) + "." + ident(tab.nome)


def tipo_valido(tipo: str, referencia: Optional[str] = None) -> bool:
    """Um dos tipos da lista ou texto livre que tenha cara de tipo (`varchar(255)`, `int[]`...). `referencia` é o
    tipo da PK de destino como o catálogo o escreve (domínio, enum, tipo de outro schema): igual a ele, caractere
    por caractere, também vale — o texto vem do catálogo, não de quem digitou."""
    return (referencia is not None and tipo == referencia) or tipo in TIPOS or bool(_TIPO.fullmatch(tipo))


def _clip(s: str, n: int) -> str:
    return s.encode("utf-8")[:n].decode("utf-8", "ignore")


def nome_auto(tabela: str, colunas: List[str], sufixo: str) -> str:
    """`{tabela}_{colunas}_{sufixo}` truncado a 63 bytes como o PostgreSQL (encurta a parte mais longa)."""
    a, b = tabela, "_".join(colunas)
    disponivel = 63 - (len(sufixo) + 2)
    while len(a.encode("utf-8")) + len(b.encode("utf-8")) > disponivel:
        if len(a.encode("utf-8")) > len(b.encode("utf-8")):
            a = _clip(a, len(a.encode("utf-8")) - 1)
        else:
            b = _clip(b, len(b.encode("utf-8")) - 1)
    return f"{a}_{b}_{sufixo}"


# ── achar coisas no modelo pelo id ──

def _achar(m: Modelo, id: str):
    """(objeto, tabela_dona) para um id de tabela, coluna ou ligação; None se não existir."""
    if id in m.tabelas:
        return m.tabelas[id], m.tabelas[id]
    if id in m.ligacoes:
        return m.ligacoes[id], None
    for tab in m.tabelas.values():
        c = tab.coluna(id)
        if c is not None:
            return c, tab
    return None


def _nome_col(m: Modelo, tab: Tabela, id: str, extra: Optional[Coluna] = None) -> str:
    c = tab.coluna(id)
    if c is None and extra is not None and extra.id == id:
        c = extra
    if c is None:
        raise KeyError(id)
    return c.nome


def _marcar(obj, estado: str) -> None:
    """Marca o item sem rebaixar um "novo" (algo criado no rascunho continua novo ao ser editado)."""
    if obj.estado != "novo":
        obj.estado = estado


def _col_sql(c: Coluna, sem_not_null: bool = False, referencia: Optional[str] = None) -> str:
    if not tipo_valido(c.tipo, referencia):
        raise ValueError(t("canvas.e.tipo", tipo=c.tipo))
    partes = [ident(c.nome), c.tipo]
    if c.identidade:
        partes.append("GENERATED ALWAYS AS IDENTITY")
    if c.obrigatoria and not sem_not_null:
        partes.append("NOT NULL")
    if c.padrao is not None and c.padrao != "":
        partes.append("DEFAULT " + c.padrao)
    return " ".join(partes)


# ── operações ──

class Operacao:
    tipo: ClassVar[str] = ""

    def aplicar(self, m: Modelo) -> None:
        raise NotImplementedError

    def comandos(self, m: Modelo) -> List[str]:
        raise NotImplementedError

    def descricao(self, m: Modelo) -> str:
        raise NotImplementedError

    def refere(self) -> Set[str]:
        """Ids que já precisam existir (e não estar apagados) para a operação valer."""
        return set()

    def cria(self) -> Set[str]:
        """Ids que a operação cria."""
        return set()


@dataclass
class CriarTabela(Operacao):
    tabela: Tabela
    # nome que cada tabela/coluna/ligação da base tinha quando a operação entrou: se o mesmo id passar a
    # ter outro nome (o oid reaproveitado depois de um `ratt db reset`), a operação deixa de valer
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "criar_tabela"

    def aplicar(self, m: Modelo) -> None:
        nova = copy.deepcopy(self.tabela)
        nova.estado = "novo"
        for c in nova.colunas:
            c.estado = "novo"
        m.tabelas[nova.id] = nova

    def comandos(self, m: Modelo) -> List[str]:
        tab = self.tabela
        pk = tab.pk()
        inline = len(pk) == 1 and pk[0].identidade
        itens = []
        for c in tab.colunas:
            if inline and c.pk:
                itens.append(_col_sql(c, sem_not_null=True) + " PRIMARY KEY")
            else:
                itens.append(_col_sql(c))
        if pk and not inline:
            itens.append("PRIMARY KEY (" + ", ".join(ident(c.nome) for c in pk) + ")")
        return [f"CREATE TABLE {nome_tabela(tab)} ({', '.join(itens)})"]

    def descricao(self, m: Modelo) -> str:
        return t("op.criar_tabela", tabela=self.tabela.nome)

    def cria(self) -> Set[str]:
        return {self.tabela.id} | {c.id for c in self.tabela.colunas}


@dataclass
class AdicionarColuna(Operacao):
    tabela: str
    coluna: Coluna
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "adicionar_coluna"

    def aplicar(self, m: Modelo) -> None:
        c = copy.deepcopy(self.coluna)
        c.estado = "novo"
        m.tabelas[self.tabela].colunas.append(c)

    def comandos(self, m: Modelo) -> List[str]:
        return [f"ALTER TABLE {nome_tabela(m.tabelas[self.tabela])} ADD COLUMN {_col_sql(self.coluna)}"]

    def descricao(self, m: Modelo) -> str:
        return t("op.adicionar_coluna", coluna=self.coluna.nome, tabela=m.tabelas[self.tabela].nome)

    def refere(self) -> Set[str]:
        return {self.tabela}

    def cria(self) -> Set[str]:
        return {self.coluna.id}


@dataclass
class Renomear(Operacao):
    tabela: str
    coluna: Optional[str]
    novo: str
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "renomear"

    def aplicar(self, m: Modelo) -> None:
        tab = m.tabelas[self.tabela]
        if self.coluna is None:
            tab.nome = self.novo
            _marcar(tab, "mudado")
        else:
            c = tab.coluna(self.coluna)
            c.nome = self.novo
            _marcar(c, "mudado")

    def comandos(self, m: Modelo) -> List[str]:
        tab = m.tabelas[self.tabela]
        if self.coluna is None:
            return [f"ALTER TABLE {nome_tabela(tab)} RENAME TO {ident(self.novo)}"]
        antigo = _nome_col(m, tab, self.coluna)
        return [f"ALTER TABLE {nome_tabela(tab)} RENAME COLUMN {ident(antigo)} TO {ident(self.novo)}"]

    def descricao(self, m: Modelo) -> str:
        tab = m.tabelas[self.tabela]
        if self.coluna is None:
            return t("op.renomear_tabela", tabela=tab.nome, novo=self.novo)
        return t("op.renomear_coluna", tabela=tab.nome, coluna=_nome_col(m, tab, self.coluna), novo=self.novo)

    def refere(self) -> Set[str]:
        return {self.tabela} | ({self.coluna} if self.coluna else set())


@dataclass
class Obrigatoria(Operacao):
    tabela: str
    coluna: str
    sim: bool
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "obrigatoria"

    def aplicar(self, m: Modelo) -> None:
        c = m.tabelas[self.tabela].coluna(self.coluna)
        c.obrigatoria = self.sim
        _marcar(c, "mudado")

    def comandos(self, m: Modelo) -> List[str]:
        tab = m.tabelas[self.tabela]
        acao = "SET" if self.sim else "DROP"
        return [f"ALTER TABLE {nome_tabela(tab)} ALTER COLUMN {ident(_nome_col(m, tab, self.coluna))} {acao} NOT NULL"]

    def descricao(self, m: Modelo) -> str:
        tab = m.tabelas[self.tabela]
        return t("op.obrigatoria_sim" if self.sim else "op.obrigatoria_nao",
                 tabela=tab.nome, coluna=_nome_col(m, tab, self.coluna))

    def refere(self) -> Set[str]:
        return {self.tabela, self.coluna}


@dataclass
class Unica(Operacao):
    tabela: str
    coluna: str
    sim: bool
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "unica"

    def _nome(self, tab: Tabela, c: Coluna) -> str:
        return nome_auto(tab.nome, [c.nome], "key")

    def aplicar(self, m: Modelo) -> None:
        tab = m.tabelas[self.tabela]
        c = tab.coluna(self.coluna)
        if self.sim:
            c.nome_unica = self._nome(tab, c)
        else:
            c.nome_unica = None
        c.unica = self.sim
        _marcar(c, "mudado")

    def comandos(self, m: Modelo) -> List[str]:
        tab = m.tabelas[self.tabela]
        c = tab.coluna(self.coluna)
        if self.sim:
            return [f"ALTER TABLE {nome_tabela(tab)} ADD CONSTRAINT {ident(self._nome(tab, c))} UNIQUE ({ident(c.nome)})"]
        nome = c.nome_unica or self._nome(tab, c)
        return [f"ALTER TABLE {nome_tabela(tab)} DROP CONSTRAINT {ident(nome)}"]

    def descricao(self, m: Modelo) -> str:
        tab = m.tabelas[self.tabela]
        return t("op.unica_sim" if self.sim else "op.unica_nao",
                 tabela=tab.nome, coluna=_nome_col(m, tab, self.coluna))

    def refere(self) -> Set[str]:
        return {self.tabela, self.coluna}


@dataclass
class Ligar(Operacao):
    ligacao: Ligacao
    nova_coluna: Optional[Coluna] = None
    unica: bool = False
    indice: bool = True
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "ligar"

    def _nomes(self, m: Modelo) -> Tuple[Tabela, Tabela, List[str], List[str]]:
        lig = self.ligacao
        origem, destino = m.tabelas[lig.origem], m.tabelas[lig.destino]
        cols = [_nome_col(m, origem, i, self.nova_coluna) for i in lig.colunas]
        dest = [_nome_col(m, destino, i) for i in lig.colunas_destino]
        return origem, destino, cols, dest

    def _nome_fk(self, origem: Tabela, cols: List[str]) -> str:
        return self.ligacao.nome or nome_auto(origem.nome, cols, "fkey")

    def aplicar(self, m: Modelo) -> None:
        origem, _, cols, _ = self._nomes(m)
        lig = copy.deepcopy(self.ligacao)
        lig.nome = self._nome_fk(origem, cols)
        lig.estado = "novo"
        if self.nova_coluna is not None:
            nc = copy.deepcopy(self.nova_coluna)
            nc.estado = "novo"
            origem.colunas.append(nc)
        if len(lig.colunas) == 1:
            c = origem.coluna(lig.colunas[0])
            if self.unica:
                c.unica = True
                c.nome_unica = nome_auto(origem.nome, cols, "key")
                _marcar(c, "mudado")
            if self.indice:
                c.indexada = True
        m.ligacoes[lig.id] = lig

    def comandos(self, m: Modelo) -> List[str]:
        lig = self.ligacao
        origem, destino, cols, dest = self._nomes(m)
        t_sql = nome_tabela(origem)
        lista = ", ".join(ident(c) for c in cols)
        cmds = []
        if self.nova_coluna is not None:
            ref = destino.coluna(lig.colunas_destino[0]).tipo if len(lig.colunas_destino) == 1 else None
            cmds.append(f"ALTER TABLE {t_sql} ADD COLUMN {_col_sql(self.nova_coluna, referencia=ref)}")
        fk = (f"ALTER TABLE {t_sql} ADD CONSTRAINT {ident(self._nome_fk(origem, cols))} FOREIGN KEY ({lista}) "
              f"REFERENCES {nome_tabela(destino)} ({', '.join(ident(c) for c in dest)})")
        if lig.ao_apagar not in _AO_APAGAR:
            raise ValueError(t("canvas.e.ao_apagar", valor=lig.ao_apagar))
        if lig.ao_apagar != "NO ACTION":
            fk += f" ON DELETE {lig.ao_apagar}"
        cmds.append(fk)
        if self.unica:
            cmds.append(f"ALTER TABLE {t_sql} ADD CONSTRAINT {ident(nome_auto(origem.nome, cols, 'key'))} UNIQUE ({lista})")
        if self.indice:
            cmds.append(f"CREATE INDEX {ident(nome_auto(origem.nome, cols, 'idx'))} ON {t_sql} ({lista})")
        return cmds

    def descricao(self, m: Modelo) -> str:
        origem, destino, cols, dest = self._nomes(m)
        return t("op.ligar", origem=f"{origem.nome}.{cols[0]}", destino=f"{destino.nome}.{dest[0]}")

    def refere(self) -> Set[str]:
        lig = self.ligacao
        ids = {lig.origem, lig.destino} | set(lig.colunas) | set(lig.colunas_destino)
        return ids - self.cria()

    def cria(self) -> Set[str]:
        return {self.ligacao.id} | ({self.nova_coluna.id} if self.nova_coluna else set())


def singular(nome: str) -> str:
    return nome[:-1] if nome.endswith("s") else nome


@dataclass
class LigarNN(Operacao):
    tabela_id: str
    nome: str
    a: str
    b: str
    colunas_ids: Tuple[str, str]
    ligacoes_ids: Tuple[str, str]
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "ligar_nn"

    def _construir(self, m: Modelo) -> Tuple[Tabela, List[Ligacao]]:
        ta, tb = m.tabelas[self.a], m.tabelas[self.b]
        lados = []
        for tab, cid, kid in ((ta, self.colunas_ids[0], self.ligacoes_ids[0]),
                              (tb, self.colunas_ids[1], self.ligacoes_ids[1])):
            pks = tab.pk()
            pk = pks[0] if pks else None
            lados.append((tab, pk, cid, kid))
        # ligar a tabela a ela mesma (seguidores, amigos): `user_a_id` e `user_b_id`, senão as colunas teriam o
        # mesmo nome
        sufixos = ("_a_id", "_b_id") if self.a == self.b else ("_id", "_id")
        colunas = []
        for (tab, pk, cid, _), sufixo in zip(lados, sufixos):
            colunas.append(Coluna(cid, f"{singular(tab.nome)}{sufixo}", pk.tipo if pk else "bigint",
                                  obrigatoria=True, pk=True, estado="novo"))
        nova = Tabela(self.tabela_id, m.esquema, self.nome, colunas, estado="novo")
        ligs = []
        for (tab, pk, cid, kid), col in zip(lados, colunas):
            ligs.append(Ligacao(kid, self.tabela_id, (cid,), tab.id, (pk.id,) if pk else (),
                                nome_auto(self.nome, [col.nome], "fkey"), estado="novo"))
        return nova, ligs

    def aplicar(self, m: Modelo) -> None:
        nova, ligs = self._construir(m)
        m.tabelas[nova.id] = nova
        for k in ligs:
            m.ligacoes[k.id] = k

    def comandos(self, m: Modelo) -> List[str]:
        nova, ligs = self._construir(m)
        # o tipo de cada coluna é o da PK que ela aponta (`_construir`): é a referência aceita
        refs = [m.tabelas[k.destino].coluna(k.colunas_destino[0]).tipo if k.colunas_destino else None for k in ligs]
        cols = [_col_sql(c, referencia=ref) for c, ref in zip(nova.colunas, refs)]
        pk = ", ".join(ident(c.nome) for c in nova.colunas)
        cmds = [f"CREATE TABLE {nome_tabela(nova)} ({', '.join(cols)}, PRIMARY KEY ({pk}))"]
        for k, col in zip(ligs, nova.colunas):
            pai = m.tabelas[k.destino]
            dest = ", ".join(ident(_nome_col(m, pai, i)) for i in k.colunas_destino)
            cmds.append(f"ALTER TABLE {nome_tabela(nova)} ADD CONSTRAINT {ident(k.nome)} FOREIGN KEY "
                        f"({ident(col.nome)}) REFERENCES {nome_tabela(pai)} ({dest})")
        return cmds

    def descricao(self, m: Modelo) -> str:
        if self.a == self.b:
            nova, _ = self._construir(m)
            return t("op.ligar_nn_mesma", a=m.tabelas[self.a].nome, tabela=self.nome,
                     col_a=nova.colunas[0].nome, col_b=nova.colunas[1].nome)
        return t("op.ligar_nn", a=m.tabelas[self.a].nome, b=m.tabelas[self.b].nome, tabela=self.nome)

    def refere(self) -> Set[str]:
        return {self.a, self.b}

    def cria(self) -> Set[str]:
        return {self.tabela_id} | set(self.colunas_ids) | set(self.ligacoes_ids)


@dataclass
class ApagarLigacao(Operacao):
    ligacao: str
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "apagar_ligacao"

    def aplicar(self, m: Modelo) -> None:
        m.ligacoes[self.ligacao].estado = "apagado"

    def comandos(self, m: Modelo) -> List[str]:
        lig = m.ligacoes[self.ligacao]
        return [f"ALTER TABLE {nome_tabela(m.tabelas[lig.origem])} DROP CONSTRAINT {ident(lig.nome)}"]

    def descricao(self, m: Modelo) -> str:
        lig = m.ligacoes[self.ligacao]
        o, d = m.tabelas[lig.origem], m.tabelas[lig.destino]
        return t("op.apagar_ligacao", origem=f"{o.nome}.{_nome_col(m, o, lig.colunas[0])}",
                 destino=f"{d.nome}.{_nome_col(m, d, lig.colunas_destino[0])}")

    def refere(self) -> Set[str]:
        return {self.ligacao}


@dataclass
class ApagarColuna(Operacao):
    tabela: str
    coluna: str
    cascata: bool = False
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "apagar_coluna"

    def aplicar(self, m: Modelo) -> None:
        m.tabelas[self.tabela].coluna(self.coluna).estado = "apagado"
        for lig in m.ligacoes.values():
            if self.coluna in lig.colunas or (self.cascata and self.coluna in lig.colunas_destino):
                lig.estado = "apagado"

    def comandos(self, m: Modelo) -> List[str]:
        tab = m.tabelas[self.tabela]
        fim = " CASCADE" if self.cascata else ""
        return [f"ALTER TABLE {nome_tabela(tab)} DROP COLUMN {ident(_nome_col(m, tab, self.coluna))}{fim}"]

    def descricao(self, m: Modelo) -> str:
        tab = m.tabelas[self.tabela]
        return t("op.apagar_coluna", tabela=tab.nome, coluna=_nome_col(m, tab, self.coluna))

    def refere(self) -> Set[str]:
        return {self.tabela, self.coluna}


@dataclass
class ApagarTabela(Operacao):
    tabela: str
    cascata: bool = False
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "apagar_tabela"

    def aplicar(self, m: Modelo) -> None:
        m.tabelas[self.tabela].estado = "apagado"
        for lig in m.ligacoes.values():
            if lig.origem == self.tabela or (self.cascata and lig.destino == self.tabela):
                lig.estado = "apagado"

    def comandos(self, m: Modelo) -> List[str]:
        fim = " CASCADE" if self.cascata else ""
        return [f"DROP TABLE {nome_tabela(m.tabelas[self.tabela])}{fim}"]

    def descricao(self, m: Modelo) -> str:
        return t("op.apagar_tabela", tabela=m.tabelas[self.tabela].nome)

    def refere(self) -> Set[str]:
        return {self.tabela}


# ── operações de dados ──
# O filtro vem de `filtro.py`, que importa este módulo: por isso o import é feito dentro das funções.

_LIMPEZAS = {"trim": "trim({})", "minusculas": "lower({})", "maiusculas": "upper({})",
             "branco_vazio": "CASE WHEN trim({0}) = '' THEN NULL ELSE {0} END"}
_MUDANCAS = ("valor", "vazio", "coluna") + tuple(_LIMPEZAS)
_MANTER = ("menor", "maior", "recente")


def _coluna_de(tab: Tabela, id: str) -> Coluna:
    c = tab.coluna(id)
    if c is None:
        raise KeyError(id)
    return c


def _onde(f, m: Modelo, tabela_id: str) -> str:
    from . import filtro
    return filtro.where(f, m, tabela_id)


def _refere_filtro(f, tabela_id: str) -> Set[str]:
    from . import filtro
    return filtro.refere(f, tabela_id)


def _descricao_filtro(f, m: Modelo, tabela_id: str) -> str:
    from . import filtro
    return filtro.descricao(f, m, tabela_id) if not f.vazio() else t("op.todas_as_linhas")


def _chaves(tab: Tabela) -> List[str]:
    """As colunas que identificam a linha: a PK, ou `ctid` (o último recurso) numa tabela sem ela."""
    pk = tab.pk()
    if any(c.estado == "apagado" for c in pk):
        raise ValueError(t("op.e.pk_apagada"))
    # `ctid` sozinho se repete entre partições e filhas de herança: a linha é (tableoid, ctid)
    return [ident(c.nome) for c in pk] if pk else ["tableoid", "ctid"]


def _com_filtro(f, onde: str, extra: List[str]) -> str:
    """O WHERE (sem a palavra) com condições nossas e o filtro, que vai entre parênteses se tiver `OR` solto."""
    if onde and len(f.condicoes) > 1 and not (f.texto or "").strip():
        onde = f"({onde})"
    return " AND ".join(extra + ([onde] if onde else []))


@dataclass
class Inserir(Operacao):
    tabela: str
    valores: Dict[str, Optional[str]]         # id da coluna → texto digitado; None = NULL; ausente = o banco preenche
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "inserir"

    def aplicar(self, m: Modelo) -> None:
        pass

    def comandos(self, m: Modelo) -> List[str]:
        from . import filtro
        tab = m.tabelas[self.tabela]
        if not self.valores:
            return [f"INSERT INTO {nome_tabela(tab)} DEFAULT VALUES"]
        cols, vals = [], []
        for i, v in self.valores.items():
            c = _coluna_de(tab, i)
            cols.append(ident(c.nome))
            vals.append("NULL" if v is None else filtro.literal(v, c.tipo))
        return [f"INSERT INTO {nome_tabela(tab)} ({', '.join(cols)}) VALUES ({', '.join(vals)})"]

    def descricao(self, m: Modelo) -> str:
        return t("op.inserir_um" if len(self.valores) == 1 else "op.inserir", tabela=m.tabelas[self.tabela].nome,
                 n=len(self.valores))

    def refere(self) -> Set[str]:
        return {self.tabela} | set(self.valores)


@dataclass
class Mudanca:
    tipo: str                                 # valor, vazio, trim, minusculas, maiusculas, branco_vazio, coluna
    valor: Optional[str] = None
    coluna: Optional[str] = None


@dataclass
class Alterar(Operacao):
    tabela: str
    filtro: "Filtro"
    mudancas: Dict[str, Mudanca]
    vista: Optional[int] = None               # quantas linhas a pessoa viu que seriam pegas
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "alterar"

    def aplicar(self, m: Modelo) -> None:
        pass

    def _expressao(self, tab: Tabela, c: Coluna, mud: Mudanca) -> str:
        from . import filtro
        if mud.tipo == "valor":
            if mud.valor is None:
                raise ValueError(t("op.e.sem_valor", coluna=c.nome))
            return filtro.literal(mud.valor, c.tipo)
        if mud.tipo == "vazio":
            return "NULL"
        if mud.tipo == "coluna":
            if not mud.coluna:
                raise ValueError(t("op.e.sem_coluna", coluna=c.nome))
            return ident(_coluna_de(tab, mud.coluna).nome)
        if mud.tipo in _LIMPEZAS:
            if filtro.familia(c.tipo) != "texto":
                raise ValueError(t("op.e.so_texto", coluna=c.nome))
            return _LIMPEZAS[mud.tipo].format(ident(c.nome))
        raise ValueError(t("op.e.mudanca", mudanca=str(mud.tipo)))

    def sets(self, m: Modelo) -> Dict[str, str]:
        """Nome da coluna (já por `ident`) → a expressão que ela recebe, na ordem das mudanças."""
        tab = m.tabelas[self.tabela]
        if not self.mudancas:
            raise ValueError(t("op.e.sem_mudancas"))
        return {ident(c.nome): self._expressao(tab, c, mud)
                for c, mud in ((_coluna_de(tab, i), mud) for i, mud in self.mudancas.items())}

    def onde(self, m: Modelo) -> str:
        """O WHERE exato do UPDATE (sem a palavra): o filtro e a condição que pula quem não muda. A contagem que a
        pessoa vê sai deste mesmo texto."""
        quem = " OR ".join(f"{c} IS DISTINCT FROM {e}" for c, e in self.sets(m).items())   # só entra quem muda
        onde = _com_filtro(self.filtro, _onde(self.filtro, m, self.tabela), [])
        return f"{onde} AND ({quem})" if onde else quem

    def comandos(self, m: Modelo) -> List[str]:
        sets = ", ".join(f"{c} = {e}" for c, e in self.sets(m).items())
        return [f"UPDATE {nome_tabela(m.tabelas[self.tabela])} SET {sets} WHERE {self.onde(m)}"]

    def descricao(self, m: Modelo) -> str:
        tab = m.tabelas[self.tabela]
        return t("op.alterar", tabela=tab.nome, colunas=", ".join(_nome_col(m, tab, i) for i in self.mudancas),
                 filtro=_descricao_filtro(self.filtro, m, self.tabela))

    def refere(self) -> Set[str]:
        ids = {self.tabela} | set(self.mudancas) | _refere_filtro(self.filtro, self.tabela)
        return ids | {x.coluna for x in self.mudancas.values() if x.coluna}


@dataclass
class Apagar(Operacao):
    tabela: str
    filtro: "Filtro"
    vista: Optional[int] = None
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "apagar"

    def aplicar(self, m: Modelo) -> None:
        pass

    def onde(self, m: Modelo) -> str:
        """O WHERE exato do DELETE (sem a palavra); "" apaga todas."""
        return _onde(self.filtro, m, self.tabela)

    def comandos(self, m: Modelo) -> List[str]:
        onde = self.onde(m)
        return [f"DELETE FROM {nome_tabela(m.tabelas[self.tabela])}" + (f" WHERE {onde}" if onde else "")]

    def descricao(self, m: Modelo) -> str:
        return t("op.apagar_linhas", tabela=m.tabelas[self.tabela].nome,
                 filtro=_descricao_filtro(self.filtro, m, self.tabela))

    def refere(self) -> Set[str]:
        return _refere_filtro(self.filtro, self.tabela)


@dataclass
class ApagarDuplicatas(Operacao):
    tabela: str
    filtro: "Filtro"
    por: Tuple[str, ...]
    normalizar: bool = False                  # ignora maiúsculas e espaços nas colunas de texto
    manter: str = "menor"                     # menor | maior (chave) | recente (pela coluna `ordem`)
    ordem: Optional[str] = None
    n: int = 1                                # quantas ficam por grupo
    vista: Optional[int] = None
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "apagar_duplicatas"

    def aplicar(self, m: Modelo) -> None:
        pass

    def _partes(self, m: Modelo) -> Tuple[Tabela, List[str], List[str], List[str], List[str], str]:
        """(tabela, expressões do grupo, condições de comparável, chaves, ordem, WHERE do filtro)."""
        from . import filtro
        tab = m.tabelas[self.tabela]
        if not self.por:
            raise ValueError(t("op.e.sem_por"))
        if self.manter not in _MANTER:
            raise ValueError(t("op.e.manter", valor=str(self.manter)))
        if isinstance(self.n, bool) or not isinstance(self.n, int) or self.n < 1:
            raise ValueError(t("op.e.n", valor=str(self.n)))
        grupo, vazios = [], []
        for i in self.por:
            c = _coluna_de(tab, i)
            nome = ident(c.nome)
            vazios.append(f"{nome} IS NOT NULL")                   # vazio não é repetido: fica fora da comparação
            if self.normalizar and filtro.familia(c.tipo) == "texto":
                vazios.append(f"trim({nome}) <> ''")
                grupo.append(f"lower(trim({nome}))")
            else:
                grupo.append(nome)
        chaves = _chaves(tab)
        if self.manter == "recente":
            if not self.ordem:
                raise ValueError(t("op.e.sem_ordem"))
            ordem = [ident(_coluna_de(tab, self.ordem).nome) + " DESC NULLS LAST"] + chaves
        elif self.manter == "maior":
            ordem = [k + " DESC" for k in chaves]
        else:
            ordem = chaves
        return tab, grupo, vazios, chaves, ordem, _onde(self.filtro, m, self.tabela)

    def por_sql(self, m: Modelo) -> List[str]:
        """As expressões que definem o grupo (o PARTITION BY)."""
        return self._partes(m)[1]

    def onde_comparadas(self, m: Modelo) -> str:
        """As linhas filtradas que entram na comparação (sem vazio nas colunas do grupo)."""
        _, _, vazios, _, _, filtrado = self._partes(m)
        return _com_filtro(self.filtro, filtrado, vazios)

    def onde_fora(self, m: Modelo) -> str:
        """As linhas filtradas que ficam fora da comparação por terem vazio numa coluna do grupo."""
        _, _, vazios, _, _, filtrado = self._partes(m)
        return _com_filtro(self.filtro, filtrado, ["NOT (" + " AND ".join(vazios) + ")"])

    def onde(self, m: Modelo) -> str:
        """O WHERE exato do DELETE (sem a palavra): as linhas que sairiam."""
        tab, grupo, _, chaves, ordem, _ = self._partes(m)
        lista = ", ".join(chaves)
        return (f"({lista}) IN (SELECT {lista} FROM (SELECT {lista}, "
                f"row_number() OVER (PARTITION BY {', '.join(grupo)} ORDER BY {', '.join(ordem)}) AS _ratt_rn "
                f"FROM {nome_tabela(tab)} WHERE {self.onde_comparadas(m)}) AS d WHERE d._ratt_rn > {self.n})")

    def comandos(self, m: Modelo) -> List[str]:
        return [f"DELETE FROM {nome_tabela(m.tabelas[self.tabela])} WHERE {self.onde(m)}"]

    def descricao(self, m: Modelo) -> str:
        tab = m.tabelas[self.tabela]
        return t("op.apagar_duplicatas", tabela=tab.nome, colunas=", ".join(_nome_col(m, tab, i) for i in self.por),
                 filtro=_descricao_filtro(self.filtro, m, self.tabela))

    def refere(self) -> Set[str]:
        return {self.tabela} | set(self.por) | ({self.ordem} if self.manter == "recente" and self.ordem else set()) \
            | _refere_filtro(self.filtro, self.tabela)


# ── o rascunho ──

def _existe(m: Modelo, id: str) -> bool:
    """O id existe no modelo e nem ele nem a tabela dele estão apagados."""
    achado = _achar(m, id)
    if achado is None:
        return False
    obj, dona = achado
    if obj.estado == "apagado":
        return False
    return dona is None or dona.estado != "apagado"


def comandos_de(op: Operacao, m: Modelo) -> List[str]:
    """Os comandos da operação, conferidos: cada um tem de ser exatamente UM comando SQL. Um padrão como
    `1); DROP TABLE users; --` ou um comentário que engoliria o `;` viraria mais de um (ou nenhum fim) no `.sql`."""
    from .rodar import dividir
    cmds = op.comandos(m)
    for cmd in cmds:
        partes = dividir(cmd + ";")
        if len(partes) != 1 or partes[0].meta or partes[0].texto != cmd:
            raise ValueError(t("canvas.e.um_comando"))
    return cmds


def _nome_de(id: str, *modelos: Modelo) -> str:
    """O nome de um id para uma mensagem (o primeiro modelo que o conhece); o próprio id se nenhum conhece."""
    for m in modelos:
        achado = _achar(m, id)
        if achado is not None and getattr(achado[0], "nome", None):
            return achado[0].nome
    return id


def _nome_na_base(base: Modelo, id: str) -> Optional[str]:
    """O nome que o id tem na base (tabela, coluna ou ligação); None se ela não o conhece."""
    if id in base.tabelas:
        return base.tabelas[id].nome
    if id in base.ligacoes:
        return base.ligacoes[id].nome
    tab = base.tabelas.get(id.rsplit(".", 1)[0])     # coluna do catálogo: `t{oid}.c{n}` mora em `t{oid}`
    c = tab.coluna(id) if tab is not None else None
    if c is None:
        achado = _achar(base, id)
        c = achado[0] if achado is not None and isinstance(achado[0], Coluna) else None
    return c.nome if c is not None else None


def _mesmos_nomes(base: Modelo, op: Operacao) -> bool:
    """O que a operação refere na base ainda tem o nome de quando ela entrou. Compara com a BASE, não com a
    vista: uma renomeação feita no próprio rascunho não muda a base e não invalida nada."""
    for i, nome in op.nomes.items():
        atual = _nome_na_base(base, i)
        if atual is not None and atual != nome:
            return False
    return True


def _valida(m: Modelo, op: Operacao, base: Optional[Modelo] = None) -> bool:
    return all(_existe(m, i) for i in op.refere()) and (base is None or _mesmos_nomes(base, op))


# ── de e para o disco ──

# Campos que não são texto/número/booleano: como voltam de um dict.
_CAMPOS = {
    CriarTabela: {"tabela": "Tabela"},
    AdicionarColuna: {"coluna": "Coluna"},
    Ligar: {"ligacao": "Ligacao", "nova_coluna": "Coluna?"},
    LigarNN: {"colunas_ids": "tupla", "ligacoes_ids": "tupla"},
    Alterar: {"filtro": "Filtro", "mudancas": "Mudancas"},
    Apagar: {"filtro": "Filtro"},
    ApagarDuplicatas: {"filtro": "Filtro", "por": "tupla"},
}
_TIPOS_OP = {cls.tipo: cls for cls in (CriarTabela, AdicionarColuna, Renomear, Obrigatoria, Unica, Ligar,
                                       LigarNN, ApagarLigacao, ApagarColuna, ApagarTabela, Inserir, Alterar,
                                       Apagar, ApagarDuplicatas)}


def _op_para_dict(op: Operacao) -> dict:
    from . import filtro
    d = {"tipo": op.tipo}
    d.update(asdict(op))
    if hasattr(op, "filtro"):             # o filtro tem a forma própria do JSON dele
        d["filtro"] = filtro.para_dict(op.filtro)
    return d


def _op_de_dict(d: dict) -> Operacao:
    cls = _TIPOS_OP[d["tipo"]]
    campos = {k: v for k, v in d.items() if k != "tipo"}
    for nome, como in _CAMPOS.get(cls, {}).items():
        v = campos.get(nome)
        if v is None:
            continue
        if como == "Tabela":
            v = dict(v)
            v["colunas"] = [Coluna(**c) for c in v["colunas"]]
            campos[nome] = Tabela(**v)
        elif como == "Filtro":
            from . import filtro
            campos[nome] = filtro.de_dict(v)
        elif como == "Mudancas":
            if not isinstance(v, dict) or not all(isinstance(x, dict) for x in v.values()):
                raise TypeError("mudanças inválidas")
            campos[nome] = {k: Mudanca(**x) for k, x in v.items()}
        elif como.startswith("Coluna"):
            campos[nome] = Coluna(**v)
        elif como == "Ligacao":
            v = dict(v)
            v["colunas"], v["colunas_destino"] = tuple(v["colunas"]), tuple(v["colunas_destino"])
            campos[nome] = Ligacao(**v)
        else:
            campos[nome] = tuple(v)
    return cls(**campos)


class RascunhoCorrompido(ValueError):
    """O rascunho lido do disco não tem a forma esperada (editado à mão ou estragado)."""


# o que um JSON editado à mão pode provocar dentro de uma operação: nunca passa daqui
_ERROS_OP = (KeyError, ValueError, TypeError, AttributeError, IndexError)


def _conferir(op: Operacao) -> None:
    """Os ids que a operação usa têm de ser textos: uma lista ou um objeto no lugar de um id quebraria
    cada conta do rascunho (conjuntos, dependências), não só esta operação."""
    for i in op.refere() | op.cria():
        if not isinstance(i, str):
            raise TypeError(f"id inválido: {i!r}")
    if isinstance(op, Inserir) and (not isinstance(op.valores, dict) or not all(
            v is None or isinstance(v, str) for v in op.valores.values())):
        raise TypeError(f"valores inválidos: {op.valores!r}")
    if hasattr(op, "vista") and op.vista is not None and (
            isinstance(op.vista, bool) or not isinstance(op.vista, int) or op.vista < 0):
        raise TypeError(f"vista inválida: {op.vista!r}")
    if isinstance(op, ApagarDuplicatas):
        if not isinstance(op.normalizar, bool) or op.manter not in _MANTER:
            raise TypeError("normalizar ou manter inválido")
        if isinstance(op.n, bool) or not isinstance(op.n, int) or op.n < 1:
            raise TypeError(f"n inválido: {op.n!r}")
        if op.manter == "recente" and not isinstance(op.ordem, str):
            raise TypeError("falta a coluna de ordem")
    if isinstance(op, Alterar) and not all(isinstance(x.tipo, str) and (x.valor is None or isinstance(x.valor, str))
                                           for x in op.mudancas.values()):
        raise TypeError("mudanças inválidas")
    if not isinstance(op.nomes, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in op.nomes.items()):
        raise TypeError(f"nomes inválidos: {op.nomes!r}")


class Rascunho:
    """A base (o que está no banco) mais a lista de operações que a pessoa fez em cima dela."""

    def __init__(self, base: Modelo, operacoes: Optional[List[Operacao]] = None, proximo: int = 1):
        self.base = base
        self.operacoes: List[Operacao] = list(operacoes or [])
        self.proximo = proximo

    def novo_id(self, tipo: str) -> str:
        """Id de algo criado no rascunho: `novo-t3`, `novo-c4`, `novo-k5` (o contador nunca volta atrás)."""
        i = f"novo-{tipo}{self.proximo}"
        self.proximo += 1
        return i

    def acrescentar(self, op: Operacao) -> None:
        v = self.vista()
        for i in sorted(op.refere()):
            if not _existe(v, i):
                raise ValueError(t("canvas.e.nao_existe", nome=_nome_de(i, v, self.base)))
        try:
            comandos_de(op, v)      # um nome vazio, um id solto ou SQL a mais falha aqui, não no meio do export
        except KeyError as e:
            i = e.args[0] if e.args and isinstance(e.args[0], str) else str(e)
            raise ValueError(t("canvas.e.nao_existe", nome=_nome_de(i, v, self.base))) from e
        op.nomes = {i: n for i in sorted(op.refere()) for n in [_nome_na_base(self.base, i)] if n is not None}
        self.operacoes.append(op)

    def desfazer(self) -> Optional[Operacao]:
        return self.operacoes.pop() if self.operacoes else None

    def _percorrer(self) -> Tuple[Modelo, List[Tuple[Operacao, bool, str, List[str]]]]:
        """Aplica as operações uma a uma na cópia da base. Cada passo: (operação, válida, descrição, comandos).
        Inválida é a que refere id que não existe (ou já foi apagado) *naquele ponto* ou cujo SQL não se gera."""
        m = self.base.copia()
        passos = []
        for op in self.operacoes:
            desc, cmds = op.tipo, []
            try:
                valida = _valida(m, op, self.base)
                if valida:
                    cmds = comandos_de(op, m)
                    desc = " ".join(op.descricao(m).splitlines())   # uma linha só: vai para um comentário SQL
                    op.aplicar(m)
            except _ERROS_OP:
                valida, cmds = False, []
            passos.append((op, valida, desc, cmds))
        return m, passos

    def vista(self) -> Modelo:
        """A base copiada com as operações válidas aplicadas, itens tocados marcados."""
        return self._percorrer()[0]

    def passos(self) -> List[Tuple[Operacao, bool, str, List[str]]]:
        """Para cada operação: (operação, válida, descrição, comandos). O painel do SQL desenha daqui."""
        return self._percorrer()[1]

    @property
    def invalidas(self) -> Set[int]:
        """Índices das operações que não valem sobre a base de agora; seguem na lista, fora do resto."""
        return {i for i, (_, valida, _, _) in enumerate(self._percorrer()[1]) if not valida}

    def rebase(self, base: Modelo) -> Set[int]:
        """Troca a base (o banco mudou) sem perder nenhuma operação e devolve as que deixaram de valer."""
        self.base = base
        return self.invalidas

    def dependentes(self, i: int) -> List[int]:
        """Índices posteriores que, direta ou transitivamente, referem ids criados pela operação `i`."""
        criados = set(self.operacoes[i].cria())
        achados = []
        for j in range(i + 1, len(self.operacoes)):
            op = self.operacoes[j]
            if op.refere() & criados:
                achados.append(j)
                criados |= op.cria()
        return achados

    def tirar(self, indices: Iterable[int]) -> None:
        fora = set(indices)
        self.operacoes = [op for i, op in enumerate(self.operacoes) if i not in fora]

    def para_dict(self) -> dict:
        return {"operacoes": [_op_para_dict(op) for op in self.operacoes], "proximo": self.proximo}

    @classmethod
    def de_dict(cls, d: dict, base: Modelo) -> "Rascunho":
        try:
            if not isinstance(d, dict) or not isinstance(d.get("operacoes", []), list):
                raise TypeError("esperava um objeto com a lista \"operacoes\"")
            ops = []
            for n, o in enumerate(d.get("operacoes", []), 1):
                try:
                    op = _op_de_dict(o)
                    _conferir(op)
                    ops.append(op)
                except _ERROS_OP as e:
                    raise RascunhoCorrompido(f"operação {n} ilegível: {type(e).__name__}: {e}") from e
            r = cls(base, ops, int(d.get("proximo", 1)))
            r.rebase(base)
            for i in range(len(r.operacoes)):
                r.dependentes(i)
        except RascunhoCorrompido:
            raise
        except _ERROS_OP as e:
            raise RascunhoCorrompido(f"rascunho ilegível: {type(e).__name__}: {e}") from e
        return r

    def modelo(self) -> Modelo:
        """A vista sem o que está apagado: como o banco ficaria."""
        v = self.vista()
        tabelas = {}
        for i, tab in v.tabelas.items():
            if tab.estado == "apagado":
                continue
            tab.colunas = [c for c in tab.colunas if c.estado != "apagado"]
            tabelas[i] = tab
        ligacoes = {i: k for i, k in v.ligacoes.items()
                    if k.estado != "apagado" and k.origem in tabelas and k.destino in tabelas}
        return Modelo(v.esquema, tabelas, ligacoes)

    def _linhas(self, cabecalho: str) -> Tuple[List[str], List[int]]:
        """As linhas do texto SQL e a linha (1-based) do comentário de cada operação."""
        linhas: List[str] = []
        if cabecalho:
            # uma linha só e sempre comentário: o cabeçalho vem de nomes do banco, não pode virar comando
            cab = " ".join(cabecalho.splitlines())
            linhas.append(cab if cab.startswith("--") else "-- " + cab)
        marcas: List[int] = []
        vazio = True
        for n, (op, valida, desc, cmds) in enumerate(self._percorrer()[1], 1):
            if not valida:
                marcas.append(0)
                continue
            if not vazio:
                linhas.append("")
            vazio = False
            marcas.append(len(linhas) + 1)
            linhas.append(f"-- {n}. {desc}")
            for cmd in cmds:
                linhas.extend((cmd + ";").split("\n"))
        return linhas, marcas

    def sql_texto(self, cabecalho: str = "") -> str:
        linhas, _ = self._linhas(cabecalho)
        return "\n".join(linhas) + ("\n" if linhas else "")

    def linhas_das_operacoes(self, cabecalho: str = "") -> List[int]:
        return self._linhas(cabecalho)[1]

    def operacao_da_linha(self, linha: int, cabecalho: str = "") -> int:
        """Índice (0-based) da operação a que a linha do texto pertence; -1 se não há operação válida.
        `cabecalho` tem de ser o mesmo de `sql_texto`: ele ocupa uma linha e desloca todas as outras."""
        marcas = self.linhas_das_operacoes(cabecalho)
        idx = -1
        for i, ln in enumerate(marcas):
            if ln and ln <= linha:
                idx = i
        if idx < 0 and self.operacoes:
            return next((i for i, ln in enumerate(marcas) if ln), -1)
        return idx
