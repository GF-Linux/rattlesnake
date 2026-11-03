"""Testes puros da fase 3 (usuários e permissões): o modelo do servidor e, depois, o rascunho e as telas."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ratt.db import servidor
from ratt.db.servidor import Banco, ModeloServidor, Papel


def _papel(id, nome, login=True, super_=False, membro_de=()):
    return Papel(id, nome, login, super_, False, False, tuple(membro_de))


def modelo_exemplo() -> ModeloServidor:
    """jared (superusuário), app (entra, dono de `sistema`), leitura (grupo), ana (em leitura), bruno (em
    `meio`, que está em leitura), carla (sem nada). `estudos` tirou o CONNECT do PUBLIC e deu a leitura; `sistema`
    deixa o PUBLIC entrar; `fechado` não deixa ninguém (só o dono, app) e não aceita conexão."""
    papeis = {p.id: p for p in (
        _papel("r10", "jared", super_=True),
        _papel("r11", "app"),
        _papel("r12", "leitura", login=False),
        _papel("r13", "ana", membro_de=("r12",)),
        _papel("r14", "meio", login=False, membro_de=("r12",)),
        _papel("r15", "bruno", membro_de=("r14",)),
        _papel("r16", "carla"),
        _papel("r17", "velho", login=False, super_=True),
    )}
    bancos = {b.id: b for b in (
        Banco("d1", "estudos", "r10", True, ("r10", "r12"), False),
        Banco("d2", "sistema", "r11", True, ("r11",), True),
        Banco("d3", "fechado", "r11", False, ("r11",), False),
    )}
    return ModeloServidor(papeis, bancos, "r10", 180000)


def test_acesso_dono_direto_herdado_nenhum_e_super():
    m = modelo_exemplo()
    assert servidor.acesso(m, "r10", "d1") == "super"       # superusuário entra em qualquer um
    assert servidor.acesso(m, "r10", "d3") == "super"
    assert servidor.acesso(m, "r11", "d2") == "dono"
    assert servidor.acesso(m, "r11", "d1") == "nenhum"      # PUBLIC sem CONNECT em estudos, app sem grupo
    assert servidor.acesso(m, "r12", "d1") == "direto"
    assert servidor.acesso(m, "r13", "d1") == "herdado"     # pelo grupo leitura
    assert servidor.acesso(m, "r15", "d1") == "herdado"     # bruno → meio → leitura (transitivo)
    assert servidor.acesso(m, "r16", "d1") == "nenhum"
    assert servidor.acesso(m, "r16", "d2") == "herdado"     # pelo PUBLIC
    assert servidor.acesso(m, "r12", "d2") == "herdado"
    # banco que não aceita conexão: o valor é o das permissões (a tela marca à parte)
    assert servidor.acesso(m, "r11", "d3") == "dono" and servidor.acesso(m, "r16", "d3") == "nenhum"


def test_acesso_herdado_de_grupo_dono_e_ciclo_nao_trava():
    m = modelo_exemplo()
    m.bancos["d3"] = Banco("d3", "fechado", "r14", False, (), False)        # o dono é o grupo meio
    assert servidor.acesso(m, "r14", "d3") == "dono"
    assert servidor.acesso(m, "r15", "d3") == "herdado"
    assert servidor.acesso(m, "r13", "d3") == "nenhum"
    # um grupo que some do modelo (um pg_* filtrado) ou um ciclo não quebram a busca
    m.papeis["r12"] = _papel("r12", "leitura", login=False, membro_de=("r15", "r999"))
    assert servidor.acesso(m, "r16", "d1") == "nenhum"
    assert servidor.acesso(m, "r15", "d1") == "herdado"
    assert servidor.acesso(m, "r999", "d1") == "nenhum" and servidor.acesso(m, "r13", "d999") == "nenhum"


def test_superusuarios_com_login_ignora_quem_nao_entra_e_os_apagados():
    m = modelo_exemplo()
    assert servidor.superusuarios_com_login(m) == ["r10"]           # velho é superusuário sem LOGIN
    m.papeis["r11"].superusuario = True
    assert servidor.superusuarios_com_login(m) == ["r10", "r11"]
    m.papeis["r10"].estado = "apagado"
    assert servidor.superusuarios_com_login(m) == ["r11"]


def test_modelo_copia_e_busca_por_nome():
    m = modelo_exemplo()
    c = m.copia()
    c.papeis["r13"].membro_de = ()
    c.bancos["d1"].publico = True
    assert m.papeis["r13"].membro_de == ("r12",) and m.bancos["d1"].publico is False
    assert m.papel_por_nome("ana").id == "r13" and m.papel_por_nome("Ana") is None
    assert m.banco_por_nome("sistema").id == "d2" and m.banco_por_nome("nada") is None
    assert m.eu == "r10" and m.versao == 180000
    assert m.bancos["d1"].template is False
