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
                           concedido_por={"r10": ("r10",), "r20": ("r10",), "r21": ("r20",), "PUBLIC": ("r20",)})
    m.eu, m.banco_atual = "r21", "d5"
    texto = _recusa(m, RemoverPapeis(("r20",), "r10", ("x2",)))
    assert "x2" in texto
    m.eu, m.banco_atual = "r10", None
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r20",), "r10", ("x2",)))
    b = r.vista().bancos["d5"]
    assert b.acesso_direto == ("r10",) and b.publico is False and b.concedido_por == {"r10": ("r10",)}
    assert servidor.acesso(r.modelo(), "r21", "d5") == "nenhum" and servidor.acesso(r.modelo(), "r16", "d5") == "nenhum"


def test_cascata_transitiva_e_quem_tem_outro_concedente_fica():
    m = modelo_exemplo()
    m.bancos["d5"] = Banco("d5", "x2", "r10", True, ("r10", "r13", "r15", "r16", "r11"), False,
                           concedido_por={"r13": ("r16",), "r15": ("r13",), "r11": ("r16", "r10")})
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r16",), "r10"))                   # carla deu a ana, que deu a bruno
    b = r.vista().bancos["d5"]
    assert b.acesso_direto == ("r10", "r11") and b.concedido_por == {"r11": ("r10",)}   # app tinha também do dono


def test_dono_removido_passa_o_que_deu_ao_novo_dono():
    m = modelo_exemplo()
    m.bancos["d5"] = Banco("d5", "x2", "r16", True, ("r16", "r13"), False,
                           concedido_por={"r16": ("r16",), "r13": ("r16",)})
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r16",), "r11"))
    b = r.vista().bancos["d5"]
    assert b.dono == "r11" and b.acesso_direto == ("r11", "r13") and b.concedido_por == {"r11": ("r11",),
                                                                                         "r13": ("r11",)}


def test_remover_quem_deu_a_pertenca_tira_a_pertenca():
    """16+: a pertença dada por um papel removido cai (e, em cascata, a que dependia dela)."""
    m = modelo_exemplo()
    m.papeis["r16"].membro_de, m.papeis["r16"].concedido_por = ("r12",), {"r12": ("r11",)}   # app deu a carla
    m.papeis["r11"].membro_de, m.papeis["r11"].concedido_por = ("r12",), {"r12": ("r10",)}
    m.papeis["r15"].membro_de = ("r14", "r12")
    m.papeis["r15"].concedido_por = {"r12": ("r16",), "r14": ("r10",)}                       # carla deu a bruno
    m.eu, m.banco_atual = "r15", "d1"
    m.papeis["r14"].membro_de = ()                                  # bruno entra em estudos só pela pertença direta
    texto = _recusa(m, RemoverPapeis(("r11",), "r10"))
    assert "estudos" in texto
    m.eu, m.banco_atual = "r10", None
    r = RascunhoServidor(m)
    r.acrescentar(RemoverPapeis(("r11",), "r10"))
    v = r.vista()
    assert v.papeis["r16"].membro_de == () and v.papeis["r15"].membro_de == ("r14",)
    assert v.papeis["r15"].concedido_por == {"r14": ("r10",)}
    assert v.papeis["r13"].membro_de == ("r12",)                    # sem concedido_por: não cai


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
