"""Extracao de preco.

Estrategia, em ordem:
  1. API do Mercado Livre quando a URL for de la (mais confiavel que HTML).
  2. JSON-LD schema.org Product/Offer - padrao que Amazon, Magalu, Kabum,
     Americanas e a maioria do e-commerce brasileiro publica na propria pagina.
  3. Microdados / meta tags (itemprop=price, og:price:amount, twitter:data1).

Se nenhuma funcionar, devolve None em vez de inventar numero.
"""
from __future__ import annotations

import json
import re
import time
import urllib.robotparser as robotparser
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .modelos import Leitura

_ULTIMA_REQUISICAO = 0.0
_ROBOTS: dict[str, robotparser.RobotFileParser | None] = {}

_INDISPONIVEL = re.compile(
    r"outofstock|soldout|discontinued|indispon|esgotad", re.I)


class PrecoNaoEncontrado(Exception):
    pass


# ------------------------------------------------------------------ helpers

def _esperar(intervalo: float) -> None:
    global _ULTIMA_REQUISICAO
    delta = time.monotonic() - _ULTIMA_REQUISICAO
    if delta < intervalo:
        time.sleep(intervalo - delta)
    _ULTIMA_REQUISICAO = time.monotonic()


def _normalizar(valor: Any) -> float | None:
    """Converte '1.299,90', 'R$ 1299.90', 1299.9 em float."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        preco = float(valor)
        return preco if preco > 0 else None
    texto = str(valor).strip()
    if not texto:
        return None
    texto = re.sub(r"[^\d,.\-]", "", texto)
    if not texto:
        return None
    # 1.299,90 -> 1299.90   |   1,299.90 -> 1299.90   |   1299.90 -> 1299.90
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        preco = float(texto)
    except ValueError:
        return None
    return preco if preco > 0 else None


def _achatar(no: Any) -> Iterable[dict]:
    """Percorre JSON-LD aninhado (@graph, listas) devolvendo cada dicionario."""
    if isinstance(no, dict):
        yield no
        for valor in no.values():
            if isinstance(valor, (dict, list)):
                yield from _achatar(valor)
    elif isinstance(no, list):
        for item in no:
            yield from _achatar(item)


def robots_permite(url: str, user_agent: str) -> bool:
    """Respeita o robots.txt do site. Em caso de duvida, permite."""
    base = urlparse(url)
    dominio = f"{base.scheme}://{base.netloc}"
    if dominio not in _ROBOTS:
        parser = robotparser.RobotFileParser()
        parser.set_url(f"{dominio}/robots.txt")
        try:
            parser.read()
        except Exception:
            parser = None
        _ROBOTS[dominio] = parser
    parser = _ROBOTS[dominio]
    if parser is None:
        return True
    try:
        return parser.can_fetch(user_agent, url)
    except Exception:
        return True


# ------------------------------------------------------------ extratores

def de_jsonld(html: str) -> tuple[float | None, bool]:
    sopa = BeautifulSoup(html, "html.parser")
    for script in sopa.find_all("script", type="application/ld+json"):
        bruto = script.string or script.get_text() or ""
        try:
            dados = json.loads(bruto)
        except (json.JSONDecodeError, TypeError):
            continue
        for no in _achatar(dados):
            tipo = no.get("@type", "")
            tipos = tipo if isinstance(tipo, list) else [tipo]
            if not any(str(t).lower() in ("offer", "aggregateoffer", "product") for t in tipos):
                continue
            preco = _normalizar(no.get("price") or no.get("lowPrice"))
            if preco is None:
                continue
            disp = str(no.get("availability", "")) or ""
            return preco, not bool(_INDISPONIVEL.search(disp))
    return None, True


def de_microdados(html: str) -> tuple[float | None, bool]:
    sopa = BeautifulSoup(html, "html.parser")
    seletores = [
        ("meta", {"itemprop": "price"}, "content"),
        ("meta", {"property": "product:price:amount"}, "content"),
        ("meta", {"property": "og:price:amount"}, "content"),
        ("meta", {"name": "twitter:data1"}, "content"),
        ("span", {"itemprop": "price"}, None),
    ]
    for tag, attrs, campo in seletores:
        elemento = sopa.find(tag, attrs=attrs)
        if not elemento:
            continue
        valor = elemento.get(campo) if campo else elemento.get_text()
        preco = _normalizar(valor)
        if preco is not None:
            disp_tag = sopa.find("meta", attrs={"itemprop": "availability"})
            disp = str(disp_tag.get("content", "")) if disp_tag else ""
            return preco, not bool(_INDISPONIVEL.search(disp))
    return None, True


# ------------------------------------------------------------ fonte: ML

_ML_ID = re.compile(r"(ML[A-Z]-?\d{6,})", re.I)


def id_mercadolivre(url: str) -> str | None:
    achado = _ML_ID.search(url)
    return achado.group(1).upper().replace("-", "") if achado else None


def buscar_mercadolivre(url: str, sessao: requests.Session, timeout: int) -> tuple[float, bool] | None:
    item = id_mercadolivre(url)
    if not item:
        return None
    try:
        resp = sessao.get(f"https://api.mercadolibre.com/items/{item}", timeout=timeout)
        if resp.status_code != 200:
            return None
        dados = resp.json()
    except (requests.RequestException, ValueError):
        return None
    preco = _normalizar(dados.get("price"))
    if preco is None:
        return None
    disponivel = str(dados.get("status", "")).lower() == "active" and \
        int(dados.get("available_quantity") or 0) > 0
    return preco, disponivel


# ------------------------------------------------------------------ fachada

def buscar_preco(sku: str, url: str, cfg: dict,
                 sessao: requests.Session | None = None) -> Leitura | None:
    """Devolve uma Leitura ou None se o preco nao pode ser lido com confianca."""
    coleta = cfg["coleta"]
    ua = coleta["user_agent"]
    sessao = sessao or requests.Session()
    sessao.headers.update({
        "User-Agent": ua,
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    })
    agora = datetime.now(timezone.utc)

    # 1. Mercado Livre pela API
    _esperar(coleta["intervalo_segundos"])
    ml = buscar_mercadolivre(url, sessao, coleta["timeout_segundos"])
    if ml:
        preco, disponivel = ml
        return Leitura(sku=sku, preco=preco, disponivel=disponivel,
                       momento=agora, origem="mercadolivre-api")

    # 2 e 3. HTML da propria pagina
    if not robots_permite(url, ua):
        return None

    html = ""
    for tentativa in range(int(coleta["tentativas"])):
        _esperar(coleta["intervalo_segundos"])
        try:
            resp = sessao.get(url, timeout=coleta["timeout_segundos"])
            if resp.status_code == 200:
                html = resp.text
                break
            if resp.status_code in (403, 404, 410):
                return None
        except requests.RequestException:
            if tentativa == int(coleta["tentativas"]) - 1:
                return None
            time.sleep(2)
    if not html:
        return None

    preco, disponivel = de_jsonld(html)
    origem = "jsonld"
    if preco is None:
        preco, disponivel = de_microdados(html)
        origem = "microdados"
    if preco is None:
        return None
    return Leitura(sku=sku, preco=preco, disponivel=disponivel,
                   momento=agora, origem=origem)
