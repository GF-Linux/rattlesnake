"""Avisos que ensinam — o canvas "avisa, não decide".

Dois tipos: os estáticos, lidos só do modelo (tabela sem PK, FK sem índice), e os de uma operação,
que contam no banco (NULLs, repetidos, órfãos, dependentes) antes de a operação entrar no rascunho.
Tudo aqui é leitura: cada consulta roda numa transação `READ ONLY` com `statement_timeout` curto, pelo protocolo
estendido (um comando só: um `; COMMIT; …` no meio de um WHERE é recusado). O READ ONLY barra escritas em
tabelas e sequências; não barra todo efeito colateral que uma função chamada num WHERE escrito à mão possa ter
(advisory locks, `pg_terminate_backend`, escrita num servidor estrangeiro) — esse WHERE é SQL da pessoa. Um aviso
"impede" não bloqueia nada; diz o que o PostgreSQL recusaria e por quê, e a pessoa decide.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

import psycopg
from psycopg import sql

from . import arquivos, catalogo, filtro, rodar
from . import rascunho as rs
from .idioma import t
from .modelo import Modelo, opcional


@dataclass
class Aviso:
    alvo: str                   # id da tabela, coluna ou ligação a que o aviso se refere
    nivel: str                  # "info", "atencao" ou "impede"
    chave: str                  # chave de texto em idioma.py (`chk.*`)
    valores: Dict[str, object] = field(default_factory=dict)
    oferta: Optional[object] = None     # uma operação que o aviso oferece pôr junto no rascunho (a tela pergunta)

    def texto(self) -> str:
        return t(self.chave, **self.valores)


# ── avisos estáticos, do modelo ──

def avisos_do_modelo(m: Modelo) -> List[Aviso]:
    avisos = []
    for tab in m.tabelas.values():
        if tab.estado != "apagado" and not tab.pk():
            avisos.append(Aviso(tab.id, "atencao", "chk.sem_pk", {"tabela": tab.nome}))
    for lig in m.ligacoes.values():
        if lig.estado == "apagado" or lig.origem not in m.tabelas or lig.destino not in m.tabelas:
            continue
        origem = m.tabelas[lig.origem]
        primeira = origem.coluna(lig.colunas[0]) if lig.colunas else None
        if primeira is None:
            continue
        valores = {"tabela": origem.nome, "coluna": primeira.nome, "ligacao": lig.nome}
        if not primeira.indexada:
            avisos.append(Aviso(lig.id, "atencao", "chk.fk_sem_indice", valores))
        if opcional(m, lig):
            avisos.append(Aviso(lig.id, "info", "chk.fk_opcional", valores))
    return avisos


@dataclass
class Dependente:
    """Algo no banco que depende de uma tabela ou coluna. O texto sai de `idioma`, não do SQL."""
    tipo: str                               # "fk" (chave estrangeira de outra tabela) ou "view"
    oid: int                                # da tabela ou view dependente
    nome: str
    oid_restricao: Optional[int] = None     # só para "fk"
    nome_restricao: Optional[str] = None

    def texto(self) -> str:
        if self.tipo == "fk":
            return t("chk.dep_fk", nome=self.nome, restricao=self.nome_restricao)
        return t("chk.dep_view", nome=self.nome)


# ── contagens no banco, só leitura ──

NULO_EM_OBRIGATORIA = "SET NULL → NOT NULL"
_ACOES = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}
_PROFUNDIDADE = 16          # tabelas seguidas por CASCADE (só uma rede de segurança: o normal é acabar antes)
_ORCAMENTO_S = 4.0          # tempo total de uma cascata; passou, ela fica incompleta
_TAMANHO_MAX = 200_000      # caracteres do predicado de uma tabela


class Cascata(list):
    """[(tabela, ação, linhas)]. `incompleta`: algo ficou sem olhar (teto, tempo, ciclo entre tabelas, permissão)
    e os números são o mínimo. `sem_permissao`: as tabelas que não deu para ler."""

    def __init__(self, itens=(), incompleta: bool = False, sem_permissao=()):
        super().__init__(itens)
        self.incompleta = incompleta
        self.sem_permissao: List[str] = list(sem_permissao)


@dataclass(frozen=True)
class _Aresta:
    filha: int                  # oid da tabela que tem a chave estrangeira
    esquema: str
    nome: str
    minhas: Tuple[str, ...]     # colunas da filha
    delas: Tuple[str, ...]      # colunas do pai
    acao: str
    nulo_em_obrigatoria: bool   # o SET NULL cairia numa coluna NOT NULL


def _qualificado(esquema: str, nome: str) -> str:
    return f"{rs.ident(esquema)}.{rs.ident(nome)}"


def _pega(filha: str, minhas, pai: str, delas, sai_do_pai: str) -> str:
    """Linhas da filha que apontam para as linhas do pai que saem. Pai e filha vão pelo nome completo, sem apelido:
    numa subconsulta o nome mais de dentro vale, então a mesma tabela dos dois lados também funciona."""
    return (f"({', '.join(f'{filha}.{rs.ident(c)}' for c in minhas)}) IN "
            f"(SELECT {', '.join(f'{pai}.{rs.ident(c)}' for c in delas)} FROM {pai} WHERE {sai_do_pai})")


def _recursivo(tab: str, base: str, proprias: List[_Aresta], n: int) -> str:
    """As linhas que saem de uma tabela com chave estrangeira para ela mesma: as da `base` e, de novo e de novo,
    as que apontam para elas. UNION (não UNION ALL) sobre (tableoid, ctid, chaves) faz um ciclo nos dados terminar."""
    chaves: List[str] = []
    for a in proprias:
        for c in a.delas:
            if c not in chaves:
                chaves.append(c)
    r = f"_ratt_r{n}"
    k = {c: f"_k{i}" for i, c in enumerate(chaves)}
    junta = " OR ".join("(" + ", ".join(f"_x.{rs.ident(m)}" for m in a.minhas) + ") = ("
                        + ", ".join(f"{r}.{k[d]}" for d in a.delas) + ")" for a in proprias)
    lista = ", ".join(rs.ident(c) for c in chaves)
    return (f"({tab}.tableoid, {tab}.ctid) IN (WITH RECURSIVE {r}(_t, _c, {', '.join(k[c] for c in chaves)}) AS ("
            f"SELECT tableoid, ctid, {lista} FROM {tab} WHERE {base} "
            f"UNION SELECT _x.tableoid, _x.ctid, {', '.join(f'_x.{rs.ident(c)}' for c in chaves)} "
            f"FROM {tab} AS _x JOIN {r} ON {junta}) SELECT _t, _c FROM {r})")

# Nome de tipo como o `format_type` do catálogo escreve: nomes simples ou entre aspas (com `""` dobrado), um
# schema opcional, `(n)`/`(n,m)`, as palavras fixas dos tipos de hora e de intervalo e `[]`. Só isso pode ir
# colado num CAST: qualquer outra coisa (`date); DROP …`) é recusada antes de chegar ao banco.
_NOME = r'(?:[a-z_][a-z0-9_$]*|"(?:[^"]|"")+")'
_PALAVRAS_DE_TIPO = r"(?: (?:with|without|time|zone|varying|precision|year|month|day|hour|minute|second|to))*"
_TIPO_SEGURO = re.compile(rf"(?:{_NOME}\.)?{_NOME}{_PALAVRAS_DE_TIPO}(?:\(\d+(?:,\s*\d+)?\))?"
                          rf"{_PALAVRAS_DE_TIPO}(?:\(\d+\))?(?:\[\])*")


def tipo_seguro(tipo: str) -> bool:
    return isinstance(tipo, str) and bool(_TIPO_SEGURO.fullmatch(tipo))


class Consulta:
    """Contagens pontuais. Cada uma roda numa transação somente leitura com `statement_timeout` e devolve None
    se o PostgreSQL cancelar por demora (tabela grande demais para contar de graça). As que recebem um WHERE
    (`onde`: a saída de `filtro.where`, ou o texto que a pessoa escreveu) também devolvem None se ele falhar
    no banco; o motivo fica em `erro()`."""

    def __init__(self, conexao, limite_ms: int = 2000):
        self.conexao = conexao
        self.limite_ms = int(limite_ms)
        self.forcar_cast = False            # valida valores pelo CAST mesmo no PostgreSQL 16+ (o caminho antigo)
        self._erro: Optional[str] = None

    def erro(self) -> Optional[str]:
        """Por que a última consulta devolveu None, se não foi o tempo (None quando foi o tempo, ou deu certo)."""
        return self._erro

    def _falhou(self, e: Exception) -> None:
        linhas = str(e).strip().splitlines()
        self._erro = linhas[0] if linhas else type(e).__name__

    def _rodar(self, consulta, params=None, engolir: bool = False):
        """As linhas da consulta, ou None no tempo esgotado. `engolir`: um erro do banco também vira None (com o
        motivo em `erro()`), para o WHERE que veio de fora. Sem `params`, a consulta não é lida atrás de `%s`."""
        self._erro = None
        try:
            with catalogo.somente_leitura(self.conexao, self.limite_ms):
                return catalogo.executar(self.conexao, consulta, params).fetchall()
        except psycopg.errors.QueryCanceled:
            return None
        except psycopg.Error as e:
            if not engolir:
                raise
            self._falhou(e)
            return None

    def _contar(self, consulta, params=None) -> Optional[int]:
        linhas = self._rodar(consulta, params)
        return None if linhas is None else int(linhas[0][0])

    def linhas(self, esquema, tabela) -> Optional[int]:
        return self._contar(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(esquema, tabela)))

    def nulos(self, esquema, tabela, coluna) -> Optional[int]:
        return self._contar(sql.SQL("SELECT count(*) FROM {} WHERE {} IS NULL").format(
            sql.Identifier(esquema, tabela), sql.Identifier(coluna)))

    def repetidos(self, esquema, tabela, coluna) -> Optional[int]:
        """Quantos valores não nulos aparecem mais de uma vez."""
        c = sql.Identifier(coluna)
        return self._contar(sql.SQL(
            "SELECT count(*) FROM (SELECT 1 FROM {} WHERE {c} IS NOT NULL GROUP BY {c} HAVING count(*) > 1) r"
        ).format(sql.Identifier(esquema, tabela), c=c))

    def orfaos(self, esquema, tabela, coluna, esquema_pai, pai, coluna_pai) -> Optional[int]:
        """Linhas com valor que não existe na coluna do pai (NULL não é órfão)."""
        return self._contar(sql.SQL(
            "SELECT count(*) FROM {f} x WHERE x.{c} IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM {p} y WHERE y.{pc} = x.{c})"
        ).format(f=sql.Identifier(esquema, tabela), c=sql.Identifier(coluna),
                 p=sql.Identifier(esquema_pai, pai), pc=sql.Identifier(coluna_pai)))

    def dependentes(self, oid_tabela: int, attnum: Optional[int] = None) -> Optional[List[Dependente]]:
        """O que quebra ou some junto: chaves estrangeiras de outras tabelas que chegam na tabela (ou
        na coluna) e views que a leem. None se o PostgreSQL cancelou por demora."""
        so_coluna_fk = "" if attnum is None else "AND %(a)s = ANY (k.confkey) "
        so_coluna_dep = "" if attnum is None else "AND d.refobjsubid = %(a)s "
        consulta = (
            "SELECT 'fk', c.oid::bigint, c.relname::text, k.oid::bigint, k.conname::text "
            "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
            "WHERE k.contype = 'f' AND k.confrelid = %(o)s AND k.conrelid <> %(o)s " + so_coluna_fk +
            "UNION "
            "SELECT 'view', c.oid::bigint, c.relname::text, NULL::bigint, NULL::text FROM pg_depend d "
            "JOIN pg_rewrite w ON w.oid = d.objid JOIN pg_class c ON c.oid = w.ev_class "
            "WHERE d.classid = 'pg_rewrite'::regclass AND d.refclassid = 'pg_class'::regclass "
            "AND d.refobjid = %(o)s AND c.oid <> %(o)s " + so_coluna_dep +
            "ORDER BY 3, 5")
        linhas = self._rodar(consulta, {"o": oid_tabela, "a": attnum})
        return None if linhas is None else [Dependente(*l) for l in linhas]

    # ── as da tela Dados ──

    def contar_onde(self, esquema, tabela, onde: str) -> Optional[int]:
        """Quantas linhas o WHERE pega ("" = todas)."""
        linhas = self._rodar(sql.SQL("SELECT count(*) FROM {}{}").format(
            sql.Identifier(esquema, tabela), catalogo.onde_sql(onde)), engolir=True)
        return None if linhas is None else int(linhas[0][0])

    def amostra(self, esquema, tabela, onde: str, colunas: List[str], limite: int = 5) -> Optional[List[tuple]]:
        return self._rodar(sql.SQL("SELECT {} FROM {}{} LIMIT {}").format(
            sql.SQL(", ").join(map(sql.Identifier, colunas)), sql.Identifier(esquema, tabela),
            catalogo.onde_sql(onde), sql.Literal(int(limite))), engolir=True)

    def selecionar(self, consulta: str) -> Optional[List[tuple]]:
        """As linhas de um SELECT montado pelo ratt (a amostra das duplicatas: `ApagarDuplicatas.amostra_sql`)."""
        return self._rodar(consulta, engolir=True)

    def antes_depois(self, esquema, tabela, onde: str, sets: Dict[str, str],
                     limite: int = 5) -> Optional[List[Tuple[tuple, tuple]]]:
        """Até `limite` linhas como estão e como ficariam. `sets`: coluna (já por `ident`, como `Alterar.sets`)
        → a expressão SQL que ela recebe."""
        colunas = list(sets)
        linhas = self._rodar(sql.SQL("SELECT {}, {} FROM {}{} LIMIT {}").format(
            sql.SQL(", ").join(sql.SQL(c) for c in colunas), sql.SQL(", ").join(sql.SQL(sets[c]) for c in colunas),
            sql.Identifier(esquema, tabela), catalogo.onde_sql(onde), sql.Literal(int(limite))), engolir=True)
        if linhas is None:
            return None
        n = len(colunas)
        return [(tuple(l[:n]), tuple(l[n:])) for l in linhas]

    # ── as das permissões ──

    def usage_no_schema(self, esquema, nomes: List[str]) -> Optional[Dict[str, bool]]:
        """Nome do papel ("public" para o PUBLIC) → tem USAGE no schema, como o PostgreSQL decide (direto, pelo
        PUBLIC, por um grupo, sendo o dono ou superusuário). None no tempo esgotado ou se o banco recusar."""
        linhas = self._rodar("SELECT u.nome, has_schema_privilege(u.nome::name, %s, 'USAGE') "
                             "FROM unnest(%s::text[]) AS u(nome)", (esquema, list(dict.fromkeys(nomes))), engolir=True)
        return None if linhas is None else {nome: bool(tem) for nome, tem in linhas}

    def permissoes(self, esquema) -> Optional[catalogo.Permissoes]:
        """As permissões do schema (`catalogo.permissoes`); None no tempo esgotado."""
        self._erro = None
        try:
            return catalogo.permissoes(self.conexao, esquema, self.limite_ms)
        except psycopg.errors.QueryCanceled:
            return None

    def _grafo(self) -> Optional[Dict[int, List[_Aresta]]]:
        """Todas as chaves estrangeiras do banco (de qualquer schema), por tabela pai. As que o PostgreSQL copia nas
        partições da tabela FILHA ficam de fora (a tabela mãe já conta por elas); as copiadas para cada partição da
        tabela PAI ficam, e só são alcançadas quando se apaga direto numa partição."""
        anula = ("CASE WHEN cardinality(coalesce(k.confdelsetcols, '{}')) > 0 THEN k.confdelsetcols ELSE k.conkey END"
                 if self.versao() >= 150000 else "k.conkey")           # ON DELETE SET NULL (colunas), do PG 15
        linhas = self._rodar(
            "SELECT k.confrelid, k.conrelid, n.nspname::text, c.relname::text, "
            "ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY u(num, o) "
            "      JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.num ORDER BY u.o), "
            "ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY u(num, o) "
            "      JOIN pg_attribute a ON a.attrelid = k.confrelid AND a.attnum = u.num ORDER BY u.o), "
            "k.confdeltype::text, "
            "EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = k.conrelid AND a.attnotnull "
            f"        AND a.attnum = ANY ({anula})) "
            "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE k.contype = 'f' AND NOT c.relispartition ORDER BY k.oid", engolir=True)
        if linhas is None:
            return None
        grafo: Dict[int, List[_Aresta]] = {}
        for pai, filha, esquema, nome, minhas, delas, tipo, nulo in linhas:
            grafo.setdefault(pai, []).append(_Aresta(filha, esquema, nome, tuple(minhas), tuple(delas),
                                                     _ACOES.get(tipo, "NO ACTION"), bool(nulo)))
        return grafo

    def cascata(self, esquema, tabela, onde: str) -> Optional["Cascata"]:
        """O que apagar as linhas do WHERE faz nas outras tabelas: uma `Cascata` [(tabela, ação, linhas)], só as que
        têm alguma. As chaves estrangeiras vêm do catálogo (de qualquer schema). Para cada tabela alcançada por
        CASCADE monta-se o conjunto exato das linhas que sairiam (as das tabelas pai, e numa chave para a própria
        tabela um `WITH RECURSIVE … UNION`, que termina mesmo num ciclo); "CASCADE" conta linhas DISTINTAS além das
        do WHERE. As outras ações contam as linhas filhas que apontam para as que saem e não saem junto: `NO ACTION`/
        `RESTRICT` impedem, `NULO_EM_OBRIGATORIA` é SET NULL numa coluna NOT NULL (também impede). Ciclo entre
        tabelas diferentes, mais de `_PROFUNDIDADE` níveis ou mais de `_ORCAMENTO_S` segundos: o que se viu é o
        mínimo e a cascata fica `incompleta`. Tabela que não se pode ler vai em `sem_permissao`. None se uma
        consulta estourar o tempo ou o WHERE falhar."""
        comeco = time.monotonic()
        inicio = self._rodar("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                             "WHERE n.nspname = %s AND c.relname = %s", (esquema, tabela), engolir=True)
        grafo = self._grafo() if inicio else None
        if not inicio or grafo is None:
            return None
        t0 = inicio[0][0]
        res = Cascata()
        nomes = {t0: _qualificado(esquema, tabela)}
        rotulos = {t0: f"{esquema}.{tabela}"}

        # 1. as tabelas que perdem linhas (por CASCADE), em ordem topológica; ciclo ou teto cortam e marcam
        ordem: List[int] = []
        estado: Dict[int, int] = {}
        cortadas = set()

        def visitar(oid, nivel):
            estado[oid] = 1
            for i, a in enumerate(grafo.get(oid, [])):
                if a.acao != "CASCADE" or a.filha == oid:
                    continue
                nomes.setdefault(a.filha, _qualificado(a.esquema, a.nome))
                rotulos.setdefault(a.filha, f"{a.esquema}.{a.nome}")
                if estado.get(a.filha) == 1 or (a.filha not in estado and nivel + 1 >= _PROFUNDIDADE):
                    cortadas.add((oid, i))
                    res.incompleta = True
                elif a.filha not in estado:
                    visitar(a.filha, nivel + 1)
            estado[oid] = 2
            ordem.append(oid)
        visitar(t0, 0)
        ordem.reverse()

        # 2. o predicado de cada uma: "esta linha sai"
        chegam: Dict[int, List[Tuple[int, _Aresta]]] = {}
        for pai in ordem:
            for i, a in enumerate(grafo.get(pai, [])):
                if a.acao == "CASCADE" and a.filha != pai and (pai, i) not in cortadas:
                    chegam.setdefault(a.filha, []).append((pai, a))
        sai: Dict[int, str] = {}
        depende: Dict[int, set] = {}
        for n_, x in enumerate(ordem):
            partes = [onde or "true"] if x == t0 else []
            depende[x] = set()
            for pai, a in chegam.get(x, []):
                if pai in sai:
                    partes.append(_pega(nomes[x], a.minhas, nomes[pai], a.delas, sai[pai]))
                    depende[x] |= depende[pai] | {pai}
            base = " OR ".join(f"({p})" for p in partes) if partes else "false"
            proprias = [a for a in grafo.get(x, []) if a.acao == "CASCADE" and a.filha == x]
            sai[x] = _recursivo(nomes[x], base, proprias, n_) if proprias else base
            if len(sai[x]) > _TAMANHO_MAX:
                del sai[x]
                res.incompleta = True

        # 3. as contagens, dentro do orçamento de tempo
        presas = set()

        def contar(consulta: str, tabela_oid: int) -> Optional[int]:
            if time.monotonic() - comeco > _ORCAMENTO_S:
                res.incompleta = True
                return -1
            try:
                linhas = self._rodar(consulta)
            except psycopg.errors.InsufficientPrivilege:
                presas.add(tabela_oid)
                if rotulos[tabela_oid] not in res.sem_permissao:
                    res.sem_permissao.append(rotulos[tabela_oid])
                res.incompleta = True
                return -1
            except psycopg.Error as e:
                self._falhou(e)
                return None
            return None if linhas is None else int(linhas[0][0])

        somas: Dict[Tuple[str, str], int] = {}
        for x in ordem:
            if x not in sai or depende[x] & presas:
                continue
            proprias = any(a.acao == "CASCADE" and a.filha == x for a in grafo.get(x, []))
            if x != t0 or proprias:
                extra = f" AND ({onde or 'true'}) IS NOT TRUE" if x == t0 else ""
                k = contar(f"SELECT count(*) FROM {nomes[x]} WHERE ({sai[x]}){extra}", x)
                if k is None:
                    return None
                if k > 0:
                    somas[(rotulos[x], "CASCADE")] = somas.get((rotulos[x], "CASCADE"), 0) + k
            if x in presas:
                continue
            for a in grafo.get(x, []):
                if a.acao == "CASCADE":
                    continue
                nomes.setdefault(a.filha, _qualificado(a.esquema, a.nome))
                rotulos.setdefault(a.filha, f"{a.esquema}.{a.nome}")
                consulta = f"SELECT count(*) FROM {nomes[a.filha]} WHERE " + \
                    _pega(nomes[a.filha], a.minhas, nomes[x], a.delas, sai[x])
                if a.filha in sai:                  # quem também sai junto não impede nem fica vazio
                    consulta += f" AND ({sai[a.filha]}) IS NOT TRUE"
                k = contar(consulta, a.filha)
                if k is None:
                    return None
                if k > 0:
                    acao = NULO_EM_OBRIGATORIA if a.acao == "SET NULL" and a.nulo_em_obrigatoria else a.acao
                    somas[(rotulos[a.filha], acao)] = somas.get((rotulos[a.filha], acao), 0) + k
        res.extend((t_, a, n) for (t_, a), n in somas.items())
        return res

    def versao(self) -> int:
        return catalogo.versao(self.conexao)

    def valor_valido(self, tipo: str, texto: str) -> Optional[bool]:
        """O texto serve para o tipo? `pg_input_is_valid` no PostgreSQL 16+; antes (ou com `forcar_cast`), um CAST
        num savepoint da transação somente leitura. `tipo` vem do catálogo e ainda assim tem de passar por
        `tipo_seguro`. None se não deu para saber (tempo, tipo desconhecido; o motivo em `erro()`)."""
        self._erro = None
        if not tipo_seguro(tipo):
            self._erro = t("chk.tipo_recusado", tipo=tipo)
            return None
        try:
            with catalogo.somente_leitura(self.conexao, self.limite_ms):
                if self.versao() >= 160000 and not self.forcar_cast:
                    return bool(self.conexao.execute("SELECT pg_input_is_valid(%s, %s)", (texto, tipo)).fetchone()[0])
                try:
                    with self.conexao.transaction():
                        self.conexao.execute(sql.SQL("SELECT CAST(%s::text AS {})").format(sql.SQL(tipo)), (texto,))
                    return True
                except psycopg.DataError:                   # classe 22: o valor não serve para o tipo
                    return False
        except psycopg.errors.QueryCanceled:
            return None
        except psycopg.Error as e:
            self._falhou(e)
            return None

    def ja_existe(self, esquema, tabela, coluna, literal_sql: str) -> Optional[int]:
        """Quantas linhas já têm este valor na coluna. `literal_sql` sai de `filtro.literal` (nunca texto cru)."""
        return self.contar_onde(esquema, tabela, f"{rs.ident(coluna)} = {literal_sql}")

    def grupos_repetidos(self, esquema, tabela, onde: str, por_sql: List[str]) -> Optional[Tuple[int, int]]:
        """(grupos com mais de uma linha, linhas que sairiam deixando uma por grupo)."""
        linhas = self._rodar(sql.SQL(
            "SELECT count(*), coalesce(sum(n - 1), 0) FROM (SELECT count(*) AS n FROM {}{} GROUP BY {} "
            "HAVING count(*) > 1) AS g").format(sql.Identifier(esquema, tabela), catalogo.onde_sql(onde),
                                                sql.SQL(", ").join(sql.SQL(p) for p in por_sql)), engolir=True)
        return None if linhas is None else (int(linhas[0][0]), int(linhas[0][1]))


# ── avisos de uma operação ──

# Os apelidos, as famílias e a normalização de tipos moram em filtro.py (o filtro também precisa deles).
_tipo_normal = filtro.tipo_normal
_familia = filtro.familia_fina


def _id_catalogo(id: str):
    """(oid, attnum) de um id do catálogo (`t123`, `t123.c4`); None para o que nasceu no rascunho."""
    m = re.fullmatch(r"t(\d+)(?:\.c(\d+))?", id)
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


class _Contagens:
    """Faz as contagens de uma operação com os nomes da base (o que existe no banco), junta o que
    ficou sem resposta num aviso só e deduz sem consultar o que o rascunho criou."""

    def __init__(self, r: rs.Rascunho, consulta):
        self.r, self.consulta = r, consulta
        self.avisos: List[Aviso] = []

    def _sem_tempo(self, alvo):
        self.avisos.append(Aviso(alvo, "info", "chk.sem_tempo", {}))

    def _base(self, tabela_id, coluna_id=None):
        tab = self.r.base.tabelas.get(tabela_id)
        return tab, (tab.coluna(coluna_id) if tab is not None and coluna_id else None)

    def linhas(self, tabela_id) -> Optional[int]:
        tab, _ = self._base(tabela_id)
        if tab is None:
            return 0                        # criada no rascunho: ainda não tem linha
        n = self.consulta.linhas(tab.esquema, tab.nome)
        if n is None:
            self._sem_tempo(tabela_id)
        return n

    def nulos(self, tabela_id, coluna_id) -> Optional[int]:
        tab, col = self._base(tabela_id, coluna_id)
        if tab is None:
            return 0
        if col is None:                     # coluna do rascunho: só tem NULL se não houver padrão
            vista = self.r.vista().tabelas[tabela_id].coluna(coluna_id)
            return self.linhas(tabela_id) if vista is not None and vista.padrao is None else 0
        n = self.consulta.nulos(tab.esquema, tab.nome, col.nome)
        if n is None:
            self._sem_tempo(coluna_id)
        return n

    def _deduzida(self, tabela_id, coluna_id, extra, repetidos: bool) -> Optional[int]:
        """Contagem de uma coluna que o banco ainda não tem. NULL nunca é repetido nem órfão; com
        padrão, todas as linhas ganham o mesmo valor: repetido se houver mais de uma. Sem como deduzir,
        None (e o aviso de indeterminado)."""
        if tabela_id not in self.r.base.tabelas:
            return 0                        # tabela do rascunho: sem linhas
        tab = self.r.vista().tabelas.get(tabela_id)
        col = (tab.coluna(coluna_id) if tab is not None else None) or extra
        if col is not None and col.padrao is None:
            return 0
        if col is not None and repetidos:
            k = self.linhas(tabela_id)
            return None if k is None else int(k > 1)
        self.avisos.append(Aviso(coluna_id, "info", "chk.indeterminado", {}))
        return None

    def repetidos(self, tabela_id, coluna_id, extra=None) -> Optional[int]:
        tab, col = self._base(tabela_id, coluna_id)
        if col is None:
            return self._deduzida(tabela_id, coluna_id, extra, True)
        n = self.consulta.repetidos(tab.esquema, tab.nome, col.nome)
        if n is None:
            self._sem_tempo(coluna_id)
        return n

    def orfaos(self, origem_id, coluna_id, destino_id, coluna_destino_id, extra=None) -> Optional[int]:
        tab, col = self._base(origem_id, coluna_id)
        pai, col_pai = self._base(destino_id, coluna_destino_id)
        if col is None:
            return self._deduzida(origem_id, coluna_id, extra, False)
        if col_pai is None:
            self.avisos.append(Aviso(coluna_id, "info", "chk.indeterminado", {}))
            return None
        n = self.consulta.orfaos(tab.esquema, tab.nome, col.nome, pai.esquema, pai.nome, col_pai.nome)
        if n is None:
            self._sem_tempo(coluna_id)
        return n


def _nome(v: Modelo, tabela_id, coluna_id=None, extra=None) -> str:
    tab = v.tabelas[tabela_id]
    if coluna_id is None:
        return tab.nome
    c = tab.coluna(coluna_id) or extra
    return f"{tab.nome}.{c.nome}"


def _obrigatoria(op, r, n: _Contagens, v):
    if op.sim:
        k = n.nulos(op.tabela, op.coluna)
        if k:
            n.avisos.append(Aviso(op.coluna, "impede", "chk.nulos_impedem",
                                  {"n": k, "coluna": _nome(v, op.tabela, op.coluna)}))


def _unica(op, r, n: _Contagens, v):
    if op.sim:
        k = n.repetidos(op.tabela, op.coluna)
        if k:
            n.avisos.append(Aviso(op.coluna, "impede", "chk.repetidos_impedem",
                                  {"n": k, "coluna": _nome(v, op.tabela, op.coluna)}))


def _adicionar_coluna(op, r, n: _Contagens, v):
    c = op.coluna
    if c.obrigatoria and c.padrao is None:
        k = n.linhas(op.tabela)
        if k:
            n.avisos.append(Aviso(c.id, "impede", "chk.obrigatoria_sem_padrao",
                                  {"n": k, "coluna": _nome(v, op.tabela, c.id, c)}))


def _ligar(op, r, n: _Contagens, v):
    lig = op.ligacao
    origem, destino = v.tabelas[lig.origem], v.tabelas[lig.destino]
    col = origem.coluna(lig.colunas[0]) or op.nova_coluna
    col_pai = destino.coluna(lig.colunas_destino[0])
    nome = f"{origem.nome}.{col.nome}"
    tipos = {"coluna": nome, "tipo": col.tipo, "pai": f"{destino.nome}.{col_pai.nome}", "tipo_pai": col_pai.tipo}
    if _familia(col.tipo) != _familia(col_pai.tipo):
        n.avisos.append(Aviso(lig.id, "impede", "chk.tipos", tipos))
    else:                                   # de famílias diferentes a contagem nem rodaria
        if _tipo_normal(col.tipo) != _tipo_normal(col_pai.tipo):
            n.avisos.append(Aviso(lig.id, "info", "chk.tipos_familia", tipos))
        k = n.orfaos(lig.origem, col.id, lig.destino, col_pai.id, op.nova_coluna)
        if k:
            n.avisos.append(Aviso(lig.id, "impede", "chk.orfaos_impedem", {"n": k, "coluna": nome}))
    if op.unica:
        n.avisos.append(Aviso(lig.id, "info", "chk.um_para_um", {"coluna": nome}))


def _ainda_depende(d: Dependente, r: rs.Rascunho, v: Modelo) -> bool:
    """Falso se o rascunho já apagou a tabela dependente ou a chave estrangeira (nesse caso ela não
    impede mais). O que a base não conhece (outro esquema, views) continua valendo."""
    alvo = "t%d" % d.oid
    if alvo in r.base.tabelas and (alvo not in v.tabelas or v.tabelas[alvo].estado == "apagado"):
        return False
    if d.oid_restricao is not None:
        k = "k%d" % d.oid_restricao
        if k in r.base.ligacoes and (k not in v.ligacoes or v.ligacoes[k].estado == "apagado"):
            return False
    return True


def _dependentes(op, r, n: _Contagens, v, alvo, nome, oid_attnum):
    if oid_attnum is None:                  # o que nasceu no rascunho não existe no banco: ninguém depende
        return
    deps = n.consulta.dependentes(*oid_attnum)
    if deps is None:
        n._sem_tempo(alvo)
        return
    deps = [d for d in (deps or []) if _ainda_depende(d, r, v)]
    if deps:
        n.avisos.append(Aviso(alvo, "atencao", "chk.dependentes",
                              {"nome": nome, "lista": "; ".join(d.texto() for d in deps)}))
        if not op.cascata:
            n.avisos.append(Aviso(alvo, "impede", "chk.precisa_cascata", {"nome": nome}))


def _apagar_coluna(op, r, n: _Contagens, v):
    ids = _id_catalogo(op.coluna) if op.tabela in r.base.tabelas else None
    _dependentes(op, r, n, v, op.coluna, _nome(v, op.tabela, op.coluna), ids)


def _apagar_tabela(op, r, n: _Contagens, v):
    ids = _id_catalogo(op.tabela) if op.tabela in r.base.tabelas else None
    nome = v.tabelas[op.tabela].nome
    _dependentes(op, r, n, v, op.tabela, nome, ids)
    k = n.linhas(op.tabela)
    if k:
        n.avisos.append(Aviso(op.tabela, "atencao", "chk.linhas_somem", {"n": k, "tabela": nome}))


_POR_OPERACAO = {rs.Obrigatoria: _obrigatoria, rs.Unica: _unica, rs.AdicionarColuna: _adicionar_coluna,
                 rs.Ligar: _ligar, rs.ApagarColuna: _apagar_coluna, rs.ApagarTabela: _apagar_tabela}


# ── avisos das operações de dados ──
# As contagens rodam com os nomes da BASE (o que o banco tem agora): o WHERE de cada uma é `op.onde(r.base)`, o
# mesmo texto que o comando vai rodar. O que o rascunho criou e o banco ainda não tem não é consultado.

_SOFT_DELETE = ("deleted_at", "discarded_at", "is_deleted", "ativo", "active", "removido")
_CONTAGENS = ("chk.linhas_alteradas", "chk.linhas_apagadas")


def contagem(avisos: List[Aviso]) -> Optional[int]:
    """Quantas linhas a operação pega, como os avisos contaram; None se não deu para contar."""
    return next((a.valores["n"] for a in avisos if a.chave in _CONTAGENS), None)


def _unica_sozinha(tab, c) -> bool:
    """A coluna sozinha não aceita repetido: UNIQUE dela ou a chave primária inteira."""
    return c.unica or (c.pk and len(tab.pk()) == 1)


def _espaco_nas_pontas(texto: str) -> bool:
    """Espaço sobrando no começo ou no fim. As quebras de linha do fim não contam: quase todo arquivo de texto
    termina numa (`\n` ou `\r\n`), e avisar de todo FASTA carregado ensinaria a ignorar o aviso."""
    sem_fim = texto.rstrip("\r\n")
    return sem_fim != sem_fim.strip()


class _Dados:
    def __init__(self, op, r: rs.Rascunho, consulta):
        self.op, self.r, self.consulta = op, r, consulta
        self.v = r.vista()
        self.tab = self.v.tabelas[op.tabela]
        self.base = r.base
        self.tab_base = r.base.tabelas.get(op.tabela)
        self.na_base = self.tab_base is not None and all(rs._nome_na_base(r.base, i) is not None for i in op.refere())
        self.avisos: List[Aviso] = []

    def avisar(self, alvo, nivel, chave, **valores) -> None:
        if not any(a.alvo == alvo and a.chave == chave and a.valores == valores for a in self.avisos):
            self.avisos.append(Aviso(alvo, nivel, chave, valores))

    def falhou(self, alvo) -> None:
        """A consulta devolveu None: o tempo acabou ou (com o WHERE escrito à mão) o banco recusou."""
        _sem_resposta(self.consulta, self.avisar, alvo)

    def contar(self, onde: str, alvo) -> Optional[int]:
        n = self.consulta.contar_onde(self.tab_base.esquema, self.tab_base.nome, onde)
        if n is None:
            self.falhou(alvo)
        return n

    def valor(self, c, texto: str) -> Optional[str]:
        """O literal SQL de um valor digitado, ou None (com o aviso) se ele não servir para o tipo da coluna."""
        if filtro.familia(c.tipo) == "texto" and _espaco_nas_pontas(texto):
            self.avisar(c.id, "atencao", "chk.espacos_valor", coluna=c.nome)
        try:
            literal = filtro.literal(texto, c.tipo)
        except ValueError:
            self.avisar(c.id, "impede", "chk.valor_invalido", coluna=c.nome, valor=arquivos.valor_curto(texto),
                        tipo=c.tipo)
            return None
        base = self.tab_base.coluna(c.id) if self.na_base else None
        if base is None or filtro.familia(c.tipo) == "booleano" or filtro.tipo_normal(c.tipo) == "text":
            return literal                  # o literal já decide (ou não há banco para perguntar)
        ok = self.consulta.valor_valido(base.tipo, texto)
        if ok is None:
            self.falhou(c.id)
        elif not ok:
            self.avisar(c.id, "impede", "chk.valor_invalido", coluna=c.nome, valor=arquivos.valor_curto(texto),
                        tipo=c.tipo)
            return None
        return literal

    def ja_existe(self, c, texto: str, literal: str) -> None:
        base = self.tab_base.coluna(c.id)
        k = self.consulta.ja_existe(self.tab_base.esquema, self.tab_base.nome, base.nome, literal)
        if k is None:
            self.falhou(c.id)
        elif k:
            self.avisar(c.id, "impede", "chk.ja_existe", coluna=c.nome, valor=arquivos.valor_curto(texto), n=k)

    def pai_existe(self, c, texto: str) -> None:
        """Valor de chave estrangeira (de uma coluna só) que não existe na tabela pai."""
        for lig in self.v.ligacoes.values():
            if lig.estado == "apagado" or lig.origem != self.op.tabela or tuple(lig.colunas) != (c.id,):
                continue
            pai = self.base.tabelas.get(lig.destino)
            col_pai = pai.coluna(lig.colunas_destino[0]) if pai is not None else None
            if col_pai is None:
                continue                    # o pai ainda não existe no banco: o ensaio confere
            try:
                literal = filtro.literal(texto, col_pai.tipo)
            except ValueError:
                continue
            k = self.consulta.ja_existe(pai.esquema, pai.nome, col_pai.nome, literal)
            if k is None:
                self.falhou(c.id)
            elif k == 0:
                vista_pai = self.v.tabelas[lig.destino]
                self.avisar(c.id, "impede", "chk.fk_inexistente", coluna=c.nome, valor=arquivos.valor_curto(texto),
                            pai=f"{vista_pai.nome}.{vista_pai.coluna(col_pai.id).nome}")

    def cascata(self, onde: str) -> None:
        achados = self.consulta.cascata(self.tab_base.esquema, self.tab_base.nome, onde)
        if achados is None:
            self.falhou(self.op.tabela)
            return
        incompleta = bool(getattr(achados, "incompleta", False))
        for tabela in getattr(achados, "sem_permissao", None) or []:
            self.avisar(self.op.tabela, "atencao", "chk.sem_permissao_ver", tabela=tabela)
        if incompleta:
            self.avisar(self.op.tabela, "atencao", "chk.cascata_incompleta")
        for filha, acao, k in achados or []:
            if not k:
                continue
            if acao == "CASCADE" and incompleta:        # o que se viu é o mínimo
                self.avisar(self.op.tabela, "atencao", "chk.cascata_apaga_pelo_menos", n=k, tabela=filha)
                continue
            chave, nivel = {"CASCADE": ("chk.cascata_apaga", "atencao"), "SET NULL": ("chk.cascata_esvazia", "atencao"),
                            "SET DEFAULT": ("chk.cascata_padrao", "atencao"),
                            NULO_EM_OBRIGATORIA: ("chk.cascata_nulo_impede", "impede")}.get(acao, ("chk.impede_filhos", "impede"))
            self.avisar(self.op.tabela, nivel, chave, n=k, tabela=filha)


def _sem_resposta(consulta, avisar, alvo) -> None:
    erro = getattr(consulta, "erro", None)
    motivo = erro() if callable(erro) else None
    if motivo:
        avisar(alvo, "impede", "chk.consulta_falhou", erro=rodar.curta(motivo))     # o aplicar falharia do mesmo jeito
    else:
        avisar(alvo, "info", "chk.sem_tempo")


def _inserir(d: _Dados) -> None:
    op, tab = d.op, d.tab
    for c in tab.colunas:
        if c.estado != "apagado" and c.obrigatoria and c.padrao is None and not c.identidade \
                and op.valores.get(c.id) is None:
            d.avisar(c.id, "impede", "chk.obrigatoria_vazia", coluna=c.nome)
    for i, texto in op.valores.items():
        c = tab.coluna(i)
        if c is None or texto is None:
            continue
        literal = d.valor(c, texto)
        if literal is None or not d.na_base:
            continue
        if _unica_sozinha(tab, c):
            d.ja_existe(c, texto, literal)
        d.pai_existe(c, texto)


def _alterar(d: _Dados) -> None:
    op, tab = d.op, d.tab
    n, onde, sets = None, None, {}
    if d.na_base:
        onde, sets = op.onde(d.base), op.sets(d.base)
        n = d.contar(onde, op.tabela)
        if n is not None:
            d.avisar(op.tabela, "info", "chk.linhas_alteradas", n=n, tabela=tab.nome)
    for i, mud in op.mudancas.items():
        c = tab.coluna(i)
        if c.obrigatoria and mud.tipo == "vazio" and n != 0:
            d.avisar(c.id, "impede", "chk.obrigatoria_recebe_vazio", coluna=c.nome)
        elif c.obrigatoria and mud.tipo in ("coluna", "branco_vazio") and d.na_base and n != 0:
            expr = sets[rs.ident(d.tab_base.coluna(i).nome)]
            k = d.contar(f"({onde}) AND ({expr}) IS NULL", c.id)
            if k:
                d.avisar(c.id, "impede", "chk.obrigatoria_recebe_vazio_n", coluna=c.nome, n=k)
        if mud.tipo != "valor" or mud.valor is None:
            continue
        literal = d.valor(c, mud.valor)
        if literal is None or not d.na_base:
            continue
        if _unica_sozinha(tab, c):
            if n is not None and n > 1:
                d.avisar(c.id, "impede", "chk.unica_varias", coluna=c.nome, n=n)
            elif n:                         # quem já tem o valor e fica fora do UPDATE continua com ele
                col = rs.ident(d.tab_base.coluna(i).nome)
                k = d.contar(f"{col} = {literal} AND ({onde}) IS NOT TRUE", c.id)
                if k:
                    d.avisar(c.id, "impede", "chk.ja_existe", coluna=c.nome, valor=arquivos.valor_curto(mud.valor),
                             n=k)
        d.pai_existe(c, mud.valor)


def _apagar(d: _Dados) -> None:
    op = d.op
    if d.na_base:
        onde = op.onde(d.base)
        n = d.contar(onde, op.tabela)
        if n is not None:
            d.avisar(op.tabela, "info", "chk.linhas_apagadas", n=n, tabela=d.tab.nome)
        d.cascata(onde)
    _soft_delete(d)


def _soft_delete(d: _Dados) -> None:
    for c in d.tab.colunas:
        if c.estado != "apagado" and c.nome.lower() in _SOFT_DELETE:
            d.avisar(d.op.tabela, "info", "chk.soft_delete", coluna=c.nome)
            return


def _duplicatas(d: _Dados) -> None:
    op, tab = d.op, d.tab
    if not tab.pk():
        d.avisar(op.tabela, "atencao", "chk.sem_pk_ctid", tabela=tab.nome)
    if not d.na_base:
        return
    grupos = d.consulta.grupos_repetidos(d.tab_base.esquema, d.tab_base.nome, op.onde_comparadas(d.base),
                                         op.por_sql(d.base))
    if grupos is None:
        d.falhou(op.tabela)
    else:
        d.avisar(op.tabela, "info", "chk.grupos_repetidos", grupos=(grupos or (0, 0))[0])
    onde = op.onde(d.base)
    n = d.contar(onde, op.tabela)
    if n is not None:
        d.avisar(op.tabela, "info", "chk.linhas_apagadas", n=n, tabela=tab.nome)
    fora = d.contar(op.onde_fora(d.base), op.tabela)
    if fora:
        d.avisar(op.tabela, "info", "chk.duplicatas_vazias_fora", n=fora)
    d.cascata(onde)


_DE_DADOS = {rs.Inserir: _inserir, rs.Alterar: _alterar, rs.Apagar: _apagar, rs.ApagarDuplicatas: _duplicatas}


def _avisos_de_dados(op, r: rs.Rascunho, consulta) -> List[Aviso]:
    d = _Dados(op, r, consulta)
    _DE_DADOS[type(op)](d)
    if not d.na_base:
        d.avisar(op.tabela, "info", "chk.so_depois_de_aplicar", tabela=d.tab.nome)
    else:
        antes = sum(1 for o in r.operacoes if op.tabela in (o.refere() | o.cria()))
        if antes:
            d.avisar(op.tabela, "info", "chk.aproximada", n=antes)
    return d.avisos


# ── avisos das permissões ──
# O que a pessoa tem vem da base (o catálogo de agora); o rascunho só conta para o USAGE que uma operação anterior
# já concede (o aviso não insiste no que vai junto).

def _anteriores(op, r: rs.Rascunho) -> list:
    """As operações do rascunho antes de `op` (todas, se ela ainda não está nele)."""
    for i, o in enumerate(r.operacoes):
        if o is op:
            return r.operacoes[:i]
    return list(r.operacoes)


def _usage_no_rascunho(anteriores, papel: str) -> bool:
    return any(isinstance(o, rs.PermissaoNoSchema) and o.usage is True and o.papel in (papel, rs.PUBLIC)
               for o in anteriores)


class _Permissao:
    def __init__(self, op, r: rs.Rascunho, consulta):
        self.op, self.r, self.consulta = op, r, consulta
        self.v = r.vista()
        self.eu = r.base.eu
        self.avisos: List[Aviso] = []
        self._perms = None
        self._lidas = False

    def avisar(self, alvo, nivel, chave, oferta=None, **valores) -> None:
        if not any(a.alvo == alvo and a.chave == chave and a.valores == valores for a in self.avisos):
            self.avisos.append(Aviso(alvo, nivel, chave, valores, oferta))

    def nome(self, papel: str) -> str:
        return rs.PUBLIC if papel == rs.PUBLIC else self.v.papeis.get(papel, papel)

    def perms(self, alvo) -> Optional[catalogo.Permissoes]:
        """`catalogo.permissoes` lida uma vez; None (com o aviso de sem resposta) se não deu."""
        if not self._lidas:
            self._lidas = True
            self._perms = self.consulta.permissoes(self.r.base.esquema)
            if self._perms is None:
                _sem_resposta(self.consulta, self.avisar, alvo)
        return self._perms

    def revogar_de_si(self, alvo, privilegios: str, onde: str) -> None:
        self.avisar(alvo, "atencao", "chk.perm_revogar_si", papel=self.nome(alvo), eu=self.nome(self.eu),
                    privilegios=privilegios, onde=onde)


def _sem_usage(d: _Permissao) -> None:
    op = d.op
    anteriores = _anteriores(op, d.r)
    faltam = [p for p in dict.fromkeys(op.papeis) if not _usage_no_rascunho(anteriores, p)]
    if not faltam:
        return
    nomes = {p: ("public" if p == rs.PUBLIC else d.nome(p)) for p in faltam}
    tem = d.consulta.usage_no_schema(d.r.base.esquema, list(nomes.values()))
    if tem is None:
        _sem_resposta(d.consulta, d.avisar, faltam[0])
        return
    for p in faltam:
        if not tem.get(nomes[p], False):
            d.avisar(p, "atencao", "chk.perm_sem_usage", rs.PermissaoNoSchema(p, usage=True, create=None),
                     papel=d.nome(p), esquema=d.r.base.esquema)


def _conceder(d: _Permissao) -> None:
    op = d.op
    _sem_usage(d)
    papeis = [p for p in dict.fromkeys(op.papeis) if p != rs.PUBLIC]
    if op.tabelas and papeis:
        perms = d.perms(papeis[0])
        dono = dict(perms.dono) if perms is not None else {}
        for i in op.tabelas:
            if i not in d.r.base.tabelas:
                dono[i] = d.eu                  # criada no rascunho: será de quem aplica
        for p in papeis:
            suas = [d.v.tabelas[i].nome for i in dict.fromkeys(op.tabelas) if dono.get(i) == p and i in d.v.tabelas]
            if suas:
                d.avisar(p, "info", "chk.perm_dono", papel=d.nome(p), tabelas=", ".join(suas))
    if op.futuras:
        d.avisar(op.papeis[0] if op.papeis else "", "info", "chk.perm_futuras",
                 papel=d.nome(op.por) if isinstance(op.por, str) else "")


def _revogar(d: _Permissao) -> None:
    op = d.op
    privs = ", ".join(nome for letra, nome in rs.PRIVILEGIOS.items() if letra in op.privilegios)
    onde = ", ".join(d.v.tabelas[i].nome for i in dict.fromkeys(op.tabelas) if i in d.v.tabelas) or d.r.base.esquema
    if d.eu in op.papeis:
        d.revogar_de_si(d.eu, privs, onde)
    elif rs.PUBLIC in op.papeis and op.tabelas:
        perms = d.perms(rs.PUBLIC)
        if perms is None:
            return
        for i in op.tabelas:
            if perms.dono.get(i) == d.eu:
                continue
            minhas = perms.por_tabela.get((d.eu, i), frozenset())
            publicas = perms.por_tabela.get((rs.PUBLIC, i), frozenset())
            if any(x in publicas and x not in minhas for x in op.privilegios):
                d.revogar_de_si(rs.PUBLIC, privs, onde)
                return


def _no_schema(d: _Permissao) -> None:
    op = d.op
    tirar = [nome for nome, valor in (("USAGE", op.usage), ("CREATE", op.create)) if valor is False]
    if not tirar:
        return
    onde = d.r.base.esquema
    if op.papel == d.eu:
        d.revogar_de_si(d.eu, ", ".join(tirar), onde)
    elif op.papel == rs.PUBLIC:
        perms = d.perms(rs.PUBLIC)
        if perms is None:
            return
        minhas, publicas = perms.schema.get(d.eu, frozenset()), perms.schema.get(rs.PUBLIC, frozenset())
        if any(x in publicas and x not in minhas for x in tirar):
            d.revogar_de_si(rs.PUBLIC, ", ".join(tirar), onde)


_DE_PERMISSAO = {rs.Conceder: _conceder, rs.Revogar: _revogar, rs.PermissaoNoSchema: _no_schema}


def avisos_da_operacao(op, r: rs.Rascunho, consulta) -> List[Aviso]:
    """O que a operação vai encontrar no banco, antes de entrar no rascunho. `consulta` é uma `Consulta`
    (ou qualquer objeto com os mesmos métodos)."""
    if type(op) in _DE_DADOS:
        return _avisos_de_dados(op, r, consulta)
    if type(op) in _DE_PERMISSAO:
        d = _Permissao(op, r, consulta)
        _DE_PERMISSAO[type(op)](d)
        return d.avisos
    f = _POR_OPERACAO.get(type(op))
    if f is None:
        return []
    n = _Contagens(r, consulta)
    f(op, r, n, r.vista())
    return n.avisos


# ── avisos do filtro ──

def avisos_do_filtro(f: filtro.Filtro, r: rs.Rascunho, tabela_id: str, consulta) -> List[Aviso]:
    """As armadilhas do filtro, contadas no banco com os nomes da base do rascunho (o que o banco tem agora): uma
    coluna que só existe no rascunho deixa as contagens para depois de aplicar. Cada contagem é de quem o filtro
    INTEIRO deixa de fora só por causa da armadilha: com OU, quem entra por outra condição não conta."""
    m = r.base
    avisos: List[Aviso] = []

    def avisar(alvo, nivel, chave, **valores):
        if not any(a.alvo == alvo and a.chave == chave for a in avisos):
            avisos.append(Aviso(alvo, nivel, chave, valores))

    if filtro.fora_lista_com_vazio(f):
        avisar(tabela_id, "atencao", "chk.fora_lista_vazio")
    tab = m.tabelas.get(tabela_id)
    if tab is None or any(rs._nome_na_base(m, i) is None for i in filtro.refere(f, tabela_id)):
        vista = r.vista().tabelas.get(tabela_id)
        avisar(tabela_id, "info", "chk.so_depois_de_aplicar", tabela=vista.nome if vista is not None else tabela_id)
        return avisos
    if f.vazio() or (f.texto is not None and f.texto.strip()):
        return avisos
    try:
        inteiro = filtro.where(f, m, tabela_id)
    except ValueError:
        return avisos
    fora = f"({inteiro}) IS NOT TRUE"

    def contar(onde, alvo):
        n = consulta.contar_onde(tab.esquema, tab.nome, onde)
        if n is None:
            _sem_resposta(consulta, avisar, alvo)
        return n

    # vazio: quem passaria pelo resto do filtro (com a condição trocada por TRUE), tem NULL e acaba de fora
    nulas = filtro.deixa_vazios_de_fora(f, m, tabela_id)
    restos: Dict[str, List[str]] = {}
    for j, c in enumerate(f.condicoes):
        if c.existe is not None:
            continue
        ids = ([] if c.operador in ("vazio", "preenchido", "verdadeiro", "falso") else [c.coluna]) + \
              ([c.outra_coluna] if c.outra_coluna is not None else [])
        outras = f.condicoes[:j] + f.condicoes[j + 1:]
        resto = "" if f.conectivo == "OU" or not outras else filtro.where(filtro.Filtro(outras), m, tabela_id)
        for cid in ids:
            if cid in nulas:
                restos.setdefault(cid, []).append(resto)
    for cid in nulas:
        if cid not in restos:
            continue
        partes = [f"{rs.ident(tab.coluna(cid).nome)} IS NULL", fora]
        if all(restos[cid]):                # algum resto vazio quer dizer TRUE: não restringe
            ou = " OR ".join(f"({x})" for x in restos[cid])
            partes.insert(0, f"({ou})" if len(restos[cid]) > 1 else ou)
        k = contar(" AND ".join(partes), cid)
        if k:
            avisar(cid, "atencao", "chk.vazios_de_fora", n=k, coluna=tab.coluna(cid).nome)
    # espaço nas pontas: quem o filtro pegaria com a coluna aparada e não pega
    for j, c in enumerate(f.condicoes):
        col = tab.coluna(c.coluna) if c.existe is None and c.outra_coluna is None else None
        if col is None or c.transformacao or c.operador not in ("contem", "comeca", "termina") \
                or filtro.familia(col.tipo) != "texto":
            continue
        trocadas = list(f.condicoes)
        trocadas[j] = replace(c, transformacao="trim")
        aparado = filtro.where(filtro.Filtro(trocadas, f.conectivo), m, tabela_id)
        k = contar(f"({aparado}) IS TRUE AND {fora}", col.id)
        if k:
            avisar(col.id, "atencao", "chk.espacos_nas_pontas", n=k, coluna=col.nome)
    return avisos
