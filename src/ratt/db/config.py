"""O que o ratt db lembra entre uma execução e outra: ~/.config/ratt/config.json.

    {"lang": "en", "db": {"usuario": "jared", "porta": 5432, "banco": "estudos"}}

Nenhuma senha mora aqui: ela fica no ~/.pgpass, que é onde o psql e o psycopg procuram.
"""
import json
import os
from pathlib import Path


def arquivo():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "ratt" / "config.json"


def ler():
    try:
        return json.loads(arquivo().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def gravar(**campos):
    config = ler()
    config.update(campos)
    destino = arquivo()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(config, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
