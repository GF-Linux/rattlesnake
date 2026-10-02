"""O comando ratt: instalar, ver, atualizar e remover a releitura.

ratt -h imprime e sai, como git -h e dnf -h; ratt sozinho, num terminal, abre o menu.
"""
import os
import sys
from pathlib import Path

from ratt import REPOSITORIO, __version__
from ratt import alvo
from ratt import atualizar

# ── cor, só quando faz sentido ─────────────────────────────────────────────

_COR = {"azul": "38;2;74;163;239", "amarelo": "38;2;240;180;60", "laranja": "38;2;240;138;60",
        "cinza": "38;2;125;133;146", "verde": "38;2;110;200;140", "negrito": "1"}


def _tem_cor():
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def c(texto, *cores):
    if not _tem_cor() or not cores:
        return str(texto)
    return "".join(f"\x1b[{_COR[k]}m" for k in cores) + str(texto) + "\x1b[0m"


def _casa(caminho):
    s = str(caminho)
    casa = os.path.expanduser("~")
    return "~" + s[len(casa):] if s.startswith(casa) else s


# ── a ajuda ────────────────────────────────────────────────────────────────

COMANDOS = [
    ("install", "liga a releitura no ambiente virtual desta pasta"),
    ("install --global", "liga para o seu usuário: aquele Python, em qualquer pasta (fora de ambiente virtual)"),
    ("uninstall", "desliga daqui   (--global: do usuário · --all: de todo lugar)"),
    ("status", "onde está ligada, em que versão, e se esta pasta tem"),
    ("templates", "o que é relido, e como aparece"),
    ("check-update", "procura uma versão nova e mostra o que mudou, sem instalar"),
    ("upgrade", "procura, mostra o que mudou e pergunta antes de instalar"),
    ("sync", "reescreve a releitura em todo lugar registrado, com a versão deste ratt"),
]


def ajuda():
    w = max(len(n) for n, _ in COMANDOS) + 4
    linhas = [
        f"{c('ratt', 'azul', 'negrito')} {__version__} — o chocalho do pandas no terminal {c('(rattlesnake)', 'cinza')}",
        "",
        f"{c('uso:', 'negrito')} ratt <comando> [opções]",
        f"     ratt              {c('sem nada, num terminal, abre o menu', 'cinza')}",
        "",
        c("comandos:", "negrito"),
    ]
    linhas += [f"  {n:<{w}}{c(d, 'cinza')}" for n, d in COMANDOS]
    linhas += [
        "",
        c("opções:", "negrito"),
        f"  {'--python CAMINHO':<{w}}{c('usa este Python em vez de descobrir sozinho', 'cinza')}",
        f"  {'-y, --yes':<{w}}{c('não pergunta (upgrade, uninstall --all)', 'cinza')}",
        f"  {'-h, --help':<{w}}{c('esta ajuda   ·   ratt <comando> -h: a ajuda de um comando', 'cinza')}",
        f"  {'-V, --version':<{w}}{c('a versão', 'cinza')}",
        "",
        c("variáveis:", "negrito"),
        f"  {'RATT=0':<{w}}{c('desliga a releitura numa execução:   RATT=0 python x.py', 'cinza')}",
        f"  {'RATT_REPO=dono/repo':<{w}}{c(f'de onde o upgrade busca (padrão: {REPOSITORIO})', 'cinza')}",
        f"  {'NO_COLOR':<{w}}{c('sem cor na saída do ratt', 'cinza')}",
        "",
        c("exemplos:", "negrito"),
        "  cd meu-projeto && python -m venv .venv && ratt install",
        "  ratt install --global",
        "  ratt status",
        "  ratt upgrade",
    ]
    return "\n".join(linhas)


AJUDA_COMANDO = {
    "install": """ratt install [--global] [--python CAMINHO]

  sem --global   liga no ambiente virtual desta pasta: .venv/, venv/ ou env/; se não houver,
                 no ambiente ativo ($VIRTUAL_ENV). Vale só para quem usa aquele ambiente.
  --global       liga na pasta de pacotes do seu usuário de um Python (o python3 do PATH).
                 Vale para aquele Python em qualquer pasta — mas não entra em ambiente virtual:
                 o venv isola justamente isso. Num projeto com .venv, use o install local.
  --python P     escolhe o Python à mão.

  Uma pasta só com .python-version (pyenv) é recusada no install local: o pyenv escolhe o
  interpretador, não isola pacotes — instalar ali ligaria em todo projeto da mesma versão.""",
    "uninstall": """ratt uninstall [--global] [--all] [--python CAMINHO] [-y]

  sem opção      desliga do ambiente virtual desta pasta
  --global       desliga do seu usuário (o python3 do PATH, ou --python)
  --all          desliga de todo lugar que o ratt lembra (pergunta antes, a menos que -y)

  Apaga só os dois arquivos que o ratt pôs lá: ratt.pth e ratt_releitura.py.""",
    "status": """ratt status

  Lista todo lugar onde a releitura foi instalada, a versão de cada um, e diz se esta pasta
  tem a releitura ligada. Avisa se RATT=0 está definido neste shell.""",
    "templates": """ratt templates

  O que é relido e como aparece. O que não está na lista sai como o pandas escreve.""",
    "check-update": """ratt check-update

  Procura a versão no repositório e mostra o que mudou desde a sua. Não instala nada.""",
    "upgrade": """ratt upgrade [-y]

  Procura a versão nova, mostra o que mudou e pergunta [s/N]. Se sim, atualiza o ratt
  (pelo pipx ou pelo pip, do jeito que ele foi instalado) e reescreve a releitura em todo
  lugar registrado.""",
    "sync": """ratt sync

  Reescreve a releitura em todo lugar registrado com a versão deste ratt. O upgrade chama
  sozinho; à mão, serve depois de um  git pull  numa cópia de desenvolvimento.""",
}

TEMPLATES = [
    ("inspeção", [
        ("print(df.shape)", "a tupla, e as setas Li/Co: ──▶ colunas, ▼ linhas"),
        ("print(df.describe())", "medidas em português ao lado do nome original, e uma caixa de barras ao lado"),
        ('print(df["col"].describe())', "o cartão de uma coluna"),
        ('print(df.describe(include="all"))', "as colunas de texto numa caixa própria; o 'top' que não avisa empate"),
        ("print(df.head())  tail()  sample()", "todas as colunas em blocos, o tipo embaixo do nome, NaN e ␣ em laranja"),
        ("print(df)", "as primeiras e as últimas, com o ⋮ no meio, como o pandas corta"),
        ('print(df["col"])', "a coluna com nome, tipo, total e vazios no topo"),
        ("print(df.dtypes)", "lista numerada, o significado de cada tipo e a contagem por tipo"),
        ("print(df.columns)", "lista numerada, a cor do nome é o tipo da coluna"),
        ("print(df.index)", "o RangeIndex explicado; num índice de texto, os repetidos em laranja"),
        ("print(df.isna().sum())", "vazios por coluna, com % e barra; as sem vazio ficam apagadas"),
        ("print(df.nunique())", "valores diferentes 'de N linhas'; constante, vazia e todos diferentes marcados"),
        ('print(df["col"].value_counts())', "%, barra, ␣ visível e os vazios que ficaram fora da contagem"),
        ('print(df["col"].value_counts(normalize=True))', "a proporção ao lado do mesmo número em %"),
        ('print(df["col"].unique())', "lista numerada, na ordem do arquivo"),
    ]),
    ("transformação", [
        ('print(df[df["x"] > 3])', "a condição no topo, quantas linhas ficaram e quantas saíram"),
        ("qualquer outra tabela: sort_values, dropna, merge, df[[...]]…", "o que mudou: linhas, ordem, colunas, vazios"),
        ("qualquer outra coluna: .str.strip(), .fillna(0), .isna()…", "quantos valores mudaram; True/False contados"),
        ("print(df.mean(numeric_only=True))  sum()  count()…", "um valor por coluna, com o 'de N' que entrou na conta"),
    ]),
    ("agrupamento", [
        ('print(df.groupby("a")["b"].mean())', "uma barra por grupo; o título vem da sua linha"),
        ('print(df.groupby("a")["b"].agg([...]))', "nome em português sobre o original; o count em amarelo é o n"),
        ('print(df.groupby(["a", "b"])["c"].mean())', "uma árvore, com barras"),
        ("print(df.pivot_table(...))  .unstack()", "fundo mais forte = valor maior; NaN = combinação que não existe"),
        ('print(pd.crosstab(df["a"], df["b"]))', "fundo mais forte = contagem maior; · = zero; totais"),
        ("print(df[[...]].corr())", "azul sobem juntas, lilás uma sobe e a outra desce"),
        ('print(pd.cut(df["x"], bins=3))  pd.qcut', "cada valor na sua faixa, e como ler (a, b]"),
    ]),
    ("tempo", [
        ('print(pd.to_datetime(df["data"], format=...))', "cada texto ao lado da data que virou, com o dia da semana"),
        ('print(df["data"].dt.day_name())  .dt.month…', "cada data ao lado da parte pedida"),
        ("print(indice_de_datas)", "ordem, passo, buracos, repetidas e o ritmo que o pandas deduz"),
        ('print(pd.date_range(..., freq="ME"))', "as datas e a tabela dos códigos de frequência"),
        ('print(df["data"].dt.to_period("M"))', "cada data e o período inteiro que a contém"),
        ('print(s.resample("W").mean())', "um balde por linha, com o 'de N'; balde incompleto em laranja"),
        ("print(s.rolling(7).mean())  .diff()  .shift()…", "a série inteira em ▁▂▃▄▅▆▇█, antes e depois"),
    ]),
]


def templates():
    w = max(len(cmd) for _, grupo in TEMPLATES for cmd, _ in grupo) + 3
    linhas = []
    for nome, grupo in TEMPLATES:
        linhas += [c(nome, "amarelo", "negrito")]
        linhas += [f"  {c(cmd.ljust(w), 'azul')}{c(o, 'cinza')}" for cmd, o in grupo]
        linhas.append("")
    linhas.append(c("o resto sai como o pandas escreve: o info(), números soltos e tabelas de índice com vários níveis.",
                    "cinza"))
    return "\n".join(linhas)


# ── os comandos ─────────────────────────────────────────────────────────────

def _perguntar_sim(pergunta, sim_sempre=False):
    if sim_sempre:
        return True
    if not sys.stdin.isatty():
        print(c("sem terminal para perguntar: use -y para confirmar", "laranja"))
        return False
    try:
        return input(f"{pergunta} [s/N] ").strip().lower() in ("s", "sim", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def cmd_install(op):
    if op["global"]:
        python, como = alvo.achar_global(op["python"])
    else:
        python, como = alvo.achar_local(Path.cwd(), op["python"])
    escopo = "global" if op["global"] else "local"
    print(f"{c('ratt install', 'azul', 'negrito')} · {escopo} · {como}")
    r = alvo.instalar(escopo, python, como)
    info = r["info"]
    print(f"  {c('✓', 'verde')} ligada em {_casa(r['destino'])}")
    print(f"    Python {info['versao']} · {_casa(info['executavel'])}")
    if not r["ligada"]:
        print(c("  ⚠ os arquivos estão lá, mas o Python não carregou a releitura ao começar — rode  ratt status", "laranja"))
    if not info["pandas"]:
        print(c("  · o pandas não está instalado neste Python: a releitura liga, mas só age quando ele estiver",
                "cinza"))
    if escopo == "global":
        print(c("  · vale para este Python em qualquer pasta — menos dentro de ambiente virtual (lá, ratt install)",
                "cinza"))
    print(c("  · para desligar numa execução:  RATT=0 python x.py", "cinza"))
    return 0


def cmd_uninstall(op):
    if op["all"]:
        itens = alvo.ler_registro()
        if not itens:
            print("o ratt não lembra de nenhuma instalação")
            return 0
        for i in itens:
            print(f"  {i['escopo']:<7} {_casa(i['pasta_pacotes'])}")
        if not _perguntar_sim(f"desligar dos {len(itens)} lugares?", op["yes"]):
            return 1
        for i in itens:
            alvo.remover(i["pasta_pacotes"])
        print(c(f"✓ desligada de {len(itens)} lugares", "verde"))
        return 0
    if op["global"]:
        python, _ = alvo.achar_global(op["python"])
        pasta = alvo.perguntar(python)["usuario"]
    else:
        python, _ = alvo.achar_local(Path.cwd(), op["python"])
        pasta = alvo.perguntar(python)["pacotes"]
    apagados = alvo.remover(pasta)
    if apagados:
        print(c("✓ desligada", "verde") + f" de {_casa(pasta)}")
    else:
        print(f"não estava ligada em {_casa(pasta)}")
    return 0


def cmd_status(op):
    itens = alvo.ler_registro()
    print(f"{c('ratt', 'azul', 'negrito')} {__version__} · {len(itens)} instalaç{'ão' if len(itens) == 1 else 'ões'}")
    if itens:
        print()
        for i in itens:
            existe = (Path(i["pasta_pacotes"]) / alvo.PTH).exists()
            v = alvo.versao_instalada(i["pasta_pacotes"]) if existe else None
            if not existe:
                estado = c("sumiu (ambiente apagado?)", "laranja")
            elif v != __version__:
                estado = c(f"{v} → rode ratt sync", "amarelo")
            else:
                estado = c(v, "verde")
            print(f"  {i['escopo']:<7} Python {i['versao_python']:<8} {_casa(i['pasta_pacotes'])}  {estado}")
    print()
    try:
        python, como = alvo.achar_local(Path.cwd())
        pacotes = Path(alvo.perguntar(python)["pacotes"])
        if (pacotes / alvo.PTH).exists():
            print(f"nesta pasta: {c('ligada', 'verde')} — {como}")
        else:
            print(f"nesta pasta: {c('não ligada', 'amarelo')} — {como}; para ligar:  ratt install")
    except alvo.Recusa:
        print(f"nesta pasta: {c('sem ambiente virtual', 'cinza')} — vale o global, se houver")
    if os.environ.get("RATT") == "0" or os.environ.get("RELEITURA") == "0":
        print(c("RATT=0 está definido neste shell: aqui a releitura está desligada", "laranja"))
    return 0


def cmd_check_update(op):
    print(f"{c('ratt check-update', 'azul', 'negrito')} · {atualizar._repositorio()}")
    try:
        remota = atualizar.versao_remota()
    except Exception as e:
        print(c(f"  não consegui consultar o repositório: {e}", "laranja"))
        return 1
    if atualizar._tupla(remota) <= atualizar._tupla(__version__):
        print(f"  {c('✓', 'verde')} você está na versão mais nova ({__version__})")
        return 0
    print(f"  versão nova: {c(__version__, 'cinza')} → {c(remota, 'verde', 'negrito')}")
    for versao, texto in atualizar.novidades(__version__):
        print()
        print(c(f"  {versao}", "amarelo", "negrito"))
        for linha in texto.splitlines():
            print("    " + linha)
    return 100      # como o dnf check-update: 100 = há atualização


def cmd_upgrade(op):
    codigo = cmd_check_update(op)
    if codigo != 100:
        return codigo
    forma = atualizar.como_foi_instalado()
    cmd = atualizar.comando_de_atualizacao(forma)
    print()
    if cmd is None:
        print(c("  este ratt é uma cópia de desenvolvimento: atualize com  git pull  na pasta dele e depois  ratt sync",
                "amarelo"))
        return 1
    print(c(f"  vai rodar: {' '.join(cmd)}", "cinza"))
    if not _perguntar_sim("  atualizar?", op["yes"]):
        return 1
    import subprocess
    if subprocess.run(cmd).returncode != 0:
        print(c("  a atualização falhou — nada foi trocado nas instalações", "laranja"))
        return 1
    return atualizar.reaplicar_com_a_versao_nova()


def cmd_sync(op):
    feitos, sumidos = alvo.reaplicar()
    for i in feitos:
        print(f"  {c('✓', 'verde')} {i['escopo']:<7} {_casa(i['pasta_pacotes'])}  → {__version__}")
    for i in sumidos:
        print(f"  {c('·', 'cinza')} {i['escopo']:<7} {_casa(i['pasta_pacotes'])}  {c('sumiu: saiu do registro', 'cinza')}")
    if not feitos and not sumidos:
        print("o ratt não lembra de nenhuma instalação")
    return 0


EXECUTA = {"install": cmd_install, "uninstall": cmd_uninstall, "status": cmd_status, "templates": None,
           "check-update": cmd_check_update, "upgrade": cmd_upgrade, "sync": cmd_sync}


# ── o menu ──────────────────────────────────────────────────────────────────

def menu():
    opcoes = [
        ("status", "onde está ligada", "status", {}),
        ("ligar nesta pasta", "install", "install", {}),
        ("ligar no usuário (global)", "install --global", "install", {"global": True}),
        ("o que é relido", "templates", "templates", {}),
        ("procurar atualização", "check-update", "check-update", {}),
        ("atualizar", "upgrade", "upgrade", {}),
        ("desligar desta pasta", "uninstall", "uninstall", {}),
    ]
    while True:
        print()
        print(f"{c('ratt', 'azul', 'negrito')} {__version__} {c('— o chocalho do pandas no terminal', 'cinza')}")
        print()
        for n, (nome, cmd, _, _) in enumerate(opcoes, 1):
            print(f"  {c(n, 'amarelo', 'negrito')}  {nome:<28}{c('ratt ' + cmd, 'cinza')}")
        print(f"  {c(0, 'amarelo', 'negrito')}  sair")
        try:
            escolha = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if escolha in ("0", "q", "sair", ""):
            return 0
        if not escolha.isdigit() or not 1 <= int(escolha) <= len(opcoes):
            print(c("escolha um número da lista", "laranja"))
            continue
        _, _, comando, extra = opcoes[int(escolha) - 1]
        op = dict(_OPCOES_PADRAO, **extra)
        print()
        _rodar(comando, op)


# ── a entrada ───────────────────────────────────────────────────────────────

_OPCOES_PADRAO = {"global": False, "all": False, "yes": False, "python": None, "help": False}


def _erro_de_uso(msg):
    """Como as ferramentas do Linux: erro de uso vai para o stderr e sai com 2."""
    print(f"ratt: {msg} (veja ratt -h)", file=sys.stderr)
    raise SystemExit(2)


def _ler_argumentos(args):
    comando, op = None, dict(_OPCOES_PADRAO)
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            op["help"] = True
        elif a in ("-V", "--version"):
            return "versao", op
        elif a == "--global":
            op["global"] = True
        elif a == "--all":
            op["all"] = True
        elif a in ("-y", "--yes"):
            op["yes"] = True
        elif a == "--python" or a.startswith("--python="):
            if "=" in a:
                op["python"] = a.split("=", 1)[1]
            else:
                i += 1
                if i >= len(args):
                    _erro_de_uso("--python precisa de um caminho")
                op["python"] = args[i]
        elif a.startswith("-"):
            _erro_de_uso(f"opção desconhecida: {a}")
        elif comando is None:
            comando = a
        else:
            _erro_de_uso(f"argumento a mais: {a}")
        i += 1
    return comando, op


def _rodar(comando, op):
    if comando == "templates":
        print(templates())
        return 0
    try:
        return EXECUTA[comando](op)
    except alvo.Recusa as e:
        print(c("ratt: ", "laranja", "negrito") + str(e))
        return 1


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    comando, op = _ler_argumentos(args)
    if comando == "versao":
        print(f"ratt {__version__}")
        return 0
    if comando is None:
        if op["help"] or not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(ajuda())
            return 0
        return menu()
    if comando not in EXECUTA:
        _erro_de_uso(f"comando desconhecido: {comando}")
    if op["help"]:
        print(AJUDA_COMANDO.get(comando, ajuda()))
        return 0
    return _rodar(comando, op)
