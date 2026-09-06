"""Linha de comando do Radar.

  python -m radar coletar      # le o preco de todo o catalogo
  python -m radar detectar     # avalia e manda candidatos para aprovacao
  python -m radar aprovacoes   # publica o que voce aprovou no Telegram
  python -m radar site         # gera o site estatico
  python -m radar rodada       # coletar + detectar + aprovacoes + site
  python -m radar testar URL   # testa a leitura de preco de uma URL
  python -m radar chatid       # descobre o seu TELEGRAM_ADMIN_CHAT_ID
  python -m radar resumo       # estado atual do banco
"""
from __future__ import annotations

import sys

import requests

from .config import carregar_catalogo, carregar_config
from .detector import varrer
from .historico import conectar, gravar_leituras, limpar_antigas, publicados
from .site import gerar


def _catalogo_dict(produtos):
    return {p.sku: p for p in produtos}


def cmd_coletar() -> int:
    cfg = carregar_config()
    produtos = [p for p in carregar_catalogo() if p.ativo]
    from .fontes import buscar_preco

    sessao = requests.Session()
    leituras, falhas = [], []
    for i, produto in enumerate(produtos, 1):
        leitura = buscar_preco(produto.sku, produto.url, cfg, sessao)
        if leitura:
            leituras.append(leitura)
            print(f"  [{i}/{len(produtos)}] {produto.sku}: R$ {leitura.preco:.2f} "
                  f"({leitura.origem}{'' if leitura.disponivel else ', indisponivel'})")
        else:
            falhas.append(produto.sku)
            print(f"  [{i}/{len(produtos)}] {produto.sku}: nao foi possivel ler")

    with conectar() as con:
        gravadas = gravar_leituras(con, leituras)
        limpar_antigas(con)
    print(f"\n{gravadas} leituras gravadas, {len(falhas)} falhas.")
    if falhas:
        print("Falhas: " + ", ".join(falhas))
    return 0


def cmd_detectar() -> int:
    cfg = carregar_config()
    produtos = carregar_catalogo()
    from .telegram import TelegramErro, enviar_candidato

    with conectar() as con:
        sinais = varrer(con, produtos, cfg)
        if not sinais:
            print("Nenhuma queda passou nas regras. Isso e normal na maioria das rodadas.")
            return 0
        for sinal in sinais:
            try:
                enviar_candidato(con, sinal)
                print(f"  candidato enviado: {sinal.sku} "
                      f"(R$ {sinal.preco_atual:.2f}, {sinal.queda_pct:.0%} abaixo da media)")
            except TelegramErro as erro:
                print(f"  falhou ao enviar {sinal.sku}: {erro}")
    print(f"\n{len(sinais)} candidatos aguardando a sua aprovacao no Telegram.")
    return 0


def cmd_aprovacoes() -> int:
    cfg = carregar_config()
    catalogo = _catalogo_dict(carregar_catalogo())
    from .telegram import processar_aprovacoes

    with conectar() as con:
        contagem = processar_aprovacoes(con, catalogo, cfg)
    print(f"publicados={contagem['publicados']} "
          f"descartados={contagem['descartados']} "
          f"comentarios={contagem['comentarios']}")
    return 0


def cmd_site() -> int:
    cfg = carregar_config()
    produtos = carregar_catalogo()
    with conectar() as con:
        total = gerar(con, produtos, cfg)
    print(f"{total} paginas geradas em site/")
    return 0


def cmd_testar(url: str) -> int:
    cfg = carregar_config()
    from .fontes import buscar_preco, robots_permite

    print(f"robots.txt permite: {robots_permite(url, cfg['coleta']['user_agent'])}")
    leitura = buscar_preco("TESTE", url, cfg)
    if leitura is None:
        print("Nao consegui ler o preco desta pagina.")
        print("Ela provavelmente monta o preco por JavaScript. Use outra loja para "
              "este produto ou pegue o link do Mercado Livre, que tem API.")
        return 1
    print(f"preco={leitura.preco:.2f} disponivel={leitura.disponivel} "
          f"origem={leitura.origem}")
    return 0


def cmd_chatid() -> int:
    from .telegram import descobrir_chat_id
    print("Mande qualquer mensagem para o seu bot antes de rodar isto.\n")
    descobrir_chat_id()
    return 0


def cmd_resumo() -> int:
    produtos = carregar_catalogo()
    with conectar() as con:
        total = con.execute("SELECT COUNT(*) c FROM leituras").fetchone()["c"]
        skus = con.execute("SELECT COUNT(DISTINCT sku) c FROM leituras").fetchone()["c"]
        pend = con.execute(
            "SELECT COUNT(*) c FROM alertas WHERE estado='pendente'").fetchone()["c"]
        pub = len(publicados(con, 10_000))
    ativos = sum(1 for p in produtos if p.ativo)
    print(f"catalogo: {len(produtos)} produtos ({ativos} ativos)")
    print(f"leituras: {total} de {skus} SKUs")
    print(f"alertas:  {pend} pendentes, {pub} publicados")
    return 0


def cmd_rodada() -> int:
    print("== coleta ==");      cmd_coletar()
    print("\n== aprovacoes =="); cmd_aprovacoes()
    print("\n== deteccao ==");   cmd_detectar()
    print("\n== site ==");       cmd_site()
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "ajuda"):
        print(__doc__)
        return 0
    comando, *resto = argv
    acoes = {
        "coletar": cmd_coletar, "detectar": cmd_detectar, "aprovacoes": cmd_aprovacoes,
        "site": cmd_site, "chatid": cmd_chatid, "resumo": cmd_resumo, "rodada": cmd_rodada,
    }
    if comando == "testar":
        if not resto:
            print("uso: python -m radar testar <url>")
            return 2
        return cmd_testar(resto[0])
    if comando not in acoes:
        print(f"comando desconhecido: {comando}\n")
        print(__doc__)
        return 2
    return acoes[comando]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
