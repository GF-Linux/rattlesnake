"""O idioma da interface do ratt db: português ou inglês.

Só a interface muda — rótulos, avisos, a ajuda. Os comandos são sempre os mesmos (ratt db status),
o SQL continua SQL e o que o PostgreSQL escreve sai como ele escreve.

A escolha fica no config do ratt (ratt/db/config.py); RATT_LANG=en vale para uma execução.
"""
import os

from ratt.db import config

IDIOMAS = ("pt", "en")


def atual():
    escolhido = os.environ.get("RATT_LANG") or config.ler().get("lang") or "pt"
    return escolhido if escolhido in IDIOMAS else "pt"


def gravar(idioma):
    config.gravar(lang=idioma)


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

    # ── setup ──
    "ajuda.setup": {"pt": "do zero até o primeiro banco: instala, inicializa, liga, senha, usuário",
                    "en": "from scratch to the first database: install, init, start, password, user"},
    "ajuda_cmd.setup": {
        "pt": """ratt db setup [--user NOME] [--db NOME] [--superuser] [-y]

  Deixa o PostgreSQL pronto, em seis passos. Cada um confere antes de fazer — rodar de novo só
  pula o que já está pronto — e mostra o comando antes de rodar:

    1 instalar        pelo gerenciador do sistema (dnf, apt ou pacman)
    2 inicializar     cria a pasta de dados
    3 ligar           agora e junto com a máquina
    4 login por senha pela rede, ident e trust viram scram-sha-256 (o pg_hba.conf ganha uma cópia .bak)
    5 usuário         o seu usuário no banco, com senha; ela pode ficar no ~/.pgpass
    6 banco           o primeiro banco, com esse usuário como dono (opcional)

  --user NOME     o usuário do banco (padrão: o seu do Linux)
  --db NOME       o primeiro banco (sem ele, pergunta; com -y, pula)
  --superuser     o usuário vira superusuário (padrão: pode criar bancos, e só)
  -y, --yes       não pergunta nada; a senha vem da entrada:  ratt db setup -y --user app < senha.txt

  Os nomes ficam em minúsculas, como o PostgreSQL guarda: letras sem acento, números e _.""",
        "en": """ratt db setup [--user NAME] [--db NAME] [--superuser] [-y]

  Gets PostgreSQL ready in six steps. Each one checks before acting — running it again only
  skips what is already done — and shows the command before running it:

    1 install         with the system's package manager (dnf, apt or pacman)
    2 initialize      creates the data directory
    3 start           now and whenever the machine boots
    4 password login  over the network, ident and trust become scram-sha-256 (pg_hba.conf gets a .bak copy)
    5 user            your database user, with a password; it can be saved in ~/.pgpass
    6 database        the first database, owned by that user (optional)

  --user NAME     the database user (default: your Linux user)
  --db NAME       the first database (without it, asks; with -y, skips)
  --superuser     the user becomes a superuser (default: can create databases, nothing else)
  -y, --yes       asks nothing; the password comes from stdin:  ratt db setup -y --user app < password.txt

  Names are lowercase, as PostgreSQL stores them: unaccented letters, digits and _.""",
    },
    "erro.precisa_valor": {"pt": "{nome} precisa de um valor", "en": "{nome} needs a value"},
    "c.sem_sudo": {"pt": "você não está em {grupos}: o setup precisa de sudo para instalar e configurar",
                   "en": "you are not in {grupos}: setup needs sudo to install and configure"},
    "c.sem_terminal": {"pt": "sem terminal para perguntar: use -y (e --user, --db)",
                       "en": "no terminal to ask: use -y (and --user, --db)"},
    "c.qual_usuario": {"pt": "usuário do banco:", "en": "database user:"},
    "c.qual_banco": {"pt": "primeiro banco (Enter pula):", "en": "first database (Enter skips):"},
    "c.minusculas": {"pt": "o PostgreSQL guarda nomes sem aspas em minúsculas",
                     "en": "PostgreSQL stores unquoted names in lowercase"},
    "c.usar": {"pt": "usar {nome}?", "en": "use {nome}?"},
    "c.nome_invalido": {"pt": "{nome}: use letras sem acento, números e _, começando por letra",
                        "en": "{nome}: use unaccented letters, digits and _, starting with a letter"},
    "c.plano": {"pt": "PostgreSQL na família {nome} ({gerenciador}):", "en": "PostgreSQL on the {nome} family ({gerenciador}):"},
    "c.p.instalar": {"pt": "instalar", "en": "install"},
    "c.p.inicializar": {"pt": "inicializar", "en": "initialize"},
    "c.p.ligar": {"pt": "ligar", "en": "start"},
    "c.p.senha": {"pt": "login por senha", "en": "password login"},
    "c.p.usuario": {"pt": "usuário", "en": "user"},
    "c.p.banco": {"pt": "banco", "en": "database"},
    "c.confere_e_faz": {"pt": "cria a pasta de dados, se ainda não existe", "en": "creates the data directory if it doesn't exist"},
    "c.p.ligar_boot": {"pt": "agora e junto com a máquina", "en": "now and on every boot"},
    "c.p.ligar_sem_systemd": {"pt": "agora (sem systemd aqui: não volta sozinho ao reiniciar)",
                              "en": "now (no systemd here: won't come back by itself after a restart)"},
    "c.p.senha_o_que": {"pt": "pg_hba.conf: ident/trust → scram-sha-256, se precisar (com cópia .bak)",
                        "en": "pg_hba.conf: ident/trust → scram-sha-256 if needed (with a .bak copy)"},
    "c.p.usuario_o_que": {"pt": "{usuario}, com senha", "en": "{usuario}, with a password"},
    "c.p.banco_o_que": {"pt": "{banco}, dono {usuario}", "en": "{banco}, owned by {usuario}"},
    "c.p.banco_pula": {"pt": "nenhum agora", "en": "none for now"},
    "c.pronto": {"pt": "já está pronto", "en": "already done"},
    "c.nada_a_fazer": {"pt": "nada a fazer: está tudo pronto", "en": "nothing to do: everything is ready"},
    "c.continuar": {"pt": "continuar?", "en": "continue?"},
    "c.sudo_falhou": {"pt": "sem a senha do sudo não dá para continuar", "en": "can't go on without the sudo password"},
    "c.ja": {"pt": "já estava: {o}", "en": "already done: {o}"},
    "c.nao_achei_instalado": {"pt": "o gerenciador terminou, mas o servidor não aparece instalado",
                              "en": "the package manager finished, but the server doesn't show as installed"},
    "c.inicializado": {"pt": "pasta de dados criada", "en": "data directory created"},
    "c.rodando": {"pt": "rodando", "en": "running"},
    "c.rodando_boot": {"pt": "rodando, e liga junto com a máquina", "en": "running, and starts on boot"},
    "c.rodando_sem_boot": {"pt": "rodando (sem systemd: ligue de novo depois de reiniciar)",
                           "en": "running (no systemd: start it again after a restart)"},
    "c.porta_nao_abriu": {"pt": "o servidor não começou a atender em localhost:{porta}",
                          "en": "the server didn't start answering on localhost:{porta}"},
    "c.hba_ok": {"pt": "pela rede, pede senha", "en": "network logins ask for a password"},
    "c.hba_trocado": {"pt": "pela rede, agora pede senha — {arquivo}", "en": "network logins now ask for a password — {arquivo}"},
    "c.copia": {"pt": "o original ficou em {arquivo}", "en": "the original was kept as {arquivo}"},
    "c.senha": {"pt": "senha do usuário {usuario} no banco:", "en": "password for database user {usuario}:"},
    "c.senha_de_novo": {"pt": "de novo:", "en": "again:"},
    "c.senha_vazia": {"pt": "sem senha, nenhum programa entra pela rede: digite uma",
                      "en": "without a password no program can log in over the network: type one"},
    "c.senha_diferente": {"pt": "as duas não batem, de novo", "en": "they don't match, again"},
    "c.usuario_criado": {"pt": "{usuario} criado ({poderes})", "en": "{usuario} created ({poderes})"},
    "c.guardar_senha": {"pt": "guardar a senha no ~/.pgpass (o psql e o ratt entram sem perguntar)?",
                        "en": "save the password in ~/.pgpass (psql and ratt log in without asking)?"},
    "c.senha_guardada": {"pt": "senha guardada em {arquivo} (só você lê)", "en": "password saved in {arquivo} (only you can read it)"},
    "c.falhou": {"pt": "falhou: {cmd}", "en": "failed: {cmd}"},
    "c.de_novo": {"pt": "corrija e rode ratt db setup de novo: ele pula o que já está pronto",
                  "en": "fix it and run ratt db setup again: it skips what is already done"},
    "c.conectar": {"pt": "conectar:", "en": "connect:"},
    "c.conferir": {"pt": "conferir:", "en": "check:   "},
}
