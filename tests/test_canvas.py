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


def test_nome_tabela_sempre_com_o_schema():
    t = modelo.Tabela("t9", "vendas", "Pedidos", [])
    assert rascunho.nome_tabela(t) == 'vendas."Pedidos"'
    assert rascunho.nome_tabela(modelo.Tabela("t1", "public", "users", [])) == "public.users"
    assert rascunho.nome_tabela(modelo.Tabela("t2", "public", "user", [])) == 'public."user"'


def test_adicionar_coluna_sql_e_vista():
    r = rascunho.Rascunho(modelo_png())
    c = modelo.Coluna(r.novo_id("c"), "birth", "date")
    r.acrescentar(rascunho.AdicionarColuna("t1", c))
    assert r.sql_texto().splitlines()[:2] == ["-- 1. " + r.operacoes[0].descricao(modelo_png()),
                                             "ALTER TABLE public.users ADD COLUMN birth date;"]
    assert r.vista().tabelas["t1"].coluna(c.id).estado == "novo"


def test_ligar_com_unica_e_indice():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.ApagarLigacao("k3"))
    lig = modelo.Ligacao(r.novo_id("k"), "t4", ("t4.c4",), "t1", ("t1.c1",), "profiles_user_id_fkey", "CASCADE")
    r.acrescentar(rascunho.Ligar(lig, unica=True, indice=True))
    sql = r.sql_texto()
    assert "ALTER TABLE public.profiles DROP CONSTRAINT profiles_user_id_fkey;" in sql
    assert ("ALTER TABLE public.profiles ADD CONSTRAINT profiles_user_id_fkey FOREIGN KEY (user_id) "
            "REFERENCES public.users (id) ON DELETE CASCADE;") in sql
    assert "ALTER TABLE public.profiles ADD CONSTRAINT profiles_user_id_key UNIQUE (user_id);" in sql
    assert "CREATE INDEX profiles_user_id_idx ON public.profiles (user_id);" in sql
    assert modelo.cardinalidade(r.modelo(), r.modelo().ligacoes[lig.id]) == "1:1"


def test_criar_tabela_com_identidade():
    r = rascunho.Rascunho(modelo_png())
    tid = r.novo_id("t")
    t = modelo.Tabela(tid, "public", "pedidos", [
        modelo.Coluna(f"{tid}.c1", "id", "bigint", obrigatoria=True, identidade=True, pk=True),
        modelo.Coluna(f"{tid}.c2", "total", "numeric", obrigatoria=True, padrao="0")])
    r.acrescentar(rascunho.CriarTabela(t))
    assert ("CREATE TABLE public.pedidos (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
            "total numeric NOT NULL DEFAULT 0);") in r.sql_texto()


def test_ligar_nn():
    r = rascunho.Rascunho(modelo_png())
    op = rascunho.LigarNN(r.novo_id("t"), "users_tags", "t1", "t2",
                          (r.novo_id("c"), r.novo_id("c")), (r.novo_id("k"), r.novo_id("k")))
    r.acrescentar(op)
    sql = r.sql_texto()
    assert "CREATE TABLE public.users_tags (user_id bigint NOT NULL, role_id bigint NOT NULL, PRIMARY KEY (user_id, role_id));" in sql
    assert modelo.juncao(r.modelo(), op.tabela_id)


def test_renomear_vale_para_as_seguintes():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    r.acrescentar(rascunho.Obrigatoria("t1", "t1.c2", True))
    assert "ALTER TABLE public.users RENAME TO pessoas;" in r.sql_texto()
    assert "ALTER TABLE public.pessoas ALTER COLUMN first_name SET NOT NULL;" in r.sql_texto()


def test_apagar_tabela_marca_ligacoes():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.ApagarTabela("t1", cascata=True))
    v = r.vista()
    assert v.tabelas["t1"].estado == "apagado" and v.ligacoes["k3"].estado == "apagado"
    assert "t1" not in r.modelo().tabelas
    assert "DROP TABLE public.users CASCADE;" in r.sql_texto()


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
    assert 'ALTER TABLE public."user" ADD COLUMN "Data Nasc" date;' in r.sql_texto()


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
    assert linhas[1] == 'ALTER TABLE public.users RENAME TO "x'
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


# ---- Task 6: o desenhista ----------------------------------------------------------------------------------
from ratt.db import desenho


def test_caixa_do_users():
    m = modelo_png()
    d = desenho.desenhar(m, {"t1": (0, 0)})
    assert d.texto().splitlines()[:3] == [
        "┌─ users ───────────────┐",
        "│ PK id          bigint │",
        "│    first_name    text │",
    ]


def test_estado_do_rascunho_no_desenho():
    r = rascunho.Rascunho(modelo_png())
    c = modelo.Coluna(r.novo_id("c"), "birth", "date")
    r.acrescentar(rascunho.AdicionarColuna("t1", c))
    d = desenho.desenhar(r.vista(), {"t1": (0, 0)})
    linha = d.texto().splitlines()[6]
    assert linha.startswith("│+   birth") and d.papel_em(5, 6) == "novo"


def test_posicionar_sem_sobrepor():
    m = modelo_png()
    pos = desenho.posicionar(m, {})
    caixas = [(x, y, *desenho.tamanho_caixa(m.tabelas[i])) for i, (x, y) in pos.items()]
    for i, a in enumerate(caixas):
        for b in caixas[i + 1:]:
            assert a[0] + a[2] + 1 <= b[0] or b[0] + b[2] + 1 <= a[0] or a[1] + a[3] + 1 <= b[1] or b[1] + b[3] + 1 <= a[1]
    assert pos["t1"][0] < pos["t3"][0]          # users (referenciada) à esquerda de users_roles


def test_posicao_guardada_e_respeitada():
    pos = desenho.posicionar(modelo_png(), {"t2": (100, 50)})
    assert pos["t2"] == (100, 50)


def test_onde():
    d = desenho.desenhar(modelo_png(), {"t1": (0, 0)})
    assert d.onde(3, 0) == ("tabela", "t1")
    assert d.onde(5, 2) == ("coluna", "t1", "t1.c2")
    assert d.onde(60, 30) is None


def test_tabela_larga_e_nomes_longos():
    cols = [modelo.Coluna(f"t9.c{i}", f"coluna_numero_{i}", "timestamp with time zone") for i in range(30)]
    cols.append(modelo.Coluna("t9.c99", "n" * 60, "character varying(255)"))
    t = modelo.Tabela("t9", "public", "x" * 60, cols)
    m = modelo.Modelo("public", {"t9": t}, {})
    w, h = desenho.tamanho_caixa(t)
    assert w == desenho.LARGURA_MAX and h == 33
    d = desenho.desenhar(m, {"t9": (0, 0)})
    assert all(len(l) <= desenho.LARGURA_MAX for l in d.texto().splitlines())
    assert "timestamptz" in d.texto() and "…" in d.texto()
    assert d.onde(5, 31) == ("coluna", "t9", "t9.c99")


def test_schema_vazio_desenha_nada():
    d = desenho.desenhar(modelo.Modelo("public", {}, {}), {})
    assert d.texto() == "" and d.onde(0, 0) is None


def test_aviso_particoes_selecao_e_fora_da_grade():
    m = modelo_png()
    m.tabelas["t1"].particoes = 55
    d = desenho.desenhar(m, {"t1": (2, 1)}, avisos={"t1": "aviso", "t1.c2": "aviso"}, selecao=("coluna", "t1", "t1.c2"))
    linhas = d.texto().splitlines()
    assert "▤55" in linhas[1] and linhas[1].endswith("⚠")
    assert linhas[3].endswith("⚠") and d.papel_em(6, 3) == "selecao"
    x, y, w, h = d.caixas["t1"]
    assert d.papel_em(x + w, 1) == "aviso" and d.largura == x + w + 1
    assert d.trechos(-1) == [] and d.trechos(99) == [] and d.papel_em(-1, 0) == "" and d.onde(-1, -1) is None


def _sem_sobreposicao(m, pos):
    caixas = [(x, y, *desenho.tamanho_caixa(m.tabelas[i])) for i, (x, y) in pos.items()]
    for i, a in enumerate(caixas):
        for b in caixas[i + 1:]:
            assert not desenho._colide(a, b)


def test_posicionar_ciclo_e_auto_referencia():
    def tab(i, nome):
        return modelo.Tabela(i, "public", nome, [modelo.Coluna(i + ".c1", "id", "bigint", pk=True),
                                                  modelo.Coluna(i + ".c2", "outra", "bigint")])
    m = modelo.Modelo("public", {"a": tab("a", "a"), "b": tab("b", "b"), "c": tab("c", "c")}, {
        "k1": Ligacao("k1", "a", ("a.c2",), "b", ("b.c1",), "a_b"),
        "k2": Ligacao("k2", "b", ("b.c2",), "a", ("a.c1",), "b_a"),
        "k3": Ligacao("k3", "c", ("c.c2",), "c", ("c.c1",), "c_c"),
    })
    pos = desenho.posicionar(m, {})
    assert set(pos) == {"a", "b", "c"}
    _sem_sobreposicao(m, pos)


def test_guardada_empurra_automatica_para_baixo():
    m = modelo_png()
    livre = desenho.posicionar(m, {})
    pos = desenho.posicionar(m, {"t3": livre["t2"]})   # t3 guardada em cima de onde t2 cairia
    assert pos["t3"] == livre["t2"]
    assert pos["t2"][1] > livre["t2"][1] or pos["t2"][0] != livre["t2"][0]
    caixas = {i: (x, y, *desenho.tamanho_caixa(m.tabelas[i])) for i, (x, y) in pos.items()}
    assert not desenho._colide(caixas["t2"], caixas["t3"])


def test_estado_apagado_no_desenho():
    m = modelo_png()
    m.tabelas["t1"].colunas[1].estado = "apagado"
    d = desenho.desenhar(m, {"t1": (0, 0)})
    assert d.texto().splitlines()[2].startswith("│−") and d.papel_em(1, 2) == "apagado"
    assert d.papel_em(5, 2) == "apagado" and d.papel_em(0, 2) == "borda"


def test_corte_no_nome_e_tipo_inteiro_e_cliques_na_tabela_larga():
    t = modelo.Tabela("t9", "public", "x" * 60, [modelo.Coluna("t9.c1", "n" * 60, "character varying(255)")])
    m = modelo.Modelo("public", {"t9": t}, {})
    d = desenho.desenhar(m, {"t9": (0, 0)})
    linhas = d.texto().splitlines()
    assert linhas[1].endswith("varchar(255) │") and "…" in linhas[1] and "n" * 30 not in linhas[1]
    assert linhas[1].index("…") < linhas[1].index("varchar")
    assert "…" in linhas[0] and linhas[0].endswith("┐")
    assert d.onde(10, 0) == ("tabela", "t9")
    assert d.onde(desenho.LARGURA_MAX - 1, 1) == ("coluna", "t9", "t9.c1")
    assert d.onde(desenho.LARGURA_MAX, 1) is None


def test_vazio_tem_tamanho_zero():
    d = desenho.desenhar(modelo.Modelo("public", {}, {}), {})
    assert d.largura == 0 and d.altura == 0 and d.trechos(0) == []


# ---- Task 7: setas e pé de galinha -------------------------------------------------------------------------

def _posicoes_png():
    return {"t1": (0, 0), "t3": (34, 0), "t2": (68, 0), "t4": (34, 8)}


def test_pe_de_galinha_users_para_users_roles():
    m = modelo_png()
    linhas = desenho.desenhar(m, _posicoes_png()).texto().splitlines()
    assert "├┼" in linhas[1] and "<┤" in linhas[1]          # users.id ──< users_roles.user_id


def test_ligacao_opcional_tem_o():
    m = modelo_png()
    del m.ligacoes["k1"], m.ligacoes["k2"]                  # sem outra seta na mesma célula da borda
    texto = desenho.desenhar(m, _posicoes_png()).texto()
    assert "├┼o" in texto or "o┼┤" in texto                 # profiles.user_id aceita NULL


def test_um_para_um():
    m = modelo_png(); m.tabelas["t4"].coluna("t4.c4").unica = True
    texto = desenho.desenhar(m, _posicoes_png()).texto()
    assert "<┤" in texto.splitlines()[1]                    # a ligação 1:N continua
    d = desenho.desenhar(m, _posicoes_png())
    xp, yp, _, _ = d.caixas["t4"]
    assert d.texto().splitlines()[yp + 4][xp - 1] == "┼"    # origem 1:1 (profiles.user_id) é "um"
    assert d.texto().splitlines()[1][d.caixas["t1"][2]] == "┼"  # e users.id continua "um"


def test_seta_nao_atravessa_caixa():
    m = modelo_png()
    d = desenho.desenhar(m, _posicoes_png())
    for tid, (x, y, w, h) in d.caixas.items():
        for yy in range(y + 1, y + h - 1):
            for xx in range(x + 1, x + w - 1):
                assert d.papel_em(xx, yy) not in ("seta", "marca")


def test_auto_referencia():
    t = modelo.Tabela("t1", "public", "employees", [
        modelo.Coluna("t1.c1", "id", "int8", True, pk=True), modelo.Coluna("t1.c2", "reports_to", "int8")])
    m = modelo.Modelo("public", {"t1": t}, {"k1": modelo.Ligacao("k1", "t1", ("t1.c2",), "t1", ("t1.c1",), "f")})
    d = desenho.desenhar(m, {"t1": (0, 0)})
    assert d.onde(d.caixas["t1"][2] + 1, 1) == ("ligacao", "k1")


def test_recolhe_tabela_centro():
    m = modelo_png()
    texto = desenho.desenhar(m, _posicoes_png(), limite_chegando=1).texto()
    assert "←2" in texto and "<┤" not in texto
    assert "<┤" in desenho.desenhar(m, _posicoes_png(), limite_chegando=1, expandidas={"t1"}).texto()


def test_fk_composta_uma_seta():
    pai = modelo.Tabela("t1", "public", "pai", [modelo.Coluna("t1.c1", "a", "int8", True, pk=True),
                                                 modelo.Coluna("t1.c2", "b", "int8", True, pk=True)])
    filho = modelo.Tabela("t2", "public", "filho", [modelo.Coluna("t2.c1", "a", "int8", True),
                                                     modelo.Coluna("t2.c2", "b", "int8", True)])
    m = modelo.Modelo("public", {"t1": pai, "t2": filho},
                      {"k1": modelo.Ligacao("k1", "t2", ("t2.c1", "t2.c2"), "t1", ("t1.c1", "t1.c2"), "f")})
    texto = desenho.desenhar(m, {"t1": (0, 0), "t2": (30, 0)}).texto()
    assert texto.count("<┤") == 1 and "⋯┤" in texto


def test_rotear_desvia():
    bloqueadas = {(5, y) for y in range(0, 9)}
    caminho = desenho.rotear((0, 4), (10, 4), bloqueadas, (0, 0, 20, 12))
    assert caminho[0] == (0, 4) and caminho[-1] == (10, 4) and not (set(caminho) & bloqueadas)


def test_rotear_sem_caminho_nao_falha():
    parede = {(5, y) for y in range(0, 13)}
    caminho = desenho.rotear((0, 4), (10, 4), parede, (0, 0, 20, 12))
    assert caminho[0] == (0, 4) and caminho[-1] == (10, 4)


def test_ligacao_com_tabela_ausente_e_ignorada_e_estado_vira_papel():
    m = modelo_png()
    m.ligacoes["k9"] = modelo.Ligacao("k9", "t3", ("t3.c1",), "t99", ("t99.c1",), "x")
    desenho.desenhar(m, _posicoes_png())  # não levanta KeyError
    m.ligacoes["k3"].estado = "novo"
    d = desenho.desenhar(m, _posicoes_png())
    papeis = {d.papel_em(x, y) for (x, y), a in d._alvos.items() if a == ("ligacao", "k3")}
    assert "novo" in papeis and "seta" not in {d.papel_em(x, y) for (x, y), a in d._alvos.items()
                                               if a == ("ligacao", "k3")}


def test_largura_e_altura_incluem_setas():
    d = desenho.desenhar(modelo_png(), _posicoes_png())
    for (x, y) in d._celulas:
        assert x < d.largura and y < d.altura


PNG_DESENHADO = """\
┌─ users ───────────────┐         ┌─ users_roles ─────┐             ┌─ roles ──────┐
│ PK id          bigint ├┼┬──────<┤ PK user_id bigint │           ┌┼┤ PK id bigint │
│    first_name    text │ o       │ PK role_id bigint ├>──────────┘ │    name text │
│    last_name     text │ │       └───────────────────┘             └──────────────┘
│    email         text │ │
│    password_hash text │ │
└───────────────────────┘ │
                          │
                          │       ┌─ profiles ──────────┐
                          │       │ PK id        bigint │
                          │       │    bio         text │
                          │       │    description text │
                          └──────<┤    user_id   bigint │
                                  └─────────────────────┘
"""


def test_pontas_na_mesma_borda_tem_juncao_e_o_proprio():
    d = desenho.desenhar(modelo_png(), _posicoes_png())
    linhas = d.texto().splitlines()
    x = d.caixas["t1"][2]                                   # a célula da borda direita de users é x - 1
    assert linhas[1][x - 1:x + 2] == "├┼┬"                  # borda, "um" dividido, junção (nunca ┼)
    # (a) a seta que continua depois da junção é a certa: k1 para a direita, k3 para baixo
    assert d.onde(x + 2, 1) == ("ligacao", "k1")
    assert d.onde(x + 1, 2) == ("ligacao", "k3")
    # (b) o `o` do k3 é do k3, na primeira célula do ramo dele, ao lado de uma célula de linha do k3
    assert linhas[2][x + 1] == "o"
    assert d.onde(x + 1, 2) == ("ligacao", "k3")
    assert d.onde(x + 1, 3) == ("ligacao", "k3") and linhas[3][x + 1] == "│"


def test_nenhuma_marca_seguida_de_linha_de_outra_seta():
    d = desenho.desenhar(modelo_png(), _posicoes_png())
    linhas = d.texto().splitlines()
    for tid, (x, y, w, h) in d.caixas.items():
        for yy in range(y + 1, y + h - 1):
            for bx, s in ((x + w - 1, 1), (x, -1)):
                if linhas[yy][bx] not in "├┤":
                    continue
                dono = d.onde(bx + s, yy)
                assert dono is not None and dono[0] == "ligacao"
                seguinte = linhas[yy][bx + 2 * s]
                if seguinte not in "┬┴├┤":                   # sem junção, a seta segue sozinha
                    assert d.onde(bx + 2 * s, yy) == dono


def test_png_desenhado_golden():
    assert desenho.desenhar(modelo_png(), _posicoes_png()).texto() == PNG_DESENHADO.rstrip("\n")


def _hub(n, nulos=True):
    """users.id referenciada por `n` tabelas postas abaixo da caixa dela, cada uma com uma FK anulável."""
    ts = {"t0": modelo.Tabela("t0", "public", "users", [modelo.Coluna("t0.c1", "id", "int8", True, pk=True)])}
    ligs = {}
    pos = {"t0": (0, 0)}
    for k in range(1, n + 1):
        ts["t%d" % k] = modelo.Tabela("t%d" % k, "public", "r%d" % k, [
            modelo.Coluna("t%d.c1" % k, "id", "int8", True, pk=True),
            modelo.Coluna("t%d.c2" % k, "user_id", "int8", not nulos)])
        ligs["k%d" % k] = modelo.Ligacao("k%d" % k, "t%d" % k, ("t%d.c2" % k,), "t0", ("t0.c1",), "f")
        pos["t%d" % k] = (30, 5 + (k - 1) * 6)
    return modelo.Modelo("public", ts, ligs), pos


def _os_do_hub(n):
    m, pos = _hub(n)
    d = desenho.desenhar(m, pos)
    linhas = d.texto().splitlines()
    achados = {}
    for y, linha in enumerate(linhas):
        for x, ch in enumerate(linha):
            alvo = d.onde(x, y)
            if ch == "o" and alvo and alvo[0] == "ligacao":
                achados[alvo[1]] = (x, y)
    return d, linhas, achados


def _confere_os(n):
    d, linhas, achados = _os_do_hub(n)
    assert sorted(achados) == ["k%d" % k for k in range(1, n + 1)], "\n".join(linhas)
    for lid, (x, y) in achados.items():
        vizinhas = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        assert any(d.onde(*v) == ("ligacao", lid) and linhas[v[1]][v[0]] in "─│┌┐└┘" for v in vizinhas), \
            "\n".join(linhas)


def test_duas_opcionais_na_mesma_coluna_ficam_opcionais():
    _confere_os(2)


def test_tres_opcionais_na_mesma_coluna_ficam_opcionais():
    _confere_os(3)


# ---- a tela do canvas (Task 8) -------------------------------------------------------------------------------

import asyncio

from textual.app import App

from ratt.db import canvas, guardar
from ratt.db.idioma import t


class FonteFalsa:
    def __init__(self, m=None):
        self.m = m or modelo_png()
        self.porta, self.banco = 5432, "teste"
        self.sig = 1

    def esquemas(self):
        return ["public"]

    def modelo(self, esquema):
        return self.m.copia()

    def assinatura(self):
        return self.sig

    def consulta(self):
        return ConsultaFalsa()


class AppTeste(App):
    def __init__(self, fonte):
        super().__init__()
        self.fonte = fonte

    def on_mount(self):
        self.push_screen(canvas.TelaCanvas(self.fonte, "public"))


async def pronto(app):
    """Espera as leituras do banco (feitas em thread) terminarem e chegarem à tela."""
    await app.workers.wait_for_complete()
    await asyncio.sleep(0.05)


def test_canvas_abre_e_seleciona(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            tela = app.screen
            await piloto.press("tab")
            assert tela.selecao[0] == "tabela"
            await piloto.press("down")
            assert tela.selecao[0] == "coluna"
    asyncio.run(roteiro())


def test_mover_pelo_teclado_guarda_posicao(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await piloto.press("tab")
            tid = app.screen.selecao[1]
            antes = app.screen.posicoes[tid]
            await piloto.press("shift+down")
            assert guardar.ler_posicoes(5432, "teste", "public")[tid] == (antes[0], antes[1] + 1)
    asyncio.run(roteiro())


def test_terminal_pequeno_e_vazio(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa(modelo.Modelo("public", {}, {})))
        async with app.run_test(size=(80, 24)) as piloto:
            await pronto(app)
            await piloto.pause()
            assert t("canvas.vazio") in app.screen.query_one(canvas.Quadro).aviso_vazio
            await piloto.resize_terminal(60, 20)
            await piloto.pause()
    asyncio.run(roteiro())


def test_redesenha_quando_o_banco_muda(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        fonte = FonteFalsa()
        app = AppTeste(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            del fonte.m.tabelas["t4"]
            fonte.sig = 2
            app.screen.vigiar()
            await piloto.pause(0.3)
            assert "t4" not in app.screen.vista.tabelas
    asyncio.run(roteiro())


# ---- a tela do canvas: rodada de correções ------------------------------------------------------------------

def modelo_largo(n, prefixo="tab"):
    """n tabelas soltas, sem ligações, cada uma com algumas colunas: bem maior que um terminal de 80x24."""
    tabelas = {}
    for k in range(n):
        tid = "t%d" % (k + 1)
        tabelas[tid] = Tabela(tid, "public", "%s%02d_com_nome_comprido" % (prefixo, k), [
            Coluna("%s.c1" % tid, "id", "bigint", obrigatoria=True, pk=True),
            Coluna("%s.c2" % tid, "descricao_longa", "character varying(255)"),
            Coluna("%s.c3" % tid, "criado_em", "timestamp with time zone")], nome_pk="pk%d" % k)
    return Modelo("public", tabelas, {})


def test_setas_rolam_o_quadro_sem_selecao(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa(modelo_largo(12)))
        async with app.run_test(size=(80, 24)) as piloto:
            await pronto(app)
            q = app.screen.query_one(canvas.Quadro)
            assert app.screen.selecao is None and q.max_scroll_y > 0
            antes = q.scroll_offset
            await piloto.press("down")
            await piloto.pause(0.1)
            assert q.scroll_offset.y > antes.y
            await piloto.press("pagedown")
            await piloto.pause(0.1)
            assert q.scroll_offset.y > antes.y + 2
            await piloto.press("home")
            await piloto.pause(0.1)
            assert q.scroll_offset.y == 0
            await piloto.press("end")
            await piloto.pause(0.1)
            assert q.scroll_offset.y == q.max_scroll_y
            # com uma tabela selecionada, ↑/↓ andam pelas colunas e não rolam
            await piloto.press("home")
            await piloto.press("tab")
            await piloto.press("down")
            assert app.screen.selecao[0] == "coluna"
    asyncio.run(roteiro())


def test_setas_horizontais_rolam(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        m = modelo_png()
        app = AppTeste(FonteFalsa(m))
        async with app.run_test(size=(40, 24)) as piloto:
            await pronto(app)
            q = app.screen.query_one(canvas.Quadro)
            assert q.max_scroll_x > 0
            await piloto.press("right")
            await piloto.pause(0.1)
            assert q.scroll_offset.x > 0
            await piloto.press("left")
            await piloto.pause(0.1)
            assert q.scroll_offset.x == 0
    asyncio.run(roteiro())


def test_erro_ao_ler_o_banco_nao_derruba_a_tela(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    avisos = []
    monkeypatch.setattr(canvas.TelaCanvas, "notify", lambda self, msg, **kw: avisos.append((msg, kw.get("severity"))))

    class Quebrada(FonteFalsa):
        def modelo(self, esquema):
            raise RuntimeError("a conexão caiu")

    async def roteiro():
        app = AppTeste(Quebrada())
        async with app.run_test(size=(80, 24)) as piloto:
            await pronto(app)
            assert isinstance(app.screen, canvas.TelaCanvas)
            assert app.screen.query_one(canvas.Quadro).aviso_vazio == t("canvas.sem_leitura")
            assert avisos and avisos[0][1] == "error" and "a conexão caiu" in avisos[0][0]
    asyncio.run(roteiro())


def test_resposta_de_outro_schema_e_descartada(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        fonte = FonteFalsa()
        app = AppTeste(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            tela = app.screen
            antes = set(tela.vista.tabelas)

            def virando(esquema):
                tela.esquema = "outro"          # a tela trocou de schema com a consulta ainda no ar
                m = fonte.m.copia()
                del m.tabelas["t4"]
                return m
            fonte.modelo = virando
            fonte.sig = 2
            tela.vigiar()
            await pronto(app)
            assert set(tela.vista.tabelas) == antes
    asyncio.run(roteiro())


def test_fonte_banco_tem_conexao_propria_e_fecha(monkeypatch):
    abertas = []

    class Con:
        closed = False

        def close(self):
            self.closed = True

    def entrar(porta, usuario, banco="postgres"):
        c = Con()
        abertas.append((porta, usuario, banco, c))
        return c, "s.como_socket"
    monkeypatch.setattr(canvas.acesso, "entrar", entrar)
    f = canvas.FonteBanco(None, 5432, "eu", "bd")
    assert abertas == []                         # preguiçosa
    assert f.conexao is f.conexao and len(abertas) == 1 and abertas[0][:3] == (5432, "eu", "bd")
    f.fechar()
    assert abertas[0][3].closed


def test_sql_visivel_e_quadro_rola_em_80x24(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa(modelo_largo(12)))
        async with app.run_test(size=(80, 24)) as piloto:
            await pronto(app)
            tela = app.screen
            sql = tela.query_one("#canvas_sql").region
            quadro = tela.query_one(canvas.Quadro)
            assert sql.height > 0 and sql.bottom <= 24 and sql.y >= quadro.region.bottom
            assert quadro.virtual_size.height > quadro.size.height
            assert quadro.max_scroll_y > 0
            await piloto.resize_terminal(60, 20)
            await piloto.pause(0.1)
            assert tela.query_one("#canvas_sql").region.bottom <= 20
    asyncio.run(roteiro())


def test_arrastar_pelo_titulo_move_limita_e_guarda(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            tela = app.screen
            q = tela.query_one(canvas.Quadro)
            tid = "t1"
            x0, y0, _, _ = q.desenho.caixas[tid]
            ini = (x0 + 4, y0 - q.scroll_offset.y)
            await piloto.mouse_down(q, offset=ini)
            await piloto.hover(q, offset=(ini[0] + 10, ini[1] + 3))
            await piloto.pause(0.2)
            await piloto.mouse_up(q, offset=(ini[0] + 10, ini[1] + 3))
            await piloto.pause(0.1)
            assert tela.selecao == ("tabela", tid)
            assert tela.posicoes[tid] == (x0 + 10, y0 + 3)
            assert guardar.ler_posicoes(5432, "teste", "public")[tid] == (x0 + 10, y0 + 3)
            # arrastar para além do canto: trava em x>=4, y>=2
            x1, y1, _, _ = q.desenho.caixas[tid]
            ini = (x1 + 4, y1 - q.scroll_offset.y)
            await piloto.mouse_down(q, offset=ini)
            await piloto.hover(q, offset=(2, 0))
            await piloto.pause(0.2)
            await piloto.mouse_up(q, offset=(2, 0))
            await piloto.pause(0.1)
            assert tela.posicoes[tid] == (canvas.MIN_X, canvas.MIN_Y)
            assert guardar.ler_posicoes(5432, "teste", "public")[tid] == (canvas.MIN_X, canvas.MIN_Y)
    asyncio.run(roteiro())


def test_schema_grande_abre_em_foco(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    m = modelo_largo(30)
    # tab00 é a primeira pelo nome: t1 e t2 ligam nela; t3 liga em t2 (2 saltos: fora do recorte)
    m.ligacoes["k1"] = Ligacao("k1", "t2", ("t2.c1",), "t1", ("t1.c1",), "a")
    m.ligacoes["k2"] = Ligacao("k2", "t3", ("t3.c1",), "t2", ("t2.c1",), "b")

    async def roteiro():
        app = AppTeste(FonteFalsa(m))
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            tela = app.screen
            assert set(tela.vista.tabelas) == {"t1", "t2"}
            await piloto.press("F")
            await piloto.pause(0.1)
            assert len(tela.vista.tabelas) == 30
    asyncio.run(roteiro())


def test_trocar_de_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    class Dois(FonteFalsa):
        def esquemas(self):
            return ["public", "outro"]

        def modelo(self, esquema):
            if esquema == "outro":
                return modelo_largo(1)
            return self.m.copia()

    async def roteiro():
        app = AppTeste(Dois())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            assert len(app.screen.vista.tabelas) == 4
            await piloto.press("s")
            await pronto(app)
            await piloto.pause(0.1)
            await piloto.press("down")
            await piloto.press("enter")
            await piloto.pause(0.1)
            await pronto(app)
            tela = app.screen
            assert isinstance(tela, canvas.TelaCanvas) and tela.esquema == "outro"
            assert list(tela.vista.tabelas) == ["t1"]
    asyncio.run(roteiro())


def test_tabela_selecionada_some_do_banco(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        fonte = FonteFalsa()
        app = AppTeste(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            tela = app.screen
            tela.selecao = ("coluna", "t4", "t4.c2")
            tela.redesenhar()
            del fonte.m.tabelas["t4"]
            del fonte.m.ligacoes["k3"]
            fonte.sig = 2
            tela.vigiar()
            await pronto(app)
            assert "t4" not in tela.vista.tabelas and tela.selecao is None
    asyncio.run(roteiro())


# ---- editar: formulários, rascunho no desenho, painel do SQL (Task 9) ---------------------------------------

import json

from ratt.db import checagem, formularios


async def _selecionar(piloto, app, nome):
    """Anda com `tab` até a tabela de nome `nome` (a ordem é a alfabética)."""
    for _ in range(10):
        await piloto.press("tab")
        sel = app.screen.selecao
        if sel and app.screen.vista.tabelas[sel[1]].nome == nome:
            return
    raise AssertionError("não achei " + nome)


def test_nova_coluna_pelo_formulario(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await _selecionar(piloto, app, "users")
            await piloto.press("c")
            await piloto.press(*"birth")
            app.screen.query_one("#tipo").value = "date"
            await piloto.click("#por_no_rascunho")
            await pronto(app)
            tela = app.screen
            r = tela.rascunho
            assert "ALTER TABLE public.users ADD COLUMN birth date;" in r.sql_texto()
            assert guardar.ler_rascunho(5432, "teste", "public") is not None
            assert t("canvas.rascunho", n=1) in str(tela.query_one("#canvas_titulo").render())
            assert "birth" in tela.vista.tabelas["t1"].colunas[-1].nome
            await piloto.press("u")
            assert r.operacoes == []
            assert guardar.ler_rascunho(5432, "teste", "public") is None      # vazio apaga o arquivo
    asyncio.run(roteiro())


def test_rascunho_volta_ao_reabrir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    guardar.gravar_rascunho(5432, "teste", "public", r.para_dict())

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            assert "RENAME TO pessoas" in app.screen.rascunho.sql_texto()
            assert "pessoas" in [tb.nome for tb in app.screen.vista.tabelas.values()]
    asyncio.run(roteiro())


def test_banco_muda_e_rascunho_marca_invalida(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t2", None, "papeis"))
    guardar.gravar_rascunho(5432, "teste", "public", r.para_dict())

    async def roteiro():
        fonte = FonteFalsa()
        app = AppTeste(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            del fonte.m.tabelas["t2"]
            fonte.m.ligacoes.pop("k2")
            fonte.sig = 2
            app.screen.vigiar()
            await piloto.pause(0.3)
            assert app.screen.rascunho.invalidas == {0}
            assert guardar.ler_rascunho(5432, "teste", "public")["operacoes"]      # não se perdeu
    asyncio.run(roteiro())


def test_rascunho_corrompido_vai_para_o_lado(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    pasta = guardar.pasta(5432, "teste")
    pasta.mkdir(parents=True)
    (pasta / "rascunho-public.json").write_text(json.dumps({"operacoes": [{"tipo": "nao_existe"}]}))

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            assert app.screen.rascunho.operacoes == []
            assert app.screen.vista.tabelas.keys() == modelo_png().tabelas.keys()
    asyncio.run(roteiro())
    assert not (pasta / "rascunho-public.json").exists()
    guardados = list(pasta.glob("rascunho-public.corrompido-*.json"))
    assert len(guardados) == 1 and "nao_existe" in guardados[0].read_text()


def test_separar_rascunho_nunca_sobrescreve(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    real = guardar.datetime.datetime
    monkeypatch.setattr(guardar.datetime, "datetime", type("D", (), {
        "now": staticmethod(lambda: real(2026, 11, 1, 20, 0, 0))}))
    caminhos = []
    for i in range(2):
        guardar.gravar_rascunho(5432, "teste", "public", {"n": i})
        caminhos.append(guardar.separar_rascunho(5432, "teste", "public"))
    assert caminhos[0] != caminhos[1] and all(c.exists() for c in caminhos)
    assert caminhos[0].name == "rascunho-public.corrompido-20261101-200000.json"


def test_ligar_como_nn_pelo_formulario(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await _selecionar(piloto, app, "users")
            await piloto.press("l")
            form = app.screen
            assert isinstance(form, formularios.FormLigar)
            lista = form.query_one("#destino")
            nomes = [str(lista.get_option_at_index(i).prompt) for i in range(lista.option_count)]
            lista.highlighted = nomes.index("profiles")
            form.query_one("#como_nn").value = True
            await piloto.pause(0.1)
            await piloto.click("#por_no_rascunho")
            await pronto(app)
            sql = app.screen.rascunho.sql_texto()
            assert "CREATE TABLE public.users_profiles" in sql and sql.count("FOREIGN KEY") == 2
    asyncio.run(roteiro())


def test_ligar_tabela_cria_a_coluna_e_a_unica(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await _selecionar(piloto, app, "profiles")
            await piloto.press("l")
            form = app.screen
            lista = form.query_one("#destino")
            nomes = [str(lista.get_option_at_index(i).prompt) for i in range(lista.option_count)]
            lista.highlighted = nomes.index("roles")
            form.query_one("#um").value = True              # cada role tem no máximo um profile
            await piloto.pause(0.1)
            await piloto.click("#por_no_rascunho")
            await pronto(app)
            sql = app.screen.rascunho.sql_texto()
            assert "ADD COLUMN role_id bigint" in sql and "UNIQUE (role_id)" in sql
    asyncio.run(roteiro())


def test_formulario_mostra_o_erro_e_nao_fecha(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await _selecionar(piloto, app, "users")
            await piloto.press("c")
            await piloto.click("#por_no_rascunho")            # nome vazio
            assert isinstance(app.screen, formularios.FormColuna)
            assert str(app.screen.query_one("#erro").render()).strip()
            assert app.screen.rascunho.operacoes == []
    asyncio.run(roteiro())


def test_tecla_sem_selecao_explica(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            tela = app.screen
            for tecla in ("c", "l", "x", "space"):
                await piloto.press(tecla)
                assert app.screen is tela
            assert len(app._notifications) >= 4
    asyncio.run(roteiro())


def test_nova_tabela_pelo_formulario(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa(modelo.Modelo("public", {}, {})))
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await piloto.press("a")
            await piloto.press(*"tags")
            app.screen.query_one("#col_nome").value = "nome"
            await piloto.click("#add_coluna")
            await piloto.click("#por_no_rascunho")
            await pronto(app)
            sql = app.screen.rascunho.sql_texto()
            assert "CREATE TABLE public.tags (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, nome text)" in sql
            assert "tags" in [tb.nome for tb in app.screen.vista.tabelas.values()]
    asyncio.run(roteiro())


def test_menu_torna_obrigatoria_e_renomeia(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await _selecionar(piloto, app, "users")
            await piloto.press("down", "down")               # first_name
            assert app.screen.selecao[0] == "coluna"
            await piloto.press("space")
            assert isinstance(app.screen, formularios.MenuSelecao)
            app.screen.dismiss("obrigatoria")
            await pronto(app)
            assert isinstance(app.screen, formularios.FormSimples)
            await piloto.click("#por_no_rascunho")
            await pronto(app)
            assert "SET NOT NULL" in app.screen.rascunho.sql_texto()
    asyncio.run(roteiro())


def test_apagar_tabela_com_dependentes_oferece_cascade(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    dep = checagem.Dependente("fk", 3, "users_roles", 1, "users_roles_role_id_fkey")

    class Fonte(FonteFalsa):
        def consulta(self):
            return ConsultaFalsa(dependentes=[dep])

    async def roteiro():
        app = AppTeste(Fonte())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await _selecionar(piloto, app, "roles")
            await piloto.press("x")
            assert isinstance(app.screen, formularios.ConfirmarApagar)
            await pronto(app)
            casc = app.screen.query_one("#cascata")
            assert casc.display
            casc.value = True
            await piloto.pause(0.1)
            await piloto.click("#por_no_rascunho")
            await pronto(app)
            assert "DROP TABLE public.roles CASCADE;" in app.screen.rascunho.sql_texto()
    asyncio.run(roteiro())


def test_painel_tira_operacao_e_pergunta_pelas_dependentes(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    r = rascunho.Rascunho(modelo_png())
    c = modelo.Coluna(r.novo_id("c"), "apelido", "text")
    r.acrescentar(rascunho.AdicionarColuna("t1", c))
    r.acrescentar(rascunho.Obrigatoria("t1", c.id, True))
    r.acrescentar(rascunho.Renomear("t2", None, "papeis"))
    guardar.gravar_rascunho(5432, "teste", "public", r.para_dict())

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            tela = app.screen
            await piloto.press("r")
            assert tela.focused is tela.query_one("#canvas_sql")
            await piloto.press("down", "down", "x")           # a terceira, sem dependentes
            assert [op.tipo for op in tela.rascunho.operacoes] == ["adicionar_coluna", "obrigatoria"]
            await piloto.press("up", "up", "x")               # a primeira: a segunda depende dela
            assert isinstance(app.screen, formularios.ConfirmarTirar)
            await piloto.click("#tirar_todas")
            await pronto(app)
            assert tela.rascunho.operacoes == []
            assert guardar.ler_rascunho(5432, "teste", "public") is None
            await piloto.press("escape")
            assert app.screen is tela and tela.focused is None
    asyncio.run(roteiro())


def test_painel_marca_invalida_em_laranja(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t2", None, "papeis"))
    guardar.gravar_rascunho(5432, "teste", "public", r.para_dict())
    fonte = FonteFalsa()
    del fonte.m.tabelas["t2"]
    fonte.m.ligacoes.pop("k2")

    async def roteiro():
        app = AppTeste(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            assert app.screen.rascunho.invalidas == {0}
            assert t("canvas.invalida") in app.screen.query_one("#canvas_sql").texto_plano()
    asyncio.run(roteiro())


def test_formulario_cabe_em_80x24_com_botoes_a_vista(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    r = rascunho.Rascunho(modelo_png())
    for k in range(6):
        r.acrescentar(rascunho.Renomear("t1", "t1.c2", "nome%d" % k))
    guardar.gravar_rascunho(5432, "teste", "public", r.para_dict())

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(80, 24)) as piloto:
            await pronto(app)
            tela = app.screen
            assert tela.query_one("#canvas_sql").region.bottom <= 24
            await piloto.press("a")
            assert isinstance(app.screen, formularios.FormTabela)
            for botao in ("#por_no_rascunho", "#cancelar"):
                assert app.screen.query_one(botao).region.bottom <= 24
            await piloto.press("escape")
            assert app.screen is tela
    asyncio.run(roteiro())


# ---- rodada de correções da Task 9 ---------------------------------------------------------------------------

def test_json_truncado_vai_para_o_lado_e_nunca_se_perde(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    pasta = guardar.pasta(5432, "teste")
    pasta.mkdir(parents=True)
    bytes_originais = '{"operacoes": [{"tipo": "renomear", "tabela": "t1", "coluna": null, "novo": "x"}], "proximo": 1'
    (pasta / "rascunho-public.json").write_text(bytes_originais)

    async def roteiro():
        fonte = FonteFalsa()
        app = AppTeste(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            assert app.screen.rascunho.operacoes == []
            fonte.sig = 2
            app.screen.vigiar()
            await pronto(app)
            await _selecionar(piloto, app, "users")
            await piloto.press("c")
            await piloto.press(*"birth")
            await piloto.click("#por_no_rascunho")
            await pronto(app)
    asyncio.run(roteiro())
    guardados = list(pasta.glob("rascunho-public.corrompido-*.json"))
    assert len(guardados) == 1 and guardados[0].read_text() == bytes_originais


def test_json_que_nao_e_objeto_tambem_e_separado(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    pasta = guardar.pasta(5432, "teste")
    pasta.mkdir(parents=True)
    (pasta / "rascunho-public.json").write_text("[1, 2]")
    with pytest.raises(rascunho.RascunhoCorrompido):
        guardar.ler_rascunho(5432, "teste", "public")
    assert guardar.ler_rascunho(5432, "teste", "outro") is None          # sem arquivo continua None


def test_formulario_sobrevive_a_tabela_que_some(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        fonte = FonteFalsa()
        app = AppTeste(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await _selecionar(piloto, app, "profiles")
            await piloto.press("c")
            await piloto.press(*"x")
            form = app.screen
            del fonte.m.tabelas["t4"]
            fonte.sig = 2
            tela = [s for s in app.screen_stack if isinstance(s, canvas.TelaCanvas)][0]
            tela.vigiar()
            await pronto(app)
            await piloto.click("#por_no_rascunho")
            assert app.screen is form and form.rascunho.operacoes == []
            assert str(form.query_one("#erro").render()).strip()
    asyncio.run(roteiro())


def test_clique_no_painel_escolhe_a_operacao_certa_com_rolagem(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    r = rascunho.Rascunho(modelo_png())
    for k in range(8):
        r.acrescentar(rascunho.Renomear("t1", "t1.c2", "nome%d" % k))
    guardar.gravar_rascunho(5432, "teste", "public", r.para_dict())

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            painel = app.screen.query_one(canvas.PainelSQL)
            assert painel.max_scroll_y > 0
            painel.scroll_to(y=painel.max_scroll_y, animate=False)
            await piloto.pause(0.1)
            topo = painel.scroll_offset.y
            esperada = max(n for n, ln in enumerate(painel._linhas) if ln <= topo)
            await piloto.click("#canvas_sql", offset=(5, painel.content_region.y - painel.region.y))
            assert painel.indice == esperada
    asyncio.run(roteiro())


def test_aplicar_recusa_com_invalidas(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t2", None, "papeis"))
    guardar.gravar_rascunho(5432, "teste", "public", r.para_dict())
    m = modelo_png(); del m.tabelas["t2"]; m.ligacoes.pop("k2")
    async def roteiro():
        app = AppTeste(FonteFalsa(m))
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await piloto.press("enter")
            await piloto.pause()
            assert app.screen.rascunho.operacoes and isinstance(app.screen, canvas.TelaCanvas)   # nenhum modal abriu
    asyncio.run(roteiro())


def test_exportar(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path)); monkeypatch.chdir(tmp_path)
    r = rascunho.Rascunho(modelo_png()); r.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    guardar.gravar_rascunho(5432, "teste", "public", r.para_dict())
    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await piloto.press("w"); await piloto.press("enter"); await piloto.pause()
    asyncio.run(roteiro())
    assert "ALTER TABLE public.users RENAME TO pessoas;" in (tmp_path / "rascunho-teste-public.sql").read_text()


def test_exportar_pede_confirmacao_para_sobrescrever(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path)); monkeypatch.chdir(tmp_path)
    (tmp_path / "rascunho-teste-public.sql").write_text("antigo")
    r = rascunho.Rascunho(modelo_png()); r.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    guardar.gravar_rascunho(5432, "teste", "public", r.para_dict())
    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await piloto.press("w"); await piloto.press("enter"); await piloto.pause()
            assert (tmp_path / "rascunho-teste-public.sql").read_text() == "antigo"   # parou na pergunta
            await piloto.press("escape"); await piloto.pause()
            assert (tmp_path / "rascunho-teste-public.sql").read_text() == "antigo"
            await piloto.press("w"); await piloto.press("enter"); await piloto.pause()
            await piloto.press("tab"); await piloto.press("enter"); await piloto.pause()
    asyncio.run(roteiro())
    assert "RENAME TO pessoas" in (tmp_path / "rascunho-teste-public.sql").read_text()


def test_exportar_recusa_rascunho_vazio(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path)); monkeypatch.chdir(tmp_path)
    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await piloto.press("w"); await piloto.pause()
            assert isinstance(app.screen, canvas.TelaCanvas)
    asyncio.run(roteiro())
    assert not (tmp_path / "rascunho-teste-public.sql").exists()


def test_depois_de_aplicar_remapeia_posicoes_e_esvazia(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            tela = app.screen
            nova = modelo.Tabela(tela.rascunho.novo_id("t"), "public", "pets", [modelo.Coluna("x", "id", "bigint", pk=True)])
            tela.rascunho.acrescentar(rascunho.CriarTabela(nova))
            tela._mudou()
            antes = tela.posicoes[nova.id]
            fresco = modelo_png()
            fresco.tabelas["t9"] = modelo.Tabela("t9", "public", "pets", [modelo.Coluna("t9.c1", "id", "bigint", pk=True)])
            tela._concluir(0, None, False, "-- ok\n")
            assert tela.rascunho.operacoes == [] and tela._aplicando       # já esvaziado, antes da releitura
            tela._aplicado("public", fresco, {nova.id: "pets"})
            assert tela.rascunho.operacoes == [] and tela.posicoes["t9"] == antes and nova.id not in tela.posicoes
            assert guardar.ler_rascunho(5432, "teste", "public") is None
            assert guardar.ler_posicoes(5432, "teste", "public")["t9"] == antes
            assert len(list((guardar.pasta(5432, "teste") / "aplicados").glob("*.sql"))) == 1
    asyncio.run(roteiro())


def _com_rascunho(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path)); monkeypatch.chdir(tmp_path)
    r = rascunho.Rascunho(modelo_png()); r.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    guardar.gravar_rascunho(5432, "teste", "public", r.para_dict())


def test_escape_no_caminho_nao_exporta(monkeypatch, tmp_path):
    _com_rascunho(tmp_path, monkeypatch)
    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await piloto.press("w"); await piloto.pause()
            await piloto.press("escape"); await piloto.pause()
    asyncio.run(roteiro())
    assert not (tmp_path / "rascunho-teste-public.sql").exists()


def test_um_enter_nao_aplica_e_cancelar_nao_roda(monkeypatch, tmp_path):
    _com_rascunho(tmp_path, monkeypatch)
    chamado = []
    monkeypatch.setattr(canvas.TelaCanvas, "_rodar", lambda self, ensaio: chamado.append(ensaio))
    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await piloto.press("enter"); await piloto.pause()
            assert not isinstance(app.screen, canvas.TelaCanvas)          # a confirmação abriu
            await piloto.press("enter"); await piloto.pause()             # o foco inicial é cancelar
            await piloto.press("enter"); await piloto.pause()
            await piloto.press("escape"); await piloto.pause()
    asyncio.run(roteiro())
    assert chamado == []


def test_ensaio_na_tela_nao_mexe_no_rascunho(monkeypatch, tmp_path):
    _com_rascunho(tmp_path, monkeypatch)
    antes = guardar.ler_rascunho(5432, "teste", "public")
    monkeypatch.setattr(canvas.TelaCanvas, "_sessao", lambda self, con, arq, ensaio, cab: (0, None))
    class Con:
        def close(self): pass
    fonte = FonteFalsa(); fonte.conexao_para_aplicar = lambda: Con()
    from contextlib import contextmanager
    @contextmanager
    def sem_suspender(self): yield
    monkeypatch.setattr(App, "suspend", sem_suspender)
    async def roteiro():
        app = AppTeste(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await piloto.press("e"); await piloto.pause()
            assert len(app.screen.rascunho.operacoes) == 1 and not app.screen._aplicando
    asyncio.run(roteiro())
    assert guardar.ler_rascunho(5432, "teste", "public") == antes
    assert not (guardar.pasta(5432, "teste") / "aplicados").exists()


def test_sair_logo_depois_de_aplicar_nao_deixa_o_aplicado_pendente(monkeypatch, tmp_path):
    _com_rascunho(tmp_path, monkeypatch)
    monkeypatch.setattr(canvas.TelaCanvas, "_reler_aplicado", lambda self, *a: None)   # a releitura nunca chega
    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            app.screen._concluir(0, None, False, "-- sql\n")
            await piloto.press("escape"); await piloto.pause()
    asyncio.run(roteiro())
    assert guardar.ler_rascunho(5432, "teste", "public") is None
    assert len(list((guardar.pasta(5432, "teste") / "aplicados").glob("*.sql"))) == 1


def test_trocar_de_esquema_na_janela_nao_toca_o_outro(monkeypatch, tmp_path):
    _com_rascunho(tmp_path, monkeypatch)
    guardar.gravar_rascunho(5432, "teste", "outro", {"operacoes": [], "proximo": 1})
    guardar.gravar_posicoes(5432, "teste", "outro", {"t1": (9, 9)})
    monkeypatch.setattr(canvas.TelaCanvas, "_reler_aplicado", lambda self, *a: None)
    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            tela = app.screen
            tela._concluir(0, None, False, "-- sql\n")
            await piloto.press("s"); await piloto.pause()
            assert isinstance(app.screen, canvas.TelaCanvas)               # bloqueado: nenhum seletor abriu
            tela.esquema = "outro"                                         # mesmo se trocasse à força
            tela._aplicado("public", modelo_png(), {})
    asyncio.run(roteiro())
    assert guardar.ler_rascunho(5432, "teste", "outro") is not None
    assert guardar.ler_posicoes(5432, "teste", "outro") == {"t1": (9, 9)}
    assert guardar.ler_rascunho(5432, "teste", "public") is None


# ---- revisão final: correções ---------------------------------------------------------------------------------

from ratt.db import tela as _tela


class _ConFalsa:
    def close(self):
        pass


class TelaBancoTeste(_tela.TelaBanco):
    """A tela do banco sem banco: só o que importa para as teclas que abrem o canvas."""

    def compose(self):
        from textual.widgets import Static
        yield Static("base")

    def on_mount(self, evento):
        evento.prevent_default()            # o on_mount da TelaBanco montaria a árvore e conectaria
        self.conexao = _ConFalsa()


def test_d_dentro_do_canvas_nao_abre_outro(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(canvas, "FonteBanco", lambda *a, **k: FonteFalsa())

    async def roteiro():
        app = TelaBancoTeste(5432, "u", "teste")
        async with app.run_test(size=(120, 40)) as piloto:
            await piloto.press("d"); await pronto(app)
            assert isinstance(app.screen, canvas.TelaCanvas)
            for tecla in ("d", "b", "p", "1", "n", "r"):
                await piloto.press(tecla); await pronto(app)
            assert sum(isinstance(s, canvas.TelaCanvas) for s in app.screen_stack) == 1
            assert len(app.screen_stack) == 2
    asyncio.run(roteiro())


def test_q_dentro_de_um_modal_nao_sai(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(canvas, "FonteBanco", lambda *a, **k: FonteFalsa())

    async def roteiro():
        app = TelaBancoTeste(5432, "u", "teste")
        async with app.run_test(size=(120, 40)) as piloto:
            await piloto.press("d"); await pronto(app)
            await piloto.press("s"); await pronto(app); await piloto.pause()
            assert isinstance(app.screen, canvas.EscolherEsquema)
            await piloto.press("q"); await piloto.pause()
            assert app.is_running and isinstance(app.screen, canvas.EscolherEsquema)
            await piloto.press("escape"); await piloto.pause()
            await piloto.press("q"); await piloto.pause()           # no canvas, q volta à tela do banco
            assert app.is_running and len(app.screen_stack) == 1
    asyncio.run(roteiro())


def test_canvas_coberto_nao_apaga_o_rascunho_de_outro(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FonteFalsa()

    async def roteiro():
        app = AppTeste(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            a = app.screen
            b = canvas.TelaCanvas(fonte, "public")
            await app.push_screen(b); await pronto(app)
            b.rascunho.acrescentar(rascunho.AdicionarColuna("t1", Coluna(b.rascunho.novo_id("c"), "birth", "date")))
            b._mudou()
            fonte.sig += 1                       # o banco muda com A coberto: A não grava nada
            a.vigiar(); await pronto(app)
            assert guardar.ler_rascunho(5432, "teste", "public") is not None
            await piloto.press("escape"); await pronto(app)
            assert app.screen is a and len(a.rascunho.operacoes) == 1      # A releu o disco ao voltar
            fonte.sig += 1
            a.vigiar(); await pronto(app)
            assert len(guardar.ler_rascunho(5432, "teste", "public")["operacoes"]) == 1
    asyncio.run(roteiro())


# ---- revisão final: Importants --------------------------------------------------------------------------------

class _ConCtx:
    """Conexão falsa que aguenta `with` e conta os rollbacks."""

    def __init__(self):
        self.rollbacks = 0
        self.fechada = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.fechada = True


def _interrompe(*a, **k):
    raise KeyboardInterrupt


def test_ctrl_c_durante_o_aplicar_desfaz_e_nao_derruba(monkeypatch, tmp_path):
    _com_rascunho(tmp_path, monkeypatch)
    monkeypatch.setattr(canvas, "aplicar_rascunho", _interrompe)
    monkeypatch.setattr("builtins.input", lambda *a: "")

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            con = _ConCtx()
            assert app.screen._sessao(con, tmp_path / "a.sql", False, "") == (1, None)
            assert con.rollbacks == 1
    asyncio.run(roteiro())


def test_ctrl_c_no_enter_para_voltar_vale_como_enter(monkeypatch, tmp_path):
    _com_rascunho(tmp_path, monkeypatch)
    monkeypatch.setattr(canvas, "aplicar_rascunho", lambda *a, **k: (0, None))
    monkeypatch.setattr("builtins.input", _interrompe)
    monkeypatch.setattr(canvas.TelaCanvas, "_reler_aplicado", lambda self, *a: None)
    from contextlib import contextmanager

    @contextmanager
    def sem_suspender(self):
        yield
    monkeypatch.setattr(App, "suspend", sem_suspender)
    fonte = FonteFalsa(); fonte.conexao_para_aplicar = lambda: _ConCtx()

    async def roteiro():
        app = AppTeste(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            app.screen._rodar(False)
            await piloto.pause()
            assert app.screen.rascunho.operacoes == []
    asyncio.run(roteiro())
    assert guardar.ler_rascunho(5432, "teste", "public") is None          # o aplicado não fica pendente


def test_rascunho_malformado_nunca_derruba_so_corrompido_ou_invalida():
    base = modelo_png()
    lig_vazia = {"id": "novo-k1", "origem": "t1", "colunas": [], "destino": "t2", "colunas_destino": [], "nome": ""}
    ruins = [
        {"operacoes": [{"tipo": "renomear", "tabela": "t1", "coluna": ["x"], "novo": "a"}]},
        {"operacoes": [{"tipo": "apagar_tabela", "tabela": {"a": 1}}]},
        {"operacoes": [{"tipo": "renomear", "tabela": "t1", "coluna": None, "novo": 5}]},
        {"operacoes": [{"tipo": "ligar", "ligacao": lig_vazia}]},
        {"operacoes": [{"tipo": "ligar", "ligacao": dict(lig_vazia, colunas=["t1.c2"])}]},
        {"operacoes": [{"tipo": "ligar_nn", "tabela_id": "novo-t1", "nome": "x", "a": "t1", "b": "t2",
                        "colunas_ids": [], "ligacoes_ids": []}]},
        {"operacoes": [{"tipo": "criar_tabela", "tabela": {"id": "novo-t1", "esquema": "public", "nome": "x",
                                                           "colunas": [{"id": ["c"], "nome": "a", "tipo": "text"}]}}]},
    ]
    for d in ruins:
        try:
            r = rascunho.Rascunho.de_dict(d, base)
        except rascunho.RascunhoCorrompido:
            continue
        # o que passa é usável em tudo o que a tela faz com ele
        assert r.invalidas == {0}, d
        r.vista(); r.passos(); r.sql_texto(); r.dependentes(0); r.modelo()


def _leitura_falha(monkeypatch, alvo_nome):
    """Faz a leitura do arquivo `alvo_nome` falhar com um erro de disco (não 'não existe'); devolve o desfazer."""
    from pathlib import Path
    original = Path.read_text

    def read_text(self, *a, **k):
        if self.name == alvo_nome:
            raise PermissionError(13, "Permission denied", str(self))
        return original(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", read_text)
    return lambda: monkeypatch.setattr(Path, "read_text", original)


def test_ler_rascunho_com_erro_de_disco_levanta(monkeypatch, tmp_path):
    _com_rascunho(tmp_path, monkeypatch)
    _leitura_falha(monkeypatch, "rascunho-public.json")
    with pytest.raises(OSError):
        guardar.ler_rascunho(5432, "teste", "public")


def test_erro_ao_ler_o_rascunho_nao_deixa_gravar_por_cima(monkeypatch, tmp_path):
    _com_rascunho(tmp_path, monkeypatch)
    antes = guardar.ler_rascunho(5432, "teste", "public")
    desfazer = _leitura_falha(monkeypatch, "rascunho-public.json")

    async def roteiro():
        fonte = FonteFalsa()
        app = AppTeste(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            tela = app.screen
            assert tela.rascunho.operacoes == []
            tela.rascunho.acrescentar(rascunho.AdicionarColuna("t1", Coluna(tela.rascunho.novo_id("c"), "birth", "date")))
            tela._mudou()                               # editar segue, mas não grava por cima do que não se leu
            tela.rascunho.desfazer(); tela._mudou()     # nem apaga
            fonte.sig += 1
            tela.vigiar(); await pronto(app)
            desfazer()
            assert guardar.ler_rascunho(5432, "teste", "public") == antes
    asyncio.run(roteiro())


def test_lendo_enquanto_carrega_e_tenta_de_novo_depois_da_falha(monkeypatch, tmp_path):
    import threading
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    avisos = []
    monkeypatch.setattr(canvas.TelaCanvas, "notify", lambda self, msg, **kw: avisos.append(msg))
    solta = threading.Event()

    class Lenta(FonteFalsa):
        quebrada = True

        def modelo(self, esquema):
            solta.wait(5)
            if self.quebrada:
                raise RuntimeError("a conexão caiu")
            return super().modelo(esquema)

    async def roteiro():
        fonte = Lenta()
        app = AppTeste(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await piloto.pause(0.1)
            quadro = app.screen.query_one(canvas.Quadro)
            assert quadro.aviso_vazio == t("canvas.lendo")
            solta.set()
            await pronto(app)
            assert quadro.aviso_vazio == t("canvas.sem_leitura")
            app.screen.vigiar(); await pronto(app)          # ainda quebrada: tenta, falha, não repete o aviso
            assert len(avisos) == 1
            fonte.quebrada = False
            app.screen.vigiar(); await pronto(app)
            assert app.screen.carregado and quadro.aviso_vazio == "" and app.screen.vista.tabelas
    asyncio.run(roteiro())


def test_apagar_comeca_no_cancelar(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    async def roteiro():
        app = AppTeste(FonteFalsa())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await _selecionar(piloto, app, "roles")
            await piloto.press("x"); await piloto.pause()
            assert isinstance(app.screen, formularios.ConfirmarApagar)
            assert app.screen.focused is not None and app.screen.focused.id == "cancelar"
            await piloto.press("enter"); await pronto(app)            # x Enter: nada entra no rascunho
            assert isinstance(app.screen, canvas.TelaCanvas) and app.screen.rascunho.operacoes == []
    asyncio.run(roteiro())


def test_confirmacao_de_aplicar_lista_o_que_apaga(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    class Fonte(FonteFalsa):
        def consulta(self):
            return ConsultaFalsa(linhas=12)

    async def roteiro():
        app = AppTeste(Fonte())
        async with app.run_test(size=(120, 40)) as piloto:
            await pronto(app)
            await _selecionar(piloto, app, "roles")
            await piloto.press("x"); await pronto(app); await piloto.pause(0.4); await pronto(app)
            await piloto.click("#por_no_rascunho"); await pronto(app)
            tela = app.screen
            tela.rascunho.acrescentar(rascunho.ApagarColuna("t1", "t1.c5"))
            tela.rascunho.acrescentar(rascunho.ApagarLigacao("k3"))
            tela._mudou()
            await piloto.press("enter"); await piloto.pause()
            conf = app.screen
            assert isinstance(conf, formularios.Confirmar)
            itens = "\n".join(conf.itens)
            assert t("canvas.d.tabela", n=12) in itens
            assert t("canvas.d.coluna") in itens and t("canvas.d.restricao") in itens
            assert itens.index("roles") < itens.index("password_hash")
            await piloto.press("escape"); await piloto.pause()
            # reaberto (sem a contagem guardada na memória): diz que não contou
            app.screen._contagens.clear()
            await piloto.press("enter"); await piloto.pause()
            assert t("canvas.d.nao_contado") in "\n".join(app.screen.itens)
    asyncio.run(roteiro())


def test_operacao_guarda_os_nomes_da_base_e_nao_muda_de_alvo():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    r.acrescentar(rascunho.Obrigatoria("t1", "t1.c2", True))
    r.acrescentar(rascunho.ApagarLigacao("k3"))
    assert r.operacoes[1].nomes == {"t1": "users", "t1.c2": "first_name"}
    assert r.operacoes[2].nomes == {"k3": "profiles_user_id_fkey"}
    assert r.invalidas == set()                       # a renomeação do próprio rascunho não conta
    d = json.loads(json.dumps(r.para_dict()))
    assert rascunho.Rascunho.de_dict(d, modelo_png()).operacoes == r.operacoes
    # `ratt db reset` + setup: o mesmo oid agora é outra tabela, com outro nome
    outra = modelo_png()
    outra.tabelas["t1"].nome = "clientes"
    assert r.rebase(outra) == {0, 1}
    # a coluna do mesmo attnum agora é outra
    outra = modelo_png()
    outra.tabelas["t1"].colunas[1].nome = "apelido"
    assert r.rebase(outra) == {1}
    outra = modelo_png()
    outra.ligacoes["k3"].nome = "outra_fkey"
    assert r.rebase(outra) == {2}
    # rascunho antigo, sem `nomes`: não há o que conferir
    for o in d["operacoes"]:
        del o["nomes"]
    velho = rascunho.Rascunho.de_dict(d, modelo_png())
    outra = modelo_png()
    outra.tabelas["t1"].nome = "clientes"
    assert velho.rebase(outra) == set()


def test_nomes_malformados_sao_corrompidos():
    r = rascunho.Rascunho(modelo_png())
    r.acrescentar(rascunho.Renomear("t1", None, "pessoas"))
    d = json.loads(json.dumps(r.para_dict()))
    d["operacoes"][0]["nomes"] = ["t1"]
    with pytest.raises(rascunho.RascunhoCorrompido):
        rascunho.Rascunho.de_dict(d, modelo_png())
