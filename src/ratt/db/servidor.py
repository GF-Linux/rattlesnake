"""O modelo do servidor — papéis (usuários e grupos), bancos, quem é membro de quem e quem entra onde. Puro: sem
banco e sem tela.

Papéis são do servidor inteiro, não de um banco. Tudo se liga por ids estáveis (não por nomes), como no modelo das
tabelas: papel `r{oid}` (do catálogo) ou `novo-p{n}` (criado no rascunho), banco `d{oid}`. O catálogo preenche
(`catalogo_servidor.modelo_servidor`), o rascunho do servidor altera uma cópia, as telas leem.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class Papel:
    id: str
    nome: str
    login: bool                         # LOGIN: entra (um usuário); sem ele, é um grupo
    superusuario: bool
    cria_bancos: bool                   # CREATEDB
    cria_papeis: bool                   # CREATEROLE
    membro_de: Tuple[str, ...] = ()     # ids dos grupos de que é membro direto
    estado: str = ""                    # "", "novo", "apagado" ou "mudado" (a vista do rascunho)
    nao_herda: Tuple[str, ...] = ()     # dos `membro_de`, os que NÃO passam os privilégios do grupo: papel NOINHERIT
                                        # (até o 15) ou pertença com INHERIT FALSE (16+). O padrão: tudo herda
    sem_set: Tuple[str, ...] = ()       # dos `membro_de`, os sem SET (16+: não pode `SET ROLE` para o grupo); só se lê
    herda: bool = True                  # rolinherit (16+: só o padrão das pertenças novas)
    sistema: bool = False               # papel do próprio PostgreSQL (`pg_*`): conta no acesso, as telas escondem
    concedido_por: Dict[str, Dict[str, Tuple[bool, bool]]] = field(default_factory=dict)
                                        # grupo → {quem deu a pertença: (INHERIT, SET) dessa linha} — 16+: uma linha
                                        # de `pg_auth_members` por concedente (`grantor`). `nao_herda`/`sem_set` são
                                        # o resumo delas (herda se alguma herda). Sem entrada: não se sabe (até o
                                        # 15, ou dada no rascunho) e a pertença não cai junto com ninguém


@dataclass
class Banco:
    id: str
    nome: str
    dono: str                           # id do papel dono
    aceita_conexao: bool                # datallowconn
    acesso_direto: Tuple[str, ...]      # ids dos papéis com CONNECT dado a eles no datacl (o dono costuma estar)
    publico: bool                       # PUBLIC tem CONNECT (o padrão do PostgreSQL)
    estado: str = ""
    template: bool = False              # datistemplate (template1, template0)
    concedido_por: Dict[str, Dict[str, bool]] = field(default_factory=dict)
                                        # quem tem o CONNECT (id ou "PUBLIC") → {quem o deu (o grantor no `datacl`;
                                        # pode haver vários): com a opção de conceder}. Sem entrada: dado pelo dono


@dataclass
class ModeloServidor:
    papeis: Dict[str, Papel] = field(default_factory=dict)
    bancos: Dict[str, Banco] = field(default_factory=dict)
    eu: str = ""                        # id do papel da conexão: o current_user (o ratt nunca faz SET ROLE)
    versao: int = 0                     # server_version_num (180000 = 18)
    banco_atual: Optional[str] = None   # id do banco desta conexão: as travas olham se `eu` continua entrando nele

    def copia(self) -> "ModeloServidor":
        """Cópia profunda: o rascunho mexe nela sem tocar no que veio do servidor."""
        return copy.deepcopy(self)

    def papel_por_nome(self, nome: str) -> Optional[Papel]:
        """O papel de nome exatamente `nome` (nomes de papel diferenciam maiúsculas)."""
        return next((p for p in self.papeis.values() if p.nome == nome), None)

    def banco_por_nome(self, nome: str) -> Optional[Banco]:
        return next((b for b in self.bancos.values() if b.nome == nome), None)


def grupos(m: ModeloServidor, papel_id: str, herdados: bool = False) -> Set[str]:
    """Todos os grupos de que `papel_id` é membro, direto ou por outro grupo (transitivo; um id que não está no
    modelo é ignorado; um ciclo não trava). `herdados`: só pelas pertenças que passam os privilégios (fora as de
    `nao_herda`, em cada passo da corrente) — os grupos cujos privilégios o papel tem sem `SET ROLE`."""
    vistos: Set[str] = set()
    borda = [papel_id]
    while borda:
        papel = m.papeis.get(borda.pop())
        if papel is None:
            continue
        for g in papel.membro_de:
            if herdados and g in papel.nao_herda:
                continue
            if g not in vistos and g != papel_id and g in m.papeis:
                vistos.add(g)
                borda.append(g)
    return vistos


def acesso(m: ModeloServidor, papel_id: str, banco_id: str) -> str:
    """Como `papel_id` entra em `banco_id` — o mesmo que o `has_database_privilege(…, 'CONNECT')` do PostgreSQL:
    "super" (superusuário entra em qualquer um), "dono" (o dono, com o CONNECT dele), "direto" (CONNECT dado a ele),
    "herdado" (pelo PUBLIC, ou por um grupo com CONNECT de que é membro — mesmo por outro grupo — por pertenças que
    herdam) ou "nenhum". Ser dono não dá CONNECT por si: o dono entra pela entrada dele no `datacl` (no padrão, ele
    está lá), e um dono que tirou de si o CONNECT não entra por ser dono. Um banco que não aceita conexão
    (`aceita_conexao=False`) dá o mesmo valor: o das permissões; quem mostra marca à parte que ninguém entra agora."""
    papel, banco = m.papeis.get(papel_id), m.bancos.get(banco_id)
    if papel is None or banco is None:
        return "nenhum"
    if papel.superusuario:
        return "super"
    if papel_id in banco.acesso_direto:
        return "dono" if banco.dono == papel_id else "direto"
    if banco.publico or any(g in banco.acesso_direto for g in grupos(m, papel_id, herdados=True)):
        return "herdado"
    return "nenhum"


def superusuarios_com_login(m: ModeloServidor) -> List[str]:
    """Os ids dos superusuários que entram (com LOGIN), fora os que o rascunho apaga — o "último superusuário" de
    que ninguém pode se livrar. Na ordem do modelo."""
    return [p.id for p in m.papeis.values() if p.superusuario and p.login and p.estado != "apagado"]
