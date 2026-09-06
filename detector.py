"""A regra que decide o que vira post.

Uma oferta so passa se as DUAS condicoes forem verdadeiras:
  A) preco atual esta pelo menos X% abaixo da media dos ultimos 30 dias
  B) preco atual esta perto da minima dos ultimos 90 dias

A condicao B e a que mata o 'de R$ 899 por R$ 499' que nunca custou R$ 899:
se o produto vive oscilando, a minima de 90 dias denuncia.
"""
from __future__ import annotations

import sqlite3
from statistics import mean

from .config import Produto
from .historico import alerta_recente, serie
from .modelos import Sinal


def avaliar(con: sqlite3.Connection, produto: Produto, cfg: dict) -> Sinal | None:
    regras = cfg["deteccao"]

    s30 = serie(con, produto.sku, 30)
    if len(s30) < int(regras["minimo_amostras_30d"]):
        return None  # historico curto demais para ter opiniao

    atual = s30[-1][1]
    if atual < float(regras["preco_minimo"]):
        return None

    media_30d = mean(p for _, p in s30)
    if media_30d <= 0:
        return None

    queda = (media_30d - atual) / media_30d
    if queda < float(regras["queda_minima"]):
        return None

    s90 = serie(con, produto.sku, 90) or s30
    minima_90d = min(p for _, p in s90)
    if atual > minima_90d * (1 + float(regras["margem_sobre_minima"])):
        return None

    if alerta_recente(con, produto.sku, int(regras["cooldown_dias"])):
        return None

    return Sinal(
        sku=produto.sku,
        nome=produto.nome,
        url=produto.url,
        categoria=produto.categoria,
        preco_atual=atual,
        media_30d=media_30d,
        minima_90d=minima_90d,
        queda_pct=queda,
        amostras_30d=len(s30),
        e_minima_historica=atual <= minima_90d,
    )


def varrer(con: sqlite3.Connection, produtos: list[Produto], cfg: dict) -> list[Sinal]:
    """Avalia o catalogo inteiro e devolve os melhores candidatos."""
    sinais = [s for s in (avaliar(con, p, cfg) for p in produtos if p.ativo) if s]
    sinais.sort(key=lambda s: s.queda_pct, reverse=True)
    return sinais[: int(cfg["deteccao"]["max_candidatos_por_rodada"])]
