"""Bater na porta do PostgreSQL e ver o que ele responde — sem senha e sem sudo.

O pg_hba.conf, que diz como cada conexão entra, só abre para o usuário postgres. Mas o servidor
conta o método na primeira resposta: quem conecta manda "sou fulano, quero o banco tal", e ele
devolve "me mande a senha (SCRAM)", "pode entrar" ou "não". O ratt lê essa resposta e desliga
antes de mandar qualquer senha. É o protocolo do PostgreSQL, versão 3.0, só o começo dele.
"""
import socket
import struct
from dataclasses import dataclass

# o código que vem depois do 'R' (AuthenticationRequest)
_METODOS = {0: "trust", 3: "password", 5: "md5", 7: "gss", 9: "sspi", 10: "scram-sha-256"}


@dataclass
class Resposta:
    porta_aberta: bool
    metodo: str = None       # trust, scram-sha-256, md5, password… ou "recusa"
    codigo: str = None       # o SQLSTATE quando o servidor recusa (28000)
    mensagem: str = None     # o texto do servidor, no idioma dele

    @property
    def pede_senha(self):
        return self.metodo in ("scram-sha-256", "md5", "password")


def _mensagem_inicial(usuario, banco):
    corpo = struct.pack("!i", 196608)      # protocolo 3.0
    for chave, valor in (("user", usuario), ("database", banco)):
        corpo += chave.encode() + b"\0" + valor.encode() + b"\0"
    corpo += b"\0"
    return struct.pack("!i", len(corpo) + 4) + corpo


def _ler(conexao, n):
    dados = b""
    while len(dados) < n:
        pedaco = conexao.recv(n - len(dados))
        if not pedaco:
            raise ConnectionError("o servidor fechou a conexão")
        dados += pedaco
    return dados


def _campos_de_erro(corpo):
    """Uma ErrorResponse: pares <letra><texto>\\0 — C é o SQLSTATE, M a mensagem."""
    campos = {}
    for parte in corpo.split(b"\0"):
        if parte:
            campos[chr(parte[0])] = parte[1:].decode("utf-8", "replace")
    return campos


def porta_aberta(host="127.0.0.1", porta=5432, espera=2.0):
    try:
        with socket.create_connection((host, porta), timeout=espera):
            return True
    except OSError:
        return False


def sondar(host="127.0.0.1", porta=5432, usuario="postgres", banco="postgres", espera=3.0):
    """Como o servidor trata uma conexão de rede deste usuário: Resposta(porta_aberta, metodo…)."""
    try:
        conexao = socket.create_connection((host, porta), timeout=espera)
    except OSError:
        return Resposta(False)
    with conexao:
        try:
            conexao.sendall(_mensagem_inicial(usuario, banco))
            tipo = _ler(conexao, 1)
            tamanho, = struct.unpack("!i", _ler(conexao, 4))
            corpo = _ler(conexao, tamanho - 4)
        except (OSError, ConnectionError, struct.error):
            return Resposta(True)
    if tipo == b"R" and len(corpo) >= 4:
        codigo, = struct.unpack("!i", corpo[:4])
        return Resposta(True, _METODOS.get(codigo, f"desconhecido ({codigo})"))
    if tipo == b"E":
        campos = _campos_de_erro(corpo)
        return Resposta(True, "recusa", campos.get("C"), campos.get("M"))
    return Resposta(True)
