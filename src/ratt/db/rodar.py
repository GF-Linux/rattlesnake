"""ratt db run: o arquivo .sql do VS Code, rodado comando por comando, com cada resultado relido.

O arquivo inteiro roda numa transação: se um comando falha, os anteriores são desfeitos e o erro
aponta a linha do arquivo — não a posição dentro do texto, como o PostgreSQL conta. --dry-run roda
tudo e desfaz no fim: mostra o que um DELETE faria sem apagar nada.

A releitura das tabelas segue a do pandas: o tipo embaixo do nome, o vazio (NULL) diferente do
texto vazio, o espaço sobrando nas pontas — avisa, não decide.
"""
import getpass
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ratt.cli import c, _casa
from ratt.db import acesso, config
from ratt.db.idioma import t

LIMITE_LINHAS = 50
LARGURA_CELULA = 40


# ── dividir o arquivo em comandos ───────────────────────────────────────────

@dataclass
class Comando:
    texto: str          # como vai para o servidor
    inicio: int         # onde começa no arquivo (posição do primeiro caractere)
    linha: int          # a linha do arquivo onde ele começa (1, 2, …)
    meta: bool = False  # um comando do psql (\d, \i…), que não é SQL

    @property
    def codigo(self):
        """Onde começa o código no arquivo, depois dos comentários do topo do comando."""
        resto = self.texto
        while True:
            sem = resto.lstrip()
            if sem.startswith("--"):
                fim = sem.find("\n")
                resto = "" if fim < 0 else sem[fim:]
            elif sem.startswith("/*"):
                resto = sem[_fim_do_bloco(sem, 0):]
            else:
                return self.inicio + len(self.texto) - len(sem)


_DOLAR = re.compile(r"\$([A-Za-z_][A-Za-z_0-9]*)?\$")


def dividir(sql):
    """Os comandos do arquivo, na ordem. O ; só separa fora de texto, comentário e $$ … $$."""
    comandos, i, n, inicio = [], 0, len(sql), 0

    def fechar(fim):
        trecho = sql[inicio:fim]
        sem_espaco = len(trecho) - len(trecho.lstrip())
        if _so_comentario(trecho):
            return
        comeco = inicio + sem_espaco
        comandos.append(Comando(trecho.strip(), comeco, sql.count("\n", 0, comeco) + 1))

    while i < n:
        ch = sql[i]
        if ch == "\\" and not sql[inicio:i].strip():               # \d tabela — até o fim da linha
            fim = sql.find("\n", i)
            fim = n if fim < 0 else fim
            comandos.append(Comando(sql[i:fim].strip(), i, sql.count("\n", 0, i) + 1, meta=True))
            i = inicio = fim
            continue
        if ch == "-" and sql.startswith("--", i):
            fim = sql.find("\n", i)
            i = n if fim < 0 else fim
        elif ch == "/" and sql.startswith("/*", i):
            i = _fim_do_bloco(sql, i)
        elif ch == "'":
            escapa = i > 0 and sql[i - 1] in "eE" and (i < 2 or not (sql[i - 2].isalnum() or sql[i - 2] == "_"))
            i = _fim_do_texto(sql, i, "'", escapa)
        elif ch == '"':
            i = _fim_do_texto(sql, i, '"', False)
        elif ch == "$" and (i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_")) and _DOLAR.match(sql, i):
            marca = _DOLAR.match(sql, i).group(0)
            fim = sql.find(marca, i + len(marca))
            i = n if fim < 0 else fim + len(marca)
        elif ch == ";":
            fechar(i)
            i = inicio = i + 1
        else:
            i += 1
    fechar(n)
    return comandos


def _fim_do_bloco(sql, i):
    """/* … */ — que no PostgreSQL pode ter outro /* … */ dentro."""
    nivel, i = 0, i
    while i < len(sql):
        if sql.startswith("/*", i):
            nivel, i = nivel + 1, i + 2
        elif sql.startswith("*/", i):
            nivel, i = nivel - 1, i + 2
            if nivel == 0:
                return i
        else:
            i += 1
    return len(sql)


def _fim_do_texto(sql, i, aspa, barra_escapa):
    i += 1
    while i < len(sql):
        if barra_escapa and sql[i] == "\\":
            i += 2
        elif sql[i] == aspa:
            if sql.startswith(aspa * 2, i):      # '' dentro de '…' é uma aspa
                i += 2
            else:
                return i + 1
        else:
            i += 1
    return len(sql)


def _so_comentario(trecho):
    sem = re.sub(r"--[^\n]*", "", trecho)
    while "/*" in sem:
        a = sem.find("/*")
        sem = sem[:a] + sem[_fim_do_bloco(sem, a):]
    return not sem.strip()


def titulo(texto, largura=48):
    """A primeira linha de verdade do comando (sem comentário), curta."""
    for linha in texto.splitlines():
        linha = linha.split("--")[0].strip()
        if linha and not linha.startswith("/*"):
            linha = re.sub(r"\s+", " ", linha)
            return linha if len(linha) <= largura else linha[:largura - 1] + "…"
    return texto[:largura]


# ── a releitura de um resultado ─────────────────────────────────────────────

def _formatar(valor):
    if valor is None:
        return "∅"
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, str):
        if valor == "" or valor != valor.strip():
            return "'" + valor.replace("\n", "↵") + "'"
        return valor.replace("\n", "↵")
    if isinstance(valor, datetime):
        return valor.isoformat(sep=" ")
    return str(valor)


def _cortar(texto, largura):
    return texto if len(texto) <= largura else texto[:largura - 1] + "…"


def _numerico(tipo):
    return tipo in ("int2", "int4", "int8", "numeric", "float4", "float8", "money", "oid")


def avisos(colunas, linhas):
    """[(marca, cor, texto)] coluna por coluna: os vazios (NULL), o texto vazio e o espaço nas pontas.
    O mesmo que a releitura do pandas avisa — e a TUI mostra embaixo da aba de dados."""
    achados = []
    for j, col in enumerate(colunas):
        vazios = sum(1 for linha in linhas if linha[j] is None)
        pontas = sum(1 for linha in linhas if isinstance(linha[j], str) and linha[j] and linha[j] != linha[j].strip())
        vazio_texto = sum(1 for linha in linhas if linha[j] == "")
        if vazios:
            achados.append(("∅", "laranja", t("run.nulos", col=col, n=vazios, de=len(linhas))))
        if pontas:
            achados.append(("⚠", "amarelo", t("run.pontas", col=col, n=pontas)))
        if vazio_texto:
            achados.append(("⚠", "amarelo", t("run.texto_vazio", col=col, n=vazio_texto)))
    return achados


def tabela(colunas, tipos, linhas, total, largura_tela=None):
    """As linhas como texto: blocos de colunas que cabem na tela, o tipo embaixo do nome, e avisos."""
    largura_tela = largura_tela or shutil.get_terminal_size((120, 40)).columns
    textos = [[_cortar(_formatar(v), LARGURA_CELULA) for v in linha] for linha in linhas]
    larguras = [max([len(col), len(tipo)] + [len(r[j]) for r in textos]) for j, (col, tipo) in enumerate(zip(colunas, tipos))]

    # colunas em blocos que cabem na largura da tela, como o head do pandas relido
    blocos, atual, usado = [], [], 5
    for j, w in enumerate(larguras):
        if atual and usado + w + 3 > largura_tela:
            blocos.append(atual)
            atual, usado = [], 5
        atual.append(j)
        usado += w + 3
    if atual:
        blocos.append(atual)

    saida = []
    for bloco in blocos:
        saida.append("   " + "   ".join(c(colunas[j].ljust(larguras[j]), "negrito") for j in bloco))
        saida.append("   " + "   ".join(c(tipos[j].ljust(larguras[j]), "cinza") for j in bloco))
        for linha, texto in zip(linhas, textos):
            celulas = []
            for j in bloco:
                v, s = linha[j], texto[j]
                s = s.rjust(larguras[j]) if _numerico(tipos[j]) and v is not None else s.ljust(larguras[j])
                if v is None:
                    s = c(s, "laranja")
                elif isinstance(v, str) and (v == "" or v != v.strip()):
                    s = c(s, "amarelo")
                celulas.append(s)
            saida.append("   " + "   ".join(celulas))
        saida.append("")

    for marca, cor, texto in avisos(colunas, linhas):
        saida.append(c(f"   {marca} {texto}", cor))
    if total > len(linhas):
        saida.append(c(f"   … {t('run.mais_linhas', n=total - len(linhas), mostradas=len(linhas))}", "cinza"))
    while saida and saida[-1] == "":
        saida.pop()
    return "\n".join(saida)


# ── o que cada comando fez ──────────────────────────────────────────────────

def resumo(status, ensaio):
    """O command tag do PostgreSQL ("INSERT 0 3", "CREATE TABLE") em palavras."""
    partes = (status or "").split()
    if not partes:
        return "ok"
    verbo = partes[0]
    n = partes[-1] if partes[-1].isdigit() else None
    chaves = {"INSERT": "run.inseridas", "UPDATE": "run.atualizadas", "DELETE": "run.apagadas",
              "SELECT": "run.linhas", "MERGE": "run.mescladas", "COPY": "run.copiadas"}
    if verbo in chaves and n is not None:
        chave = chaves[verbo] + (".ensaio" if ensaio and verbo != "SELECT" else "")
        return t(chave + (".um" if n == "1" else ""), n=n)
    return status.lower()


# ── rodar ───────────────────────────────────────────────────────────────────

def _cabecalho(numero, total, comando, direita, cor=None):
    # 3. a coluna da direita alinhada: o título tem no máximo 48, mais o contador
    esquerda = f"── {numero}/{total} · {titulo(comando.texto)}".ljust(58 + 2 * len(str(total)))
    print(f"{c(esquerda, 'azul')}  {c(direita, cor) if cor else direita}")


def _mostrar_avisos(avisos):
    for aviso in avisos:
        print(c(f"   ℹ {aviso}", "cinza"))
    avisos.clear()


def _mostrar_erro(erro, comando, sql, arquivo):
    """O erro com a linha do arquivo e um ^ embaixo do lugar, quando o servidor diz onde foi."""
    diag = erro.diag
    posicao = int(diag.statement_position) if getattr(diag, "statement_position", None) else None
    onde = comando.inicio + posicao - 1 if posicao else comando.codigo
    linha_n = sql.count("\n", 0, onde) + 1
    comeco_linha = sql.rfind("\n", 0, onde) + 1
    fim_linha = sql.find("\n", onde)
    texto_linha = sql[comeco_linha:fim_linha if fim_linha >= 0 else len(sql)]
    print(c(f"   ✗ {t('run.erro_linha', linha=linha_n, arquivo=arquivo.name)}", "laranja", "negrito"))
    margem = len(str(linha_n))
    print(c(f"   {linha_n} │ ", "cinza") + texto_linha)
    if posicao:
        print(c(f"   {' ' * margem} │ ", "cinza") + " " * (onde - comeco_linha) + c("^", "laranja", "negrito"))
    print(f"   {diag.message_primary or str(erro).strip()}")
    for extra in (diag.message_detail, diag.message_hint):
        if extra:
            print(c(f"   {extra}", "cinza"))


def executar(arquivo, conexao, ensaio=False, autocommit=False):
    """Roda o arquivo na conexão. Devolve 0 se tudo deu certo, 1 se algum comando falhou."""
    import psycopg
    sql = arquivo.read_text(encoding="utf-8")
    comandos = dividir(sql)
    sql_de_verdade = [cmd for cmd in comandos if not cmd.meta]
    if not sql_de_verdade:
        print(c(f"   {t('run.vazio')}", "cinza"))
        return 0
    conexao.autocommit = autocommit and not ensaio
    avisos = []         # o servidor avisa durante o comando; eles aparecem embaixo do cabeçalho dele
    conexao.add_notice_handler(lambda d: avisos.append(d.message_primary))
    total, feitos = len(comandos), 0
    for numero, comando in enumerate(comandos, 1):
        if comando.meta:
            _cabecalho(numero, total, comando, t("run.meta"), "amarelo")
            continue
        inicio = time.monotonic()
        try:
            with conexao.cursor() as cur:
                cur.execute(comando.texto.encode())       # bytes: um % no SQL não vira parâmetro
                ms = (time.monotonic() - inicio) * 1000
                direita = f"{resumo(cur.statusmessage, ensaio)} {c(f'· {ms:.0f} ms', 'cinza')}"
                _cabecalho(numero, total, comando, direita)
                _mostrar_avisos(avisos)
                if cur.description:
                    colunas = [d.name for d in cur.description]
                    tipos = [getattr(conexao.adapters.types.get(d.type_code), "name", str(d.type_code))
                             for d in cur.description]
                    linhas = cur.fetchmany(LIMITE_LINHAS)
                    total_linhas = cur.rowcount if cur.rowcount >= 0 else len(linhas)
                    if linhas:
                        print(tabela(colunas, tipos, linhas, total_linhas))
                    else:
                        print(c(f"   {t('run.nenhuma_linha')}", "cinza"))
            feitos += 1
        except psycopg.Error as erro:
            _cabecalho(numero, total, comando, c("✗", "laranja", "negrito"))
            _mostrar_avisos(avisos)
            _mostrar_erro(erro, comando, sql, arquivo)
            if isinstance(erro, psycopg.errors.ActiveSqlTransaction):
                print(c(f"   {t('run.fora_de_transacao')}", "cinza"))
            if not conexao.autocommit:
                conexao.rollback()
                print(c(f"   ↺ {t('run.desfeitos')}", "amarelo"))
            return 1
    if ensaio:
        conexao.rollback()
        print(c(f"↺ {t('run.ensaio_fim', n=feitos)}", "amarelo", "negrito"))
    else:
        if not conexao.autocommit:
            conexao.commit()
        print(c(f"✓ {t('run.gravado', n=feitos, total=len(sql_de_verdade))}", "verde", "negrito"))
    return 0


def cmd_run(args, op):
    if not args:
        print(c(f"  {t('run.sem_arquivo')}", "laranja"))
        return 2
    arquivo = Path(args[0])
    if not arquivo.is_file():
        print(c(f"  {t('run.nao_existe', arquivo=args[0])}", "laranja"))
        return 1
    lembrado = config.ler().get("db") or {}
    porta = int(os.environ.get("PGPORT") or lembrado.get("porta") or 5432)
    usuario = op.get("user") or lembrado.get("usuario") or getpass.getuser()
    banco = op.get("db") or lembrado.get("banco")
    if not banco:
        print(c(f"  {t('run.sem_banco')}", "laranja"))
        return 1

    def uma_vez():
        conexao, _ = acesso.entrar(porta, usuario, banco)
        if not conexao:
            print(c(f"  {t('run.sem_acesso', usuario=usuario, banco=banco)}", "laranja"))
            return 1
        modo = c(f" · {t('run.modo_ensaio')}", "amarelo") if op["dry_run"] else ""
        print(f"{c('ratt db run', 'azul', 'negrito')} {c(f'{_casa(arquivo)} · {usuario} @ {banco}', 'cinza')}{modo}")
        with conexao:
            return executar(arquivo, conexao, ensaio=op["dry_run"], autocommit=op["autocommit"])

    if not op["watch"]:
        return uma_vez()
    # --watch: roda, espera o arquivo ser salvo de novo, roda outra vez
    try:
        while True:
            uma_vez()
            visto = arquivo.stat().st_mtime
            print(c(f"\n{t('run.esperando', arquivo=arquivo.name)}", "cinza"))
            while arquivo.stat().st_mtime == visto:
                time.sleep(0.4)
            print("\n" + c(f"── {t('run.salvo', hora=datetime.now().strftime('%H:%M:%S'))} " + "─" * 30, "cinza"))
    except KeyboardInterrupt:
        print()
        return 0
