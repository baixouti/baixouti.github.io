"""Telegram: fila de aprovacao e publicacao no canal.

O bot NUNCA publica sozinho. O fluxo e:
  1. detectar  -> manda o candidato para o SEU chat privado com dois botoes
  2. (opcional) voce responde a mensagem com uma frase sua sobre a oferta
  3. aprovacoes -> le os botoes apertados e publica no canal o que voce aprovou

Funciona em cron (GitHub Actions) porque o offset do getUpdates fica no banco.
"""
from __future__ import annotations

import html
import sqlite3
from typing import Any

import requests

from .config import Produto, env
from .historico import (alerta, alerta_por_mensagem, decidir_alerta, gravar_comentario,
                        gravar_controle, ler_controle, registrar_alerta, vincular_mensagem)
from .links import afiliar, encurtar
from .modelos import Sinal

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

        # (b) voce apertou um botao
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


def descobrir_chat_id() -> None:
    """Utilitario: mande qualquer mensagem para o seu bot e rode isto."""
    for upd in _chamar("getUpdates") or []:
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat:
            print(f"chat_id={chat.get('id')}  tipo={chat.get('type')}  "
                  f"nome={chat.get('title') or chat.get('first_name')}")
