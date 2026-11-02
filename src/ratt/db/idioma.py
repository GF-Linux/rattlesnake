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

    # ── list, connect, new, user ──
    "arg.connect": {"pt": "[banco]", "en": "[database]"},
    "arg.new": {"pt": "<banco>", "en": "<database>"},
    "arg.user": {"pt": "[add|passwd] [nome]", "en": "[add|passwd] [name]"},
    "arg.lang": {"pt": "[pt|en]", "en": "[pt|en]"},
    "ajuda.list": {"pt": "os bancos: dono, tamanho e qual é o atual", "en": "the databases: owner, size and which is current"},
    "ajuda.connect": {"pt": "abre o psql num banco (sem nome, escolhe da lista)",
                      "en": "opens psql on a database (no name: pick from the list)"},
    "ajuda.new": {"pt": "cria um banco  (--owner: outro dono)", "en": "creates a database  (--owner: another owner)"},
    "ajuda.user": {"pt": "os usuários do banco; add cria, passwd troca a senha",
                   "en": "the database users; add creates one, passwd changes a password"},
    "ajuda_cmd.list": {
        "pt": """ratt db list [--user NOME]

  Os bancos que você enxerga, com dono e tamanho. O ● marca o atual: o último que você abriu ou
  criou, e o que o ratt db connect sugere. Os bancos do sistema (postgres, template0/1) não aparecem.""",
        "en": """ratt db list [--user NAME]

  The databases you can see, with owner and size. The ● marks the current one: the last you opened
  or created, and the one ratt db connect suggests. System databases (postgres, template0/1) are hidden.""",
    },
    "ajuda_cmd.connect": {
        "pt": """ratt db connect [banco] [--user NOME]

  Abre o psql no banco. Sem nome, mostra a lista numerada: o número escolhe, Enter abre o atual.
  O comando do psql aparece antes de abrir — é o mesmo que você digitaria:

    psql -h localhost -U jared estudos     (pela rede, senha do ~/.pgpass)
    psql -U jared estudos                  (pelo socket, quando o seu usuário do Linux entra direto)

  Para sair do psql:  \\q""",
        "en": """ratt db connect [database] [--user NAME]

  Opens psql on the database. With no name, shows the numbered list: a number picks, Enter opens
  the current one. The psql command is shown before opening — the same one you would type:

    psql -h localhost -U jared estudos     (over the network, password from ~/.pgpass)
    psql -U jared estudos                  (local socket, when your Linux user logs in directly)

  To leave psql:  \\q""",
    },
    "ajuda_cmd.new": {
        "pt": """ratt db new <banco> [--owner NOME] [-y]

  Cria o banco, com você como dono (ou --owner), e ele passa a ser o atual. O nome fica em
  minúsculas, como o PostgreSQL guarda. Se o seu usuário não puder dar o banco a outro dono,
  o ratt faz pelo superusuário postgres, via sudo, e avisa antes.""",
        "en": """ratt db new <database> [--owner NAME] [-y]

  Creates the database, owned by you (or --owner), and makes it the current one. The name is
  lowercase, as PostgreSQL stores it. If your user can't give the database to another owner,
  ratt does it as the postgres superuser, via sudo, and says so first.""",
    },
    "ajuda_cmd.user": {
        "pt": """ratt db user                          os usuários: o que cada um pode, e de quais bancos é dono
ratt db user add <nome> [--createdb | --superuser]
                                      cria um usuário com senha (padrão: só entra)
ratt db user passwd [nome]            troca a senha (sem nome: a sua)

  Criar usuários pede um poder que o seu usuário do setup não tem: o ratt faz pelo superusuário
  postgres, via sudo, e avisa antes. A senha vira hash SCRAM aqui mesmo, antes de ir ao servidor.
  Se ela já estava no ~/.pgpass, é atualizada lá; senão, o ratt pergunta se quer guardar.""",
        "en": """ratt db user                          the users: what each can do, and which databases each owns
ratt db user add <name> [--createdb | --superuser]
                                      creates a user with a password (default: login only)
ratt db user passwd [name]            changes a password (no name: yours)

  Creating users needs a power your setup user doesn't have: ratt does it as the postgres
  superuser, via sudo, and says so first. The password becomes a SCRAM hash right here, before
  it reaches the server. If it was in ~/.pgpass it is updated there; if not, ratt asks.""",
    },
    "g.sem_acesso": {"pt": "o usuário {usuario} não entra no banco — veja ratt db status",
                     "en": "user {usuario} can't log in to the database — see ratt db status"},
    "g.via_postgres": {"pt": "{usuario} não tem permissão para isso: vai pelo superusuário postgres (sudo)",
                       "en": "{usuario} isn't allowed to do this: going through the postgres superuser (sudo)"},
    "g.atual": {"pt": "atual", "en": "current"},
    "g.nenhum_banco": {"pt": "nenhum banco ainda: ratt db new <nome>", "en": "no databases yet: ratt db new <name>"},
    "g.dica_connect": {"pt": "abrir um:  ratt db connect [banco]", "en": "open one:  ratt db connect [database]"},
    "g.sair": {"pt": "sair", "en": "quit"},
    "g.qual": {"pt": "qual banco?", "en": "which database?"},
    "g.escolha_numero": {"pt": "escolha um número da lista", "en": "pick a number from the list"},
    "g.sem_psql": {"pt": "o psql não está instalado (no Fedora: postgresql; no Debian/Ubuntu: postgresql-client)",
                   "en": "psql is not installed (Fedora: postgresql; Debian/Ubuntu: postgresql-client)"},
    "g.banco_nao_existe": {"pt": "o banco {banco} não existe; estes existem:", "en": "database {banco} doesn't exist; these do:"},
    "g.sem_terminal_connect": {"pt": "sem terminal para escolher: diga o banco, ratt db connect <banco>",
                               "en": "no terminal to pick from: name the database, ratt db connect <database>"},
    "g.new_sem_nome": {"pt": "diga o nome: ratt db new <banco>", "en": "name it: ratt db new <database>"},
    "g.banco_ja_existe": {"pt": "o banco {banco} já existe — ratt db connect {banco}",
                          "en": "database {banco} already exists — ratt db connect {banco}"},
    "g.dono_nao_existe": {"pt": "o usuário {dono} não existe — crie com ratt db user add {dono}",
                          "en": "user {dono} doesn't exist — create it with ratt db user add {dono}"},
    "g.banco_criado": {"pt": "banco {banco}, dono {dono} — agora é o atual",
                       "en": "database {banco}, owned by {dono} — now the current one"},
    "g.dica_entrar": {"pt": "entrar:  ratt db connect {banco}", "en": "open it:  ratt db connect {banco}"},
    "g.superusuario": {"pt": "superusuário", "en": "superuser"},
    "g.cria_bancos": {"pt": "cria bancos", "en": "creates databases"},
    "g.cria_usuarios": {"pt": "cria usuários", "en": "creates users"},
    "g.so_entra": {"pt": "só entra", "en": "login only"},
    "g.nao_entra": {"pt": "não entra (um grupo)", "en": "no login (a group)"},
    "g.dono_de": {"pt": "dono de {bancos}", "en": "owns {bancos}"},
    "g.voce": {"pt": "você", "en": "you"},
    "g.guardar_senha": {"pt": "guardar a senha de {usuario} no ~/.pgpass?", "en": "save {usuario}'s password in ~/.pgpass?"},
    "g.usuario_ja_existe": {"pt": "o usuário {nome} já existe — para trocar a senha: ratt db user passwd {nome}",
                            "en": "user {nome} already exists — to change its password: ratt db user passwd {nome}"},
    "g.usuario_nao_existe": {"pt": "o usuário {nome} não existe — veja ratt db user",
                             "en": "user {nome} doesn't exist — see ratt db user"},
    "g.dica_dono": {"pt": "um banco para ele:  ratt db new <banco> --owner {nome}",
                    "en": "a database for it:  ratt db new <database> --owner {nome}"},
    "g.senha_trocada": {"pt": "senha de {nome} trocada", "en": "{nome}'s password changed"},
    "g.add_sem_nome": {"pt": "diga o nome: ratt db user add <nome>", "en": "name it: ratt db user add <name>"},
    "g.user_sub": {"pt": "ratt db user {nome}? use add, passwd ou nada (a lista)",
                   "en": "ratt db user {nome}? use add, passwd or nothing (the list)"},

    # ── reset ──
    "ajuda.reset": {"pt": "apaga todos os bancos e usuários (com backup antes) e volta ao antes do setup",
                    "en": "deletes every database and user (backup first) and goes back to before setup"},
    "ajuda_cmd.reset": {
        "pt": """ratt db reset [--no-backup] [-y --confirm MÁQUINA]

  Apaga TODOS os bancos e usuários deste PostgreSQL e deixa a máquina como antes do setup: o
  pacote continua instalado, a pasta de dados some (no Debian/Ubuntu, o cluster inteiro).

    1 mostra o que vai sumir: cada banco, quantas tabelas, o tamanho, e os usuários
    2 oferece um backup de tudo (pg_dumpall) na sua pasta pessoal — o padrão é sim
    3 pede que você digite o nome desta máquina — é a trava contra o terminal errado
    4 para o servidor e apaga; as senhas de localhost no ~/.pgpass saem junto
    5 pergunta se já quer rodar o ratt db setup de novo

  --no-backup         pula o backup
  -y --confirm NOME   sem perguntas (backup feito, setup não); o nome da máquina vai na linha
                      de comando — sem ele, o -y sozinho é recusado

  Restaurar o backup num PostgreSQL novo:  psql -f ~/ratt-backup-….sql postgres""",
        "en": """ratt db reset [--no-backup] [-y --confirm MACHINE]

  Deletes EVERY database and user on this PostgreSQL and leaves the machine as before setup: the
  package stays installed, the data directory goes away (on Debian/Ubuntu, the whole cluster).

    1 shows what will go away: each database, its tables, its size, and the users
    2 offers a backup of everything (pg_dumpall) in your home directory — the default is yes
    3 asks you to type this machine's name — the lock against the wrong terminal
    4 stops the server and deletes; the localhost passwords in ~/.pgpass go too
    5 asks whether to run ratt db setup again right away

  --no-backup         skips the backup
  -y --confirm NAME   no questions (backup yes, setup no); the machine name goes on the command
                      line — without it, -y alone is refused

  Restoring the backup on a fresh PostgreSQL:  psql -f ~/ratt-backup-….sql postgres""",
    },
    "z.nada": {"pt": "não há PostgreSQL inicializado aqui: nada para zerar", "en": "no initialized PostgreSQL here: nothing to reset"},
    "z.y_sem_confirm": {"pt": "com -y, diga o nome da máquina: ratt db reset -y --confirm {maquina}",
                        "en": "with -y, name the machine: ratt db reset -y --confirm {maquina}"},
    "z.sem_terminal": {"pt": "sem terminal para confirmar: use -y --confirm <nome da máquina>",
                       "en": "no terminal to confirm: use -y --confirm <machine name>"},
    "z.aviso": {"pt": "isto apaga TODOS os bancos e usuários deste PostgreSQL:",
                "en": "this deletes EVERY database and user on this PostgreSQL:"},
    "z.sem_bancos": {"pt": "(nenhum banco além dos do sistema)", "en": "(no databases besides the system ones)"},
    "z.tabela": {"pt": "{n} tabela", "en": "{n} table"},
    "z.tabelas": {"pt": "{n} tabelas", "en": "{n} tables"},
    "z.usuarios": {"pt": "usuários: {nomes}", "en": "users: {nomes}"},
    "z.parado": {"pt": "o servidor está parado: não dá para ver o que tem dentro, nem fazer backup",
                 "en": "the server is stopped: can't see what's inside, nor back it up"},
    "z.backup": {"pt": "fazer backup de tudo antes?", "en": "back everything up first?"},
    "z.backup_feito": {"pt": "backup em {arquivo} ({tamanho})", "en": "backup in {arquivo} ({tamanho})"},
    "z.restaurar": {"pt": "para restaurar num PostgreSQL novo:  psql -f {arquivo} postgres",
                    "en": "to restore on a fresh PostgreSQL:  psql -f {arquivo} postgres"},
    "z.digite": {"pt": "para confirmar, digite o nome desta máquina ({maquina}):",
                 "en": "to confirm, type this machine's name ({maquina}):"},
    "z.cancelado": {"pt": "cancelado: nada foi apagado", "en": "cancelled: nothing was deleted"},
    "z.zerado": {"pt": "zerado: nenhum banco, nenhum usuário, como antes do setup",
                 "en": "reset: no databases, no users, as before setup"},
    "z.pgpass": {"pt": "{n} senha(s) de localhost:{porta} tirada(s) do ~/.pgpass",
                 "en": "{n} localhost:{porta} password(s) removed from ~/.pgpass"},
    "z.setup_agora": {"pt": "rodar o ratt db setup agora?", "en": "run ratt db setup now?"},

    # ── run ──
    "arg.run": {"pt": "<arquivo.sql>", "en": "<file.sql>"},
    "ajuda.run": {"pt": "roda um .sql comando por comando  (--dry-run · --watch)",
                  "en": "runs a .sql file statement by statement  (--dry-run · --watch)"},
    "ajuda_cmd.run": {
        "pt": """ratt db run <arquivo.sql> [--db BANCO] [--dry-run] [--watch] [--autocommit]

  Roda o arquivo no banco atual (ou --db), um comando de cada vez, e mostra o que cada um fez:
  quantas linhas, quanto tempo, e a tabela de um SELECT com o tipo embaixo de cada coluna, o
  vazio (NULL) em laranja e o texto com espaço sobrando em amarelo.

  O arquivo inteiro é uma transação: se um comando falha, os anteriores são desfeitos, e o erro
  aponta a linha do arquivo com um ^ embaixo do lugar.

  --dry-run      roda tudo e desfaz no fim: mostra o que o arquivo faria, sem gravar nada
  --watch        roda de novo cada vez que o arquivo é salvo (Ctrl+C para sair)
  --autocommit   cada comando grava sozinho — para os que não rodam numa transação
                 (CREATE DATABASE, VACUUM…); um erro no meio não desfaz os anteriores

  Comandos do psql (\\d, \\i…) não são SQL: aparecem marcados e são pulados.
  No VS Code: deixe  ratt db run --watch arquivo.sql  aberto no terminal e só salve o arquivo.""",
        "en": """ratt db run <file.sql> [--db DATABASE] [--dry-run] [--watch] [--autocommit]

  Runs the file on the current database (or --db), one statement at a time, and shows what each
  did: how many rows, how long, and a SELECT's table with each column's type under its name, NULL
  in orange and text with stray spaces in yellow.

  The whole file is one transaction: if a statement fails, the earlier ones are undone, and the
  error points at the file's line with a ^ under the spot.

  --dry-run      runs everything and undoes it at the end: shows what the file would do, saves nothing
  --watch        runs again every time the file is saved (Ctrl+C to quit)
  --autocommit   each statement saves on its own — for those that can't run in a transaction
                 (CREATE DATABASE, VACUUM…); an error midway doesn't undo the earlier ones

  psql commands (\\d, \\i…) aren't SQL: they show up marked and are skipped.
  In VS Code: keep  ratt db run --watch file.sql  open in the terminal and just save the file.""",
    },
    "run.sem_arquivo": {"pt": "diga o arquivo: ratt db run <arquivo.sql>", "en": "name the file: ratt db run <file.sql>"},
    "run.nao_existe": {"pt": "o arquivo {arquivo} não existe", "en": "file {arquivo} doesn't exist"},
    "run.sem_banco": {"pt": "nenhum banco atual: use --db <banco>, ou crie um com ratt db new <banco>",
                      "en": "no current database: use --db <database>, or create one with ratt db new <database>"},
    "run.sem_acesso": {"pt": "{usuario} não entra no banco {banco} — veja ratt db list e ratt db status",
                       "en": "{usuario} can't log in to database {banco} — see ratt db list and ratt db status"},
    "run.modo_ensaio": {"pt": "ensaio (--dry-run)", "en": "dry run"},
    "run.vazio": {"pt": "o arquivo não tem nenhum comando SQL", "en": "the file has no SQL statements"},
    "run.meta": {"pt": "comando do psql: pulado (o ratt db run só roda SQL)", "en": "psql command: skipped (ratt db run only runs SQL)"},
    "run.nenhuma_linha": {"pt": "nenhuma linha", "en": "no rows"},
    "run.linhas": {"pt": "{n} linhas", "en": "{n} rows"},
    "run.linhas.um": {"pt": "1 linha", "en": "1 row"},
    "run.inseridas": {"pt": "{n} linhas inseridas", "en": "{n} rows inserted"},
    "run.inseridas.um": {"pt": "1 linha inserida", "en": "1 row inserted"},
    "run.atualizadas": {"pt": "{n} linhas atualizadas", "en": "{n} rows updated"},
    "run.atualizadas.um": {"pt": "1 linha atualizada", "en": "1 row updated"},
    "run.apagadas": {"pt": "{n} linhas apagadas", "en": "{n} rows deleted"},
    "run.apagadas.um": {"pt": "1 linha apagada", "en": "1 row deleted"},
    "run.mescladas": {"pt": "{n} linhas mescladas", "en": "{n} rows merged"},
    "run.mescladas.um": {"pt": "1 linha mesclada", "en": "1 row merged"},
    "run.copiadas": {"pt": "{n} linhas copiadas", "en": "{n} rows copied"},
    "run.copiadas.um": {"pt": "1 linha copiada", "en": "1 row copied"},
    "run.inseridas.ensaio": {"pt": "inseriria {n} linhas", "en": "would insert {n} rows"},
    "run.inseridas.ensaio.um": {"pt": "inseriria 1 linha", "en": "would insert 1 row"},
    "run.atualizadas.ensaio": {"pt": "atualizaria {n} linhas", "en": "would update {n} rows"},
    "run.atualizadas.ensaio.um": {"pt": "atualizaria 1 linha", "en": "would update 1 row"},
    "run.apagadas.ensaio": {"pt": "apagaria {n} linhas", "en": "would delete {n} rows"},
    "run.apagadas.ensaio.um": {"pt": "apagaria 1 linha", "en": "would delete 1 row"},
    "run.mescladas.ensaio": {"pt": "mesclaria {n} linhas", "en": "would merge {n} rows"},
    "run.mescladas.ensaio.um": {"pt": "mesclaria 1 linha", "en": "would merge 1 row"},
    "run.copiadas.ensaio": {"pt": "copiaria {n} linhas", "en": "would copy {n} rows"},
    "run.copiadas.ensaio.um": {"pt": "copiaria 1 linha", "en": "would copy 1 row"},
    "run.nulos": {"pt": "{col}: {n} vazio(s) (NULL) de {de}", "en": "{col}: {n} empty (NULL) of {de}"},
    "run.pontas": {"pt": "{col}: {n} valor(es) com espaço nas pontas", "en": "{col}: {n} value(s) with leading/trailing spaces"},
    "run.texto_vazio": {"pt": "{col}: {n} texto(s) vazio(s) '' — não é NULL", "en": "{col}: {n} empty string(s) '' — not NULL"},
    "run.mais_linhas": {"pt": "mais {n} linhas (mostrando as {mostradas} primeiras)",
                        "en": "{n} more rows (showing the first {mostradas})"},
    "run.erro_linha": {"pt": "erro na linha {linha} de {arquivo}", "en": "error on line {linha} of {arquivo}"},
    "run.desfeitos": {"pt": "os comandos anteriores foram desfeitos: nada foi gravado",
                      "en": "the earlier statements were undone: nothing was saved"},
    "run.fora_de_transacao": {"pt": "esse comando não roda dentro de uma transação: rode com --autocommit",
                              "en": "this statement can't run inside a transaction: use --autocommit"},
    "run.ensaio_fim": {"pt": "ensaio: {n} comando(s) rodaram e foram desfeitos — nada foi gravado",
                       "en": "dry run: {n} statement(s) ran and were undone — nothing was saved"},
    "run.gravado": {"pt": "{n} de {total} comandos · gravado", "en": "{n} of {total} statements · saved"},
    "run.esperando": {"pt": "esperando {arquivo} ser salvo de novo… (Ctrl+C sai)",
                      "en": "waiting for {arquivo} to be saved again… (Ctrl+C quits)"},
    "run.salvo": {"pt": "salvo às {hora}", "en": "saved at {hora}"},

    # ── a tela (TUI) ──
    "ajuda.tela": {"pt": "sem comando, num terminal: abre a tela do banco atual (--db: outro)",
                   "en": "no command, in a terminal: opens the current database's screen (--db: another)"},
    "tui.sair": {"pt": "sair", "en": "quit"},
    "tui.recarregar": {"pt": "recarregar", "en": "reload"},
    "tui.banco": {"pt": "trocar banco", "en": "switch database"},
    "tui.proxima": {"pt": "próx. página", "en": "next page"},
    "tui.anterior": {"pt": "pág. anterior", "en": "prev page"},
    "tui.voltar": {"pt": "voltar", "en": "back"},
    "tui.qual_banco": {"pt": "qual banco abrir?", "en": "which database to open?"},
    "tui.dados": {"pt": "Dados", "en": "Data"},
    "tui.estrutura": {"pt": "Estrutura", "en": "Structure"},
    "tui.relacoes": {"pt": "Relações", "en": "Relations"},
    "tui.tabelas": {"pt": "Tabelas", "en": "Tables"},
    "tui.views": {"pt": "Views", "en": "Views"},
    "tui.funcoes": {"pt": "Funções", "en": "Functions"},
    "tui.sequencias": {"pt": "Sequências", "en": "Sequences"},
    "tui.tipo.tabela": {"pt": "tabela", "en": "table"},
    "tui.tipo.view": {"pt": "view", "en": "view"},
    "tui.tipo.funcao": {"pt": "função", "en": "function"},
    "tui.tipo.sequencia": {"pt": "sequência", "en": "sequence"},
    "tui.linhas": {"pt": "{n} linhas", "en": "{n} rows"},
    "tui.mostrando": {"pt": "mostrando {inicio}–{fim}", "en": "showing {inicio}–{fim}"},
    "tui.vazio": {"pt": "nenhuma tabela ainda: escreva um .sql e rode com  ratt db run arquivo.sql — a tela se atualiza sozinha",
                  "en": "no tables yet: write a .sql and run it with  ratt db run file.sql — the screen updates by itself"},
    "tui.sem_dados": {"pt": "este objeto não tem linhas para mostrar — veja a aba SQL",
                      "en": "this object has no rows to show — see the SQL tab"},
    "tui.sem_estrutura": {"pt": "sem colunas — veja a aba SQL", "en": "no columns — see the SQL tab"},
    "tui.sem_relacoes_tipo": {"pt": "relações (chaves estrangeiras) são de tabelas", "en": "relations (foreign keys) belong to tables"},
    "tui.c.num": {"pt": "#", "en": "#"},
    "tui.c.coluna": {"pt": "coluna", "en": "column"},
    "tui.c.tipo": {"pt": "tipo", "en": "type"},
    "tui.c.vazio": {"pt": "aceita vazio", "en": "nullable"},
    "tui.c.padrao": {"pt": "padrão", "en": "default"},
    "tui.c.chave": {"pt": "chave", "en": "key"},
    "tui.pode": {"pt": "sim", "en": "yes"},
    "tui.nao": {"pt": "não (NOT NULL)", "en": "no (NOT NULL)"},
    "tui.indices": {"pt": "Índices", "en": "Indexes"},
    "tui.restricoes": {"pt": "Restrições", "en": "Constraints"},
    "tui.saem": {"pt": "Saem desta tabela (ela aponta para)", "en": "Going out (this table points to)"},
    "tui.chegam": {"pt": "Chegam nesta tabela (apontam para ela)", "en": "Coming in (pointing at this table)"},
    "tui.nenhuma": {"pt": "nenhuma", "en": "none"},
    "tui.mudou": {"pt": "o banco mudou — atualizado", "en": "the database changed — updated"},
    "tui.volta_com_q": {"pt": "\\q volta para a tela", "en": "\\q goes back to the screen"},
    # ── o canvas: avisos e checagens ──
    "chk.sem_pk": {"pt": "{tabela} não tem chave primária: as linhas não têm identidade própria",
                   "en": "{tabela} has no primary key: rows have no identity of their own"},
    "chk.fk_sem_indice": {"pt": "{tabela}.{coluna} não tem índice: juntar e apagar o pai fica lento",
                          "en": "{tabela}.{coluna} has no index: joins and parent deletes get slow"},
    "chk.fk_opcional": {"pt": "{tabela}.{coluna} aceita NULL: a ligação é opcional",
                        "en": "{tabela}.{coluna} accepts NULL: the link is optional"},
    "chk.sem_tempo": {"pt": "a contagem demorou demais e foi cancelada", "en": "counting took too long and was cancelled"},
    "chk.indeterminado": {"pt": "ainda não dá para contar: depende de algo criado no rascunho",
                          "en": "cannot count yet: it depends on something created in the draft"},
    "chk.nulos_impedem": {"pt": "{coluna} tem {n} linhas com NULL: o PostgreSQL recusaria NOT NULL",
                          "en": "{coluna} has {n} NULL rows: PostgreSQL would refuse NOT NULL"},
    "chk.obrigatoria_sem_padrao": {"pt": "{coluna} é obrigatória e sem padrão, mas a tabela tem {n} linhas: o PostgreSQL recusaria",
                                   "en": "{coluna} is required with no default, but the table has {n} rows: PostgreSQL would refuse"},
    "chk.repetidos_impedem": {"pt": "{coluna} tem {n} valores repetidos: o PostgreSQL recusaria UNIQUE",
                              "en": "{coluna} has {n} repeated values: PostgreSQL would refuse UNIQUE"},
    "chk.orfaos_impedem": {"pt": "{coluna} tem {n} linhas sem pai: o PostgreSQL recusaria a chave estrangeira",
                           "en": "{coluna} has {n} rows with no parent: PostgreSQL would refuse the foreign key"},
    "chk.tipos": {"pt": "{coluna} é {tipo} e {pai} é {tipo_pai}: tipos diferentes, o PostgreSQL recusaria",
                  "en": "{coluna} is {tipo} and {pai} is {tipo_pai}: different types, PostgreSQL would refuse"},
    "chk.um_para_um": {"pt": "sem UNIQUE em {coluna} o banco aceitaria vários filhos por pai",
                       "en": "without UNIQUE on {coluna} the database would accept many children per parent"},
    "chk.dep_fk": {"pt": "tabela {nome} (chave estrangeira {restricao})",
                   "en": "table {nome} (foreign key {restricao})"},
    "chk.dep_view": {"pt": "view {nome}", "en": "view {nome}"},
    "chk.dependentes": {"pt": "{nome} tem dependentes: {lista}", "en": "{nome} has dependents: {lista}"},
    "chk.precisa_cascata": {"pt": "{nome} tem dependentes: sem CASCADE o PostgreSQL recusaria apagar",
                            "en": "{nome} has dependents: without CASCADE PostgreSQL would refuse the drop"},
    "chk.linhas_somem": {"pt": "as {n} linhas de {tabela} somem junto", "en": "the {n} rows of {tabela} go away too"},
    "chk.leitura_insegura": {"pt": "a transação não ficou somente leitura: a checagem não rodou",
                             "en": "the transaction did not become read-only: the check did not run"},
    "chk.tipo_recusado": {"pt": "tipo que não sei validar: {tipo}", "en": "type I cannot validate: {tipo}"},
    "chk.consulta_falhou": {"pt": "não deu para contar: {erro}", "en": "could not count: {erro}"},
    "chk.so_depois_de_aplicar": {"pt": "{tabela} tem tabela ou coluna que só existe no rascunho: as contagens só depois de aplicar",
                                 "en": "{tabela} has a table or column that only exists in the draft: counts only after applying"},
    "chk.aproximada": {"pt": "contagens aproximadas — não consideram {n} mudança(s) pendente(s) nesta tabela",
                       "en": "approximate counts — they ignore {n} pending change(s) to this table"},
    "chk.linhas_alteradas": {"pt": "{n} linha(s) de {tabela} mudam", "en": "{n} row(s) of {tabela} change"},
    "chk.linhas_apagadas": {"pt": "{n} linha(s) de {tabela} saem", "en": "{n} row(s) of {tabela} go away"},
    "chk.grupos_repetidos": {"pt": "{grupos} grupo(s) de linhas repetidas", "en": "{grupos} group(s) of repeated rows"},
    "chk.duplicatas_vazias_fora": {"pt": "{n} linha(s) com vazio numa coluna comparada ficam fora da comparação (vazio não é repetido)",
                                   "en": "{n} row(s) with an empty value in a compared column are left out (empty is not a repeat)"},
    "chk.sem_pk_ctid": {"pt": "{tabela} não tem chave primária: as linhas são achadas pelo ctid, o último recurso",
                        "en": "{tabela} has no primary key: rows are found by ctid, the last resort"},
    "chk.cascata_apaga": {"pt": "{n} linha(s) de {tabela} são apagadas junto (ON DELETE CASCADE)",
                          "en": "{n} row(s) of {tabela} are deleted too (ON DELETE CASCADE)"},
    "chk.cascata_esvazia": {"pt": "{n} linha(s) de {tabela} ficam com a ligação vazia (ON DELETE SET NULL)",
                            "en": "{n} row(s) of {tabela} lose the link (ON DELETE SET NULL)"},
    "chk.cascata_padrao": {"pt": "{n} linha(s) de {tabela} voltam ao valor padrão (ON DELETE SET DEFAULT)",
                           "en": "{n} row(s) of {tabela} go back to the default (ON DELETE SET DEFAULT)"},
    "chk.cascata_nulo_impede": {"pt": "{n} linha(s) de {tabela} ficariam com a ligação vazia, mas a coluna é obrigatória: o PostgreSQL recusaria apagar",
                                "en": "{n} row(s) of {tabela} would lose the link, but the column is required: PostgreSQL would refuse to delete"},
    "chk.cascata_apaga_pelo_menos": {"pt": "pelo menos {n} linha(s) de {tabela} são apagadas junto (ON DELETE CASCADE)",
                                     "en": "at least {n} row(s) of {tabela} are deleted too (ON DELETE CASCADE)"},
    "chk.cascata_incompleta": {"pt": "a cascata não foi olhada até o fim (fundo demais, demorou ou deu volta): o resto, inclusive o que impediria, fica para o ensaio",
                               "en": "the cascade was not followed to the end (too deep, too slow or circular): the rest, including what would block, is left to the dry run"},
    "chk.sem_permissao_ver": {"pt": "sem permissão para ler {tabela}: o que acontece lá ao apagar não foi contado",
                              "en": "no permission to read {tabela}: what happens there on delete was not counted"},
    "chk.impede_filhos": {"pt": "{n} linha(s) de {tabela} apontam para estas: o PostgreSQL recusaria apagar",
                          "en": "{n} row(s) of {tabela} point at these: PostgreSQL would refuse to delete"},
    "chk.soft_delete": {"pt": "{coluna} existe: talvez seja um UPDATE dela em vez de apagar",
                        "en": "{coluna} exists: maybe this is an UPDATE of it instead of a delete"},
    "chk.obrigatoria_vazia": {"pt": "{coluna} é obrigatória e está vazia: o PostgreSQL recusaria",
                              "en": "{coluna} is required and empty: PostgreSQL would refuse"},
    "chk.valor_invalido": {"pt": "«{valor}» não serve para {coluna} ({tipo})", "en": "«{valor}» does not fit {coluna} ({tipo})"},
    "chk.ja_existe": {"pt": "«{valor}» já existe em {coluna} ({n} linha(s)): o PostgreSQL recusaria o repetido",
                      "en": "«{valor}» already exists in {coluna} ({n} row(s)): PostgreSQL would refuse the repeat"},
    "chk.fk_inexistente": {"pt": "«{valor}» não existe em {pai}: o PostgreSQL recusaria {coluna}",
                           "en": "«{valor}» does not exist in {pai}: PostgreSQL would refuse {coluna}"},
    "chk.espacos_valor": {"pt": "o valor de {coluna} tem espaço sobrando nas pontas",
                          "en": "the value for {coluna} has extra spaces at the ends"},
    "chk.obrigatoria_recebe_vazio": {"pt": "{coluna} é obrigatória e ficaria vazia: o PostgreSQL recusaria",
                                     "en": "{coluna} is required and would become empty: PostgreSQL would refuse"},
    "chk.obrigatoria_recebe_vazio_n": {"pt": "{coluna} é obrigatória e ficaria vazia em {n} linha(s): o PostgreSQL recusaria",
                                       "en": "{coluna} is required and would become empty in {n} row(s): PostgreSQL would refuse"},
    "chk.unica_varias": {"pt": "{coluna} é única e o mesmo valor iria para {n} linhas: o PostgreSQL recusaria",
                         "en": "{coluna} is unique and the same value would go to {n} rows: PostgreSQL would refuse"},
    "chk.vazios_de_fora": {"pt": "{n} linha(s) com {coluna} vazia ficam de fora", "en": "{n} row(s) with empty {coluna} are left out"},
    "chk.fora_lista_vazio": {"pt": "«não está na lista» com vazio na lista não pega nada (NOT IN com NULL)",
                             "en": "«not in list» with an empty value in the list matches nothing (NOT IN with NULL)"},
    "chk.espacos_nas_pontas": {"pt": "{n} linha(s) de {coluna} têm espaço sobrando nas pontas e por isso não casam",
                               "en": "{n} row(s) of {coluna} have extra spaces at the ends and so do not match"},
    # ── o canvas: a descrição de cada operação do rascunho ──
    "op.criar_tabela": {"pt": "criar a tabela {tabela}", "en": "create table {tabela}"},
    "op.adicionar_coluna": {"pt": "adicionar a coluna {coluna} em {tabela}", "en": "add column {coluna} to {tabela}"},
    "op.renomear_tabela": {"pt": "renomear a tabela {tabela} para {novo}", "en": "rename table {tabela} to {novo}"},
    "op.renomear_coluna": {"pt": "renomear a coluna {tabela}.{coluna} para {novo}",
                           "en": "rename column {tabela}.{coluna} to {novo}"},
    "op.obrigatoria_sim": {"pt": "tornar {tabela}.{coluna} obrigatória", "en": "make {tabela}.{coluna} required"},
    "op.obrigatoria_nao": {"pt": "tornar {tabela}.{coluna} opcional", "en": "make {tabela}.{coluna} optional"},
    "op.unica_sim": {"pt": "tornar {tabela}.{coluna} única", "en": "make {tabela}.{coluna} unique"},
    "op.unica_nao": {"pt": "tirar a unicidade de {tabela}.{coluna}", "en": "drop uniqueness of {tabela}.{coluna}"},
    "op.ligar": {"pt": "ligar {origem} → {destino}", "en": "link {origem} → {destino}"},
    "op.ligar_nn": {"pt": "ligar {a} e {b} com a tabela {tabela} (muitos para muitos)",
                    "en": "link {a} and {b} with table {tabela} (many to many)"},
    "op.apagar_ligacao": {"pt": "apagar a ligação {origem} → {destino}", "en": "drop link {origem} → {destino}"},
    "op.apagar_coluna": {"pt": "apagar a coluna {coluna} de {tabela}", "en": "drop column {coluna} from {tabela}"},
    "op.apagar_tabela": {"pt": "apagar a tabela {tabela}", "en": "drop table {tabela}"},
    "op.inserir": {"pt": "inserir uma linha em {tabela} ({n} valores)", "en": "insert a row into {tabela} ({n} values)"},
    "op.inserir_um": {"pt": "inserir uma linha em {tabela} ({n} valor)", "en": "insert a row into {tabela} ({n} value)"},
    "op.e.pk_apagada": {"pt": "a chave primária perdeu uma coluna no rascunho", "en": "the primary key lost a column in the draft"},
    "op.alterar": {"pt": "alterar {colunas} em {tabela} ({filtro})", "en": "change {colunas} in {tabela} ({filtro})"},
    "op.apagar_linhas": {"pt": "apagar linhas de {tabela} ({filtro})", "en": "delete rows from {tabela} ({filtro})"},
    "op.apagar_duplicatas": {"pt": "apagar duplicatas de {tabela} repetidas por {colunas} ({filtro})",
                             "en": "delete duplicates in {tabela} repeated by {colunas} ({filtro})"},
    "op.todas_as_linhas": {"pt": "todas as linhas", "en": "all rows"},
    "op.e.sem_valor": {"pt": "falta o valor para {coluna}", "en": "missing value for {coluna}"},
    "op.e.sem_coluna": {"pt": "falta a coluna de origem para {coluna}", "en": "missing source column for {coluna}"},
    "op.e.so_texto": {"pt": "{coluna} não é de texto: não dá para limpar", "en": "{coluna} is not text: cannot clean it"},
    "op.e.mudanca": {"pt": "mudança desconhecida: {mudanca}", "en": "unknown change: {mudanca}"},
    "op.e.sem_mudancas": {"pt": "escolha ao menos uma coluna para alterar", "en": "pick at least one column to change"},
    "op.e.sem_por": {"pt": "escolha ao menos uma coluna para achar as repetidas", "en": "pick at least one column to find repeats"},
    "op.e.manter": {"pt": "não sei manter assim: {valor}", "en": "unknown way to keep: {valor}"},
    "op.e.n": {"pt": "quantas ficam tem de ser um inteiro a partir de 1 (veio {valor})",
               "en": "how many to keep must be an integer from 1 (got {valor})"},
    "op.e.sem_ordem": {"pt": "falta a coluna de data para manter a mais recente", "en": "missing the date column to keep the most recent"},
    "canvas.diagrama": {"pt": "diagrama", "en": "diagram"},
    "canvas.titulo": {"pt": "Diagrama", "en": "Diagram"},
    "canvas.rascunho": {"pt": "rascunho: {n}", "en": "draft: {n}"},
    "canvas.sem_rascunho": {"pt": "Sem alterações pendentes.", "en": "No pending changes."},
    "canvas.vazio": {"pt": "Nenhuma tabela neste schema — a: nova tabela",
                     "en": "No tables in this schema — a: new table"},
    "canvas.k.proxima": {"pt": "próxima", "en": "next"},
    "canvas.k.anterior": {"pt": "anterior", "en": "previous"},
    "canvas.k.coluna_acima": {"pt": "coluna acima", "en": "column up"},
    "canvas.k.coluna_abaixo": {"pt": "coluna abaixo", "en": "column down"},
    "canvas.k.mover": {"pt": "mover caixa", "en": "move box"},
    "canvas.k.esquema": {"pt": "schema", "en": "schema"},
    "canvas.k.foco": {"pt": "foco", "en": "focus"},
    "canvas.k.tudo": {"pt": "tudo", "en": "show all"},
    "canvas.k.voltar": {"pt": "voltar", "en": "back"},
    "canvas.k.rolar": {"pt": "rolar: setas/PgUp/PgDn", "en": "scroll: arrows/PgUp/PgDn"},
    "canvas.erro_banco": {"pt": "Não consegui ler o banco: {erro}", "en": "Could not read the database: {erro}"},
    "canvas.qual_esquema": {"pt": "Qual schema?", "en": "Which schema?"},
    "canvas.qual_foco": {"pt": "Foco em qual tabela? (parte do nome)", "en": "Focus on which table? (part of the name)"},
    "canvas.foco_nao_achou": {"pt": "Nenhuma tabela com «{nome}».", "en": "No table matching \u00ab{nome}\u00bb."},
    "canvas.em_foco": {"pt": "foco: {tabela}", "en": "focus: {tabela}"},
    "canvas.invalida": {"pt": "inválida: o banco mudou e ela não vale mais (tire-a do rascunho)",
                        "en": "invalid: the database changed and it no longer applies (remove it from the draft)"},
    "canvas.invalidas": {"pt": "{n} operação(ões) do rascunho deixaram de valer: o banco mudou",
                         "en": "{n} draft operation(s) no longer apply: the database changed"},
    "canvas.corrompido": {"pt": "O rascunho salvo estava ilegível; guardei a cópia em {caminho} e comecei vazio.",
                          "en": "The saved draft was unreadable; I kept a copy at {caminho} and started empty."},
    "canvas.erro_gravar": {"pt": "Não consegui gravar o rascunho: {erro}", "en": "Could not save the draft: {erro}"},
    "canvas.carregando": {"pt": "Ainda lendo o banco…", "en": "Still reading the database…"},
    "canvas.sel_tabela_ou_coluna": {"pt": "Selecione uma tabela ou uma coluna (tab, ou clique nela).",
                                    "en": "Select a table or a column (tab, or click it)."},
    "canvas.sel_qualquer": {"pt": "Selecione uma tabela, coluna ou ligação (tab, ou clique nela).",
                            "en": "Select a table, column or link (tab, or click it)."},
    "canvas.sem_menu": {"pt": "Não há o que fazer com uma ligação aqui: x apaga.", "en": "Nothing to do with a link here: x deletes it."},
    "canvas.nada_desfazer": {"pt": "O rascunho está vazio: nada a desfazer.", "en": "The draft is empty: nothing to undo."},
    "canvas.k.tabela": {"pt": "nova tabela", "en": "new table"},
    "canvas.k.coluna": {"pt": "nova coluna", "en": "new column"},
    "canvas.k.ligar": {"pt": "ligar", "en": "link"},
    "canvas.k.editar": {"pt": "editar", "en": "edit"},
    "canvas.k.apagar": {"pt": "apagar", "en": "delete"},
    "canvas.k.desfazer": {"pt": "desfazer", "en": "undo"},
    "canvas.k.rascunho": {"pt": "rascunho", "en": "draft"},
    "canvas.k.tirar": {"pt": "tirar do rascunho", "en": "remove from draft"},
    "canvas.aplicando": {"pt": "Acabei de aplicar: espere a releitura do banco.", "en": "Just applied: wait for the database to be re-read."},
    "canvas.k.aplicar": {"pt": "aplicar", "en": "apply"},
    "canvas.k.ensaio": {"pt": "ensaio", "en": "dry run"},
    "canvas.k.exportar": {"pt": "exportar .sql", "en": "export .sql"},
    "canvas.rascunho_vazio": {"pt": "O rascunho está vazio: não há o que enviar.", "en": "The draft is empty: nothing to send."},
    "canvas.tem_invalidas": {"pt": "{n} operação(ões) deixaram de valer: tire-as do rascunho antes.",
                             "en": "{n} operation(s) no longer hold: remove them from the draft first."},
    "canvas.cabecalho_sql": {"pt": "ratt db · rascunho de {banco}.{esquema} · {n} operações",
                             "en": "ratt db · draft of {banco}.{esquema} · {n} operations"},
    "canvas.run_rascunho": {"pt": "rascunho", "en": "draft"},
    "canvas.enter_volta": {"pt": "Enter para voltar ao desenho", "en": "Press Enter to go back to the diagram"},
    "canvas.falhou": {"pt": "✗ falhou: nada foi gravado", "en": "✗ failed: nothing was written"},
    "canvas.ensaio_ok": {"pt": "Ensaio ok: tudo rodou e foi desfeito.", "en": "Dry run ok: everything ran and was rolled back."},
    "canvas.ensaio_falhou": {"pt": "O ensaio falhou: veja a operação marcada no painel.", "en": "The dry run failed: see the marked operation in the panel."},
    "canvas.aplicado": {"pt": "Rascunho aplicado ({caminho}).", "en": "Draft applied ({caminho})."},
    "canvas.aplicar_falhou": {"pt": "Falhou: nada foi gravado. A operação com problema está marcada.",
                              "en": "Failed: nothing was written. The offending operation is marked."},
    "dados.pegou_viu": {"pt": "pegou {x} (você viu {y})", "en": "caught {x} (you saw {y})"},
    "dados.banco_mudou": {"pt": "O banco mudou desde que você viu: pegou {x} linhas, você viu {y}. Nada foi gravado.",
                          "en": "The database changed since you looked: caught {x} rows, you saw {y}. Nothing was written."},
    "canvas.sem_conexao": {"pt": "Não consegui abrir uma conexão para aplicar.", "en": "Could not open a connection to apply."},
    "canvas.erro_aplicar": {"pt": "Não consegui preparar o aplicar: {erro}", "en": "Could not prepare the apply: {erro}"},
    "canvas.exportado": {"pt": "Rascunho exportado para {caminho}", "en": "Draft exported to {caminho}"},
    "canvas.erro_exportar": {"pt": "Não consegui gravar {caminho}: {erro}", "en": "Could not write {caminho}: {erro}"},
    "canvas.f.caminho": {"pt": "Exportar para qual arquivo?", "en": "Export to which file?"},
    "canvas.f.aplicar_pergunta": {"pt": "Aplicar ao banco {banco} estas {n} operações? Tudo roda numa transação.",
                                  "en": "Apply these {n} operations to {banco}? It all runs in one transaction."},
    "canvas.f.aplicar": {"pt": "aplicar", "en": "apply"},
    "canvas.f.sobrescrever": {"pt": "{caminho} já existe. Sobrescrever?", "en": "{caminho} already exists. Overwrite?"},
    "canvas.f.sobrescrever_sim": {"pt": "sobrescrever", "en": "overwrite"},
    "canvas.k.escolher": {"pt": "escolher", "en": "choose"},
    "canvas.f.nova_tabela": {"pt": "Nova tabela", "en": "New table"},
    "canvas.f.nova_coluna": {"pt": "Nova coluna em {tabela}", "en": "New column in {tabela}"},
    "canvas.f.ligar": {"pt": "Ligar {origem}", "en": "Link {origem}"},
    "canvas.f.renomear": {"pt": "Renomear {nome}", "en": "Rename {nome}"},
    "canvas.f.obrigatoria": {"pt": "Tornar {nome} obrigatória", "en": "Make {nome} required"},
    "canvas.f.opcional": {"pt": "Tornar {nome} opcional", "en": "Make {nome} optional"},
    "canvas.f.unica": {"pt": "Tornar {nome} única", "en": "Make {nome} unique"},
    "canvas.f.nao_unica": {"pt": "Tirar a unicidade de {nome}", "en": "Drop uniqueness of {nome}"},
    "canvas.f.apagar": {"pt": "Apagar {nome}", "en": "Delete {nome}"},
    "canvas.f.nome": {"pt": "nome", "en": "name"},
    "canvas.f.tipo": {"pt": "tipo", "en": "type"},
    "canvas.f.obrig": {"pt": "obrigatória (NOT NULL)", "en": "required (NOT NULL)"},
    "canvas.f.padrao": {"pt": "valor padrão (expressão SQL, opcional)", "en": "default value (SQL expression, optional)"},
    "canvas.f.colunas": {"pt": "colunas da tabela:", "en": "table columns:"},
    "canvas.f.outra_coluna": {"pt": "acrescentar uma coluna:", "en": "add a column:"},
    "canvas.f.add_coluna": {"pt": "+ coluna", "en": "+ column"},
    "canvas.f.tirar_coluna": {"pt": "− última coluna", "en": "− last column"},
    "canvas.f.pr": {"pt": "pôr no rascunho", "en": "add to draft"},
    "canvas.f.cancelar": {"pt": "cancelar", "en": "cancel"},
    "canvas.f.sql": {"pt": "SQL que isto gera:", "en": "SQL this generates:"},
    "canvas.f.contando": {"pt": "contando…", "en": "counting…"},
    "canvas.f.sem_avisos": {"pt": "nenhum aviso", "en": "no warnings"},
    "canvas.f.destino": {"pt": "ligar a qual tabela? (setas e enter, ou clique)", "en": "link to which table? (arrows and enter, or click)"},
    "canvas.f.cada": {"pt": "cada {destino} tem quantos {origem}?", "en": "how many {origem} does each {destino} have?"},
    "canvas.f.varios": {"pt": "vários", "en": "many"},
    "canvas.f.um": {"pt": "no máximo um", "en": "at most one"},
    "canvas.f.ao_apagar": {"pt": "ao apagar o pai", "en": "when the parent is deleted"},
    "canvas.f.indice": {"pt": "criar índice", "en": "create an index"},
    "canvas.f.nn": {"pt": "como N:N (cria uma tabela de junção)", "en": "as N:N (creates a join table)"},
    "canvas.f.nome_nn": {"pt": "nome da tabela de junção", "en": "join table name"},
    "canvas.f.coluna_nova": {"pt": "nome da nova coluna", "en": "new column name"},
    "canvas.f.cascata": {"pt": "apagar também os dependentes (CASCADE)", "en": "also delete the dependents (CASCADE)"},
    "canvas.f.o_que": {"pt": "Isto vai: {descricao}", "en": "This will: {descricao}"},
    "canvas.f.tirar_pergunta": {"pt": "Estas operações dependem da escolhida e saem junto:",
                                "en": "These operations depend on the chosen one and go with it:"},
    "canvas.f.tirar_todas": {"pt": "tirar todas", "en": "remove all"},
    "canvas.f.menu": {"pt": "O que fazer com {nome}?", "en": "What to do with {nome}?"},
    "canvas.m.renomear": {"pt": "renomear", "en": "rename"},
    "canvas.m.obrigatoria": {"pt": "tornar obrigatória", "en": "make required"},
    "canvas.m.opcional": {"pt": "tornar opcional", "en": "make optional"},
    "canvas.m.unica": {"pt": "tornar única", "en": "make unique"},
    "canvas.m.nao_unica": {"pt": "tornar não única", "en": "make not unique"},
    "canvas.e.sumiu": {"pt": "o que você selecionou não existe mais no banco", "en": "what you selected no longer exists in the database"},
    "canvas.e.nome_vazio": {"pt": "dê um nome", "en": "give it a name"},
    "canvas.e.nome_existe": {"pt": "já existe «{nome}»", "en": "«{nome}» already exists"},
    "canvas.e.tipo": {"pt": "tipo inválido: {tipo}", "en": "invalid type: {tipo}"},
    "canvas.e.sem_destino": {"pt": "escolha a tabela de destino", "en": "choose the destination table"},
    "canvas.e.sem_pk": {"pt": "{tabela} não tem uma chave primária de uma coluna só: não dá para apontar para ela",
                        "en": "{tabela} has no single-column primary key: nothing to point to"},
    "canvas.e.operacao": {"pt": "não deu para montar o SQL: {erro}", "en": "could not build the SQL: {erro}"},
    "canvas.interrompido": {"pt": "interrompido (Ctrl+C): nada foi gravado", "en": "interrupted (Ctrl+C): nothing was written"},
    "canvas.sem_gravar": {"pt": "Não consegui ler o rascunho salvo ({erro}); nesta sessão o rascunho deste schema não será gravado, para não apagar o que está lá.",
                          "en": "Could not read the saved draft ({erro}); this session will not save this schema's draft, so nothing there is overwritten."},
    "canvas.desenhando": {"pt": "desenhando…", "en": "drawing…"},
    "canvas.tudo_pergunta": {"pt": "Desenhar as {n} tabelas de uma vez? Pode demorar e o desenho fica pesado.",
                             "en": "Draw all {n} tables at once? It may take a while and the drawing gets heavy."},
    "canvas.tudo_sim": {"pt": "Desenhar tudo", "en": "Draw all"},
    "canvas.lendo": {"pt": "Lendo o banco…", "en": "Reading the database…"},
    "canvas.sem_leitura": {"pt": "Não consegui ler este schema; tento de novo a cada 2 s.",
                           "en": "Could not read this schema; retrying every 2 s."},
    "canvas.d.titulo": {"pt": "Isto apaga:", "en": "This deletes:"},
    "canvas.d.tabela": {"pt": "as {n} linhas dela somem junto", "en": "its {n} rows go with it"},
    "canvas.d.nao_contado": {"pt": "linhas: não contado", "en": "rows: not counted"},
    "canvas.d.coluna": {"pt": "os valores dela somem de todas as linhas", "en": "its values go from every row"},
    "canvas.d.restricao": {"pt": "só a regra some, nenhum dado", "en": "only the rule goes, no data"},
    "canvas.d.cascata": {"pt": " — e, com CASCADE, o que depende dela", "en": " — and, with CASCADE, what depends on it"},
    "canvas.k.expandir": {"pt": "expandir ←N", "en": "expand ←N"},
    "canvas.em_foco_corte": {"pt": "foco: {tabela} ({n} de {total} tabelas)", "en": "focus: {tabela} ({n} of {total} tables)"},
    "canvas.e.nao_existe": {"pt": "«{nome}» não existe mais ou foi apagado no rascunho",
                            "en": "«{nome}» no longer exists or was deleted in the draft"},
    "canvas.e.ao_apagar": {"pt": "ação ao apagar inválida: {valor}", "en": "invalid on-delete action: {valor}"},
    "canvas.e.um_comando": {"pt": "isto geraria mais de um comando SQL (ou um comentário): confira o tipo e o valor padrão",
                            "en": "this would produce more than one SQL statement (or a comment): check the type and the default value"},
    "op.ligar_nn_mesma": {"pt": "ligar {a} a ela mesma com a tabela {tabela} ({col_a} e {col_b}, muitos para muitos)",
                          "en": "link {a} to itself with table {tabela} ({col_a} and {col_b}, many to many)"},
    "chk.tipos_familia": {"pt": "{coluna} é {tipo} e {pai} é {tipo_pai}: tipos diferentes da mesma família; o PostgreSQL aceita a ligação se conseguir comparar os dois (o ensaio confirma) — o mesmo tipo dos dois lados é o mais simples",
                          "en": "{coluna} is {tipo} and {pai} is {tipo_pai}: different types of the same family; PostgreSQL accepts the link if it can compare them (the dry run confirms) — the same type on both sides is simplest"},
    "canvas.fonte_fechada": {"pt": "o canvas já fechou a conexão", "en": "the canvas already closed its connection"},
    "canvas.erro_posicoes": {"pt": "Não consegui gravar as posições das caixas: {erro}", "en": "Could not save the box positions: {erro}"},
    "canvas.sql_mudou": {"pt": "O rascunho mudou depois da confirmação: confira o SQL de novo.",
                         "en": "The draft changed after you confirmed: check the SQL again."},
    "canvas.e.nome_igual": {"pt": "é o mesmo nome", "en": "that is the same name"},
    "canvas.aplicar_falhou_sem_marca": {"pt": "Falhou: nada foi gravado. Veja o erro na saída do ratt db run.",
                                        "en": "Failed: nothing was written. See the error in the ratt db run output."},
    "canvas.ensaio_falhou_sem_marca": {"pt": "O ensaio falhou: veja o erro na saída do ratt db run.",
                                       "en": "The dry run failed: see the error in the ratt db run output."},
    "canvas.dica_exportado": {"pt": "Para rodar tudo numa transação só (falhou um, nada fica):\n  ratt db run {arquivo}\nou, fora do ratt:\n  psql -1 -f {arquivo}",
                              "en": "To run it all in a single transaction (one fails, nothing stays):\n  ratt db run {arquivo}\nor, outside ratt:\n  psql -1 -f {arquivo}"},
    # ── a tela Dados ──
    "dados.abrir": {"pt": "dados", "en": "data"},
    "dados.titulo": {"pt": "Dados", "en": "Data"},
    "dados.filtro": {"pt": "filtro:", "en": "filter:"},
    "dados.filtro_texto": {"pt": "filtro (WHERE escrito à mão):", "en": "filter (hand-written WHERE):"},
    "dados.dicas": {"pt": "(f condição · F limpa · t texto · o E/OU · ⌫ tira a última)",
                    "en": "(f condition · F clear · t text · o AND/OR · ⌫ drop the last)"},
    "dados.so_ver": {"pt": "só para ver e filtrar: views e tabelas estrangeiras não se alteram aqui",
                     "en": "view and filter only: views and foreign tables are not changed here"},
    "dados.so_depois": {"pt": "só depois de aplicar", "en": "only after applying"},
    "dados.so_depois_tabela": {"pt": "{tabela} ainda não existe no banco: as linhas aparecem só depois de aplicar",
                               "en": "{tabela} does not exist in the database yet: its rows show up only after applying"},
    "dados.n_de_m": {"pt": "{n} de {m} linhas", "en": "{n} of {m} rows"},
    "dados.n_linhas": {"pt": "{n} linhas", "en": "{n} rows"},
    "dados.n_mais": {"pt": "{n}+ linhas", "en": "{n}+ rows"},
    "dados.sem_contagem": {"pt": "não deu para contar a tempo", "en": "could not count in time"},
    "dados.sem_tempo": {"pt": "a leitura demorou demais e foi cancelada: a grade mostra a leitura anterior",
                        "en": "the read took too long and was cancelled: the grid shows the previous read"},
    "dados.sem_ordem": {"pt": "sem chave primária: a ordem das páginas não é estável",
                        "en": "no primary key: page order is not stable"},
    "dados.lendo": {"pt": "lendo…", "en": "reading…"},
    "dados.erro": {"pt": "o banco recusou o filtro: {erro}", "en": "the database refused the filter: {erro}"},
    "dados.pendentes": {"pt": "{n} mudança(s) pendente(s) nesta tabela não aparecem aqui",
                        "en": "{n} pending change(s) on this table are not shown here"},
    "dados.o_filtro": {"pt": "o filtro:", "en": "the filter:"},
    "dados.k.filtro": {"pt": "filtro", "en": "filter"},
    "dados.k.limpar": {"pt": "limpar filtro", "en": "clear filter"},
    "dados.k.texto": {"pt": "WHERE em texto", "en": "WHERE as text"},
    "dados.k.conectivo": {"pt": "E/OU", "en": "AND/OR"},
    "dados.k.tirar": {"pt": "tirar a última condição", "en": "drop the last condition"},
    "dados.f.texto": {"pt": "WHERE escrito à mão (vazio volta ao filtro montado):",
                      "en": "Hand-written WHERE (empty goes back to the built filter):"},
    "dados.f.voltar_montado": {"pt": "Voltar ao filtro montado? O WHERE escrito à mão é descartado.",
                               "en": "Go back to the built filter? The hand-written WHERE is discarded."},
    "dados.f.voltar_sim": {"pt": "voltar ao montado", "en": "back to built"},
    "dados.f.condicao": {"pt": "Condição em {tabela}", "en": "Condition on {tabela}"},
    "dados.f.coluna": {"pt": "coluna", "en": "column"},
    "dados.f.transformacao": {"pt": "transformação (opcional)", "en": "transformation (optional)"},
    "dados.f.sem_transformacao": {"pt": "nenhuma", "en": "none"},
    "dados.f.operador": {"pt": "operador", "en": "operator"},
    "dados.f.valor": {"pt": "valor", "en": "value"},
    "dados.f.lista": {"pt": "valores separados por vírgula", "en": "comma-separated values"},
    "dados.f.vazio_lista": {"pt": "incluir o vazio (∅) na lista", "en": "include empty (∅) in the list"},
    "dados.f.quantos": {"pt": "quantos", "en": "how many"},
    "dados.f.nao": {"pt": "NÃO (negar a condição)", "en": "NOT (negate the condition)"},
    "dados.f.diferenciar": {"pt": "diferenciar maiúsculas (LIKE em vez de ILIKE)", "en": "case sensitive (LIKE instead of ILIKE)"},
    "dados.f.existe": {"pt": "⋯ existe em outra tabela", "en": "⋯ exists in another table"},
    "dados.f.ligacao": {"pt": "por qual ligação?", "en": "through which link?"},
    "dados.f.sai": {"pt": "→ {tabela} ({colunas})", "en": "→ {tabela} ({colunas})"},
    "dados.f.chega": {"pt": "← {tabela} ({colunas})", "en": "← {tabela} ({colunas})"},
    "dados.f.nao_existe": {"pt": "NÃO existe (NOT EXISTS)", "en": "does NOT exist (NOT EXISTS)"},
    "dados.f.internas": {"pt": "condições na outra tabela:", "en": "conditions on the other table:"},
    "dados.f.nenhuma_interna": {"pt": "nenhuma: basta existir", "en": "none: existing is enough"},
    "dados.f.mais_interna": {"pt": "+ condição", "en": "+ condition"},
    "dados.f.menos_interna": {"pt": "− última", "en": "− last"},
    "dados.f.usar": {"pt": "usar", "en": "use"},
    "dados.f.so_depois": {"pt": " (só depois de aplicar)", "en": " (only after applying)"},
    "dados.e.so_depois": {"pt": "{nome} ainda não existe no banco: dá para filtrar por ela só depois de aplicar",
                          "en": "{nome} does not exist in the database yet: you can filter by it only after applying"},
    "dados.e.coluna": {"pt": "escolha a coluna", "en": "choose the column"},
    "dados.e.operador": {"pt": "escolha o operador", "en": "choose the operator"},
    "dados.e.ligacao": {"pt": "escolha a ligação", "en": "choose the link"},
    "dados.e.sem_condicoes": {"pt": "não há condição para tirar", "en": "there is no condition to drop"},
    "dados.e.em_texto": {"pt": "o filtro está em texto: t para editar, F para limpar",
                         "en": "the filter is text: t to edit, F to clear"},
    "dados.e.so_tabela": {"pt": "escolha uma tabela ou view na árvore", "en": "choose a table or view in the tree"},
    "dados.e.coluna_grade": {"pt": "essa coluna não é do banco", "en": "that column is not from the database"},
    "dados.f.usar_outra": {"pt": "comparar com outra coluna da mesma linha", "en": "compare with another column of the same row"},
    "dados.f.outra_coluna": {"pt": "qual coluna?", "en": "which column?"},
    "dados.e.outra_coluna": {"pt": "escolha a outra coluna", "en": "choose the other column"},
    "dados.e.celula": {"pt": "{coluna} é {tipo}: o valor mostrado não volta como filtro; use t (WHERE em texto)",
                       "en": "{coluna} is {tipo}: the shown value does not round-trip as a filter; use t (WHERE as text)"},
    "dados.e.valor": {"pt": "«{valor}» não serve aqui", "en": "«{valor}» does not fit here"},
    "dados.sem_tempo_filtro": {"pt": "a leitura deste filtro demorou demais e foi cancelada: n tenta de novo",
                               "en": "reading this filter took too long and was cancelled: n tries again"},
    # inserir
    "dados.k.inserir": {"pt": "inserir", "en": "insert"},
    "dados.f.inserir": {"pt": "inserir em {tabela}", "en": "insert into {tabela}"},
    "dados.f.por_e_outra": {"pt": "pôr e inserir outra", "en": "add and insert another"},
    "dados.f.posta": {"pt": "linha posta no rascunho ({n}); o formulário segue aberto para a próxima",
                      "en": "row added to the draft ({n}); the form stays open for the next one"},
    "dados.banco_preenche": {"pt": "o banco preenche", "en": "the database fills it in"},
    "dados.f.padrao": {"pt": "vazio: o banco preenche ({padrao})", "en": "empty: the database fills it in ({padrao})"},
    "dados.f.vazio": {"pt": "vazio (∅)", "en": "empty (∅)"},
    "dados.f.obrigatoria": {"pt": "obrigatória", "en": "required"},
    "dados.f.referencia": {"pt": "→ {tabela}.{coluna}", "en": "→ {tabela}.{coluna}"},
    "dados.f.sim": {"pt": "sim", "en": "yes"},
    "dados.f.nao_bool": {"pt": "não", "en": "no"},
    "dados.f.numero": {"pt": "número", "en": "number"},
    "dados.f.data": {"pt": "AAAA-MM-DD", "en": "YYYY-MM-DD"},
    "dados.f.data_hora": {"pt": "AAAA-MM-DD HH:MM", "en": "YYYY-MM-DD HH:MM"},
    "dados.f.escolher": {"pt": "escolher…", "en": "choose…"},
    "dados.f.escolher_em": {"pt": "escolher em {tabela} (enter escolhe, esc volta)",
                            "en": "choose from {tabela} (enter picks, esc goes back)"},
    "dados.f.buscar": {"pt": "buscar nas colunas de texto ou na chave", "en": "search the text columns or the key"},
    "dados.f.buscando": {"pt": "buscando…", "en": "searching…"},
    "dados.f.sem_tempo_busca": {"pt": "não deu para buscar a tempo", "en": "could not search in time"},
    "dados.f.nenhuma_linha": {"pt": "nenhuma linha", "en": "no rows"},
    "dados.f.primeiras": {"pt": "as primeiras {n}: busque para achar outras", "en": "the first {n}: search to find others"},
    "dados.f.pai_so_depois": {"pt": "{tabela} ainda não existe no banco: digite o valor",
                              "en": "{tabela} does not exist in the database yet: type the value"},
    # ── o filtro ──
    "filtro.op.igual": {"pt": "é igual a", "en": "equals"},
    "filtro.op.diferente": {"pt": "é diferente de", "en": "is not"},
    "filtro.op.vazio": {"pt": "está vazio", "en": "is empty"},
    "filtro.op.preenchido": {"pt": "está preenchido", "en": "is filled in"},
    "filtro.op.na_lista": {"pt": "está na lista", "en": "is in the list"},
    "filtro.op.fora_lista": {"pt": "não está na lista", "en": "is not in the list"},
    "filtro.op.contem": {"pt": "contém", "en": "contains"},
    "filtro.op.comeca": {"pt": "começa com", "en": "starts with"},
    "filtro.op.termina": {"pt": "termina com", "en": "ends with"},
    "filtro.op.igual_sem_caixa": {"pt": "é igual a (sem distinguir maiúsculas)", "en": "equals (ignoring case)"},
    "filtro.op.menor": {"pt": "é menor que", "en": "is less than"},
    "filtro.op.menor_igual": {"pt": "é menor ou igual a", "en": "is at most"},
    "filtro.op.maior": {"pt": "é maior que", "en": "is greater than"},
    "filtro.op.maior_igual": {"pt": "é maior ou igual a", "en": "is at least"},
    "filtro.op.entre": {"pt": "está entre", "en": "is between"},
    "filtro.op.antes": {"pt": "é antes de", "en": "is before"},
    "filtro.op.depois": {"pt": "é depois de", "en": "is after"},
    "filtro.op.ha_mais_de": {"pt": "há mais de", "en": "more than"},
    "filtro.op.nos_ultimos": {"pt": "nos últimos", "en": "within the last"},
    "filtro.op.verdadeiro": {"pt": "é verdadeiro", "en": "is true"},
    "filtro.op.falso": {"pt": "é falso", "en": "is false"},
    "filtro.tr.minusculas": {"pt": "minúsculas", "en": "lowercase"},
    "filtro.tr.trim": {"pt": "sem espaços nas pontas", "en": "trimmed"},
    "filtro.tr.tamanho": {"pt": "tamanho", "en": "length"},
    "filtro.tr.mes": {"pt": "mês", "en": "month"},
    "filtro.tr.ano": {"pt": "ano", "en": "year"},
    "filtro.tr.dia_semana": {"pt": "dia da semana", "en": "weekday"},
    "filtro.tr.dia": {"pt": "dia do mês", "en": "day of month"},
    "filtro.tr.so_data": {"pt": "só a data", "en": "date only"},
    "filtro.mes.1": {"pt": "janeiro", "en": "January"},
    "filtro.mes.2": {"pt": "fevereiro", "en": "February"},
    "filtro.mes.3": {"pt": "março", "en": "March"},
    "filtro.mes.4": {"pt": "abril", "en": "April"},
    "filtro.mes.5": {"pt": "maio", "en": "May"},
    "filtro.mes.6": {"pt": "junho", "en": "June"},
    "filtro.mes.7": {"pt": "julho", "en": "July"},
    "filtro.mes.8": {"pt": "agosto", "en": "August"},
    "filtro.mes.9": {"pt": "setembro", "en": "September"},
    "filtro.mes.10": {"pt": "outubro", "en": "October"},
    "filtro.mes.11": {"pt": "novembro", "en": "November"},
    "filtro.mes.12": {"pt": "dezembro", "en": "December"},
    "filtro.dsem.1": {"pt": "segunda", "en": "Monday"},
    "filtro.dsem.2": {"pt": "terça", "en": "Tuesday"},
    "filtro.dsem.3": {"pt": "quarta", "en": "Wednesday"},
    "filtro.dsem.4": {"pt": "quinta", "en": "Thursday"},
    "filtro.dsem.5": {"pt": "sexta", "en": "Friday"},
    "filtro.dsem.6": {"pt": "sábado", "en": "Saturday"},
    "filtro.dsem.7": {"pt": "domingo", "en": "Sunday"},
    "filtro.un.dias": {"pt": "dias", "en": "days"},
    "filtro.un.meses": {"pt": "meses", "en": "months"},
    "filtro.un.anos": {"pt": "anos", "en": "years"},
    "filtro.e": {"pt": "e", "en": "and"},
    "filtro.ou": {"pt": "ou", "en": "or"},
    "filtro.nao": {"pt": "não", "en": "not"},
    "filtro.existe": {"pt": "existe em {tabela}", "en": "exists in {tabela}"},
    "filtro.nao_existe": {"pt": "não existe em {tabela}", "en": "does not exist in {tabela}"},
    "filtro.com": {"pt": "com", "en": "with"},
    "filtro.sem_condicoes": {"pt": "sem filtro", "en": "no filter"},
    "filtro.e.coluna": {"pt": "coluna desconhecida: {coluna}", "en": "unknown column: {coluna}"},
    "filtro.e.operador": {"pt": "«{operador}» não serve para o tipo {tipo}", "en": "«{operador}» does not apply to type {tipo}"},
    "filtro.e.transformacao": {"pt": "«{transformacao}» não serve para o tipo {tipo}", "en": "«{transformacao}» does not apply to type {tipo}"},
    "filtro.e.valores": {"pt": "faltam valores para «{operador}»", "en": "missing values for «{operador}»"},
    "filtro.e.numero": {"pt": "«{valor}» não é um número", "en": "«{valor}» is not a number"},
    "filtro.e.booleano": {"pt": "«{valor}» não é sim ou não", "en": "«{valor}» is not yes or no"},
    "filtro.e.mes": {"pt": "o mês vai de 1 a 12: «{valor}»", "en": "month goes from 1 to 12: «{valor}»"},
    "filtro.e.dia_semana": {"pt": "o dia da semana vai de 1 a 7: «{valor}»", "en": "weekday goes from 1 to 7: «{valor}»"},
    "filtro.e.dia": {"pt": "o dia do mês vai de 1 a 31: «{valor}»", "en": "day of month goes from 1 to 31: «{valor}»"},
    "filtro.e.intervalo": {"pt": "use um número inteiro positivo: «{valor}»", "en": "use a positive whole number: «{valor}»"},
    "filtro.e.unidade": {"pt": "unidade desconhecida: «{unidade}»", "en": "unknown unit: «{unidade}»"},
    "filtro.e.ligacao": {"pt": "a ligação {ligacao} não sai nem chega nesta tabela", "en": "link {ligacao} does not start or end at this table"},
    "filtro.e.conectivo": {"pt": "conectivo desconhecido: «{conectivo}»", "en": "unknown connective: «{conectivo}»"},
    "filtro.e.nul": {"pt": "o valor não pode ter o caractere NUL", "en": "the value cannot contain the NUL character"},
    "filtro.e.pad": {"pt": "não dá para comparar com a outra coluna neste operador", "en": "cannot compare with another column with this operator"},
    "filtro.e.texto_vazio": {"pt": "escreva a condição", "en": "write the condition"},
    "filtro.e.texto_ponto_virgula": {"pt": "só uma condição: sem ; fora de aspas", "en": "one condition only: no ; outside quotes"},
    "filtro.e.texto_comentario": {"pt": "sem comentários (-- ou /*) na condição", "en": "no comments (-- or /*) in the condition"},
    "filtro.e.texto_aspa": {"pt": "aspas ou $$ sem fechar", "en": "unclosed quote or $$"},
    "filtro.e.texto_parenteses": {"pt": "parênteses desbalanceados", "en": "unbalanced parentheses"},
    "filtro.e.texto_varios": {"pt": "a condição precisa ser uma expressão só", "en": "the condition must be a single expression"},
    "filtro.e.texto_barra": {"pt": "barra invertida numa string '…': use E'…' ou $$…$$", "en": "backslash in a '…' string: use E'…' or $$…$$"},
    "filtro.e.chave": {"pt": "chave desconhecida no filtro: {campo}", "en": "unknown key in the filter: {campo}"},
    "filtro.e.forma": {"pt": "filtro mal formado", "en": "malformed filter"},
}
