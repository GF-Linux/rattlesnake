"""Testes do canvas: o modelo puro e, nas tarefas seguintes, a tela."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from ratt.db import modelo, rascunho
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


# ── o rascunho: operações e SQL ──

def test_ident():
    assert rascunho.ident("users") == "users"
    assert rascunho.ident("user") == '"user"'          # reservada
    assert rascunho.ident("order") == '"order"'
    assert rascunho.ident("Nome") == '"Nome"'
    assert rascunho.ident("minha tabela") == '"minha tabela"'
    assert rascunho.ident('a"b') == '"a""b"'
    assert rascunho.ident("1abc") == '"1abc"'
    assert rascunho.ident("ação") == '"ação"'


def test_nome_tabela_fora_do_public():
    t = modelo.Tabela("t9", "vendas", "Pedidos", [])
    assert rascunho.nome_tabela(t) == 'vendas."Pedidos"'


def test_adicionar_coluna_sql_e_vista():
    r = rascunho.Rascunho(modelo_png())
    c = modelo.Coluna(r.novo_id("c"), "birth", "date")
    r.acrescentar(rascunho.AdicionarColuna("t1", c))
    assert r.sql_texto().splitlines()[:2] == ["-- 1. " + r.operacoes[0].descricao(modelo_png()),
                                             "ALTER TABLE users ADD COLUMN birth date;"]
    assert r.vista().tabelas["t1"].coluna(c.id).estado == "novo"


def test_ligar_com_unica_e_indice():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.ApagarLigacao("k3"))
    lig = modelo.Ligacao(r.novo_id("k"), "t4", ("t4.c4",), "t1", ("t1.c1",), "profiles_user_id_fkey", "CASCADE")
    r.acrescentar(rascunho.Ligar(lig, unica=True, indice=True))
    sql = r.sql_texto()
    assert "ALTER TABLE profiles DROP CONSTRAINT profiles_user_id_fkey;" in sql
    assert ("ALTER TABLE profiles ADD CONSTRAINT profiles_user_id_fkey FOREIGN KEY (user_id) "
            "REFERENCES users (id) ON DELETE CASCADE;") in sql
    assert "ALTER TABLE profiles ADD CONSTRAINT profiles_user_id_key UNIQUE (user_id);" in sql
    assert "CREATE INDEX profiles_user_id_idx ON profiles (user_id);" in sql
    assert modelo.cardinalidade(r.modelo(), r.modelo().ligacoes[lig.id]) == "1:1"


def test_criar_tabela_com_identidade():
    r = rascunho.Rascunho(modelo_png())
    tid = r.novo_id("t")
    t = modelo.Tabela(tid, "public", "pedidos", [
        modelo.Coluna(f"{tid}.c1", "id", "bigint", obrigatoria=True, identidade=True, pk=True),
        modelo.Coluna(f"{tid}.c2", "total", "numeric", obrigatoria=True, padrao="0")])
    r.acrescentar(rascunho.CriarTabela(t))
    assert ("CREATE TABLE pedidos (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
            "total numeric NOT NULL DEFAULT 0);") in r.sql_texto()


def test_ligar_nn():
    r = rascunho.Rascunho(modelo_png())
    op = rascunho.LigarNN(r.novo_id("t"), "users_tags", "t1", "t2",
                          (r.novo_id("c"), r.novo_id("c")), (r.novo_id("k"), r.novo_id("k")))
    r.acrescentar(op)
    sql = r.sql_texto()
    assert "CREATE TABLE users_tags (user_id bigint NOT NULL, role_id bigint NOT NULL, PRIMARY KEY (user_id, role_id));" in sql
    assert modelo.juncao(r.modelo(), op.tabela_id)


def test_renomear_vale_para_as_seguintes():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    r.acrescentar(rascunho.Obrigatoria("t1", "t1.c2", True))
    assert "ALTER TABLE users RENAME TO pessoas;" in r.sql_texto()
    assert "ALTER TABLE pessoas ALTER COLUMN first_name SET NOT NULL;" in r.sql_texto()


def test_apagar_tabela_marca_ligacoes():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.ApagarTabela("t1", cascata=True))
    v = r.vista()
    assert v.tabelas["t1"].estado == "apagado" and v.ligacoes["k3"].estado == "apagado"
    assert "t1" not in r.modelo().tabelas
    assert "DROP TABLE users CASCADE;" in r.sql_texto()


def test_nao_aceita_operacao_sobre_apagado():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.ApagarTabela("t2"))
    with pytest.raises(ValueError):
        r.acrescentar(rascunho.Renomear("t2", None, "papeis"))


def test_operacao_da_linha():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    r.acrescentar(rascunho.Obrigatoria("t1", "t1.c2", True))
    linhas = r.linhas_das_operacoes()
    assert r.operacao_da_linha(linhas[1] + 1) == 1


def test_nomes_hostis_no_sql():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t1", None, "user"))
    r.acrescentar(rascunho.AdicionarColuna("t1", modelo.Coluna(r.novo_id("c"), "Data Nasc", "date")))
    assert 'ALTER TABLE "user" ADD COLUMN "Data Nasc" date;' in r.sql_texto()


def test_ident_quebra_de_linha_e_vazio():
    assert rascunho.ident("abc\n") == '"abc\n"'
    with pytest.raises(ValueError):
        rascunho.ident("")
    assert not rascunho.tipo_valido("text\n")


def test_descricao_com_quebra_de_linha_fica_em_um_comentario():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t1", None, "x\nDROP TABLE users"))
    r.acrescentar(rascunho.AdicionarColuna("t1", modelo.Coluna(r.novo_id("c"), "a\u2028b\x0bc", "date")))
    linhas = r.sql_texto().split("\n")
    # o comentário é uma linha só; a quebra no SQL fica dentro do identificador entre aspas
    assert linhas[0] == "-- 1. renomear a tabela users para x DROP TABLE users"
    assert linhas[1] == 'ALTER TABLE users RENAME TO "x'
    assert linhas[2] == 'DROP TABLE users";'
    assert linhas[r.linhas_das_operacoes()[1] - 2] == ""
    assert '"a\u2028b\x0bc"' in r.sql_texto()


def test_nome_vazio_e_recusado_sem_entrar_no_rascunho():
    r = rascunho.Rascunho(modelo_png())
    with pytest.raises(ValueError):
        r.acrescentar(rascunho.Renomear("t1", None, ""))
    assert r.operacoes == []
    assert r.sql_texto() == ""


def test_cabecalho_vira_uma_linha_de_comentario():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    texto = r.sql_texto("x\nDROP TABLE users\u2028y\x0bz")
    linhas = texto.split("\n")
    assert linhas[0] == "-- x DROP TABLE users y z"
    assert linhas[1].startswith("-- 1.")
    assert r.linhas_das_operacoes("x\nDROP") == [2]
    assert r.sql_texto("-- ok").split("\n")[0] == "-- ok"


# ── Task 3: dependências, banco que mudou, disco ──

import json

from ratt.db import guardar


def test_dependentes_e_tirar():
    r = rascunho.Rascunho(modelo_png())
    tid = r.novo_id("t")
    r.acrescentar(rascunho.CriarTabela(modelo.Tabela(tid, "public", "tags", [
        modelo.Coluna(f"{tid}.c1", "id", "bigint", obrigatoria=True, identidade=True, pk=True)])))
    cid = r.novo_id("c")
    r.acrescentar(rascunho.AdicionarColuna(tid, modelo.Coluna(cid, "nome", "text")))
    r.acrescentar(rascunho.Obrigatoria(tid, cid, True))
    r.acrescentar(rascunho.Renomear("t1", None, "pessoas"))           # independente
    assert r.dependentes(0) == [1, 2]
    r.tirar([0] + r.dependentes(0))
    assert len(r.operacoes) == 1 and "RENAME TO pessoas" in r.sql_texto()


def test_rebase_marca_invalidas_sem_perder():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t2", None, "papeis"))
    r.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    nova = modelo_png(); del nova.tabelas["t2"]                       # alguém apagou roles pelo psql
    assert r.rebase(nova) == {0}
    assert r.invalidas == {0}
    assert len(r.operacoes) == 2 and "papeis" not in r.sql_texto() and "pessoas" in r.sql_texto()
    assert "-- 2. " in r.sql_texto() and r.linhas_das_operacoes()[0] == 0
    assert r.operacao_da_linha(1) == 1


def test_json_ida_e_volta():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.AdicionarColuna("t1", modelo.Coluna(r.novo_id("c"), "birth", "date")))
    de_novo = rascunho.Rascunho.de_dict(json.loads(json.dumps(r.para_dict())), modelo_png())
    assert de_novo.sql_texto() == r.sql_texto() and de_novo.novo_id("c") == "novo-c2"


def test_json_ida_e_volta_de_todas_as_operacoes():
    r = rascunho.Rascunho(modelo_png())
    tid = r.novo_id("t")
    r.acrescentar(rascunho.CriarTabela(modelo.Tabela(tid, "public", "tags", [
        modelo.Coluna(f"{tid}.c1", "id", "bigint", obrigatoria=True, identidade=True, pk=True)])))
    r.acrescentar(rascunho.Ligar(modelo.Ligacao(r.novo_id("k"), "t1", ("t1.c2",), "t2", ("t2.c1",), "",
                                                "CASCADE"), unica=True))
    r.acrescentar(rascunho.LigarNN(r.novo_id("t"), "users_tags", "t1", tid,
                                   (r.novo_id("c"), r.novo_id("c")), (r.novo_id("k"), r.novo_id("k"))))
    r.acrescentar(rascunho.Unica("t1", "t1.c4", True))
    r.acrescentar(rascunho.ApagarTabela("t2", True))
    de_novo = rascunho.Rascunho.de_dict(json.loads(json.dumps(r.para_dict())), modelo_png())
    assert de_novo.operacoes == r.operacoes
    assert de_novo.sql_texto() == r.sql_texto()


def test_json_a_mao_nao_injeta_sql():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Ligar(modelo.Ligacao("novo-k1", "t1", ("t1.c2",), "t2", ("t2.c1",), "")))
    d = json.loads(json.dumps(r.para_dict()))
    d["operacoes"][0]["ligacao"]["ao_apagar"] = "CASCADE; DROP TABLE users"
    de_novo = rascunho.Rascunho.de_dict(d, modelo_png())
    assert de_novo.invalidas == {0}
    assert "DROP TABLE" not in de_novo.sql_texto()
    r2 = rascunho.Rascunho(modelo_png())
    r2.acrescentar(rascunho.AdicionarColuna("t1", modelo.Coluna("novo-c1", "x", "text")))
    d = r2.para_dict()
    d["operacoes"][0]["coluna"]["tipo"] = "text); DROP TABLE users; --"
    assert rascunho.Rascunho.de_dict(d, modelo_png()).invalidas == {0}


def test_guardar(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    guardar.gravar_posicoes(5432, "estudos", "public", {"t1": (3, 4)})
    assert guardar.ler_posicoes(5432, "estudos", "public") == {"t1": (3, 4)}
    guardar.gravar_rascunho(5432, "estudos", "public", {"a": 1})
    assert guardar.ler_rascunho(5432, "estudos", "public") == {"a": 1}
    guardar.gravar_rascunho(5432, "estudos", "public", None)
    assert guardar.ler_rascunho(5432, "estudos", "public") is None
    assert guardar.guardar_aplicado(5432, "estudos", "SELECT 1;").read_text() == "SELECT 1;"
    assert not list(tmp_path.rglob("*.tmp"))


def test_rascunho_corrompido_levanta_so_rascunhocorrompido():
    base = modelo_png()
    ruins = [
        {"operacoes": [{"tipo": "inexistente"}]},
        {"operacoes": [{"tipo": "renomear", "tabela": "t1"}]},
        {"operacoes": [{"tipo": "renomear", "tabela": "t1", "coluna": None, "novo": "x", "extra": 1}]},
        {"operacoes": "x"},
        {"operacoes": ["x"]},
        {"operacoes": [{"tipo": "adicionar_coluna", "tabela": "t1", "coluna": "x"}]},
        {"operacoes": [], "proximo": "abc"},
        [],
    ]
    for d in ruins:
        with pytest.raises(rascunho.RascunhoCorrompido):
            rascunho.Rascunho.de_dict(d, base)


# ── Task 5: checagens e avisos ──

from ratt.db import checagem


class ConsultaFalsa:
    def __init__(self, **respostas):
        self.r = respostas
        self.chamadas = []

    def __getattr__(self, nome):
        def f(*args):
            self.chamadas.append((nome, args))
            return self.r.get(nome, 0)
        return f


def test_avisos_do_modelo_png():
    chaves = {(a.alvo, a.chave) for a in checagem.avisos_do_modelo(modelo_png())}
    assert ("k3", "chk.fk_opcional") in chaves and ("k3", "chk.fk_sem_indice") in chaves
    assert not any(c == "chk.sem_pk" for _, c in chaves)


def test_sem_pk_avisa_e_texto_existe_nos_dois_idiomas():
    m = modelo_png()
    m.tabelas["t2"].colunas[0].pk = False
    avisos = [a for a in checagem.avisos_do_modelo(m) if a.chave == "chk.sem_pk"]
    assert [(a.alvo, a.nivel) for a in avisos] == [("t2", "atencao")]
    assert "roles" in avisos[0].texto()


def test_obrigatoria_com_nulos_impede():
    r = rascunho.Rascunho(modelo_png())
    avisos = checagem.avisos_da_operacao(rascunho.Obrigatoria("t1", "t1.c2", True), r, ConsultaFalsa(nulos=3))
    assert [(a.nivel, a.chave, a.valores["n"]) for a in avisos] == [("impede", "chk.nulos_impedem", 3)]
    assert avisos[0].texto()


def test_coluna_do_rascunho_e_deduzida_sem_consultar():
    r = rascunho.Rascunho(modelo_png())
    c = modelo.Coluna(r.novo_id("c"), "apelido", "text")
    r.acrescentar(rascunho.AdicionarColuna("t1", c))
    falsa = ConsultaFalsa(linhas=10)
    avisos = checagem.avisos_da_operacao(rascunho.Obrigatoria("t1", c.id, True), r, falsa)
    assert avisos[0].chave == "chk.nulos_impedem" and avisos[0].valores["n"] == 10
    assert all(nome != "nulos" for nome, _ in falsa.chamadas)


def test_usa_o_nome_da_base_depois_de_renomear():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    falsa = ConsultaFalsa()
    checagem.avisos_da_operacao(rascunho.Unica("t1", "t1.c4", True), r, falsa)
    assert falsa.chamadas[0] == ("repetidos", ("public", "users", "email"))


def test_sem_tempo():
    r = rascunho.Rascunho(modelo_png())
    avisos = checagem.avisos_da_operacao(rascunho.Unica("t1", "t1.c4", True), r, ConsultaFalsa(repetidos=None))
    assert avisos[0].chave == "chk.sem_tempo"


def test_tipos_incompativeis():
    r = rascunho.Rascunho(modelo_png())
    lig = modelo.Ligacao(r.novo_id("k"), "t4", ("t4.c2",), "t1", ("t1.c1",), "x")     # bio (text) → users.id
    assert any(a.chave == "chk.tipos" for a in checagem.avisos_da_operacao(rascunho.Ligar(lig), r, ConsultaFalsa()))


def test_tipos_equivalentes_nao_avisam_e_orfaos_impedem():
    r = rascunho.Rascunho(modelo_png())
    r.base.tabelas["t4"].coluna("t4.c4").tipo = "int8"
    lig = modelo.Ligacao(r.novo_id("k"), "t4", ("t4.c4",), "t1", ("t1.c1",), "x", )
    avisos = checagem.avisos_da_operacao(rascunho.Ligar(lig, unica=True), r, ConsultaFalsa(orfaos=2))
    assert [a.chave for a in avisos] == ["chk.orfaos_impedem", "chk.um_para_um"]
    assert avisos[0].valores["n"] == 2


def test_obrigatoria_sem_padrao_em_tabela_com_linhas():
    r = rascunho.Rascunho(modelo_png())
    c = modelo.Coluna(r.novo_id("c"), "x", "int", obrigatoria=True)
    avisos = checagem.avisos_da_operacao(rascunho.AdicionarColuna("t1", c), r, ConsultaFalsa(linhas=5))
    assert [(a.nivel, a.chave) for a in avisos] == [("impede", "chk.obrigatoria_sem_padrao")]
    assert checagem.avisos_da_operacao(rascunho.AdicionarColuna("t1", c), r, ConsultaFalsa(linhas=0)) == []


def test_tabela_nova_nao_consulta():
    r = rascunho.Rascunho(modelo_png())
    nova = modelo.Tabela(r.novo_id("t"), "public", "nova", [modelo.Coluna(r.novo_id("c"), "id", "int")])
    r.acrescentar(rascunho.CriarTabela(nova))
    falsa = ConsultaFalsa(nulos=9, linhas=9)
    assert checagem.avisos_da_operacao(rascunho.Obrigatoria(nova.id, nova.colunas[0].id, True), r, falsa) == []
    assert checagem.avisos_da_operacao(rascunho.ApagarTabela(nova.id), r, falsa) == []
    assert falsa.chamadas == []


def test_apagar_tabela_com_dependentes_pede_cascata():
    r = rascunho.Rascunho(modelo_png())
    falsa = ConsultaFalsa(dependentes=[checagem.Dependente("fk", 4, "profiles", 3, "x")], linhas=4)
    avisos = checagem.avisos_da_operacao(rascunho.ApagarTabela("t1"), r, falsa)
    assert [(a.nivel, a.chave) for a in avisos] == [
        ("atencao", "chk.dependentes"), ("impede", "chk.precisa_cascata"), ("atencao", "chk.linhas_somem")]
    assert ("dependentes", (1, None)) in falsa.chamadas
    avisos = checagem.avisos_da_operacao(rascunho.ApagarTabela("t1", cascata=True), r, falsa)
    assert [a.chave for a in avisos] == ["chk.dependentes", "chk.linhas_somem"]


def test_apagar_coluna_consulta_pelo_attnum():
    r = rascunho.Rascunho(modelo_png())
    falsa = ConsultaFalsa()
    assert checagem.avisos_da_operacao(rascunho.ApagarColuna("t1", "t1.c4"), r, falsa) == []
    assert ("dependentes", (1, 4)) in falsa.chamadas


def _fk_profiles(**kw):
    return checagem.Dependente("fk", 4, "profiles", 3, "profiles_user_id_fkey")


def test_dependente_tem_texto_nos_dois_idiomas(monkeypatch):
    monkeypatch.setenv("RATT_LANG", "en")
    assert checagem.Dependente("fk", 4, "profiles", 3, "f").texto() == "table profiles (foreign key f)"
    monkeypatch.setenv("RATT_LANG", "pt")
    assert checagem.Dependente("fk", 4, "profiles", 3, "f").texto() == "tabela profiles (chave estrangeira f)"
    assert checagem.Dependente("view", 9, "v_x").texto() == "view v_x"


def test_dependentes_com_tempo_esgotado_avisa_sem_tempo():
    r = rascunho.Rascunho(modelo_png())
    avisos = checagem.avisos_da_operacao(rascunho.ApagarColuna("t1", "t1.c4"), r, ConsultaFalsa(dependentes=None))
    assert [a.chave for a in avisos] == ["chk.sem_tempo"]


def test_dependente_ja_apagado_no_rascunho_nao_conta():
    base = modelo_png()
    falsa = ConsultaFalsa(dependentes=[_fk_profiles()])
    # profiles inteira apagada antes
    r = rascunho.Rascunho(base)
    r.acrescentar(rascunho.ApagarTabela("t4"))
    assert [a.chave for a in checagem.avisos_da_operacao(rascunho.ApagarTabela("t1"), r, falsa)] == []
    # só a ligação apagada
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.ApagarLigacao("k3"))
    assert [a.chave for a in checagem.avisos_da_operacao(rascunho.ApagarTabela("t1"), r, falsa)] == []
    # nada apagado: conta
    r = rascunho.Rascunho(modelo_png())
    assert "chk.precisa_cascata" in [a.chave for a in checagem.avisos_da_operacao(rascunho.ApagarTabela("t1"), r, falsa)]


def test_coluna_nova_sem_padrao_nao_tem_repetidos_nem_orfaos():
    r = rascunho.Rascunho(modelo_png())
    c = modelo.Coluna(r.novo_id("c"), "ref", "bigint")
    r.acrescentar(rascunho.AdicionarColuna("t4", c))
    falsa = ConsultaFalsa(linhas=10, repetidos=5, orfaos=5)
    assert checagem.avisos_da_operacao(rascunho.Unica("t4", c.id, True), r, falsa) == []
    lig = modelo.Ligacao(r.novo_id("k"), "t4", (c.id,), "t1", ("t1.c1",), "x")
    assert checagem.avisos_da_operacao(rascunho.Ligar(lig), r, falsa) == []
    assert all(nome not in ("repetidos", "orfaos") for nome, _ in falsa.chamadas)


def test_coluna_nova_com_padrao_deduz_repetidos():
    r = rascunho.Rascunho(modelo_png())
    c = modelo.Coluna(r.novo_id("c"), "ref", "bigint", padrao="0")
    r.acrescentar(rascunho.AdicionarColuna("t4", c))
    mais = checagem.avisos_da_operacao(rascunho.Unica("t4", c.id, True), r, ConsultaFalsa(linhas=2))
    assert [(a.chave, a.valores["n"]) for a in mais] == [("chk.repetidos_impedem", 1)]
    uma = checagem.avisos_da_operacao(rascunho.Unica("t4", c.id, True), r, ConsultaFalsa(linhas=1))
    assert uma == []
    lig = modelo.Ligacao(r.novo_id("k"), "t4", (c.id,), "t1", ("t1.c1",), "x")
    assert [a.chave for a in checagem.avisos_da_operacao(rascunho.Ligar(lig), r, ConsultaFalsa())] == ["chk.indeterminado"]
