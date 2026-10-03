# -*- coding: utf-8 -*-
"""A releitura do rattlesnake: o que o print do pandas mostra no terminal, relido.

O ratt instala este arquivo num ambiente Python (ratt install) junto com um .pth, e a partir dali
todo  python x.py  desse ambiente nasce com o print relido. O código de quem escreve não muda.

As regras do desenho:
  - os comandos não mudam: print(df.describe()) continua sendo print(df.describe())
  - é só visual: o objeto que o pandas devolve é o mesmo, nada é recalculado nem alterado
  - descreve, não conclui: nenhum veredito ("assimétrica", "use a mediana") — o julgamento é de
    quem lê; a releitura mostra os fatos que levam a ele (vazios, n, ordem, buracos, empates)
  - o nome original da medida fica ao lado da tradução: o pandas cru continua legível em
    qualquer outra máquina
  - a tabela fica na orientação do pandas; as barras nascem numa caixa própria, ao lado
  - o info() fica como é

Alguns templates mostram um número que a saída do pandas não traz — o total de linhas, os
tipos das colunas, os vazios que o value_counts deixou de fora, o "de N" de uma média. Vêm de
LER o df que aparece na própria linha do print (o primeiro nome depois de  print( ); nada é
executado nem alterado.

Só relê quando a saída vai para um terminal. Redirecionada para arquivo (python x.py > out.txt),
sai o texto original. E  RATT=0 python x.py  desliga tudo, para ver o pandas cru.

Se qualquer coisa aqui falhar, o print original roda: a releitura nunca come uma saída.
"""
import builtins
import linecache
import os
import re
import shutil
import sys

_print_original = builtins.print

# o índice que o describe() devolve para colunas numéricas, na ordem dele
INDICE = ["count", "mean", "std", "min", "25%", "50%", "75%", "max"]

# (original, tradução) — a tradução vem primeiro na tela, o original em cinza ao lado
MEDIDAS = [
    ("count", "preenchidos"),
    ("mean",  "média"),
    ("std",   "desvio padrão"),
    ("min",   "mínimo"),
    ("25%",   "25%"),
    ("50%",   "mediana"),
    ("75%",   "75%"),
    ("max",   "máximo"),
]

COR = {
    "azul":    (74, 163, 239),    # nome da coluna, metade central da barra
    "amarelo": (240, 180, 60),    # mediana: o número e o traço dela na barra
    "laranja": (240, 138, 60),    # vazios — só aparece onde existe vazio
    "cinza":   (125, 133, 146),   # nome original, legenda, o que ficou fora
    "branco":  (230, 232, 236),   # pontas da barra
    "borda":   (74, 80, 90),      # caixas e o fio da barra
    "lilas":   (190, 150, 230),   # coluna de texto
    "verde":   (110, 200, 140),   # coluna de data
}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def ligar():
    """Chamado pelo ratt.pth quando o Python começa. Pode ser chamado mais de uma vez."""
    if os.environ.get("RATT") == "0" or os.environ.get("RELEITURA") == "0":
        return
    builtins.print = _print_relido


def _print_relido(*args, **kw):
    try:
        if len(args) == 1 and _vai_para_o_terminal(kw):
            texto = _reler(args[0], sys._getframe(1))
            if texto is not None:
                return _print_original(texto, end=kw.get("end", "\n"), flush=kw.get("flush", False))
    except Exception:
        pass
    return _print_original(*args, **kw)


def _vai_para_o_terminal(kw):
    destino = kw.get("file") or sys.stdout
    return destino is sys.stdout and hasattr(destino, "isatty") and destino.isatty()


# ── o que é relido ──────────────────────────────────────────────────────────

def _reler(obj, quadro):
    pd = sys.modules.get("pandas")
    if pd is None:                        # sem pandas carregado, não há nada a reler
        return None
    arquivo, numero = quadro.f_code.co_filename, quadro.f_lineno
    linha = linecache.getline(arquivo, numero)
    if isinstance(obj, tuple) and not re.search(r"print\(\s*[A-Za-z_]\w*\.shape\s*\)", linha) \
            and any(_e_do_pandas(i) for i in obj):
        return _tupla(obj, linha, quadro, arquivo, numero)
    return _reler_linha(obj, linha, quadro)


def _reler_linha(obj, linha, quadro):
    """O roteador: decide o template pelo objeto e pela linha que você escreveu."""
    pd = sys.modules["pandas"]
    if isinstance(obj, tuple) and re.search(r"print\(\s*[A-Za-z_]\w*\.shape\s*\)", linha):
        base = _base(linha, quadro)
        if isinstance(base, (pd.DataFrame, pd.Series)) and base.shape == obj:
            return _shape(obj)
        return None

    if isinstance(obj, pd.DataFrame) and list(obj.index) == INDICE and obj.columns.is_unique:
        base = _base(linha, quadro)
        base = base if isinstance(base, pd.DataFrame) and base is not obj else None
        return _tabela(obj, base)

    if isinstance(obj, pd.Series) and list(obj.index) == INDICE:
        base = _base(linha, quadro)
        base = base if isinstance(base, (pd.DataFrame, pd.Series)) and base is not obj else None
        return _cartao(obj, base)

    # daqui para baixo, quem decide é a linha que você escreveu
    base = _base(linha, quadro)
    df = base if isinstance(base, pd.DataFrame) and base is not obj else None

    if isinstance(obj, pd.DatetimeIndex):
        if re.search(r"date_range\(", linha):
            m = re.search(r"freq\s*=\s*[\"']([^\"']+)[\"']", linha)
            return _date_range(obj, m.group(1) if m else (obj.freqstr or "D"))
        return _eixo(obj)
    if isinstance(obj, pd.Index) and re.search(r"\.columns\s*\)", linha):
        return _columns(obj, df)
    if isinstance(obj, pd.Index) and re.search(r"\.index\s*\)", linha):
        if isinstance(obj, (pd.PeriodIndex, pd.TimedeltaIndex)):
            return None
        return _indice(obj, f'"{obj.name}"' if obj.name is not None else "")

    np = sys.modules.get("numpy")
    if re.search(r"\.unique\(\)", linha) and (isinstance(obj, pd.api.extensions.ExtensionArray)
                                               or (np is not None and isinstance(obj, np.ndarray) and obj.ndim == 1)):
        m = re.search(r"\[\s*[\"'](.+?)[\"']\s*\]\s*\.unique\(", linha) or re.search(r"\.(\w+)\.unique\(", linha)
        return _unicos(obj, m.group(1) if m else None)

    sozinho = r"^\s*print\(\s*{}\s*\)\s*(#.*)?$"           # a linha é só o print disso, e mais nada

    if isinstance(obj, pd.DataFrame):
        if {"unique", "top", "freq"} <= set(obj.index) and re.search(r"\.describe\(", linha) and _simples(obj):
            return _describe_texto(obj, df)
        if not _simples(obj):
            return None
        if re.search(r"\.groupby\(", linha) and re.search(r"\.agg\(", linha):
            return _agg(obj, linha)
        if re.search(r"crosstab\(", linha):
            return _crosstab(obj)
        if re.search(r"\.corr\(", linha):
            return _corr(obj, linha)
        m = re.search(r"\.(head|tail|sample)\(", linha)
        if m:
            return _linhas(obj, df, m.group(1))
        m = re.match(sozinho.format(r"[A-Za-z_]\w*(?:\.loc)?\[(.+)\]"), linha)
        if m and df is not None and re.search(r"[<>]=?|==|!=|\.isin\(|\.str\.|\.between\(|\.isna\(|\.notna\(",
                                              m.group(1)):
            return _filtro(obj, df, m.group(1).strip())
        if re.match(sozinho.format(r"[A-Za-z_]\w*"), linha) and base is obj:
            return _inteiro(obj)
        if re.search(r"pivot_table\(|\.pivot\(|\.unstack\(", linha):
            r = _pivot(obj, linha)
            if r is not None:
                return r
        # o molde genérico: qualquer outra tabela, comparada com o df da linha
        nome, resto = _expressao(linha)
        return _generico(obj, df, resto if df is not None else (nome or "") + (resto or ""))

    if not isinstance(obj, pd.Series):
        return None
    if re.search(r"\.dtypes\s*\)", linha) and df is not None:
        return _dtypes(obj, df)
    if re.search(r"\.(isna|isnull)\(\)\.sum\(\)", linha):
        return _vazios(obj, df)
    if re.search(r"\.nunique\(\)", linha):
        return _distintos(obj, df)
    if re.search(r"\.value_counts\(", linha) and obj.index.nlevels == 1:
        if obj.name == "count":
            return _contagem(obj, df)
        if obj.name == "proportion":
            return _proporcao(obj, df)
        return None
    tempo = _tempo(obj, linha, quadro, base)
    if tempo is not None:
        return tempo
    if re.search(r"\.groupby\(", linha) and not re.search(r"\.(transform|cumsum|cumcount|rank|shift|diff|"
                                                       r"fillna|ffill|bfill|pct_change)\(", linha):
        if obj.index.nlevels == 1 and obj.index.name is not None:
            return _grupos(obj, linha)
        if obj.index.nlevels == 2 and all(n is not None for n in obj.index.names):
            return _duas_chaves(obj, linha)
        if obj.index.nlevels > 1:
            return None
    if re.match(sozinho.format(r"[A-Za-z_]\w*\[\s*[\"'].+?[\"']\s*\]"), linha) and obj.index.nlevels == 1:
        return _serie(obj)
    if obj.index.nlevels != 1:
        return None

    if isinstance(obj.dtype, pd.CategoricalDtype) and isinstance(obj.cat.categories, pd.IntervalIndex):
        m = re.search(r"pd\.(cut|qcut)\(\s*([A-Za-z_]\w*)\[\s*[\"'](.+?)[\"']\s*\]", linha)
        origem = None
        if m:
            fonte = _procura(quadro, m.group(2))
            if isinstance(fonte, pd.DataFrame) and m.group(3) in fonte.columns and len(fonte) == len(obj):
                origem = fonte[m.group(3)]
        return _faixas(obj, origem, f"pd.{m.group(1)}" if m else "faixas")

    m = re.search(r"\.(sum|mean|count|min|max|std|median|var|quantile|prod|any|all|idxmax|idxmin|skew|sem|"
                  r"memory_usage)\([^()]*\)\s*\)\s*(#.*)?$", linha)
    if m and df is not None and df.columns.is_unique and all(i in df.columns for i in obj.index):
        return _reducao(obj, df, m.group(1))

    # o molde genérico de coluna: comparada com a coluna de origem, quando a linha diz qual é
    nome, resto = _expressao(linha)
    antes = None
    c = re.match(r"\[\s*[\"'](.+?)[\"']\s*\]", resto or "")
    if df is not None and c and c.group(1) in df.columns and df.columns.is_unique:
        antes = df[c.group(1)]
    elif isinstance(base, pd.Series) and base is not obj:
        antes = base
    return _serie_generica(obj, antes, resto if (df is not None or antes is not None) else (nome or "") + (resto or ""))


def _coluna_de(linha, quadro, depois_de):
    """A coluna de origem escrita na linha: df["x"] seguido de depois_de. Só lê, nada executa."""
    pd = sys.modules["pandas"]
    m = re.search(r"([A-Za-z_]\w*)\[\s*[\"']([^\"']+)[\"']\s*\]\s*" + depois_de, linha)
    if not m:
        return None
    fonte = _procura(quadro, m.group(1))
    if isinstance(fonte, pd.DataFrame) and fonte.columns.is_unique and m.group(2) in fonte.columns:
        return fonte[m.group(2)]
    return None


def _tempo(obj, linha, quadro, base):
    """A leva do tempo: to_datetime, .dt, to_period, resample, rolling/diff/shift/pct_change."""
    pd = sys.modules["pandas"]
    nome, resto = _expressao(linha)
    dentro = (nome or "") + (resto or "")
    if obj.dtype.kind == "M" or isinstance(obj.dtype, pd.DatetimeTZDtype):
        m = re.search(r"to_datetime\(\s*[A-Za-z_]\w*\[\s*[\"'][^\"']+[\"']\s*\]\s*(.*)\)\s*$", dentro)
        antes = _coluna_de(linha, quadro, r"[,)]") if "to_datetime(" in linha else None
        if antes is not None and len(antes) == len(obj) and m is not None:
            args = m.group(1).strip().lstrip(",").strip()
            return _conversao(antes, obj, "pd.to_datetime(…" + (", " + args if args else "") + ")")
    if isinstance(obj.dtype, pd.PeriodDtype) and ".to_period(" in linha:
        origem = _coluna_de(linha, quadro, r"\.dt\.to_period")
        m = re.search(r"to_period\(\s*[\"']([^\"']+)[\"']", linha)
        if origem is not None and len(origem) == len(obj):
            return _period(origem, obj, m.group(1) if m else "?")
    if ".dt." in linha:
        origem = _coluna_de(linha, quadro, r"\.dt\.")
        if origem is not None and origem.dtype.kind == "M" and len(origem) == len(obj) and origem.index.equals(obj.index):
            return _dt(origem, obj, dentro[dentro.index(".dt."):])
    if not re.search(r"\.resample\(|\.(rolling|expanding|ewm)\(|\.(diff|pct_change|shift)\(", linha):
        return None
    serie = _coluna_de(linha, quadro, r"\.(resample|rolling|expanding|ewm|diff|pct_change|shift)\(")
    if serie is None and isinstance(base, pd.Series) and base is not obj:
        serie = base
    if serie is None and isinstance(base, pd.DataFrame) and obj.name in base.columns and base.columns.is_unique:
        serie = base[obj.name]
    if serie is None:
        return None
    if ".resample(" in linha:
        if not isinstance(serie.index, pd.DatetimeIndex):
            return None
        m = re.search(r"\.resample\(\s*[\"']([^\"']+)[\"']", linha)
        f = re.search(r"\.resample\([^)]*\)\s*(?:\[\s*[\"'][^\"']+[\"']\s*\])?\s*\.(\w+)\(", linha)
        if not m or not f:
            return None
        return _resample(serie, obj, m.group(1), f.group(1))
    if obj.index.equals(serie.index):
        return _janela(serie, obj, dentro[dentro.index(".", len(nome or "")):] if nome else dentro, linha)
    return None


def _base(linha, quadro):
    """O df de  print(df.describe())  ou  print(df["x"].describe()): só para saber quantas
    linhas ele tem. Só um nome simples é procurado, e nada é executado."""
    m = re.search(r"print\(\s*([A-Za-z_]\w*)", linha)
    if not m:
        return None
    nome = m.group(1)
    if nome in quadro.f_locals:
        return quadro.f_locals[nome]
    return quadro.f_globals.get(nome)


# ── peças ───────────────────────────────────────────────────────────────────

def _c(texto, cor=None, negrito=False):
    if cor is None and not negrito:
        return texto
    abre = ("\x1b[1m" if negrito else "") + ("\x1b[38;2;%d;%d;%dm" % COR[cor] if cor else "")
    return abre + texto + "\x1b[0m"


def _largura(texto):
    return len(_ANSI.sub("", texto))


def _encher(texto, largura, lado="<"):
    falta = " " * max(0, largura - _largura(texto))
    return texto + falta if lado == "<" else falta + texto


def _n(v):
    if v != v:                            # NaN
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"{v:.4g}"
    if v == int(v):
        return str(int(v))
    if a < 1:
        return f"{v:.3g}"
    return f"{v:.1f}"


def _caixa(titulo, linhas, largura):
    """linhas: textos, ou "---" para um separador. largura conta as bordas."""
    largura = max(largura, _largura(titulo) + 6)   # o título nunca fica maior que a caixa
    miolo = largura - 4
    topo = _c("╭─ ", "borda") + _c(titulo, "azul", True) + " "
    saida = [topo + _c("─" * max(0, largura - _largura(topo) - 1) + "╮", "borda")]
    for ln in linhas:
        if ln == "---":
            saida.append(_c("├" + "─" * (largura - 2) + "┤", "borda"))
        else:
            saida.append(_c("│ ", "borda") + _encher(ln, miolo) + _c(" │", "borda"))
    saida.append(_c("╰" + "─" * (largura - 2) + "╯", "borda"))
    return saida


def _barra(d, largura):
    lo, q1, me, q3, hi = d["min"], d["25%"], d["50%"], d["75%"], d["max"]
    pos = lambda v: round((v - lo) / (hi - lo) * (largura - 1))
    traco = []
    for i in range(largura):
        if i == pos(me):
            traco.append(_c("┃", "amarelo"))
        elif pos(q1) <= i <= pos(q3):
            traco.append(_c("█", "azul"))
        else:
            traco.append(_c("─", "borda"))
    return _c("┃", "branco") + "".join(traco) + _c("┃", "branco")


LEGENDA = (_c("┃", "branco") + _c(" mín/máx  ", "cinza") + _c("█", "azul") + _c(" 25%–75%  ", "cinza")
           + _c("┃", "amarelo") + _c(" mediana", "cinza"))


# ── df.shape ────────────────────────────────────────────────────────────────

def _shape(forma):
    if len(forma) == 1:
        return "\n".join([str(forma), "  Li", "  │", "  ▼", " " + _c(str(forma[0]), "azul", True)])
    li, co = forma
    return "\n".join([
        str(forma),
        "  Li/Co " + _c("──▶ ", "cinza") + _c(str(co), "amarelo", True) + _c("   colunas", "cinza"),
        "    " + _c("│", "cinza"),
        "    " + _c("▼", "cinza"),
        "   " + _c(str(li), "azul", True) + _c("   linhas", "cinza"),
    ])


# ── df.describe() ───────────────────────────────────────────────────────────

def _celula(medida, v, total):
    if medida == "count":
        txt = str(int(v))
        if total is not None and int(v) < total:
            return _c(txt + f" ({total - int(v)} vaz.)", "laranja")
        return txt
    if medida == "mean":
        return _c(_n(v), negrito=True)
    if medida == "50%":
        return _c(_n(v), "amarelo", True)
    if medida == "std":
        return "± " + _n(v)
    return _n(v)


def _rotulo(original, traducao):
    t = _c(traducao, "amarelo", True) if original == "50%" else traducao
    if original == traducao:
        return t
    return _encher(t, 15) + _c(original, "cinza")


def _tabela(desc, base):
    total = len(base) if base is not None else None
    vazias = [c for c in desc.columns if desc.at["count", c] == 0]
    constantes = [c for c in desc.columns if c not in vazias and desc.at["std", c] == 0]
    normais = [c for c in desc.columns if c not in vazias and c not in constantes]

    tela = shutil.get_terminal_size((120, 40)).columns
    LR = max(_largura(_rotulo(o, t)) for o, t in MEDIDAS) + 2
    cels = {c: [_celula(m, desc.at[m, c], total) for m, _ in MEDIDAS] for c in normais}
    larg = {c: max(len(str(c)), *(_largura(x) for x in cels[c])) + 3 for c in normais}
    NB = min(18, max((len(str(c)) for c in normais), default=4)) + 2
    LB = max(NB + 26, _largura(LEGENDA)) + 4          # caixa das barras

    # blocos de colunas: o que cabe na tela com a caixa das barras ao lado (no máximo 6,
    # que é o que cabe na altura da tabela); se nem uma coluna cabe ao lado, as barras descem
    ao_lado = bool(normais) and LR + larg[normais[0]] + 4 + 2 + LB <= tela
    extra = 2 + LB if ao_lado else 0
    blocos, atual = [], []
    for c in normais:
        tenta = atual + [c]
        cabe = LR + sum(larg[x] for x in tenta) + 4 + extra <= tela and (len(tenta) <= 6 or not ao_lado)
        if atual and not cabe:
            blocos.append(atual)
            atual = [c]
        else:
            atual = tenta
    if atual:
        blocos.append(atual)

    n_num = len(desc.columns)
    cab = [_c("Resumo", "azul", True)]
    if total is not None:
        cab.append(f"{total} linhas")
    cab.append(f"{n_num} coluna{'s' if n_num != 1 else ''} numérica{'s' if n_num != 1 else ''}")
    if base is not None and base.shape[1] > n_num:
        fora = base.shape[1] - n_num
        cab.append(f"{fora} não numérica{'s' if fora > 1 else ''} fora " + _c('(include="all" as inclui)', "cinza"))
    saida = [_c(" · ", "cinza").join(cab), ""]

    feitas = 0
    for bloco in blocos:
        A = [_encher("", LR) + "".join(_encher(_c(str(c), "azul", True), larg[c], ">") for c in bloco), "---"]
        for i, (orig, trad) in enumerate(MEDIDAS):
            A.append(_encher(_rotulo(orig, trad), LR) + "".join(_encher(cels[c][i], larg[c], ">") for c in bloco))
        LA = LR + sum(larg[c] for c in bloco) + 4
        de, ate = feitas + 1, feitas + len(bloco)
        feitas = ate
        titulo = f"colunas {de}–{ate} de {len(normais)}" if len(blocos) > 1 else "describe"

        B = [_encher("", NB) + _c("mín", "cinza") + " " * 20 + _c("máx", "cinza"), "---"]
        for c in bloco:
            nome = str(c) if len(str(c)) <= 18 else str(c)[:17] + "…"
            B.append(_encher(_c(nome, "azul", True), NB) + _barra(desc[c], 24))
        B += [""] * max(0, len(A) - len(B) - 2) + ["---", LEGENDA]

        caixa_a, caixa_b = _caixa(titulo, A, LA), _caixa("distribuição", B, LB)
        if ao_lado:
            saida += [a + "  " + b for a, b in zip(caixa_a, caixa_b)]
        else:
            saida += caixa_a + caixa_b
        saida.append("")

    fora = ([_c(str(c), "azul") + _c(f" constante, todos {_n(desc.at['min', c])}", "cinza") for c in constantes]
            + [_c(str(c), "azul") + _c(" vazia, 0" + (f" de {total}" if total is not None else ""), "cinza")
               for c in vazias])
    if fora:
        linha = _c("fora da tabela: ", "cinza")
        for i, item in enumerate(fora):
            pedaco = ("" if i == 0 else _c(" · ", "cinza")) + item
            if _largura(linha) + _largura(pedaco) > tela - 1:
                saida.append(linha)
                linha = "                " + item
            else:
                linha += pedaco
        saida.append(linha)
    return "\n".join(saida).rstrip("\n")


# ── df["coluna"].describe() ─────────────────────────────────────────────────

def _cartao(d, base):
    total = len(base) if base is not None else None
    nome = str(d.name) if d.name is not None else "série"
    titulo = nome + (f" · {total} linhas" if total is not None else "")
    cont = int(d["count"])
    if total is not None:
        preench = f"{cont} / {total}" + (f"  ({total - cont} vaz.)" if cont < total else "")
        preench = _c(preench, "laranja") if cont < total else preench
    else:
        preench = str(cont)

    if cont == 0:
        linhas = ["", "preenchidos     " + preench + "   " + _c("vazia: não há o que descrever", "cinza"), ""]
    elif d["std"] == 0:
        linhas = ["", "preenchidos     " + preench, "", _c(f"constante: todos {_n(d['min'])}", "cinza"), ""]
    else:
        linhas = [""]
        for rot, valor, orig in [
            ("preenchidos",    preench,                                 "count"),
            ("média",          _c(_n(d["mean"]), negrito=True),         "mean"),
            ("mediana",        _c(_n(d["50%"]), "amarelo", True),       "50%"),
            ("desvio padrão",  "± " + _n(d["std"]),                     "std (amostral, n−1)"),
            ("faixa",          f"{_n(d['min'])} → {_n(d['max'])}",      "min / max"),
            ("metade central", f"{_n(d['25%'])} a {_n(d['75%'])}",      "25% / 75%"),
        ]:
            linhas.append(_encher(rot, 16) + _encher(valor, 22) + _c(orig, "cinza"))
        linhas += ["", _c(_n(d["min"]) + " ", "cinza") + _barra(d, 30) + _c(" " + _n(d["max"]), "cinza"),
                   "", LEGENDA]
    largura = max(_largura(x) for x in linhas + [titulo + "    "]) + 4
    return "\n".join(_caixa(titulo, linhas, largura))


# ── peças comuns dos templates ──────────────────────────────────────────────

def _tela():
    return shutil.get_terminal_size((120, 40)).columns


def _cor_tipo(dt):
    k = getattr(dt, "kind", "O")
    if k in "iuf":
        return "azul"
    if k == "M":
        return "verde"
    if k == "b":
        return "amarelo"
    return "lilas"


SIGNIFICADO = {"int64": "inteiro", "float64": "decimal", "str": "texto", "object": "texto (antigo)",
               "bool": "verdadeiro/falso", "category": "categoria"}


def _significado(dt):
    s = str(dt)
    return "data" if s.startswith("datetime64") else SIGNIFICADO.get(s, s)


def _visivel(texto):
    """Espaço no começo ou no fim de um texto vira ␣ laranja: o que o cat -A mostraria."""
    a, meio, b = re.match(r"^( *)(.*?)( *)$", texto, re.S).groups()
    return _c("␣" * len(a), "laranja") + meio + _c("␣" * len(b), "laranja")


def _cabecalho(*partes):
    return _c(" · ", "cinza").join([_c(partes[0], "azul", True)] + [p for p in partes[1:] if p])


def _lado_a_lado(caixas):
    """Caixas lado a lado se couberem na tela; senão, uma embaixo da outra."""
    larg = [max(_largura(x) for x in c) for c in caixas]
    if sum(larg) + 2 * (len(caixas) - 1) > _tela():
        return [ln for c in caixas for ln in c]
    saida = []
    for i in range(max(len(c) for c in caixas)):
        saida.append("  ".join(_encher(c[i] if i < len(c) else "", larg[j]) for j, c in enumerate(caixas)))
    return saida


def _em_colunas(itens, n_col):
    """Divide a lista em n_col pedaços, quantos couberem na tela (no mínimo 1)."""
    largura = max(_largura(x) for x in itens) + 6
    n_col = max(1, min(n_col, _tela() // largura, -(-len(itens) // 10)))   # lista curta: uma caixa só
    por = -(-len(itens) // n_col)
    return [itens[i * por:(i + 1) * por] for i in range(n_col) if itens[i * por:(i + 1) * por]]


def _caixas(titulo, grupos):
    """Uma caixa por grupo; o título diz que pedaço da lista ela tem."""
    saida, feitos = [], 0
    for g in grupos:
        t = f"{titulo} {feitos + 1}–{feitos + len(g)}" if len(grupos) > 1 else titulo
        feitos += len(g)
        saida.append(_caixa(t, g, max(_largura(x) for x in g) + 4))
    return _lado_a_lado(saida)


LEGENDA_TIPO = (_c("número", "azul") + _c(" · ", "cinza") + _c("texto", "lilas") + _c(" · ", "cinza")
                + _c("data", "verde"))


# ── a grade: o molde de head/tail/sample, print(df), filtro e coluna ─────────

def _rotulo_valor(v):
    return _c("NaN", "laranja") if (v is None or v != v) else _visivel(str(v))


def _celulas(s):
    if s.dtype.kind in "iuf":                         # a formatação de número é a do próprio pandas
        fmt = s.to_string(index=False).split("\n")
        return [_c("NaN", "laranja") if v.strip() == "NaN" else v.strip() for v in fmt]
    if s.dtype.kind == "M":
        return [_c("NaT", "laranja") if v != v else _c(str(v.date()) if v == v.normalize() else str(v), "verde")
                for v in s]
    return [_rotulo_valor(v) for v in s]


def _corte(n):
    """Quantas linhas mostrar em cima e embaixo — a mesma regra do pandas (max_rows / min_rows)."""
    pd = sys.modules["pandas"]
    maximo, minimo = pd.get_option("display.max_rows"), pd.get_option("display.min_rows")
    if maximo is None or n <= maximo:
        return None
    meio = (minimo or maximo) // 2
    return (meio, meio)


def _grade(obj, rotulo):
    """As caixas da tabela; cada caixa é um bloco de colunas que cabe na tela."""
    cortar = _corte(len(obj))
    mostra = obj.iloc[list(range(cortar[0])) + list(range(len(obj) - cortar[1], len(obj)))] if cortar else obj
    cols = list(mostra.columns)
    cel = {c: _celulas(mostra[c]) for c in cols}
    larg = {c: max(len(str(c)), len(str(mostra[c].dtype)), *(_largura(x) for x in cel[c])) + 3 for c in cols}
    LI = max(len(str(i)) for i in mostra.index) + 2
    tela = _tela()
    blocos, atual = [], []
    for c in cols:
        if atual and LI + sum(larg[x] for x in atual + [c]) + 4 > tela:
            blocos.append(atual)
            atual = []
        atual.append(c)
    blocos.append(atual)
    por = -(-len(cols) // len(blocos))               # blocos iguais, sem uma caixa de uma coluna só
    iguais = [cols[i:i + por] for i in range(0, len(cols), por)]
    if all(LI + sum(larg[x] for x in b) + 4 <= tela for b in iguais):
        blocos = iguais
    saida, feitas = [], 0
    for b in blocos:
        L = [_encher("", LI) + "".join(_encher(_c(str(c), "azul", True), larg[c], ">") for c in b),
             _encher("", LI) + "".join(_encher(_c(str(mostra[c].dtype), _cor_tipo(mostra[c].dtype)), larg[c], ">")
                                       for c in b),
             "---"]
        for i, ix in enumerate(mostra.index):
            if cortar and i == cortar[0]:
                L.append(_c(f"  ⋮  {len(obj) - cortar[0] - cortar[1]} linhas não mostradas", "cinza"))
            L.append(_encher(_c(str(ix), "cinza"), LI) + "".join(_encher(cel[c][i], larg[c], ">") for c in b))
        titulo = f"colunas {feitas + 1}–{feitas + len(b)} de {len(cols)}" if len(blocos) > 1 else rotulo
        feitas += len(b)
        saida += _caixa(titulo, L, LI + sum(larg[c] for c in b) + 4)
    saida.append(_c("tipo: ", "cinza") + LEGENDA_TIPO + _c("   NaN", "laranja") + _c(" = vazio   ", "cinza")
                 + _c("␣", "laranja") + _c(" = espaço no começo ou no fim do texto", "cinza"))
    return saida


def _simples(obj):
    """Só índice e colunas de um nível: o resto (MultiIndex) sai como o pandas escreve."""
    return obj.index.nlevels == 1 and getattr(obj, "columns", obj.index).nlevels == 1


# ── df.head() / tail() / sample() ───────────────────────────────────────────

def _linhas(obj, df, rotulo):
    if not len(obj.columns) or not len(obj):
        return None
    total = f"{len(obj)} de {len(df)} linhas" if df is not None else f"{len(obj)} linhas"
    return "\n".join([_cabecalho(rotulo, total, f"{len(obj.columns)} colunas"), ""] + _grade(obj, rotulo))


# ── print(df) ───────────────────────────────────────────────────────────────

def _inteiro(obj):
    if not len(obj.columns) or not len(obj):
        return None
    cortar = _corte(len(obj))
    nota = _c(f"mostrando as {cortar[0]} primeiras e as {cortar[1]} últimas, como o pandas", "cinza") if cortar else ""
    return "\n".join([_cabecalho("DataFrame", f"{len(obj)} linhas", f"{len(obj.columns)} colunas", nota), ""]
                     + _grade(obj, "DataFrame"))


# ── print(df[condição]) ─────────────────────────────────────────────────────

def _filtro(obj, df, condicao):
    if not len(obj.columns):
        return None
    partes = ["filtro", _c(condicao, "amarelo")]
    if df is not None:
        partes += [f"{len(obj)} de {len(df)} linhas ficaram", f"{len(df) - len(obj)} saíram"]
    else:
        partes += [f"{len(obj)} linhas"]
    saida = [_cabecalho(*partes), ""]
    if not len(obj):
        return "\n".join(saida + [_c("nenhuma linha passou no filtro", "cinza")])
    saida += _grade(obj, "filtro")
    saida.append(_c("o índice (cinza, à esquerda) é o da linha no df original: os buracos são as linhas que saíram",
                    "cinza"))
    return "\n".join(saida)


# ── print(df["coluna"]) ─────────────────────────────────────────────────────

def _serie(obj):
    if not len(obj):
        return None
    vaz = int(obj.isna().sum())
    cortar = _corte(len(obj))
    mostra = obj.iloc[list(range(cortar[0])) + list(range(len(obj) - cortar[1], len(obj)))] if cortar else obj
    cel = _celulas(mostra)
    LI = max(len(str(i)) for i in mostra.index) + 2
    LV = max([len(str(obj.name)), len(str(obj.dtype))] + [_largura(x) for x in cel]) + 2
    L = [_encher("", LI) + _encher(_c(str(obj.name), "azul", True), LV, ">"),
         _encher("", LI) + _encher(_c(str(obj.dtype), _cor_tipo(obj.dtype)), LV, ">"), "---"]
    for i, ix in enumerate(mostra.index):
        if cortar and i == cortar[0]:
            L.append(_c(f"  ⋮  {len(obj) - cortar[0] - cortar[1]} linhas não mostradas", "cinza"))
        L.append(_encher(_c(str(ix), "cinza"), LI) + _encher(cel[i], LV, ">"))
    saida = [_cabecalho("Series", f'"{obj.name}"', str(obj.dtype), f"{len(obj)} linhas",
                        _c(f"{vaz} vazios", "laranja") if vaz else "0 vazios"), ""]
    saida += _caixa(str(obj.name), L, max(_largura(x) for x in L) + 4)
    return "\n".join(saida)


# ── df.dtypes ───────────────────────────────────────────────────────────────

def _dtypes(obj, df):
    nomes = max(len(str(c)) for c in obj.index) + 2
    itens = [_c(f"{i:>2} ", "cinza") + _encher(str(c), nomes) + _encher(_c(str(dt), _cor_tipo(dt), True), 9)
             + _c(_significado(dt), "cinza") for i, (c, dt) in enumerate(obj.items(), 1)]
    contagem = {}
    for dt in obj:
        contagem.setdefault(str(dt), [dt, 0])[1] += 1
    resumo = [_c(t, _cor_tipo(dt)) + f" {n}" for t, (dt, n) in sorted(contagem.items(), key=lambda x: -x[1][1])]
    saida = [_cabecalho("dtypes", f"{len(obj)} colunas", *resumo), ""]
    saida += _caixas("colunas", _em_colunas(itens, 2))
    saida.append(_c("int64 ", "azul") + _c("inteiro — não guarda vazio   ", "cinza") + _c("float64 ", "azul")
                 + _c("decimal — guarda vazio (NaN)   ", "cinza") + _c("str ", "lilas") + _c("texto   ", "cinza")
                 + _c("datetime64 ", "verde") + _c("data", "cinza"))
    return "\n".join(saida)


# ── df.isna().sum() ─────────────────────────────────────────────────────────

def _vazios(obj, df):
    total = len(df) if df is not None else None
    nomes = max(len(str(c)) for c in obj.index) + 2
    itens = []
    for c, n in obj.items():
        n = int(n)
        nome = _encher(_c(str(c), "azul") if n else _c(str(c), "cinza"), nomes)
        num = _c(f"{n:>5}", "laranja", True) if n else _c(f"{n:>5}", "cinza")
        if total:
            pct = n / total
            cheio = max(1, round(pct * 20)) if n else 0   # vazio que existe nunca some no arredondamento
            resto = (" " + _c(f"{pct:>6.1%}", "laranja") + "  " + _c("█" * cheio, "laranja")
                     + _c("─" * (20 - cheio), "borda")) if n else ""
        else:
            resto = ""
        itens.append(nome + num + resto)
    com = int((obj > 0).sum())
    partes = ["isna().sum()"]
    if total:
        cel = total * len(obj)
        partes += [f"{total} linhas", f"{com} de {len(obj)} colunas têm vazios",
                   f"{int(obj.sum())} de {cel} células vazias ({obj.sum() / cel:.1%})"]
    else:
        partes += [f"{com} de {len(obj)} colunas têm vazios"]
    saida = [_cabecalho(*partes), ""]
    saida += _caixas("vazios por coluna", _em_colunas(itens, 2))
    if total:
        saida.append(_c("barra: 0% ", "cinza") + _c("─" * 10, "borda") + _c("█" * 10, "laranja")
                     + _c(" 100% das linhas vazias", "cinza"))
    return "\n".join(saida)


# ── df.nunique() ────────────────────────────────────────────────────────────

def _distintos(obj, df):
    total = len(df) if df is not None else None
    nomes = max(len(str(c)) for c in obj.index) + 2
    itens = []
    for c, n in obj.items():
        n = int(n)
        if n == 0:
            nota = "vazia"
        elif n == 1:
            nota = "1 valor só: constante"
        elif total is not None and n == total:
            nota = "todos diferentes"
        else:
            nota = ""
        de = _c(f" de {total}  ", "cinza") if total is not None else "  "
        itens.append(_encher(_c(str(c), "azul"), nomes) + _c(f"{n:>6}", negrito=True) + de + _c(nota, "cinza"))
    saida = [_cabecalho("nunique()", f"{total} linhas" if total is not None else "", f"{len(obj)} colunas"), ""]
    saida += _caixas("valores diferentes", _em_colunas(itens, 2))
    saida.append(_c("nunique não conta vazios: uma coluna toda vazia tem 0 valores diferentes", "cinza"))
    return "\n".join(saida)


# ── df["col"].value_counts() ────────────────────────────────────────────────

def _contagem(obj, df):
    coluna = obj.index.name
    total = int(obj.sum())
    maior = obj.max() if len(obj) else 0
    rotulos = [_c("NaN", "laranja") if (v is None or v != v) else _visivel(str(v)) for v in obj.index]
    LV = max([len("valor")] + [_largura(r) for r in rotulos]) + 2
    L = [_encher(_c("valor", "cinza"), LV) + _c(f"{'quantas':>8}{'%':>8}", "cinza"), "---"]
    for r, n in zip(rotulos, obj):
        cheio = round(n / maior * 24) if maior else 0
        L.append(_encher(r, LV) + _c(f"{n:>8}", negrito=True) + f"{n / total:>8.1%}  "
                 + _c("█" * cheio, "azul") + _c("─" * (24 - cheio), "borda"))
    rodape = _c(f"{len(obj)} valores diferentes · {total} contados", "cinza")
    if df is not None and coluna in df.columns:
        vazios = int(df[coluna].isna().sum())
        if obj.index.hasnans:                         # dropna=False: os vazios já estão na lista
            pass
        elif vazios:
            rodape += _c(" · ", "cinza") + _c(f"{vazios} vazios não contados", "laranja")
        else:
            rodape += _c(" · 0 vazios", "cinza")
    L += ["---", rodape]
    saida = [_cabecalho("value_counts", f'coluna "{coluna}"' if coluna is not None else "",
                        f"{len(df)} linhas" if df is not None else ""), ""]
    saida += _caixa(str(coluna) if coluna is not None else "contagem", L, max(_largura(x) for x in L) + 4)
    saida.append(_c("␣", "laranja") + _c(" = espaço no começo ou no fim do texto   ", "cinza")
                 + _c("value_counts não conta vazios; dropna=False os inclui", "cinza"))
    return "\n".join(saida)


# ── df.columns ──────────────────────────────────────────────────────────────

def _columns(obj, df):
    def cor(c):
        if df is None or c not in df.columns or not df.columns.is_unique:
            return None
        return _cor_tipo(df[c].dtype)
    itens = [_c(f"{i:>2} ", "cinza") + _c(_visivel(str(c)), cor(c)) for i, c in enumerate(obj, 1)]
    partes = ["columns", f"{len(obj)} colunas"]
    if df is not None:
        contagem = {}
        for dt in df.dtypes:
            contagem.setdefault(str(dt), [dt, 0])[1] += 1
        partes += [_c(t, _cor_tipo(dt)) + f" {n}" for t, (dt, n) in sorted(contagem.items(), key=lambda x: -x[1][1])]
    saida = [_cabecalho(*partes), ""]
    saida += _caixas("colunas", _em_colunas(itens, 3))
    legenda = _c("␣", "laranja") + _c(" = espaço no começo ou no fim do nome", "cinza")
    if df is not None:
        legenda = _c("cor do nome = tipo: ", "cinza") + LEGENDA_TIPO + "   " + legenda
    saida.append(legenda)
    return "\n".join(saida)


# ── df.groupby("chave")["coluna"].func() ───────────────────────────────────

NOMES_FUNC = {"mean": "média", "sum": "soma", "median": "mediana", "count": "contagem", "size": "contagem",
              "max": "máximo", "min": "mínimo", "std": "desvio padrão", "nunique": "valores diferentes"}


def _grupos(obj, linha):
    if obj.dtype.kind not in "iuf":
        return None
    m = re.search(r"groupby\(\s*[\"'](.+?)[\"'].*?\)\s*\[\s*[\"'](.+?)[\"']\s*\]\s*\.(\w+)\(", linha)
    if m:
        chave, coluna, func = m.groups()
    elif re.search(r"\.size\(\)", linha):
        chave, coluna, func = obj.index.name or "grupo", "linhas", "size"
    else:
        chave, coluna, func = obj.index.name or "grupo", obj.name or "valor", ""
    validos = obj.dropna()
    maior = validos.abs().max() if len(validos) else 0
    rotulos = [_c("NaN", "laranja") if (g is None or g != g) else _visivel(str(g)) for g in obj.index]
    LG = max([len(str(chave))] + [_largura(r) for r in rotulos]) + 2
    L = [_encher(_c(str(chave), "cinza"), LG) + _c(f"{func:>10}", "cinza"), "---"]
    for r, v in zip(rotulos, obj):
        if v != v:
            L.append(_encher(r, LG) + _c(f"{'NaN':>10}", "laranja"))
            continue
        cheio = round(abs(v) / maior * 30) if maior else 0
        L.append(_encher(r, LG) + _c(f"{_n(v):>10}", negrito=True) + "  "
                 + _c("█" * cheio, "azul") + _c("─" * (30 - cheio), "borda"))
    L += ["---", _c(f"barra: de 0 até o maior valor ({_n(maior)})", "cinza")]
    titulo = f"{NOMES_FUNC.get(func, func)} de {coluna} por {chave}" if func else f"{coluna} por {chave}"
    saida = [_cabecalho("groupby", titulo, f"{len(obj)} grupos"), ""]
    saida += _caixa(f"{coluna} por {chave}", L, max(_largura(x) for x in L) + 4)
    return "\n".join(saida)


# ── df.describe(include="all") — as colunas de texto ───────────────────────

def _describe_texto(obj, df):
    """As colunas de texto ganham a caixa delas; as numéricas vão para o template do describe."""
    texto = [c for c in obj.columns if obj[c].get("unique") is not None and obj.at["unique", c] == obj.at["unique", c]]
    numer = [c for c in obj.columns if c not in texto]
    total = len(df) if df is not None else None
    partes = ['describe(include="all")' if numer else "describe", f"{total} linhas" if total is not None else "",
              f"{len(texto)} coluna{'s' if len(texto) != 1 else ''} de texto"]
    if numer:
        partes.append(f"{len(numer)} numérica{'s' if len(numer) != 1 else ''}")
    saida = [_cabecalho(*partes), ""]
    if texto:
        med = [("count", "preenchidos"), ("unique", "valores diferentes"), ("top", "mais frequente"),
               ("freq", "quantas vezes")]
        cel = {}
        for c in texto:
            cont = int(obj.at["count", c])
            vaz = total - cont if total is not None else 0
            cel[c] = [_c(f"{cont} ({vaz} vaz.)", "laranja") if vaz else str(cont),
                      str(int(obj.at["unique", c])),
                      _c(_rotulo_valor(obj.at["top", c]), negrito=True),
                      str(int(obj.at["freq", c]))]
        larg = {c: max(len(str(c)), *(_largura(x) for x in cel[c])) + 4 for c in texto}
        LR = 27
        tela = _tela()
        blocos, atual = [], []                       # quantas colunas cabem na largura da tela
        for c in texto:
            if atual and LR + sum(larg[x] for x in atual + [c]) + 4 > tela:
                blocos.append(atual)
                atual = []
            atual.append(c)
        blocos.append(atual)
        feitas = 0
        for b in blocos:
            L = [_encher("", LR) + "".join(_encher(_c(str(c), "lilas", True), larg[c], ">") for c in b), "---"]
            for i, (orig, trad) in enumerate(med):
                L.append(_encher(trad, 20) + _encher(_c(orig, "cinza"), LR - 20)
                         + "".join(_encher(cel[c][i], larg[c], ">") for c in b))
            titulo = (f"colunas de texto {feitas + 1}–{feitas + len(b)} de {len(texto)}" if len(blocos) > 1
                      else "colunas de texto")
            feitas += len(b)
            saida += _caixa(titulo, L, LR + sum(larg[c] for c in b) + 4)
        saida.append(_c("se dois valores empatam como mais frequente, o pandas mostra um só — o 'top' não avisa o empate",
                        "cinza"))
    if numer:
        desc = obj.loc[INDICE, numer].astype(float)
        saida += ["", _tabela(desc, df).split("\n", 2)[2]]   # sem o cabeçalho: o de cima já disse tudo
    return "\n".join(saida)


# ── value_counts(normalize=True) ────────────────────────────────────────────

def _proporcao(obj, df):
    coluna = obj.index.name
    maior = obj.max() if len(obj) else 0
    rot = [_rotulo_valor(v) for v in obj.index]
    LV = max([len("valor")] + [_largura(r) for r in rot]) + 2
    L = [_encher(_c("valor", "cinza"), LV) + _c(f"{'proporção':>10}{'%':>8}", "cinza"), "---"]
    for r, p in zip(rot, obj):
        cheio = round(p / maior * 24) if maior else 0
        L.append(_encher(r, LV) + f"{p:>10.4f}" + _c(f"{p:>8.1%}", negrito=True) + "  "
                 + _c("█" * cheio, "azul") + _c("─" * (24 - cheio), "borda"))
    if df is not None and coluna in df.columns and not obj.index.hasnans:
        contados, vazios = int(df[coluna].notna().sum()), int(df[coluna].isna().sum())
        rodape = _c(f"proporção sobre os {contados} valores contados", "cinza") + (
            _c(f" · {vazios} vazios ficaram fora do denominador", "laranja") if vazios else _c(" · 0 vazios", "cinza"))
        L += ["---", rodape]
    saida = [_cabecalho("value_counts(normalize=True)", f'coluna "{coluna}"' if coluna is not None else "",
                        f"{len(df)} linhas" if df is not None else ""), ""]
    saida += _caixa(str(coluna) if coluna is not None else "proporção", L, max(_largura(x) for x in L) + 4)
    saida.append(_c("a coluna proporção é o número que o pandas devolve; o % é o mesmo número, lido", "cinza"))
    return "\n".join(saida)


# ── df["col"].unique() ──────────────────────────────────────────────────────

def _unicos(obj, coluna):
    if len(obj) == 0 or len(obj) > 500:               # lista enorme não é para ler na tela
        return None
    itens = [_c(f"{i:>3} ", "cinza") + _rotulo_valor(v) for i, v in enumerate(obj, 1)]
    saida = [_cabecalho("unique()", f'coluna "{coluna}"' if coluna else "", f"{len(obj)} valores diferentes",
                        f"dtype {obj.dtype}"), ""]
    saida += _caixas("valores", _em_colunas(itens, 3))
    saida.append(_c("na ordem em que aparecem no arquivo, não em ordem alfabética   ", "cinza")
                 + _c("␣", "laranja") + _c(" = espaço no começo ou no fim   ", "cinza")
                 + _c("NaN", "laranja") + _c(" = o vazio também conta como um valor aqui", "cinza"))
    return "\n".join(saida)


# ── groupby(...).agg([...]) ─────────────────────────────────────────────────

NOMES_AGG = {"mean": "média", "count": "preenchidos", "size": "linhas", "min": "mínimo", "max": "máximo",
             "sum": "soma", "median": "mediana", "std": "desvio padrão", "nunique": "diferentes"}


def _agg(obj, linha):
    cols = [str(c) for c in obj.columns]
    if not all(obj[c].dtype.kind in "iuf" for c in obj.columns):
        return None
    m = re.search(r"groupby\(\s*[\"'](.+?)[\"'].*?\)\s*\[\s*[\"'](.+?)[\"']\s*\]", linha)
    chave, coluna = m.groups() if m else (obj.index.name or "grupo", "")
    larg = {c: max(len(NOMES_AGG.get(c, c)), len(c), *(len(_n(v)) for v in obj[c])) + 4 for c in cols}
    LG = max([len(str(chave))] + [_largura(_rotulo_valor(g)) for g in obj.index]) + 2
    L = [_encher("", LG) + "".join(_encher(NOMES_AGG.get(c, c), larg[c], ">") for c in cols),
         _encher(_c(str(chave), "cinza"), LG) + "".join(_encher(_c(c, "cinza"), larg[c], ">") for c in cols), "---"]
    for g, valores in zip(obj.index, obj.itertuples(index=False)):
        lin = _encher(_rotulo_valor(g), LG)
        for c, v in zip(cols, valores):
            txt = _c("NaN", "laranja") if v != v else _n(v)
            if c == "count" and v == v:
                txt = _c(txt, "amarelo", True)
            elif c == "mean" and v == v:
                txt = _c(txt, negrito=True)
            lin += _encher(txt, larg[c], ">")
        L.append(lin)
    titulo = f"{coluna} por {chave}" if coluna else f"por {chave}"
    saida = [_cabecalho("groupby · agg", titulo, f"{len(obj)} grupos",
                        f"{len(cols)} funções: " + ", ".join(cols)), ""]
    saida += _caixa(titulo, L, max(_largura(x) for x in L) + 4)
    legenda = _c("o nome em português em cima, o original embaixo", "cinza")
    if "count" in cols:
        legenda += _c(" · ", "cinza") + _c("preenchidos", "amarelo") + _c(" em amarelo: é o n de cada grupo", "cinza")
    saida.append(legenda)
    return "\n".join(saida)


# ── groupby com duas chaves ─────────────────────────────────────────────────

def _duas_chaves(obj, linha):
    if obj.dtype.kind not in "iuf" or len(obj) > 200:
        return None
    m = re.search(r"groupby\(\s*\[\s*[\"'](.+?)[\"']\s*,\s*[\"'](.+?)[\"']\s*\]\s*\)\s*\[\s*[\"'](.+?)[\"']\s*\]\s*\.(\w+)\(",
                  linha)
    if m:
        k0, k1, coluna, func = m.groups()
    else:
        k0, k1 = (str(n) if n is not None else "chave" for n in obj.index.names)
        coluna, func = obj.name or "valor", ""
    validos = obj.dropna()
    maior = validos.abs().max() if len(validos) else 0
    LG = max(_largura(_rotulo_valor(g)) for g in obj.index.get_level_values(1)) + 6
    L = [_c(f"{k0} → {k1}", "cinza") + " " * 4 + _c(func, "cinza"), "---"]
    vistos = []
    for g0 in obj.index.get_level_values(0):
        if any(g0 is v or g0 == v for v in vistos):
            continue
        vistos.append(g0)
        L.append(_c(_rotulo_valor(g0), "azul", True))
        for (a, g1), v in obj.items():
            if not (a is g0 or a == g0):
                continue
            if v != v:
                L.append("   " + _encher(_rotulo_valor(g1), LG) + _c(f"{'NaN':>8}", "laranja"))
                continue
            cheio = round(abs(v) / maior * 24) if maior else 0
            L.append("   " + _encher(_rotulo_valor(g1), LG) + _c(f"{_n(v):>8}", negrito=True) + "  "
                     + _c("█" * cheio, "azul") + _c("─" * (24 - cheio), "borda"))
    L += ["---", _c(f"barra: de 0 até o maior valor ({_n(maior)})", "cinza")]
    titulo = f"{NOMES_FUNC.get(func, func)} de {coluna} por {k0} e {k1}" if func else f"{coluna} por {k0} e {k1}"
    saida = [_cabecalho("groupby · duas chaves", titulo, f"{len(obj)} combinações"), ""]
    saida += _caixa(f"{coluna} por {k0} e {k1}", L, max(_largura(x) for x in L) + 4)
    saida.append(_c("só aparecem as combinações que existem no dado: combinação sem linha nenhuma não vira 0, some",
                    "cinza"))
    return "\n".join(saida)


# ── pd.crosstab ─────────────────────────────────────────────────────────────

def _crosstab(obj):
    if not all(obj[c].dtype.kind in "iu" for c in obj.columns) or obj.shape[1] > 15 or obj.shape[0] > 60:
        return None
    nl = str(obj.index.name) if obj.index.name is not None else "linhas"
    nc = str(obj.columns.name) if obj.columns.name is not None else "colunas"
    com_totais = "All" in obj.index and "All" in obj.columns     # margins=True: o pandas já somou
    corpo = obj.drop(index="All", columns="All") if com_totais else obj
    maior = corpo.values.max() if corpo.size else 0
    cols = list(obj.columns)
    LR = max([len(f"{nl} ↓  {nc} →")] + [_largura(_rotulo_valor(r)) for r in obj.index]) + 2
    W = max([6] + [len(str(c)) for c in cols] + [len(str(int(v))) + 2 for v in obj.values.ravel()]) + 3
    tons = [(60, 66, 76), (45, 85, 130), (40, 110, 180), (74, 163, 239)]

    def celula(n, total):
        if total:
            return _c(str(n), "cinza")
        if n == 0:
            return _c(" · ", "cinza")
        r, g, b = tons[min(3, int(n / maior * 3.999))]
        return f"\x1b[48;2;{r};{g};{b}m\x1b[1m {n} \x1b[0m"
    cab = lambda t, cor: _encher(_c(t, cor, cor == "azul"), W - 1, ">") + " "
    L = [_encher(_c(f"{nl} ↓  {nc} →", "cinza"), LR) + "".join(cab(str(c), "cinza" if c == "All" else "azul")
                                                            for c in cols)
         + ("" if com_totais else cab("total", "cinza")), "---"]
    for r in obj.index:
        linha = obj.loc[r]
        e_total = com_totais and r == "All"
        cels = "".join(_encher(celula(int(linha[c]), e_total or c == "All"), W, ">") if not (e_total or c == "All")
                       else cab(str(int(linha[c])), "cinza") for c in cols)
        if e_total:
            L.append("---")
        L.append(_encher(_rotulo_valor(r) if not e_total else _c("All", "cinza"), LR) + cels
                 + ("" if com_totais else cab(str(int(linha.sum())), "cinza")))
    if not com_totais:
        L += ["---", _encher(_c("total", "cinza"), LR) + "".join(cab(str(int(obj[c].sum())), "cinza") for c in cols)
              + cab(str(int(obj.values.sum())), "cinza")]
    n = int(corpo.values.sum())
    saida = [_cabecalho("crosstab", f"{nl} × {nc}", f"{corpo.shape[0]} × {corpo.shape[1]}", f"{n} linhas contadas"), ""]
    saida += _caixa(f"{nl} × {nc}", L, max(_largura(x) for x in L) + 4)
    saida.append(_c("fundo mais forte = contagem maior · ", "cinza") + _c("·", "cinza")
                 + _c(" = zero · " + ("All = os totais que o pandas somou (margins=True)" if com_totais else
                                      "os totais (cinza) são a soma da própria tabela"), "cinza"))
    return "\n".join(saida)


# ── df.corr() ───────────────────────────────────────────────────────────────

def _corr(obj, linha):
    cols = list(obj.columns)
    if list(obj.index) != cols or len(cols) > 15:
        return None
    W = max([9] + [len(str(c)) for c in cols]) + 2
    LR = max(len(str(c)) for c in cols) + 2

    def celula(v, diag):
        if diag:
            return _c("1", "cinza")
        if v != v:
            return _c("NaN", "laranja")
        base = (74, 163, 239) if v >= 0 else (190, 150, 230)
        f = 0.25 + 0.75 * abs(v)
        r, g, b = (int(x * f + 29 * (1 - f)) for x in base)
        return f"\x1b[48;2;{r};{g};{b}m\x1b[1m{v:+.2f}\x1b[0m"
    L = [_encher("", LR) + "".join(_encher(_c(str(c), "azul", True), W, ">") for c in cols), "---"]
    for i, r in enumerate(cols):
        L.append(_encher(_c(str(r), "azul", True), LR)
                 + "".join(_encher(celula(obj.iat[i, j], i == j), W, ">") for j in range(len(cols))))
    m = re.search(r"corr\(\s*(?:method\s*=\s*)?[\"'](\w+)[\"']", linha)
    metodo = m.group(1) if m else "pearson (o padrão do pandas)"
    saida = [_cabecalho("corr()", f"{len(cols)} colunas", metodo), ""]
    saida += _caixa("correlação", L, max(_largura(x) for x in L) + 4)
    saida.append(_c("+0.80", "azul") + _c(" sobem juntas · ", "cinza") + _c("−0.80", "lilas")
                 + _c(" uma sobe, a outra desce · fundo mais forte = mais perto de ±1 · a metade de cima é espelho da de baixo",
                      "cinza"))
    saida.append(_c("o pandas ignora os pares com vazio: cada número pode ter vindo de um n diferente", "cinza"))
    return "\n".join(saida)


# ── os moldes genéricos, reduções, pivot, faixas e índice ─────────────────

def _procura(quadro, nome):
    if nome in quadro.f_locals:
        return quadro.f_locals[nome]
    return quadro.f_globals.get(nome)


def _expressao(linha):
    """print(df.sort_values("x"))  →  ("df", '.sort_values("x")'). Só lê o texto da linha."""
    m = re.match(r"^\s*print\(\s*(.*?)\s*\)\s*(#.*)?$", linha)
    if not m:
        return None, None
    dentro = m.group(1)
    n = re.match(r"([A-Za-z_]\w*)(.*)$", dentro)
    return (n.group(1), n.group(2)) if n else (None, dentro)


def _mudancas(antes, depois):
    """O que mudou entre o df da linha e o resultado — só contagem, nenhum julgamento."""
    partes = []
    if len(antes) != len(depois):
        d = len(antes) - len(depois)
        partes.append(f"linhas {len(antes)} → {len(depois)}"
                      + (_c(f" ({d} {'saiu' if d == 1 else 'saíram'})", "laranja") if d > 0 else f" ({-d} a mais)"))
    elif antes.index.equals(depois.index):
        partes.append(f"{len(depois)} linhas")
    elif set(antes.index) == set(depois.index):
        partes.append("mesmas linhas, em outra ordem")
    else:
        partes.append(f"{len(depois)} linhas")
    pd = sys.modules["pandas"]
    if isinstance(antes, pd.DataFrame) and isinstance(depois, pd.DataFrame):
        novas = [c for c in depois.columns if c not in antes.columns]
        sairam = [c for c in antes.columns if c not in depois.columns]
        if novas or sairam:
            p = f"colunas {antes.shape[1]} → {depois.shape[1]}"
            if novas:
                p += " (+ " + ", ".join(_c(str(c), "azul") for c in novas[:8]) + (" …" if len(novas) > 8 else "") + ")"
            if sairam:
                p += " (− " + ", ".join(_c(str(c), "laranja") for c in sairam[:8]) + (" …" if len(sairam) > 8 else "") + ")"
            partes.append(p)
        else:
            partes.append(f"{depois.shape[1]} colunas")
    va, vd = int(antes.isna().sum().sum()), int(depois.isna().sum().sum())
    if va != vd:
        partes.append(_c(f"vazios {va} → {vd}", "laranja"))
    return partes


# ── 17. qualquer tabela transformada ───────────────────────────────────────

def _generico(obj, antes, expressao):
    if not len(obj.columns) or not len(obj):
        return None
    if antes is not None:
        partes = _mudancas(antes, obj)
    else:
        partes = [f"{len(obj)} linhas", f"{obj.shape[1]} colunas"]
    metodo = re.search(r"([A-Za-z_]\w*)\(", expressao or "")
    rotulo = metodo.group(1) if metodo else "DataFrame"
    saida = [_cabecalho("DataFrame", _c(expressao, "amarelo") if expressao else "", *partes), ""]
    saida += _grade(obj, rotulo)
    return "\n".join(saida)


# ── 18. qualquer coluna transformada ───────────────────────────────────────

def _serie_generica(obj, antes, expressao):
    if not len(obj):
        return None
    partes = [f'"{obj.name}"' if obj.name is not None else "", _c(expressao, "amarelo") if expressao else ""]
    if antes is not None and antes.dtype != obj.dtype:
        partes.append(f"{antes.dtype} → {obj.dtype}")
    else:
        partes.append(str(obj.dtype))
    if obj.dtype == bool:                                 # verdadeiro/falso: o que descreve é quantos de cada
        sim = int(obj.sum())
        partes += [f"{len(obj)} linhas", _c(f"{sim} True", "amarelo"), f"{len(obj) - sim} False"]
        corpo = _serie(obj).split("\n", 2)[2]
        return "\n".join([_cabecalho("Series", *partes), "", corpo])
    if antes is not None and len(antes) == len(obj) and antes.index.equals(obj.index):
        try:
            mudou = int(((antes != obj) & ~(antes.isna() & obj.isna())).sum())
            partes.append(f"{mudou} de {len(obj)} valores mudaram")
        except (TypeError, ValueError):
            pass
    if antes is not None:
        partes += [p for p in _mudancas(antes, obj) if "vazios" in p or "→" in p]
    else:
        vaz = int(obj.isna().sum())
        partes += [f"{len(obj)} linhas", _c(f"{vaz} vazios", "laranja") if vaz else "0 vazios"]
    corpo = _serie(obj).split("\n", 2)[2]
    return "\n".join([_cabecalho("Series", *partes), "", corpo])


# ── 19. reduções: um número por coluna ─────────────────────────────────────

NOMES_REDUCAO = {"mean": "média", "sum": "soma", "count": "preenchidos", "min": "mínimo", "max": "máximo",
                 "std": "desvio padrão", "median": "mediana", "var": "variância", "quantile": "quantil",
                 "prod": "produto", "any": "algum verdadeiro", "all": "todos verdadeiros",
                 "idxmax": "linha do máximo", "idxmin": "linha do mínimo", "skew": "assimetria",
                 "sem": "erro padrão", "memory_usage": "memória (bytes)"}


def _reducao(obj, df, func):
    nomes = max(len(str(c)) for c in obj.index) + 2
    itens = []
    for c, v in obj.items():
        cor = _cor_tipo(df[c].dtype) if c in df.columns and df.columns.is_unique else None
        n = int(df[c].notna().sum()) if cor is not None else None
        if v is None or (isinstance(v, float) and v != v):
            val, nota = _c(f"{'NaN':>12}", "laranja"), _c("  coluna vazia" if n == 0 else "", "cinza")
        else:
            val = _c(f"{_n(v) if isinstance(v, (int, float)) else str(v):>12}", negrito=True)
            nota = ""
            if n is not None and func not in ("count", "memory_usage"):
                nota = _c(f"  de {n}", "cinza") + (_c(f" ({len(df) - n} vaz.)", "laranja") if n < len(df) else "")
        itens.append(_encher(_c(str(c), cor), nomes) + val + nota)
    nome = NOMES_REDUCAO.get(func, func)
    saida = [_cabecalho(func + "()", nome + " de cada coluna", f"{len(obj)} colunas", f"{len(df)} linhas"), ""]
    saida += _caixas(nome, _em_colunas(itens, 2))
    if func not in ("count", "memory_usage"):
        saida.append(_c('"de N" = quantos valores entraram na conta: o pandas pula os vazios sem avisar', "cinza"))
    return "\n".join(saida)


# ── 20. pivot_table / unstack: tabela larga ────────────────────────────────

def _pivot(obj, linha):
    pd = sys.modules["pandas"]
    if obj.shape[0] > 60 or obj.shape[1] > 15 or not all(obj[c].dtype.kind in "iuf" for c in obj.columns):
        return None
    nl = str(obj.index.name) if obj.index.name is not None else "linhas"
    nc = str(obj.columns.name) if obj.columns.name is not None else "colunas"
    m = re.search(r"values\s*=\s*[\"'](.+?)[\"']", linha) or re.search(r"\[\s*[\"']([^\"']+)[\"']\s*\]\s*\.\w+\(\)\s*\.unstack",
                                                                           linha)
    valores = m.group(1) if m else "valores"
    m = re.search(r"aggfunc\s*=\s*[\"'](\w+)[\"']", linha) or re.search(r"\.(\w+)\(\)\s*\.unstack", linha)
    func = m.group(1) if m else ("mean" if "pivot_table" in linha else "")
    vals = obj.values[~pd.isna(obj.values)]
    lo, hi = (vals.min(), vals.max()) if len(vals) else (0, 0)
    cols = list(obj.columns)
    W = max([8] + [len(str(c)) for c in cols] + [len(_n(v)) + 2 for v in vals]) + 3
    LR = max([len(f"{nl} ↓  {nc} →")] + [_largura(_rotulo_valor(r)) for r in obj.index]) + 2

    def celula(v):
        if v != v:
            return _c("NaN", "laranja") + " "
        f = (v - lo) / (hi - lo) if hi > lo else 1
        r, g, b = (int(29 + (x - 29) * (0.2 + 0.8 * f)) for x in (74, 163, 239))
        return f"\x1b[48;2;{r};{g};{b}m\x1b[1m {_n(v)} \x1b[0m"
    L = [_encher(_c(f"{nl} ↓  {nc} →", "cinza"), LR)
         + "".join(_encher(_c(str(c), "azul", True), W - 1, ">") + " " for c in cols), "---"]
    for r, valores_linha in zip(obj.index, obj.itertuples(index=False)):
        L.append(_encher(_rotulo_valor(r), LR) + "".join(_encher(celula(v), W, ">") for v in valores_linha))
    rotulo = "pivot_table" if "pivot_table" in linha else ("unstack" if "unstack" in linha else "pivot")
    titulo = f"{NOMES_FUNC.get(func, func)} de {valores}" if func else valores
    saida = [_cabecalho(rotulo, titulo, f"linhas: {nl}", f"colunas: {nc}", f"{obj.shape[0]} × {obj.shape[1]}"), ""]
    saida += _caixa(f"{valores} · {nl} × {nc}", L, max(_largura(x) for x in L) + 4)
    saida.append(_c("fundo mais forte = valor maior (de ", "cinza") + _c(_n(lo), "azul") + _c(" a ", "cinza")
                 + _c(_n(hi), "azul") + _c(")   ", "cinza") + _c("NaN", "laranja")
                 + _c(" = combinação que não existe no dado (no groupby ela some; aqui ela vira um buraco)", "cinza"))
    return "\n".join(saida)


# ── 21. pd.cut / pd.qcut: faixas ───────────────────────────────────────────

def _faixas(obj, origem, funcao):
    cats = list(obj.cat.categories)
    if not cats or len(cats) > 40:
        return None
    cont = obj.value_counts(sort=False)
    vaz = int(obj.isna().sum())
    maior = cont.max() if len(cont) else 0
    LF = max(len(str(c)) for c in cats) + 2
    L = [_encher(_c("faixa", "cinza"), LF) + _c("   quantos", "cinza"), "---"]
    for c in cats:
        n = int(cont.get(c, 0))
        cheio = round(n / maior * 20) if maior else 0
        L.append(_encher(_c(str(c), "azul", True), LF) + f"{n:>10}  " + _c("█" * cheio, "azul")
                 + _c("─" * (20 - cheio), "borda"))
    if vaz:
        L.append(_encher(_c("NaN", "laranja"), LF) + _c(f"{vaz:>10}", "laranja")
                 + _c("  ficou fora de todas as faixas", "cinza"))
    a = cats[0]
    fecha = "]" if str(a).endswith("]") else ")"
    if fecha == "]":
        L += ["---", _c(f"{a} = maior que {a.left:g} e até {a.right:g}, incluindo o {a.right:g}", "cinza")]
    else:
        L += ["---", _c(f"{a} = a partir de {a.left:g}, incluindo, e menor que {a.right:g}", "cinza")]
    caixa_faixas = _caixa("as faixas, em ordem", L, max(_largura(x) for x in L) + 4)
    cortar = _corte(len(obj))
    pos = list(range(cortar[0])) + list(range(len(obj) - cortar[1], len(obj))) if cortar else range(len(obj))
    linhas = []
    for k, i in enumerate(pos):
        if cortar and k == cortar[0]:
            linhas.append(_c(f"  ⋮  {len(obj) - cortar[0] - cortar[1]} linhas não mostradas", "cinza"))
        v = origem.iloc[i] if origem is not None else None
        f = obj.iloc[i]
        linhas.append(_encher(_c(str(obj.index[i]), "cinza"), 6)
                      + (_encher(_rotulo_valor(v), 10, ">") + _c("  →  ", "cinza") if origem is not None else "")
                      + (_c("NaN", "laranja") if f != f else _c(str(f), "azul")))
    caixa_linhas = _caixa("cada valor, na sua faixa", linhas, max(_largura(x) for x in linhas) + 4)
    saida = [_cabecalho(funcao, f'"{obj.name}"' if obj.name is not None else "", f"{len(cats)} faixas",
                        f"{len(obj)} linhas", "category"), ""]
    saida += _lado_a_lado([caixa_linhas, caixa_faixas])
    saida.append(_c("( = aberto: o número não entra · ] = fechado: o número entra", "cinza"))
    return "\n".join(saida)


# ── 22. o índice ────────────────────────────────────────────────────────────

def _indice(obj, rotulo):
    pd = sys.modules["pandas"]
    partes = ["index", rotulo, f"{len(obj)} posições", str(obj.dtype)]
    if isinstance(obj, pd.RangeIndex):
        corpo = [_c(f"de {obj.start} a {obj.stop - obj.step}, de {obj.step} em {obj.step}" if len(obj)
                    else "vazio", negrito=True),
                 _c(f"RangeIndex: o pandas guarda só a regra (start={obj.start}, stop={obj.stop}, step={obj.step}),",
                    "cinza"),
                 _c(f"não os {len(obj)} números — o stop não entra", "cinza")]
        return "\n".join([_cabecalho(*partes), ""] + _caixa("RangeIndex", corpo, max(_largura(x) for x in corpo) + 4))
    if len(obj) > 500 or obj.nlevels != 1:
        return None
    repetidos = list(obj[obj.duplicated(keep=False)].unique())
    partes.append(_c(f"{len(repetidos)} repetido{'s' if len(repetidos) != 1 else ''}", "laranja") if repetidos
                  else "todos únicos")
    rep = set(repetidos)
    itens = [_c(f"{i:>3} ", "cinza") + (_c(_rotulo_valor(v), "laranja", True) if v in rep else _rotulo_valor(v))
             for i, v in enumerate(obj)]
    saida = [_cabecalho(*partes), ""]
    saida += _caixas("posições", _em_colunas(itens, 3))
    if repetidos:
        saida.append(_c("em laranja, os valores que aparecem mais de uma vez: ", "cinza")
                     + ", ".join(_c(_rotulo_valor(v), "laranja") for v in repetidos[:10])
                     + _c(" — df.loc[valor] vai devolver mais de uma linha", "cinza"))
    return "\n".join(saida)


# ── o tempo ────────────────────────────────────────────────────────────────

DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
BLOCOS = "▁▂▃▄▅▆▇█"
CODIGOS = [("D", "cada dia"), ("W", "cada semana, fechando no domingo (o mesmo que W-SUN)"),
           ("MS", "primeiro dia de cada mês"), ("ME", "último dia de cada mês (era M até o pandas 2.1)"),
           ("QE", "último dia de cada trimestre (era Q)"), ("YE", "último dia de cada ano (era Y / A)"),
           ("h", "cada hora (era H)")]


def _data(v, com_dia=True):
    if v is None or v != v:
        return _c("NaT", "laranja")
    txt = str(v.date()) if v == v.normalize() else str(v)
    return _c(txt, "verde") + (_c(" " + DIAS[v.weekday()], "cinza") if com_dia else "")


def _faixa_datas(idx):
    v = idx.dropna()
    if not len(v):
        return []
    lo, hi = v.min(), v.max()
    return [f"de {lo.date()} a {hi.date()}", f"{(hi - lo).days} dias de extensão"]


def _faisca(valores, largura=60):
    """Uma linha de ▁▂▃▄▅▆▇█: a forma da série inteira. Vazio vira espaço."""
    np = sys.modules["numpy"]
    v = np.asarray(valores, dtype=float)
    if len(v) > largura:                                   # comprime tirando a média de cada pedaço
        pedacos = np.array_split(v, largura)
        v = np.array([np.nanmean(p) if np.isfinite(p).any() else np.nan for p in pedacos])
    ok = v[np.isfinite(v)]
    if not len(ok):
        return ""
    lo, hi = ok.min(), ok.max()
    return "".join(" " if not np.isfinite(x) else BLOCOS[int((x - lo) / (hi - lo) * 7) if hi > lo else 3] for x in v)


# ── 23. to_datetime e .dt ───────────────────────────────────────────────────

def _conversao(antes, depois, expressao):
    if len(antes) > 500:
        return None
    vaz_antes, vaz_dep = int(antes.isna().sum()), int(depois.isna().sum())
    partes = [f'"{antes.name}"', _c(expressao, "amarelo"), f"{antes.dtype} → {depois.dtype}"]
    partes += _faixa_datas(depois)
    partes.append(f"{depois.nunique()} datas diferentes")
    if vaz_dep > vaz_antes:
        partes.append(_c(f"{vaz_dep - vaz_antes} não viraram data (NaT)", "laranja"))
    cortar = _corte(len(depois))
    pos = list(range(cortar[0])) + list(range(len(depois) - cortar[1], len(depois))) if cortar else range(len(depois))
    LA = max([len("antes")] + [_largura(_rotulo_valor(antes.iloc[i])) for i in pos]) + 2
    LI = max(len(str(depois.index[i])) for i in pos) + 2
    L = [_encher("", LI) + _encher(_c("antes", "cinza"), LA) + "     " + _c("depois", "cinza"), "---"]
    for k, i in enumerate(pos):
        if cortar and k == cortar[0]:
            L.append(_c(f"  ⋮  {len(depois) - cortar[0] - cortar[1]} linhas não mostradas", "cinza"))
        L.append(_encher(_c(str(depois.index[i]), "cinza"), LI) + _encher(_rotulo_valor(antes.iloc[i]), LA)
                 + _c("  →  ", "cinza") + _data(depois.iloc[i]))
    saida = [_cabecalho("to_datetime", *partes), ""]
    saida += _caixa("cada texto e a data que ele virou", L, max(_largura(x) for x in L) + 4)
    saida.append(_c("ao lado de cada data, o dia da semana: se você sabe que pesou numa segunda e aparece outro dia,",
                    "cinza"))
    saida.append(_c("o dia e o mês podem ter trocado de lugar (03/02 lido como 2 de março)", "cinza"))
    return "\n".join(saida)


def _dt(origem, depois, expressao):
    cortar = _corte(len(depois))
    pos = list(range(cortar[0])) + list(range(len(depois) - cortar[1], len(depois))) if cortar else range(len(depois))
    LI = max(len(str(depois.index[i])) for i in pos) + 2
    rotulo = expressao.lstrip(".")
    L = [_encher("", LI) + _encher(_c("data", "cinza"), 18) + "     " + _c(rotulo, "cinza"), "---"]
    for k, i in enumerate(pos):
        if cortar and k == cortar[0]:
            L.append(_c(f"  ⋮  {len(depois) - cortar[0] - cortar[1]} linhas não mostradas", "cinza"))
        v = depois.iloc[i]
        L.append(_encher(_c(str(depois.index[i]), "cinza"), LI) + _encher(_data(origem.iloc[i]), 18)
                 + _c("  →  ", "cinza") + (_c("NaN", "laranja") if v is None or v != v else _c(str(v), "azul", True)))
    saida = [_cabecalho(".dt", f'"{origem.name}"', _c(expressao, "amarelo"), str(depois.dtype),
                        f"{len(depois)} linhas"), ""]
    saida += _caixa("de cada data, a parte que você pediu", L, max(_largura(x) for x in L) + 4)
    return "\n".join(saida)


# ── 24. DatetimeIndex ───────────────────────────────────────────────────────

def _eixo(idx):
    pd, np = sys.modules["pandas"], sys.modules["numpy"]
    if len(idx) > 2000:
        return None
    ordenado = sorted(idx.dropna())
    passos = pd.Series(np.diff(ordenado)) if len(ordenado) > 1 else pd.Series([], dtype="timedelta64[ns]")
    passos = passos[passos > pd.Timedelta(0)]
    passo = passos.mode().iloc[0] if len(passos) else None
    if idx.is_monotonic_increasing:
        ordem = "crescente"
    elif idx.is_monotonic_decreasing:
        ordem = _c("decrescente", "amarelo")
    else:
        ordem = _c("fora de ordem", "laranja")
    buracos = []
    if passo is not None:
        try:
            esperado = pd.date_range(ordenado[0], ordenado[-1], freq=passo)
            tem = set(ordenado)
            buracos = [d for d in esperado if d not in tem] if len(esperado) <= 20000 else []
        except (ValueError, TypeError):
            buracos = []
    repetidas = int(idx.duplicated().sum())
    try:
        deduzido = pd.infer_freq(idx)
    except (TypeError, ValueError):
        deduzido = None
    if passo is None:
        txt_passo = ""
    elif passo % pd.Timedelta(days=1) == pd.Timedelta(0):
        txt_passo = f"passo mais comum: {passo.days} dia{'s' if passo.days != 1 else ''}"
    else:
        txt_passo = f"passo mais comum: {passo}"
    partes = ["DatetimeIndex", f"{len(idx)} datas", *_faixa_datas(idx), "ordem: " + ordem, txt_passo,
              _c(f"{len(buracos)} buraco{'s' if len(buracos) != 1 else ''}", "laranja") if buracos else "sem buracos"]
    if repetidas:
        partes.append(_c(f"{repetidas} repetidas", "laranja"))
    partes.append("ritmo: " + (_c(deduzido, "amarelo") if deduzido else _c("não dá para deduzir", "laranja")))
    if len(idx) <= 60:
        itens = [_c(f"{i:>3} ", "cinza") + _data(d) for i, d in enumerate(idx)]
    else:
        itens = ([_c(f"{i:>4} ", "cinza") + _data(idx[i]) for i in range(5)]
                 + [_c(f"  ⋮  {len(idx) - 10} datas", "cinza")]
                 + [_c(f"{i:>4} ", "cinza") + _data(idx[i]) for i in range(len(idx) - 5, len(idx))])
    saida = [_cabecalho(*partes), ""]
    saida += _caixas("datas", _em_colunas(itens, 3))
    if buracos:
        mostra = ", ".join(_data(d, False) for d in buracos[:12]) + (_c(f" … e mais {len(buracos) - 12}", "cinza")
                                                                     if len(buracos) > 12 else "")
        saida.append(_c("faltando, pelo passo mais comum: ", "cinza") + mostra)
    saida.append(_c("ritmo = o que pd.infer_freq deduz das datas. O freq=None que o pandas imprime é o normal para datas",
                    "cinza"))
    saida.append(_c("vindas de arquivo — ele não grava o ritmo. Um buraco ou uma data repetida já impedem a dedução;",
                    "cinza"))
    saida.append(_c("a ordem decrescente não: ela vira um ritmo negativo (-1W-SUN).", "cinza"))
    return "\n".join(saida)


# ── 25. date_range e to_period ──────────────────────────────────────────────

def _date_range(idx, codigo):
    if len(idx) > 500:
        return None
    itens = [_c(f"{i:>3} ", "cinza") + _data(d) for i, d in enumerate(idx)]
    sig = dict(CODIGOS).get(codigo, "")
    saida = [_cabecalho("date_range", f"freq={_c(codigo, 'amarelo')}", sig, f"{len(idx)} datas", *_faixa_datas(idx)),
             ""]
    saida += _caixas("datas", _em_colunas(itens, 3))
    L = [_encher(_c(c, "amarelo" if c == codigo else "azul", True), 8) + _c(t, "cinza") for c, t in CODIGOS]
    saida += [""] + _caixa("os códigos mais usados (pandas 3)", L, max(_largura(x) for x in L) + 4)
    return "\n".join(saida)


def _period(origem, depois, codigo):
    cortar = _corte(len(depois))
    pos = list(range(cortar[0])) + list(range(len(depois) - cortar[1], len(depois))) if cortar else range(len(depois))
    LI = max(len(str(depois.index[i])) for i in pos) + 2
    L = [_encher("", LI) + _encher(_c("data", "cinza"), 18) + "     " + _c("período", "cinza"), "---"]
    for k, i in enumerate(pos):
        if cortar and k == cortar[0]:
            L.append(_c(f"  ⋮  {len(depois) - cortar[0] - cortar[1]} linhas não mostradas", "cinza"))
        p = depois.iloc[i]
        resto = (_c("NaT", "laranja") if p is None or p != p else
                 _c(str(p), "azul", True) + _c(f"   de {p.start_time.date()} a {p.end_time.date()}", "cinza"))
        L.append(_encher(_c(str(depois.index[i]), "cinza"), LI) + _encher(_data(origem.iloc[i]), 18)
                 + _c("  →  ", "cinza") + resto)
    n = depois.nunique()
    saida = [_cabecalho("to_period", f'freq={_c(codigo, "amarelo")}',
                        f"{n} período{'s' if n != 1 else ''} diferente{'s' if n != 1 else ''}", str(depois.dtype)), ""]
    saida += _caixa("cada data e o período que a contém", L, max(_largura(x) for x in L) + 4)
    exemplo = next((p for p in depois if p == p), None)
    if exemplo is not None:
        saida.append(_c(f"um Period não é um dia: é o intervalo inteiro — {exemplo} vai de {exemplo.start_time.date()} "
                        f"a {exemplo.end_time.date()}", "cinza"))
    return "\n".join(saida)


# ── 26. resample ────────────────────────────────────────────────────────────

def _resample(serie, obj, codigo, func):
    if len(obj) > 120 or obj.dtype.kind not in "iuf":
        return None
    try:
        cont = serie.resample(codigo).count()
    except (ValueError, TypeError):
        return None
    if len(cont) != len(obj):
        return None
    validos = obj.dropna()
    maior = validos.abs().max() if len(validos) else 0
    cheio_max = cont.max()
    L = [_encher(_c("balde", "cinza"), 18) + _c(f"{func:>9}", "cinza") + _c("   de N", "cinza"), "---"]
    for (d, v), n in zip(obj.items(), cont):
        if v != v:
            L.append(_encher(_data(d), 18) + _c(f"{'NaN':>9}", "laranja") + _c(f"   de {n}", "laranja"))
            continue
        cheio = round(abs(v) / maior * 24) if maior else 0
        L.append(_encher(_data(d), 18) + _c(f"{_n(v):>9}", negrito=True)
                 + _c(f"   de {n}", "cinza" if n == cheio_max else "laranja") + "  "
                 + _c("█" * cheio, "azul") + _c("─" * (24 - cheio), "borda"))
    sig = dict(CODIGOS).get(codigo, "")
    saida = [_cabecalho("resample", f"freq={_c(codigo, 'amarelo')}", sig, f"{func} de cada balde",
                        f"{len(serie)} linhas → {len(obj)} baldes"), ""]
    saida += _caixa(f"{serie.name} por balde" if serie.name is not None else "por balde", L,
                    max(_largura(x) for x in L) + 4)
    saida.append(_c("o rótulo é o dia que FECHA o balde nos códigos de fim (W, ME, QE, YE) · ", "cinza")
                 + _c("de N em laranja", "laranja") + _c(" = balde com menos valores que o mais cheio", "cinza"))
    return "\n".join(saida)


# ── 27. rolling, diff, shift, pct_change ───────────────────────────────────

def _janela(serie, obj, expressao, linha):
    pd = sys.modules["pandas"]
    if len(serie) != len(obj) or obj.dtype.kind not in "iuf" or serie.dtype.kind not in "iuf":
        return None
    em_data = isinstance(serie.index, pd.DatetimeIndex)
    if em_data:
        ordem = ("crescente" if serie.index.is_monotonic_increasing else
                 _c("decrescente", "amarelo") if serie.index.is_monotonic_decreasing else _c("fora de ordem", "laranja"))
    else:
        ordem = "crescente" if serie.index.is_monotonic_increasing else _c("não crescente", "amarelo")
    novos = int(obj.isna().sum()) - int(serie.isna().sum())
    m = re.search(r"\.rolling\(\s*(\d+)", linha)
    if m:
        explica = f"a janela de {m.group(1)} só enche na linha {m.group(1)} — antes disso não há valores bastantes"
    elif re.search(r"\.(diff|pct_change)\(", linha):
        explica = "essas linhas não têm a anterior para comparar"
    elif re.search(r"\.shift\(", linha):
        explica = "os valores foram empurrados: essas posições ficaram sem nada"
    else:
        explica = ""
    rotulo = (re.search(r"\.(\w+)\(", expressao or "") or re.search(r"(\w+)", "resultado")).group(1)
    L = [_encher(_c("original", "cinza"), 12) + _c(_faisca(serie), "branco"),
         _encher(_c(rotulo, "cinza"), 12) + _c(_faisca(obj), "azul"), "---"]
    n = len(obj)
    mostra = list(range(n)) if n <= 15 else list(range(8)) + list(range(n - 3, n))
    LI = 18 if em_data else max(len(str(i)) for i in serie.index) + 2
    for k, i in enumerate(mostra):
        if n > 15 and k == 8:
            L.append(_c(f"  ⋮  {n - 11} linhas não mostradas", "cinza"))
        v0, v1 = serie.iloc[i], obj.iloc[i]
        rot = _data(serie.index[i]) if em_data else _c(str(serie.index[i]), "cinza")
        L.append(_encher(rot, LI) + (f"{_n(v0):>10}" if v0 == v0 else _c(f"{'NaN':>10}", "laranja"))
                 + _c("  →  ", "cinza") + (_c(f"{'NaN':>10}", "laranja") if v1 != v1 else _c(f"{_n(v1):>10}", negrito=True)))
    partes = ["Series", f'"{serie.name}"' if serie.name is not None else "", _c(expressao, "amarelo"), f"{n} linhas",
              "ordem do índice: " + ordem]
    partes.append(_c(f"{novos} NaN novo{'s' if novos != 1 else ''}", "laranja") if novos > 0 else "nenhum NaN novo")
    saida = [_cabecalho(*partes), ""]
    saida += _caixa(rotulo, L, max(_largura(x) for x in L) + 4)
    if novos > 0 and explica:
        saida.append(_c("NaN novos: ", "laranja") + _c(explica, "cinza"))
    saida.append(_c("as duas linhas de ▁▂▃▄▅▆▇█ são a série inteira, comprimida na largura da tela, cada uma na sua escala",
                    "cinza"))
    return "\n".join(saida)


# ── a tupla: print(f()) com return a, b, c — ou x = a, b — cada item relido sozinho ─────────

class _Quadro:
    """Um quadro de mentira: só os nomes que a releitura procura (f_locals e f_globals)."""

    def __init__(self, f_locals, f_globals):
        self.f_locals, self.f_globals = f_locals, f_globals


def _e_do_pandas(v):
    pd = sys.modules.get("pandas")
    return pd is not None and isinstance(v, (pd.DataFrame, pd.Series, pd.Index))


def _itens_do_codigo(no, fonte, n):
    """Os textos de cada item de uma tupla escrita no código, se ela tiver n itens."""
    import ast
    if isinstance(no, ast.Tuple) and len(no.elts) == n:
        return [ast.get_source_segment(fonte, e) for e in no.elts]
    return None


def _expressoes(obj, linha, quadro, arquivo, numero):
    """De onde veio cada item: lê a linha do print e, se preciso, o return da função ou a
    atribuição da variável. Só lê o código — nada é executado. None se não der para saber."""
    import ast
    import inspect
    import textwrap
    m = re.match(r"^\s*print\((.*)\)\s*(#.*)?$", linha)
    if not m:
        return None, None
    dentro = m.group(1).strip()
    try:
        arvore = ast.parse(dentro, mode="eval").body
    except SyntaxError:
        return None, None
    n = len(obj)
    # print((a, b)) — a tupla está escrita ali mesmo
    achou = _itens_do_codigo(arvore, dentro, n)
    if achou:
        return achou, dentro
    # print(f(...)) — os itens estão no return de f
    if isinstance(arvore, ast.Call) and isinstance(arvore.func, ast.Name):
        funcao = quadro.f_locals.get(arvore.func.id, quadro.f_globals.get(arvore.func.id))
        try:
            fonte = textwrap.dedent(inspect.getsource(funcao))
            corpo = ast.parse(fonte).body[0]
        except (TypeError, OSError, SyntaxError, IndexError):
            return None, dentro
        candidatos = []
        for no in ast.walk(corpo):
            if isinstance(no, ast.Return) and no.value is not None:
                itens = _itens_do_codigo(no.value, fonte, n)
                if itens:
                    candidatos.append(itens)
        return (candidatos[0] if len(candidatos) == 1 else None), dentro
    # print(x) — os itens estão na última atribuição de x antes desta linha
    if isinstance(arvore, ast.Name):
        try:
            fonte = "".join(linecache.getlines(arquivo))
            modulo = ast.parse(fonte)
        except (SyntaxError, ValueError):
            return None, dentro
        melhor = None
        for no in ast.walk(modulo):
            if (isinstance(no, ast.Assign) and no.lineno < numero and len(no.targets) == 1
                    and isinstance(no.targets[0], ast.Name) and no.targets[0].id == arvore.id):
                if melhor is None or no.lineno > melhor.lineno:
                    melhor = no
        if melhor is not None:
            return _itens_do_codigo(melhor.value, fonte, n), dentro
    return None, dentro


def _tupla(obj, linha, quadro, arquivo, numero):
    exprs, dentro = _expressoes(obj, linha, quadro, arquivo, numero)
    n = len(obj)
    # um item que é só um nome (o df do return df, ...) passa a existir para os outros itens
    nomes = dict(quadro.f_globals)
    nomes.update(quadro.f_locals)
    if exprs:
        for e, v in zip(exprs, obj):
            if e and re.fullmatch(r"[A-Za-z_]\w*", e):
                nomes[e] = v
    falso = _Quadro(nomes, quadro.f_globals)
    saida = [_cabecalho("tupla", f"{n} itens", _c(dentro, "amarelo") if dentro else ""), ""]
    tela = _tela()
    for i, v in enumerate(obj):
        expr = exprs[i] if exprs else None
        rotulo = expr or type(v).__name__
        titulo = f"── {i + 1}/{n} · "
        saida.append(_c(titulo, "borda") + _c(rotulo, "amarelo", True) + " "
                     + _c("─" * max(3, min(60, tela - len(titulo) - len(rotulo) - 2)), "borda"))
        texto = None
        try:
            if expr and expr.endswith(".shape") and isinstance(v, tuple) and all(isinstance(k, int) for k in v):
                texto = _shape(v)
            elif expr:
                texto = _reler_linha(v, f"print({expr})\n", falso)
            elif _e_do_pandas(v):
                texto = _reler_linha(v, "print(item)\n", _Quadro({"item": v}, {}))
        except Exception:
            texto = None
        saida.append(texto if texto is not None else str(v))
        saida.append("")
    saida.append(_c("cada item da tupla relido sozinho; o nome vem do seu código (o return, a atribuição ou o próprio print)",
                    "cinza"))
    return "\n".join(saida)
