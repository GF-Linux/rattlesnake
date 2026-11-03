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
                                                      "-v", "VERBOSITY=verbose", "-f", "-", "-v", "ON_ERROR_STOP=1",
                                                      "-1"]
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
    linha, texto, estado = _sessao._erro_psql(saida)
    assert linha == 3 and texto == 'ERROR:  syntax error at or near "x"' and estado is None
    # o modo verboso (VERBOSITY=verbose): o SQLSTATE do erro (a última linha do psql; antes, um aviso) sai do texto,
    # e a LOCATION (traduzida ou não) também
    saida = ("psql:<stdin>:1: WARNING:  25001: there is already a transaction in progress\n"
             "LOCALIZAÇÃO:  BeginTransactionBlock, xact.c:3954\n"
             "psql:<stdin>:4: ERRO:  2BP01: role \"ana\" cannot be dropped because some objects depend on it\n"
             "DETALHE:  owner of table t_ana\nLOCATION:  DropRole, user.c:1297\n")
    linha, texto, estado = _sessao._erro_psql(saida)
    assert linha == 4 and estado == "2BP01"
    assert texto == ('WARNING:  there is already a transaction in progress — ERRO:  role "ana" cannot be dropped '
                     'because some objects depend on it — DETALHE:  owner of table t_ana')
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


# ── Task 5: permissões de tabela e de schema no rascunho do schema ──

from ratt.db import checagem as _checagem, rascunho as _rascunho
from ratt.db.modelo import Coluna as _Coluna, Modelo as _Modelo, Tabela as _Tabela
from ratt.db.rascunho import Conceder, PermissaoNoSchema, Revogar


def _schema_perm(esquema="public"):
    """Um schema com duas tabelas (uma de nome hostil) e papéis de nomes hostis; a pessoa é `r1` (app)."""
    tabelas = {
        "t1": _Tabela("t1", esquema, "a", [_Coluna("t1.c1", "id", "bigint", pk=True)]),
        "t2": _Tabela("t2", esquema, 'Tabela "X"', [_Coluna("t2.c1", "id", "bigint", pk=True)]),
        "t3": _Tabela("t3", esquema, "ação", [_Coluna("t3.c1", "id", "bigint")]),
    }
    papeis = {"r1": "app", "r2": "Leitura Ü", "r3": 'o"quote', "r4": "select", "r5": "Eu Mesmo"}
    return _Modelo(esquema, tabelas, {}, papeis=papeis, eu="r1")


def _cmds(op, m):
    return _rascunho.comandos_de(op, m)


def test_conceder_e_revogar_um_comando_com_as_listas_e_nomes_hostis():
    m = _schema_perm()
    op = Conceder(("r1", "r2", "PUBLIC"), ("t1", "t2"), ("I", "S"))
    assert _cmds(op, m) == ['GRANT SELECT, INSERT ON public.a, public."Tabela ""X""" TO app, "Leitura Ü", PUBLIC']
    op = Revogar(("r3", "r4"), ("t3",), ("t", "R", "T", "D", "U", "I", "S"))
    assert _cmds(op, m) == ['REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON public."ação" '
                            'FROM "o""quote", "select"']
    # schema de nome hostil também vai citado
    m2 = _schema_perm('Meu "Esq"')
    assert _cmds(Conceder(("r2",), ("t1",), ("S",)), m2) == ['GRANT SELECT ON "Meu ""Esq""".a TO "Leitura Ü"']


def test_conceder_futuras_gera_alter_default_privileges_for_role():
    m = _schema_perm("Vendas")
    op = Conceder(("r2", "PUBLIC"), ("t1",), ("S", "I"), futuras=True, por="r5")
    assert _cmds(op, m) == [
        'GRANT SELECT, INSERT ON "Vendas".a TO "Leitura Ü", PUBLIC',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "Eu Mesmo" IN SCHEMA "Vendas" GRANT SELECT, INSERT ON TABLES '
        'TO "Leitura Ü", PUBLIC']
    # sem tabela na lista: só as futuras
    assert _cmds(Conceder(("r2",), (), ("S",), futuras=True, por="r1"), m) == [
        'ALTER DEFAULT PRIVILEGES FOR ROLE app IN SCHEMA "Vendas" GRANT SELECT ON TABLES TO "Leitura Ü"']
    assert _cmds(Revogar(("r2",), ("t1",), ("S",), futuras=True, por="r1"), m) == [
        'REVOKE SELECT ON "Vendas".a FROM "Leitura Ü"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE app IN SCHEMA "Vendas" REVOKE SELECT ON TABLES FROM "Leitura Ü"']


def test_operacao_de_permissao_mal_formada_e_recusada_e_fica_invalida():
    m = _schema_perm()
    for op in (Conceder(("r2",), ("t1",), ("S",), futuras=True),           # futuras sem `por`
               Conceder(("r2",), ("t1",), ("X",)),                         # letra que não é privilégio
               Conceder(("r2",), ("t1",), ("m",)),                         # MAINTAIN não é oferecido
               Conceder((), ("t1",), ("S",)),
               Conceder(("r2",), (), ("S",)),                              # sem tabela e sem futuras
               Conceder(("r2",), ("t1",), ()),
               PermissaoNoSchema("r2", None, None)):
        with pytest.raises(ValueError):
            _rascunho.Rascunho(m).acrescentar(op)
        r = _rascunho.Rascunho(m, [op])
        assert r.invalidas == {0}


def test_por_das_futuras_e_o_id_do_papel_e_invalida_quando_ele_some_ou_muda_de_nome():
    """`por` guarda o id (`r{oid}`), não o nome: entra no `refere()` e nos `nomes`, e o rebase invalida a operação
    quando o papel que cria as tabelas some ou muda de nome — como qualquer outro id."""
    m = _schema_perm("Vendas")
    op = Conceder(("r2",), ("t1",), ("S",), futuras=True, por="r5")
    assert op.refere() == {"r2", "t1", "r5"}
    assert Conceder(("r2",), ("t1",), ("S",), por="r5").refere() == {"r2", "t1"}     # sem futuras, o por não conta
    r = _rascunho.Rascunho(m)
    r.acrescentar(op)
    assert op.nomes["r5"] == "Eu Mesmo"
    assert "Eu Mesmo" in r.passos()[0][2]                       # a descrição diz o nome, não o id
    renomeado = _schema_perm("Vendas")
    renomeado.papeis["r5"] = "Outro"
    assert r.rebase(renomeado) == {0}
    sem = _schema_perm("Vendas")
    del sem.papeis["r5"]
    assert r.rebase(sem) == {0}
    assert r.rebase(_schema_perm("Vendas")) == set()
    # um por que não é papel conhecido (o nome, num rascunho de antes desta versão): recusado ao pôr, inválido ao ler
    with pytest.raises(ValueError):
        _rascunho.Rascunho(m).acrescentar(Conceder(("r2",), ("t1",), ("S",), futuras=True, por="Eu Mesmo"))
    antigo = {"operacoes": [{"tipo": "conceder", "papeis": ["r2"], "tabelas": ["t1"], "privilegios": ["S"],
                             "futuras": True, "por": "Eu Mesmo", "nomes": {"r2": "Leitura Ü", "t1": "a"}}]}
    lido = _rascunho.Rascunho.de_dict(antigo, m)
    assert lido.invalidas == {0} and len(lido.operacoes) == 1   # nada se perde: fica marcada, para tirar


def test_permissao_no_schema_grant_e_revoke():
    m = _schema_perm("Vendas")
    assert _cmds(PermissaoNoSchema("r2", True, True), m) == ['GRANT USAGE, CREATE ON SCHEMA "Vendas" TO "Leitura Ü"']
    assert _cmds(PermissaoNoSchema("PUBLIC", False, None), m) == ['REVOKE USAGE ON SCHEMA "Vendas" FROM PUBLIC']
    assert _cmds(PermissaoNoSchema("r3", True, False), m) == [
        'GRANT USAGE ON SCHEMA "Vendas" TO "o""quote"', 'REVOKE CREATE ON SCHEMA "Vendas" FROM "o""quote"']
    assert PermissaoNoSchema("r3", True, False).refere() == {"r3"}
    assert PermissaoNoSchema("PUBLIC", True, None).refere() == set()


def test_refere_tabelas_e_papeis_e_o_texto_sql():
    m = _schema_perm()
    op = Conceder(("r2", "PUBLIC"), ("t1", "t2"), ("S",))
    assert op.refere() == {"r2", "t1", "t2"}
    r = _rascunho.Rascunho(m)
    r.acrescentar(op)
    assert op.nomes == {"r2": "Leitura Ü", "t1": "a", "t2": 'Tabela "X"'}
    texto = r.sql_texto()
    assert 'GRANT SELECT ON public.a, public."Tabela ""X""" TO "Leitura Ü", PUBLIC;' in texto
    assert texto.startswith("-- 1. ")


def test_papel_que_some_ou_muda_de_nome_invalida_no_rebase():
    m = _schema_perm()
    r = _rascunho.Rascunho(m)
    r.acrescentar(Conceder(("r2",), ("t1",), ("S",)))
    r.acrescentar(PermissaoNoSchema("r3", True, None))
    r.acrescentar(Conceder(("PUBLIC",), ("t2",), ("S",)))
    assert r.invalidas == set()
    sem = _schema_perm()
    del sem.papeis["r2"]
    assert r.rebase(sem) == {0}
    renomeado = _schema_perm()
    renomeado.papeis["r3"] = "outro"
    assert r.rebase(renomeado) == {1}
    sem_tabela = _schema_perm()
    del sem_tabela.tabelas["t2"]
    assert r.rebase(sem_tabela) == {2}
    assert r.rebase(_schema_perm()) == set()


def test_papel_pendente_do_rascunho_do_servidor_e_recusado():
    r = _rascunho.Rascunho(_schema_perm())
    with pytest.raises(ValueError, match="aplique primeiro o rascunho do servidor"):
        r.acrescentar(Conceder(("novo-p1",), ("t1",), ("S",)))
    with pytest.raises(ValueError, match="aplique primeiro o rascunho do servidor"):
        r.acrescentar(PermissaoNoSchema("novo-p2", True, None))
    assert r.operacoes == []


def test_permissoes_vao_e_voltam_do_json_e_rascunho_antigo_ainda_abre():
    import json
    m = _schema_perm()
    r = _rascunho.Rascunho(m)
    r.acrescentar(Conceder(("r2", "PUBLIC"), ("t1", "t2"), ("S", "I"), futuras=True, por="r1"))
    r.acrescentar(Revogar(("r3",), ("t3",), ("D",)))
    r.acrescentar(PermissaoNoSchema("r2", True, None))
    dados = json.loads(json.dumps(r.para_dict()))
    volta = _rascunho.Rascunho.de_dict(dados, m)
    assert volta.operacoes == r.operacoes and volta.sql_texto() == r.sql_texto()
    # um rascunho de antes (só operações de estrutura) abre sobre o modelo novo, e o modelo sem papéis ainda serve
    antigo = {"operacoes": [{"tipo": "renomear", "tabela": "t1", "coluna": None, "novo": "b", "nomes": {"t1": "a"}}],
              "proximo": 1}
    assert _rascunho.Rascunho.de_dict(antigo, m).invalidas == set()
    assert _rascunho.Rascunho.de_dict(antigo, _Modelo("public", m.tabelas)).invalidas == set()
    # forma errada no JSON: corrompido, não um erro solto
    ruim = {"operacoes": [{"tipo": "conceder", "papeis": [["r2"]], "tabelas": ["t1"], "privilegios": ["S"]}]}
    with pytest.raises(_rascunho.RascunhoCorrompido):
        _rascunho.Rascunho.de_dict(ruim, m)
    ruim = {"operacoes": [{"tipo": "permissao_schema", "papel": "r2", "usage": "sim", "create": None}]}
    with pytest.raises(_rascunho.RascunhoCorrompido):
        _rascunho.Rascunho.de_dict(ruim, m)


def test_modelo_do_rascunho_aplicado_guarda_papeis_e_eu():
    r = _rascunho.Rascunho(_schema_perm())
    r.acrescentar(Conceder(("r2",), ("t1",), ("S",)))
    depois = r.modelo()
    assert depois.papeis == _schema_perm().papeis and depois.eu == "r1"


class _ConsultaPerm:
    """Finge o banco para os avisos de permissão: quem tem USAGE no schema e o que `permissoes` leria."""

    def __init__(self, usage, permissoes):
        self._usage, self._permissoes = usage, permissoes

    def erro(self):
        return None

    def usage_no_schema(self, esquema, nomes):
        return {n: self._usage.get(n, False) for n in nomes}

    def permissoes(self, esquema):
        return self._permissoes


def _perms(por_tabela=None, dono=None, schema=None):
    from ratt.db.catalogo import Permissoes
    return Permissoes(por_tabela or {}, dono or {"t1": "r1", "t2": "r3", "t3": "r1"}, schema or {}, {})


def _chaves(avisos):
    return sorted((a.nivel, a.chave, a.alvo) for a in avisos)


def test_aviso_sem_usage_oferece_a_permissao_no_schema():
    r = _rascunho.Rascunho(_schema_perm())
    c = _ConsultaPerm({"app": True, "public": False}, _perms())
    avisos = _checagem.avisos_da_operacao(Conceder(("r2", "PUBLIC"), ("t1",), ("S",)), r, c)
    sem = [a for a in avisos if a.chave == "chk.perm_sem_usage"]
    assert {a.alvo for a in sem} == {"r2", "PUBLIC"} and all(a.nivel == "atencao" for a in sem)
    oferta = next(a.oferta for a in sem if a.alvo == "r2")
    assert oferta == PermissaoNoSchema("r2", usage=True, create=None)
    assert "USAGE" in sem[0].texto()
    # com o USAGE já no rascunho (antes), o aviso some
    r.acrescentar(PermissaoNoSchema("r2", True, None))
    avisos = _checagem.avisos_da_operacao(Conceder(("r2",), ("t1",), ("S",)), r, c)
    assert not [a for a in avisos if a.chave == "chk.perm_sem_usage"]


def test_aviso_dono_ja_tem_tudo_e_futuras():
    r = _rascunho.Rascunho(_schema_perm())
    c = _ConsultaPerm({"o\"quote": True, "Leitura Ü": True}, _perms())
    avisos = _checagem.avisos_da_operacao(Conceder(("r3",), ("t1", "t2"), ("S",)), r, c)
    assert _chaves(avisos) == [("info", "chk.perm_dono", "r3")]
    assert "Tabela \"X\"" in avisos[0].texto()
    avisos = _checagem.avisos_da_operacao(Conceder(("r2",), (), ("S",), futuras=True, por="r1"), r, c)
    assert _chaves(avisos) == [("info", "chk.perm_futuras", "r2")]
    assert "app" in avisos[0].texto()


def test_aviso_revogar_de_si_mesmo_ou_do_public_quando_e_por_ele():
    r = _rascunho.Rascunho(_schema_perm())
    # t2 é de r3; app (a pessoa) só tem SELECT em t2 pelo PUBLIC
    perms = _perms(por_tabela={("PUBLIC", "t2"): frozenset("S"), ("r3", "t2"): frozenset("SIUDTRt")})
    c = _ConsultaPerm({}, perms)
    avisos = _checagem.avisos_da_operacao(Revogar(("r1",), ("t2",), ("S",)), r, c)
    assert _chaves(avisos) == [("atencao", "chk.perm_revogar_si", "r1")]
    avisos = _checagem.avisos_da_operacao(Revogar(("PUBLIC",), ("t2",), ("S",)), r, c)
    assert _chaves(avisos) == [("atencao", "chk.perm_revogar_si", "PUBLIC")]
    # dela mesma no que ela possui não vem pelo PUBLIC; e tirar do PUBLIC o que ela tem direto também não
    assert _checagem.avisos_da_operacao(Revogar(("PUBLIC",), ("t1",), ("S",)), r, c) == []
    perms.por_tabela[("r1", "t2")] = frozenset("S")
    assert _checagem.avisos_da_operacao(Revogar(("PUBLIC",), ("t2",), ("S",)), r, c) == []
    # no schema: tirar o próprio USAGE, ou o do PUBLIC quando é por ele que ela tem
    c = _ConsultaPerm({}, _perms(schema={"PUBLIC": frozenset({"USAGE"})}))
    avisos = _checagem.avisos_da_operacao(PermissaoNoSchema("PUBLIC", False, None), r, c)
    assert _chaves(avisos) == [("atencao", "chk.perm_revogar_si", "PUBLIC")]
    avisos = _checagem.avisos_da_operacao(PermissaoNoSchema("r1", None, False), r, c)
    assert _chaves(avisos) == [("atencao", "chk.perm_revogar_si", "r1")]


# ── Task 6: a tela Usuários (fonte falsa: o modelo do servidor fixo) ──

import asyncio
import threading

from conftest import esperar_que, esperar_tela, esperar_workers

from ratt.db.idioma import t


class FonteServidorFalsa:
    """O servidor de mentira da tela Usuários: o modelo fixo, a assinatura que o teste muda, os avisos pela
    checagem de verdade sem entrar em banco nenhum (`conectar` dá None: "não verificado"). `segurar` faz os avisos
    esperarem o teste soltar."""

    def __init__(self, m):
        self.m = m
        self.porta, self.usuario, self.banco = 5999, "jared", "estudos"
        self.sig = 1
        self.segurar = None             # threading.Event: os avisos só voltam quando ele é posto
        self.fechada = False

    def assinatura(self):
        return self.sig

    def modelo(self):
        return self.m.copia()

    def avisos(self, op, r):
        if self.segurar is not None:
            self.segurar.wait(5)
        return cs.avisos_da_operacao_servidor(op, r, lambda nome: None)

    def fechar(self):
        self.fechada = True


def _app_usuarios(fonte):
    from textual.app import App
    from ratt.db import tela_usuarios

    class AppUsuarios(App):
        def on_mount(self):
            tela = tela_usuarios.TelaUsuarios(fonte)
            tela.VIGIA_SEGUNDOS = 0
            self.push_screen(tela)
    return AppUsuarios()


async def _usuarios_pronta(app):
    from ratt.db import tela_usuarios
    await esperar_que(app._piloto, lambda: isinstance(app.screen, tela_usuarios.TelaUsuarios) and app.screen.carregado)
    await esperar_workers(app)


def _grade_texto(tela):
    """A grade como texto: {nome da linha: [células]} (o nome sem as marcas da frente)."""
    from ratt.db.grade_dados import Grade
    grade = tela.query_one(Grade)
    linhas = {}
    for i in range(grade.row_count):
        celulas = [c.plain if hasattr(c, "plain") else str(c) for c in grade.get_row_at(i)]
        linhas[celulas[0].lstrip("+−▪● ").split(" ")[0]] = celulas
    return linhas


def _texto(widget):
    """O texto de um Static: Text, Syntax (o SQL) ou o que for."""
    c = widget.content
    return getattr(c, "plain", None) or getattr(c, "code", None) or str(c)


def _modelo_com_banco_atual():
    m = modelo_exemplo()
    m.banco_atual = "d1"
    return m


def test_tela_usuarios_grade_mostra_d_c_c_traco_e_a_linha_public(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FonteServidorFalsa(_modelo_com_banco_atual())

    async def roteiro():
        app = _app_usuarios(fonte)
        async with app.run_test(size=(140, 40)) as piloto:
            app._piloto = piloto
            await _usuarios_pronta(app)
            tela = app.screen
            g = _grade_texto(tela)
            bancos = [tela.query_one("#usu_grade").ordered_columns[k].label.plain.split()[0] for k in range(3, 6)]
            assert bancos == ["estudos", "fechado", "sistema"]
            assert g["PUBLIC"][3:] == ["—", "—", "C"]
            assert g["jared"][3:] == ["D", "c", "c"]          # dono de estudos; nos outros, por ser superusuário
            assert g["app"][3:] == ["—", "D", "D"]
            assert g["leitura"][3:] == ["C", "—", "c"]        # CONNECT direto em estudos; PUBLIC em sistema
            assert g["ana"][3:] == ["c", "—", "c"]            # pelo grupo leitura
            assert g["bruno"][3:] == ["c", "—", "c"]          # bruno → meio → leitura
            assert g["carla"][3:] == ["—", "—", "c"]
            assert g["jared"][1] == t("usu.p.super") and g["leitura"][1] == t("usu.p.nao_entra_grupo")
            assert g["ana"][2] == "leitura" and g["carla"][2] == "—"
            assert "●" in tela.query_one("#usu_grade").get_row_at(
                list(g).index("jared"))[0].plain                # a pessoa
            assert list(g)[0] == "PUBLIC" and tela.query_one("#usu_grade").fixed_rows == 1
            assert "sistema" in _texto(tela.query_one("#usu_avisos"))    # PUBLIC entra em sistema
            titulo = _texto(tela.query_one("#usu_titulo"))
            assert "localhost:5999" in titulo and t("usu.titulo") in titulo
    asyncio.run(roteiro())


def test_tela_usuarios_criar_usuario_pelo_formulario_sem_senha_no_sql(monkeypatch, tmp_path):
    from ratt.db import formularios_usuarios as fu
    from ratt.db.painel import PainelSQL
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FonteServidorFalsa(_modelo_com_banco_atual())

    async def roteiro():
        app = _app_usuarios(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            app._piloto = piloto
            await _usuarios_pronta(app)
            tela = app.screen
            await piloto.press("a")
            await esperar_que(piloto, lambda: isinstance(app.screen, fu.FormNovoUsuario))
            form = app.screen
            await esperar_tela(piloto, form)
            for tecla in "dora":
                await piloto.press(tecla)
            await esperar_workers(app)
            assert "CREATE ROLE dora LOGIN;" in _texto(form.query_one("#form_sql"))
            await piloto.click("#por_no_rascunho")
            await esperar_que(piloto, lambda: app.screen is tela)
            ops = tela.sessao.rascunho.operacoes
            assert len(ops) == 1 and isinstance(ops[0], CriarPapel) and ops[0].papel.nome == "dora" and ops[0].papel.login
            plano = tela.query_one(PainelSQL).texto_plano()
            assert "CREATE ROLE dora LOGIN;" in plano
            assert t("usu.senha_ao_aplicar", papel="dora") in plano and "PASSWORD" not in plano
            g = _grade_texto(tela)
            assert "dora" in g and tela.query_one("#usu_grade").get_row_at(list(g).index("dora"))[0].plain.startswith("+")
            caminho = tmp_path / "srv.sql"
            tela._gravar_exportado(caminho)
            texto = caminho.read_text(encoding="utf-8")
            assert "CREATE ROLE dora LOGIN;" in texto and "PASSWORD" not in texto and "\\connect -reuse-previous=on 'estudos'" in texto
    asyncio.run(roteiro())


def test_tela_usuarios_trava_deixa_o_botao_desligado_com_o_motivo(monkeypatch, tmp_path):
    from textual.widgets import Button
    from ratt.db import formularios_usuarios as fu
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FonteServidorFalsa(_modelo_com_banco_atual())

    async def roteiro():
        app = _app_usuarios(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            app._piloto = piloto
            await _usuarios_pronta(app)
            tela = app.screen
            tela.ir_para("r10")                             # a própria linha (jared)
            await piloto.press("x")
            await esperar_que(piloto, lambda: isinstance(app.screen, fu.ConfirmarRemocao))
            form = app.screen
            await esperar_tela(piloto, form)
            await esperar_workers(app)
            assert form.query_one("#por_no_rascunho", Button).disabled
            assert t("usu.trava.remover_si") in _texto(form.query_one("#erro"))
            await piloto.click("#por_no_rascunho")
            assert app.screen is form and tela.sessao.rascunho.operacoes == []
            await piloto.press("escape")
            # tirar de si o SUPERUSER pelo formulário de poderes: também recusa, com o motivo
            await esperar_que(piloto, lambda: app.screen is tela)
            await piloto.press("space")
            await esperar_que(piloto, lambda: isinstance(app.screen, fu.FormPoderes))
            form = app.screen
            await esperar_tela(piloto, form)
            form.query_one("#super").value = False
            await esperar_workers(app)
            await piloto.pause()
            assert form.query_one("#por_no_rascunho", Button).disabled
            assert t("usu.trava.proprio_super") in _texto(form.query_one("#erro"))
    asyncio.run(roteiro())


def test_tela_usuarios_remover_dois_marcados_confirma_com_foco_no_cancelar(monkeypatch, tmp_path):
    from textual.widgets import Button
    from ratt.db import formularios_usuarios as fu
    from ratt.db.rascunho_servidor import RemoverPapeis
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FonteServidorFalsa(_modelo_com_banco_atual())
    fonte.segurar = threading.Event()

    async def roteiro():
        app = _app_usuarios(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            app._piloto = piloto
            await _usuarios_pronta(app)
            tela = app.screen
            tela.ir_para("r13")
            await piloto.press("m")
            tela.ir_para("r16")
            await piloto.press("m")
            assert tela.marcados == {"r13", "r16"}
            await piloto.press("x")
            await esperar_que(piloto, lambda: isinstance(app.screen, fu.ConfirmarRemocao))
            form = app.screen
            await esperar_tela(piloto, form)
            assert app.focused is form.query_one("#cancelar", Button)
            assert form.query_one("#por_no_rascunho", Button).disabled      # os avisos ainda não chegaram
            fonte.segurar.set()
            await esperar_que(piloto, lambda: not form.contando)
            await esperar_workers(app)
            assert not form.query_one("#por_no_rascunho", Button).disabled
            assert t("chk.nao_verificado", banco="estudos") in _texto(form.query_one("#form_avisos"))
            await piloto.click("#por_no_rascunho")
            await esperar_que(piloto, lambda: app.screen is tela)
            ops = tela.sessao.rascunho.operacoes
            assert len(ops) == 1 and isinstance(ops[0], RemoverPapeis)
            assert set(ops[0].papeis) == {"r13", "r16"} and ops[0].novo_dono == "r10"      # o padrão: a pessoa
            assert tela.marcados == set()
    asyncio.run(roteiro())


def test_tela_usuarios_c_herdado_explica_e_oferece_tirar_do_public_ou_do_grupo(monkeypatch, tmp_path):
    from ratt.db import formularios_usuarios as fu
    from ratt.db.rascunho_servidor import TirarAcesso, TirarDoGrupo
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FonteServidorFalsa(_modelo_com_banco_atual())

    async def roteiro():
        app = _app_usuarios(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            app._piloto = piloto
            await _usuarios_pronta(app)
            tela = app.screen
            # ana entra em estudos pelo grupo leitura: c explica e oferece tirá-la do grupo
            tela.ir_para("r13", "d1")
            await piloto.press("c")
            await esperar_que(piloto, lambda: isinstance(app.screen, fu.ExplicarAcesso))
            form = app.screen
            await esperar_tela(piloto, form)
            assert "leitura" in _texto(form.query_one("#explica_origens"))
            await piloto.click("#oferta_0")
            await esperar_que(piloto, lambda: app.screen is tela)
            ops = tela.sessao.rascunho.operacoes
            assert len(ops) == 1 and isinstance(ops[0], TirarDoGrupo) and (ops[0].papel, ops[0].grupo) == ("r13", "r12")
            # carla entra em sistema pelo PUBLIC: a oferta é tirar do PUBLIC
            tela.ir_para("r16", "d2")
            await piloto.press("c")
            await esperar_que(piloto, lambda: isinstance(app.screen, fu.ExplicarAcesso))
            form = app.screen
            await esperar_tela(piloto, form)
            assert "PUBLIC" in _texto(form.query_one("#explica_origens"))
            await piloto.click("#oferta_0")
            await esperar_que(piloto, lambda: app.screen is tela)
            assert isinstance(tela.sessao.rascunho.operacoes[-1], TirarAcesso)
            assert tela.sessao.rascunho.operacoes[-1].alvo == "PUBLIC"
            # C (direto) tira na hora; — dá na hora; D só explica
            tela.ir_para("r12", "d1")
            await piloto.press("c")
            assert isinstance(tela.sessao.rascunho.operacoes[-1], TirarAcesso)
            n = len(tela.sessao.rascunho.operacoes)
            tela.ir_para("r10", "d1")
            await piloto.press("c")
            assert len(tela.sessao.rascunho.operacoes) == n and app.screen is tela
            await piloto.press("u")                         # dentro da tela, u desfaz
            assert len(tela.sessao.rascunho.operacoes) == n - 1
    asyncio.run(roteiro())


def test_tela_usuarios_c_que_trancaria_a_pessoa_e_recusado_com_o_motivo(monkeypatch, tmp_path):
    """jared deixa de ser superusuário no modelo e entra em sistema só pelo PUBLIC (banco atual): tirar o PUBLIC
    pela explicação tem o botão desligado, com o motivo."""
    from textual.widgets import Button
    from ratt.db import formularios_usuarios as fu
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    m = modelo_exemplo()
    m.papeis["r10"].superusuario = False
    m.banco_atual = "d2"
    fonte = FonteServidorFalsa(m)

    async def roteiro():
        app = _app_usuarios(fonte)
        async with app.run_test(size=(140, 44)) as piloto:
            app._piloto = piloto
            await _usuarios_pronta(app)
            tela = app.screen
            tela.ir_para("r10", "d2")
            await piloto.press("c")
            await esperar_que(piloto, lambda: isinstance(app.screen, fu.ExplicarAcesso))
            form = app.screen
            await esperar_tela(piloto, form)
            assert form.query_one("#oferta_0", Button).disabled
            assert t("usu.trava.sem_entrar", banco="sistema") in _texto(form.query_one("#explica_origens"))
    asyncio.run(roteiro())


def test_tela_usuarios_80x24_com_15_bancos_rola_e_mostra_public_e_painel(monkeypatch, tmp_path):
    from textual.geometry import Region
    from ratt.db.grade_dados import Grade
    from ratt.db.painel import PainelSQL
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    m = modelo_exemplo()
    for k in range(4, 19):                              # 15 bancos a mais
        m.bancos[f"d{k}"] = Banco(f"d{k}", f"banco_{k:02d}", "r11", True, ("r11",), k % 2 == 0)
    for k in range(30, 42):                             # e mais papéis que cabem na altura
        m.papeis[f"r{k}"] = _papel(f"r{k}", f"pessoa_{k}")
    m.banco_atual = "d1"
    fonte = FonteServidorFalsa(m)

    def visivel(grade):
        return "\n".join("".join(s.text for s in linha)
                         for linha in grade.render_lines(Region(0, 0, grade.size.width, grade.size.height)))

    async def roteiro():
        app = _app_usuarios(fonte)
        async with app.run_test(size=(80, 24)) as piloto:
            app._piloto = piloto
            await _usuarios_pronta(app)
            tela = app.screen
            tela.ir_para("r10")
            await piloto.press("o")                         # sem coluna de banco: só avisa
            await piloto.pause()
            grade = tela.query_one(Grade)
            painel = tela.query_one(PainelSQL)
            assert grade.size.height >= 4 and painel.region.height >= 1
            assert painel.region.bottom <= 23               # acima do rodapé
            assert grade.max_scroll_x > 0 and grade.max_scroll_y > 0
            grade.move_cursor(row=grade.row_count - 1, column=len(grade.columns) - 1)
            await piloto.pause()
            await piloto.pause()
            assert grade.scroll_x > 0 and grade.scroll_y > 0
            texto = visivel(grade)
            assert "PUBLIC" in texto                        # a linha fixa no alto
            assert "velho" in texto                         # a última linha; o nome fica (coluna fixa) rolado à direita
            assert "banco_18" in texto
    asyncio.run(roteiro())


def test_tela_usuarios_vigia_rebase_quando_o_servidor_muda(monkeypatch, tmp_path):
    from ratt.db.rascunho_servidor import DarAcesso
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FonteServidorFalsa(_modelo_com_banco_atual())

    async def roteiro():
        app = _app_usuarios(fonte)
        async with app.run_test(size=(140, 40)) as piloto:
            app._piloto = piloto
            await _usuarios_pronta(app)
            tela = app.screen
            tela.ir_para("r16", "d1")
            await piloto.press("c")                         # — em estudos: dá o CONNECT a carla
            assert isinstance(tela.sessao.rascunho.operacoes[-1], DarAcesso)
            m2 = _modelo_com_banco_atual()
            del m2.papeis["r16"]                            # alguém removeu carla por fora
            fonte.m, fonte.sig = m2, 2
            tela.vigiar()
            await esperar_workers(app)
            await esperar_que(piloto, lambda: tela.sessao.rascunho.invalidas == {0})
            assert "carla" not in _grade_texto(tela)
    asyncio.run(roteiro())


def test_rascunho_servidor_sql_para_psql_com_connect_por_banco():
    base = modelo_exemplo()
    base.bancos["d5"] = Banco("d5", "a=b 'x'\\y", "r11", True, ("r11",), True)
    r = RascunhoServidor(base)
    n = r.novo_id("p")
    r.acrescentar(CriarPapel(Papel(n, "dora", True, False, False, False)))
    r.acrescentar(RemoverPapeis(("r15",), "r10", ("estudos", "sistema", "a=b 'x'\\y")))
    texto = r.sql_psql("-- cabeçalho", "estudos")
    linhas = texto.splitlines()
    conecta = [l for l in linhas if l.startswith("\\connect")]
    # cada grupo de um \\connect numa transação: BEGIN logo depois, COMMIT antes do próximo e no fim
    comandos = [l for l in linhas if l and not l.startswith("--")]
    assert [l for l in comandos if l.startswith("\\connect") or l in ("BEGIN;", "COMMIT;")] == \
        [x for c in conecta for x in (c, "BEGIN;", "COMMIT;")]
    assert comandos[-1] == "COMMIT;" and linhas[-1] == "COMMIT;"
    assert all(comandos[comandos.index(c) + 1] == "BEGIN;" for c in conecta)
    assert conecta == ["\\connect -reuse-previous=on 'estudos'", "\\connect -reuse-previous=on 'sistema'",
                       "\\connect -reuse-previous=on 'dbname=''a=b \\\\''x\\\\''\\\\\\\\y'''",
                       "\\connect -reuse-previous=on 'estudos'"]
    assert "PASSWORD" not in texto and "CREATE ROLE dora LOGIN;" in texto
    assert linhas.index(conecta[0]) < linhas.index("CREATE ROLE dora LOGIN;")
    assert linhas.index(conecta[3]) < linhas.index("DROP ROLE bruno;")


# ── Task 7: a tela Permissões (fonte falsa: o schema e as permissões fixos) ──

from ratt.db.catalogo import Permissoes as _Permissoes


def _schema_tela(n_tabelas=0, n_papeis=0):
    """public com pessoas, pedidos e itens (de jared, a pessoa), uma tabela estrangeira (no modelo, fora das
    permissões) e os papéis app, leitura e um do PostgreSQL (`pg_*`, que a grade não mostra)."""
    tabelas = {}
    for i, nome in ((10, "pessoas"), (11, "pedidos"), (12, "itens"), (13, "externa")):
        tabelas[f"t{i}"] = _Tabela(f"t{i}", "public", nome, [_Coluna(f"t{i}.c1", "id", "bigint", pk=True)])
    for k in range(n_tabelas):
        tabelas[f"t{100 + k}"] = _Tabela(f"t{100 + k}", "public", f"tab_{k:02d}", [_Coluna(f"t{100 + k}.c1", "id", "int")])
    papeis = {"r10": "jared", "r11": "app", "r12": "leitura", "r13": "pg_read_all_data"}
    for k in range(n_papeis):
        papeis[f"r{30 + k}"] = f"pessoa_{k:02d}"
    m = _Modelo("public", tabelas, {}, papeis=papeis, eu="r10")
    regulares = [i for i in tabelas if i != "t13"]
    tudo = frozenset("SIUDTRt")
    por_tabela = {("r10", i): tudo for i in regulares}
    por_tabela.update({("r11", i): frozenset("SIUD") for i in ("t10", "t11", "t12")})
    por_tabela[("r12", "t10")] = frozenset("S")
    perms = _Permissoes(por_tabela, {i: "r10" for i in regulares},
                        {"PUBLIC": frozenset({"USAGE"}), "r10": frozenset({"USAGE", "CREATE"}),
                         "r11": frozenset({"USAGE"})}, {})
    return m, perms


class FontePermFalsa:
    """O banco de mentira da tela Permissões (e do canvas e da tela Dados por baixo dela): o modelo e as
    permissões fixos por schema, a assinatura que o teste muda, e quem tem USAGE (para os avisos)."""

    def __init__(self, m=None, perms=None):
        if m is None:
            m, perms = _schema_tela()
        self.modelos = {"public": m}
        self.perms = {"public": perms}
        self.usage = {"jared": True, "app": True, "leitura": False, "public": True}
        self.porta, self.usuario, self.banco = 5999, "jared", "estudos"
        self.sig = 1
        self.outras = 0
        self.fechada = False

    def esquemas(self):
        return sorted(self.modelos)

    def modelo(self, esquema):
        return self.modelos[esquema].copia()

    def permissoes(self, esquema):
        return copy.deepcopy(self.perms[esquema])

    def assinatura(self):
        return self.sig

    def assinatura_permissoes(self, esquema):
        return (self.sig, esquema)

    def assinaturas(self, oid=None):
        return self.sig, 0

    def consulta(self):
        return _ConsultaPerm(self.usage, self.perms["public"])

    def colunas(self, oid):
        return []

    def pagina(self, esquema, tabela, onde, ordem, deslocamento, limite):
        return ["id"], ["int8"], []

    def contar(self, esquema, tabela, onde):
        return 0

    def classe(self, oid):
        return "r"

    def outra(self):
        self.outras += 1
        return self

    def fechar(self):
        self.fechada = True


import copy


def _app_perm(fonte, esquema="public"):
    from textual.app import App
    from ratt.db import tela_permissoes

    class AppPerm(App):
        def on_mount(self):
            tela = tela_permissoes.TelaPermissoes(fonte, esquema)
            tela.VIGIA_SEGUNDOS = 0
            self.push_screen(tela)
    return AppPerm()


async def _perm_pronta(app, piloto):
    from ratt.db import tela_permissoes
    await esperar_que(piloto, lambda: isinstance(app.screen, tela_permissoes.TelaPermissoes) and app.screen.carregado)
    await esperar_workers(app)


def _grade_perm(tela):
    """{nome da linha: [células]} da grade de permissões (o nome sem a marca da pessoa)."""
    from ratt.db.grade_dados import Grade
    grade = tela.query_one(Grade)
    linhas = {}
    for i in range(grade.row_count):
        celulas = [c.plain if hasattr(c, "plain") else str(c) for c in grade.get_row_at(i)]
        linhas[celulas[0].split(" ")[0]] = celulas
    return linhas


def _colunas_perm(tela):
    from ratt.db.grade_dados import Grade
    return [c.label.plain for c in tela.query_one(Grade).ordered_columns]


def _sem_nomes(ops):
    """As operações sem os `nomes` (que o rascunho preenche ao pôr), para comparar com as montadas no teste."""
    import dataclasses
    return [dataclasses.replace(op, nomes={}) for op in ops]


async def _contado(app, piloto, form):
    await esperar_workers(app)
    await esperar_que(piloto, lambda: not form.contando and form.avisos_chegados is not None, mensagem="avisos não chegaram")
    await esperar_workers(app)


def test_efetivas_sobrepoem_o_rascunho_as_permissoes_lidas():
    from ratt.db import tela_permissoes
    m, perms = _schema_tela()
    r = _rascunho.Rascunho(m)
    r.acrescentar(Conceder(("r12", "PUBLIC"), ("t10", "t11"), ("I", "S")))
    r.acrescentar(Revogar(("r11",), ("t10",), ("D", "U")))
    r.acrescentar(Conceder(("r12",), (), ("S",), futuras=True, por="r10"))      # futuras: não muda célula nenhuma
    r.acrescentar(PermissaoNoSchema("r12", True, None))
    r.acrescentar(PermissaoNoSchema("PUBLIC", False, None))
    ef = tela_permissoes.efetivas(perms, r)
    assert ef.por_tabela[("r12", "t10")] == frozenset("SI") and ef.por_tabela[("r12", "t11")] == frozenset("SI")
    assert ef.por_tabela[("PUBLIC", "t10")] == frozenset("SI")
    assert ef.por_tabela[("r11", "t10")] == frozenset("SI") and ef.por_tabela[("r11", "t11")] == frozenset("SIUD")
    assert ("r12", "t12") not in ef.por_tabela
    assert ef.schema["r12"] == frozenset({"USAGE"}) and ef.schema.get("PUBLIC", frozenset()) == frozenset()
    assert ef.padrao == {"r12": frozenset("S")}
    assert perms.por_tabela[("r12", "t10")] == frozenset("S")                  # o lido não muda
    # operação inválida (a tabela sumiu) não conta
    r2 = _rascunho.Rascunho(m, [Conceder(("r12",), ("t99",), ("T",))])
    assert tela_permissoes.efetivas(perms, r2).por_tabela == perms.por_tabela


def test_tela_permissoes_grade_public_dono_schema_e_o_pendente_marcado(monkeypatch, tmp_path):
    from ratt.db import tela_permissoes
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FontePermFalsa()

    async def roteiro():
        app = _app_perm(fonte)
        async with app.run_test(size=(120, 36)) as piloto:
            await _perm_pronta(app, piloto)
            tela = app.screen
            assert _colunas_perm(tela)[1:] == ["itens", "pedidos", "pessoas"]     # sem a estrangeira
            g = _grade_perm(tela)
            assert list(g) == ["PUBLIC", "app", "jared", "leitura"]              # sem os pg_*; PUBLIC primeiro
            assert tela.query_one("#perm_grade").fixed_rows == 1
            assert g["PUBLIC"][1:] == ["·······"] * 3
            assert g["app"][1:] == ["SIUD···"] * 3
            assert g["jared"][1:] == ["dono"] * 3
            assert g["leitura"][1:] == ["·······", "·······", "S······"]
            schema = _texto(tela.query_one("#perm_schema"))
            assert "app USAGE" in schema and "jared USAGE, CREATE" in schema and "PUBLIC USAGE" in schema
            assert "leitura" not in schema
            titulo = _texto(tela.query_one("#perm_titulo"))
            assert "estudos" in titulo and "public" in titulo and t("perm.titulo") in titulo
            # o rascunho por cima: a letra nova aparece marcada, a tirada também
            tela.sessao.acrescentar(Conceder(("r12",), ("t11",), ("I",)))
            tela.sessao.acrescentar(Revogar(("r11",), ("t10",), ("D",)))
            tela.sessao.acrescentar(PermissaoNoSchema("r12", True, None))
            await piloto.pause()
            g = _grade_perm(tela)
            assert g["leitura"][2] == "·I·····" and g["app"][3] == "SIU····"
            linha = [tela.linha_id(i) for i in range(4)].index("r12")
            celula = tela.query_one("#perm_grade").get_row_at(linha)[2]
            assert any("bold" in str(s.style) for s in celula.spans)              # a letra pendente se destaca
            assert "leitura USAGE" in _texto(tela.query_one("#perm_schema"))
            assert _texto(tela.query_one("#perm_titulo")).count("3")
    asyncio.run(roteiro())


def test_tela_permissoes_coluna_pessoas_concede_select_a_todos(monkeypatch, tmp_path):
    from textual.widgets import Button, SelectionList
    from ratt.db import formularios_usuarios as fu
    from ratt.db.painel import PainelSQL
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FontePermFalsa()

    async def roteiro():
        app = _app_perm(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await _perm_pronta(app, piloto)
            tela = app.screen
            tela.ir_para("r11", "t10")
            await piloto.press("k")                                         # a coluna pessoas inteira
            assert tela.alvo() == (("PUBLIC", "r11", "r10", "r12"), ("t10",))
            await piloto.press("space")
            await esperar_que(piloto, lambda: isinstance(app.screen, fu.FormPrivilegios))
            form = app.screen
            await esperar_tela(piloto, form)
            form.query_one("#conceder", SelectionList).select("S")
            await _contado(app, piloto, form)
            sql = _texto(form.query_one("#form_sql"))
            assert "GRANT SELECT ON public.pessoas TO PUBLIC, app, jared, leitura;" in sql
            avisos = _texto(form.query_one("#form_avisos"))
            assert "leitura" in avisos and "USAGE" in avisos                  # o aviso da Task 5, ao vivo
            assert not form.query_one("#por_no_rascunho", Button).disabled
            await piloto.click("#por_no_rascunho")
            await esperar_que(piloto, lambda: app.screen is tela)
            ops = _sem_nomes(tela.sessao.rascunho.operacoes)
            assert ops == [Conceder(("PUBLIC", "r11", "r10", "r12"), ("t10",), ("S",))]
            assert "GRANT SELECT ON public.pessoas TO PUBLIC, app, jared, leitura;" in \
                tela.query_one(PainelSQL).texto_plano()
            assert tela.selecao is None                                     # a seleção se desfaz depois de pôr
            assert _grade_perm(tela)["PUBLIC"][3] == "S······"
            # l: a linha inteira do papel; shift+clique numa célula também
            tela.ir_para("r12", "t11")
            await piloto.press("l")
            assert tela.alvo() == (("r12",), ("t12", "t11", "t10"))
            await piloto.press("escape")                                    # escape desfaz a seleção (não sai)
            assert tela.selecao is None and app.screen is tela
            assert tela.alvo() == (("r12",), ("t11",))                      # sem seleção: a célula do cursor
            tela.escolher_celula(1, 2, shift=True)                         # o que o shift+clique faz
            assert tela.alvo() == (("r11",), ("t12", "t11", "t10"))
    asyncio.run(roteiro())


def test_tela_permissoes_todas_e_a_lista_explicita_nunca_all_tables(monkeypatch, tmp_path):
    from textual.widgets import SelectionList
    from ratt.db import formularios_usuarios as fu
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FontePermFalsa()

    async def roteiro():
        app = _app_perm(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await _perm_pronta(app, piloto)
            tela = app.screen
            await piloto.press("asterisk")
            assert tela.alvo() == (("PUBLIC", "r11", "r10", "r12"), ("t12", "t11", "t10"))
            await piloto.press("space")
            await esperar_que(piloto, lambda: isinstance(app.screen, fu.FormPrivilegios))
            form = app.screen
            await esperar_tela(piloto, form)
            form.query_one("#revogar", SelectionList).select("T")
            form.query_one("#futuras").value = True
            await _contado(app, piloto, form)
            sql = _texto(form.query_one("#form_sql"))
            assert "REVOKE TRUNCATE ON public.itens, public.pedidos, public.pessoas FROM PUBLIC, app, jared, leitura;" in sql
            assert "ALTER DEFAULT PRIVILEGES FOR ROLE jared IN SCHEMA public REVOKE TRUNCATE ON TABLES" in sql
            assert "ALL TABLES" not in sql and "externa" not in sql
            await piloto.click("#por_no_rascunho")
            await esperar_que(piloto, lambda: app.screen is tela)
            op = tela.sessao.rascunho.operacoes[0]
            assert isinstance(op, Revogar) and op.tabelas == ("t12", "t11", "t10") and op.por == "r10" and op.futuras
    asyncio.run(roteiro())


def test_form_privilegios_oferece_usage_junto_e_recusa_com_o_motivo(monkeypatch, tmp_path):
    from textual.widgets import Button, Checkbox, SelectionList
    from ratt.db import formularios_usuarios as fu
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FontePermFalsa()

    async def roteiro():
        app = _app_perm(fonte)
        async with app.run_test(size=(120, 44)) as piloto:
            await _perm_pronta(app, piloto)
            tela = app.screen
            tela.ir_para("r12", "t11")
            await piloto.press("space")
            await esperar_que(piloto, lambda: isinstance(app.screen, fu.FormPrivilegios))
            form = app.screen
            await esperar_tela(piloto, form)
            usage = form.query_one("#usage", Checkbox)
            assert not usage.display                                        # só aparece com a oferta
            form.query_one("#conceder", SelectionList).select("S")
            await _contado(app, piloto, form)
            assert usage.display and "leitura" in str(usage.label)          # falta USAGE: oferece junto
            usage.value = True
            await _contado(app, piloto, form)
            sql = _texto(form.query_one("#form_sql"))
            assert sql.index("GRANT USAGE ON SCHEMA public TO leitura;") < sql.index("GRANT SELECT ON public.pedidos TO leitura;")
            assert usage.display                                            # já resolvido, a oferta continua à vista
            # conceder e revogar a mesma letra: botão desligado, com o motivo
            form.query_one("#revogar", SelectionList).select("S")
            await piloto.pause()
            assert form.query_one("#por_no_rascunho", Button).disabled
            assert t("perm.e.conceder_e_revogar", privilegios="SELECT") in _texto(form.query_one("#erro"))
            form.query_one("#revogar", SelectionList).deselect("S")
            await _contado(app, piloto, form)
            await piloto.click("#por_no_rascunho")
            await esperar_que(piloto, lambda: app.screen is tela)
            assert _sem_nomes(tela.sessao.rascunho.operacoes) == [PermissaoNoSchema("r12", True, None),
                                                                  Conceder(("r12",), ("t11",), ("S",))]
            # papel pendente (do rascunho do servidor): o rascunho de verdade recusa, numa cópia, e o botão desliga
            pendente = fu.FormPrivilegios(tela.sessao, tela.fonte_avisos, ("novo-p1",), ("t10",))
            app.push_screen(pendente)
            await esperar_tela(piloto, pendente)
            pendente.query_one("#conceder", SelectionList).select("S")
            await piloto.pause()
            assert pendente.query_one("#por_no_rascunho", Button).disabled
            assert t("perm.e.papel_pendente") in _texto(pendente.query_one("#erro"))
            assert len(tela.sessao.rascunho.operacoes) == 2
    asyncio.run(roteiro())


def test_tela_permissoes_s_muda_usage_e_create_do_papel(monkeypatch, tmp_path):
    from textual.widgets import Checkbox
    from ratt.db import formularios_usuarios as fu
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FontePermFalsa()

    async def roteiro():
        app = _app_perm(fonte)
        async with app.run_test(size=(120, 40)) as piloto:
            await _perm_pronta(app, piloto)
            tela = app.screen
            tela.ir_para("r11", "t10")
            await piloto.press("s")
            await esperar_que(piloto, lambda: isinstance(app.screen, fu.FormSchema))
            form = app.screen
            await esperar_tela(piloto, form)
            assert form.query_one("#usage", Checkbox).value and not form.query_one("#create", Checkbox).value
            form.query_one("#usage", Checkbox).value = False
            form.query_one("#create", Checkbox).value = True
            await _contado(app, piloto, form)
            sql = _texto(form.query_one("#form_sql"))
            assert "GRANT CREATE ON SCHEMA public TO app;" in sql and "REVOKE USAGE ON SCHEMA public FROM app;" in sql
            await piloto.click("#por_no_rascunho")
            await esperar_que(piloto, lambda: app.screen is tela)
            assert _sem_nomes(tela.sessao.rascunho.operacoes) == [PermissaoNoSchema("r11", False, True)]
            assert "app CREATE" in _texto(tela.query_one("#perm_schema"))
            # na linha do PUBLIC também (é editável)
            tela.ir_para("PUBLIC", "t10")
            await piloto.press("s")
            await esperar_que(piloto, lambda: isinstance(app.screen, fu.FormSchema))
            form = app.screen
            await esperar_tela(piloto, form)
            assert form.query_one("#usage", Checkbox).value
    asyncio.run(roteiro())


def test_tela_permissoes_80x24_com_40_tabelas_rola_e_mostra_public_e_painel(monkeypatch, tmp_path):
    from textual.geometry import Region
    from ratt.db.grade_dados import Grade
    from ratt.db.painel import PainelSQL
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FontePermFalsa(*_schema_tela(n_tabelas=40, n_papeis=15))

    def visivel(grade):
        return "\n".join("".join(s.text for s in linha)
                         for linha in grade.render_lines(Region(0, 0, grade.size.width, grade.size.height)))

    async def roteiro():
        app = _app_perm(fonte)
        async with app.run_test(size=(80, 24)) as piloto:
            await _perm_pronta(app, piloto)
            tela = app.screen
            tela.sessao.acrescentar(Conceder(("r12",), ("t100",), ("S",)))
            await piloto.pause()
            grade = tela.query_one(Grade)
            painel = tela.query_one(PainelSQL)
            assert len(grade.columns) == 44 and grade.row_count == 19
            assert grade.max_scroll_x > 0 and grade.max_scroll_y > 0
            grade.move_cursor(row=grade.row_count - 1, column=len(grade.columns) - 1)
            await piloto.pause()
            await piloto.pause()
            assert grade.scroll_x > 0 and grade.scroll_y > 0
            texto = visivel(grade)
            assert "PUBLIC" in texto                         # a linha fixa no alto
            assert "pessoa_14" in texto                      # a última linha; o nome fica (coluna fixa)
            assert "tab_39" in texto
            assert painel.region.bottom <= 23                # acima do rodapé
            tela_linhas = ["".join(s.text for s in st) for st in app.screen._compositor.render_strips()]
            do_painel = tela_linhas[painel.region.y:painel.region.bottom]
            assert any("GRANT SELECT ON public.tab_00 TO leitura;" in linha for linha in do_painel)
            assert grade.region.height >= 4
    asyncio.run(roteiro())


def test_tela_permissoes_vigia_rele_quando_as_permissoes_mudam(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FontePermFalsa()

    async def roteiro():
        app = _app_perm(fonte)
        async with app.run_test(size=(120, 36)) as piloto:
            await _perm_pronta(app, piloto)
            tela = app.screen
            tela.sessao.acrescentar(Conceder(("r12",), ("t11",), ("S",), futuras=True, por="r10"))
            # alguém deu INSERT a leitura em pedidos pelo psql: só as ACLs mudaram
            fonte.perms["public"].por_tabela[("r12", "t11")] = frozenset("I")
            fonte.sig = 2
            tela.vigiar()
            await esperar_workers(app)
            await esperar_que(piloto, lambda: _grade_perm(tela)["leitura"][2] == "SI·····")
            # o papel que cria as tabelas (o `por` das futuras) mudou de nome: a operação cai no rebase
            fonte.modelos["public"].papeis["r10"] = "jared2"
            fonte.sig = 3
            tela.vigiar()
            await esperar_workers(app)
            await esperar_que(piloto, lambda: tela.sessao.rascunho.invalidas == {0})
    asyncio.run(roteiro())


def test_P_no_canvas_e_na_tela_dados_abre_permissoes_no_schema_deles(monkeypatch, tmp_path):
    from textual.app import App
    from ratt.db import canvas, tela_dados, tela_permissoes
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FontePermFalsa()
    m2, p2 = _schema_tela()
    m2.esquema = "vendas"
    for tab in m2.tabelas.values():
        tab.esquema = "vendas"
    fonte.modelos["vendas"], fonte.perms["vendas"] = m2, p2

    class AppCanvas(App):
        def on_mount(self):
            self.push_screen(canvas.TelaCanvas(fonte, "vendas"))

    async def roteiro():
        app = AppCanvas()
        async with app.run_test(size=(120, 40)) as piloto:
            await esperar_que(piloto, lambda: app.screen.carregado)
            await esperar_workers(app)
            cv = app.screen
            assert any(b.binding.action == "permissoes" and b.binding.show for b in cv.active_bindings.values())
            await piloto.press("P")
            await esperar_que(piloto, lambda: isinstance(app.screen, tela_permissoes.TelaPermissoes))
            perm = app.screen
            assert perm.esquema == "vendas" and fonte.outras == 1
            await esperar_que(piloto, lambda: perm.carregado)
            await esperar_workers(app)
            assert perm.sessao is cv.sessao                                  # o mesmo rascunho do schema
            assert "vendas" in _texto(perm.query_one("#perm_titulo"))
            await piloto.press("escape")
            await esperar_que(piloto, lambda: app.screen is cv)
            # da tela Dados de uma tabela de vendas
            cv.selecao = ("tabela", "t10")
            await piloto.press("v")
            await esperar_que(piloto, lambda: isinstance(app.screen, tela_dados.TelaDados))
            dados = app.screen
            dados.VIGIA_SEGUNDOS = 0
            await esperar_que(piloto, lambda: dados.carregado)
            await esperar_workers(app)
            await piloto.press("P")
            await esperar_que(piloto, lambda: isinstance(app.screen, tela_permissoes.TelaPermissoes))
            assert app.screen.esquema == "vendas"
            await esperar_que(piloto, lambda: app.screen.carregado)
            await esperar_workers(app)
            assert app.screen.sessao is dados.sessao
    asyncio.run(roteiro())


def test_P_na_tela_usuarios_escolhe_o_schema_do_banco_atual(monkeypatch, tmp_path):
    from textual.widgets import OptionList
    from ratt.db import canvas, tela_permissoes
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    banco = FontePermFalsa()
    m2, p2 = _schema_tela()
    m2.esquema = "vendas"
    banco.modelos["vendas"], banco.perms["vendas"] = m2, p2
    srv = FonteServidorFalsa(_modelo_com_banco_atual())
    pedidos = []
    srv.fonte_banco = lambda: pedidos.append(srv.banco) or banco

    async def roteiro():
        app = _app_usuarios(srv)
        async with app.run_test(size=(140, 40)) as piloto:
            app._piloto = piloto
            await _usuarios_pronta(app)
            usuarios = app.screen
            await piloto.press("P")
            await esperar_que(piloto, lambda: isinstance(app.screen, canvas.EscolherEsquema))
            escolha = app.screen
            await esperar_tela(piloto, escolha)
            assert escolha.esquemas == ["public", "vendas"] and pedidos == ["estudos"]
            escolha.query_one(OptionList).highlighted = 1
            await piloto.press("enter")
            await esperar_que(piloto, lambda: isinstance(app.screen, tela_permissoes.TelaPermissoes))
            perm = app.screen
            perm.VIGIA_SEGUNDOS = 0
            assert perm.esquema == "vendas" and perm.fonte is banco
            await esperar_que(piloto, lambda: perm.carregado)
            await esperar_workers(app)
            await piloto.press("escape")
            await esperar_que(piloto, lambda: app.screen is usuarios)
            assert banco.fechada                                            # a tela fechou a conexão dela
            # escolher e desistir: a conexão aberta para listar os schemas fecha
            banco.fechada = False
            await piloto.press("P")
            await esperar_que(piloto, lambda: isinstance(app.screen, canvas.EscolherEsquema))
            await esperar_tela(piloto, app.screen)
            await piloto.press("escape")
            await esperar_que(piloto, lambda: app.screen is usuarios)
            assert banco.fechada
    asyncio.run(roteiro())


# ── revisão final: os formulários num terminal de 80×24 ──

def _inteiro_na_tela(w) -> bool:
    """A região do widget tem altura, está inteira na tela e nenhum contêiner de cima a corta."""
    from textual.errors import NoWidget
    try:
        geo = w.screen.find_widget(w)
    except NoWidget:
        return False
    return (w.region.height >= 1 and w.screen.region.contains_region(w.region)
            and geo.clip.contains_region(w.region))


async def _botoes_e_erro_a_vista(app, piloto, tela, form, com_motivo=True):
    from textual.widgets import Button
    app.push_screen(form)
    await esperar_tela(piloto, form)
    await esperar_workers(app)
    erro = form.query_one("#erro")
    alvos = [form.query_one("#por_no_rascunho", Button), form.query_one("#cancelar", Button), erro]
    await esperar_que(piloto, lambda: (_texto(erro).strip() or not com_motivo)
                      and all(_inteiro_na_tela(w) for w in alvos),
                      mensagem=f"{type(form).__name__}: botões ou motivo fora da tela: "
                               f"{[(w.id, w.region) for w in alvos]}")
    if form.foco_no_cancelar:
        assert app.focused is alvos[1]
    form.dismiss(None)
    await esperar_que(piloto, lambda: app.screen is tela)


def test_formularios_usuarios_em_80x24_botoes_e_motivo_a_vista(monkeypatch, tmp_path):
    from ratt.db import formularios_usuarios as fu
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    m = _modelo_com_banco_atual()
    m.papeis["r40"] = _papel("r40", "nome_" + "comprido_" * 16)    # título e motivo de várias linhas
    fonte = FonteServidorFalsa(m)

    async def roteiro():
        app = _app_usuarios(fonte)
        async with app.run_test(size=(80, 24)) as piloto:
            app._piloto = piloto
            await _usuarios_pronta(app)
            tela = app.screen
            s = tela.sessao
            for form in (fu.FormNovoUsuario(s, fonte),                      # nome vazio
                         fu.FormPoderes(s, fonte, "r13"),                   # nada muda
                         fu.FormPoderes(s, fonte, "r40"),
                         fu.FormGrupos(s, fonte, "r13"),                    # nada mudou nos grupos
                         fu.EscolherPapel(s, fonte, "d1"),                  # já é o dono
                         fu.ConfirmarRemocao(s, fonte, ("r10",))):          # a trava: remover a si
                await _botoes_e_erro_a_vista(app, piloto, tela, form)
            # sem motivo, com os avisos da remoção (a área maior): os botões continuam à vista, foco no cancelar
            form = fu.ConfirmarRemocao(s, fonte, ("r40", "r13"))
            await _botoes_e_erro_a_vista(app, piloto, tela, form, com_motivo=False)
    asyncio.run(roteiro())


def test_formularios_permissoes_em_80x24_botoes_e_motivo_a_vista(monkeypatch, tmp_path):
    from ratt.db import formularios_usuarios as fu
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fonte = FontePermFalsa(*_schema_tela(n_tabelas=40, n_papeis=15))

    async def roteiro():
        app = _app_perm(fonte)
        async with app.run_test(size=(80, 24)) as piloto:
            await _perm_pronta(app, piloto)
            tela = app.screen
            s, f = tela.sessao, tela.fonte_avisos
            muitas = tuple(f"t{100 + k}" for k in range(40))
            for form in (fu.FormPrivilegios(s, f, ("r12",), ("t10", "t11", "t12")),    # marque o que conceder
                         fu.FormPrivilegios(s, f, ("r12", "r11", "r30", "r31", "r32", "r33"), muitas),
                         fu.FormSchema(s, f, "r11", {"USAGE"})):                        # nada muda no schema
                await _botoes_e_erro_a_vista(app, piloto, tela, form)
    asyncio.run(roteiro())
