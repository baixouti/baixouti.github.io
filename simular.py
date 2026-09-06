"""Enche o banco com 90 dias de historico FALSO para voce ver o sistema
funcionando hoje, sem esperar 5 dias de coleta real.

    python scripts/simular.py          # cria dados/simulacao.db
    python scripts/simular.py --usar   # cria direto em dados/precos.db (cuidado)

Depois:
    python -m radar site
    open site/index.html
"""
from __future__ import annotations

import math
import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.config import RAIZ, carregar_config
from radar.detector import varrer
from radar.historico import agora, conectar, gravar_leituras
from radar.modelos import Leitura
from radar.config import Produto
from radar.site import gerar

random.seed(42)

PRODUTOS = [
    Produto("SIM_LIVRO_01", "Clean Architecture (edicao brasileira)",
            "https://exemplo.com.br/p/clean-architecture", "generico", "livros", "baixa", True),
    Produto("SIM_TECLADO_01", "Teclado mecanico 75% ABNT2",
            "https://exemplo.com.br/p/teclado-75", "generico", "home office", "media", True),
    Produto("SIM_MONITOR_01", "Monitor 27 polegadas QHD IPS",
            "https://exemplo.com.br/p/monitor-27-qhd", "generico", "home office", "alta", True),
    Produto("SIM_CADEIRA_01", "Cadeira ergonomica com apoio lombar",
            "https://exemplo.com.br/p/cadeira-ergonomica", "generico", "home office", "alta", True),
]

# base, amplitude da oscilacao, queda no final (0.0 = sem queda)
PERFIS = {
    "SIM_LIVRO_01":   (89.90,  0.06, 0.00),
    "SIM_TECLADO_01": (429.00, 0.09, 0.22),   # este vai disparar o alerta
    "SIM_MONITOR_01": (1899.00, 0.05, 0.00),
    "SIM_CADEIRA_01": (1249.00, 0.11, 0.18),  # este tambem
}


def gerar_serie(sku: str, dias: int = 90, por_dia: int = 12) -> list[Leitura]:
    base, amplitude, queda = PERFIS[sku]
    fim = agora()
    leituras: list[Leitura] = []
    total = dias * por_dia
    for i in range(total):
        momento = fim - timedelta(hours=(total - i) * (24 / por_dia))
        # oscilacao sazonal suave + ruido
        onda = math.sin(i / (por_dia * 11.0) * math.tau) * amplitude
        ruido = random.uniform(-0.012, 0.012)
        preco = base * (1 + onda + ruido)
        # a queda entra nos ultimos 2 dias
        if queda and i > total - por_dia * 2:
            preco = base * (1 - queda) * (1 + random.uniform(-0.004, 0.004))
        leituras.append(Leitura(sku=sku, preco=round(preco, 2), disponivel=True,
                                momento=momento, origem="simulacao"))
    return leituras


def main() -> int:
    usar_real = "--usar" in sys.argv
    destino = (RAIZ / "dados" / "precos.db") if usar_real else (RAIZ / "dados" / "simulacao.db")
    if destino.exists() and not usar_real:
        destino.unlink()

    cfg = carregar_config()
    with conectar(destino) as con:
        total = 0
        for produto in PRODUTOS:
            total += gravar_leituras(con, gerar_serie(produto.sku))
        print(f"{total} leituras simuladas em {destino.relative_to(RAIZ)}")

        sinais = varrer(con, PRODUTOS, cfg)
        print(f"\nO detector encontrou {len(sinais)} oferta(s) que passariam nas regras:")
        for s in sinais:
            selo = " [MINIMA HISTORICA]" if s.e_minima_historica else ""
            print(f"  - {s.nome}{selo}\n"
                  f"      agora R$ {s.preco_atual:.2f} | media 30d R$ {s.media_30d:.2f} "
                  f"| queda {s.queda_pct:.1%} | minima 90d R$ {s.minima_90d:.2f}")
        if not sinais:
            print("  (nenhuma - afrouxe queda_minima no config.yaml para testar)")

        saida = RAIZ / ("site" if usar_real else "site-simulado")
        paginas = gerar(con, PRODUTOS, cfg, saida)
        print(f"\n{paginas} paginas geradas em {saida.relative_to(RAIZ)}/")
        print(f"Abra {saida.relative_to(RAIZ)}/index.html no navegador.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
