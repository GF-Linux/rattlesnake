"""O filtro visual — coluna → transformação → operador → valor, juntos por E/OU — vira uma expressão `WHERE`.

Puro: sem tela e sem banco. Todo valor digitado sai por `literal` (citado, nunca colado), todo nome por
`rascunho.ident`; o que a pessoa escreve nunca vira SQL por outro caminho — salvo o modo texto livre, que passa
por `validar_texto` antes. Qualquer coisa que não dê para montar é `ValueError` com mensagem em `filtro.e.*`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from . import rodar
from .idioma import t
from .modelo import Coluna, Modelo, Tabela
from .rascunho import ident, nome_tabela

# ── tipos ──

_SINONIMOS = {"int8": "bigint", "int4": "integer", "int": "integer", "int2": "smallint",
              "serial": "integer", "serial4": "integer", "bigserial": "bigint", "serial8": "bigint",
              "smallserial": "smallint", "serial2": "smallint", "decimal": "numeric",
              "float8": "double precision", "float": "double precision", "float4": "real",
              "varchar": "character varying", "char": "character", "bpchar": "character",
              "timestamptz": "timestamp with time zone", "timestamp": "timestamp without time zone",
              "timetz": "time with time zone", "time": "time without time zone", "bool": "boolean"}

# Famílias finas (as da checagem): tipos diferentes de uma mesma família o PostgreSQL costuma comparar.
_FAMILIAS = {
    "numero": {"smallint", "integer", "bigint", "numeric", "real", "double precision", "money"},
    "texto": {"text", "character varying", "character", "name", "citext"},
    "tempo": {"date", "timestamp without time zone", "timestamp with time zone", "time without time zone",
              "time with time zone", "interval"},
    "booleano": {"boolean"},
    "uuid": {"uuid"},
    "json": {"json", "jsonb"},
}
_DATAS = {"date", "timestamp without time zone", "timestamp with time zone"}


def tipo_normal(tipo: str) -> str:
    """O tipo sem modificador `(255)`, com os espaços normalizados e os apelidos do PostgreSQL unidos."""
    base = " ".join(re.sub(r"\(.*?\)", "", tipo).lower().split())
    return _SINONIMOS.get(base, base)


def familia_fina(tipo: str) -> str:
    """A família como a checagem a vê (numero, texto, tempo, booleano, uuid, json; listas; o resto é uma só dele)."""
    normal = tipo_normal(tipo)
    if normal.endswith("[]"):
        return "lista de " + familia_fina(normal[:-2])
    return next((nome for nome, tipos in _FAMILIAS.items() if normal in tipos), normal)


def familia(tipo: str) -> str:
    """"texto", "numero", "data" (date e timestamps), "booleano" ou "outro" (uuid, json, enum, listas, hora...)."""
    fina = familia_fina(tipo)
    if fina in ("texto", "numero", "booleano"):
        return fina
    return "data" if tipo_normal(tipo) in _DATAS else "outro"


# ── a gramática ──

@dataclass
class Condicao:
    coluna: str                                 # id de coluna; "" quando `existe` está preenchido
    operador: str
    valores: List[str] = field(default_factory=list)
    transformacao: str = ""
    negar: bool = False
    diferenciar: bool = False                   # contem/comeca/termina: LIKE em vez de ILIKE
    outra_coluna: Optional[str] = None          # compara com esta coluna em vez de um valor
    existe: Optional["Existe"] = None


@dataclass
class Existe:
    ligacao: str                                # id da Ligacao que sai ou chega na tabela
    condicoes: List[Condicao] = field(default_factory=list)     # sobre a OUTRA tabela
    nao: bool = False


@dataclass
class Filtro:
    condicoes: List[Condicao] = field(default_factory=list)
    conectivo: str = "E"                        # "E" ou "OU"
    texto: Optional[str] = None                 # modo texto livre: vale no lugar das condições

    def vazio(self) -> bool:
        if self.texto is not None and self.texto.strip():
            return False
        return not self.condicoes


_BASE = ["igual", "diferente", "vazio", "preenchido", "na_lista", "fora_lista"]
_EXTRA = {
    "texto": ["contem", "comeca", "termina", "igual_sem_caixa"],
    "numero": ["menor", "menor_igual", "maior", "maior_igual", "entre"],
    "data": ["antes", "depois", "entre", "ha_mais_de", "nos_ultimos"],
    "outro": [],
}
_NUMERICAS = {"tamanho", "mes", "ano", "dia_semana", "dia"}
_FUNCOES = {"minusculas": "lower({})", "trim": "trim({})", "tamanho": "length({})",
            "mes": "extract(month FROM {})", "ano": "extract(year FROM {})",
            "dia_semana": "extract(isodow FROM {})", "dia": "extract(day FROM {})", "so_data": "{}::date"}
_COMPARACOES = {"igual": "=", "diferente": "<>", "menor": "<", "menor_igual": "<=", "maior": ">",
                "maior_igual": ">=", "antes": "<", "depois": ">"}
_UNIDADES = {"dias": "days", "meses": "months", "anos": "years"}


def transformacoes(tipo: str) -> List[str]:
    fam = familia(tipo)
    if fam == "texto":
        return ["minusculas", "trim", "tamanho"]
    if fam == "data":
        return ["mes", "ano", "dia_semana", "dia"] + (["so_data"] if tipo_normal(tipo).startswith("timestamp") else [])
    return []


def _familia_depois(tipo: str, transformacao: str) -> str:
    if transformacao in _NUMERICAS:
        return "numero"
    if transformacao == "so_data":
        return "data"
    if transformacao in ("minusculas", "trim"):
        return "texto"
    return familia(tipo)


def operadores(tipo: str, transformacao: str = "") -> List[str]:
    fam = _familia_depois(tipo, transformacao)
    if fam == "booleano":
        return ["verdadeiro", "falso", "vazio", "preenchido"]
    return _BASE + _EXTRA[fam]


def _tipo_efetivo(tipo: str, transformacao: str) -> str:
    if transformacao in _NUMERICAS:
        return "integer"
    return {"so_data": "date", "minusculas": "text", "trim": "text"}.get(transformacao, tipo)


# ── valores ──

_NUMERO = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_SIM = {"sim", "true", "t", "1", "s", "yes", "y"}
_NAO = {"não", "nao", "false", "f", "0", "n", "no"}


def _citar(texto: str) -> str:
    if "\x00" in texto:
        raise ValueError(t("filtro.e.nul"))
    corpo = texto.replace("'", "''")
    if "\\" in texto:                  # E'…' lê a barra igual com standard_conforming_strings ligado ou não
        return "E'" + corpo.replace("\\", "\\\\") + "'"
    return "'" + corpo + "'"


def literal(texto: str, tipo: str) -> str:
    """O valor como literal SQL do tipo: número sem aspas, booleano TRUE/FALSE, o resto citado."""
    fam = familia(tipo)
    if fam == "numero":
        limpo = texto.strip()
        if not _NUMERO.fullmatch(limpo):
            raise ValueError(t("filtro.e.numero", valor=texto))
        return limpo
    if fam == "booleano":
        palavra = texto.strip().lower()
        if palavra in _SIM:
            return "TRUE"
        if palavra in _NAO:
            return "FALSE"
        raise ValueError(t("filtro.e.booleano", valor=texto))
    return _citar(texto)


def padrao_like(texto: str, onde: str) -> str:
    """`'%x%'` (contem), `'x%'` (comeca) ou `'%x'` (termina), com `\\`, `%` e `_` escapados (a barra é o escape do LIKE)."""
    if onde not in ("contem", "comeca", "termina"):
        raise ValueError(t("filtro.e.operador", operador=onde, tipo="text"))
    corpo = texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return _citar(("%" if onde in ("contem", "termina") else "") + corpo + ("%" if onde in ("contem", "comeca") else ""))


def _inteiro_em(valor: str, minimo: int, maximo: int, chave: str) -> None:
    limpo = valor.strip()
    if not re.fullmatch(r"[0-9]+", limpo) or not minimo <= int(limpo) <= maximo:
        raise ValueError(t(chave, valor=valor))


# ── o WHERE ──

def _coluna(tab: Tabela, id: str) -> Coluna:
    col = tab.coluna(id) if id else None
    if col is None:
        raise ValueError(t("filtro.e.coluna", coluna=id))
    return col


def _ref(col: Coluna, qual: Optional[str]) -> str:
    return (qual + "." if qual else "") + ident(col.nome)


def _valores(c: Condicao, n: Optional[int] = None) -> List[str]:
    v = c.valores
    if (n is not None and len(v) != n) or (n is None and not v):
        raise ValueError(t("filtro.e.valores", operador=c.operador))
    return v


def _condicao(c: Condicao, m: Modelo, tab: Tabela, qual: Optional[str], nivel: int) -> Tuple[str, bool]:
    """(SQL, já vem entre parênteses)."""
    if c.existe is not None:
        sql, proprio = _existe(c.existe, m, tab, qual, nivel), False
    else:
        sql, proprio = _comum(c, tab, qual)
    if c.negar:
        sql, proprio = ("NOT " + sql if proprio else "NOT (" + sql + ")"), False
    return sql, proprio


def _comum(c: Condicao, tab: Tabela, qual: Optional[str]) -> Tuple[str, bool]:
    col = _coluna(tab, c.coluna)
    if c.transformacao and c.transformacao not in transformacoes(col.tipo):
        raise ValueError(t("filtro.e.transformacao", transformacao=c.transformacao, tipo=col.tipo))
    if c.operador not in operadores(col.tipo, c.transformacao):
        raise ValueError(t("filtro.e.operador", operador=c.operador, tipo=col.tipo))
    expr = _ref(col, qual)
    if c.transformacao:
        expr = _FUNCOES[c.transformacao].format(expr)
    tipo = _tipo_efetivo(col.tipo, c.transformacao)
    fam = familia(tipo)
    op = c.operador

    def lit(v):
        if c.transformacao == "mes":
            _inteiro_em(v, 1, 12, "filtro.e.mes")
        elif c.transformacao == "dia_semana":
            _inteiro_em(v, 1, 7, "filtro.e.dia_semana")
        elif c.transformacao == "dia":
            _inteiro_em(v, 1, 31, "filtro.e.dia")
        return literal(v, tipo)

    if c.outra_coluna is not None:
        if op not in _COMPARACOES and op != "igual_sem_caixa":
            raise ValueError(t("filtro.e.pad"))
        outra = _ref(_coluna(tab, c.outra_coluna), qual)
        if op == "igual_sem_caixa":
            return f"lower({expr}) = lower({outra})", False
        return f"{expr} {_COMPARACOES[op]} {outra}", False
    if op in ("vazio", "preenchido"):
        if fam == "texto":
            return ((f"({expr} IS NULL OR trim({expr}) = '')" if op == "vazio"
                     else f"({expr} IS NOT NULL AND trim({expr}) <> '')"), True)
        return f"{expr} IS {'NULL' if op == 'vazio' else 'NOT NULL'}", False
    if op in ("verdadeiro", "falso"):
        return f"{expr} IS {'TRUE' if op == 'verdadeiro' else 'FALSE'}", False
    if op == "na_lista" and "" in _valores(c):
        # o vazio (∅) na lista: `IN (…, NULL)` nunca casa com NULL — as linhas vazias entram por um OU à parte
        vazio = f"{expr} IS NULL" + (f" OR trim({expr}) = ''" if fam == "texto" else "")
        itens = ", ".join(lit(v) for v in c.valores if v != "")
        if not itens:
            return (f"({vazio})", True) if fam == "texto" else (vazio, False)
        return f"({expr} IN ({itens}) OR {vazio})", True
    if op in ("na_lista", "fora_lista"):
        # "" na lista é o vazio (NULL); em "não está na lista" fica a armadilha que se ensina: NOT IN (…, NULL) não
        # pega nada (o aviso chk.fora_lista_vazio diz isso)
        itens = ", ".join("NULL" if v == "" else lit(v) for v in _valores(c))
        return f"{expr} {'IN' if op == 'na_lista' else 'NOT IN'} ({itens})", False
    if op in ("contem", "comeca", "termina"):
        _valores(c, 1)
        return f"{expr} {'LIKE' if c.diferenciar else 'ILIKE'} {padrao_like(c.valores[0], op)}", False
    if op == "igual_sem_caixa":
        _valores(c, 1)
        return f"lower({expr}) = lower({lit(c.valores[0])})", False
    if op == "entre":
        a, b = _valores(c, 2)
        return f"{expr} BETWEEN {lit(a)} AND {lit(b)}", False
    if op in ("ha_mais_de", "nos_ultimos"):
        n, unidade = _valores(c, 2)
        if not re.fullmatch(r"[0-9]+", n.strip()) or int(n) <= 0:
            raise ValueError(t("filtro.e.intervalo", valor=n))
        if unidade not in _UNIDADES:
            raise ValueError(t("filtro.e.unidade", unidade=unidade))
        return (f"{expr} {'<' if op == 'ha_mais_de' else '>='} now() - interval '{int(n)} {_UNIDADES[unidade]}'"), False
    _valores(c, 1)                                                # igual, diferente, menor..., antes, depois
    return f"{expr} {_COMPARACOES[op]} {lit(c.valores[0])}", False


def _existe(e: Existe, m: Modelo, tab: Tabela, qual: Optional[str], nivel: int) -> str:
    lig = m.ligacoes.get(e.ligacao)
    if lig is None or lig.estado == "apagado" or tab.id not in (lig.origem, lig.destino):
        raise ValueError(t("filtro.e.ligacao", ligacao=e.ligacao))
    if tab.id == lig.origem:
        outra_id, minhas, delas = lig.destino, lig.colunas, lig.colunas_destino
    else:
        outra_id, minhas, delas = lig.origem, lig.colunas_destino, lig.colunas
    outra = m.tabelas.get(outra_id)
    if outra is None:
        raise ValueError(t("filtro.e.ligacao", ligacao=e.ligacao))
    meu_nome = qual or nome_tabela(tab)
    if outra.id == tab.id:                                         # ligação para a própria tabela: precisa de apelido
        alias = f"_o{nivel + 1}"
        de, nome_outra = f"{nome_tabela(outra)} AS {alias}", alias
    else:
        de = nome_outra = nome_tabela(outra)
    juncao = [f"{_ref(_coluna(outra, d), nome_outra)} = {_ref(_coluna(tab, mi), meu_nome)}"
              for mi, d in zip(minhas, delas)]
    resto = _juntar(e.condicoes, "E", m, outra, nome_outra, nivel + 1)
    sql = f"SELECT 1 FROM {de} WHERE " + " AND ".join(juncao) + (" AND " + resto if resto else "")
    return ("NOT EXISTS (" if e.nao else "EXISTS (") + sql + ")"


def _juntar(condicoes: List[Condicao], conectivo: str, m: Modelo, tab: Tabela, qual: Optional[str], nivel: int) -> str:
    if conectivo not in ("E", "OU"):
        raise ValueError(t("filtro.e.conectivo", conectivo=conectivo))
    partes = [_condicao(c, m, tab, qual, nivel) for c in condicoes]
    if len(partes) == 1:
        return partes[0][0]
    return (" AND " if conectivo == "E" else " OR ").join(sql if proprio else f"({sql})" for sql, proprio in partes)


def where(f: Filtro, m: Modelo, tabela_id: str) -> str:
    """A expressão do WHERE (sem a palavra); "" para o filtro vazio; o texto livre sai entre parênteses."""
    tab = m.tabelas[tabela_id]
    if f.texto is not None and f.texto.strip():
        validar_texto(f.texto)
        return "(" + f.texto.strip() + ")"
    return _juntar(f.condicoes, f.conectivo, m, tab, None, 0)


# ── texto livre ──

_DOLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


def validar_texto(texto: str) -> None:
    """O texto livre é uma expressão só: sem `;` nem comentário fora de aspas, aspas e parênteses fechados, e sem
    barra invertida numa string comum `'…'` (E-strings e `$$…$$` têm regras próprias e valem)."""
    if not texto.strip():
        raise ValueError(t("filtro.e.texto_vazio"))
    i, n, nivel = 0, len(texto), 0
    while i < n:
        ch = texto[i]
        if ch == ";":
            raise ValueError(t("filtro.e.texto_ponto_virgula"))
        if texto.startswith("--", i) or texto.startswith("/*", i):
            raise ValueError(t("filtro.e.texto_comentario"))
        if ch in "'\"":
            escapa = ch == "'" and i > 0 and texto[i - 1] in "eE" and (i < 2 or not rodar.caractere_de_nome(texto[i - 2]))
            i += 1
            while True:
                if i >= n:
                    raise ValueError(t("filtro.e.texto_aspa"))
                if escapa and texto[i] == "\\":
                    i += 2
                elif ch == "'" and texto[i] == "\\":
                    # numa string comum o sentido da barra depende de standard_conforming_strings: o validador e o
                    # servidor poderiam ver aspas diferentes. Quem precisa de barra usa E'…' ou $$…$$.
                    raise ValueError(t("filtro.e.texto_barra"))
                elif texto[i] == ch:
                    if texto.startswith(ch * 2, i):
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
            continue
        if ch == "$" and (i == 0 or not rodar.caractere_de_nome(texto[i - 1])):
            marca = _DOLAR.match(texto, i)
            if marca is not None:
                fim = texto.find(marca.group(0), i + len(marca.group(0)))
                if fim < 0:
                    raise ValueError(t("filtro.e.texto_aspa"))
                i = fim + len(marca.group(0))
                continue
        if ch == "(":
            nivel += 1
        elif ch == ")":
            nivel -= 1
            if nivel < 0:
                raise ValueError(t("filtro.e.texto_parenteses"))
        i += 1
    if nivel != 0:
        raise ValueError(t("filtro.e.texto_parenteses"))
    comandos = rodar.dividir(f"SELECT 1 WHERE {texto};")
    if len(comandos) != 1 or comandos[0].meta or comandos[0].texto != f"SELECT 1 WHERE {texto}".strip():
        raise ValueError(t("filtro.e.texto_varios"))


# ── avisos ──

def _todas(condicoes: List[Condicao]):
    for c in condicoes:
        yield c
        if c.existe is not None:
            yield from _todas(c.existe.condicoes)


def deixa_vazios_de_fora(f: Filtro, m: Modelo, tabela_id: str) -> List[str]:
    """Ids das colunas anuláveis cujas linhas vazias o filtro tira sem a pessoa pedir (NULL não casa com `<>`, `NOT`...)."""
    if f.texto is not None and f.texto.strip():
        return []
    tab = m.tabelas[tabela_id]
    achados: List[str] = []
    for c in f.condicoes:
        if c.existe is not None:
            continue
        pede_vazio = c.operador == "na_lista" and "" in c.valores        # o vazio está na lista: sai só se pedir
        ids = [] if c.operador in ("vazio", "preenchido", "verdadeiro", "falso") or pede_vazio else [c.coluna]
        if c.outra_coluna is not None:
            ids.append(c.outra_coluna)
        for id in ids:
            col = tab.coluna(id)
            if col is not None and not col.obrigatoria and col.id not in achados:
                achados.append(col.id)
    return achados


def fora_lista_com_vazio(f: Filtro) -> bool:
    return any(c.operador == "fora_lista" and "" in c.valores for c in _todas(f.condicoes))


def refere(f: Filtro, tabela_id: str) -> Set[str]:
    """Ids que o filtro usa: a tabela, as colunas e as ligações (com texto livre, só a tabela)."""
    achados = {tabela_id}
    if f.texto is not None and f.texto.strip():
        return achados
    for c in _todas(f.condicoes):
        for id in (c.coluna, c.outra_coluna):
            if id:
                achados.add(id)
        if c.existe is not None:
            achados.add(c.existe.ligacao)
    return achados


# ── descrição ──

def _valor_legivel(c: Condicao, v: str) -> str:
    if c.transformacao == "mes" and re.fullmatch(r"[0-9]+", v.strip()) and 1 <= int(v) <= 12:
        return t(f"filtro.mes.{int(v)}")
    if c.transformacao == "dia_semana" and re.fullmatch(r"[0-9]+", v.strip()) and 1 <= int(v) <= 7:
        return t(f"filtro.dsem.{int(v)}")
    return v


def descricao_condicao(c: Condicao, m: Modelo, tabela_id: str) -> str:
    tab = m.tabelas[tabela_id]
    if c.existe is not None:
        lig = m.ligacoes.get(c.existe.ligacao)
        outra_id = None if lig is None else (lig.destino if lig.origem == tabela_id else lig.origem)
        outra = m.tabelas.get(outra_id) if outra_id else None
        texto = t("filtro.nao_existe" if c.existe.nao else "filtro.existe", tabela=outra.nome if outra else c.existe.ligacao)
        if outra is not None and c.existe.condicoes:
            inner = f" {t('filtro.e')} ".join(descricao_condicao(x, m, outra.id) for x in c.existe.condicoes)
            texto += f" {t('filtro.com')} {inner}"
    else:
        col = tab.coluna(c.coluna)
        nome = col.nome if col else c.coluna
        if c.transformacao:
            nome = f"{t('filtro.tr.' + c.transformacao)}({nome})"
        partes = [nome, t("filtro.op." + c.operador)]
        if c.outra_coluna is not None:
            outra = tab.coluna(c.outra_coluna)
            partes.append(outra.nome if outra else c.outra_coluna)
        elif c.operador in ("na_lista", "fora_lista"):
            partes.append("(" + ", ".join(c.valores) + ")")
        elif c.operador == "entre":
            partes.append(f" {t('filtro.e')} ".join(_valor_legivel(c, v) for v in c.valores))
        elif c.operador in ("ha_mais_de", "nos_ultimos") and len(c.valores) == 2:
            partes.append(f"{c.valores[0]} {t('filtro.un.' + c.valores[1]) if c.valores[1] in _UNIDADES else c.valores[1]}")
        else:
            partes.extend(_valor_legivel(c, v) for v in c.valores)
        texto = " ".join(partes)
    return f"{t('filtro.nao')} ({texto})" if c.negar else texto


def descricao(f: Filtro, m: Modelo, tabela_id: str) -> str:
    if f.texto is not None and f.texto.strip():
        return f.texto.strip()
    if not f.condicoes:
        return t("filtro.sem_condicoes")
    liga = f" {t('filtro.ou' if f.conectivo == 'OU' else 'filtro.e')} "
    return liga.join(descricao_condicao(c, m, tabela_id) for c in f.condicoes)


# ── JSON ──

_CAMPOS_CONDICAO = {"coluna", "operador", "valores", "transformacao", "negar", "diferenciar", "outra_coluna", "existe"}
_CAMPOS_EXISTE = {"ligacao", "condicoes", "nao"}
_CAMPOS_FILTRO = {"condicoes", "conectivo", "texto"}


def _condicao_para_dict(c: Condicao) -> dict:
    d: dict = {"coluna": c.coluna, "operador": c.operador}
    if c.valores:
        d["valores"] = list(c.valores)
    for chave in ("transformacao", "negar", "diferenciar", "outra_coluna"):
        valor = getattr(c, chave)
        if valor not in ("", False, None):
            d[chave] = valor
    if c.existe is not None:
        d["existe"] = {"ligacao": c.existe.ligacao, "condicoes": [_condicao_para_dict(x) for x in c.existe.condicoes],
                       "nao": c.existe.nao}
    return d


def para_dict(f: Filtro) -> dict:
    d: dict = {"condicoes": [_condicao_para_dict(c) for c in f.condicoes], "conectivo": f.conectivo}
    if f.texto is not None:
        d["texto"] = f.texto
    return d


def _sem_chave_estranha(d, permitidas) -> None:
    if not isinstance(d, dict):
        raise ValueError(t("filtro.e.forma"))
    for chave in d:
        if chave not in permitidas:
            raise ValueError(t("filtro.e.chave", campo=chave))


def _condicao_de_dict(d: dict) -> Condicao:
    _sem_chave_estranha(d, _CAMPOS_CONDICAO)
    if "operador" not in d:
        raise ValueError(t("filtro.e.forma"))
    existe = None
    if d.get("existe") is not None:
        e = d["existe"]
        _sem_chave_estranha(e, _CAMPOS_EXISTE)
        existe = Existe(e["ligacao"], [_condicao_de_dict(x) for x in e.get("condicoes", [])], bool(e.get("nao", False)))
    return Condicao(d.get("coluna", ""), d["operador"], [str(v) for v in d.get("valores", [])],
                    d.get("transformacao", ""), bool(d.get("negar", False)), bool(d.get("diferenciar", False)),
                    d.get("outra_coluna"), existe)


def de_dict(d: dict) -> Filtro:
    _sem_chave_estranha(d, _CAMPOS_FILTRO)
    return Filtro([_condicao_de_dict(c) for c in d.get("condicoes", [])], d.get("conectivo", "E"), d.get("texto"))
