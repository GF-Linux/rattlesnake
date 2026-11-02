"""O modelo das tabelas e ligações que o canvas desenha e edita — dados puros, sem banco e sem tela.

O catálogo preenche, o rascunho altera uma cópia, o desenho lê. Tudo se liga por ids (não por nomes),
para que renomear uma tabela ou coluna no rascunho não quebre as ligações. Ids do catálogo:
tabela `t{oid}`, coluna `t{oid}.c{attnum}`, ligação `k{oid_da_restricao}`; os criados no rascunho
são `novo-t{n}`, `novo-c{n}` e `novo-k{n}`.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Coluna:
    id: str
    nome: str
    tipo: str                           # como o format_type do PostgreSQL escreve
    obrigatoria: bool = False           # NOT NULL
    padrao: Optional[str] = None
    identidade: bool = False
    pk: bool = False
    unica: bool = False                 # UNIQUE de uma coluna só
    nome_unica: Optional[str] = None    # nome dessa restrição, para poder apagá-la
    indexada: bool = False              # primeira coluna de algum índice
    gerada: bool = False                # GENERATED ALWAYS AS (…) STORED: o banco calcula, ninguém insere
    estado: str = ""                    # "", "novo", "apagado" ou "mudado" (a vista do rascunho)


@dataclass
class Tabela:
    id: str
    esquema: str
    nome: str
    colunas: List[Coluna] = field(default_factory=list)
    linhas: Optional[int] = None        # estimativa do PostgreSQL
    particoes: int = 0
    nome_pk: Optional[str] = None
    estado: str = ""

    def coluna(self, id: str) -> Optional[Coluna]:
        for c in self.colunas:
            if c.id == id:
                return c
        return None

    def pk(self) -> List[Coluna]:
        """As colunas da chave primária, na ordem em que a tabela as declara."""
        return [c for c in self.colunas if c.pk]


@dataclass
class Ligacao:
    id: str
    origem: str                         # id da tabela que tem a chave estrangeira
    colunas: Tuple[str, ...]            # ids das colunas de origem
    destino: str                        # id da tabela referenciada
    colunas_destino: Tuple[str, ...]
    nome: str
    ao_apagar: str = "NO ACTION"
    estado: str = ""


@dataclass
class Modelo:
    esquema: str
    tabelas: Dict[str, Tabela] = field(default_factory=dict)
    ligacoes: Dict[str, Ligacao] = field(default_factory=dict)

    def copia(self) -> "Modelo":
        """Cópia profunda: o rascunho mexe nela sem tocar no que veio do banco."""
        return copy.deepcopy(self)

    def por_nome(self, nome: str) -> Optional[Tabela]:
        for t in self.tabelas.values():
            if t.nome == nome:
                return t
        return None


def cardinalidade(m: Modelo, lig: Ligacao) -> str:
    """"1:1" se a origem só pode ter uma linha por linha do destino (as colunas são a PK inteira da
    origem, ou uma coluna só com UNIQUE); senão "1:N"."""
    origem = m.tabelas[lig.origem]
    if set(lig.colunas) == {c.id for c in origem.pk()} and lig.colunas:
        return "1:1"
    if len(lig.colunas) == 1:
        c = origem.coluna(lig.colunas[0])
        if c is not None and c.unica:
            return "1:1"
    return "1:N"


def opcional(m: Modelo, lig: Ligacao) -> bool:
    """A ligação é opcional se alguma coluna de origem aceita NULL."""
    origem = m.tabelas[lig.origem]
    return any(c is not None and not c.obrigatoria for c in (origem.coluna(i) for i in lig.colunas))


def juncao(m: Modelo, tabela_id: str) -> bool:
    """Tabela de junção (muitos-para-muitos): PK de exatamente duas colunas, e cada uma delas é a única
    coluna de origem de alguma ligação que sai da tabela."""
    pk = m.tabelas[tabela_id].pk()
    if len(pk) != 2:
        return False
    sozinhas = {lig.colunas[0] for lig in m.ligacoes.values() if lig.origem == tabela_id and len(lig.colunas) == 1}
    return all(c.id in sozinhas for c in pk)


def chegando(m: Modelo) -> Dict[str, int]:
    """Id da tabela -> quantas ligações chegam nela (quem é muito referenciada é o centro do desenho)."""
    n = {i: 0 for i in m.tabelas}
    for lig in m.ligacoes.values():
        if lig.destino in n:
            n[lig.destino] += 1
    return n


def recortar(m: Modelo, centro: str, saltos: int = 1) -> Modelo:
    """Só as tabelas a até `saltos` ligações do centro (nos dois sentidos) e as ligações entre elas."""
    dentro = {centro}
    borda = {centro}
    for _ in range(saltos):
        nova = set()
        for lig in m.ligacoes.values():
            if lig.origem in borda and lig.destino not in dentro:
                nova.add(lig.destino)
            if lig.destino in borda and lig.origem not in dentro:
                nova.add(lig.origem)
        dentro |= nova
        borda = nova
    return Modelo(
        m.esquema,
        {i: copy.deepcopy(t) for i, t in m.tabelas.items() if i in dentro},
        {i: copy.deepcopy(k) for i, k in m.ligacoes.items() if k.origem in dentro and k.destino in dentro},
    )
