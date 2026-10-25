"""O idioma da interface do ratt db: português ou inglês.

Só a interface muda — rótulos, avisos, a ajuda. Os comandos são sempre os mesmos (ratt db status),
o SQL continua SQL e o que o PostgreSQL escreve sai como ele escreve.

A escolha fica em ~/.config/ratt/config.json; RATT_LANG=en vale para uma execução.
"""
import json
import os
from pathlib import Path

IDIOMAS = ("pt", "en")


def _arquivo_config():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "ratt" / "config.json"


def _ler_config():
    try:
        return json.loads(_arquivo_config().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def atual():
    escolhido = os.environ.get("RATT_LANG") or _ler_config().get("lang") or "pt"
    return escolhido if escolhido in IDIOMAS else "pt"


def gravar(idioma):
    config = _ler_config()
    config["lang"] = idioma
    arquivo = _arquivo_config()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(config, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def t(chave, **valores):
    """O texto de uma chave no idioma atual; se faltar a tradução, o português."""
    par = TEXTOS[chave]
    return par.get(atual(), par["pt"]).format(**valores)


TEXTOS = {
    # ── a ajuda ──
    "slogan": {"pt": "o PostgreSQL no terminal", "en": "PostgreSQL in the terminal"},
    "uso": {"pt": "uso:", "en": "usage:"},
    "uso.linha": {"pt": "ratt db <comando> [opções]", "en": "ratt db <command> [options]"},
    "comandos": {"pt": "comandos:", "en": "commands:"},
    "opcoes": {"pt": "opções:", "en": "options:"},
    "variaveis": {"pt": "variáveis:", "en": "variables:"},
    "ajuda.status": {"pt": "o que está instalado, ligado e configurado, e o próximo passo",
                     "en": "what is installed, running and configured, and the next step"},
    "ajuda.lang": {"pt": "o idioma da interface: pt ou en", "en": "the interface language: pt or en"},
    "ajuda.h": {"pt": "esta ajuda   ·   ratt db <comando> -h: a de um comando",
                "en": "this help   ·   ratt db <command> -h: help for a command"},
    "ajuda.RATT_LANG": {"pt": "o idioma só nesta execução:   RATT_LANG=en ratt db status",
                        "en": "the language for one run only:   RATT_LANG=pt ratt db status"},
    "ajuda.NO_COLOR": {"pt": "sem cor na saída", "en": "no colors in the output"},
    "ajuda_cmd.status": {
        "pt": """ratt db status

  Confere, em ordem, cada peça de que o PostgreSQL precisa: o sistema, o sudo, a instalação,
  a pasta de dados, o serviço, a porta, como o login pela rede é feito, e se você já entra.
  Onde algo falta, diz o comando que resolve. Não muda nada e não pede senha.""",
        "en": """ratt db status

  Checks, in order, each piece PostgreSQL needs: the system, sudo, the installation, the data
  directory, the service, the port, how network logins are handled, and whether you can log in.
  Where something is missing, it shows the command that fixes it. Changes nothing, asks no password.""",
    },
    "ajuda_cmd.lang": {
        "pt": """ratt db lang [pt|en]

  Sem argumento, mostra o idioma atual. Com pt ou en, troca e guarda a escolha.
  Muda só a interface: os comandos e o SQL são os mesmos nos dois idiomas.""",
        "en": """ratt db lang [pt|en]

  With no argument, shows the current language. With pt or en, switches and saves the choice.
  Only the interface changes: commands and SQL are the same in both languages.""",
    },

    # ── erros de uso ──
    "erro.comando": {"pt": "comando desconhecido: {nome}", "en": "unknown command: {nome}"},
    "erro.opcao": {"pt": "opção desconhecida: {nome}", "en": "unknown option: {nome}"},
    "erro.argumento": {"pt": "argumento a mais: {nome}", "en": "unexpected argument: {nome}"},
    "erro.veja": {"pt": "veja ratt db -h", "en": "see ratt db -h"},

    # ── lang ──
    "lang.atual": {"pt": "idioma da interface: {idioma}", "en": "interface language: {idioma}"},
    "lang.trocado": {"pt": "idioma da interface: {idioma}  (salvo)", "en": "interface language: {idioma}  (saved)"},
    "lang.invalido": {"pt": "idioma desconhecido: {idioma}  (pt ou en)", "en": "unknown language: {idioma}  (pt or en)"},
    "lang.env": {"pt": "· RATT_LANG={idioma} está definido neste shell e vence o que está salvo",
                 "en": "· RATT_LANG={idioma} is set in this shell and overrides the saved choice"},

    # ── status: rótulos ──
    "r.sistema": {"pt": "sistema", "en": "system"},
    "r.sudo": {"pt": "sudo", "en": "sudo"},
    "r.postgres": {"pt": "PostgreSQL", "en": "PostgreSQL"},
    "r.dados": {"pt": "dados", "en": "data"},
    "r.servico": {"pt": "serviço", "en": "service"},
    "r.porta": {"pt": "porta", "en": "port"},
    "r.login": {"pt": "login na rede", "en": "network login"},
    "r.acesso": {"pt": "seu acesso", "en": "your access"},
    "r.bancos": {"pt": "bancos", "en": "databases"},

    # ── status: o que cada peça diz ──
    "s.familia": {"pt": "família {nome} · {gerenciador}", "en": "{nome} family · {gerenciador}"},
    "s.sistema_desconhecido": {"pt": "não reconhecido — o ratt db conhece as famílias Debian, Red Hat e Arch",
                               "en": "not recognized — ratt db knows the Debian, Red Hat and Arch families"},
    "s.sudo_sim": {"pt": "você está no grupo {grupo}", "en": "you are in the {grupo} group"},
    "s.sudo_root": {"pt": "rodando como root", "en": "running as root"},
    "s.sudo_nao": {"pt": "você não está em {grupos}: instalar e configurar precisa de quem administra a máquina",
                   "en": "you are not in {grupos}: installing and configuring needs whoever runs this machine"},
    "s.instalado": {"pt": "{versao} instalado", "en": "{versao} installed"},
    "s.nao_instalado": {"pt": "não instalado", "en": "not installed"},
    "s.inicializado": {"pt": "inicializados em {pasta}", "en": "initialized in {pasta}"},
    "s.nao_inicializado": {"pt": "pasta de dados não inicializada", "en": "data directory not initialized"},
    "s.inicializado_duvida": {"pt": "não dá para saber sem sudo: o serviço está parado e {pasta} só abre para o postgres",
                              "en": "can't tell without sudo: the service is stopped and {pasta} only opens for postgres"},
    "s.rodando": {"pt": "rodando", "en": "running"},
    "s.parado": {"pt": "parado", "en": "stopped"},
    "s.liga_boot": {"pt": "liga junto com a máquina", "en": "starts with the machine"},
    "s.nao_liga_boot": {"pt": "não liga junto com a máquina", "en": "does not start with the machine"},
    "s.sem_systemd": {"pt": "sem systemd aqui (um container?)", "en": "no systemd here (a container?)"},
    "s.porta_aberta": {"pt": "localhost:{porta} aceita conexões", "en": "localhost:{porta} accepts connections"},
    "s.porta_fechada": {"pt": "ninguém atende em localhost:{porta}", "en": "nothing answers on localhost:{porta}"},
    "s.pede_senha": {"pt": "pede senha ({metodo})", "en": "asks for a password ({metodo})"},
    "s.trust": {"pt": "entra SEM senha (trust): qualquer usuário desta máquina entra como qualquer um",
                "en": "lets in WITHOUT a password (trust): any user on this machine can log in as anyone"},
    "s.recusa": {"pt": "recusa sem pedir senha — programas como a extensão do VS Code não entram",
                 "en": "refuses without asking for a password — tools like the VS Code extension can't log in"},
    "s.recusa_ident": {"pt": "recusa sem pedir senha: no {nome} o padrão é ident — programas como a extensão do VS Code não entram",
                       "en": "refuses without asking for a password: {nome} defaults to ident — tools like the VS Code extension can't log in"},
    "s.metodo_outro": {"pt": "responde com {metodo}", "en": "answers with {metodo}"},
    "s.entra": {"pt": "entra como {usuario} ({como})", "en": "logged in as {usuario} ({como})"},
    "s.como_socket": {"pt": "socket local, sem senha", "en": "local socket, no password"},
    "s.como_pgpass": {"pt": "pela rede, senha do ~/.pgpass", "en": "over the network, password from ~/.pgpass"},
    "s.sem_acesso": {"pt": "o usuário {usuario} ainda não entra no banco", "en": "user {usuario} can't log in to the database yet"},
    "s.nenhum_banco": {"pt": "nenhum além dos do sistema", "en": "none besides the system ones"},
    "s.banco": {"pt": "dono {dono}", "en": "owner {dono}"},
    "s.sem_permissao": {"pt": "sem permissão para ver", "en": "no permission to see"},
    "s.depois": {"pt": "—", "en": "—"},
    "s.proximo": {"pt": "próximo passo:", "en": "next step:"},
    "s.tudo_certo": {"pt": "tudo certo", "en": "all good"},
    "s.manual": {"pt": "este sistema não tem receita: instale o PostgreSQL pelo gerenciador dele, e o ratt db status confere o resto",
                 "en": "no recipe for this system: install PostgreSQL with its package manager, and ratt db status checks the rest"},
}
