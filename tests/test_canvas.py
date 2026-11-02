"""Testes do canvas: o modelo puro e, nas tarefas seguintes, a tela."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ratt.db import modelo
from ratt.db.modelo import Coluna, Ligacao, Modelo, Tabela


def modelo_png():
    """O esquema do PNG (users, roles, users_roles, profiles) montado à mão, como o catálogo o veria."""
    def c(i, nome, tipo="text", **kw):
        return Coluna(i, nome, tipo, **kw)

    users = Tabela("t1", "public", "users", [
        c("t1.c1", "id", "bigint", obrigatoria=True, identidade=True, pk=True, indexada=True),
        c("t1.c2", "first_name"), c("t1.c3", "last_name"),
        c("t1.c4", "email", obrigatoria=True), c("t1.c5", "password_hash"),
    ], nome_pk="users_pkey")
    roles = Tabela("t2", "public", "roles", [
        c("t2.c1", "id", "bigint", obrigatoria=True, identidade=True, pk=True, indexada=True),
        c("t2.c2", "name", obrigatoria=True),
    ], nome_pk="roles_pkey")
    users_roles = Tabela("t3", "public", "users_roles", [
        c("t3.c1", "user_id", "bigint", obrigatoria=True, pk=True, indexada=True),
        c("t3.c2", "role_id", "bigint", obrigatoria=True, pk=True),
    ], nome_pk="users_roles_pkey")
    profiles = Tabela("t4", "public", "profiles", [
        c("t4.c1", "id", "bigint", obrigatoria=True, identidade=True, pk=True, indexada=True),
        c("t4.c2", "bio"), c("t4.c3", "description"), c("t4.c4", "user_id", "bigint"),
    ], nome_pk="profiles_pkey")
    ligacoes = [
        Ligacao("k1", "t3", ("t3.c1",), "t1", ("t1.c1",), "users_roles_user_id_fkey"),
        Ligacao("k2", "t3", ("t3.c2",), "t2", ("t2.c1",), "users_roles_role_id_fkey"),
        Ligacao("k3", "t4", ("t4.c4",), "t1", ("t1.c1",), "profiles_user_id_fkey"),
    ]
    return Modelo("public", {t.id: t for t in (users, roles, users_roles, profiles)},
                  {k.id: k for k in ligacoes})


def test_cardinalidade_do_png():
    m = modelo_png()
    assert modelo.cardinalidade(m, m.ligacoes["k1"]) == "1:N"   # user_id é só parte da PK composta
    assert modelo.cardinalidade(m, m.ligacoes["k3"]) == "1:N"   # profiles.user_id sem UNIQUE
    m.tabelas["t4"].coluna("t4.c4").unica = True
    assert modelo.cardinalidade(m, m.ligacoes["k3"]) == "1:1"


def test_cardinalidade_pk_inteira_e_1_para_1():
    m = modelo_png()
    m.ligacoes["k4"] = Ligacao("k4", "t4", ("t4.c1",), "t1", ("t1.c1",), "profiles_id_fkey")
    assert modelo.cardinalidade(m, m.ligacoes["k4"]) == "1:1"


def test_opcional():
    m = modelo_png()
    assert modelo.opcional(m, m.ligacoes["k3"]) is True        # profiles.user_id aceita NULL
    assert modelo.opcional(m, m.ligacoes["k1"]) is False


def test_juncao():
    m = modelo_png()
    assert modelo.juncao(m, "t3") is True and modelo.juncao(m, "t1") is False


def test_chegando_e_recorte():
    m = modelo_png()
    assert modelo.chegando(m)["t1"] == 2
    so_roles = modelo.recortar(m, "t2", 1)
    assert set(so_roles.tabelas) == {"t2", "t3"} and set(so_roles.ligacoes) == {"k2"}


def test_recorte_com_dois_saltos():
    m = modelo_png()
    assert set(modelo.recortar(m, "t2", 2).tabelas) == {"t2", "t3", "t1"}
    assert set(modelo.recortar(m, "t2", 2).ligacoes) == {"k1", "k2"}


def test_copia_e_profunda():
    m = modelo_png()
    c = m.copia()
    c.tabelas["t1"].colunas[0].nome = "outro"
    assert m.tabelas["t1"].colunas[0].nome == "id"


def test_coluna_pk_e_por_nome():
    m = modelo_png()
    assert [x.nome for x in m.tabelas["t3"].pk()] == ["user_id", "role_id"]
    assert m.por_nome("roles").id == "t2" and m.por_nome("nada") is None
    assert m.tabelas["t1"].coluna("t1.c9") is None
