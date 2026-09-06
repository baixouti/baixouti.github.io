"""Banco de precos em SQLite. E o arquivo mais importante do projeto:
sem historico nao existe 'queda de preco', existe chute."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import RAIZ
from .modelos import Leitura

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
