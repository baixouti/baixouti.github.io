"""Baixou - bot de curadoria de ofertas com link de afiliado.

Tudo em um arquivo so, de proposito: assim voce cria o projeto no GitHub pelo
navegador, sem precisar montar pastas nem instalar nada no computador.

Uso:
    python radar.py <comando>

Comandos em `python radar.py ajuda`.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import json
import math
import os
import random
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.robotparser as robotparser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
import yaml
from bs4 import BeautifulSoup

RAIZ = Path(__file__).resolve().parent




# ======================================================================
# CONFIG
# ======================================================================

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


# ======================================================================
# MODELOS
# ======================================================================

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


# ======================================================================
# HISTORICO
# ======================================================================

CAMINHO_BANCO = RAIZ / "dados" / "precos.db"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS leituras (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sku        TEXT    NOT NULL,
    preco      REAL    NOT NULL,
    disponivel INTEGER NOT NULL DEFAULT 1,
    momento    TEXT    NOT NULL,
    origem     TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_leituras_sku_momento ON leituras(sku, momento);

CREATE TABLE IF NOT EXISTS alertas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sku          TEXT    NOT NULL,
    preco        REAL    NOT NULL,
    media_30d    REAL    NOT NULL,
    queda_pct    REAL    NOT NULL,
    criado_em    TEXT    NOT NULL,
    estado       TEXT    NOT NULL DEFAULT 'pendente',
    message_id   INTEGER,
    comentario   TEXT    DEFAULT '',
    decidido_em  TEXT
);
CREATE INDEX IF NOT EXISTS idx_alertas_estado ON alertas(estado);
CREATE INDEX IF NOT EXISTS idx_alertas_sku ON alertas(sku, criado_em);

CREATE TABLE IF NOT EXISTS controle (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""


def agora() -> datetime:
    return datetime.now(timezone.utc)


def conectar(caminho: Path | None = None) -> sqlite3.Connection:
    caminho = caminho or CAMINHO_BANCO
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA)
    return con


# ---------------------------------------------------------------- leituras

def gravar_leituras(con: sqlite3.Connection, leituras: list[Leitura]) -> int:
    if not leituras:
        return 0
    con.executemany(
        "INSERT INTO leituras (sku, preco, disponivel, momento, origem) VALUES (?,?,?,?,?)",
        [(l.sku, l.preco, int(l.disponivel), l.momento.isoformat(), l.origem) for l in leituras],
    )
    con.commit()
    return len(leituras)


def serie(con: sqlite3.Connection, sku: str, dias: int) -> list[tuple[datetime, float]]:
    """Precos de um SKU nos ultimos N dias, do mais antigo para o mais novo."""
    corte = (agora() - timedelta(days=dias)).isoformat()
    cur = con.execute(
        "SELECT momento, preco FROM leituras "
        "WHERE sku = ? AND momento >= ? AND disponivel = 1 ORDER BY momento",
        (sku, corte),
    )
    return [(datetime.fromisoformat(r["momento"]), r["preco"]) for r in cur.fetchall()]


def ultimo_preco(con: sqlite3.Connection, sku: str) -> float | None:
    cur = con.execute(
        "SELECT preco FROM leituras WHERE sku = ? AND disponivel = 1 "
        "ORDER BY momento DESC LIMIT 1",
        (sku,),
    )
    linha = cur.fetchone()
    return linha["preco"] if linha else None


def limpar_antigas(con: sqlite3.Connection, manter_dias: int = 400) -> int:
    """Poda o banco para ele nao crescer sem limite dentro do repositorio."""
    corte = (agora() - timedelta(days=manter_dias)).isoformat()
    cur = con.execute("DELETE FROM leituras WHERE momento < ?", (corte,))
    con.commit()
    return cur.rowcount


# ---------------------------------------------------------------- alertas

def alerta_recente(con: sqlite3.Connection, sku: str, dias: int) -> bool:
    """True se ja alertamos esse SKU dentro da janela de cooldown."""
    corte = (agora() - timedelta(days=dias)).isoformat()
    cur = con.execute(
        "SELECT 1 FROM alertas WHERE sku = ? AND criado_em >= ? "
        "AND estado IN ('pendente','publicado') LIMIT 1",
        (sku, corte),
    )
    return cur.fetchone() is not None


def registrar_alerta(con: sqlite3.Connection, sku: str, preco: float,
                     media_30d: float, queda_pct: float) -> int:
    cur = con.execute(
        "INSERT INTO alertas (sku, preco, media_30d, queda_pct, criado_em, estado) "
        "VALUES (?,?,?,?,?, 'pendente')",
        (sku, preco, media_30d, queda_pct, agora().isoformat()),
    )
    con.commit()
    return int(cur.lastrowid)


def vincular_mensagem(con: sqlite3.Connection, alerta_id: int, message_id: int) -> None:
    con.execute("UPDATE alertas SET message_id = ? WHERE id = ?", (message_id, alerta_id))
    con.commit()


def decidir_alerta(con: sqlite3.Connection, alerta_id: int, estado: str) -> sqlite3.Row | None:
    """estado: 'publicado' ou 'descartado'."""
    con.execute(
        "UPDATE alertas SET estado = ?, decidido_em = ? WHERE id = ? AND estado = 'pendente'",
        (estado, agora().isoformat(), alerta_id),
    )
    con.commit()
    cur = con.execute("SELECT * FROM alertas WHERE id = ?", (alerta_id,))
    return cur.fetchone()


def alerta(con: sqlite3.Connection, alerta_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM alertas WHERE id = ?", (alerta_id,)).fetchone()


def alerta_por_mensagem(con: sqlite3.Connection, message_id: int) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM alertas WHERE message_id = ? ORDER BY id DESC LIMIT 1",
        (message_id,),
    ).fetchone()


def gravar_comentario(con: sqlite3.Connection, alerta_id: int, texto: str) -> None:
    """A sua linha sobre a oferta. E o que separa o canal de um robo."""
    con.execute("UPDATE alertas SET comentario = ? WHERE id = ?", (texto, alerta_id))
    con.commit()


def publicados(con: sqlite3.Connection, limite: int = 100) -> list[sqlite3.Row]:
    cur = con.execute(
        "SELECT * FROM alertas WHERE estado = 'publicado' ORDER BY decidido_em DESC LIMIT ?",
        (limite,),
    )
    return cur.fetchall()


# ---------------------------------------------------------------- controle

def ler_controle(con: sqlite3.Connection, chave: str, padrao: str = "") -> str:
    linha = con.execute("SELECT valor FROM controle WHERE chave = ?", (chave,)).fetchone()
    return linha["valor"] if linha else padrao


def gravar_controle(con: sqlite3.Connection, chave: str, valor: str) -> None:
    con.execute(
        "INSERT INTO controle (chave, valor) VALUES (?,?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (chave, str(valor)),
    )
    con.commit()


# ======================================================================
# FONTES
# ======================================================================

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
    """Le o preco pela API oficial.

    O Mercado Livre proibe a leitura das paginas por robots.txt, entao a API e
    o unico caminho - inclusive para a coleta de todo dia, nao so para montar
    o catalogo. Por isso o token entra aqui tambem.
    """
    item = id_mercadolivre(url)
    if not item:
        return None
    cabecalhos = {}
    token = token_ml()
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    try:
        resp = sessao.get(f"https://api.mercadolibre.com/items/{item}",
                          headers=cabecalhos, timeout=timeout)
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

MOTIVOS = {
    "robots": "o robots.txt da loja proibe leitura automatizada",
    "bloqueado": "a loja bloqueou o acesso (403) - ela recusa robos",
    "nao_existe": "pagina nao encontrada (404) - o link pode ter mudado",
    "http": "a loja respondeu com erro",
    "rede": "nao consegui conectar na loja",
    "sem_preco": "a pagina abriu, mas nao publica o preco de forma legivel "
                 "(provavelmente monta por JavaScript)",
}


def buscar_preco_detalhado(sku: str, url: str, cfg: dict,
                           sessao: requests.Session | None = None
                           ) -> tuple[Leitura | None, str]:
    """Igual a buscar_preco, mas devolve tambem o motivo da falha.

    O motivo importa: 'a loja me bloqueou' e 'a pagina nao tem preco' pedem
    decisoes diferentes. Sem isso voce fica no escuro.
    """
    coleta = cfg["coleta"]
    ua = coleta["user_agent"]
    sessao = sessao or requests.Session()
    sessao.headers.update({
        "User-Agent": ua,
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    })
    momento = datetime.now(timezone.utc)

    _esperar(coleta["intervalo_segundos"])
    ml = buscar_mercadolivre(url, sessao, coleta["timeout_segundos"])
    if ml:
        preco, disponivel = ml
        return Leitura(sku=sku, preco=preco, disponivel=disponivel,
                       momento=momento, origem="mercadolivre-api"), ""

    if not robots_permite(url, ua):
        return None, "robots"

    html_pagina, codigo = "", 0
    for tentativa in range(int(coleta["tentativas"])):
        _esperar(coleta["intervalo_segundos"])
        try:
            resp = sessao.get(url, timeout=coleta["timeout_segundos"])
            codigo = resp.status_code
            if codigo == 200:
                html_pagina = resp.text
                break
            if codigo in (403, 404, 410):
                break
        except requests.RequestException:
            if tentativa == int(coleta["tentativas"]) - 1:
                return None, "rede"
            time.sleep(2)

    if not html_pagina:
        if codigo == 403:
            return None, "bloqueado"
        if codigo in (404, 410):
            return None, "nao_existe"
        return None, "http" if codigo else "rede"

    preco, disponivel = de_jsonld(html_pagina)
    origem = "jsonld"
    if preco is None:
        preco, disponivel = de_microdados(html_pagina)
        origem = "microdados"
    if preco is None:
        return None, "sem_preco"
    return Leitura(sku=sku, preco=preco, disponivel=disponivel,
                   momento=momento, origem=origem), ""


def buscar_preco(sku: str, url: str, cfg: dict,
                 sessao: requests.Session | None = None) -> Leitura | None:
    leitura, _ = buscar_preco_detalhado(sku, url, cfg, sessao)
    return leitura


# ======================================================================
# LINKS
# ======================================================================

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


# ======================================================================
# DETECTOR
# ======================================================================

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


# ======================================================================
# TELEGRAM
# ======================================================================

API = "https://api.telegram.org/bot{token}/{metodo}"


class TelegramErro(Exception):
    pass


def _chamar(metodo: str, **params: Any) -> Any:
    token = env("TELEGRAM_TOKEN")
    if not token:
        raise TelegramErro("TELEGRAM_TOKEN nao configurado")
    resp = requests.post(API.format(token=token, metodo=metodo), json=params, timeout=30)
    dados = resp.json()
    if not dados.get("ok"):
        raise TelegramErro(f"{metodo}: {dados.get('description')}")
    return dados.get("result")


def _brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# --------------------------------------------------------------- candidatos

def enviar_candidato(con: sqlite3.Connection, sinal: Sinal) -> int:
    """Manda a oferta para o seu chat privado com os botoes de decisao."""
    alerta_id = registrar_alerta(con, sinal.sku, sinal.preco_atual,
                                 sinal.media_30d, sinal.queda_pct)

    selo = "MINIMA HISTORICA" if sinal.e_minima_historica else "queda confirmada"
    texto = (
        f"<b>{html.escape(sinal.nome)}</b>\n"
        f"<i>{html.escape(sinal.categoria or 'sem categoria')} · {selo}</i>\n\n"
        f"Agora: <b>{_brl(sinal.preco_atual)}</b>\n"
        f"Media 30d: {_brl(sinal.media_30d)}  ({sinal.queda_pct:.0%} abaixo)\n"
        f"Minima 90d: {_brl(sinal.minima_90d)}\n"
        f"Economia: {_brl(sinal.economia)}\n"
        f"Amostras: {sinal.amostras_30d} leituras\n\n"
        f'<a href="{html.escape(sinal.url)}">abrir produto</a>\n\n'
        f"<i>Responda esta mensagem com uma frase sua antes de publicar.</i>"
    )
    resultado = _chamar(
        "sendMessage",
        chat_id=env("TELEGRAM_ADMIN_CHAT_ID"),
        text=texto,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup={
            "inline_keyboard": [[
                {"text": "Publicar", "callback_data": f"pub:{alerta_id}"},
                {"text": "Descartar", "callback_data": f"des:{alerta_id}"},
            ]]
        },
    )
    vincular_mensagem(con, alerta_id, int(resultado["message_id"]))
    return alerta_id


# --------------------------------------------------------------- publicacao

def publicar_no_canal(sinal_row: sqlite3.Row, produto: Produto,
                      cfg: dict, comentario: str = "") -> int:
    preco = float(sinal_row["preco"])
    media = float(sinal_row["media_30d"])
    queda = float(sinal_row["queda_pct"])

    link = encurtar(afiliar(produto.url), produto.sku)
    linhas = [f"<b>{html.escape(produto.nome)}</b>"]
    if comentario:
        linhas.append(f"\n{html.escape(comentario)}")
    linhas += [
        "",
        f"Por <b>{_brl(preco)}</b>",
        f"<s>{_brl(media)}</s> era a media dos ultimos 30 dias — {queda:.0%} de queda",
        "",
        f'<a href="{html.escape(link)}">Ver a oferta</a>',
        "",
        f"<i>{html.escape(cfg['telegram']['rodape'])}</i>",
    ]
    resultado = _chamar(
        "sendMessage",
        chat_id=env("TELEGRAM_CANAL"),
        text="\n".join(linhas),
        parse_mode="HTML",
        disable_web_page_preview=False,
    )
    return int(resultado["message_id"])


# --------------------------------------------------------------- aprovacoes

def processar_aprovacoes(con: sqlite3.Connection, catalogo: dict[str, Produto],
                         cfg: dict) -> dict[str, int]:
    """Le o getUpdates e executa o que voce decidiu desde a ultima rodada."""
    offset = int(ler_controle(con, "telegram_offset", "0") or 0)
    try:
        atualizacoes = _chamar("getUpdates", offset=offset, timeout=0,
                               allowed_updates=["callback_query", "message"])
    except TelegramErro:
        return {"publicados": 0, "descartados": 0, "comentarios": 0}

    contagem = {"publicados": 0, "descartados": 0, "comentarios": 0}

    for upd in atualizacoes:
        offset = max(offset, int(upd["update_id"]) + 1)

        # (a) voce respondeu a um candidato com a sua frase
        msg = upd.get("message") or {}
        resposta = msg.get("reply_to_message")
        if resposta and msg.get("text"):
            linha = alerta_por_mensagem(con, int(resposta["message_id"]))
            if linha:
                gravar_comentario(con, int(linha["id"]), msg["text"].strip())
                contagem["comentarios"] += 1
            continue

        # (b) /start ou /id numa conversa privada: o bot se apresenta e diz o
        #     numero do chat. Sem isto o bot parece morto para quem manda "oi".
        texto = (msg.get("text") or "").strip().lower()
        chat = msg.get("chat") or {}
        if texto.startswith(("/start", "/id")) and chat.get("type") == "private":
            try:
                _chamar(
                    "sendMessage", chat_id=chat["id"], parse_mode="HTML",
                    text=(
                        "<b>Baixou</b> — bot de curadoria de ofertas.\n\n"
                        f"O numero deste chat e <code>{chat['id']}</code>.\n"
                        "Salve como o secret <b>TELEGRAM_ADMIN_CHAT_ID</b> no GitHub.\n\n"
                        "<i>Eu nao converso. So apareco aqui quando o radar encontra "
                        "uma queda de preco de verdade, e ai voce aprova ou descarta.</i>"
                    ))
            except TelegramErro:
                pass
            continue

        # (c) voce apertou um botao
        cb = upd.get("callback_query")
        if not cb:
            continue
        dados = str(cb.get("data", ""))
        if ":" not in dados:
            continue
        acao, _, bruto = dados.partition(":")
        if not bruto.isdigit():
            continue
        alerta_id = int(bruto)

        atual = alerta(con, alerta_id)
        if atual is None or atual["estado"] != "pendente":
            _chamar("answerCallbackQuery", callback_query_id=cb["id"],
                    text="Esse ja foi decidido.")
            continue

        if acao == "pub":
            produto = catalogo.get(atual["sku"])
            if produto is None:
                _chamar("answerCallbackQuery", callback_query_id=cb["id"],
                        text="Produto saiu do catalogo.")
                continue
            linha = decidir_alerta(con, alerta_id, "publicado")
            try:
                publicar_no_canal(linha, produto, cfg, linha["comentario"] or "")
                contagem["publicados"] += 1
                aviso = "Publicado no canal."
            except TelegramErro as erro:
                decidir_alerta(con, alerta_id, "pendente")
                aviso = f"Falhou: {erro}"
        else:
            decidir_alerta(con, alerta_id, "descartado")
            contagem["descartados"] += 1
            aviso = "Descartado."

        _chamar("answerCallbackQuery", callback_query_id=cb["id"], text=aviso)
        try:
            _chamar("editMessageReplyMarkup",
                    chat_id=cb["message"]["chat"]["id"],
                    message_id=cb["message"]["message_id"],
                    reply_markup={"inline_keyboard": [[{"text": aviso,
                                                        "callback_data": "feito"}]]})
        except TelegramErro:
            pass

    gravar_controle(con, "telegram_offset", str(offset))
    return contagem


def verificar_bot() -> dict:
    """Confere se o token vale e devolve os dados do bot."""
    return _chamar("getMe")


def verificar_canal(canal: str) -> dict:
    """Confere se o bot enxerga o canal e se e admin la."""
    return _chamar("getChat", chat_id=canal)


def mensagem_de_teste(canal: str) -> int:
    resultado = _chamar(
        "sendMessage", chat_id=canal, parse_mode="HTML",
        text="<b>Baixou</b>\n\nCanal configurado. Este e um teste automatico "
             "e pode ser apagado.")
    return int(resultado["message_id"])


def descobrir_chat_id() -> None:
    """Utilitario: mande qualquer mensagem para o seu bot e rode isto."""
    for upd in _chamar("getUpdates") or []:
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat:
            print(f"chat_id={chat.get('id')}  tipo={chat.get('type')}  "
                  f"nome={chat.get('title') or chat.get('first_name')}")


# ======================================================================
# SITE
# ======================================================================

SAIDA = RAIZ / "site"

CSS = """
:root{--paper:#F1F3F2;--surface:#fff;--surface-2:#E6EBEA;--ink:#0E1A1D;--ink-2:#3D5155;
--ink-3:#6E8286;--line:#D3DAD9;--line-strong:#AFBDBB;--petrol:#124F5C;--rust:#9A3412;--ok:#186B4E}
@media (prefers-color-scheme:dark){:root{--paper:#0B1416;--surface:#121F22;--surface-2:#18292D;
--ink:#E7EDEC;--ink-2:#AABCBC;--ink-3:#7E9497;--line:#22363A;--line-strong:#38504F;--petrol:#66C6D6;--rust:#EE9264;--ok:#5CC694}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:Newsreader,Georgia,serif;
font-size:18px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:880px;margin:0 auto;padding:0 22px 80px}
a{color:var(--petrol);text-underline-offset:3px}
h1,h2,h3,.disp{font-family:Archivo,Helvetica,Arial,sans-serif;font-weight:700;
letter-spacing:-.02em;line-height:1.15;text-wrap:balance;margin:0}
.mono{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}
header{padding:44px 0 26px;border-bottom:1.5px solid var(--line);margin-bottom:34px}
header h1{font-size:34px}
header p{color:var(--ink-2);margin:10px 0 0;font-size:17px}
header a{text-decoration:none}
.grid{display:grid;gap:14px}
.card{background:var(--surface);border:1.5px solid var(--line);padding:18px 20px;
display:grid;grid-template-columns:1fr auto;gap:18px;align-items:center;text-decoration:none;color:inherit}
.card:hover{border-color:var(--petrol)}
.card h3{font-size:17px;margin-bottom:5px}
.card .cat{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.1em;
text-transform:uppercase;color:var(--ink-3)}
.preco{font-family:Archivo,sans-serif;font-weight:700;font-size:23px;letter-spacing:-.03em;
font-variant-numeric:tabular-nums;color:var(--ok);white-space:nowrap;text-align:right}
.preco small{display:block;font-family:"IBM Plex Mono",monospace;font-size:10.5px;font-weight:500;
letter-spacing:.08em;color:var(--ink-3);margin-top:5px}
.fatos{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:0;
border:1.5px solid var(--line);background:var(--surface);margin:26px 0}
.fato{padding:16px 18px;border-right:1.5px solid var(--line)}
.fato:last-child{border-right:0}
.fato .k{font-family:"IBM Plex Mono",monospace;font-size:10px;letter-spacing:.11em;
text-transform:uppercase;color:var(--ink-3)}
.fato .v{font-family:Archivo,sans-serif;font-weight:700;font-size:22px;letter-spacing:-.03em;
font-variant-numeric:tabular-nums;margin-top:6px}
.fato.hoje .v{color:var(--ok)}
.cta{display:inline-block;background:var(--petrol);color:var(--paper);padding:13px 26px;
font-family:Archivo,sans-serif;font-weight:600;font-size:16px;text-decoration:none;margin:6px 0 4px}
.aviso{font-size:14px;color:var(--ink-3);margin-top:12px}
figure{margin:26px 0;background:var(--surface);border:1.5px solid var(--line);padding:20px}
figcaption{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.1em;
text-transform:uppercase;color:var(--ink-3);margin-bottom:14px}
svg{display:block;width:100%;height:auto;overflow:visible}
footer{margin-top:56px;padding-top:20px;border-top:1.5px solid var(--line);
font-size:14px;color:var(--ink-3);max-width:70ch}
.sobre{background:var(--surface);border:1.5px solid var(--line);padding:24px 26px}
.sobre h2{font-size:19px;margin-bottom:12px}
.sobre p{margin:0 0 14px;color:var(--ink-2);font-size:16.5px;max-width:64ch}
.sobre p:last-child{margin-bottom:0}
.vazio{background:var(--surface);border:1.5px dashed var(--line-strong);padding:32px 26px;
margin-top:26px;text-align:center}
.vazio h2{font-size:18px;margin-bottom:10px}
.vazio p{color:var(--ink-3);font-size:16px;margin:0 auto;max-width:52ch}
.voltar{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.1em;
text-transform:uppercase;text-decoration:none;display:inline-block;margin-bottom:22px}
@media(max-width:640px){.card{grid-template-columns:1fr;gap:10px}.preco{text-align:left}
.fato{border-right:0;border-bottom:1.5px solid var(--line)}}
"""

CABECA = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titulo}</title>
<meta name="description" content="{descricao}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;700&family=IBM+Plex+Mono:wght@400;500&family=Newsreader:opsz,wght@6..72,400;6..72,500&display=swap">
<style>{css}</style></head><body><div class="wrap">
"""


def _brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def grafico(pontos: list[tuple[datetime, float]], largura: int = 780,
            altura: int = 240) -> str:
    """Grafico de preco em SVG puro. Sem biblioteca, sem JS."""
    if len(pontos) < 2:
        return '<p class="aviso">Historico ainda curto para desenhar o grafico.</p>'

    esq, dir_, topo, base = 62, 14, 16, 30
    precos = [p for _, p in pontos]
    pmin, pmax = min(precos), max(precos)
    if pmax == pmin:
        pmax = pmin * 1.02 + 0.01
    tmin, tmax = pontos[0][0].timestamp(), pontos[-1][0].timestamp()
    span = (tmax - tmin) or 1.0

    def x(t: datetime) -> float:
        return esq + (t.timestamp() - tmin) / span * (largura - esq - dir_)

    def y(p: float) -> float:
        return topo + (pmax - p) / (pmax - pmin) * (altura - topo - base)

    caminho = " ".join(
        f"{'M' if i == 0 else 'L'}{x(t):.1f},{y(p):.1f}"
        for i, (t, p) in enumerate(pontos))
    area = (f"M{x(pontos[0][0]):.1f},{altura - base:.1f} "
            + " ".join(f"L{x(t):.1f},{y(p):.1f}" for t, p in pontos)
            + f" L{x(pontos[-1][0]):.1f},{altura - base:.1f} Z")

    grades, rotulos = [], []
    for i in range(4):
        valor = pmin + (pmax - pmin) * i / 3
        py = y(valor)
        grades.append(f'<line x1="{esq}" y1="{py:.1f}" x2="{largura - dir_}" '
                      f'y2="{py:.1f}" stroke="var(--line)" stroke-width="1"/>')
        rotulos.append(f'<text x="{esq - 9}" y="{py + 4:.1f}" text-anchor="end" '
                       f'font-size="11" font-family="IBM Plex Mono, monospace" '
                       f'fill="var(--ink-3)">{_brl(valor)}</text>')

    ultimo_t, ultimo_p = pontos[-1]
    marcadores = (
        f'<circle cx="{x(ultimo_t):.1f}" cy="{y(ultimo_p):.1f}" r="4.5" '
        f'fill="var(--ok)" stroke="var(--surface)" stroke-width="2"/>')

    datas = (f'<text x="{esq}" y="{altura - 8}" font-size="11" '
             f'font-family="IBM Plex Mono, monospace" fill="var(--ink-3)">'
             f'{pontos[0][0]:%d/%m}</text>'
             f'<text x="{largura - dir_}" y="{altura - 8}" text-anchor="end" font-size="11" '
             f'font-family="IBM Plex Mono, monospace" fill="var(--ink-3)">'
             f'{ultimo_t:%d/%m}</text>')

    return (
        f'<svg viewBox="0 0 {largura} {altura}" role="img" '
        f'aria-label="Historico de preco, de {_brl(pmin)} a {_brl(pmax)}">'
        f'{"".join(grades)}'
        f'<path d="{area}" fill="var(--petrol)" opacity="0.10"/>'
        f'<path d="{caminho}" fill="none" stroke="var(--petrol)" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'{marcadores}{"".join(rotulos)}{datas}</svg>')


PAGINA_ADICIONAR = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Adicionar produtos - Baixou</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;700&family=IBM+Plex+Mono:wght@400;500&family=Newsreader:opsz,wght@6..72,400&display=swap">
<style>
__CSS__
.form{background:var(--surface);border:1.5px solid var(--line);padding:24px 26px;margin-top:26px}
.campo{display:grid;gap:7px;margin-bottom:20px}
.campo label{font-family:"IBM Plex Mono",monospace;font-size:11px;font-weight:500;
letter-spacing:.11em;text-transform:uppercase;color:var(--ink-3)}
.campo .ajuda{font-size:14.5px;color:var(--ink-3);margin:0}
input,select,textarea{font-family:"IBM Plex Mono",monospace;font-size:15px;
background:var(--paper);color:var(--ink);border:1.5px solid var(--line-strong);
padding:10px 12px;width:100%;border-radius:0}
textarea{min-height:190px;resize:vertical;line-height:1.5;font-size:13.5px}
input:focus,select:focus,textarea:focus{outline:2px solid var(--petrol);
outline-offset:1px;border-color:var(--petrol)}
.dupla{display:grid;grid-template-columns:1fr 1fr;gap:16px}
button{font-family:"Archivo",sans-serif;font-weight:600;font-size:16px;
background:var(--petrol);color:var(--paper);border:0;padding:14px 30px;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
button.secundario{background:none;color:var(--ink-3);border:1.5px solid var(--line-strong);
padding:9px 16px;font-size:14px}
.contador{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--ink-3);
margin-top:7px;font-variant-numeric:tabular-nums}
.aviso{padding:15px 17px;margin-top:20px;border:1.5px solid var(--line);font-size:15.5px;
line-height:1.5;display:none}
.aviso.ok{display:block;background:var(--ok-soft);border-left:3px solid var(--ok);color:var(--ink-2)}
.aviso.erro{display:block;background:var(--rust-soft);border-left:3px solid var(--rust);color:var(--ink-2)}
.token-area{border-top:1.5px solid var(--line);margin-top:22px;padding-top:20px}
.token-area summary{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.1em;
text-transform:uppercase;color:var(--ink-3);cursor:pointer}
.token-area[open] summary{margin-bottom:16px}
.token-area ol{font-size:15px;color:var(--ink-2);padding-left:20px;line-height:1.55}
.token-area li{margin-bottom:7px}
</style></head><body><div class="wrap">

<header><h1>Adicionar produtos</h1>
<p>Cole os links, escolha a categoria, envie. O robo le titulo e preco de cada um
e grava no catalogo.</p></header>

<div class="form">
  <div class="campo">
    <label for="urls">Links dos produtos</label>
    <p class="ajuda">Um por linha, ou separados por espaco. Qualquer loja serve,
    e nao ha limite de quantidade.</p>
    <textarea id="urls" placeholder="https://produto.mercadolivre.com.br/MLB-...
https://www.amazon.com.br/dp/...
https://www.kabum.com.br/produto/..."></textarea>
    <div class="contador" id="contador">0 links</div>
  </div>

  <div class="dupla">
    <div class="campo">
      <label for="categoria">Categoria</label>
      <select id="categoria">__CATEGORIAS__</select>
    </div>
    <div class="campo">
      <label for="faixa">Faixa de preco</label>
      <select id="faixa">
        <option value="baixa">baixa - ate R$ 150</option>
        <option value="media" selected>media - R$ 150 a 800</option>
        <option value="alta">alta - acima de R$ 800</option>
      </select>
    </div>
  </div>

  <button id="enviar">Enviar para o catalogo</button>
  <div class="aviso" id="aviso"></div>

  <details class="token-area" id="areaToken">
    <summary>Chave de acesso do GitHub</summary>
    <div class="campo">
      <input id="token" type="password" placeholder="github_pat_...">
      <p class="ajuda">Fica guardada so neste navegador. Nao e enviada para lugar
      nenhum alem do proprio GitHub.</p>
    </div>
    <button class="secundario" id="salvarToken">Salvar neste navegador</button>
    <ol style="margin-top:18px">
      <li>Abra <a href="https://github.com/settings/personal-access-tokens/new"
          target="_blank" rel="noopener">github.com/settings/personal-access-tokens/new</a></li>
      <li>Em <b>Repository access</b>, escolha <b>Only select repositories</b> e marque
          <b>__REPO__</b></li>
      <li>Em <b>Permissions</b> &rarr; <b>Repository permissions</b>, adicione
          <b>duas</b> permissoes, ambas em <b>Read and write</b>:
          <b>Actions</b> e <b>Contents</b></li>
      <li>Clique em <b>Generate token</b>, copie e cole aqui em cima</li>
    </ol>
  </details>
</div>

<footer>Esta pagina nao aparece em buscas e nao faz nada sem a sua chave de acesso.</footer>

</div>
<script>
(function(){
  "use strict";
  var REPO = "__REPO__";
  var urls = document.getElementById("urls");
  var contador = document.getElementById("contador");
  var aviso = document.getElementById("aviso");
  var botao = document.getElementById("enviar");
  var campoToken = document.getElementById("token");
  var areaToken = document.getElementById("areaToken");

  function lista(){
    return urls.value.split(/[\s,;]+/).filter(function(u){ return u.indexOf("http") === 0; });
  }
  function contar(){
    var n = lista().length;
    contador.textContent = n + (n === 1 ? " link" : " links");
  }
  urls.addEventListener("input", contar);
  contar();

  function guardado(){
    try { return localStorage.getItem("baixou_token") || ""; } catch(e){ return ""; }
  }
  campoToken.value = guardado();
  if (!campoToken.value) areaToken.open = true;

  document.getElementById("salvarToken").addEventListener("click", function(){
    try {
      localStorage.setItem("baixou_token", campoToken.value.trim());
      mostrar("ok", "Chave guardada neste navegador.");
      areaToken.open = false;
    } catch(e){ mostrar("erro", "Este navegador nao deixou guardar a chave."); }
  });

  function mostrar(tipo, html){
    aviso.className = "aviso " + tipo;
    aviso.innerHTML = html;
  }

  function base64(texto){
    var bytes = new TextEncoder().encode(texto);
    var bruto = "";
    bytes.forEach(function(b){ bruto += String.fromCharCode(b); });
    return btoa(bruto);
  }

  function cabecalhos(token){
    return {
      "Accept": "application/vnd.github+json",
      "Authorization": "Bearer " + token,
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json"
    };
  }

  function explicar(status, corpo){
    if (status === 401 || status === 403) {
      return "A chave de acesso foi recusada. Ela precisa das permissoes " +
             "<b>Contents: Read and write</b> e <b>Actions: Read and write</b> " +
             "neste repositorio.";
    }
    if (status === 404) {
      return "Nao encontrei o repositorio <b>" + REPO + "</b>. Confira se a chave " +
             "de acesso tem esse repositorio selecionado.";
    }
    return (corpo && corpo.message) || ("HTTP " + status);
  }

  botao.addEventListener("click", async function(){
    var links = lista();
    var token = (campoToken.value || guardado()).trim();
    if (!token) {
      mostrar("erro", "Falta a chave de acesso do GitHub. Abra a secao abaixo.");
      areaToken.open = true;
      return;
    }
    if (!links.length) {
      mostrar("erro", "Cole pelo menos um link comecando com http.");
      return;
    }

    var categoria = document.getElementById("categoria").value;
    var faixa = document.getElementById("faixa").value;
    var base = "https://api.github.com/repos/" + REPO;
    var cab = cabecalhos(token);

    botao.disabled = true;
    mostrar("ok", "Gravando " + links.length + " link(s) na fila...");

    try {
      // 1. descobrir se ja existe uma fila (precisa do sha para sobrescrever)
      var sha = null;
      var atual = await fetch(base + "/contents/fila.txt", { headers: cab });
      if (atual.status === 200) { sha = (await atual.json()).sha; }
      else if (atual.status !== 404) {
        mostrar("erro", explicar(atual.status, await atual.json().catch(function(){})));
        botao.disabled = false; return;
      }

      // 2. gravar o arquivo. Uma linha por link: categoria|faixa|url
      var conteudo = links.map(function(u){
        return categoria + "|" + faixa + "|" + u;
      }).join("\\n") + "\\n";

      var corpoPut = {
        message: "fila: " + links.length + " link(s) em " + categoria,
        content: base64(conteudo)
      };
      if (sha) { corpoPut.sha = sha; }

      var gravou = await fetch(base + "/contents/fila.txt", {
        method: "PUT", headers: cab, body: JSON.stringify(corpoPut)
      });
      if (!gravou.ok) {
        mostrar("erro", explicar(gravou.status, await gravou.json().catch(function(){})));
        botao.disabled = false; return;
      }

      // 3. chamar o robo, sem passar os links pelo formulario
      var disparo = await fetch(base + "/actions/workflows/painel.yml/dispatches", {
        method: "POST", headers: cab,
        body: JSON.stringify({ ref: "main", inputs: { acao: "processar_fila" } })
      });
      if (disparo.status !== 204) {
        mostrar("erro", explicar(disparo.status, await disparo.json().catch(function(){})));
        botao.disabled = false; return;
      }

      mostrar("ok", "<b>" + links.length + " link(s) enviados.</b> O robo leva de 1 a " +
        "3 minutos, dependendo da quantidade. Acompanhe em " +
        "<a target='_blank' rel='noopener' href='https://github.com/" + REPO +
        "/actions'>Actions</a>.");
      urls.value = ""; contar();
    } catch (e) {
      mostrar("erro", "Nao consegui falar com o GitHub: " + e.message);
    }
    botao.disabled = false;
  });
})();
</script></body></html>
"""

def pagina_produto(con: sqlite3.Connection, produto: Produto, cfg: dict) -> str | None:
    pontos = serie(con, produto.sku, 180)
    if not pontos:
        return None
    precos = [p for _, p in pontos]
    atual, minimo, maximo = precos[-1], min(precos), max(precos)
    media = sum(precos) / len(precos)
    link = encurtar(afiliar(produto.url), produto.sku)
    nome = html.escape(produto.nome)

    dados = {
        "@context": "https://schema.org", "@type": "Product", "name": produto.nome,
        "category": produto.categoria,
        "offers": {"@type": "Offer", "price": f"{atual:.2f}", "priceCurrency": "BRL",
                   "url": produto.url},
    }

    return (
        CABECA.format(titulo=f"{nome} - historico de preco",
                      descricao=f"Historico real de preco de {nome}. "
                                f"Hoje {_brl(atual)}, minima {_brl(minimo)}.",
                      css=CSS)
        + f'<a class="voltar" href="./index.html">&larr; todas as ofertas</a>'
        + f'<h1>{nome}</h1>'
        + f'<p style="color:var(--ink-2);margin-top:10px">'
          f'{html.escape(produto.categoria or "")} · {len(pontos)} leituras de preco '
          f'desde {pontos[0][0]:%d/%m/%Y}</p>'
        + '<div class="fatos">'
        + f'<div class="fato hoje"><div class="k">Hoje</div><div class="v">{_brl(atual)}</div></div>'
        + f'<div class="fato"><div class="k">Minima do periodo</div><div class="v">{_brl(minimo)}</div></div>'
        + f'<div class="fato"><div class="k">Media</div><div class="v">{_brl(media)}</div></div>'
        + f'<div class="fato"><div class="k">Maxima</div><div class="v">{_brl(maximo)}</div></div>'
        + '</div>'
        + f'<figure><figcaption>Preco medido a cada 2 horas</figcaption>{grafico(pontos)}</figure>'
        + f'<a class="cta" href="{html.escape(link)}" rel="nofollow sponsored">Ver na loja</a>'
        + f'<p class="aviso">Link de afiliado: se voce comprar por ele, o site recebe uma '
          f'comissao sem custo nenhum para voce. O preco mostrado foi medido em '
          f'{pontos[-1][0]:%d/%m/%Y as %H:%M} UTC e pode ter mudado.</p>'
        + f'<script type="application/ld+json">{json.dumps(dados, ensure_ascii=False)}</script>'
        + '<footer>Historico coletado automaticamente. Nenhum preco "de/por" e informado '
          'pela loja: todos os numeros desta pagina foram medidos por nos.</footer>'
        + '</div></body></html>'
    )


def gerar(con: sqlite3.Connection, produtos: list[Produto], cfg: dict,
          saida: Path | None = None) -> int:
    saida = saida or SAIDA
    saida.mkdir(parents=True, exist_ok=True)
    (saida / ".nojekyll").write_text("", encoding="utf-8")

    cartoes, gerados = [], 0
    for produto in sorted(produtos, key=lambda p: p.nome):
        if not produto.ativo:
            continue
        pagina = pagina_produto(con, produto, cfg)
        if pagina is None:
            continue
        (saida / f"{produto.slug}.html").write_text(pagina, encoding="utf-8")
        gerados += 1

        pontos = serie(con, produto.sku, 180)
        atual = pontos[-1][1]
        minimo = min(p for _, p in pontos)
        selo = "na minima" if atual <= minimo * 1.005 else f"minima {_brl(minimo)}"
        cartoes.append(
            f'<a class="card" href="./{produto.slug}.html">'
            f'<div><div class="cat">{html.escape(produto.categoria or "geral")}</div>'
            f'<h3>{html.escape(produto.nome)}</h3></div>'
            f'<div class="preco">{_brl(atual)}<small>{selo}</small></div></a>')

    cfg_site = cfg["site"]
    canal = cfg_site.get("canal_telegram", "")
    link_canal = f"https://t.me/{canal.lstrip('@')}" if canal else ""

    if cartoes:
        miolo = ('<h2 style="font-size:20px;margin:34px 0 16px">Ofertas monitoradas</h2>'
                 f'<div class="grid">{"".join(cartoes[: int(cfg_site["destaques"])])}</div>')
        rodape = (f'{gerados} produtos monitorados. Atualizado em '
                  f'{datetime.now(timezone.utc):%d/%m/%Y as %H:%M} UTC.')
    else:
        miolo = ('<div class="vazio"><h2>O radar esta coletando</h2>'
                 '<p>Cada produto precisa de alguns dias de leitura antes de virar uma '
                 'pagina com historico. Assim que houver medicao suficiente, as ofertas '
                 'aparecem aqui automaticamente.</p></div>')
        rodape = (f'Coleta iniciada. Atualizado em '
                  f'{datetime.now(timezone.utc):%d/%m/%Y as %H:%M} UTC.')

    home = (
        CABECA.format(titulo=cfg_site["titulo"], descricao=cfg_site["subtitulo"], css=CSS)
        + f'<header><h1>{html.escape(cfg_site["titulo"])}</h1>'
          f'<p>{html.escape(cfg_site["subtitulo"])}</p></header>'
        + '<div class="sobre"><h2>Como funciona</h2>'
          '<p>Um robo mede o preco de um catalogo escolhido a dedo, de duas em duas '
          'horas, e guarda todo o historico. Quando um preco cai de verdade &mdash; '
          'abaixo da media dos ultimos 30 dias <em>e</em> perto da minima dos ultimos '
          '90 &mdash; a oferta passa pela revisao de uma pessoa antes de ser publicada.</p>'
          '<p>Nenhum preco &ldquo;de/por&rdquo; informado pela loja aparece aqui. Todo '
          'numero destas paginas foi medido por nos, e o historico completo fica visivel '
          'em cada produto. Se o desconto for invencionice, o grafico mostra.</p>'
        + (f'<p><a class="cta" href="{html.escape(link_canal)}">Receber as ofertas no '
           f'Telegram</a></p>' if link_canal else "")
        + '</div>'
        + miolo
        + f'<footer>{rodape} Este site participa de programas de afiliados: compras '
          f'feitas pelos links podem gerar comissao, sem custo adicional para voce.'
          f'</footer>'
        + '</div></body></html>'
    )
    (saida / "index.html").write_text(home, encoding="utf-8")

    # pagina de administracao: cola links e manda para o catalogo
    try:
        termos = yaml.safe_load(open(RAIZ / "termos.yaml", encoding="utf-8"))
        nomes = list(termos.keys())
    except Exception:
        nomes = ["perifericos", "componentes", "monitores", "livros"]
    opcoes = "".join(f'<option value="{html.escape(n)}">{html.escape(n)}</option>'
                     for n in nomes)
    (saida / "adicionar.html").write_text(
        PAGINA_ADICIONAR.replace("__CSS__", CSS)
                        .replace("__CATEGORIAS__", opcoes)
                        .replace("__REPO__", cfg_site.get("repositorio", "")),
        encoding="utf-8")
    return gerados

# ======================================================================
# MONTADOR DE CATALOGO
# ======================================================================

API_ML = "https://api.mercadolibre.com/sites/MLB/search"
FAIXAS = {"baixa": (25, 150), "media": (150, 800), "alta": (800, 6000)}
LIXO = re.compile(
    r"\b(kit|combo|lote|atacado|usado|recondicionad|seminov|replica|generic)\b", re.I)


def sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn").lower()


def assinatura(titulo: str) -> str:
    palavras = re.findall(r"[a-z0-9]+", sem_acento(titulo))
    return " ".join(sorted(set(w for w in palavras if len(w) > 2))[:6])


_TOKEN_ML: dict[str, Any] = {"valor": None, "expira": 0.0}

AUTORIZACAO_ML = "https://auth.mercadolivre.com.br/authorization"
TOKEN_ML_URL = "https://api.mercadolibre.com/oauth/token"
COFRE_ML = RAIZ / "dados" / "ml_token.enc"
ULTIMO_ERRO_ML: dict[str, Any] = {}


def app_id_ml() -> str:
    """O App ID do Mercado Livre.

    Fica no config.yaml, nao nos secrets, DE PROPOSITO: em OAuth o client_id
    e publico (aparece na URL de autorizacao). Se ele estiver como secret, o
    GitHub apaga o numero de qualquer saida e o link de autorizacao sai com
    *** no lugar - inutilizavel.
    """
    try:
        cfg = carregar_config().get("mercadolivre") or {}
        do_config = str(cfg.get("app_id") or "").strip()
        if do_config and not do_config.startswith("SEU_"):
            return do_config
    except Exception:
        pass
    return env("ML_CLIENT_ID")


def redirect_ml() -> str:
    try:
        cfg = carregar_config().get("mercadolivre") or {}
        valor = str(cfg.get("redirect_uri") or "").strip()
        if valor:
            return valor
    except Exception:
        pass
    return env("ML_REDIRECT_URI") or "https://baixouti.github.io/"


def _fernet():
    """Chave de criptografia derivada do secret ML_REFRESH_KEY.

    A chave de renovacao do Mercado Livre NAO pode ficar em texto puro num
    repositorio publico. Ela e gravada criptografada; a senha vive so no
    cofre de secrets do GitHub. Quem clonar o repositorio pega um arquivo
    ilegivel.
    """
    from cryptography.fernet import Fernet

    senha = env("ML_REFRESH_KEY")
    if not senha:
        return None
    material = hashlib.sha256(senha.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(material))


def guardar_refresh(refresh: str) -> bool:
    cofre = _fernet()
    if not cofre:
        print("  ML_REFRESH_KEY nao configurado - nao posso guardar a chave.")
        return False
    COFRE_ML.parent.mkdir(parents=True, exist_ok=True)
    COFRE_ML.write_bytes(cofre.encrypt(json.dumps(
        {"refresh_token": refresh,
         "gravado_em": agora().isoformat()}).encode("utf-8")))
    return True


def ler_refresh() -> tuple[str, str]:
    """Devolve (chave_de_renovacao, motivo_da_falha)."""
    if not env("ML_REFRESH_KEY"):
        return "", "sem_senha"
    if not COFRE_ML.exists():
        return "", "sem_cofre"
    try:
        dados = json.loads(_fernet().decrypt(COFRE_ML.read_bytes()))
        return dados["refresh_token"], ""
    except Exception:
        return "", "senha_errada"


def url_autorizacao() -> str:
    return (f"{AUTORIZACAO_ML}?response_type=code"
            f"&client_id={app_id_ml()}"
            f"&redirect_uri={redirect_ml()}")


def _trocar(dados: dict[str, str]) -> dict | None:
    """Fala com o endpoint de token do Mercado Livre."""
    dados = dict(dados, client_id=app_id_ml(),
                 client_secret=env("ML_CLIENT_SECRET"))
    try:
        resp = requests.post(TOKEN_ML_URL, data=dados, timeout=25,
                             headers={"Accept": "application/json",
                                      "Content-Type":
                                          "application/x-www-form-urlencoded"})
    except requests.RequestException as erro:
        print(f"  nao consegui falar com o Mercado Livre: {erro}")
        return None
    if resp.status_code != 200:
        try:
            corpo = resp.json()
        except ValueError:
            corpo = {"message": resp.text[:250]}
        ULTIMO_ERRO_ML.clear()
        ULTIMO_ERRO_ML.update({"http": resp.status_code, "error": corpo.get("error"),
                               "message": corpo.get("message"),
                               "cause": corpo.get("cause")})
        print(f"  o Mercado Livre recusou (HTTP {resp.status_code}): "
              f"error={corpo.get('error')!r} message={corpo.get('message')!r}")
        return None
    ULTIMO_ERRO_ML.clear()
    return resp.json()


def token_ml() -> str:
    """Devolve um access token valido do Mercado Livre, ou string vazia.

    O Mercado Livre nao aceita o fluxo aplicacao-para-aplicacao. O que ele
    aceita e voce autorizar a sua propria aplicacao uma vez no navegador;
    dali sai uma chave de renovacao que vale 6 meses e se renova sozinha a
    cada uso. E isso que este codigo faz.
    """
    if _TOKEN_ML["valor"] and time.time() < _TOKEN_ML["expira"]:
        return str(_TOKEN_ML["valor"])

    if not (app_id_ml() and env("ML_CLIENT_SECRET")):
        return ""

    refresh, motivo = ler_refresh()
    if not refresh:
        if motivo == "sem_senha":
            print("  Falta o secret ML_REFRESH_KEY.")
        elif motivo == "sem_cofre":
            print("  O arquivo dados/ml_token.enc nao esta no repositorio.")
        elif motivo == "senha_errada":
            print("  O cofre existe mas nao abre: o ML_REFRESH_KEY mudou.")
        return ""

    dados = _trocar({"grant_type": "refresh_token", "refresh_token": refresh})
    if not dados:
        return ""

    # O Mercado Livre devolve uma chave de renovacao NOVA a cada uso.
    # Se nao guardar, a proxima rodada falha.
    if dados.get("refresh_token"):
        guardar_refresh(dados["refresh_token"])
    _TOKEN_ML["valor"] = dados.get("access_token")
    _TOKEN_ML["expira"] = time.time() + int(dados.get("expires_in", 21600)) - 300
    return str(_TOKEN_ML["valor"] or "")


def cmd_ml_testar() -> int:
    """Testa so a renovacao da chave do Mercado Livre, sem tocar no catalogo."""
    print("App ID         :", app_id_ml() or "(vazio)")
    print("Client Secret  :", f"{len(env('ML_CLIENT_SECRET'))} caracteres"
          if env("ML_CLIENT_SECRET") else "(vazio)")
    print("Redirect       :", redirect_ml())
    print("ML_REFRESH_KEY :", "definido" if env("ML_REFRESH_KEY") else "(vazio)")
    print("Cofre existe   :", COFRE_ML.exists(),
          f"({COFRE_ML.stat().st_size} bytes)" if COFRE_ML.exists() else "")

    chave, motivo = ler_refresh()
    print("Cofre abre     :", "sim" if chave else f"nao ({motivo})")
    if not chave:
        print("\nNao ha o que testar. Refaca ml_autorizar + ml_salvar_codigo.")
        return 1

    print("\nTentando renovar...")
    dados = _trocar({"grant_type": "refresh_token", "refresh_token": chave})
    if not dados:
        print("\nFALHOU. Resposta do Mercado Livre:")
        for campo in ("http", "error", "message", "cause"):
            if ULTIMO_ERRO_ML.get(campo) is not None:
                print(f"  {campo:8} = {ULTIMO_ERRO_ML[campo]!r}")
        return 1

    if dados.get("refresh_token"):
        guardar_refresh(dados["refresh_token"])
        print("Chave nova gravada no cofre.")
    print(f"\nFUNCIONOU. Acesso valido por {dados.get('expires_in')} segundos.")
    return 0


def cmd_ml_autorizar() -> int:
    app = app_id_ml()
    if not app:
        print("Falta o App ID. Coloque no config.yaml, no bloco 'mercadolivre':")
        print('    app_id: "1234567890123456"')
        return 1
    if "*" in app:
        print("O App ID esta saindo mascarado (***).")
        print("Isso acontece quando ele esta guardado como SECRET no GitHub.")
        print("O App ID nao e segredo: apague o secret ML_CLIENT_ID e ponha o")
        print("numero no config.yaml, no bloco 'mercadolivre'.")
        return 1
    if not env("ML_CLIENT_SECRET"):
        print("Falta o secret ML_CLIENT_SECRET.")
        return 1
    if not env("ML_REFRESH_KEY"):
        print("Falta o secret ML_REFRESH_KEY.")
        print("Invente uma senha longa qualquer e salve com esse nome.")
        return 1

    url = url_autorizacao()
    texto = (
        "1. Abra este endereco no navegador, logado na sua conta do Mercado Livre:\n\n"
        f"   {url}\n\n"
        "2. Clique em autorizar.\n\n"
        "3. Voce cai numa pagina que pode dar erro ou ficar em branco. Nao importa.\n"
        "   Olhe a BARRA DE ENDERECO: ela vai ter ...?code=TG-xxxxxxxxxxxx\n\n"
        "4. Copie so o pedaco depois de code= (comeca com TG-).\n\n"
        "5. Volte no Painel, acao 'ml_salvar_codigo', e cole no campo URL.\n\n"
        "O codigo vale poucos minutos. Se demorar, refaca do passo 1."
    )
    print(texto)
    resumo = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumo:
        with open(resumo, "a", encoding="utf-8") as f:
            f.write("## Autorizar o Mercado Livre\n\n")
            f.write(f"**[Clique aqui para autorizar]({url})**\n\n")
            f.write("Depois de autorizar, copie o `code=TG-...` da barra de "
                    "endereco e use a acao `ml_salvar_codigo`.\n")
    return 0


def cmd_ml_codigo(codigo: str) -> int:
    codigo = codigo.strip()
    if "code=" in codigo:
        codigo = codigo.split("code=", 1)[1].split("&")[0]
    if not codigo:
        print("Cole o codigo que aparece depois de code= na barra de endereco.")
        return 1

    dados = _trocar({
        "grant_type": "authorization_code",
        "code": codigo,
        "redirect_uri": redirect_ml(),
    })
    if not dados:
        print("\nSe deu 'invalid_grant': o codigo expirou ou ja foi usado.")
        print("Cada codigo serve uma vez so. Refaca a autorizacao.")
        print("Se deu erro de redirect_uri: o endereco cadastrado na aplicacao")
        print("precisa ser exatamente https://baixouti.github.io/ (com a barra).")
        return 1

    if not dados.get("refresh_token"):
        print("O Mercado Livre nao devolveu chave de renovacao.")
        return 1
    if not guardar_refresh(dados["refresh_token"]):
        return 1

    print("Autorizacao concluida. A chave foi gravada criptografada em")
    print("dados/ml_token.enc e se renova sozinha a partir de agora.")
    print("\nPode rodar 'montar_o_catalogo'.")
    return 0


def buscar_ml(termo: str, sessao: requests.Session, limite: int = 25) -> list[dict]:
    cabecalhos = {}
    token = token_ml()
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    try:
        resp = sessao.get(API_ML, params={"q": termo, "limit": limite},
                          headers=cabecalhos, timeout=25)
    except requests.RequestException as erro:
        print(f"    erro de rede: {erro}")
        return []
    if resp.status_code in (401, 403):
        print("\n  O Mercado Livre recusou a busca por falta de autorizacao.\n")
        _, motivo = ler_refresh()
        if motivo == "sem_cofre":
            print("  CAUSA: o arquivo dados/ml_token.enc nao esta no repositorio.")
            print()
            print("  A autorizacao deu certo, mas o arquivo com a chave nao chegou a")
            print("  ser gravado no repositorio. Isso acontece quando o painel.yml e")
            print("  uma versao antiga, que nao commitava a pasta dados.")
            print()
            print("  1. Atualize .github/workflows/painel.yml para a versao mais nova")
            print("  2. Confira que dados/ml_token.enc aparece no repositorio")
            print("  3. Se nao aparecer, refaca ml_autorizar + ml_salvar_codigo")
        elif motivo == "senha_errada":
            print("  CAUSA: o ML_REFRESH_KEY mudou depois da autorizacao.")
            print("  Refaca ml_autorizar + ml_salvar_codigo com a senha atual.")
        elif motivo == "sem_senha":
            print("  CAUSA: falta o secret ML_REFRESH_KEY.")
        else:
            print("  A chave existe, mas o Mercado Livre recusou renova-la.")
            if ULTIMO_ERRO_ML:
                print()
                print(f"  RESPOSTA DELES: HTTP {ULTIMO_ERRO_ML.get('http')}")
                print(f"  error   = {ULTIMO_ERRO_ML.get('error')!r}")
                print(f"  message = {ULTIMO_ERRO_ML.get('message')!r}")
                if ULTIMO_ERRO_ML.get("cause"):
                    print(f"  cause   = {ULTIMO_ERRO_ML.get('cause')!r}")
                print()
                if ULTIMO_ERRO_ML.get("error") == "invalid_grant":
                    print("  invalid_grant = a chave ja foi usada ou expirou.")
                    print("  Refaca a autorizacao e confira que o ml_salvar_codigo")
                    print("  terminou com 'Autorizacao concluida'.")
                elif ULTIMO_ERRO_ML.get("error") == "invalid_client":
                    print("  invalid_client = App ID ou Client Secret nao batem com")
                    print("  a aplicacao. Confira os dois no DevCenter.")
        raise SystemExit(1)
    if resp.status_code != 200:
        print(f"    HTTP {resp.status_code}")
        return []
    return resp.json().get("results", [])


def cmd_catalogo(por_termo: int, anexar: bool) -> int:
    termos = yaml.safe_load(open(RAIZ / "termos.yaml", encoding="utf-8"))
    destino = RAIZ / "catalogo.csv"
    linhas, usados, vistos = [], set(), set()

    if anexar and destino.exists():
        for produto in carregar_catalogo():
            linhas.append({"sku": produto.sku, "nome": produto.nome, "url": produto.url,
                           "fonte": produto.fonte, "categoria": produto.categoria,
                           "faixa": produto.faixa,
                           "ativo": "sim" if produto.ativo else "nao"})
            usados.add(produto.sku)
            vistos.add(assinatura(produto.nome))
        print(f"Partindo de {len(linhas)} produtos ja no catalogo.\n")

    sessao = requests.Session()
    sessao.headers["User-Agent"] = "BaixouBot/1.0 (+https://baixouti.github.io)"

    for categoria, bloco in termos.items():
        faixa = bloco.get("faixa_padrao", "media")
        minimo, maximo = FAIXAS[faixa]
        print(f"[{categoria}]")
        for termo in bloco["termos"]:
            bons = []
            for item in buscar_ml(termo, sessao):
                titulo = (item.get("title") or "").strip()
                preco = item.get("price")
                if not titulo or not isinstance(preco, (int, float)):
                    continue
                if item.get("condition") != "new" or not (minimo <= preco <= maximo):
                    continue
                if LIXO.search(titulo):
                    continue
                chave = assinatura(titulo)
                if chave in vistos:
                    continue
                vistos.add(chave)
                bons.append(item)
            bons.sort(key=lambda i: (i.get("official_store_id") is None,
                                     -int(i.get("sold_quantity") or 0)))
            for item in bons[:por_termo]:
                titulo = item["title"][:90]
                base = re.sub(r"[^A-Z0-9]+", "_",
                              sem_acento(f"{categoria}_{titulo}").upper())[:38].strip("_")
                sku, n = base, 2
                while sku in usados:
                    sku, n = f"{base}_{n}", n + 1
                usados.add(sku)
                linhas.append({"sku": sku, "nome": titulo,
                               "url": item.get("permalink", ""), "fonte": "mercadolivre",
                               "categoria": categoria, "faixa": faixa, "ativo": "sim"})
                print(f"    R$ {item['price']:>8.2f}  {titulo[:56]}")
            if not bons:
                print(f"    (nada aproveitavel para '{termo}')")
            time.sleep(0.4)
        print()

    campos = ["sku", "nome", "url", "fonte", "categoria", "faixa", "ativo"]
    with open(destino, "w", encoding="utf-8", newline="") as f:
        f.write("# Gerado pelo comando 'catalogo' com dados reais do Mercado Livre.\n")
        f.write("# REVISE A MAO: apague o que voce nao recomendaria de verdade.\n")
        escritor = csv.DictWriter(f, fieldnames=campos)
        escritor.writeheader()
        escritor.writerows(linhas)

    print(f"{len(linhas)} produtos escritos em catalogo.csv")
    print("\nAgora abra catalogo.csv no GitHub, clique no lapis e APAGUE as linhas")
    print("de produtos que voce nao recomendaria. O que sobrar e o seu catalogo.")
    return 0


# ======================================================================
# DEMONSTRACAO
# ======================================================================

PERFIS_DEMO = {
    "DEMO_LIVRO":   (89.90, 0.06, 0.00, "Livro tecnico de exemplo", "livros", "baixa"),
    "DEMO_TECLADO": (429.00, 0.09, 0.22, "Teclado mecanico de exemplo", "perifericos", "media"),
    "DEMO_MONITOR": (1899.00, 0.05, 0.00, "Monitor 27 de exemplo", "monitores", "alta"),
    "DEMO_CADEIRA": (1249.00, 0.11, 0.18, "Cadeira ergonomica de exemplo", "ergonomia", "alta"),
}


def cmd_demonstracao() -> int:
    random.seed(42)
    cfg = carregar_config()
    destino = RAIZ / "dados" / "demonstracao.db"
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino.unlink()

    produtos = [Produto(sku, dados[3], f"https://exemplo.com.br/p/{sku.lower()}",
                        "generico", dados[4], dados[5], True)
                for sku, dados in PERFIS_DEMO.items()]

    with conectar(destino) as con:
        total = 0
        for sku, (base, amplitude, queda, *_) in PERFIS_DEMO.items():
            fim, por_dia, dias = agora(), 12, 90
            n = dias * por_dia
            leituras = []
            for i in range(n):
                momento = fim - timedelta(hours=(n - i) * (24 / por_dia))
                onda = math.sin(i / (por_dia * 11.0) * math.tau) * amplitude
                preco = base * (1 + onda + random.uniform(-0.012, 0.012))
                if queda and i > n - por_dia * 2:
                    preco = base * (1 - queda) * (1 + random.uniform(-0.004, 0.004))
                leituras.append(Leitura(sku, round(preco, 2), True, momento, "demonstracao"))
            total += gravar_leituras(con, leituras)
        print(f"{total} leituras simuladas.\n")

        sinais = varrer(con, produtos, cfg)
        print(f"O detector encontrou {len(sinais)} oferta(s) que passariam nas regras:")
        for s in sinais:
            selo = " [MINIMA HISTORICA]" if s.e_minima_historica else ""
            print(f"  - {s.nome}{selo}")
            print(f"      agora R$ {s.preco_atual:.2f} | media 30d R$ {s.media_30d:.2f}"
                  f" | queda {s.queda_pct:.1%} | minima 90d R$ {s.minima_90d:.2f}")
        if not sinais:
            print("  (nenhuma)")

        paginas = gerar(con, produtos, cfg, RAIZ / "site-demonstracao")
        print(f"\n{paginas} paginas geradas em site-demonstracao/")
    return 0


# ======================================================================
# COMANDOS DO DIA A DIA
# ======================================================================

def cmd_coletar() -> int:
    cfg = carregar_config()
    produtos = [p for p in carregar_catalogo() if p.ativo]
    if not produtos:
        print("Catalogo vazio. Rode o comando 'catalogo' primeiro.")
        return 1
    sessao = requests.Session()
    leituras, falhas = [], []
    for i, produto in enumerate(produtos, 1):
        leitura = buscar_preco(produto.sku, produto.url, cfg, sessao)
        if leitura:
            leituras.append(leitura)
            print(f"  [{i}/{len(produtos)}] {produto.sku}: R$ {leitura.preco:.2f}")
        else:
            falhas.append(produto.sku)
            print(f"  [{i}/{len(produtos)}] {produto.sku}: nao foi possivel ler")
    with conectar() as con:
        gravadas = gravar_leituras(con, leituras)
        limpar_antigas(con)
    print(f"\n{gravadas} leituras gravadas, {len(falhas)} falhas.")
    return 0


def cmd_detectar() -> int:
    cfg = carregar_config()
    produtos = carregar_catalogo()
    with conectar() as con:
        sinais = varrer(con, produtos, cfg)
        if not sinais:
            print("Nenhuma queda passou nas regras. Isso e normal na maioria das rodadas.")
            return 0
        for sinal in sinais:
            try:
                enviar_candidato(con, sinal)
                print(f"  candidato enviado: {sinal.sku} ({sinal.queda_pct:.0%} abaixo)")
            except TelegramErro as erro:
                print(f"  falhou ao enviar {sinal.sku}: {erro}")
    print(f"\n{len(sinais)} candidatos aguardando a sua aprovacao no Telegram.")
    return 0


def cmd_aprovacoes() -> int:
    cfg = carregar_config()
    catalogo = {p.sku: p for p in carregar_catalogo()}
    with conectar() as con:
        c = processar_aprovacoes(con, catalogo, cfg)
    print(f"publicados={c['publicados']} descartados={c['descartados']} "
          f"comentarios={c['comentarios']}")
    return 0


def cmd_site() -> int:
    cfg = carregar_config()
    with conectar() as con:
        total = gerar(con, carregar_catalogo(), cfg)
    print(f"Site gerado em site/ ({total} paginas de produto)")
    return 0


def cmd_adicionar_varias(bruto: str, categoria: str, faixa: str) -> int:
    """Adiciona varias URLs de uma vez.

    Cole quantas quiser separadas por espaco, virgula ou quebra de linha.
    Funciona com qualquer loja - nao depende de API nenhuma. E o caminho
    para montar o catalogo quando a API do Mercado Livre nao coopera.
    """
    urls = [u for u in re.split(r"[\s,;]+", bruto.strip()) if u.startswith("http")]
    if not urls:
        print("Nenhuma URL valida encontrada. Cole os links comecando com http.")
        return 2

    print(f"{len(urls)} URL(s) para processar.\n")
    ok, falhou = 0, []
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] ", end="")
        if cmd_adicionar(url, categoria, faixa, silencioso=False) == 0:
            ok += 1
        else:
            falhou.append(url)
        print()

    print(f"\n{ok} adicionado(s), {len(falhou)} falha(s).")
    if falhou:
        print("\nNao consegui ler estes (a loja monta o preco por JavaScript):")
        for url in falhou:
            print(f"  {url}")
        print("\nProcure os mesmos produtos em outra loja.")
    return 0


def cmd_adicionar(url: str, categoria: str, faixa: str, silencioso: bool = False) -> int:
    cfg = carregar_config()
    url = limpar(url)
    leitura, motivo = buscar_preco_detalhado("NOVO", url, cfg)
    if leitura is None:
        print(f"falhou: {url[:66]}")
        print(f"        {MOTIVOS.get(motivo, motivo)}")
        return 1

    titulo = ""
    try:
        pagina = requests.get(url, timeout=20,
                              headers={"User-Agent": cfg["coleta"]["user_agent"]}).text
        sopa = BeautifulSoup(pagina, "html.parser")
        for script in sopa.find_all("script", type="application/ld+json"):
            try:
                dados = json.loads(script.string or script.get_text() or "")
            except (json.JSONDecodeError, TypeError):
                continue
            for no in _achatar(dados):
                tipos = no.get("@type", "")
                tipos = tipos if isinstance(tipos, list) else [tipos]
                if any(str(t).lower() == "product" for t in tipos) and no.get("name"):
                    titulo = str(no["name"]).strip()[:90]
                    break
            if titulo:
                break
        if not titulo:
            marca = sopa.find("title")
            if marca:
                titulo = re.split(r"\s+[|–—-]\s+",
                                  marca.get_text().strip())[0][:90]
    except Exception:
        pass
    if not titulo:
        titulo = url.rsplit("/", 1)[-1][:90] or "Produto sem nome"

    existentes = {p.sku for p in carregar_catalogo()}
    base = re.sub(r"[^A-Z0-9]+", "_",
                  sem_acento(f"{categoria}_{titulo}").upper())[:38].strip("_")
    sku, n = base, 2
    while sku in existentes:
        sku, n = f"{base}_{n}", n + 1

    fonte = "mercadolivre" if "mercadoli" in url else "generico"
    with open(RAIZ / "catalogo.csv", "a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow([sku, titulo, url, fonte, categoria, faixa, "sim"])
    print(f"Adicionado: {sku}\n  {titulo}\n  R$ {leitura.preco:.2f} ({leitura.origem})")
    return 0


def cmd_fila() -> int:
    """Processa fila.txt - a lista de links que a pagina adicionar.html gravou.

    Cada linha tem o formato:  categoria|faixa|url

    Nao existe limite de quantidade: o arquivo pode ter 5 ou 5000 linhas.
    Ao terminar, a fila e esvaziada para nao reprocessar.
    """
    arquivo = RAIZ / "fila.txt"
    if not arquivo.exists():
        print("Nao ha fila para processar (fila.txt nao existe).")
        return 0

    linhas = [l.strip() for l in arquivo.read_text(encoding="utf-8").splitlines()
              if l.strip() and not l.strip().startswith("#")]
    if not linhas:
        print("A fila esta vazia.")
        return 0

    print(f"{len(linhas)} link(s) na fila.\n")
    cfg = carregar_config()
    ok, por_motivo = 0, {}
    for i, linha in enumerate(linhas, 1):
        partes = linha.split("|", 2)
        if len(partes) == 3:
            categoria, faixa, url = (p.strip() for p in partes)
        else:
            categoria, faixa, url = "geral", "media", linha
        if not url.startswith("http"):
            continue
        print(f"[{i}/{len(linhas)}] ", end="")
        leitura, motivo = buscar_preco_detalhado("NOVO", limpar(url), cfg)
        if leitura is not None:
            cmd_adicionar(url, categoria or "geral", faixa or "media")
            ok += 1
        else:
            dominio = urlparse(url).netloc.replace("www.", "")
            por_motivo.setdefault((dominio, motivo), []).append(url)
            print(f"falhou: {dominio} - {MOTIVOS.get(motivo, motivo)}")
        print()

    arquivo.write_text("# fila processada em "
                       f"{agora():%d/%m/%Y %H:%M} UTC\n", encoding="utf-8")

    total_falhas = sum(len(v) for v in por_motivo.values())
    print(f"\n{'='*60}")
    print(f"{ok} adicionado(s), {total_falhas} falha(s).")

    if por_motivo:
        print("\nPOR LOJA E MOTIVO:\n")
        for (dominio, motivo), urls in sorted(por_motivo.items(),
                                              key=lambda x: -len(x[1])):
            print(f"  {len(urls):>3} x  {dominio}")
            print(f"         {MOTIVOS.get(motivo, motivo)}")
            if motivo in ("robots", "bloqueado"):
                print("         -> Esta loja nao quer ser lida por robo. Respeite:")
                print("            monitore os mesmos produtos onde voce e afiliado.")
            elif motivo == "sem_preco":
                print("         -> Procure o mesmo produto no Mercado Livre ou na")
                print("            Amazon, que publicam o preco de forma legivel.")
            elif motivo == "nao_existe":
                print("         -> Confira se copiou o link inteiro.")
            print()

        print("LEMBRETE: so vale a pena monitorar loja da qual voce e afiliado.")
        print("Link de loja sem programa de afiliado nao gera comissao nenhuma.")
    return 0


def cmd_testar(url: str) -> int:
    cfg = carregar_config()
    print(f"robots.txt permite: {robots_permite(url, cfg['coleta']['user_agent'])}")
    leitura = buscar_preco("TESTE", url, cfg)
    if leitura is None:
        print("Nao consegui ler o preco desta pagina.")
        return 1
    print(f"preco={leitura.preco:.2f} disponivel={leitura.disponivel} "
          f"origem={leitura.origem}")
    return 0


def cmd_chatid() -> int:
    print("Mande qualquer mensagem para o SEU bot antes de rodar isto.\n")
    descobrir_chat_id()
    return 0


def cmd_diagnostico() -> int:
    linhas: list[str] = []
    faltando = 0

    def marcar(ok: bool, texto: str, dica: str = "") -> None:
        nonlocal faltando
        linhas.append(f"{'[ok]  ' if ok else '[X]   '}{texto}")
        if not ok:
            faltando += 1
            if dica:
                linhas.append(f"        -> {dica}")

    token = env("TELEGRAM_TOKEN")
    marcar(bool(token), "TELEGRAM_TOKEN configurado",
           "Crie o bot no @BotFather e salve o token em Settings > Secrets > Actions")
    marcar(bool(env("TELEGRAM_ADMIN_CHAT_ID")), "TELEGRAM_ADMIN_CHAT_ID configurado",
           "Abra api.telegram.org/botSEUTOKEN/getUpdates?offset=-1 e pegue o chat id")
    canal = env("TELEGRAM_CANAL")
    marcar(bool(canal), f"TELEGRAM_CANAL configurado ({canal or 'vazio'})", "Use @baixouti")

    if token:
        try:
            bot = verificar_bot()
            marcar(True, f"Token valido - bot @{bot.get('username')}")
        except Exception as erro:
            marcar(False, f"Token recusado pelo Telegram: {erro}",
                   "Confira se copiou o token inteiro, sem espacos")
    if token and canal:
        try:
            info = verificar_canal(canal)
            marcar(True, f"Canal encontrado - {info.get('title')}")
        except Exception as erro:
            marcar(False, f"Nao consegui ler o canal: {erro}",
                   "O bot precisa ser administrador do canal, com permissao de publicar")

    try:
        produtos = carregar_catalogo()
        ativos = [p for p in produtos if p.ativo]
        marcar(bool(ativos), f"Catalogo com {len(ativos)} produtos ativos "
                             f"(de {len(produtos)})", "Rode o comando 'catalogo'")
    except Exception as erro:
        marcar(False, f"Catalogo com problema: {erro}")
        ativos = []

    with conectar() as con:
        leituras = con.execute("SELECT COUNT(*) c FROM leituras").fetchone()["c"]
        pend = con.execute(
            "SELECT COUNT(*) c FROM alertas WHERE estado='pendente'").fetchone()["c"]
    if ativos:
        por_produto = leituras / max(len(ativos), 1)
        marcar(por_produto >= 60,
               f"Historico: {leituras} leituras (~{por_produto:.0f} por produto)",
               f"Faltam cerca de {max(0, int((60 - por_produto) / 12))} dias de coleta. "
               f"Nao ha nada a fazer alem de esperar.")
    if pend:
        linhas.append(f"[!]   {pend} oferta(s) esperando aprovacao no Telegram")
    cid, seg = app_id_ml(), env("ML_CLIENT_SECRET")
    if env("ML_ACCESS_TOKEN"):
        linhas.append("[!]   Existe um secret ML_ACCESS_TOKEN. Ele nao e mais usado "
                      "e pode ser apagado.")
    if cid and seg:
        if "*" in cid:
            faltando += 1
            linhas.append("[X]   O App ID esta mascarado (***)")
            linhas.append("        -> Apague o secret ML_CLIENT_ID e ponha o numero "
                          "no config.yaml, bloco 'mercadolivre'. Ele nao e segredo.")
        else:
            linhas.append(f"[ok]  App ID do Mercado Livre: {cid}")
        linhas.append(f"[ok]  ML_CLIENT_SECRET chegou ({len(seg)} caracteres)")
        if not env("ML_REFRESH_KEY"):
            faltando += 1
            linhas.append("[X]   Falta o secret ML_REFRESH_KEY")
            linhas.append("        -> Invente uma senha longa e salve com esse nome. "
                          "Ela protege a chave do Mercado Livre no repositorio.")
        elif not COFRE_ML.exists():
            faltando += 1
            linhas.append("[X]   Mercado Livre ainda nao autorizado")
            linhas.append("        -> Rode a acao 'ml_autorizar' e siga os 5 passos")
        else:
            # De proposito NAO renova aqui: o Mercado Livre invalida a chave
            # anterior a cada renovacao. Um diagnostico que gasta a chave que
            # esta diagnosticando quebra exatamente o que veio verificar.
            _, motivo = ler_refresh()
            if motivo == "":
                linhas.append("[ok]  Mercado Livre autorizado "
                              "(chave guardada em dados/ml_token.enc)")
            elif motivo == "senha_errada":
                faltando += 1
                linhas.append("[X]   O cofre nao abre com o ML_REFRESH_KEY atual")
                linhas.append("        -> Refaca ml_autorizar + ml_salvar_codigo")
    else:
        ausentes = [n for n, v in (("app_id no config.yaml", cid),
                                   ("ML_CLIENT_SECRET", seg)) if not v]
        if ausentes:
            linhas.append(f"[--]  Mercado Livre: nao chegou {' nem '.join(ausentes)}")
            linhas.append("        -> Se voce ja criou o secret, o problema e que o "
                          "painel.yml nao esta repassando. Confira se voce atualizou "
                          "o painel.yml com a versao nova.")
    linhas.append("[ok]  AMAZON_TAG configurado" if env("AMAZON_TAG")
                  else "[--]  AMAZON_TAG vazio (normal - a Amazon fica para o final)")

    relatorio = "\n".join(linhas)
    print(relatorio)
    print("\n" + ("Tudo pronto." if faltando == 0 else f"{faltando} item(ns) faltando."))
    resumo = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumo:
        with open(resumo, "a", encoding="utf-8") as f:
            f.write("## Diagnostico\n\n```\n" + relatorio + "\n```\n")
            f.write("\n**Tudo pronto.**\n" if faltando == 0
                    else f"\n**{faltando} item(ns) faltando.**\n")
    return 0


def cmd_rodada() -> int:
    print("== coleta ==");       cmd_coletar()
    print("\n== aprovacoes =="); cmd_aprovacoes()
    print("\n== deteccao ==");   cmd_detectar()
    print("\n== site ==");       cmd_site()
    return 0


AJUDA = """Baixou - comandos

  python radar.py rodada          coleta + aprovacoes + deteccao + site
  python radar.py coletar         so le os precos do catalogo
  python radar.py detectar        avalia as regras e manda candidatos
  python radar.py aprovacoes      publica o que voce aprovou no Telegram
  python radar.py site            gera o site em site/

  python radar.py catalogo        monta catalogo.csv pelo Mercado Livre
      --por-termo N               quantos produtos por termo (padrao 2)
      --anexar                    soma ao catalogo atual
  python radar.py adicionar URL [categoria] [faixa]
  python radar.py adicionar_varias "URL1 URL2 URL3" [categoria] [faixa]
  python radar.py fila            processa fila.txt (gravada pela pagina web)

  python radar.py ml_autorizar    gera o link de autorizacao do Mercado Livre
  python radar.py ml_codigo TG-x  guarda o codigo devolvido pela autorizacao
  python radar.py testar URL      testa a leitura de preco de uma pagina

  python radar.py diagnostico     confere se tudo esta configurado
  python radar.py chatid          descobre o seu TELEGRAM_ADMIN_CHAT_ID
  python radar.py demonstracao    gera dados falsos para ver funcionando
"""


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "ajuda"):
        print(AJUDA)
        return 0
    comando, resto = argv[0], argv[1:]

    if comando == "catalogo":
        ap = argparse.ArgumentParser(prog="radar.py catalogo")
        ap.add_argument("--por-termo", type=int, default=2)
        ap.add_argument("--anexar", action="store_true")
        args = ap.parse_args(resto)
        return cmd_catalogo(args.por_termo, args.anexar)
    if comando == "ml_codigo":
        if not resto:
            print("uso: python radar.py ml_codigo <codigo TG-...>")
            return 2
        return cmd_ml_codigo(resto[0])
    if comando in ("adicionar", "adicionar_varias"):
        if not resto:
            print(f"uso: python radar.py {comando} <url(s)> [categoria] [faixa]")
            return 2
        categoria = resto[1] if len(resto) > 1 else "geral"
        faixa = resto[2] if len(resto) > 2 else "media"
        if comando == "adicionar_varias":
            return cmd_adicionar_varias(resto[0], categoria, faixa)
        return cmd_adicionar(resto[0], categoria, faixa)
    if comando == "testar":
        if not resto:
            print("uso: python radar.py testar <url>")
            return 2
        return cmd_testar(resto[0])

    acoes = {
        "coletar": cmd_coletar, "detectar": cmd_detectar, "aprovacoes": cmd_aprovacoes,
        "site": cmd_site, "rodada": cmd_rodada, "diagnostico": cmd_diagnostico,
        "chatid": cmd_chatid, "demonstracao": cmd_demonstracao, "fila": cmd_fila,
        "ml_autorizar": cmd_ml_autorizar, "ml_testar": cmd_ml_testar,
    }
    if comando not in acoes:
        print(f"comando desconhecido: {comando}\n")
        print(AJUDA)
        return 2
    return acoes[comando]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
