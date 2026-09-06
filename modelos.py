"""Tipos compartilhados."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Leitura:
    """Uma medicao de preco num instante."""
    sku: str
    preco: float
    disponivel: bool
    momento: datetime
    origem: str = ""


@dataclass(frozen=True)
class Sinal:
    """Uma queda de preco que passou em todas as regras."""
    sku: str
    nome: str
    url: str
    categoria: str
    preco_atual: float
    media_30d: float
    minima_90d: float
    queda_pct: float
    amostras_30d: int
    e_minima_historica: bool

    @property
    def economia(self) -> float:
        return self.media_30d - self.preco_atual
