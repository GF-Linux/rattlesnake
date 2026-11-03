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


@dataclass
class ModeloServidor:
    papeis: Dict[str, Papel] = field(default_factory=dict)
    bancos: Dict[str, Banco] = field(default_factory=dict)
    eu: str = ""                        # id do papel da conexão (current_user)
    versao: int = 0                     # server_version_num (180000 = 18)

    def copia(self) -> "ModeloServidor":
        """Cópia profunda: o rascunho mexe nela sem tocar no que veio do servidor."""
        return copy.deepcopy(self)

    def papel_por_nome(self, nome: str) -> Optional[Papel]:
        """O papel de nome exatamente `nome` (nomes de papel diferenciam maiúsculas)."""
        return next((p for p in self.papeis.values() if p.nome == nome), None)

    def banco_por_nome(self, nome: str) -> Optional[Banco]:
        return next((b for b in self.bancos.values() if b.nome == nome), None)


def grupos(m: ModeloServidor, papel_id: str) -> Set[str]:
    """Todos os grupos de que `papel_id` é membro, direto ou por outro grupo (transitivo; um id que não está no
    modelo, como um `pg_*` deixado de fora, é ignorado; um ciclo não trava)."""
    vistos: Set[str] = set()
    borda = [papel_id]
    while borda:
        papel = m.papeis.get(borda.pop())
        for g in papel.membro_de if papel is not None else ():
            if g not in vistos and g != papel_id and g in m.papeis:
                vistos.add(g)
                borda.append(g)
    return vistos


def acesso(m: ModeloServidor, papel_id: str, banco_id: str) -> str:
    """Como `papel_id` entra em `banco_id`: "super" (superusuário entra em qualquer um), "dono", "direto" (CONNECT
    dado a ele), "herdado" (pelo PUBLIC, ou por um grupo de que é membro — mesmo por outro grupo — que tem o CONNECT
    ou é o dono) ou "nenhum". Um banco que não aceita conexão (`aceita_conexao=False`) dá o mesmo valor: o das
    permissões; quem mostra marca à parte que ninguém entra nele agora."""
    papel, banco = m.papeis.get(papel_id), m.bancos.get(banco_id)
    if papel is None or banco is None:
        return "nenhum"
    if papel.superusuario:
        return "super"
    if banco.dono == papel_id:
        return "dono"
    if papel_id in banco.acesso_direto:
        return "direto"
    if banco.publico or any(g == banco.dono or g in banco.acesso_direto for g in grupos(m, papel_id)):
        return "herdado"
    return "nenhum"


def superusuarios_com_login(m: ModeloServidor) -> List[str]:
    """Os ids dos superusuários que entram (com LOGIN), fora os que o rascunho apaga — o "último superusuário" de
    que ninguém pode se livrar. Na ordem do modelo."""
    return [p.id for p in m.papeis.values() if p.superusuario and p.login and p.estado != "apagado"]
