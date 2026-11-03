"""O que existe dentro de um banco, lido do catálogo do PostgreSQL — o que a TUI desenha.

Cada função recebe uma conexão aberta e devolve dados simples (tuplas, dicionários, texto):
quem desenha não precisa saber SQL, e quem consulta não precisa saber desenhar.
"""
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

from ratt.db import modelo as _modelo

# os esquemas do próprio PostgreSQL ficam de fora da árvore
_SEM_SISTEMA = "n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname !~ '^pg_(toast|temp)'"


@dataclass
class Objeto:
    esquema: str
    nome: str
    tipo: str               # tabela, view, funcao, sequencia
    oid: int
    linhas: int = None      # estimativa do PostgreSQL (reltuples); None se ele ainda não contou
    argumentos: str = ""    # só das funções


@dataclass
class Esquema:
    nome: str
    tabelas: list = field(default_factory=list)
    views: list = field(default_factory=list)
    funcoes: list = field(default_factory=list)
    sequencias: list = field(default_factory=list)


_TIPOS = {"r": "tabela", "p": "tabela", "f": "tabela", "v": "view", "m": "view", "S": "sequencia"}
_GRUPO = {"tabela": "tabelas", "view": "views", "sequencia": "sequencias", "funcao": "funcoes"}


def esquemas(conexao):
    """[Esquema], com o que há em cada um. O public vem primeiro."""
    achados = {}
    for (nome,) in conexao.execute(f"SELECT n.nspname FROM pg_namespace n WHERE {_SEM_SISTEMA}"):
        achados[nome] = Esquema(nome)
    for esquema, nome, tipo, oid, linhas in conexao.execute(f"""
            SELECT n.nspname, c.relname, c.relkind, c.oid, c.reltuples::bigint
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'f', 'v', 'm', 'S') AND NOT c.relispartition AND {_SEM_SISTEMA}
            ORDER BY c.relname"""):
        o = Objeto(esquema, nome, _TIPOS[tipo], oid, linhas if linhas is not None and linhas >= 0 else None)
        getattr(achados.setdefault(esquema, Esquema(esquema)), _GRUPO[o.tipo]).append(o)
    for esquema, nome, oid, argumentos in conexao.execute(f"""
            SELECT n.nspname, p.proname, p.oid, pg_get_function_identity_arguments(p.oid)
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE {_SEM_SISTEMA}
              AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')
            ORDER BY p.proname"""):
        achados.setdefault(esquema, Esquema(esquema)).funcoes.append(Objeto(esquema, nome, "funcao", oid, argumentos=argumentos))
    return sorted(achados.values(), key=lambda e: (e.nome != "public", e.nome))


def assinatura(conexao, escritas: bool = True, papeis: bool = True, acls: Optional[str] = None):
    """Um resumo de tudo o que importa para a tela: objetos, colunas e as escritas em cada tabela.
    Se mudou entre uma olhada e outra, alguém (um ratt db run, o psql) mexeu no banco. `escritas=False` deixa de
    fora os contadores de linhas inseridas/alteradas: o canvas só desenha a estrutura, e com eles ele releria o
    schema a cada volta do vigia num banco que recebe escritas. `papeis=False` deixa de fora os papéis do servidor:
    a tela do `ratt db` não mostra papéis, e com eles avisaria "o banco mudou" a cada usuário criado em qualquer
    banco (o canvas e a tela Dados os querem: o rascunho do schema refere papéis nas permissões). `acls`: o nome de um
    schema cujas permissões também contam — o `relacl` e o dono das tabelas, o `nspacl` e o dono do schema e as
    permissões padrão (`pg_default_acl`) dele: a tela Permissões relê a grade quando alguém dá um GRANT por fora (as
    outras telas não mostram permissões e não pedem)."""
    escritas_sql = ("(SELECT coalesce(sum(n_tup_ins + n_tup_upd + n_tup_del), 0) FROM pg_stat_user_tables)"
                    if escritas else "0")
    papeis_sql = ("(SELECT md5(coalesce(string_agg(r.oid || ':' || r.rolname, ',' ORDER BY r.oid), '')) FROM pg_roles r)"
                  if papeis else "''")
    acls_sql = """(SELECT md5(
            coalesce((SELECT string_agg(c.oid || ':' || c.relowner || ':' || coalesce(c.relacl::text, '-'), ','
                                        ORDER BY c.oid)
                        FROM pg_class c WHERE c.relnamespace = n.oid AND c.relkind IN ('r', 'p')), '')
            || '|' || n.nspowner || ':' || coalesce(n.nspacl::text, '-') || '|'
            || coalesce((SELECT string_agg(d.oid || ':' || d.defaclrole || ':' || d.defaclobjtype::text || ':'
                                           || d.defaclacl::text, ',' ORDER BY d.oid)
                           FROM pg_default_acl d WHERE d.defaclnamespace = n.oid), ''))
          FROM pg_namespace n WHERE n.nspname = %s)""" if acls is not None else "''"
    return conexao.execute(f"""
        SELECT
          (SELECT md5(coalesce(string_agg(c.oid || c.relname || c.relkind::text, ',' ORDER BY c.oid), ''))
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'f', 'v', 'm', 'S') AND {_SEM_SISTEMA}),
          (SELECT md5(coalesce(string_agg(a.attrelid || a.attname || a.atttypid::text || a.attnotnull::text, ',' ORDER BY a.attrelid, a.attnum), ''))
             FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'p', 'v', 'm') AND {_SEM_SISTEMA}),
          (SELECT md5(coalesce(string_agg(p.oid::text, ',' ORDER BY p.oid), ''))
             FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE {_SEM_SISTEMA}),
          (SELECT md5(coalesce(string_agg(k.oid || k.conname || k.contype::text || k.conrelid::text, ',' ORDER BY k.oid), ''))
             FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace WHERE {_SEM_SISTEMA}),
          (SELECT md5(coalesce(string_agg(x.indexrelid || ':' || x.indrelid || ':' || x.indkey::text, ',' ORDER BY x.indexrelid), ''))
             FROM pg_index x JOIN pg_class c ON c.oid = x.indrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE {_SEM_SISTEMA}),
          {papeis_sql},
          {escritas_sql},
          {acls_sql}""", (acls,) if acls is not None else None).fetchone()


def esquemas_com_tabelas(conexao):
    """Os nomes dos esquemas que não são do sistema, o public primeiro."""
    nomes = [n for (n,) in conexao.execute(f"SELECT n.nspname FROM pg_namespace n WHERE {_SEM_SISTEMA}")]
    return sorted(nomes, key=lambda n: (n != "public", n))


_AO_APAGAR = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}


def modelo(conexao, esquema):
    """O esquema inteiro como um Modelo, em cinco consultas — não importa quantas tabelas tenha.
    Partições ficam de fora (a tabela mãe conta quantas são); chaves estrangeiras para outro
    esquema também, porque o desenho só tem as tabelas deste. Os papéis do servidor (`r{oid}` → nome) e o da pessoa
    (`eu`) vão junto: as operações de permissão os referem pelo id, e o rebase vê um papel que sumiu ou mudou de nome."""
    m = _modelo.Modelo(esquema)
    for oid, nome, sou_eu in conexao.execute("SELECT oid, rolname, rolname = current_user FROM pg_roles ORDER BY oid"):
        m.papeis["r%d" % oid] = nome
        if sou_eu:
            m.eu = "r%d" % oid
    por_oid = {}
    for oid, nome, linhas, particoes in conexao.execute("""
            SELECT c.oid, c.relname, c.reltuples::bigint,
                   CASE WHEN c.relkind = 'p' THEN (SELECT count(*) FROM pg_inherits i WHERE i.inhparent = c.oid) ELSE 0 END
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'f') AND NOT c.relispartition
            ORDER BY c.relname""", (esquema,)):
        t = _modelo.Tabela("t%d" % oid, esquema, nome, [], linhas if linhas is not None and linhas >= 0 else None,
                           int(particoes))
        m.tabelas[t.id] = t
        por_oid[oid] = t
    if not por_oid:
        return m
    oids = list(por_oid)
    for oid, num, nome, tipo, nn, padrao, ident, gerada, sempre in conexao.execute("""
            SELECT a.attrelid, a.attnum, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
                   pg_get_expr(d.adbin, d.adrelid), a.attidentity <> '', a.attgenerated <> '', a.attidentity <> 'd'
            FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE a.attrelid = ANY (%s) AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attrelid, a.attnum""", (oids,)):
        por_oid[oid].colunas.append(_modelo.Coluna("t%d.c%d" % (oid, num), nome, tipo, nn, padrao, ident,
                                                   gerada=gerada, identidade_sempre=sempre))
    colunas_por_id = {c.id: c for t in por_oid.values() for c in t.colunas}
    for oid, coid, nome, tipo, conkey, confrelid, confkey, apagar in conexao.execute("""
            SELECT conrelid, oid, conname, contype, conkey, confrelid, confkey, confdeltype
            FROM pg_constraint WHERE conrelid = ANY (%s) AND contype IN ('p', 'u', 'f')
            ORDER BY oid""", (oids,)):
        ids = tuple("t%d.c%d" % (oid, n) for n in conkey)
        if tipo == "p":
            por_oid[oid].nome_pk = nome
            for i in ids:
                colunas_por_id[i].pk = True
        elif tipo == "u" and len(ids) == 1:
            colunas_por_id[ids[0]].unica = True
            colunas_por_id[ids[0]].nome_unica = nome
        elif tipo == "f" and confrelid in por_oid:
            lig = _modelo.Ligacao("k%d" % coid, "t%d" % oid, ids, "t%d" % confrelid,
                                  tuple("t%d.c%d" % (confrelid, n) for n in confkey), nome,
                                  _AO_APAGAR.get(apagar, "NO ACTION"))
            m.ligacoes[lig.id] = lig
    # um índice único simples (sem WHERE, sem expressão) também garante unicidade, mesmo sem restrição:
    # a coluna é "unica" (sem nome_unica, não há restrição para apagar)
    for oid, num, unico in conexao.execute("""
            SELECT indrelid, indkey[0],
                   indisunique AND NOT indisprimary AND indpred IS NULL AND indexprs IS NULL AND indnkeyatts = 1
            FROM pg_index WHERE indrelid = ANY (%s) AND indkey[0] > 0""", (oids,)):
        c = colunas_por_id["t%d.c%d" % (oid, num)]
        c.indexada = True
        if unico:
            c.unica = True
    return m


@dataclass
class Coluna:
    numero: int
    nome: str
    tipo: str
    obrigatoria: bool
    padrao: str
    chave: bool
    aponta: str             # "cursos.id" se é chave estrangeira


def colunas(conexao, oid):
    return [Coluna(*linha) for linha in conexao.execute("""
        SELECT a.attnum, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid),
               EXISTS (SELECT 1 FROM pg_constraint k
                       WHERE k.conrelid = a.attrelid AND k.contype = 'p' AND a.attnum = ANY (k.conkey)),
               (SELECT k.confrelid::regclass || '.' || f.attname
                  FROM pg_constraint k
                  JOIN pg_attribute f ON f.attrelid = k.confrelid
                                     AND f.attnum = k.confkey[array_position(k.conkey, a.attnum)]
                 WHERE k.conrelid = a.attrelid AND k.contype = 'f' AND a.attnum = ANY (k.conkey)
                 LIMIT 1)
        FROM pg_attribute a
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum""", (oid,)).fetchall()]


def indices(conexao, oid):
    """[(nome, definição)] — o CREATE INDEX de cada um, como o PostgreSQL escreve."""
    return conexao.execute("""
        SELECT i.relname, pg_get_indexdef(x.indexrelid)
        FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
        WHERE x.indrelid = %s ORDER BY NOT x.indisprimary, i.relname""", (oid,)).fetchall()


def restricoes(conexao, oid):
    """[(nome, tipo, definição)] — chave primária, única, estrangeira, check."""
    return conexao.execute("""
        SELECT conname, contype, pg_get_constraintdef(oid)
        FROM pg_constraint WHERE conrelid = %s
        ORDER BY array_position(ARRAY['p', 'u', 'f', 'c', 'x']::"char"[], contype), conname""", (oid,)).fetchall()


def relacoes(conexao, oid):
    """(saem, chegam): as chaves estrangeiras desta tabela para outras, e das outras para esta.
    Cada uma é (tabela, colunas, tabela_alvo, colunas_alvo)."""
    linhas = conexao.execute("""
        SELECT k.conrelid, k.conrelid::regclass::text,
               (SELECT string_agg(a.attname, ', ' ORDER BY u.ord) FROM unnest(k.conkey) WITH ORDINALITY u(n, ord)
                  JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.n),
               k.confrelid::regclass::text,
               (SELECT string_agg(a.attname, ', ' ORDER BY u.ord) FROM unnest(k.confkey) WITH ORDINALITY u(n, ord)
                  JOIN pg_attribute a ON a.attrelid = k.confrelid AND a.attnum = u.n)
        FROM pg_constraint k
        WHERE k.contype = 'f' AND (k.conrelid = %s OR k.confrelid = %s)
        ORDER BY 2, 4""", (oid, oid)).fetchall()
    saem = [tuple(l[1:]) for l in linhas if l[0] == oid]
    chegam = [tuple(l[1:]) for l in linhas if l[0] != oid]
    return saem, chegam


def contar(conexao, objeto):
    from psycopg import sql
    return conexao.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(
        sql.Identifier(objeto.esquema), sql.Identifier(objeto.nome))).fetchone()[0]


def tamanho(conexao, oid):
    return conexao.execute("SELECT pg_total_relation_size(%s)", (oid,)).fetchone()[0]


def dados(conexao, objeto, deslocamento=0, limite=200):
    """(colunas, tipos, linhas) de uma página da tabela ou view, na ordem da chave primária."""
    from psycopg import sql
    chave = [c.nome for c in colunas(conexao, objeto.oid) if c.chave] if objeto.tipo == "tabela" else []
    ordem = sql.SQL(" ORDER BY {}").format(sql.SQL(", ").join(map(sql.Identifier, chave))) if chave else sql.SQL("")
    consulta = sql.SQL("SELECT * FROM {}.{}{} LIMIT %s OFFSET %s").format(
        sql.Identifier(objeto.esquema), sql.Identifier(objeto.nome), ordem)
    with conexao.cursor() as cur:
        cur.execute(consulta, (limite, deslocamento))
        nomes = [d.name for d in cur.description]
        tipos = [getattr(conexao.adapters.types.get(d.type_code), "name", str(d.type_code)) for d in cur.description]
        return nomes, tipos, cur.fetchall()


def ddl(conexao, objeto):
    """O SQL que recria o objeto. Tabela e sequência pelo pg_dump (o mesmo que um backup escreveria);
    view e função direto do catálogo."""
    if objeto.tipo == "view":
        definicao = conexao.execute("SELECT pg_get_viewdef(%s, true)", (objeto.oid,)).fetchone()[0]
        return f"CREATE VIEW {objeto.esquema}.{objeto.nome} AS\n{definicao}"
    if objeto.tipo == "funcao":
        return conexao.execute("SELECT pg_get_functiondef(%s)", (objeto.oid,)).fetchone()[0]
    texto = _pg_dump(conexao, objeto)
    return texto if texto else _ddl_simples(conexao, objeto)


def _pg_dump(conexao, objeto):
    if not shutil.which("pg_dump"):
        return None
    info = conexao.info
    cmd = ["pg_dump", "--schema-only", "--no-owner", "--no-privileges", "-h", info.host, "-p", str(info.port),
           "-U", info.user, "-t", f'"{objeto.esquema}"."{objeto.nome}"', info.dbname]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    # fora o cabeçalho do pg_dump (SET …, comentários): fica só o que recria o objeto
    linhas = [l for l in p.stdout.splitlines()
              if l.strip() and not l.startswith(("SET ", "SELECT pg_catalog.set_config", "--", "\\"))]
    return "\n".join(linhas).replace(";\n", ";\n\n").strip()


def _ddl_simples(conexao, objeto):
    """Quando não há pg_dump: o CREATE TABLE montado das colunas e restrições."""
    partes = []
    for col in colunas(conexao, objeto.oid):
        linha = f"    {col.nome} {col.tipo}"
        if col.padrao:
            linha += f" DEFAULT {col.padrao}"
        if col.obrigatoria:
            linha += " NOT NULL"
        partes.append(linha)
    partes += [f"    CONSTRAINT {nome} {definicao}" for nome, _, definicao in restricoes(conexao, objeto.oid)]
    return f"CREATE TABLE {objeto.esquema}.{objeto.nome} (\n" + ",\n".join(partes) + "\n);"


# ── a tela Dados: tudo somente leitura ──

class LeituraInsegura(RuntimeError):
    """A transação não ficou somente leitura: a consulta não roda (nunca cai numa transação que escreve)."""


@contextmanager
def somente_leitura(conexao, limite_ms=None):
    """Uma transação `READ ONLY` (com `statement_timeout` local, se pedido) que sempre termina em ROLLBACK.
    Numa conexão já dentro de uma transação vira um savepoint: o READ ONLY vale nele e, desfeito o savepoint,
    nada (nem o READ ONLY, nem o tempo limite) vaza para a transação de quem chamou."""
    with conexao.transaction(force_rollback=True):
        conexao.execute("SET TRANSACTION READ ONLY")
        if limite_ms is None:
            estado = conexao.execute("SELECT current_setting('transaction_read_only')").fetchone()[0]
        else:
            estado = conexao.execute("SELECT current_setting('transaction_read_only'), "
                                     "set_config('statement_timeout', %s, true)", (str(int(limite_ms)),)).fetchone()[0]
        if estado != "on":
            from ratt.db.idioma import t
            raise LeituraInsegura(t("chk.leitura_insegura"))
        yield conexao


def onde_sql(onde):
    """` WHERE <onde>` como SQL cru ("" sem filtro). O texto vem de `filtro.where` (valores já citados) ou é o
    WHERE que a pessoa escreveu. Quem o usa roda a consulta com `executar`: sem parâmetros (um `%` do LIKE não vira
    marcador) e pelo protocolo estendido (um `; COMMIT; DELETE …` escondido no WHERE é recusado, não executado)."""
    from psycopg import sql
    return sql.SQL(" WHERE ") + sql.SQL(onde) if onde else sql.SQL("")


def executar(alvo, consulta, params=None):
    """`execute` sempre pelo protocolo estendido (`prepare=True`): ele aceita um comando só. O protocolo simples,
    que o psycopg usa quando não há parâmetros, rodaria vários — e um `COMMIT` no meio sairia da transação
    somente leitura. `alvo` é a conexão ou um cursor."""
    return alvo.execute(consulta, params, prepare=True)


def pagina(conexao, esquema, tabela, onde, ordem, deslocamento, limite, limite_ms=5000):
    """(colunas, tipos, linhas) de uma página das linhas filtradas, em transação somente leitura e com tempo limite
    (`QueryCanceled` se passar). `ordem` são as colunas da chave primária; vazia, a página sai sem ORDER BY (a
    ordem não é estável)."""
    from psycopg import sql
    por = sql.SQL(" ORDER BY {}").format(sql.SQL(", ").join(map(sql.Identifier, ordem))) if ordem else sql.SQL("")
    consulta = sql.SQL("SELECT * FROM {}{}{} LIMIT {} OFFSET {}").format(
        sql.Identifier(esquema, tabela), onde_sql(onde), por, sql.Literal(int(limite)), sql.Literal(int(deslocamento)))
    with somente_leitura(conexao, limite_ms):
        with conexao.cursor() as cur:
            executar(cur, consulta)
            nomes = [d.name for d in cur.description]
            tipos = [getattr(conexao.adapters.types.get(d.type_code), "name", str(d.type_code)) for d in cur.description]
            return nomes, tipos, cur.fetchall()


def contar_filtrado(conexao, esquema, tabela, onde, limite_ms=2000):
    """Quantas linhas o filtro pega; None se o tempo acabar (tabela grande demais para contar de graça)."""
    import psycopg
    from psycopg import sql
    consulta = sql.SQL("SELECT count(*) FROM {}{}").format(sql.Identifier(esquema, tabela), onde_sql(onde))
    try:
        with somente_leitura(conexao, limite_ms):
            return executar(conexao, consulta).fetchone()[0]
    except psycopg.errors.QueryCanceled:
        return None


def linhas_pai(conexao, esquema, tabela, chave, busca, limite=50, limite_ms=2000):
    """[(valor da chave, rótulo)] das linhas de uma tabela pai, para escolher o valor de uma chave estrangeira; None
    se o tempo acabar. O rótulo são as duas primeiras colunas de texto (fora a chave), ou a própria chave se não
    houver; `busca` procura (ILIKE, com `%`, `_` e `\\` tomados ao pé da letra) em toda coluna de texto e na chave."""
    import psycopg
    from psycopg import sql
    from ratt.db import filtro
    try:
        with somente_leitura(conexao, limite_ms):
            tipos = conexao.execute("""
                SELECT a.attname, format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum""", (esquema, tabela)).fetchall()
            textos = [sql.Identifier(nome) for nome, tipo in tipos if nome != chave and filtro.familia(tipo) == "texto"]
            k = sql.Identifier(chave)
            como_texto = sql.SQL("{}::text").format(k)
            rotulo = como_texto
            if textos:
                rotulo = sql.SQL("coalesce(nullif(concat_ws(' · ', {}), ''), {})").format(
                    sql.SQL(", ").join(textos[:2]), como_texto)
            onde = sql.SQL("")
            if busca.strip():
                padrao = sql.SQL(filtro.padrao_like(busca, "contem"))
                onde = sql.SQL(" WHERE ") + sql.SQL(" OR ").join(
                    sql.SQL("{} ILIKE {}").format(campo, padrao) for campo in textos + [como_texto])
            consulta = sql.SQL("SELECT {} AS _ratt_valor, {} AS _ratt_rotulo FROM {}{} ORDER BY 1 LIMIT {}").format(
                k, rotulo, sql.Identifier(esquema, tabela), onde, sql.Literal(int(limite)))
            return [tuple(linha) for linha in executar(conexao, consulta)]
    except psycopg.errors.QueryCanceled:
        return None


def escritas_da_tabela(conexao, oid, limite_ms=2000):
    """Linhas inseridas, alteradas e apagadas que o PostgreSQL contou nesta tabela — nas partições dela, se é
    particionada; nas tabelas que ela lê, se é uma view. Sobe quando alguém escreve ali (a tela Dados relê).
    Somente leitura e com tempo limite, como as outras leituras da tela (`QueryCanceled` se passar)."""
    with somente_leitura(conexao, limite_ms):
        return executar(conexao, """
        SELECT coalesce(sum(s.n_tup_ins + s.n_tup_upd + s.n_tup_del), 0) FROM pg_stat_user_tables s
        WHERE s.relid = %(o)s
           OR s.relid IN (SELECT p.relid FROM pg_partition_tree(%(o)s::oid::regclass) p)
           OR s.relid IN (SELECT d.refobjid FROM pg_rewrite w JOIN pg_depend d ON d.objid = w.oid
                          WHERE w.ev_class = %(o)s AND d.classid = 'pg_rewrite'::regclass
                            AND d.refclassid = 'pg_class'::regclass)""", {"o": oid}).fetchone()[0]


# ── permissões de tabela e de schema: somente leitura ──

# as letras do `aclitem` que a grade oferece, na ordem em que o GRANT as escreve (o MAINTAIN do 17+, `m`, fica de fora)
PRIVILEGIOS = {"SELECT": "S", "INSERT": "I", "UPDATE": "U", "DELETE": "D", "TRUNCATE": "T", "REFERENCES": "R",
               "TRIGGER": "t"}
_NO_SCHEMA = ("USAGE", "CREATE")


@dataclass
class Permissoes:
    """As permissões de um schema como o catálogo as tem. Papel é `r{oid}` ou "PUBLIC"; tabela é o id do modelo.
    `por_tabela`: (papel, tabela) → letras (`S I U D T R t`), só quem tem alguma. `dono`: tabela → papel dono.
    `schema`: papel → {"USAGE", "CREATE"} no schema. `padrao`: as permissões padrão (`ALTER DEFAULT PRIVILEGES FOR
    ROLE <a pessoa> IN SCHEMA …`) em tabelas — papel que recebe → letras."""
    por_tabela: dict
    dono: dict
    schema: dict
    padrao: dict


def permissoes(conexao, esquema, limite_ms=5000):
    """As permissões do schema numa consulta só (somente leitura, com tempo limite): as das tabelas (comuns e
    particionadas, sem as partições — as que o canvas mostra) pelo `relacl` (NULL: o padrão, `acldefault`), as do
    schema pelo `nspacl` e as permissões padrão da pessoa (`pg_default_acl` deste schema, em tabelas)."""
    with somente_leitura(conexao, limite_ms):
        linhas = executar(conexao, """
            WITH esq AS (SELECT oid, nspowner, nspacl FROM pg_namespace WHERE nspname = %(e)s)
            SELECT 't', c.oid, c.relowner, a.grantee, a.privilege_type
            FROM pg_class c JOIN esq ON esq.oid = c.relnamespace
            LEFT JOIN LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a ON true
            WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition
            UNION ALL
            SELECT 'n', esq.oid, esq.nspowner, a.grantee, a.privilege_type
            FROM esq, aclexplode(coalesce(esq.nspacl, acldefault('n', esq.nspowner))) a
            UNION ALL
            SELECT 'd', d.oid, d.defaclrole, a.grantee, a.privilege_type
            FROM pg_default_acl d JOIN esq ON esq.oid = d.defaclnamespace, aclexplode(d.defaclacl) a
            WHERE d.defaclobjtype = 'r' AND d.defaclrole = (SELECT oid FROM pg_roles WHERE rolname = current_user)
            """, {"e": esquema}).fetchall()
    por_tabela, dono, schema, padrao = {}, {}, {}, {}
    for tipo, oid, quem, recebe, privilegio in linhas:
        papel = None if recebe is None else ("PUBLIC" if recebe == 0 else "r%d" % recebe)
        if tipo == "t":
            dono["t%d" % oid] = "r%d" % quem
            if papel is not None and privilegio in PRIVILEGIOS:
                chave = (papel, "t%d" % oid)
                por_tabela[chave] = por_tabela.get(chave, frozenset()) | {PRIVILEGIOS[privilegio]}
        elif tipo == "n" and privilegio in _NO_SCHEMA:
            schema[papel] = schema.get(papel, frozenset()) | {privilegio}
        elif tipo == "d" and privilegio in PRIVILEGIOS:
            padrao[papel] = padrao.get(papel, frozenset()) | {PRIVILEGIOS[privilegio]}
    return Permissoes(por_tabela, dono, schema, padrao)


def versao(conexao):
    """O número da versão do servidor (160004 é o 16.4)."""
    return conexao.info.server_version
