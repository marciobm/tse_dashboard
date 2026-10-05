import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "tse.db"

# Produção 2026 / Presidente / Brasil
TSE_URL = (
    "https://resultados.tse.jus.br/"
    "oficial/ele2026/6257/dados/br/"
    "br-c0001-e006257-u.json"
)

POLL_SECONDS = 60
HTTP_TIMEOUT = 30.0

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
stop_event = asyncio.Event()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS metadata (
        key TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE TABLE IF NOT EXISTS candidatos (
        numero TEXT PRIMARY KEY,
        candidato TEXT NOT NULL,
        nome_urna TEXT,
        partido TEXT,
        votos INTEGER NOT NULL DEFAULT 0,
        percentual REAL NOT NULL DEFAULT 0,
        situacao TEXT,
        status TEXT,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS totalizacao (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        secoes_total INTEGER,
        secoes_totalizadas INTEGER,
        percentual_secoes REAL,
        eleitorado_total INTEGER,
        eleitorado_totalizado INTEGER,
        geracao_data TEXT,
        geracao_hora TEXT,
        updated_at TEXT NOT NULL
    );
    """)
    conn.commit()
    conn.close()


def number(value):
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return value
    s = str(value).strip()
    if not s:
        return 0
    if "," in s:
        try:
            return float(s.replace(".", "").replace(",", "."))
        except ValueError:
            return 0
    try:
        return int(s.replace(".", ""))
    except ValueError:
        return 0


def extract_candidates(data):
    found = {}

    def walk(obj):
        if isinstance(obj, dict):
            cand_list = obj.get("cand")
            if isinstance(cand_list, list):
                for c in cand_list:
                    if not isinstance(c, dict):
                        continue
                    numero = str(c.get("n", "")).strip()
                    nome = c.get("nm") or c.get("nmu") or ""
                    key = numero or nome
                    if not key:
                        continue
                    found[key] = {
                        "numero": numero,
                        "candidato": nome,
                        "nome_urna": c.get("nmu", ""),
                        "partido": c.get("sgp") or c.get("sg") or "",
                        "votos": int(number(c.get("vap", 0))),
                        "percentual": float(number(c.get("pvap", 0))),
                        "situacao": c.get("e", ""),
                        "status": c.get("st", ""),
                    }
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return sorted(found.values(), key=lambda x: x["votos"], reverse=True)


def extract_totalization(data):
    result = {
        "secoes_total": None,
        "secoes_totalizadas": None,
        "percentual_secoes": None,
        "eleitorado_total": None,
        "eleitorado_totalizado": None,
        "geracao_data": None,
        "geracao_hora": None,
    }

    def walk(obj):
        if isinstance(obj, dict):
            s = obj.get("s")
            if isinstance(s, dict):
                if "ts" in s:
                    result["secoes_total"] = int(number(s["ts"]))
                if "st" in s:
                    result["secoes_totalizadas"] = int(number(s["st"]))
                if "pst" in s:
                    result["percentual_secoes"] = float(number(s["pst"]))

            e = obj.get("e")
            if isinstance(e, dict):
                if "te" in e:
                    result["eleitorado_total"] = int(number(e["te"]))
                if "est" in e:
                    result["eleitorado_totalizado"] = int(number(e["est"]))

            if "dg" in obj:
                result["geracao_data"] = obj["dg"]
            if "hg" in obj:
                result["geracao_hora"] = obj["hg"]

            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return result


def save_result(data):
    now = datetime.now(timezone.utc).isoformat()
    candidates = extract_candidates(data)
    total = extract_totalization(data)

    conn = db()
    with conn:
        conn.execute("DELETE FROM candidatos")
        conn.executemany(
            """
            INSERT INTO candidatos
            (numero, candidato, nome_urna, partido, votos, percentual,
             situacao, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    c["numero"], c["candidato"], c["nome_urna"], c["partido"],
                    c["votos"], c["percentual"], c["situacao"], c["status"], now
                )
                for c in candidates
            ],
        )

        conn.execute(
            """
            INSERT INTO totalizacao
            (id, secoes_total, secoes_totalizadas, percentual_secoes,
             eleitorado_total, eleitorado_totalizado, geracao_data,
             geracao_hora, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              secoes_total=excluded.secoes_total,
              secoes_totalizadas=excluded.secoes_totalizadas,
              percentual_secoes=excluded.percentual_secoes,
              eleitorado_total=excluded.eleitorado_total,
              eleitorado_totalizado=excluded.eleitorado_totalizado,
              geracao_data=excluded.geracao_data,
              geracao_hora=excluded.geracao_hora,
              updated_at=excluded.updated_at
            """,
            (
                total["secoes_total"], total["secoes_totalizadas"],
                total["percentual_secoes"], total["eleitorado_total"],
                total["eleitorado_totalizado"], total["geracao_data"],
                total["geracao_hora"], now
            ),
        )

        conn.execute(
            "INSERT INTO metadata(key,value) VALUES('last_update',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (now,),
        )
    conn.close()


def get_api_result():
    conn = db()
    candidates = [dict(r) for r in conn.execute(
        "SELECT numero,candidato,nome_urna,partido,votos,percentual,"
        "situacao,status,updated_at FROM candidatos ORDER BY votos DESC"
    )]
    total = conn.execute("SELECT * FROM totalizacao WHERE id=1").fetchone()
    meta = conn.execute("SELECT value FROM metadata WHERE key='last_update'").fetchone()
    conn.close()

    return {
        "fonte": "TSE",
        "eleicao": "Eleições 2026",
        "cargo": "Presidente",
        "abrangencia": "Brasil",
        "url": TSE_URL,
        "ultima_atualizacao_local": meta["value"] if meta else None,
        "totalizacao": dict(total) if total else {},
        "candidatos": candidates,
    }


async def fetch_tse():
    state_path = BASE_DIR / "http_state.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    headers = {
        "User-Agent": "TSE-Results-Monitor/1.0",
        "Accept": "application/json",
    }
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        response = await client.get(TSE_URL, headers=headers)

    if response.status_code == 304:
        return False

    response.raise_for_status()

    state["etag"] = response.headers.get("etag")
    state["last_modified"] = response.headers.get("last-modified")
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    save_result(response.json())
    return True


async def updater():
    # Primeira consulta
    try:
        changed = await fetch_tse()
        print("TSE:", "dados atualizados" if changed else "sem alteração")
    except Exception as exc:
        print("Erro na consulta inicial ao TSE:", repr(exc))

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            try:
                changed = await fetch_tse()
                print("TSE:", "dados atualizados" if changed else "sem alteração")
            except Exception as exc:
                print("Erro na consulta ao TSE:", repr(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(updater())
    yield
    stop_event.set()
    await task


app = FastAPI(title="TSE Results Dashboard", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"poll_seconds": POLL_SECONDS},
    )


@app.get("/resultados")
async def resultados():
    return get_api_result()


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/resultados/candidato/{numero}")
async def candidato(numero: str):
    conn = db()
    row = conn.execute(
        "SELECT * FROM candidatos WHERE numero=?", (numero,)
    ).fetchone()
    conn.close()
    if not row:
        return {"erro": "Candidato não encontrado"}
    return dict(row)


@app.get("/resultados/top")
async def top(limit: int = Query(default=10, ge=1, le=100)):
    conn = db()
    rows = conn.execute(
        "SELECT numero,candidato,nome_urna,partido,votos,percentual "
        "FROM candidatos ORDER BY votos DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return {"candidatos": [dict(r) for r in rows]}
