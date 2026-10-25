"""Em que Linux o ratt está, e como o PostgreSQL vive nele.

Cada família instala, inicializa e liga o PostgreSQL do seu jeito. Uma Receita junta esses
detalhes; o resto do ratt db pergunta à receita e não precisa saber qual sistema é.

    debian   Ubuntu, Debian, Mint…        apt     o apt já inicializa e liga o banco
    redhat   Fedora, Rocky, Alma, RHEL…   dnf     postgresql-setup --initdb; login por ident
    arch     Arch, Manjaro, EndeavourOS…  pacman  initdb à mão; sem opção, entra sem senha
"""
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

OS_RELEASE = ("/etc/os-release", "/usr/lib/os-release")

_FAMILIAS = {
    "debian": {"debian", "ubuntu", "linuxmint", "pop", "elementary", "zorin", "kali", "raspbian", "neon"},
    "redhat": {"fedora", "rhel", "centos", "rocky", "almalinux", "ol", "amzn", "nobara"},
    "arch": {"arch", "manjaro", "endeavouros", "garuda", "cachyos", "artix"},
}


def ler_os_release(texto=None):
    """O /etc/os-release como dicionário: ID, ID_LIKE, PRETTY_NAME, VERSION_ID…"""
    if texto is None:
        for caminho in OS_RELEASE:
            try:
                texto = Path(caminho).read_text(encoding="utf-8")
                break
            except OSError:
                continue
        else:
            return {}
    campos = {}
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        campos[chave] = valor.strip().strip("\"'")
    return campos


def familia(os_release):
    """'debian', 'redhat', 'arch' ou None. O ID decide; se não for conhecido, o ID_LIKE."""
    candidatos = [os_release.get("ID", "").lower()] + os_release.get("ID_LIKE", "").lower().split()
    for candidato in candidatos:
        for nome, ids in _FAMILIAS.items():
            if candidato in ids:
                return nome
    return None


# ── as receitas ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Receita:
    familia: str
    nome: str               # como aparece para quem lê: "Red Hat", "Debian", "Arch"
    gerenciador: str        # dnf, apt, pacman
    pacotes: tuple          # o que instalar
    unidade: str            # o serviço do systemd
    pasta_dados: str        # onde o initdb escreve (no Debian, o padrão; o real vem do pg_lsclusters)
    grupos_sudo: tuple      # quem está nestes grupos pode usar sudo


RECEITAS = {
    "redhat": Receita("redhat", "Red Hat", "dnf", ("postgresql-server", "postgresql"), "postgresql",
                      "/var/lib/pgsql/data", ("wheel",)),
    "debian": Receita("debian", "Debian", "apt", ("postgresql",), "postgresql",
                      "/var/lib/postgresql/<versão>/main", ("sudo", "admin")),
    "arch": Receita("arch", "Arch", "pacman", ("postgresql",), "postgresql",
                    "/var/lib/postgres/data", ("wheel",)),
}

PASTAS_SOCKET = ("/var/run/postgresql", "/run/postgresql", "/tmp")


def _rodar(cmd):
    """(código, saída) de um comando que só lê; (None, '') se ele nem existe aqui."""
    if not shutil.which(cmd[0]):
        return None, ""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    return p.returncode, p.stdout.strip()


def versao_instalada(receita):
    """A versão do servidor PostgreSQL instalado ('18.6'), ou None se não está instalado."""
    if receita.familia == "redhat":
        codigo, saida = _rodar(["rpm", "-q", "--qf", "%{VERSION}", "postgresql-server"])
        return saida if codigo == 0 else None
    if receita.familia == "arch":
        codigo, saida = _rodar(["pacman", "-Q", "postgresql"])
        return saida.split()[1].split("-")[0] if codigo == 0 and len(saida.split()) > 1 else None
    if receita.familia == "debian":
        # o pacote "postgresql" é só um apontador; o servidor é o postgresql-16, postgresql-17…
        codigo, saida = _rodar(["dpkg-query", "-W", "-f", "${Package} ${Status} ${Version}\n", "postgresql-[0-9]*"])
        if codigo != 0:
            return None
        for linha in saida.splitlines():
            partes = linha.split()
            if len(partes) >= 5 and re.fullmatch(r"postgresql-\d+", partes[0]) and partes[3] == "installed":
                return re.match(r"[\d.]+", partes[4]).group(0).rstrip(".")
    return None


@dataclass
class Cluster:
    """Um banco inicializado: a pasta de dados e a porta onde ele atende."""
    versao: str
    nome: str
    porta: int
    online: bool
    pasta: str


def clusters_debian():
    """No Debian/Ubuntu o pg_lsclusters lista os bancos inicializados, sem precisar de sudo."""
    codigo, saida = _rodar(["pg_lsclusters", "--no-header"])
    if codigo != 0:
        return []
    achados = []
    for linha in saida.splitlines():
        partes = linha.split()
        if len(partes) >= 6 and partes[2].isdigit():
            achados.append(Cluster(partes[0], partes[1], int(partes[2]), partes[3].startswith("online"), partes[5]))
    return achados


def inicializado(receita, servico_ativo):
    """True, False, ou None quando não dá para saber sem a senha do sudo.

    A pasta de dados só abre para o usuário postgres. Se o serviço está rodando, ela existe;
    se está parado, o ratt tenta  sudo -n  (que não pergunta senha) e, sem ele, fica em dúvida.
    """
    if receita.familia == "debian":
        return bool(clusters_debian())
    if servico_ativo:
        return True
    try:
        return Path(receita.pasta_dados, "PG_VERSION").stat().st_size > 0     # root, ou o próprio postgres
    except FileNotFoundError:
        return False
    except PermissionError:
        pass
    codigo, _ = _rodar(["sudo", "-n", "test", "-s", f"{receita.pasta_dados}/PG_VERSION"])
    if codigo is None:
        return None
    if codigo == 0:
        return True
    # o sudo -n falha com 1 tanto quando o arquivo não existe quanto quando quer senha
    codigo_sudo, _ = _rodar(["sudo", "-n", "true"])
    return False if codigo_sudo == 0 else None


def servico(receita):
    """(ativo, liga_no_boot): True/False, ou None quando não há systemd (num container, por exemplo)."""
    if not Path("/run/systemd/system").exists():
        return None, None
    codigo, ativo = _rodar(["systemctl", "is-active", receita.unidade])
    if codigo is None:
        return None, None
    _, ligado = _rodar(["systemctl", "is-enabled", receita.unidade])
    ligado = ligado in ("enabled", "enabled-runtime", "alias")
    if receita.familia == "debian":
        # o postgresql.service do Debian é só um guarda-chuva que fica "active" mesmo com o banco
        # parado; quem roda de verdade é o postgresql@16-main. O pg_lsclusters diz se está online.
        return any(cl.online for cl in clusters_debian()), ligado
    return ativo == "active", ligado


def pasta_socket(porta=5432):
    """A pasta onde está o socket local do PostgreSQL (muda de sistema para sistema)."""
    for pasta in PASTAS_SOCKET:
        if Path(pasta, f".s.PGSQL.{porta}").exists():
            return pasta
    return None


def pode_usar_sudo(receita):
    """(pode, grupo): o grupo que dá o sudo, se o usuário está em um deles; root também pode."""
    if os.geteuid() == 0:
        return True, "root"
    try:
        import grp
        grupos = {grp.getgrgid(g).gr_name for g in os.getgroups()}
    except (ImportError, KeyError, OSError):
        grupos = set()
    for g in receita.grupos_sudo:
        if g in grupos:
            return True, g
    return False, None
