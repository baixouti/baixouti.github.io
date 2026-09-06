"""Testes rapidos das partes que mais quebram. Rode depois de mexer no codigo:

    python scripts/testes.py
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.config import Produto, carregar_config
from radar.detector import avaliar
from radar.fontes import _normalizar, de_jsonld, de_microdados, id_mercadolivre
from radar.historico import agora, conectar, gravar_leituras, registrar_alerta
from radar.links import afiliar, limpar
from radar.modelos import Leitura

ok = 0


def checar(nome: str, condicao: bool) -> None:
    global ok
    assert condicao, f"FALHOU: {nome}"
    ok += 1
    print(f"  ok  {nome}")


print("precos")
for entrada, esperado in [("R$ 1.299,90", 1299.90), ("1,299.90", 1299.90),
                          ("R$ 89,90", 89.90), (429, 429.0), ("", None),
                          ("R$ 0,00", None), (None, None)]:
    checar(f"normaliza {entrada!r}", _normalizar(entrada) == esperado)

print("\nextracao de html")
jsonld = ('<script type="application/ld+json">{"@graph":[{"@type":"Product",'
          '"offers":{"@type":"Offer","price":"329.90",'
          '"availability":"https://schema.org/InStock"}}]}</script>')
checar("json-ld dentro de @graph", de_jsonld(jsonld) == (329.90, True))
checar("json-ld esgotado", de_jsonld(jsonld.replace("InStock", "OutOfStock")) == (329.90, False))
checar("microdados com virgula",
       de_microdados('<meta itemprop="price" content="1.899,00">') == (1899.00, True))
checar("og:price:amount",
       de_microdados('<meta property="og:price:amount" content="59.90">')[0] == 59.90)
checar("html sem preco", de_jsonld("<html></html>")[0] is None)

print("\nlinks")
sujo = "https://www.amazon.com.br/dp/B00XYZ?ref=abc&utm_source=x&th=1"
checar("remove parametros de rastreio", limpar(sujo) == "https://www.amazon.com.br/dp/B00XYZ")
os.environ["AMAZON_TAG"] = "radar-20"
checar("aplica a tag da amazon", afiliar(sujo).endswith("?tag=radar-20"))
checar("id do mercado livre",
       id_mercadolivre("https://produto.mercadolivre.com.br/MLB-1234567890-x") == "MLB1234567890")

print("\ndetector")
cfg = carregar_config()
banco = Path(__file__).resolve().parent.parent / "dados" / "teste.db"
if banco.exists():
    banco.unlink()
produto = Produto("T1", "Produto de teste", "https://exemplo.com/p", "generico", "teste", "media", True)

with conectar(banco) as con:
    fim = agora()

    # historico curto demais -> nao alerta
    gravar_leituras(con, [Leitura("T1", 100.0, True, fim - timedelta(hours=i))
                          for i in range(10)])
    checar("ignora historico curto", avaliar(con, produto, cfg) is None)

    # 30 dias estaveis em 100 e uma queda para 80 -> alerta
    gravar_leituras(con, [Leitura("T1", 100.0, True, fim - timedelta(hours=i))
                          for i in range(11, 300)])
    gravar_leituras(con, [Leitura("T1", 80.0, True, fim)])
    sinal = avaliar(con, produto, cfg)
    checar("detecta queda real", sinal is not None and sinal.preco_atual == 80.0)
    checar("calcula a queda", sinal is not None and 0.19 < sinal.queda_pct < 0.21)

    # queda de 5% nao passa
    gravar_leituras(con, [Leitura("T1", 95.0, True, fim + timedelta(minutes=1))])
    checar("ignora queda pequena", avaliar(con, produto, cfg) is None)

    # cooldown: com alerta recente registrado, nao repete
    gravar_leituras(con, [Leitura("T1", 80.0, True, fim + timedelta(minutes=2))])
    checar("detecta de novo antes do cooldown", avaliar(con, produto, cfg) is not None)
    registrar_alerta(con, "T1", 80.0, 100.0, 0.20)
    checar("respeita o cooldown", avaliar(con, produto, cfg) is None)

banco.unlink(missing_ok=True)
print(f"\n{ok} verificacoes passaram.")
