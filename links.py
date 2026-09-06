"""Monta o link de afiliado a partir da URL limpa do produto."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .config import env

# Parametros de rastreio que os sites grudam na URL e que nao servem para nada.
_LIXO = {"ref", "ref_", "th", "psc", "linkCode", "linkId", "pd_rd_i", "pd_rd_r",
         "pd_rd_w", "pf_rd_p", "pf_rd_r", "content-id", "utm_source", "utm_medium",
         "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "tracking_id"}


def limpar(url: str) -> str:
    partes = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(partes.query) if k not in _LIXO]
    return urlunparse(partes._replace(query=urlencode(query)))


def afiliar(url: str) -> str:
    """Aplica a tag de afiliado conforme o dominio.

    Amazon: parametro tag= (funciona sem a PA-API, e o mesmo que o SiteStripe gera).
    Mercado Livre / Shopee: o link precisa ser gerado no painel de cada programa;
    aqui devolvemos a URL limpa e voce cola o link do painel no catalogo.
    """
    url = limpar(url)
    dominio = urlparse(url).netloc.lower()

    if "amazon.com.br" in dominio:
        tag = env("AMAZON_TAG")
        if not tag:
            return url
        partes = urlparse(url)
        query = dict(parse_qsl(partes.query))
        query["tag"] = tag
        return urlunparse(partes._replace(query=urlencode(query)))

    return url


def encurtar(url: str, sku: str) -> str:
    """Se voce subir o Cloudflare Worker (pasta worker/), passa a contar cliques."""
    base = env("ENCURTADOR_BASE")
    if not base:
        return url
    return f"{base.rstrip('/')}/ir/{sku}"
