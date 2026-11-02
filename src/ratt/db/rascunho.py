"""O rascunho do canvas: cada edição vira uma operação, e a lista de operações vira SQL legível.

Tudo é puro (sem banco, sem tela). O rascunho nunca mexe no modelo que veio do banco: a `vista()` é uma
cópia dele com as operações aplicadas uma a uma, e os itens tocados levam uma marca em `estado`
("novo", "mudado", "apagado") para o desenho poder pintá-los. O SQL de cada operação é gerado com os
nomes que valem *antes* dela, por isso uma renomeação vale para as operações seguintes.
"""
from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass
from typing import ClassVar, Iterable, List, Optional, Set, Tuple

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
_TIPO = re.compile(r"^[a-z][a-z0-9_ ]*(\(\d+(,\s*\d+)?\))?(\[\])?$")

_AO_APAGAR = frozenset({"NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"})

TIPOS = ("bigint", "integer", "numeric", "text", "boolean", "date", "timestamptz", "uuid", "jsonb")


def ident(nome: str) -> str:
    """O nome como o PostgreSQL o exige no SQL: sem aspas só se casar `^[a-z_][a-z0-9_]*$` e não for
    palavra reservada; senão entre aspas, com `"` dobrado."""
    if nome == "":
        raise ValueError("nome vazio")
    if _SEGURO.fullmatch(nome) and nome not in _RESERVADAS:
        return nome
    return '"' + nome.replace('"', '""') + '"'


def nome_tabela(tab: Tabela) -> str:
    """`esquema.tabela`, sempre — cada parte por `ident`. Sem o schema, o nome seguiria o `search_path`
    (`"$user", public`): um `DROP TABLE users` pensado para `public.users` apagaria `jared.users` se ela existir."""
    return ident(tab.esquema) + "." + ident(tab.nome)


def tipo_valido(tipo: str) -> bool:
    """Um dos tipos da lista ou texto livre que tenha cara de tipo (`varchar(255)`, `int[]`...)."""
    return tipo in TIPOS or bool(_TIPO.fullmatch(tipo))


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


def _col_sql(c: Coluna, sem_not_null: bool = False) -> str:
    if not tipo_valido(c.tipo):
        raise ValueError(f"tipo inválido: {c.tipo!r}")
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
            cmds.append(f"ALTER TABLE {t_sql} ADD COLUMN {_col_sql(self.nova_coluna)}")
        fk = (f"ALTER TABLE {t_sql} ADD CONSTRAINT {ident(self._nome_fk(origem, cols))} FOREIGN KEY ({lista}) "
              f"REFERENCES {nome_tabela(destino)} ({', '.join(ident(c) for c in dest)})")
        if lig.ao_apagar not in _AO_APAGAR:
            raise ValueError(f"ao_apagar inválido: {lig.ao_apagar!r}")
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
    tipo: ClassVar[str] = "ligar_nn"

    def _construir(self, m: Modelo) -> Tuple[Tabela, List[Ligacao]]:
        ta, tb = m.tabelas[self.a], m.tabelas[self.b]
        lados = []
        for tab, cid, kid in ((ta, self.colunas_ids[0], self.ligacoes_ids[0]),
                              (tb, self.colunas_ids[1], self.ligacoes_ids[1])):
            pks = tab.pk()
            pk = pks[0] if pks else None
            lados.append((tab, pk, cid, kid))
        colunas = []
        for tab, pk, cid, _ in lados:
            colunas.append(Coluna(cid, f"{singular(tab.nome)}_id", pk.tipo if pk else "bigint",
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
        cols = [_col_sql(c) for c in nova.colunas]
        pk = ", ".join(ident(c.nome) for c in nova.colunas)
        cmds = [f"CREATE TABLE {nome_tabela(nova)} ({', '.join(cols)}, PRIMARY KEY ({pk}))"]
        for k, col in zip(ligs, nova.colunas):
            pai = m.tabelas[k.destino]
            dest = ", ".join(ident(_nome_col(m, pai, i)) for i in k.colunas_destino)
            cmds.append(f"ALTER TABLE {nome_tabela(nova)} ADD CONSTRAINT {ident(k.nome)} FOREIGN KEY "
                        f"({ident(col.nome)}) REFERENCES {nome_tabela(pai)} ({dest})")
        return cmds

    def descricao(self, m: Modelo) -> str:
        return t("op.ligar_nn", a=m.tabelas[self.a].nome, b=m.tabelas[self.b].nome, tabela=self.nome)

    def refere(self) -> Set[str]:
        return {self.a, self.b}

    def cria(self) -> Set[str]:
        return {self.tabela_id} | set(self.colunas_ids) | set(self.ligacoes_ids)


@dataclass
class ApagarLigacao(Operacao):
    ligacao: str
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


def _valida(m: Modelo, op: Operacao) -> bool:
    return all(_existe(m, i) for i in op.refere())


# ── de e para o disco ──

# Campos que não são texto/número/booleano: como voltam de um dict.
_CAMPOS = {
    CriarTabela: {"tabela": "Tabela"},
    AdicionarColuna: {"coluna": "Coluna"},
    Ligar: {"ligacao": "Ligacao", "nova_coluna": "Coluna?"},
    LigarNN: {"colunas_ids": "tupla", "ligacoes_ids": "tupla"},
}
_TIPOS_OP = {cls.tipo: cls for cls in (CriarTabela, AdicionarColuna, Renomear, Obrigatoria, Unica, Ligar,
                                       LigarNN, ApagarLigacao, ApagarColuna, ApagarTabela)}


def _op_para_dict(op: Operacao) -> dict:
    d = {"tipo": op.tipo}
    d.update(asdict(op))
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
        for i in op.refere():
            if not _existe(v, i):
                raise ValueError(f"{i}: não existe ou foi apagado")
        try:
            op.comandos(v)          # um nome vazio ou id solto falha aqui, não no meio do export
        except KeyError as e:
            raise ValueError(f"{e}: não existe") from e
        self.operacoes.append(op)

    def desfazer(self) -> Optional[Operacao]:
        return self.operacoes.pop() if self.operacoes else None

    def _percorrer(self) -> Tuple[Modelo, List[Tuple[Operacao, bool, str, List[str]]]]:
        """Aplica as operações uma a uma na cópia da base. Cada passo: (operação, válida, descrição, comandos).
        Inválida é a que refere id que não existe (ou já foi apagado) *naquele ponto* ou cujo SQL não se gera."""
        m = self.base.copia()
        passos = []
        for op in self.operacoes:
            valida, desc, cmds = _valida(m, op), op.tipo, []
            if valida:
                try:
                    cmds = op.comandos(m)
                    desc = " ".join(op.descricao(m).splitlines())   # uma linha só: vai para um comentário SQL
                    op.aplicar(m)
                except (KeyError, ValueError, TypeError, AttributeError):
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
                    ops.append(_op_de_dict(o))
                except (KeyError, TypeError, AttributeError, ValueError) as e:
                    raise RascunhoCorrompido(f"operação {n} ilegível: {type(e).__name__}: {e}") from e
            r = cls(base, ops, int(d.get("proximo", 1)))
        except RascunhoCorrompido:
            raise
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            raise RascunhoCorrompido(f"rascunho ilegível: {type(e).__name__}: {e}") from e
        r.rebase(base)
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
