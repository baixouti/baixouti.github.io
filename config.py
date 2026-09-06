"""Carrega config.yaml, .env e o catalogo."""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent


def _carregar_env() -> None:
    """Le o .env se existir. No GitHub Actions as variaveis ja vem do ambiente."""
    caminho = RAIZ / ".env"
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip())


_carregar_env()


def env(chave: str, padrao: str = "") -> str:
    return os.environ.get(chave, padrao).strip()


def carregar_config() -> dict:
    with open(RAIZ / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass(frozen=True)
class Produto:
    sku: str
    nome: str
    url: str
    fonte: str
    categoria: str
    faixa: str
    ativo: bool

    @property
    def slug(self) -> str:
        return self.sku.lower().replace("_", "-")


def carregar_catalogo(caminho: Path | None = None) -> list[Produto]:
    """Le catalogo.csv. Linhas comecando com # sao ignoradas."""
    caminho = caminho or (RAIZ / "catalogo.csv")
    produtos: list[Produto] = []
    with open(caminho, encoding="utf-8", newline="") as f:
        linhas = [l for l in f if not l.lstrip().startswith("#")]
    for linha in csv.DictReader(linhas):
        if not linha.get("sku"):
            continue
        ativo = str(linha.get("ativo", "sim")).strip().lower() in ("sim", "1", "true", "s")
        produtos.append(
            Produto(
                sku=linha["sku"].strip(),
                nome=linha["nome"].strip(),
                url=linha["url"].strip(),
                fonte=linha.get("fonte", "generico").strip() or "generico",
                categoria=linha.get("categoria", "").strip(),
                faixa=linha.get("faixa", "").strip(),
                ativo=ativo,
            )
        )
    skus = [p.sku for p in produtos]
    duplicados = {s for s in skus if skus.count(s) > 1}
    if duplicados:
        raise ValueError(f"SKU repetido no catalogo: {sorted(duplicados)}")
    return produtos
