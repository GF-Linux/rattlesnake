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
}
