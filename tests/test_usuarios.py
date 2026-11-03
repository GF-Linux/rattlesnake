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


def test_acesso_dono_so_com_connect_e_grupo_que_tem_connect():
    """Ser dono não dá CONNECT por si: o dono entra pelo CONNECT que o `datacl` (ou o padrão) lhe dá, como qualquer
    um. Dono que tirou o próprio CONNECT não entra (salvo pelo PUBLIC ou por um grupo); membro de um grupo dono
    só entra se o grupo tem o CONNECT."""
    m = modelo_exemplo()
    m.bancos["d3"] = Banco("d3", "fechado", "r14", False, ("r14",), False)   # o dono é o grupo meio, com CONNECT
    assert servidor.acesso(m, "r14", "d3") == "dono"
    assert servidor.acesso(m, "r15", "d3") == "herdado"
    assert servidor.acesso(m, "r13", "d3") == "nenhum"
    m.bancos["d3"] = Banco("d3", "fechado", "r14", False, (), False)        # o dono tirou de si o CONNECT
    assert servidor.acesso(m, "r14", "d3") == "nenhum" and servidor.acesso(m, "r15", "d3") == "nenhum"
    m.bancos["d3"] = Banco("d3", "fechado", "r14", False, (), True)         # mas o PUBLIC entra
    assert servidor.acesso(m, "r14", "d3") == "herdado"


def test_acesso_so_segue_pertencas_que_herdam_e_ciclo_nao_trava():
    m = modelo_exemplo()
    m.papeis["r13"].nao_herda = ("r12",)                    # ana está em leitura sem herdar (NOINHERIT / INHERIT FALSE)
    assert servidor.acesso(m, "r13", "d1") == "nenhum"
    assert servidor.acesso(m, "r15", "d1") == "herdado"
    m.papeis["r14"].nao_herda = ("r12",)                    # o meio da corrente não herda: bruno também não
    assert servidor.acesso(m, "r15", "d1") == "nenhum"
    assert servidor.grupos(m, "r15") == {"r14", "r12"} and servidor.grupos(m, "r15", herdados=True) == {"r14"}
    m.papeis["r14"].nao_herda = ()
    m.papeis["r15"].sem_set = ("r14",)                      # SET FALSE não muda o acesso (só se lê)
    assert servidor.acesso(m, "r15", "d1") == "herdado"
    # um grupo que some do modelo ou um ciclo não quebram a busca
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
    p = m.papeis["r13"]
    assert p.herda is True and p.nao_herda == () and p.sem_set == () and p.sistema is False


# ── o rascunho do servidor: operações, SQL por banco, travas ──

import json

import pytest

from ratt.db import rascunho_servidor as rs
from ratt.db.rascunho import RascunhoCorrompido
from ratt.db.rascunho_servidor import (CriarPapel, DarAcesso, MudarPoderes, PorNoGrupo, RascunhoServidor,
                                       RemoverPapeis, TirarAcesso, TirarDoGrupo, TrocarDonoDoBanco, TrocarSenha)

HASH = "SCRAM-SHA-256$4096:c2FsdA==$Q2hhdmU=:U2Vydmlkb3I="


def _sql(op, m):
    return op.comandos(m)


def test_sql_de_cada_operacao_com_nomes_citados():
    m = modelo_exemplo()
    novo = Papel("novo-p1", "Ana Maria", True, False, True, False)
    assert _sql(CriarPapel(novo), m) == [(None, "CREATE ROLE \"Ana Maria\" LOGIN CREATEDB")]   # nunca senha provisória
    grupo = Papel("novo-p2", "leitores", False, False, False, True, membro_de=("r12",))
    assert _sql(CriarPapel(grupo), m) == [(None, "CREATE ROLE leitores CREATEROLE IN ROLE leitura")]
    assert _sql(MudarPoderes("r16", False, True, False, False), m) == [(None, "ALTER ROLE carla NOLOGIN SUPERUSER")]
    assert _sql(TrocarSenha("r16"), m) == []                        # só em grupos(senhas=…)
    assert _sql(PorNoGrupo("r16", "r12"), m) == [(None, "GRANT leitura TO carla")]
    assert _sql(TirarDoGrupo("r13", "r12"), m) == [(None, "REVOKE leitura FROM ana")]
    assert _sql(DarAcesso("r16", "d1"), m) == [(None, "GRANT CONNECT ON DATABASE estudos TO carla")]
    assert _sql(DarAcesso("PUBLIC", "d1"), m) == [(None, "GRANT CONNECT ON DATABASE estudos TO PUBLIC")]
    assert _sql(TirarAcesso("PUBLIC", "d2"), m) == [(None, "REVOKE CONNECT ON DATABASE sistema FROM PUBLIC")]
    assert _sql(TirarAcesso("r12", "d1"), m) == [(None, "REVOKE CONNECT ON DATABASE estudos FROM leitura")]
    assert _sql(TrocarDonoDoBanco("d2", "r16"), m) == [(None, "ALTER DATABASE sistema OWNER TO carla")]


def test_nomes_hostis_sao_citados_e_cada_comando_e_um_so():
    m = modelo_exemplo()
    m.papeis["r16"].nome = 'c"x; DROP ROLE jared; --'
    m.papeis["r12"].nome = "PUBLIC"                  # um papel chamado PUBLIC (maiúsculas) não é o PUBLIC
    m.bancos["d1"].nome = "est\nudos'; --"
    r = RascunhoServidor(m)
    r.acrescentar(PorNoGrupo("r16", "r12"))
    r.acrescentar(DarAcesso("r16", "d1"))
    r.acrescentar(TirarAcesso("r12", "d1"))
    r.acrescentar(RemoverPapeis(("r16",), "r11", ("est\nudos'; --",)))
    texto = r.sql_texto("cabeçalho\n DROP ROLE x;")
    assert 'GRANT "PUBLIC" TO "c""x; DROP ROLE jared; --";' in texto
    assert "GRANT CONNECT ON DATABASE \"est\nudos'; --\" TO \"c\"\"x; DROP ROLE jared; --\";" in texto
    assert 'REVOKE CONNECT ON DATABASE "est\nudos\'; --" FROM "PUBLIC";' in texto
    from ratt.db.rodar import dividir
    comandos = [c.texto for c in dividir(texto)]
    assert len(comandos) == 3 + 2 + 1          # 3 comuns; REASSIGN e DROP OWNED num banco; DROP ROLE
    assert all(not c.startswith("udos") for c in comandos)
    # o nome do banco com quebra de linha no comentário "no banco …" fica numa linha só
    assert any(ln.startswith("--") and "est udos" in ln for ln in texto.splitlines())
    with pytest.raises(ValueError):
        CriarPapel(Papel("novo-p9", "", True, False, False, False)).comandos(m)


def test_criar_papel_recusa_nome_que_ja_existe_ou_reservado_e_id_do_catalogo():
    m = modelo_exemplo()
    for nome in ("ana", "pg_meu", "public", "none", "x" * 64):
        with pytest.raises(ValueError):
            CriarPapel(Papel("novo-p1", nome, True, False, False, False)).comandos(m)
    with pytest.raises(ValueError):
        CriarPapel(Papel("r16", "outro", True, False, False, False)).comandos(m)     # id do catálogo
    assert CriarPapel(Papel("novo-p1", "Ana", True, False, False, False)).comandos(m)  # maiúsculas: outro nome


def test_operacoes_que_nao_mudam_nada_ou_impossiveis_sao_recusadas():
    m = modelo_exemplo()
    r = RascunhoServidor(m)
    for op in (MudarPoderes("r16", True, False, False, False),     # já é assim
               PorNoGrupo("r13", "r12"),                           # já é membro
               PorNoGrupo("r12", "r12"),                           # de si mesmo
               PorNoGrupo("r12", "r15"),                           # ciclo: bruno → meio → leitura
               TirarDoGrupo("r15", "r12"),                         # bruno não é membro direto de leitura
               DarAcesso("r12", "d1"), DarAcesso("PUBLIC", "d2"),  # já tem
               TirarAcesso("r16", "d1"), TirarAcesso("PUBLIC", "d1"),
               TrocarDonoDoBanco("d2", "r11"),
               RemoverPapeis((), "r11"), RemoverPapeis(("r16",), "r16"), RemoverPapeis(("r16", "r16"), "r11"),
               RemoverPapeis(("r16",), "r11", ("nao_existe",))):
        with pytest.raises(ValueError):
            r.acrescentar(op)
    assert r.operacoes == []


def test_remover_dois_papeis_em_dois_bancos_gera_as_secoes_da_spec():
    m = modelo_exemplo()
    m.bancos["d4"] = Banco("d4", "da_carla", "r16", True, ("r16",), True)
    r = RascunhoServidor(m)
    r.acrescentar(MudarPoderes("r16", True, False, True, False))
    op = RemoverPapeis(("r13", "r16"), "r11", ("estudos", "sistema"))
    r.acrescentar(op)
    # um DROP OWNED por papel: `DROP OWNED BY a, b` falha quando a permissão padrão de um dá algo ao outro
    assert op.comandos(r.base) == [
        ("estudos", "REASSIGN OWNED BY ana, carla TO app"), ("estudos", "DROP OWNED BY ana"),
        ("estudos", "DROP OWNED BY carla"),
        ("sistema", "REASSIGN OWNED BY ana, carla TO app"), ("sistema", "DROP OWNED BY ana"),
        ("sistema", "DROP OWNED BY carla"),
        (None, "ALTER DATABASE da_carla OWNER TO app"), (None, "DROP ROLE ana, carla")]
    assert r.grupos() == [
        (None, "ALTER ROLE carla CREATEDB;"),
        ("estudos", "REASSIGN OWNED BY ana, carla TO app;\nDROP OWNED BY ana;\nDROP OWNED BY carla;"),
        ("sistema", "REASSIGN OWNED BY ana, carla TO app;\nDROP OWNED BY ana;\nDROP OWNED BY carla;"),
        (None, "ALTER DATABASE da_carla OWNER TO app;\nDROP ROLE ana, carla;")]
    texto = r.sql_texto()
    assert texto.index("-- 1.") < texto.index("-- 2.")
    assert "estudos" in texto and texto.count("REASSIGN OWNED BY ana, carla TO app;") == 2
    v = r.vista()
    assert v.papeis["r13"].estado == "apagado" and v.papeis["r16"].estado == "apagado"
    assert v.bancos["d4"].dono == "r11" and v.bancos["d4"].acesso_direto == ("r11",)
    assert "r13" not in r.modelo().papeis and "r16" not in r.modelo().papeis
    assert servidor.acesso(r.modelo(), "r11", "d4") == "dono"


def test_remocao_roda_por_ultimo_mesmo_no_meio_do_rascunho():
    m = modelo_exemplo()
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r16",), "r11", ("sistema",)))
    r.acrescentar(PorNoGrupo("r15", "r12"))
    destinos = [d for d, _ in r.grupos()]
    assert destinos == [None, "sistema", None]
    assert r.grupos()[0] == (None, "GRANT leitura TO bruno;")
    texto = r.sql_texto()
    assert texto.index("-- 2.") < texto.index("-- 1.")       # o texto mostra na ordem em que roda
    linhas = r.linhas_das_operacoes()
    assert linhas[1] < linhas[0]
    assert r.operacao_da_linha(linhas[1]) == 1 and r.operacao_da_linha(linhas[0]) == 0
    assert r.operacao_da_linha(linhas[0] + 1) == 0 and r.operacao_da_linha(1) == 1


def test_papel_criado_no_rascunho_e_referido_pelo_id():
    m = modelo_exemplo()
    r = RascunhoServidor(m)
    i = r.novo_id("p")
    assert i == "novo-p1"
    r.acrescentar(CriarPapel(Papel(i, "ana2", True, False, False, False)))
    r.acrescentar(PorNoGrupo(i, "r12"))
    r.acrescentar(DarAcesso(i, "d1"))
    assert r.invalidas == set() and r.dependentes(0) == [1, 2]
    assert r.vista().papeis[i].estado == "novo" and r.vista().papeis[i].membro_de == ("r12",)
    assert r.grupos(senhas={i: HASH})[0][1].startswith(f"CREATE ROLE ana2 LOGIN;\nALTER ROLE ana2 PASSWORD '{HASH}';")
    r.tirar([0])
    assert r.invalidas == {0, 1}


def test_operacao_posterior_a_remocao_que_refere_o_removido_fica_invalida():
    m = modelo_exemplo()
    r = RascunhoServidor(m)
    r.acrescentar(PorNoGrupo("r16", "r12"))                         # antes: vale (roda antes da remoção)
    r.acrescentar(RemoverPapeis(("r16",), "r11", ()))
    with pytest.raises(ValueError):
        r.acrescentar(DarAcesso("r16", "d1"))                       # depois: carla já foi removida
    r.operacoes.append(DarAcesso("r16", "d1"))                      # como se viesse do disco
    assert r.invalidas == {2}
    assert [d for d, _ in r.grupos()] == [None, None]
    assert r.grupos()[1] == (None, "DROP ROLE carla;")


def test_rebase_invalida_quando_o_papel_some_ou_o_oid_volta_com_outro_nome():
    m = modelo_exemplo()
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r16", "r13"), "r11", ("sistema",)))
    r.acrescentar(PorNoGrupo("r15", "r12"))
    assert r.operacoes[0].nomes == {"r16": "carla", "r13": "ana", "r11": "app"}
    novo = modelo_exemplo()
    del novo.papeis["r13"]
    assert r.rebase(novo) == {0}
    novo = modelo_exemplo()
    novo.papeis["r16"].nome = "outra"                               # o oid reaproveitado
    assert r.rebase(novo) == {0}
    assert r.rebase(modelo_exemplo()) == set()


def test_senha_so_no_aplicar_nunca_no_texto_nem_no_json():
    m = modelo_exemplo()
    r = RascunhoServidor(m)
    i = r.novo_id("p")
    r.acrescentar(CriarPapel(Papel(i, "ana2", True, False, False, False)))
    r.acrescentar(TrocarSenha("r16"))
    r.acrescentar(CriarPapel(Papel(r.novo_id("p"), "grupo2", False, False, False, False)))   # sem LOGIN: sem senha
    texto = r.sql_texto()
    assert "PASSWORD" not in texto and "…" not in texto              # nem senha provisória, nem marcador
    assert texto.splitlines()[1:3] == ["CREATE ROLE ana2 LOGIN;", "-- " + rs.t("usu.senha_ao_aplicar", papel="ana2")]
    assert "\\password carla" in texto and texto.count("\\password") == 2
    g = r.grupos(senhas={i: HASH, "r16": "SCRAM-SHA-256$4096:b3V0cm8=$x:y"})
    assert g == [(None, f"CREATE ROLE ana2 LOGIN;\nALTER ROLE ana2 PASSWORD '{HASH}';\n"
                        "ALTER ROLE carla PASSWORD 'SCRAM-SHA-256$4096:b3V0cm8=$x:y';\nCREATE ROLE grupo2;")]
    assert HASH not in r.sql_texto() and HASH not in json.dumps(r.para_dict())
    with pytest.raises(ValueError):
        r.grupos()                                                  # sem senhas, com papel que pede senha
    with pytest.raises(ValueError):
        r.grupos(senhas={i: HASH})                                  # falta a de carla
    with pytest.raises(ValueError):
        r.grupos(senhas={i: HASH, "r16": "a'; DROP ROLE x; --\x00"})
    # aspas no hash são citadas (o hash vem do cliente, mas o literal não confia)
    assert "'a''b'" in r.grupos(senhas={i: "a'b", "r16": HASH})[0][1]


def test_json_ida_e_volta_e_corrompido():
    m = modelo_exemplo()
    r = RascunhoServidor(m)
    i = r.novo_id("p")
    r.acrescentar(CriarPapel(Papel(i, "ana2", True, False, True, False, membro_de=("r12",))))
    r.acrescentar(MudarPoderes("r16", True, False, True, True))
    r.acrescentar(TrocarSenha("r16"))
    r.acrescentar(PorNoGrupo("r16", "r12"))
    r.acrescentar(TirarDoGrupo("r13", "r12"))
    r.acrescentar(DarAcesso("PUBLIC", "d1"))
    r.acrescentar(TirarAcesso("r12", "d1"))
    r.acrescentar(TrocarDonoDoBanco("d2", "r16"))
    r.acrescentar(RemoverPapeis(("r15",), "r11", ("estudos", "sistema")))
    d = json.loads(json.dumps(r.para_dict()))
    volta = RascunhoServidor.de_dict(d, m)
    senhas = {i: HASH, "r16": HASH}
    assert volta.sql_texto() == r.sql_texto() and volta.grupos(senhas) == r.grupos(senhas)
    assert volta.proximo == r.proximo == 2 and volta.invalidas == set()
    assert volta.operacoes[0].papel.membro_de == ("r12",) and volta.operacoes[-1].papeis == ("r15",)
    estragados = [
        [], {"operacoes": {}}, {"operacoes": [{"tipo": "nada"}]}, {"operacoes": [1]},
        {"operacoes": [{"tipo": "por_no_grupo", "papel": ["r16"], "grupo": "r12"}]},
        {"operacoes": [{"tipo": "por_no_grupo", "papel": "r16"}]},
        {"operacoes": [{"tipo": "por_no_grupo", "papel": "r16", "grupo": "r12", "x": 1}]},
        {"operacoes": [{"tipo": "mudar_poderes", "papel": "r16", "login": "sim", "superusuario": False,
                        "cria_bancos": False, "cria_papeis": False}]},
        {"operacoes": [{"tipo": "remover_papeis", "papeis": "r16", "novo_dono": "r11"}]},
        {"operacoes": [{"tipo": "remover_papeis", "papeis": [1], "novo_dono": "r11"}]},
        {"operacoes": [{"tipo": "criar_papel", "papel": {"id": "novo-p1"}}]},
        {"operacoes": [{"tipo": "criar_papel", "papel": "x"}]},
        {"operacoes": [{"tipo": "criar_papel", "papel": {"id": "novo-p1", "nome": "x", "login": False,
                                                         "superusuario": False, "cria_bancos": False,
                                                         "cria_papeis": False, "concedido_por": {"r12": {"r10": [1, 2]}}}}]},
        {"operacoes": [{"tipo": "criar_papel", "papel": {"id": "novo-p1", "nome": "x", "login": False,
                                                         "superusuario": False, "cria_bancos": False,
                                                         "cria_papeis": False, "concedido_por": {"r12": ["r10"]}}}]},
        {"operacoes": [{"tipo": "trocar_senha", "papel": "r16", "nomes": {"r16": 3}}]},
        {"operacoes": [], "proximo": "x"},
    ]
    for e in estragados:
        with pytest.raises(RascunhoCorrompido):
            RascunhoServidor.de_dict(e, m)


def test_json_editado_a_mao_nao_passa_pela_trava():
    m = modelo_exemplo()
    d = {"operacoes": [{"tipo": "remover_papeis", "papeis": ["r10"], "novo_dono": "r11", "bancos": []},
                       {"tipo": "criar_papel", "papel": {"id": "r10", "nome": "intruso", "login": True,
                                                         "superusuario": True, "cria_bancos": False,
                                                         "cria_papeis": False}}]}
    r = RascunhoServidor.de_dict(d, m)
    assert r.invalidas == {0, 1} and r.grupos() == [] and r.sql_texto() == ""


def test_desfazer_dependentes_vista_e_modelo():
    m = modelo_exemplo()
    r = RascunhoServidor(m)
    r.acrescentar(TrocarDonoDoBanco("d2", "r16"))
    assert r.vista().bancos["d2"].estado == "mudado" and r.vista().bancos["d2"].acesso_direto == ("r16",)
    assert m.bancos["d2"].dono == "r11"                             # a base não muda
    assert r.desfazer() is not None and r.desfazer() is None and r.operacoes == []
    r.acrescentar(TirarDoGrupo("r13", "r12"))
    assert r.vista().papeis["r13"].membro_de == () and r.vista().papeis["r13"].estado == "mudado"
    assert len(r.passos()) == 1 and r.passos()[0][1] is True


# ── as travas: nunca se trancar para fora ──

def _com_postgres(m):
    m.papeis["r9"] = _papel("r9", "postgres", super_=True)
    return m


def _recusa(m, op):
    texto = rs.trava(op, m)
    assert texto, op
    r = RascunhoServidor(m)
    with pytest.raises(ValueError) as e:
        r.acrescentar(op)
    assert str(e.value) == texto and r.operacoes == []
    return texto


def test_trava_remover_a_si_postgres_e_o_ultimo_superusuario_com_login():
    m = _com_postgres(modelo_exemplo())
    assert "próprio" in _recusa(m, RemoverPapeis(("r16", "r10"), "r11"))
    assert "postgres" in _recusa(m, RemoverPapeis(("r9",), "r11"))
    m = modelo_exemplo()
    m.eu = "r11"                                                    # app tira o único superusuário que entra
    assert "superusuário" in _recusa(m, RemoverPapeis(("r10",), "r11"))
    assert "superusuário" in _recusa(m, MudarPoderes("r10", True, False, False, False))
    assert "superusuário" in _recusa(m, MudarPoderes("r10", False, True, False, False))
    assert rs.trava(RemoverPapeis(("r17",), "r11"), m) is None      # velho é superusuário, mas sem LOGIN
    m.papeis["r16"].superusuario = True                             # com outro superusuário que entra, pode
    assert rs.trava(RemoverPapeis(("r10",), "r11"), m) is None


def test_trava_tirar_de_si_login_ou_superusuario():
    m = _com_postgres(modelo_exemplo())                             # postgres também é superusuário: não importa
    assert "LOGIN" in _recusa(m, MudarPoderes("r10", False, True, False, False))
    assert "SUPERUSER" in _recusa(m, MudarPoderes("r10", True, False, False, False))
    assert rs.trava(MudarPoderes("r10", True, True, True, True), m) is None


def test_trava_tirar_connect_do_public_quando_eu_entra_por_ele():
    m = modelo_exemplo()
    m.eu, m.banco_atual = "r16", "d2"                               # carla, em sistema, entra pelo PUBLIC
    assert "sistema" in _recusa(m, TirarAcesso("PUBLIC", "d2"))
    m.bancos["d2"].acesso_direto = ("r11", "r16")                   # com CONNECT direto, pode
    assert rs.trava(TirarAcesso("PUBLIC", "d2"), m) is None


def test_trava_tirar_connect_direto_e_de_um_grupo():
    m = modelo_exemplo()
    m.eu, m.banco_atual = "r16", "d2"
    m.bancos["d2"].acesso_direto, m.bancos["d2"].publico = ("r11", "r16"), False
    assert "sistema" in _recusa(m, TirarAcesso("r16", "d2"))
    m = modelo_exemplo()
    m.eu, m.banco_atual = "r15", "d1"                               # bruno entra em estudos por meio → leitura
    assert "estudos" in _recusa(m, TirarAcesso("r12", "d1"))
    assert rs.trava(TirarAcesso("PUBLIC", "d2"), m) is None         # outro banco: não é o atual


def test_trava_sair_do_grupo_ou_remover_o_grupo_que_da_o_acesso():
    m = modelo_exemplo()
    m.eu, m.banco_atual = "r13", "d1"                               # ana entra em estudos pelo grupo leitura
    assert "estudos" in _recusa(m, TirarDoGrupo("r13", "r12"))
    assert "estudos" in _recusa(m, RemoverPapeis(("r12",), "r10"))
    m.eu = "r15"                                                    # bruno: o grupo do meio da corrente
    assert "estudos" in _recusa(m, TirarDoGrupo("r14", "r12"))
    assert "estudos" in _recusa(m, RemoverPapeis(("r14",), "r10"))
    assert rs.trava(TirarDoGrupo("r13", "r12"), m) is None          # tirar outra pessoa não tranca bruno


def test_trava_trocar_o_dono_quando_eu_entra_so_como_dono():
    m = modelo_exemplo()
    m.eu, m.banco_atual = "r11", "d2"
    m.bancos["d2"].publico = False                                  # app entra em sistema só como dono
    assert "sistema" in _recusa(m, TrocarDonoDoBanco("d2", "r16"))
    m.bancos["d2"].publico = True                                   # pelo PUBLIC ainda entra
    assert rs.trava(TrocarDonoDoBanco("d2", "r16"), m) is None


def test_trava_sem_banco_atual_so_olha_os_papeis():
    m = modelo_exemplo()
    m.eu = "r16"
    assert m.banco_atual is None
    assert rs.trava(TirarAcesso("PUBLIC", "d2"), m) is None


def test_trava_vale_pelo_estado_final_do_rascunho():
    """Uma operação comum acrescentada depois de uma remoção roda antes dela: se, por isso, a remoção passar a
    trancar a pessoa, o acrescentar recusa."""
    m = modelo_exemplo()
    m.papeis["r16"].superusuario = True
    m.eu, m.banco_atual = "r13", "d2"                               # ana, em sistema, entra pelo PUBLIC
    m.bancos["d2"].acesso_direto = ("r11", "r12")                   # e pelo grupo leitura
    r = RascunhoServidor(m)
    r.acrescentar(TirarAcesso("PUBLIC", "d2"))                      # ainda entra pelo grupo
    with pytest.raises(ValueError):
        r.acrescentar(RemoverPapeis(("r12",), "r16"))
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r12",), "r16"))                   # ainda entra pelo PUBLIC
    with pytest.raises(ValueError) as e:
        r.acrescentar(TirarAcesso("PUBLIC", "d2"))                  # roda antes da remoção: ela passaria a trancar
    assert "sistema" in str(e.value)
    assert len(r.operacoes) == 1 and r.invalidas == set()


def test_trocar_dono_depois_da_remocao_vale_como_roda():
    """Uma troca de dono acrescentada depois da remoção roda antes dela: a remoção não desfaz a troca (a vista e
    os grupos contam a mesma história)."""
    m = modelo_exemplo()
    m.bancos["d4"] = Banco("d4", "da_carla", "r16", True, ("r16",), True)
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r16",), "r11", ("da_carla",)))
    r.acrescentar(TrocarDonoDoBanco("d4", "r13"))
    assert r.vista().bancos["d4"].dono == "r13"
    assert r.grupos() == [(None, "ALTER DATABASE da_carla OWNER TO ana;"),
                          ("da_carla", "REASSIGN OWNED BY carla TO app;\nDROP OWNED BY carla;"),
                          (None, "DROP ROLE carla;")]


def test_senha_de_nome_hostil_vai_citada_no_alter_role_e_no_comentario():
    m = modelo_exemplo()
    m.papeis["r16"].nome = "c'x\n; DROP ROLE jared; --"
    r = RascunhoServidor(m)
    r.acrescentar(TrocarSenha("r16"))
    assert r.passos()[0][3] == []
    texto = r.sql_texto()
    assert len(texto.splitlines()) == 2 and all(ln.startswith("--") for ln in texto.splitlines())
    g = r.grupos(senhas={"r16": HASH})
    assert g == [(None, f'ALTER ROLE "c\'x\n; DROP ROLE jared; --" PASSWORD \'{HASH}\';')]


def test_por_no_grupo_de_quem_nao_herda_nao_da_o_acesso_do_grupo():
    """NOINHERIT: a pertença nova não herda (16+: o padrão vem do rolinherit; até o 15, o rolinherit decide)."""
    m = modelo_exemplo()
    m.eu, m.banco_atual = "r16", "d2"                               # carla entra em sistema pelo PUBLIC
    m.papeis["r16"].herda = False
    m.bancos["d2"].acesso_direto = ("r11", "r12")                   # leitura tem CONNECT em sistema
    r = RascunhoServidor(m)
    r.acrescentar(PorNoGrupo("r16", "r12"))
    assert r.vista().papeis["r16"].nao_herda == ("r12",)
    with pytest.raises(ValueError) as e:
        r.acrescentar(TirarAcesso("PUBLIC", "d2"))
    assert "sistema" in str(e.value)
    m.papeis["r16"].herda = True                                    # quem herda pode
    r = RascunhoServidor(m)
    r.acrescentar(PorNoGrupo("r16", "r12"))
    r.acrescentar(TirarAcesso("PUBLIC", "d2"))


def test_remover_quem_deu_o_connect_tira_o_que_ele_deu_e_a_trava_ve():
    """adm2 tinha CONNECT com opção de conceder e deu a eu2 (e ao PUBLIC): o DROP OWNED BY adm2 revoga em cascata."""
    m = modelo_exemplo()
    m.papeis["r20"] = _papel("r20", "adm2")
    m.papeis["r21"] = _papel("r21", "eu2")
    m.bancos["d5"] = Banco("d5", "x2", "r10", True, ("r10", "r20", "r21"), True,
                           concedido_por={"r10": {"r10": False}, "r20": {"r10": True}, "r21": {"r20": False},
                                          "PUBLIC": {"r20": False}})
    m.eu, m.banco_atual = "r21", "d5"
    texto = _recusa(m, RemoverPapeis(("r20",), "r10", ("x2",)))
    assert "x2" in texto
    m.eu, m.banco_atual = "r10", None
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r20",), "r10", ("x2",)))
    b = r.vista().bancos["d5"]
    assert b.acesso_direto == ("r10",) and b.publico is False and b.concedido_por == {"r10": {"r10": False}}
    assert servidor.acesso(r.modelo(), "r21", "d5") == "nenhum" and servidor.acesso(r.modelo(), "r16", "d5") == "nenhum"


def test_cascata_do_connect_segue_a_opcao_de_conceder():
    """Corrente dono → carla* → ana* → bruno; app tem de carla e do dono. Remover carla: ana perde (só tinha de
    carla), bruno perde (ana já não pode dar), app fica pelo dono — e o concedente que caiu sai da lista dela."""
    m = modelo_exemplo()
    m.bancos["d5"] = Banco("d5", "x2", "r10", True, ("r10", "r13", "r15", "r16", "r11"), False,
                           concedido_por={"r16": {"r10": True}, "r13": {"r16": True}, "r15": {"r13": False},
                                          "r11": {"r16": False, "r10": False}})
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r16",), "r10"))
    b = r.vista().bancos["d5"]
    assert b.acesso_direto == ("r10", "r11") and b.concedido_por == {"r11": {"r10": False}}


def test_cascata_s3_quem_fica_com_o_connect_sem_opcao_nao_segura_o_que_deu():
    """S3: ana tem c*/carla e c/dono e deu a bruno. Remover carla: ana continua entrando (pelo dono), mas sem a
    opção de conceder — o que deu a bruno cai. Bruno, se for quem está, fica trancado: a trava recusa."""
    m = modelo_exemplo()
    m.bancos["d5"] = Banco("d5", "x3", "r10", True, ("r10", "r16", "r13", "r15"), False,
                           concedido_por={"r16": {"r10": True}, "r13": {"r16": True, "r10": False},
                                          "r15": {"r13": False}})
    m.papeis["r15"].membro_de = ()
    m.eu, m.banco_atual = "r15", "d5"
    assert "x3" in _recusa(m, RemoverPapeis(("r16",), "r10"))
    m.eu, m.banco_atual = "r10", None
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r16",), "r10"))
    b = r.vista().bancos["d5"]
    assert b.acesso_direto == ("r10", "r13") and b.concedido_por == {"r13": {"r10": False}}
    m.bancos["d5"].concedido_por["r13"]["r10"] = True                # com a opção vinda do dono, ana segura
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r16",), "r10"))
    assert r.vista().bancos["d5"].acesso_direto == ("r10", "r13", "r15")


def test_dono_removido_passa_o_que_deu_ao_novo_dono():
    m = modelo_exemplo()
    m.bancos["d5"] = Banco("d5", "x2", "r16", True, ("r16", "r13"), False,
                           concedido_por={"r16": {"r16": False}, "r13": {"r16": False}})
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r16",), "r11"))
    b = r.vista().bancos["d5"]
    assert b.dono == "r11" and b.acesso_direto == ("r11", "r13")
    assert b.concedido_por == {"r11": {"r11": False}, "r13": {"r11": False}}


def test_pertenca_cai_so_quando_todas_as_linhas_sao_dos_removidos_sem_cascata():
    """S4 (16+): app deu leitura a carla com ADMIN, carla deu a bruno. Remover app: a pertença de carla cai (era só
    de app); a de bruno, dada por carla, fica (o PostgreSQL 18 não leva adiante)."""
    m = modelo_exemplo()
    m.papeis["r11"].membro_de, m.papeis["r11"].concedido_por = ("r12",), {"r12": {"r10": (True, True)}}
    m.papeis["r16"].membro_de, m.papeis["r16"].concedido_por = ("r12",), {"r12": {"r11": (True, True)}}
    m.papeis["r15"].membro_de = ("r14", "r12")
    m.papeis["r15"].concedido_por = {"r12": {"r16": (True, True)}, "r14": {"r10": (True, True)}}
    m.papeis["r13"].concedido_por = {"r12": {"r11": (True, True), "r10": (True, True)}}   # ana: de app e de jared
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r11",), "r10"))
    v = r.vista()
    assert v.papeis["r16"].membro_de == () and v.papeis["r16"].concedido_por == {}
    assert v.papeis["r15"].membro_de == ("r14", "r12")
    assert v.papeis["r13"].membro_de == ("r12",) and v.papeis["r13"].concedido_por == {"r12": {"r10": (True, True)}}
    m.eu, m.banco_atual = "r16", "d1"                               # carla entra em estudos só por leitura
    assert "estudos" in _recusa(m, RemoverPapeis(("r11",), "r10"))


def test_inherit_da_pertenca_vem_das_linhas_que_ficam():
    """ana está em leitura por app (INHERIT) e por jared (INHERIT FALSE). Remover app: continua membro, mas sem
    herdar — e perde o CONNECT de estudos, que vinha de leitura: a trava recusa se for ela."""
    m = modelo_exemplo()
    m.papeis["r13"].concedido_por = {"r12": {"r11": (True, True), "r10": (False, False)}}
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r11",), "r10"))
    ana = r.vista().papeis["r13"]
    assert ana.membro_de == ("r12",) and ana.nao_herda == ("r12",) and ana.sem_set == ("r12",)
    assert servidor.acesso(r.modelo(), "r13", "d1") == "nenhum"
    m.eu, m.banco_atual = "r13", "d1"
    assert "estudos" in _recusa(m, RemoverPapeis(("r11",), "r10"))


def test_de_dict_avanca_o_proximo_e_acrescentar_recusado_nao_mexe_nos_nomes():
    d = {"operacoes": [{"tipo": "criar_papel", "papel": {"id": "novo-p5", "nome": "x", "login": False,
                                                         "superusuario": False, "cria_bancos": False,
                                                         "cria_papeis": False}}], "proximo": 1}
    r = RascunhoServidor.de_dict(d, modelo_exemplo())
    assert r.proximo == 6 and r.novo_id("p") == "novo-p6"
    op = PorNoGrupo("r13", "r12")                                   # já é membro: recusada
    with pytest.raises(ValueError):
        r.acrescentar(op)
    assert op.nomes == {}


# ── avisos do servidor e o pré-voo ──

from ratt.db import catalogo_servidor
from ratt.db import checagem_servidor as cs
from ratt.db.catalogo_servidor import Privilegios


class _ConexaoFalsa:
    def __init__(self, banco):
        self.banco = banco


def _conectar_falso(fora=(), chamados=None):
    """`conectar(nome)` que devolve uma conexão falsa (o banco dela é o nome) e None para os bancos `fora`."""
    def conectar(nome):
        if chamados is not None:
            chamados.append(nome)
        return None if nome in fora else _ConexaoFalsa(nome)
    return conectar


def _catalogo_falso(monkeypatch, posse=None, privilegios=None, padrao=None, sessoes=None, cifra="scram-sha-256",
                    dependencias=None):
    """As leituras de `catalogo_servidor` trocadas por dicionários {(banco, papel): valor}. Devolve o que o aviso
    passou como `nao_ficam` a cada `privilegios_no_banco` (lista de conjuntos)."""
    posse, privilegios, padrao, sessoes = posse or {}, privilegios or {}, padrao or {}, sessoes or {}
    dependencias, nao_ficam = dependencias or {}, []

    def privs(c, nomes, *a, **k):
        nao_ficam.append(set(k.get("nao_ficam", ())))
        return {n: privilegios.get((c.banco, n), Privilegios(0, 0, 0)) for n in nomes}
    monkeypatch.setattr(catalogo_servidor, "posse_no_banco",
                        lambda c, nomes, *a: {n: posse.get((c.banco, n), {}) for n in nomes})
    monkeypatch.setattr(catalogo_servidor, "privilegios_no_banco", privs)
    monkeypatch.setattr(catalogo_servidor, "dependencias_por_banco", lambda c, nomes, *a: dict(dependencias))
    monkeypatch.setattr(catalogo_servidor, "permissoes_padrao",
                        lambda c, nomes, *a: {n: padrao.get((c.banco, n), 0) for n in nomes})
    monkeypatch.setattr(catalogo_servidor, "sessoes", lambda c, nomes, *a: {n: sessoes.get(n, 0) for n in nomes})
    monkeypatch.setattr(catalogo_servidor, "password_encryption", lambda c, *a: cifra)
    return nao_ficam


def _resumo(avisos):
    return [(a.nivel, a.chave, a.texto()) for a in avisos]


def _com(avisos, chave):
    return [a for a in avisos if a.chave == chave]


def test_aviso_de_remocao_por_banco_posse_privilegios_padrao_e_nao_verificado(monkeypatch):
    m = modelo_exemplo()
    m.bancos["d4"] = Banco("d4", "da_carla", "r16", True, ("r16",), True)
    nao_ficam = _catalogo_falso(monkeypatch,
                    posse={("estudos", "ana"): {"tabela": 2, "funcao": 1}},
                    privilegios={("estudos", "ana"): Privilegios(3, 1, 2)},
                    padrao={("estudos", "carla"): 1})
    chamados = []
    op = RemoverPapeis(("r13", "r16"), "r11", ("estudos", "sistema", "da_carla"))
    avisos = cs.avisos_da_operacao_servidor(op, RascunhoServidor(m), _conectar_falso({"sistema"}, chamados))
    assert "fechado" not in chamados                                # não aceita conexão: nem tenta
    posse, = _com(avisos, "chk.posse_passa")
    assert posse.nivel == "info" and posse.alvo == "r13"
    assert all(x in posse.texto() for x in ("estudos", "ana", "app", "2 tabelas", "1 função"))
    tem, = _com(avisos, "chk.priv_tem_somem")
    assert tem.nivel == "atencao" and "3" in tem.texto() and "ana" in tem.texto()
    dono, = _com(avisos, "chk.priv_deu_dono_fica")
    assert dono.nivel == "info" and "1" in dono.texto() and "app" in dono.texto()   # ficam: vêm do novo dono
    opcao, = _com(avisos, "chk.priv_deu_opcao_somem")
    assert opcao.nivel == "atencao" and "2" in opcao.texto()
    padrao, = _com(avisos, "chk.padrao_somem")
    assert padrao.nivel == "atencao" and padrao.alvo == "r16" and "carla" in padrao.texto()
    nao, = _com(avisos, "chk.nao_verificado")
    assert nao.nivel == "atencao" and nao.alvo == "d2" and "sistema" in nao.texto() and "superusuário" in nao.texto()
    banco, = _com(avisos, "chk.banco_passa")
    assert banco.nivel == "info" and all(x in banco.texto() for x in ("da_carla", "carla", "app"))
    assert not _com(avisos, "chk.sessoes_abertas")
    # o que vai aos outros removidos e ao novo dono não "fica" (some com eles, ou o novo dono já tem tudo)
    assert nao_ficam and all(x == {"ana", "carla", "app"} for x in nao_ficam)


def test_aviso_de_remocao_lista_quem_perde_o_acesso_pela_cascata(monkeypatch):
    """carla deu o CONNECT de x5 a app (tinha a opção de conceder): removida carla, app deixa de entrar."""
    m = modelo_exemplo()
    m.bancos["d5"] = Banco("d5", "x5", "r10", True, ("r10", "r16", "r11"), False,
                           concedido_por={"r10": {"r10": False}, "r16": {"r10": True}, "r11": {"r16": False}})
    _catalogo_falso(monkeypatch)
    op = RemoverPapeis(("r16",), "r10", ("estudos", "sistema", "x5"))
    r = RascunhoServidor(m)
    avisos = cs.avisos_da_operacao_servidor(op, r, _conectar_falso())
    perde = _com(avisos, "chk.perde_acesso")
    assert [(a.nivel, a.alvo) for a in perde] == [("atencao", "r11")] and "x5" in perde[0].texto()
    # a mesma conta com a operação já no rascunho
    r.acrescentar(op)
    assert _resumo(cs.avisos_da_operacao_servidor(op, r, _conectar_falso())) == _resumo(avisos)


def test_aviso_sessoes_abertas_e_password_encryption_md5(monkeypatch):
    m = modelo_exemplo()
    m.banco_atual = "d1"
    _catalogo_falso(monkeypatch, sessoes={"ana": 2}, cifra="md5")
    r = RascunhoServidor(m)
    avisos = cs.avisos_da_operacao_servidor(RemoverPapeis(("r13",), "r11", ("estudos",)), r, _conectar_falso())
    sess, = _com(avisos, "chk.sessoes_abertas")
    assert sess.nivel == "impede" and "2" in sess.texto() and "ana" in sess.texto()
    # o PostgreSQL NÃO recusa o DROP ROLE com sessão aberta: quem recusa é o ratt, porque a sessão quebraria
    assert "recusaria" not in sess.texto() and "quebrada" in sess.texto()
    usuario = CriarPapel(Papel("novo-p1", "nova", True, False, False, False))
    grupo = CriarPapel(Papel("novo-p2", "grupo", False, False, False, False))
    for op in (usuario, TrocarSenha("r16")):
        md5, = _com(cs.avisos_da_operacao_servidor(op, r, _conectar_falso()), "chk.md5")
        assert md5.nivel == "atencao" and "md5" in md5.texto() and "SCRAM" in md5.texto()
    assert not _com(cs.avisos_da_operacao_servidor(grupo, r, _conectar_falso()), "chk.md5")
    assert not _com(cs.avisos_da_operacao_servidor(usuario, r, _conectar_falso({"estudos"})), "chk.md5")
    _catalogo_falso(monkeypatch, cifra="scram-sha-256")
    assert not _com(cs.avisos_da_operacao_servidor(usuario, r, _conectar_falso()), "chk.md5")


def test_aviso_public_e_quem_continua_entrando(monkeypatch):
    m = modelo_exemplo()
    m.bancos["d1"].acesso_direto = ("r10", "r12", "r13", "r16")      # ana (também por leitura) e carla direto
    m.bancos["d2"].acesso_direto = ("r11", "r16")                   # carla direto em sistema (e pelo PUBLIC)
    _catalogo_falso(monkeypatch)
    r = RascunhoServidor(m)
    conectar = _conectar_falso()
    dar, = cs.avisos_da_operacao_servidor(DarAcesso("PUBLIC", "d1"), r, conectar)
    assert (dar.nivel, dar.chave) == ("info", "chk.public_dar") and "estudos" in dar.texto()
    tirar = cs.avisos_da_operacao_servidor(TirarAcesso("PUBLIC", "d2"), r, conectar)
    assert _com(tirar, "chk.public_tirar")[0].nivel == "info"
    perdem = {a.alvo for a in _com(tirar, "chk.perde_acesso")}
    assert perdem == {"r12", "r13", "r14", "r15"}                   # só pelo PUBLIC; carla e app têm direto
    pelo_public, = cs.avisos_da_operacao_servidor(TirarAcesso("r16", "d2"), r, conectar)
    assert (pelo_public.chave, pelo_public.nivel) == ("chk.continua_entrando", "info")
    assert "PUBLIC" in pelo_public.texto() and "carla" in pelo_public.texto()
    pelo_grupo, = cs.avisos_da_operacao_servidor(TirarAcesso("r13", "d1"), r, conectar)
    assert pelo_grupo.chave == "chk.continua_entrando" and "leitura" in pelo_grupo.texto()
    assert cs.avisos_da_operacao_servidor(TirarAcesso("r16", "d1"), r, conectar) == []   # sai mesmo: é o pedido
    assert cs.avisos_da_operacao_servidor(TirarAcesso("r16", "d9"), r, conectar) == []   # inválida: nada a avisar


def _prevoo_falso(monkeypatch, agora, sessoes=None, dependencias=None):
    _catalogo_falso(monkeypatch, sessoes=sessoes, dependencias=dependencias)
    monkeypatch.setattr(catalogo_servidor, "modelo_servidor", lambda c, *a: agora.copia())


def test_prevoo_recusa_sessoes_banco_fechado_e_novo_dono_sem_login(monkeypatch):
    base = modelo_exemplo()
    base.bancos["d0"] = Banco("d0", "template0", "r10", False, ("r10",), True, template=True)
    r = RascunhoServidor(base)
    r.acrescentar(RemoverPapeis(("r13",), "r11", ("estudos", "sistema")))
    agora = base.copia()
    agora.papeis["r11"].login = False                              # o novo dono perdeu o LOGIN depois
    _prevoo_falso(monkeypatch, agora, sessoes={"ana": 2}, dependencias={"fechado": 7})
    msgs = cs.prevoo(r, object(), _conectar_falso())
    assert any("ana" in x and "2" in x for x in msgs)
    fechado = [x for x in msgs if "fechado" in x]
    assert len(fechado) == 1 and '"fechado"' not in fechado[0] and "7" in fechado[0]   # com quantas dependências
    assert "ALTER DATABASE fechado ALLOW_CONNECTIONS true;" in fechado[0]
    assert not any("template0" in x for x in msgs)                 # sem dependência deles lá
    assert any("app" in x and "LOGIN" in x for x in msgs)
    assert len(msgs) == 3


def test_prevoo_vazio_quando_tudo_bem_e_cita_nome_com_aspas(monkeypatch):
    base = modelo_exemplo()
    del base.bancos["d3"]
    r = RascunhoServidor(base)
    r.acrescentar(RemoverPapeis(("r13",), "r10", ("estudos", "sistema")))   # o novo dono sou eu (tem LOGIN)
    _prevoo_falso(monkeypatch, base)
    assert cs.prevoo(r, object(), _conectar_falso()) == []
    agora = base.copia()
    agora.bancos["d7"] = Banco("d7", 'Fe"chado', "r10", False, ("r10",), True)
    agora.bancos["d8"] = Banco("d8", "novo_banco", "r10", True, ("r10",), True)
    agora.bancos["d9"] = Banco("d9", "vazio_fechado", "r10", False, ("r10",), True)   # fechado, mas nada deles
    _prevoo_falso(monkeypatch, agora, dependencias={'Fe"chado': 1})
    msgs = cs.prevoo(r, object(), _conectar_falso({"sistema"}))
    assert any('ALTER DATABASE "Fe""chado" ALLOW_CONNECTIONS true;' in x for x in msgs)
    assert any("novo_banco" in x for x in msgs)                    # aceita conexão e não está na remoção
    assert any("sistema" in x for x in msgs)                       # sou superusuário e não consegui entrar
    assert not any("vazio_fechado" in x for x in msgs)
    assert len(msgs) == 3
    # sem remoção no rascunho, nada disso pesa
    r2 = RascunhoServidor(base)
    r2.acrescentar(MudarPoderes("r16", True, False, True, False))
    assert cs.prevoo(r2, object(), _conectar_falso({"sistema"})) == []


def test_prevoo_operacao_que_deixou_de_valer_com_o_servidor_de_agora(monkeypatch):
    base = modelo_exemplo()
    del base.bancos["d3"]
    r = RascunhoServidor(base)
    r.acrescentar(PorNoGrupo("r16", "r12"))
    r.acrescentar(MudarPoderes("r15", True, False, True, False))
    r.operacoes.append(DarAcesso("r999", "d1"))                    # já inválida antes: não é novidade
    agora = base.copia()
    del agora.papeis["r16"]                                        # carla sumiu do servidor
    _prevoo_falso(monkeypatch, agora)
    msgs = cs.prevoo(r, object(), _conectar_falso())
    assert len(msgs) == 1 and "1" in msgs[0] and "carla" in msgs[0]
    assert len(r.operacoes) == 3 and r.base is base                # o rascunho não muda


def test_prevoo_leitura_que_falha_recusa(monkeypatch):
    import psycopg

    def falha(c, *a):
        raise psycopg.OperationalError("caiu")
    monkeypatch.setattr(catalogo_servidor, "modelo_servidor", falha)
    msgs = cs.prevoo(RascunhoServidor(modelo_exemplo()), object(), _conectar_falso())
    assert len(msgs) == 1 and "caiu" in msgs[0]


def test_aviso_quem_sai_de_um_grupo_pela_remocao_ou_pela_cascata(monkeypatch):
    m = modelo_exemplo()
    _catalogo_falso(monkeypatch)
    r = RascunhoServidor(m)
    # remover o grupo leitura: ana e meio deixam de ser membros (bruno continua em meio)
    avisos = cs.avisos_da_operacao_servidor(RemoverPapeis(("r12",), "r10"), r, _conectar_falso())
    sai = sorted((a.alvo, a.texto()) for a in _com(avisos, "chk.sai_do_grupo"))
    assert [a for a, _ in sai] == ["r13", "r14"] and all("leitura" in x for _, x in sai)
    assert all(a.nivel == "atencao" for a in _com(avisos, "chk.sai_do_grupo"))
    # a pertença de ana em leitura foi dada só por app (16+): removido app, ana sai junto
    m.papeis["r13"].concedido_por = {"r12": {"r11": (True, True)}}
    avisos = cs.avisos_da_operacao_servidor(RemoverPapeis(("r11",), "r10"), RascunhoServidor(m), _conectar_falso())
    sai, = _com(avisos, "chk.sai_do_grupo")
    assert sai.alvo == "r13" and "ana" in sai.texto() and "leitura" in sai.texto()
    # tirar do grupo é o pedido: não é aviso
    assert not _com(cs.avisos_da_operacao_servidor(TirarDoGrupo("r13", "r12"), RascunhoServidor(m), _conectar_falso()),
                    "chk.sai_do_grupo")


# ── a sessão do servidor: disco, parcial, bancos refeitos; senhas e sudo (o que não precisa de servidor) ──

from ratt.db import guardar
from ratt.db import painel as _painel
from ratt.db import sessao as _sessao


def test_prevoo_novo_dono_criado_no_proprio_rascunho(monkeypatch):
    """O novo dono pode ser um papel que o rascunho cria antes (as comuns rodam antes da remoção): o pré-voo olha a
    vista, não quebra por ele ainda não existir no servidor."""
    base = modelo_exemplo()
    del base.bancos["d3"]
    r = RascunhoServidor(base)
    n = r.novo_id("p")
    r.acrescentar(CriarPapel(Papel(n, "carlos", True, False, False, False)))
    r.acrescentar(RemoverPapeis(("r13",), n, ("estudos", "sistema")))
    _prevoo_falso(monkeypatch, base)
    assert cs.prevoo(r, object(), _conectar_falso()) == []
    r2 = RascunhoServidor(base)
    n2 = r2.novo_id("p")
    r2.acrescentar(CriarPapel(Papel(n2, "grupo", False, False, False, False)))
    r2.acrescentar(RemoverPapeis(("r13",), n2, ("estudos", "sistema")))
    msgs = cs.prevoo(r2, object(), _conectar_falso())
    assert len(msgs) == 1 and "grupo" in msgs[0] and "LOGIN" in msgs[0]


def _sessao_srv(monkeypatch, tmp_path, porta=5999):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return _sessao.SessaoServidor(porta)


def test_sessao_servidor_grava_em_porta_servidor_e_le_de_volta(monkeypatch, tmp_path):
    s = _sessao_srv(monkeypatch, tmp_path)
    assert s.abrir(modelo_exemplo()) == []
    ouvidos = []
    s.ouvir(lambda: ouvidos.append(1))
    s.acrescentar(PorNoGrupo("r16", "r12"))
    arquivo = tmp_path / "ratt" / "canvas" / "5999-servidor" / "rascunho.json"
    assert json.loads(arquivo.read_text(encoding="utf-8"))["operacoes"][0]["tipo"] == "por_no_grupo"
    assert ouvidos == [1]
    outra = _sessao.SessaoServidor(5999)
    outra.abrir(modelo_exemplo())
    assert len(outra.rascunho.operacoes) == 1 and outra.rascunho.invalidas == set()
    assert s.desfazer() is not None and not arquivo.exists()      # vazio apaga o arquivo
    assert s.cabecalho().startswith("ratt db") and "5999" in s.cabecalho()


def test_sessao_servidor_arquivo_ilegivel_vai_para_o_lado_e_sem_ler_nao_grava(monkeypatch, tmp_path):
    s = _sessao_srv(monkeypatch, tmp_path)
    arquivo = tmp_path / "ratt" / "canvas" / "5999-servidor" / "rascunho.json"
    arquivo.parent.mkdir(parents=True)
    arquivo.write_text("{ quebrado", encoding="utf-8")
    avisos = s.abrir(modelo_exemplo())
    assert len(avisos) == 1 and "corrompido" in avisos[0]
    assert not arquivo.exists() and len(list(arquivo.parent.glob("rascunho.corrompido-*.json"))) == 1
    s2 = _sessao_srv(monkeypatch, tmp_path, porta=6000)

    def nao_le(porta):
        raise PermissionError("sem permissão")

    monkeypatch.setattr(guardar, "ler_rascunho_servidor", nao_le)
    avisos = s2.abrir(modelo_exemplo())
    assert s2.sem_gravar and len(avisos) == 1 and "servidor" in avisos[0]
    s2.acrescentar(PorNoGrupo("r16", "r12"))
    assert not (tmp_path / "ratt" / "canvas" / "6000-servidor" / "rascunho.json").exists()


def test_sessao_servidor_marca_parcial_fica_no_disco_ate_a_operacao_sair(monkeypatch, tmp_path):
    s = _sessao_srv(monkeypatch, tmp_path)
    arquivo = tmp_path / "ratt" / "canvas" / "5999-servidor" / "rascunho.json"
    arquivo.parent.mkdir(parents=True)
    op = rs._op_para_dict(RemoverPapeis(("r13",), "r10", ("estudos", "sistema")))
    arquivo.write_text(json.dumps({"operacoes": [op], "proximo": 1,
                                   "parcial": {"op": 0, "mensagem": "feito em estudos; falhou em sistema: x"}}),
                       encoding="utf-8")
    s.abrir(modelo_exemplo())
    assert s.falha == 0 and s.mensagem_falha.startswith("feito em estudos")
    s.acrescentar(PorNoGrupo("r16", "r12"))                        # outra mudança não apaga a marca de parcial
    assert s.falha == 0 and json.loads(arquivo.read_text(encoding="utf-8"))["parcial"]["op"] == 0
    s.tirar([0])
    assert s.falha is None and s.mensagem_falha is None
    assert "parcial" not in json.loads(arquivo.read_text(encoding="utf-8"))
    for ruim in ({"op": 5, "mensagem": "x"}, {"op": True, "mensagem": "x"}, {"op": 0}, "lixo"):   # marca estragada
        arquivo.write_text(json.dumps({"operacoes": [op], "proximo": 1, "parcial": ruim}), encoding="utf-8")
        s3 = _sessao.SessaoServidor(5999)
        s3.abrir(modelo_exemplo())
        assert len(s3.rascunho.operacoes) == 1 and s3.falha is None


def test_sessao_servidor_rebase_refaz_os_bancos_da_remocao(monkeypatch, tmp_path):
    s = _sessao_srv(monkeypatch, tmp_path)
    s.abrir(modelo_exemplo())
    s.acrescentar(RemoverPapeis(("r13",), "r10", ("estudos",)))
    op = s.rascunho.operacoes[0]
    s.rebase(modelo_exemplo())
    assert op.bancos == ("estudos", "sistema")                     # os que aceitam conexão (fechado não)
    agora = modelo_exemplo()
    agora.bancos["d8"] = Banco("d8", "novo", "r10", True, ("r10",), True)
    s.rebase(agora)
    assert op.bancos == ("estudos", "sistema", "novo")
    arquivo = tmp_path / "ratt" / "canvas" / "5999-servidor" / "rascunho.json"
    assert json.loads(arquivo.read_text(encoding="utf-8"))["operacoes"][0]["bancos"] == ["estudos", "sistema", "novo"]
    assert "-- no banco novo:" in s.rascunho.sql_texto()


def test_obter_servidor_uma_por_porta():
    class App:
        pass

    class Fonte:
        def __init__(self, porta):
            self.porta, self.banco = porta, "x"

    app = App()
    a = _sessao.obter_servidor(app, Fonte(5432))
    assert _sessao.obter_servidor(app, Fonte(5432)) is a and _sessao.obter_servidor(app, Fonte(5433)) is not a


def test_lotes_tem_o_indice_da_operacao_e_o_hash_nao_aparece_no_texto_mostrado():
    base = modelo_exemplo()
    r = RascunhoServidor(base)
    n = r.novo_id("p")
    r.acrescentar(RemoverPapeis(("r15",), "r10", ("estudos", "sistema")))
    r.acrescentar(CriarPapel(Papel(n, "ana2", True, False, False, False)))
    r.acrescentar(TrocarSenha("r16"))
    assert r.senhas_pedidas() == [(n, "ana2"), ("r16", "carla")]
    lotes = r.lotes({n: "SCRAM-SHA-256$h1", "r16": "SCRAM-SHA-256$h2"})
    assert [d for d, _ in lotes] == [None, "estudos", "sistema", None]
    comuns = lotes[0][1]
    assert [i for i, _, _ in comuns] == [1, 1, 2] and all(i == 0 for _, cmds in lotes[1:] for i, _, _ in cmds)
    assert "SCRAM-SHA-256$h1" in comuns[1][1] and "SCRAM-SHA-256$" not in comuns[1][2] + comuns[2][2]
    assert comuns[1][2].startswith("ALTER ROLE ana2 PASSWORD <")
    assert r.grupos({n: "SCRAM-SHA-256$h1", "r16": "SCRAM-SHA-256$h2"}) == [
        (d, "\n".join(sql + ";" for _, sql, _ in cmds)) for d, cmds in lotes]


def test_painel_desenha_comandos_com_banco_de_destino():
    linhas = _painel._linhas_comandos([("estudos", "REASSIGN OWNED BY ana TO jared"), (None, "DROP ROLE ana")])
    assert linhas == ["-- no banco estudos:", "REASSIGN OWNED BY ana TO jared;", "-- no servidor (de qualquer banco):",
                      "DROP ROLE ana;"]
    assert _painel._linhas_comandos(["SELECT 1", "SELECT 2"]) == ["SELECT 1;", "SELECT 2;"]


def test_pedir_senha_terminal_duas_vezes_vazia_nao_vale_e_ctrl_c_sobe():
    respostas = iter(["", "abc", "abd", "abc", "abc"])
    mostrado = []
    assert _painel.pedir_senha_terminal("ana", ler=lambda p: next(respostas), mostrar=mostrado.append) == "abc"
    assert len(mostrado) == 2                                       # a vazia e a que não bateu

    def ctrl_c(p):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _painel.pedir_senha_terminal("ana", ler=ctrl_c, mostrar=mostrado.append)


def test_confirmar_terminal_so_sim_explicito():
    assert _painel.confirmar_terminal("?", ler=lambda p: " S ") is True
    assert _painel.confirmar_terminal("?", ler=lambda p: "") is False

    def fim(p):
        raise EOFError

    assert _painel.confirmar_terminal("?", ler=fim) is False


def test_comando_psql_sem_sql_e_nome_de_banco_com_igual_vai_como_dbname(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert _sessao.comando_psql("estudos", False) == ["sudo", "-u", "postgres", "psql", "-X", "-q", "-d", "estudos",
                                                      "-v", "ON_ERROR_STOP=1", "-1"]
    assert "-1" not in _sessao.comando_psql("estudos", True)
    argv = _sessao.comando_psql("host=/tmp x", False)
    assert argv[argv.index("-d") + 1] == "dbname='host=/tmp x'"
    assert _sessao.comando_psql("estudos", False, 5433, "/run/postgresql")[4:12] == [
        "-X", "-q", "-h", "/run/postgresql", "-p", "5433", "-d", "estudos"]
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert _sessao.comando_psql("estudos", False)[:4] == ["runuser", "-u", "postgres", "--"]


def test_erro_do_psql_sem_o_comando_e_sem_hash():
    saida = ("psql:<stdin>:3: ERROR:  syntax error at or near \"x\"\n"
             "LINE 1: ALTER ROLE a PASSWORD 'SCRAM-SHA-256$4096:abc$def:ghi'\n"
             "                                ^\n")
    linha, texto = _sessao._erro_psql(saida)
    assert linha == 3 and texto == 'ERROR:  syntax error at or near "x"'
    assert _sessao._sem_segredos("a SCRAM-SHA-256$4096:abc$def:ghi b", []) == "a <hash> b"
    assert _sessao._sem_segredos("tem segredo", ["segredo"]) == "tem <hash>"
    cmds = [(0, "CREATE ROLE a", ""), (0, "ALTER ROLE a\nPASSWORD 'x'", ""), (1, "GRANT g TO a", "")]
    assert [_sessao._comando_da_linha(cmds, n, 1) for n in (1, 2, 3, 4, 9)] == [0, 1, 1, 2, 2]
    assert _sessao._comando_da_linha(cmds, None, 1) == 0


def test_trocar_ids_leva_a_operacao_para_o_id_do_catalogo():
    op = RemoverPapeis(("r13", "novo-p1"), "novo-p2", ("novo-p1",), {"r13": "ana"})
    _sessao._trocar_ids(op, {"novo-p2": ("r40", "carlos")})
    assert op.novo_dono == "r40" and op.papeis == ("r13", "novo-p1") and op.bancos == ("novo-p1",)
    assert op.nomes == {"r13": "ana", "r40": "carlos"}
    criar = CriarPapel(Papel("novo-p3", "d", True, False, False, False, membro_de=("novo-p2",)))
    _sessao._trocar_ids(criar, {"novo-p2": ("r40", "carlos")})
    assert criar.papel.membro_de == ("r40",) and criar.papel.id == "novo-p3"
