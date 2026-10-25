"""O que existe dentro de um banco, lido do catálogo do PostgreSQL — o que a TUI desenha.

Cada função recebe uma conexão aberta e devolve dados simples (tuplas, dicionários, texto):
quem desenha não precisa saber SQL, e quem consulta não precisa saber desenhar.
"""
import shutil
import subprocess
from dataclasses import dataclass, field

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


def assinatura(conexao):
    """Um resumo de tudo o que importa para a tela: objetos, colunas e as escritas em cada tabela.
    Se mudou entre uma olhada e outra, alguém (um ratt db run, o psql) mexeu no banco."""
    return conexao.execute(f"""
        SELECT
          (SELECT md5(coalesce(string_agg(c.oid || c.relname || c.relkind::text, ',' ORDER BY c.oid), ''))
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'f', 'v', 'm', 'S') AND {_SEM_SISTEMA}),
          (SELECT md5(coalesce(string_agg(a.attrelid || a.attname || a.atttypid::text, ',' ORDER BY a.attrelid, a.attnum), ''))
             FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'p', 'v', 'm') AND {_SEM_SISTEMA}),
          (SELECT md5(coalesce(string_agg(p.oid::text, ',' ORDER BY p.oid), ''))
             FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE {_SEM_SISTEMA}),
          (SELECT coalesce(sum(n_tup_ins + n_tup_upd + n_tup_del), 0) FROM pg_stat_user_tables)""").fetchone()


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
