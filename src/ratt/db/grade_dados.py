"""O que a tela Dados usa para mostrar a grade, sem estado da tela: o widget `Grade`, o texto da linha de contagem,
o SELECT do filtro para o painel, a tabela de uma view e a condição "igual a este valor" de uma célula.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from rich.text import Text
from textual import events
from textual.coordinate import Coordinate
from textual.widgets import DataTable

from ratt.db import catalogo, filtro, modelo as _modelo, rascunho as _rascunho
from ratt.db.idioma import t
from ratt.db.tela import COR


class Grade(DataTable, inherit_bindings=False):
    """A grade: as setas andam pelas células; um clique numa célula já a escolhe ("igual a este valor"). O Enter
    fica com a tela (é o aplicar)."""

    BINDINGS = [b for b in DataTable.BINDINGS if getattr(b, "key", "") != "enter"]

    def _on_click(self, evento: events.Click) -> None:
        meta = evento.style.meta
        linha, coluna = meta.get("row"), meta.get("column")
        if linha is None or coluna is None or linha < 0 or coluna < 0 or meta.get("out_of_bounds", False):
            return                      # cabeçalho e o resto: o DataTable cuida
        evento.prevent_default()        # o DataTable só escolheria no segundo clique
        evento.stop()
        self.cursor_coordinate = Coordinate(linha, coluna)
        self.action_select_cursor()

    def trocar_linhas(self, linhas: Sequence[Sequence[Text]]) -> None:
        """Troca o conteúdo sem refazer as colunas: o cursor e a rolagem ficam onde estavam (a vigia relê a página
        enquanto a pessoa olha)."""
        antes = self.row_count
        for i, celulas in enumerate(linhas):
            if i < antes:
                for j, celula in enumerate(celulas):
                    self.update_cell_at(Coordinate(i, j), celula, update_width=True)
            else:
                self.add_row(*celulas)
        for linha in list(self.ordered_rows[len(linhas):]):
            self.remove_row(linha.key)


def modelo_da_view(esquema: str, tabela_id: str, nome: str, colunas: List[catalogo.Coluna]) -> _modelo.Modelo:
    """Uma view como um modelo de uma tabela só (as colunas do catálogo): o filtro se monta sobre ele."""
    tab = _modelo.Tabela(tabela_id, esquema, nome, [
        _modelo.Coluna(f"{tabela_id}.c{c.numero}", c.nome, c.tipo, obrigatoria=bool(c.obrigatoria),
                       padrao=c.padrao, pk=bool(c.chave)) for c in colunas])
    return _modelo.Modelo(esquema, {tabela_id: tab})


def select_texto(tab: _modelo.Tabela, onde: str) -> str:
    """O SELECT que a grade roda (sem o LIMIT/OFFSET da página), para o painel ensinar o WHERE."""
    texto = f"SELECT * FROM {_rascunho.nome_tabela(tab)}" + (f" WHERE {onde}" if onde else "")
    if tab.pk():
        texto += " ORDER BY " + ", ".join(_rascunho.ident(c.nome) for c in tab.pk())
    return texto + ";"


def texto_contagem(estado: str, *, por_pagina: int, pagina: int, n_linhas: int, total: Optional[int],
                   total_tabela: Optional[int], contando: bool, sem_pk: bool, tabela: str, erro: str) -> Tuple[str, str]:
    """(texto, cor) da linha "── N de M linhas ──" para o estado da grade."""
    simples = {"lendo": (t("dados.lendo"), COR["cinza"]), "sem_leitura": (t("canvas.sem_leitura"), COR["laranja"]),
               "so_depois": (t("dados.so_depois_tabela", tabela=tabela), COR["amarelo"]),
               "sumiu": (t("canvas.e.sumiu"), COR["laranja"]), "erro": (t("dados.erro", erro=erro), COR["laranja"]),
               "sem_tempo": (t("dados.sem_tempo"), COR["amarelo"]),
               "sem_tempo_filtro": (t("dados.sem_tempo_filtro"), COR["amarelo"])}
    if estado in simples:
        return simples[estado]
    inicio = pagina * por_pagina
    if total is not None:
        texto = (t("dados.n_de_m", n=f"{total:n}", m=f"{total_tabela:n}") if total_tabela is not None
                 else t("dados.n_linhas", n=f"{total:n}"))
    elif n_linhas < por_pagina:              # a última página: dá para saber quantas são sem contar
        texto = t("dados.n_linhas", n=f"{inicio + n_linhas:n}")
    elif contando:
        texto = t("dados.n_mais", n=f"{inicio + n_linhas:n}")
    else:
        texto = t("dados.n_mais", n=f"{inicio + n_linhas:n}") + " · " + t("dados.sem_contagem")
    if contando:
        texto += " · " + t("canvas.f.contando")
    if pagina or n_linhas >= por_pagina:
        texto += f"  ({t('tui.mostrando', inicio=inicio + 1 if n_linhas else inicio, fim=inicio + n_linhas)} · n/N)"
    if sem_pk:
        texto += " · " + t("dados.sem_ordem")
    return texto, COR["cinza"]


def _filtravel(tipo: str) -> bool:
    """Tipos cujo valor, mostrado na grade, não volta como literal confiável (bytea, json, listas)."""
    normal = filtro.tipo_normal(tipo)
    return not (normal.endswith("[]") or normal in ("bytea", "json", "jsonb"))


def condicao_da_celula(col: _modelo.Coluna, valor) -> filtro.Condicao:
    """"igual a este valor" para uma célula; ValueError (com o motivo) quando o valor não dá para filtrar assim."""
    if valor is None:
        return filtro.Condicao(col.id, "vazio")
    if not _filtravel(col.tipo) or isinstance(valor, (bytes, memoryview, list, dict)):
        raise ValueError(t("dados.e.celula", coluna=col.nome, tipo=col.tipo))
    if filtro.familia(col.tipo) == "booleano":
        return filtro.Condicao(col.id, "verdadeiro" if valor else "falso")
    return filtro.Condicao(col.id, "igual", [str(valor)])       # data e hora saem em ISO, que o PostgreSQL lê de volta
