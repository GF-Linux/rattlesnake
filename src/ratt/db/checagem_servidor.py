"""Avisos das operações do rascunho do servidor e a conferência antes de aplicar (o pré-voo).

Como em `checagem`, o ratt "avisa, não decide": `avisos_da_operacao_servidor` só lê (cada leitura de
`catalogo_servidor` é um `SELECT` só, somente leitura, com tempo limite) e devolve `checagem.Aviso`. O mais
importante é o da remoção (decisão 2 da spec): em cada banco que aceita conexão, o que os papéis possuem (passa ao
novo dono), os privilégios que têm e os que deram (uns ficam, outros somem no `DROP OWNED`) e as permissões padrão
(somem); um banco em que a pessoa não entra fica "não verificado".

`prevoo` (decisão 3) é a última conferência, relida do servidor na hora de aplicar: qualquer texto na lista recusa
o aplicar ANTES de mudar qualquer coisa. O ratt nunca liga `ALLOW_CONNECTIONS` sozinho: diz o comando.

`conectar(nome_do_banco)` devolve uma conexão a esse banco, ou None se a pessoa não consegue entrar nele. A conexão
é de quem chamou (pode guardá-la e reusá-la): nada aqui a fecha.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

import psycopg

from . import catalogo_servidor, servidor
from .checagem import Aviso
from .idioma import t
from .rascunho import ERROS_OP, ident
from .rascunho_servidor import (PUBLIC, CriarPapel, DarAcesso, OperacaoServidor, RascunhoServidor, RemoverPapeis,
                                TirarAcesso, TirarDoGrupo, TrocarSenha)
from .servidor import ModeloServidor

Conectar = Callable[[str], Optional[object]]

# a ordem em que a posse aparece no aviso; o texto de cada tipo é "singular|plural"
_TIPOS = ("tabela", "tabela_estrangeira", "view", "view_materializada", "sequencia", "funcao", "procedimento",
          "agregado", "schema", "tipo")


def _contagem(tipo: str, n: int) -> str:
    um, varios = t("usu.obj." + tipo).split("|")
    return f"{n} {um if n == 1 else varios}"


def _lista_posse(posse: Dict[str, int]) -> str:
    return ", ".join(_contagem(tipo, posse[tipo]) for tipo in _TIPOS if posse.get(tipo))


def _antes_e_depois(op: OperacaoServidor, r: RascunhoServidor) -> Tuple[ModeloServidor, ModeloServidor]:
    """A vista do rascunho sem `op` e com ela — esteja `op` já no rascunho (o painel) ou ainda não (o formulário)."""
    if any(o is op for o in r.operacoes):
        antes = RascunhoServidor(r.base, [o for o in r.operacoes if o is not op], r.proximo).vista()
        return antes, r.vista()
    return r.vista(), RascunhoServidor(r.base, r.operacoes + [op], r.proximo).vista()


def _conexao_atual(m: ModeloServidor, conectar: Conectar):
    """A conexão ao banco desta sessão (para o que é do servidor inteiro: sessões, `password_encryption`)."""
    banco = m.bancos.get(m.banco_atual) if m.banco_atual else None
    return conectar(banco.nome) if banco is not None else None


def _perde_acesso(antes: ModeloServidor, depois: ModeloServidor, fora: set) -> List[Aviso]:
    """Quem entrava num banco (que aceita conexão) e deixa de entrar — pelo acesso efetivo de antes e de depois."""
    avisos = []
    for b in depois.bancos.values():
        if b.estado == "apagado" or not b.aceita_conexao or b.id not in antes.bancos:
            continue
        for p in depois.papeis.values():
            if p.id in fora or p.sistema or p.estado == "apagado" or p.id not in antes.papeis:
                continue
            if servidor.acesso(antes, p.id, b.id) != "nenhum" and servidor.acesso(depois, p.id, b.id) == "nenhum":
                avisos.append(Aviso(p.id, "atencao", "chk.perde_acesso", {"papel": p.nome, "banco": b.nome}))
    return avisos


def _sai_de_grupo(antes: ModeloServidor, depois: ModeloServidor, fora: set,
                  pedido: Optional[Tuple[str, str]]) -> List[Aviso]:
    """Quem deixa de ser membro direto de um grupo — o grupo removido, ou a pertença que caiu junto com quem a deu
    (a cascata que a vista do rascunho já faz). `pedido`: a saída que a própria operação pede (não é aviso)."""
    avisos = []
    for p in depois.papeis.values():
        if p.id in fora or p.sistema or p.estado == "apagado" or p.id not in antes.papeis:
            continue
        for g in antes.papeis[p.id].membro_de:
            if g not in p.membro_de and g in antes.papeis and (p.id, g) != pedido:
                avisos.append(Aviso(p.id, "atencao", "chk.sai_do_grupo",
                                    {"papel": p.nome, "grupo": antes.papeis[g].nome}))
    return avisos


def _origem(m: ModeloServidor, papel_id: str, banco_id: str) -> str:
    """Por onde `papel_id` entra em `banco_id` sem o CONNECT dado a ele."""
    b = m.bancos[banco_id]
    if m.papeis[papel_id].superusuario:
        return t("chk.origem_super")
    if b.publico:
        return PUBLIC
    nomes = sorted(m.papeis[g].nome for g in servidor.grupos(m, papel_id, herdados=True) if g in b.acesso_direto)
    return t("chk.origem_grupos", grupos=", ".join(nomes))


def _remocao(op: RemoverPapeis, antes: ModeloServidor, conectar: Conectar) -> List[Aviso]:
    nomes = {p: antes.papeis[p].nome for p in op.papeis}
    novo = antes.papeis[op.novo_dono].nome
    avisos: List[Aviso] = []
    atual = _conexao_atual(antes, conectar)
    if atual is not None:
        try:
            abertas = catalogo_servidor.sessoes(atual, list(nomes.values()))
        except psycopg.Error as e:
            avisos.append(Aviso(antes.banco_atual, "atencao", "chk.banco_falhou",
                                {"banco": antes.bancos[antes.banco_atual].nome, "erro": str(e).strip()}))
        else:
            for p, nome in nomes.items():
                if abertas.get(nome):
                    avisos.append(Aviso(p, "impede", "chk.sessoes_abertas", {"papel": nome, "n": abertas[nome]}))
    for b in antes.bancos.values():
        if b.estado == "apagado":
            continue
        if b.dono in nomes:
            avisos.append(Aviso(b.id, "info", "chk.banco_passa", {"banco": b.nome, "papel": nomes[b.dono], "novo": novo}))
        if not b.aceita_conexao:
            continue
        c = conectar(b.nome)
        if c is None:
            avisos.append(Aviso(b.id, "atencao", "chk.nao_verificado", {"banco": b.nome}))
            continue
        lista = list(nomes.values())
        try:
            posse = catalogo_servidor.posse_no_banco(c, lista)
            privilegios = catalogo_servidor.privilegios_no_banco(c, lista, nao_ficam=lista + [novo])
            padrao = catalogo_servidor.permissoes_padrao(c, lista)
        except psycopg.Error as e:
            avisos.append(Aviso(b.id, "atencao", "chk.banco_falhou", {"banco": b.nome, "erro": str(e).strip()}))
            continue
        for p, nome in nomes.items():
            v = {"papel": nome, "banco": b.nome, "novo": novo}
            if posse.get(nome):
                avisos.append(Aviso(p, "info", "chk.posse_passa", dict(v, lista=_lista_posse(posse[nome]))))
            pr = privilegios.get(nome)
            if pr is not None and pr.tem:
                avisos.append(Aviso(p, "atencao", "chk.priv_tem_somem", dict(v, n=pr.tem)))
            if pr is not None and pr.deu_como_dono:
                avisos.append(Aviso(p, "info", "chk.priv_deu_dono_fica", dict(v, n=pr.deu_como_dono)))
            if pr is not None and pr.deu_por_opcao:
                avisos.append(Aviso(p, "atencao", "chk.priv_deu_opcao_somem", dict(v, n=pr.deu_por_opcao)))
            if padrao.get(nome):
                avisos.append(Aviso(p, "atencao", "chk.padrao_somem", dict(v, n=padrao[nome])))
    return avisos


def _md5(op: OperacaoServidor, antes: ModeloServidor, conectar: Conectar) -> List[Aviso]:
    """O ratt manda sempre o hash SCRAM; com o servidor em md5, diz o que isso quer dizer."""
    c = _conexao_atual(antes, conectar)
    if c is None:
        return []
    try:
        cifra = catalogo_servidor.password_encryption(c)
    except psycopg.Error:
        return []
    if cifra != "md5":
        return []
    papel = op.senha_de()
    return [Aviso(papel, "atencao", "chk.md5", {"papel": op.nome_senha(antes)})]


def avisos_da_operacao_servidor(op: OperacaoServidor, r: RascunhoServidor, conectar: Conectar) -> List[Aviso]:
    """Os avisos de `op` sobre o rascunho `r` (com ou sem `op` dentro). Uma operação que não vale ali não avisa
    nada (o rascunho a recusa com o motivo dela). Além dos avisos próprios de cada tipo, toda operação diz quem
    deixa de entrar em algum banco e quem deixa de ser membro de um grupo por causa dela (inclusive pela cascata do
    que um papel removido tinha dado)."""
    antes, depois = _antes_e_depois(op, r)
    try:
        op.comandos(antes)
    except ERROS_OP:
        return []
    avisos: List[Aviso] = []
    fora = set()
    pedido = (op.papel, op.grupo) if isinstance(op, TirarDoGrupo) else None
    if isinstance(op, RemoverPapeis):
        avisos += _remocao(op, antes, conectar)
        fora = set(op.papeis)
    elif isinstance(op, (CriarPapel, TrocarSenha)) and op.senha_de() is not None:
        avisos += _md5(op, antes, conectar)
    elif isinstance(op, DarAcesso) and op.alvo == PUBLIC:
        avisos.append(Aviso(op.banco, "info", "chk.public_dar", {"banco": antes.bancos[op.banco].nome}))
    elif isinstance(op, TirarAcesso):
        banco = antes.bancos[op.banco].nome
        if op.alvo == PUBLIC:
            avisos.append(Aviso(op.banco, "info", "chk.public_tirar", {"banco": banco}))
        else:
            fora = {op.alvo}                    # sair é o pedido; o aviso é só se ele continua entrando
            if servidor.acesso(depois, op.alvo, op.banco) != "nenhum":
                avisos.append(Aviso(op.alvo, "info", "chk.continua_entrando",
                                    {"papel": antes.papeis[op.alvo].nome, "banco": banco,
                                     "origem": _origem(depois, op.alvo, op.banco)}))
    removidos = set(op.papeis) if isinstance(op, RemoverPapeis) else set()
    return avisos + _perde_acesso(antes, depois, fora) + _sai_de_grupo(antes, depois, removidos, pedido)


# ── o pré-voo ──

def prevoo(r: RascunhoServidor, conexao_servidor, conectar: Conectar) -> List[str]:
    """Relê o servidor agora e diz, em texto, tudo o que impede aplicar `r`; lista vazia = pode aplicar. Não muda
    nada (nem o rascunho: o rebase é numa cópia).

    - Cada operação que valia e deixou de valer com o servidor de agora (o rebase), com o motivo.
    - Para cada remoção: sessões abertas dos papéis (com quantas); bancos com `datallowconn = false` em que o
      `pg_shdepend` mostra algo deles (com quantas dependências), com o `ALTER DATABASE … ALLOW_CONNECTIONS true;`
      que a pessoa pode rodar; um banco que aceita conexão e não está na remoção (o `DROP ROLE` falharia no fim);
      o novo dono tem de ter LOGIN ou ser a pessoa;
      e, se a pessoa é superusuária (o aplicar entra com ela), cada banco da remoção tem de deixar entrar.
    Uma leitura que falha também recusa (com o erro)."""
    try:
        return _prevoo(r, conexao_servidor, conectar)
    except psycopg.Error as e:
        return [t("chk.pv_falhou", erro=str(e).strip())]


def _prevoo(r: RascunhoServidor, conexao_servidor, conectar: Conectar) -> List[str]:
    agora = catalogo_servidor.modelo_servidor(conexao_servidor)
    era = r.passos()
    novo = RascunhoServidor(agora, list(r.operacoes), r.proximo)
    msgs: List[str] = []
    for i, ((op, valia, desc, _), motivo) in enumerate(zip(era, novo.motivos())):
        if valia and motivo is not None:
            msgs.append(t("chk.pv_invalida", n=i + 1, descricao=desc, motivo=motivo))
    vista = None
    for (op, vale, _, _) in novo.passos():
        if vale and isinstance(op, RemoverPapeis):
            if vista is None:
                vista = novo.vista()
            msgs += _prevoo_remocao(op, agora, vista, conexao_servidor, conectar)
    return msgs


def _prevoo_remocao(op: RemoverPapeis, agora: ModeloServidor, vista: ModeloServidor, conexao_servidor,
                    conectar: Conectar) -> List[str]:
    msgs: List[str] = []
    nomes = [agora.papeis[p].nome for p in op.papeis]
    abertas = catalogo_servidor.sessoes(conexao_servidor, nomes)
    msgs += [t("chk.pv_sessoes", papel=nome, n=abertas[nome]) for nome in nomes if abertas.get(nome)]
    fechados = set()
    dependencias = None
    for b in agora.bancos.values():
        if not b.aceita_conexao:
            fechados.add(b.nome)
            if dependencias is None:            # só se há banco fechado: o pg_shdepend diz se eles têm algo lá
                dependencias = catalogo_servidor.dependencias_por_banco(conexao_servidor, nomes)
            if dependencias.get(b.nome):
                msgs.append(t("chk.pv_banco_fechado", banco=b.nome, papeis=", ".join(nomes), n=dependencias[b.nome],
                              comando=f"ALTER DATABASE {ident(b.nome)} ALLOW_CONNECTIONS true;"))
        elif b.nome not in op.bancos:
            msgs.append(t("chk.pv_banco_fora", banco=b.nome))
    dono = agora.papeis.get(op.novo_dono) or vista.papeis[op.novo_dono]     # criado pelo próprio rascunho
    if not dono.login and dono.id != agora.eu:
        msgs.append(t("chk.pv_novo_dono", papel=dono.nome))
    eu = agora.papeis.get(agora.eu)
    if eu is not None and eu.superusuario:
        msgs += [t("chk.pv_sem_conexao", banco=nome) for nome in op.bancos
                 if nome not in fechados and conectar(nome) is None]
    return msgs
