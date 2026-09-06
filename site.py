"""Gera o site estatico: home + uma pagina por produto com grafico de preco.

E este site que sustenta o projeto no Google. A politica de spam do Google
protege explicitamente quem oferece 'informacao de preco, comparacoes e
testes' - e um historico real de preco e exatamente isso.

Sem framework, sem build. Cospe HTML puro em site/.
"""
from __future__ import annotations

import html
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import RAIZ, Produto
from .historico import serie
from .links import afiliar, encurtar

SAIDA = RAIZ / "site"

CSS = """
:root{--paper:#F1F3F2;--surface:#fff;--surface-2:#E6EBEA;--ink:#0E1A1D;--ink-2:#3D5155;
--ink-3:#6E8286;--line:#D3DAD9;--petrol:#124F5C;--rust:#9A3412;--ok:#186B4E}
@media (prefers-color-scheme:dark){:root{--paper:#0B1416;--surface:#121F22;--surface-2:#18292D;
--ink:#E7EDEC;--ink-2:#AABCBC;--ink-3:#7E9497;--line:#22363A;--petrol:#66C6D6;--rust:#EE9264;--ok:#5CC694}}
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
font-size:14px;color:var(--ink-3)}
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
    import json as _json

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
        + f'<script type="application/ld+json">{_json.dumps(dados, ensure_ascii=False)}</script>'
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
    home = (
        CABECA.format(titulo=cfg_site["titulo"], descricao=cfg_site["subtitulo"], css=CSS)
        + f'<header><h1>{html.escape(cfg_site["titulo"])}</h1>'
          f'<p>{html.escape(cfg_site["subtitulo"])}</p></header>'
        + f'<div class="grid">{"".join(cartoes[: int(cfg_site["destaques"])])}</div>'
        + f'<footer>{gerados} produtos monitorados. Atualizado em '
          f'{datetime.now(timezone.utc):%d/%m/%Y as %H:%M} UTC. '
          f'Paginas com links de afiliado.</footer>'
        + '</div></body></html>'
    )
    (saida / "index.html").write_text(home, encoding="utf-8")
    return gerados
