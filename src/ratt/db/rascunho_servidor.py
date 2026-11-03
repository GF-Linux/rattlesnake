"""O rascunho do servidor: criar, mudar e remover papéis, grupos, quem entra em que banco e o dono de um banco.

Puro (sem banco, sem tela), com o mesmo contrato do rascunho do canvas (`rascunho.Rascunho`): a base é o
`ModeloServidor` lido do catálogo, cada edição vira uma operação, a `vista()` é uma cópia da base com as operações
aplicadas e os itens tocados marcados em `estado`. Papéis e bancos se ligam por id (`r{oid}`, `d{oid}`, `novo-p{n}`
para o papel criado no rascunho); `nomes` guarda o nome que cada id tinha na base quando a operação entrou, para um
oid reaproveitado não virar outro papel.

Duas coisas o distinguem do rascunho do canvas:

- **Ordem de aplicação** (decisão 5 da spec): as operações comuns rodam primeiro, na ordem do rascunho, numa
  transação; cada `RemoverPapeis` roda depois, banco a banco. A vista, o texto SQL e os `grupos()` seguem essa
  ordem, para o que a pessoa vê ser o que o servidor fica. Uma operação que refere um papel removido por uma
  remoção ANTERIOR no rascunho é inválida (mesmo rodando antes dela).
- **Travas** (decisão 8): `trava(op, m)` recusa o que deixaria a pessoa (`m.eu`) trancada para fora; o
  `acrescentar` recusa, e uma operação que vem do disco e trancaria fica inválida.

Senha: nunca há senha provisória. `CriarPapel` com LOGIN gera o `CREATE ROLE` sem `PASSWORD`, e `TrocarSenha` não
gera comando nenhum; o texto SQL leva só um comentário ("a senha é pedida ao aplicar"). Só `grupos(senhas=…)` emite
o `ALTER ROLE … PASSWORD '<hash>'`, logo depois do `CREATE ROLE` (no mesmo grupo). Nenhuma operação guarda senha.
"""
from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field, fields
from typing import ClassVar, Dict, Iterable, List, Optional, Set, Tuple

from . import servidor
from .idioma import t
from .rascunho import _ERROS_OP, RascunhoCorrompido, _marcar, ident
from .servidor import ModeloServidor, Papel

PUBLIC = "PUBLIC"           # alvo especial de DarAcesso/TirarAcesso: não é um id (ids são `r…`, `d…`, `novo-p…`)

Comandos = List[Tuple[Optional[str], str]]


def _linha(texto: str) -> str:
    """Numa linha só: o texto vai para um comentário SQL (um nome com quebra de linha não pode virar comando)."""
    return " ".join(texto.splitlines())


def _papel(m: ModeloServidor, id: str) -> Papel:
    p = m.papeis.get(id)
    if p is None or p.estado == "apagado":
        raise KeyError(id)
    return p


def _banco(m: ModeloServidor, id: str) -> servidor.Banco:
    b = m.bancos.get(id)
    if b is None or b.estado == "apagado":
        raise KeyError(id)
    return b


def _alvo_sql(m: ModeloServidor, alvo: str) -> str:
    return PUBLIC if alvo == PUBLIC else ident(_papel(m, alvo).nome)


def _alvo_nome(m: ModeloServidor, alvo: str) -> str:
    return PUBLIC if alvo == PUBLIC else _papel(m, alvo).nome


def _novo_dono(banco: servidor.Banco, antigo: str, novo: str) -> None:
    """O dono muda como no PostgreSQL (`aclnewowner`): o que o dono antigo tinha no `datacl` passa ao novo, e o que
    ele deu passa a ter sido dado pelo novo."""
    def troca(ids):
        return tuple(dict.fromkeys(novo if i == antigo else i for i in ids))
    banco.dono = novo
    banco.acesso_direto = troca(banco.acesso_direto)
    concedido: Dict[str, Tuple[str, ...]] = {}
    for quem, por in banco.concedido_por.items():
        chave = novo if quem == antigo else quem
        concedido[chave] = troca(concedido.get(chave, ()) + por)
    banco.concedido_por = concedido
    _marcar(banco, "mudado")


def _sem(ids: Tuple[str, ...], fora: Set[str]) -> Tuple[str, ...]:
    return tuple(i for i in ids if i not in fora)


def _cascata(m: ModeloServidor, fora: Set[str]) -> None:
    """O que cai junto quando os papéis `fora` somem (`DROP OWNED` revoga com CASCADE o que foi dado a eles, e o
    que eles deram cai junto): cada CONNECT e cada pertença cujos concedentes saíram todos — transitivamente: o que
    alguém deu pela opção de conceder que perdeu também cai. Um concedente segura o que deu enquanto não sai e
    ainda tem de onde dar: no banco, é o dono ou ainda tem o CONNECT; na pertença, é superusuário ou ainda é membro
    do grupo. Sem `concedido_por` (não se sabe quem deu), nada cai por isso."""
    for b in m.bancos.values():
        tem = (set(b.acesso_direto) | ({PUBLIC} if b.publico else set())) - fora
        mudou = True
        while mudou:
            mudou = False
            for quem in sorted(tem):
                por = b.concedido_por.get(quem)
                if quem != b.dono and por and not any(x not in fora and (x == b.dono or x in tem) for x in por):
                    tem.discard(quem)
                    mudou = True
        diretos, publico = tuple(i for i in b.acesso_direto if i in tem), b.publico and PUBLIC in tem
        concedido = {q: _sem(por, fora) for q, por in b.concedido_por.items() if q in tem and _sem(por, fora)}
        if (diretos, publico, concedido) != (b.acesso_direto, b.publico, b.concedido_por):
            b.acesso_direto, b.publico, b.concedido_por = diretos, publico, concedido
            _marcar(b, "mudado")
    vivos = {p.id: set(p.membro_de) - fora for p in m.papeis.values() if p.id not in fora and p.estado != "apagado"}
    mudou = True
    while mudou:
        mudou = False
        for pid in sorted(vivos):
            for g in sorted(vivos[pid]):
                por = m.papeis[pid].concedido_por.get(g)
                if por and not any(x not in fora and x in m.papeis
                                   and (m.papeis[x].superusuario or g in vivos.get(x, ())) for x in por):
                    vivos[pid].discard(g)
                    mudou = True
    for pid, gs in vivos.items():
        p = m.papeis[pid]
        if set(p.membro_de) != gs:
            sai = set(p.membro_de) - gs
            p.membro_de, p.nao_herda, p.sem_set = _sem(p.membro_de, sai), _sem(p.nao_herda, sai), _sem(p.sem_set, sai)
        p.concedido_por = {g: _sem(por, fora) for g, por in p.concedido_por.items() if g in gs and _sem(por, fora)}


# ── operações ──

class OperacaoServidor:
    tipo: ClassVar[str] = ""
    nomes: Dict[str, str]

    def aplicar(self, m: ModeloServidor) -> None:
        raise NotImplementedError

    def comandos(self, m: ModeloServidor) -> Comandos:
        """Pares (banco de destino pelo nome, ou None = qualquer banco: o da conexão; SQL sem `;`). Nunca senha."""
        raise NotImplementedError

    def senha_de(self) -> Optional[str]:
        """O id do papel cuja senha o aplicar pede (None: a operação não leva senha)."""
        return None

    def nome_senha(self, m: ModeloServidor) -> str:
        """O nome do papel da senha, sobre o modelo de antes da operação."""
        return _papel(m, self.senha_de()).nome

    def descricao(self, m: ModeloServidor) -> str:
        raise NotImplementedError

    def refere(self) -> Set[str]:
        return set()

    def cria(self) -> Set[str]:
        return set()


_RESERVADOS = ("public", "none")


@dataclass
class CriarPapel(OperacaoServidor):
    papel: Papel
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "criar_papel"

    def _conferir(self, m: ModeloServidor) -> None:
        p = self.papel
        if not p.id.startswith("novo-p") or p.id in m.papeis:
            raise ValueError(t("usu.e.id", id=p.id))
        ident(p.nome)                                           # nome vazio
        if p.nome in _RESERVADOS or p.nome.startswith("pg_") or len(p.nome.encode("utf-8")) > 63:
            raise ValueError(t("usu.e.nome_reservado", nome=p.nome))
        if any(o.nome == p.nome and o.estado != "apagado" for o in m.papeis.values()):
            raise ValueError(t("usu.e.nome_existe", nome=p.nome))

    def aplicar(self, m: ModeloServidor) -> None:
        self._conferir(m)
        novo = copy.deepcopy(self.papel)
        novo.estado, novo.nao_herda, novo.sem_set, novo.herda, novo.sistema = "novo", (), (), True, False
        novo.concedido_por = {}
        m.papeis[novo.id] = novo

    def comandos(self, m: ModeloServidor) -> Comandos:
        self._conferir(m)
        p = self.papel
        partes = ["CREATE ROLE", ident(p.nome)]
        for sim, palavra in ((p.login, "LOGIN"), (p.superusuario, "SUPERUSER"), (p.cria_bancos, "CREATEDB"),
                             (p.cria_papeis, "CREATEROLE")):
            if sim:
                partes.append(palavra)
        if p.membro_de:
            partes.append("IN ROLE " + ", ".join(ident(_papel(m, g).nome) for g in p.membro_de))
        return [(None, " ".join(partes))]

    def senha_de(self) -> Optional[str]:
        return self.papel.id if self.papel.login else None

    def nome_senha(self, m: ModeloServidor) -> str:
        return self.papel.nome

    def descricao(self, m: ModeloServidor) -> str:
        return t("op.criar_usuario" if self.papel.login else "op.criar_grupo", papel=self.papel.nome)

    def refere(self) -> Set[str]:
        return set(self.papel.membro_de)

    def cria(self) -> Set[str]:
        return {self.papel.id}


_PODERES = (("login", "LOGIN"), ("superusuario", "SUPERUSER"), ("cria_bancos", "CREATEDB"),
            ("cria_papeis", "CREATEROLE"))


@dataclass
class MudarPoderes(OperacaoServidor):
    papel: str
    login: bool
    superusuario: bool
    cria_bancos: bool
    cria_papeis: bool
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "mudar_poderes"

    def _mudancas(self, m: ModeloServidor) -> List[str]:
        """Só os poderes que mudam (`ALTER ROLE x NOSUPERUSER` exige superusuário mesmo sem mudar nada)."""
        p = _papel(m, self.papel)
        mud = [(palavra if getattr(self, campo) else "NO" + palavra) for campo, palavra in _PODERES
               if getattr(self, campo) != getattr(p, campo)]
        if not mud:
            raise ValueError(t("usu.e.nada_muda", papel=p.nome))
        return mud

    def aplicar(self, m: ModeloServidor) -> None:
        p = _papel(m, self.papel)
        for campo, _ in _PODERES:
            setattr(p, campo, getattr(self, campo))
        _marcar(p, "mudado")

    def comandos(self, m: ModeloServidor) -> Comandos:
        return [(None, f"ALTER ROLE {ident(_papel(m, self.papel).nome)} {' '.join(self._mudancas(m))}")]

    def descricao(self, m: ModeloServidor) -> str:
        return t("op.mudar_poderes", papel=_papel(m, self.papel).nome, poderes=" ".join(self._mudancas(m)))

    def refere(self) -> Set[str]:
        return {self.papel}


@dataclass
class TrocarSenha(OperacaoServidor):
    papel: str
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "trocar_senha"

    def aplicar(self, m: ModeloServidor) -> None:
        _marcar(_papel(m, self.papel), "mudado")

    def comandos(self, m: ModeloServidor) -> Comandos:
        _papel(m, self.papel)
        return []                       # o ALTER ROLE … PASSWORD só sai em grupos(senhas=…)

    def senha_de(self) -> Optional[str]:
        return self.papel

    def descricao(self, m: ModeloServidor) -> str:
        return t("op.trocar_senha", papel=_papel(m, self.papel).nome)

    def refere(self) -> Set[str]:
        return {self.papel}


@dataclass
class PorNoGrupo(OperacaoServidor):
    papel: str
    grupo: str
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "por_no_grupo"

    def _conferir(self, m: ModeloServidor) -> Tuple[Papel, Papel]:
        p, g = _papel(m, self.papel), _papel(m, self.grupo)
        if self.grupo in p.membro_de:
            raise ValueError(t("usu.e.ja_membro", papel=p.nome, grupo=g.nome))
        if self.papel == self.grupo or self.papel in servidor.grupos(m, self.grupo):
            raise ValueError(t("usu.e.ciclo", papel=p.nome, grupo=g.nome))
        return p, g

    def aplicar(self, m: ModeloServidor) -> None:
        p, _ = self._conferir(m)
        p.membro_de = p.membro_de + (self.grupo,)
        if not p.herda:                 # NOINHERIT: a pertença nova não herda (16+: o padrão vem do rolinherit)
            p.nao_herda = p.nao_herda + (self.grupo,)
        p.concedido_por.pop(self.grupo, None)
        _marcar(p, "mudado")

    def comandos(self, m: ModeloServidor) -> Comandos:
        p, g = self._conferir(m)
        return [(None, f"GRANT {ident(g.nome)} TO {ident(p.nome)}")]

    def descricao(self, m: ModeloServidor) -> str:
        return t("op.por_no_grupo", papel=_papel(m, self.papel).nome, grupo=_papel(m, self.grupo).nome)

    def refere(self) -> Set[str]:
        return {self.papel, self.grupo}


@dataclass
class TirarDoGrupo(OperacaoServidor):
    papel: str
    grupo: str
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "tirar_do_grupo"

    def _conferir(self, m: ModeloServidor) -> Tuple[Papel, Papel]:
        p, g = _papel(m, self.papel), _papel(m, self.grupo)
        if self.grupo not in p.membro_de:
            raise ValueError(t("usu.e.nao_membro", papel=p.nome, grupo=g.nome))
        return p, g

    def aplicar(self, m: ModeloServidor) -> None:
        p, _ = self._conferir(m)
        p.membro_de = tuple(g for g in p.membro_de if g != self.grupo)
        p.nao_herda = tuple(g for g in p.nao_herda if g != self.grupo)
        p.sem_set = tuple(g for g in p.sem_set if g != self.grupo)
        p.concedido_por.pop(self.grupo, None)
        _marcar(p, "mudado")

    def comandos(self, m: ModeloServidor) -> Comandos:
        p, g = self._conferir(m)
        return [(None, f"REVOKE {ident(g.nome)} FROM {ident(p.nome)}")]

    def descricao(self, m: ModeloServidor) -> str:
        return t("op.tirar_do_grupo", papel=_papel(m, self.papel).nome, grupo=_papel(m, self.grupo).nome)

    def refere(self) -> Set[str]:
        return {self.papel, self.grupo}


@dataclass
class DarAcesso(OperacaoServidor):
    alvo: str                           # id do papel ou PUBLIC
    banco: str
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "dar_acesso"

    def _conferir(self, m: ModeloServidor) -> servidor.Banco:
        b = _banco(m, self.banco)
        tem = b.publico if self.alvo == PUBLIC else self.alvo in b.acesso_direto
        if tem:
            raise ValueError(t("usu.e.ja_entra", papel=_alvo_nome(m, self.alvo), banco=b.nome))
        return b

    def aplicar(self, m: ModeloServidor) -> None:
        b = self._conferir(m)
        if self.alvo == PUBLIC:
            b.publico = True
        else:
            b.acesso_direto = b.acesso_direto + (self.alvo,)
        b.concedido_por.pop(self.alvo, None)         # dado agora: pelo dono (ou por um superusuário, que dá como ele)
        _marcar(b, "mudado")

    def comandos(self, m: ModeloServidor) -> Comandos:
        b = self._conferir(m)
        return [(None, f"GRANT CONNECT ON DATABASE {ident(b.nome)} TO {_alvo_sql(m, self.alvo)}")]

    def descricao(self, m: ModeloServidor) -> str:
        return t("op.dar_acesso", papel=_alvo_nome(m, self.alvo), banco=_banco(m, self.banco).nome)

    def refere(self) -> Set[str]:
        return {self.banco} | ({self.alvo} if self.alvo != PUBLIC else set())


@dataclass
class TirarAcesso(OperacaoServidor):
    alvo: str                           # id do papel ou PUBLIC
    banco: str
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "tirar_acesso"

    def _conferir(self, m: ModeloServidor) -> servidor.Banco:
        b = _banco(m, self.banco)
        tem = b.publico if self.alvo == PUBLIC else self.alvo in b.acesso_direto
        if not tem:
            raise ValueError(t("usu.e.nao_entra", papel=_alvo_nome(m, self.alvo), banco=b.nome))
        return b

    def aplicar(self, m: ModeloServidor) -> None:
        b = self._conferir(m)
        if self.alvo == PUBLIC:
            b.publico = False
        else:
            b.acesso_direto = tuple(i for i in b.acesso_direto if i != self.alvo)
        b.concedido_por.pop(self.alvo, None)
        _marcar(b, "mudado")

    def comandos(self, m: ModeloServidor) -> Comandos:
        b = self._conferir(m)
        return [(None, f"REVOKE CONNECT ON DATABASE {ident(b.nome)} FROM {_alvo_sql(m, self.alvo)}")]

    def descricao(self, m: ModeloServidor) -> str:
        return t("op.tirar_acesso", papel=_alvo_nome(m, self.alvo), banco=_banco(m, self.banco).nome)

    def refere(self) -> Set[str]:
        return {self.banco} | ({self.alvo} if self.alvo != PUBLIC else set())


@dataclass
class TrocarDonoDoBanco(OperacaoServidor):
    banco: str
    papel: str
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "trocar_dono_banco"

    def _conferir(self, m: ModeloServidor) -> Tuple[servidor.Banco, Papel]:
        b, p = _banco(m, self.banco), _papel(m, self.papel)
        if b.dono == self.papel:
            raise ValueError(t("usu.e.ja_dono", papel=p.nome, banco=b.nome))
        return b, p

    def aplicar(self, m: ModeloServidor) -> None:
        b, _ = self._conferir(m)
        _novo_dono(b, b.dono, self.papel)

    def comandos(self, m: ModeloServidor) -> Comandos:
        b, p = self._conferir(m)
        return [(None, f"ALTER DATABASE {ident(b.nome)} OWNER TO {ident(p.nome)}")]

    def descricao(self, m: ModeloServidor) -> str:
        return t("op.trocar_dono_banco", banco=_banco(m, self.banco).nome, papel=_papel(m, self.papel).nome)

    def refere(self) -> Set[str]:
        return {self.banco, self.papel}


@dataclass
class RemoverPapeis(OperacaoServidor):
    papeis: Tuple[str, ...]
    novo_dono: str
    bancos: Tuple[str, ...] = ()        # NOMES dos bancos que aceitam conexão (o formulário preenche pelo modelo)
    nomes: Dict[str, str] = field(default_factory=dict)
    tipo: ClassVar[str] = "remover_papeis"

    def _conferir(self, m: ModeloServidor) -> Tuple[str, str]:
        """(lista dos papéis no SQL, novo dono no SQL)."""
        if not self.papeis or len(set(self.papeis)) != len(self.papeis) or self.novo_dono in self.papeis:
            raise ValueError(t("usu.e.remover"))
        if len(set(self.bancos)) != len(self.bancos):
            raise ValueError(t("usu.e.remover"))
        for nome in self.bancos:
            if m.banco_por_nome(nome) is None:
                raise ValueError(t("canvas.e.nao_existe", nome=nome))
        lista = ", ".join(ident(_papel(m, p).nome) for p in self.papeis)
        return lista, ident(_papel(m, self.novo_dono).nome)

    def aplicar(self, m: ModeloServidor) -> None:
        self._conferir(m)
        fora = set(self.papeis)
        for b in m.bancos.values():
            if b.dono in fora:                  # REASSIGN OWNED / ALTER DATABASE … OWNER
                _novo_dono(b, b.dono, self.novo_dono)
        for p in m.papeis.values():
            if p.id in fora:
                p.estado, p.membro_de, p.nao_herda, p.sem_set, p.concedido_por = "apagado", (), (), (), {}
        _cascata(m, fora)                       # DROP OWNED / DROP ROLE: o que foi dado a eles e por eles

    def comandos(self, m: ModeloServidor) -> Comandos:
        lista, novo = self._conferir(m)
        cmds: Comandos = []
        for nome in self.bancos:
            # um DROP OWNED por papel: `DROP OWNED BY a, b` falha ("could not find tuple for default ACL") quando
            # uma permissão padrão de um dá algo ao outro
            cmds.append((nome, f"REASSIGN OWNED BY {lista} TO {novo}"))
            cmds += [(nome, f"DROP OWNED BY {ident(_papel(m, p).nome)}") for p in self.papeis]
        fora = set(self.papeis)
        cmds += [(None, f"ALTER DATABASE {ident(b.nome)} OWNER TO {novo}")
                 for b in m.bancos.values() if b.dono in fora and b.estado != "apagado"]
        cmds.append((None, f"DROP ROLE {lista}"))
        return cmds

    def descricao(self, m: ModeloServidor) -> str:
        return t("op.remover_papeis", papeis=", ".join(_papel(m, p).nome for p in self.papeis),
                 novo=_papel(m, self.novo_dono).nome)

    def refere(self) -> Set[str]:
        return set(self.papeis) | {self.novo_dono}


_TIPOS_OP = {cls.tipo: cls for cls in (CriarPapel, MudarPoderes, TrocarSenha, PorNoGrupo, TirarDoGrupo, DarAcesso,
                                       TirarAcesso, TrocarDonoDoBanco, RemoverPapeis)}


# ── as travas ──

def trava(op: OperacaoServidor, m: ModeloServidor) -> Optional[str]:
    """O motivo de recusar `op` sobre `m` porque ela trancaria a pessoa (`m.eu`) para fora; None se pode.
    Remover o próprio papel, o `postgres` ou o último superusuário com LOGIN; tirar de si LOGIN ou SUPERUSER; e
    qualquer operação depois da qual `m.eu` não entra mais no banco desta conexão (`m.banco_atual`) — pelo acesso
    efetivo (`servidor.acesso`) da cópia com a operação aplicada: tirar o CONNECT dela, do PUBLIC ou de um grupo,
    sair de um grupo (ou tirar dele um grupo do meio da corrente), remover o grupo, trocar o dono do banco.
    Uma operação que não vale sobre `m` levanta o erro dela (o rascunho confere antes de perguntar)."""
    eu = m.eu
    if isinstance(op, RemoverPapeis):
        if eu in op.papeis:
            return t("usu.trava.remover_si")
        if any(p is not None and p.nome == "postgres" for p in (m.papeis.get(i) for i in op.papeis)):
            return t("usu.trava.postgres")
    if isinstance(op, MudarPoderes) and op.papel == eu and eu in m.papeis:
        if m.papeis[eu].login and not op.login:
            return t("usu.trava.proprio_login")
        if m.papeis[eu].superusuario and not op.superusuario:
            return t("usu.trava.proprio_super")
    depois = m.copia()
    op.aplicar(depois)
    if servidor.superusuarios_com_login(m) and not servidor.superusuarios_com_login(depois):
        return t("usu.trava.ultimo_super")
    atual = m.banco_atual
    if (atual in m.bancos and servidor.acesso(m, eu, atual) != "nenhum"
            and servidor.acesso(depois, eu, atual) == "nenhum"):
        return t("usu.trava.sem_entrar", banco=m.bancos[atual].nome)
    return None


# ── o rascunho ──

def _existe(m: ModeloServidor, id: str) -> bool:
    obj = m.papeis.get(id) or m.bancos.get(id)
    return obj is not None and obj.estado != "apagado"


def _nome_na_base(base: ModeloServidor, id: str) -> Optional[str]:
    obj = base.papeis.get(id) or base.bancos.get(id)
    return obj.nome if obj is not None else None


def _nome_de(id: str, *modelos: ModeloServidor) -> str:
    for m in modelos:
        n = _nome_na_base(m, id)
        if n:
            return n
    return id


def _um_comando(cmd: str) -> str:
    """O SQL tem de ser exatamente UM comando (como no canvas)."""
    from .rodar import dividir
    partes = dividir(cmd + ";")
    if len(partes) != 1 or partes[0].meta or partes[0].texto != cmd:
        raise ValueError(t("canvas.e.um_comando"))
    return cmd


def comandos_de(op: OperacaoServidor, m: ModeloServidor) -> Comandos:
    """Os comandos da operação, conferidos um a um."""
    cmds = op.comandos(m)
    for _, cmd in cmds:
        _um_comando(cmd)
    return cmds


def _agrupar(cmds: Comandos) -> List[Tuple[Optional[str], List[str]]]:
    """Os comandos seguidos de um mesmo destino juntos, na ordem."""
    grupos: List[Tuple[Optional[str], List[str]]] = []
    for destino, cmd in cmds:
        if grupos and grupos[-1][0] == destino:
            grupos[-1][1].append(cmd)
        else:
            grupos.append((destino, [cmd]))
    return grupos


# ── de e para o disco ──

def _tipo_certo(valor, tipo: str) -> bool:
    """O valor de um campo lido do JSON tem o tipo que a anotação diz (as anotações são texto: `annotations`)."""
    if tipo == "str":
        return isinstance(valor, str)
    if tipo == "bool":
        return isinstance(valor, bool)
    if tipo == "Tuple[str, ...]":
        return isinstance(valor, tuple) and all(isinstance(x, str) for x in valor)
    if tipo == "Dict[str, str]":
        return isinstance(valor, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in valor.items())
    if tipo == "Dict[str, Tuple[str, ...]]":
        return isinstance(valor, dict) and all(isinstance(k, str) and _tipo_certo(v, "Tuple[str, ...]")
                                               for k, v in valor.items())
    if tipo == "Papel":
        return isinstance(valor, Papel) and all(_tipo_certo(getattr(valor, f.name), f.type) for f in fields(Papel))
    raise TypeError(f"campo de tipo inesperado: {tipo}")


def _conferir(op: OperacaoServidor) -> None:
    """Cada campo com o tipo certo: uma lista no lugar de um id quebraria cada conta do rascunho."""
    for f in fields(op):
        if not _tipo_certo(getattr(op, f.name), f.type):
            raise TypeError(f"{f.name} inválido: {getattr(op, f.name)!r}")


def _tupla(v) -> tuple:
    if not isinstance(v, list):
        raise TypeError(f"esperava uma lista: {v!r}")
    return tuple(v)


def _op_para_dict(op: OperacaoServidor) -> dict:
    d = {"tipo": op.tipo}
    d.update(asdict(op))
    return d


def _op_de_dict(d: dict) -> OperacaoServidor:
    cls = _TIPOS_OP[d["tipo"]]
    campos = {k: v for k, v in d.items() if k != "tipo"}
    if cls is CriarPapel and "papel" in campos:
        v = dict(campos["papel"])
        for nome in ("membro_de", "nao_herda", "sem_set"):
            if nome in v:
                v[nome] = _tupla(v[nome])
        if "concedido_por" in v:
            if not isinstance(v["concedido_por"], dict):
                raise TypeError("concedido_por inválido")
            v["concedido_por"] = {k: _tupla(x) for k, x in v["concedido_por"].items()}
        campos["papel"] = Papel(**v)
    if cls is RemoverPapeis:
        for nome in ("papeis", "bancos"):
            if nome in campos:
                campos[nome] = _tupla(campos[nome])
    return cls(**campos)


Passo = Tuple[OperacaoServidor, bool, str, Comandos]


class RascunhoServidor:
    """A base (o servidor como foi lido) mais a lista de operações que a pessoa fez em cima dela."""

    def __init__(self, base: ModeloServidor, operacoes: Optional[List[OperacaoServidor]] = None, proximo: int = 1):
        self.base = base
        self.operacoes: List[OperacaoServidor] = list(operacoes or [])
        self.proximo = proximo

    def novo_id(self, tipo: str) -> str:
        """Id de um papel criado no rascunho: `novo-p1`, `novo-p2` (o contador nunca volta atrás)."""
        i = f"novo-{tipo}{self.proximo}"
        self.proximo += 1
        return i

    def _ordem(self) -> List[int]:
        """Os índices na ordem em que rodam: as comuns na ordem do rascunho, depois as remoções."""
        comuns = [i for i, op in enumerate(self.operacoes) if not isinstance(op, RemoverPapeis)]
        return comuns + [i for i, op in enumerate(self.operacoes) if isinstance(op, RemoverPapeis)]

    def _percorrer(self) -> Tuple[ModeloServidor, List[Passo], List[Optional[str]], List[Optional[str]]]:
        """Aplica as operações, na ordem em que rodam, numa cópia da base. Devolve o modelo, os passos (na ordem do
        rascunho: (operação, válida, descrição, comandos)), para cada inválida o motivo e, para cada válida que pede
        senha, o nome do papel dela. Inválida é a que refere
        um id que não existe ali (ou que uma remoção anterior no rascunho tira), cujo nome na base mudou, cujo SQL
        não se gera ou que trancaria a pessoa para fora."""
        m = self.base.copia()
        n = len(self.operacoes)
        passos: List[Optional[Passo]] = [None] * n
        motivos: List[Optional[str]] = [None] * n
        de_quem: List[Optional[str]] = [None] * n
        removidos: List[Set[str]] = []
        antes: Set[str] = set()
        for op in self.operacoes:
            removidos.append(set(antes))
            if isinstance(op, RemoverPapeis):
                antes |= set(op.papeis)
        for i in self._ordem():
            op = self.operacoes[i]
            desc, cmds, motivo = op.tipo, [], None
            try:
                ref = op.refere()
                falta = sorted(x for x in ref if not _existe(m, x) or x in removidos[i])
                if falta:
                    motivo = t("canvas.e.nao_existe", nome=_nome_de(falta[0], m, self.base))
                elif any(_nome_na_base(self.base, x) not in (None, nome) for x, nome in op.nomes.items()):
                    motivo = t("usu.e.nome_mudou")
                else:
                    cmds = comandos_de(op, m)
                    desc = _linha(op.descricao(m))
                    motivo = trava(op, m)
                    if motivo is None:
                        if op.senha_de() is not None:
                            de_quem[i] = op.nome_senha(m)
                        op.aplicar(m)
            except KeyError as e:
                x = e.args[0] if e.args and isinstance(e.args[0], str) else str(e)
                motivo = t("canvas.e.nao_existe", nome=_nome_de(x, m, self.base))
            except _ERROS_OP as e:
                motivo = str(e) or type(e).__name__
            if motivo is not None:
                desc, cmds, de_quem[i] = op.tipo, [], None
            passos[i] = (op, motivo is None, desc, cmds)
            motivos[i] = motivo
        return m, passos, motivos, de_quem  # type: ignore[return-value]

    def acrescentar(self, op: OperacaoServidor) -> None:
        """Entra no fim, se valer — e se não fizer outra operação do rascunho deixar de valer (uma comum roda antes
        das remoções: tirar o CONNECT do PUBLIC pode fazer uma remoção já no rascunho trancar a pessoa). Recusa
        com ValueError e o motivo (o texto da trava, quando é ela)."""
        antigos = op.nomes
        op.nomes = {i: n for i in sorted(op.refere()) for n in [_nome_na_base(self.base, i)] if n is not None}
        invalidas = self.invalidas
        self.operacoes.append(op)
        _, passos, motivos, _ = self._percorrer()
        quebradas = [i for i, (_, valida, _, _) in enumerate(passos) if not valida and i not in invalidas]
        if quebradas:
            self.operacoes.pop()
            op.nomes = antigos                  # recusada: a operação volta como veio
            i = len(passos) - 1 if len(passos) - 1 in quebradas else quebradas[0]
            raise ValueError(motivos[i])

    def desfazer(self) -> Optional[OperacaoServidor]:
        return self.operacoes.pop() if self.operacoes else None

    def vista(self) -> ModeloServidor:
        """A base copiada com as operações válidas aplicadas, na ordem em que rodam, itens tocados marcados."""
        return self._percorrer()[0]

    def passos(self) -> List[Passo]:
        """Para cada operação, na ordem do rascunho: (operação, válida, descrição, comandos)."""
        return self._percorrer()[1]

    @property
    def invalidas(self) -> Set[int]:
        return {i for i, (_, valida, _, _) in enumerate(self._percorrer()[1]) if not valida}

    def rebase(self, base: ModeloServidor) -> Set[int]:
        """Troca a base (o servidor mudou) sem perder nenhuma operação e devolve as que deixaram de valer."""
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
    def de_dict(cls, d: dict, base: ModeloServidor) -> "RascunhoServidor":
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
            proximo = d.get("proximo", 1)
            if isinstance(proximo, bool):
                raise TypeError(f"proximo inválido: {proximo!r}")
            r = cls(base, ops, int(proximo))
            for op in ops:                      # um id `novo-p7` no disco: o próximo papel novo não pode repeti-lo
                for i in op.cria() | op.refere():
                    numero = re.fullmatch(r"novo-[a-z]+([0-9]+)", i)
                    if numero:
                        r.proximo = max(r.proximo, int(numero.group(1)) + 1)
            r.rebase(base)
        except RascunhoCorrompido:
            raise
        except _ERROS_OP as e:
            raise RascunhoCorrompido(f"rascunho ilegível: {type(e).__name__}: {e}") from e
        return r

    def modelo(self) -> ModeloServidor:
        """A vista sem os papéis removidos: como o servidor ficaria."""
        v = self.vista()
        v.papeis = {i: p for i, p in v.papeis.items() if p.estado != "apagado"}
        return v

    def grupos(self, senhas: Optional[Dict[str, str]] = None) -> List[Tuple[Optional[str], str]]:
        """O texto a rodar, por destino, na ordem (decisão 5 da spec): um grupo (None = o banco da conexão) com
        todas as operações comuns válidas; depois, para cada remoção válida, um grupo por banco (`REASSIGN OWNED` e
        um `DROP OWNED` por papel) e um último (None) com o `ALTER DATABASE … OWNER` e o `DROP ROLE`. Cada senha
        que o rascunho pede vem de `senhas` ({id do papel: hash SCRAM}) e entra citada como literal num
        `ALTER ROLE … PASSWORD`, logo depois do comando da operação; faltar uma (ou não vir `senhas`) é ValueError
        — nunca sai papel com senha provisória. O ensaio passa um hash descartável. Nada daqui é guardado."""
        from .filtro import literal
        _, passos, _, de_quem = self._percorrer()
        comuns: Comandos = []
        blocos: List[Comandos] = []
        for i in self._ordem():
            op, valida, _, cmds = passos[i]
            if not valida:
                continue
            cmds = list(cmds)
            papel = op.senha_de()
            if papel is not None:
                if senhas is None or papel not in senhas:
                    raise ValueError(t("usu.e.falta_senha", papel=de_quem[i]))
                cmds.append((None, _um_comando(f"ALTER ROLE {ident(de_quem[i])} PASSWORD "
                                               f"{literal(senhas[papel], 'text')}")))
            if isinstance(op, RemoverPapeis):
                blocos.append(cmds)
            else:
                comuns += cmds
        saida = []
        for bloco in ([comuns] if comuns else []) + blocos:
            saida += [(destino, "\n".join(c + ";" for c in cmds)) for destino, cmds in _agrupar(bloco)]
        return saida


    def _linhas(self, cabecalho: str) -> Tuple[List[str], List[int]]:
        """As linhas do texto SQL (na ordem em que roda) e a linha (1-based) do comentário de cada operação (0 para
        as inválidas), indexada pela ordem do rascunho."""
        linhas: List[str] = []
        if cabecalho:
            cab = _linha(cabecalho)
            linhas.append(cab if cab.startswith("--") else "-- " + cab)
        marcas = [0] * len(self.operacoes)
        vazio = True
        _, passos, _, de_quem = self._percorrer()
        for i in self._ordem():
            op, valida, desc, cmds = passos[i]
            if not valida:
                continue
            if not vazio:
                linhas.append("")
            vazio = False
            marcas[i] = len(linhas) + 1
            linhas.append(f"-- {i + 1}. {desc}")
            por_banco = any(destino is not None for destino, _ in cmds)
            for destino, grupo in _agrupar(cmds):
                if por_banco:
                    onde = t("usu.no_servidor") if destino is None else t("usu.no_banco", banco=destino)
                    linhas.append("-- " + _linha(onde))
                for cmd in grupo:
                    linhas.extend((cmd + ";").split("\n"))
            if de_quem[i] is not None:
                linhas.append("-- " + _linha(t("usu.senha_ao_aplicar", papel=ident(de_quem[i]))))
        return linhas, marcas

    def sql_texto(self, cabecalho: str = "") -> str:
        linhas, _ = self._linhas(cabecalho)
        return "\n".join(linhas) + ("\n" if linhas else "")

    def linhas_das_operacoes(self, cabecalho: str = "") -> List[int]:
        return self._linhas(cabecalho)[1]

    def operacao_da_linha(self, linha: int, cabecalho: str = "") -> int:
        """Índice (0-based, na ordem do rascunho) da operação a que a linha do texto pertence; -1 se não há operação
        válida. O texto segue a ordem em que roda, então a operação é a de comentário mais perto acima da linha."""
        marcas = self.linhas_das_operacoes(cabecalho)
        acima = [(ln, i) for i, ln in enumerate(marcas) if ln and ln <= linha]
        if acima:
            return max(acima)[1]
        validas = [(ln, i) for i, ln in enumerate(marcas) if ln]
        return min(validas)[1] if validas else -1
