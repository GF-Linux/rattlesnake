"""Passa por todos os templates do ratt. Rode num terminal com a releitura ligada:

    python tests/exemplos/tudo.py          # relido
    RATT=0 python tests/exemplos/tudo.py   # o pandas cru, para comparar

Os dados: pesagens.csv e lotes.csv (pequenos, com a sujeira de propósito) e um rebanho
inventado com semente fixa — os números não são de animal nenhum.
"""
from pathlib import Path

import numpy as np
import pandas as pd

DADOS = Path(__file__).resolve().parent.parent / "dados"

# ── as pesagens: 6 linhas, com " nelore " sujo, um peso vazio e datas dd/mm/aaaa ──
df = pd.read_csv(DADOS / "pesagens.csv", sep=";", decimal=",")
lotes = pd.read_csv(DADOS / "lotes.csv", sep=";", decimal=",")

print(df.shape)
print(df.head())
print(df.dtypes)
print(df.columns)
print(df.index)
print(df.set_index("brinco").index)
print(df.isna().sum())
print(df.nunique())
print(df["raca"].value_counts())
print(df["raca"].value_counts(normalize=True))
print(df["raca"].unique())
print(df.describe(include="all"))
print(df[df["peso_kg"] > 300])
print(df.sort_values("peso_kg", ascending=False))
print(df.dropna())
print(df.merge(lotes, on="lote"))
print(df["raca"].str.strip().str.lower())
print(df["peso_kg"].fillna(0))
print(df.groupby("lote")["peso_kg"].mean())
print(df.groupby("lote").size())
print(df.groupby("lote")["peso_kg"].agg(["mean", "count", "min", "max"]))
print(df.groupby(["lote", "raca"])["peso_kg"].mean())
print(df.pivot_table(index="raca", columns="lote", values="peso_kg", aggfunc="mean"))
print(pd.crosstab(df["raca"], df["lote"]))
print(pd.cut(df["peso_kg"], bins=3))
print(pd.to_datetime(df["data"], format="%d/%m/%Y"))
df["quando"] = pd.to_datetime(df["data"], format="%d/%m/%Y")
print(df["quando"].dt.day_name())
print(df["quando"].dt.to_period("M"))

# ── um rebanho inventado: 120 animais ──
rng = np.random.default_rng(42)
rebanho = pd.DataFrame({
    "peso": rng.normal(420, 60, 120).round(1),
    "idade_meses": rng.integers(12, 90, 120),
    "escore": rng.choice([2.5, 3.0, 3.5, 4.0], 120),
    "lote": rng.choice(["A", "B", "C"], 120),
})
rebanho.loc[rng.choice(120, 7, replace=False), "peso"] = np.nan
print(rebanho)
print(rebanho.describe())
print(rebanho["peso"].describe())
print(rebanho["peso"])
print(rebanho.mean(numeric_only=True))
print(rebanho[["peso", "idade_meses", "escore"]].corr())

# ── uma série de tempo inventada: 60 dias ──
temp = pd.Series(24 + rng.normal(0, 2, 60).cumsum() / 4,
                 index=pd.date_range("2026-01-01", periods=60, freq="D"), name="temp")
print(temp.index)
print(pd.date_range("2026-01-31", periods=4, freq="ME"))
print(temp.resample("W").mean())
print(temp.rolling(7).mean())
print(temp.diff())
