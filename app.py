from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st


# ============================================================
# CONFIGURAÇÃO
# ============================================================
st.set_page_config(
    page_title="Scanner Cripto 2MV",
    page_icon="₿",
    layout="wide",
    initial_sidebar_state="collapsed",
)

BASE_DIR = Path(__file__).resolve().parent
TOP100_FILE = BASE_DIR / "top100_mercado.csv"

BYBIT_BASE_URLS = [
    "https://api.bybit.com",
    "https://api.bytick.com",
]

OKX_BASE_URLS = [
    "https://www.okx.com",
]

KUCOIN_BASE_URLS = [
    "https://api-futures.kucoin.com",
]

DURACAO_MS = {
    "W": 7 * 24 * 60 * 60 * 1000,
    "D": 24 * 60 * 60 * 1000,
    "120": 120 * 60 * 1000,
}

CORES = {
    "verde": "#16a34a",
    "vermelho": "#dc2626",
    "branco": "#ffffff",
}

ICONE_ESTADO = {
    "verde": "🟢",
    "vermelho": "🔴",
    "branco": "⚪",
    "": "—",
}

SITUACAO_ORDEM = {
    "COMPRA": 0,
    "ALERTA COMPRA": 1,
    "NEUTRO": 2,
    "ALERTA VENDA": 3,
    "VENDA": 4,
}


# ============================================================
# VISUAL
# ============================================================
st.markdown(
    """
    <style>
        .block-container {
            max-width: 1500px;
            padding-top: 3.75rem !important;
            padding-bottom: 2.25rem;
        }

        .crypto-title {
            font-size: 2.0rem;
            font-weight: 750;
            line-height: 1.20;
            margin: 0 0 .22rem 0;
        }

        .crypto-sub {
            opacity: .74;
            margin: 0 0 .85rem 0;
            font-size: .92rem;
        }

        /* Ticker clicável, no mesmo estilo do scanner B3. */
        button[kind="tertiary"] {
            color: #2f81f7 !important;
            text-decoration: none !important;
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            padding-left: .15rem !important;
            padding-right: .15rem !important;
            cursor: pointer !important;
        }

        button[kind="tertiary"]:hover {
            color: #58a6ff !important;
            text-decoration: none !important;
            background: transparent !important;
        }

        button[kind="tertiary"] p {
            text-decoration: none !important;
        }

        /* Cabeçalhos ordenáveis da tabela. */
        button[kind="secondary"] {
            cursor: pointer !important;
        }

        [data-testid="stPlotlyChart"] {
            width: 100% !important;
        }

        @media (max-width: 760px) {
            .block-container {
                padding-left: .58rem !important;
                padding-right: .58rem !important;
                padding-top: 2.75rem !important;
            }

            .crypto-title {
                font-size: 1.50rem;
            }

            .crypto-sub {
                font-size: .82rem;
            }

            /* Mantém as colunas compactas em celular, evitando quebra exagerada. */
            div[data-testid="stHorizontalBlock"] {
                gap: .30rem !important;
            }

            div[data-testid="column"] {
                min-width: 0 !important;
            }

            button p {
                font-size: .78rem !important;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# HTTP — MÚLTIPLAS FONTES
# ============================================================
def _request_json(
    bases: list[str],
    path: str,
    params: dict,
    timeout: int = 8,
    tentativas: int = 3,
) -> dict:
    ultimo_erro = None

    for tentativa in range(tentativas):
        for base in bases:
            try:
                r = requests.get(
                    base + path,
                    params=params,
                    timeout=timeout,
                    headers={
                        "User-Agent": "Scanner-2MV-Cripto/9.0",
                        "Accept": "application/json",
                    },
                )

                if r.status_code == 429:
                    ultimo_erro = RuntimeError("limite temporário da API")
                    continue

                r.raise_for_status()
                return r.json()

            except Exception as exc:
                ultimo_erro = exc

        if tentativa < tentativas - 1:
            time.sleep(0.35 * (tentativa + 1))

    raise RuntimeError(str(ultimo_erro))


def _get_bybit_json(path: str, params: dict) -> dict:
    data = _request_json(
        BYBIT_BASE_URLS,
        path,
        params,
        timeout=6,
        tentativas=2,
    )

    if data.get("retCode") != 0:
        raise RuntimeError(
            f"Bybit retCode={data.get('retCode')}: "
            f"{data.get('retMsg')}"
        )

    return data


def _get_okx_json(path: str, params: dict) -> dict:
    data = _request_json(
        OKX_BASE_URLS,
        path,
        params,
        timeout=8,
        tentativas=3,
    )

    if str(data.get("code", "")) != "0":
        raise RuntimeError(
            f"OKX code={data.get('code')}: {data.get('msg')}"
        )

    return data


def _get_kucoin_json(path: str, params: dict | None = None) -> dict:
    data = _request_json(
        KUCOIN_BASE_URLS,
        path,
        params or {},
        timeout=8,
        tentativas=3,
    )

    if str(data.get("code", "")) != "200000":
        raise RuntimeError(
            f"KuCoin code={data.get('code')}"
        )

    return data


# ============================================================
# UNIVERSO: TOP 100 + FUTUROS USDT
# ============================================================
@st.cache_data(ttl=3600, show_spinner=False)
def carregar_top100() -> pd.DataFrame:
    df = pd.read_csv(TOP100_FILE, encoding="utf-8-sig")
    df["simbolo"] = df["simbolo"].astype(str).str.strip()
    df["stablecoin"] = (
        pd.to_numeric(df["stablecoin"], errors="coerce")
        .fillna(0)
        .astype(int)
    )
    return df.sort_values("rank").reset_index(drop=True)


@st.cache_data(ttl=1800, show_spinner=False)
def instrumentos_bybit() -> list[dict]:
    todos = []
    cursor = ""

    for _ in range(10):
        params = {
            "category": "linear",
            "limit": 1000,
        }

        if cursor:
            params["cursor"] = cursor

        data = _get_bybit_json(
            "/v5/market/instruments-info",
            params,
        )

        result = data.get("result", {})
        todos.extend(result.get("list", []))

        cursor = result.get("nextPageCursor") or ""

        if not cursor:
            break

    elegiveis = []

    for x in todos:
        if str(x.get("status", "")).lower() != "trading":
            continue

        if str(x.get("quoteCoin", "")).upper() != "USDT":
            continue

        if str(x.get("settleCoin", "")).upper() != "USDT":
            continue

        contract_type = str(x.get("contractType", ""))

        if contract_type and contract_type != "LinearPerpetual":
            continue

        elegiveis.append(x)

    if not elegiveis:
        raise RuntimeError("Bybit não retornou contratos elegíveis")

    return elegiveis


@st.cache_data(ttl=1800, show_spinner=False)
def instrumentos_okx() -> list[dict]:
    data = _get_okx_json(
        "/api/v5/public/instruments",
        {"instType": "SWAP"},
    )

    elegiveis = []

    for x in data.get("data", []):
        inst_id = str(x.get("instId", "")).upper()

        if not inst_id.endswith("-USDT-SWAP"):
            continue

        estado = str(x.get("state", "")).lower()

        if estado and estado != "live":
            continue

        elegiveis.append(x)

    if not elegiveis:
        raise RuntimeError("OKX não retornou contratos elegíveis")

    return elegiveis


@st.cache_data(ttl=1800, show_spinner=False)
def instrumentos_kucoin() -> list[dict]:
    data = _get_kucoin_json(
        "/api/v1/contracts/active",
        {},
    )

    raw = data.get("data", [])

    if isinstance(raw, dict):
        raw = [raw]

    elegiveis = []

    for x in raw:
        quote = str(x.get("quoteCurrency", "")).upper()
        settle = str(x.get("settleCurrency", "")).upper()

        if quote != "USDT" or settle != "USDT":
            continue

        elegiveis.append(x)

    if not elegiveis:
        raise RuntimeError("KuCoin não retornou contratos elegíveis")

    return elegiveis


def _normalizar_base(base_coin: str) -> str:
    base = str(base_coin).upper().strip()

    aliases = {
        "XBT": "BTC",
    }

    base = aliases.get(base, base)

    for prefixo in ("1000000", "10000", "1000"):
        if base.startswith(prefixo) and len(base) > len(prefixo):
            return base[len(prefixo):]

    return base


def _mapear_bybit(
    top100: pd.DataFrame,
    instrumentos: list[dict],
) -> pd.DataFrame:
    exatos = {}
    escalados = {}

    for x in instrumentos:
        base = str(x.get("baseCoin", "")).upper().strip()
        api_symbol = str(x.get("symbol", "")).upper().strip()

        if not base or not api_symbol:
            continue

        base_normal = _normalizar_base(base)
        exatos.setdefault(base_normal, api_symbol)

        if base_normal != base:
            escalados.setdefault(base_normal, api_symbol)

    linhas = []

    for _, row in top100.iterrows():
        sym = str(row["simbolo"]).upper().strip()

        if int(row["stablecoin"]) == 1:
            continue

        api_symbol = exatos.get(sym) or escalados.get(sym)

        if not api_symbol:
            continue

        linhas.append(
            {
                "PosicaoMercado": int(row["rank"]),
                "Nome": row["nome"],
                "Ativo": sym,
                "Contrato": api_symbol,
                "Fonte": "Bybit",
                "InstrumentoAPI": api_symbol,
            }
        )

    return pd.DataFrame(linhas)


def _mapear_okx(
    top100: pd.DataFrame,
    instrumentos: list[dict],
) -> pd.DataFrame:
    mapa = {}

    for x in instrumentos:
        inst_id = str(x.get("instId", "")).upper().strip()

        if not inst_id:
            continue

        base = _normalizar_base(inst_id.split("-")[0])
        mapa.setdefault(base, inst_id)

    linhas = []

    for _, row in top100.iterrows():
        sym = str(row["simbolo"]).upper().strip()

        if int(row["stablecoin"]) == 1:
            continue

        api_symbol = mapa.get(sym)

        if not api_symbol:
            continue

        linhas.append(
            {
                "PosicaoMercado": int(row["rank"]),
                "Nome": row["nome"],
                "Ativo": sym,
                "Contrato": f"{sym}USDT",
                "Fonte": "OKX",
                "InstrumentoAPI": api_symbol,
            }
        )

    return pd.DataFrame(linhas)


def _mapear_kucoin(
    top100: pd.DataFrame,
    instrumentos: list[dict],
) -> pd.DataFrame:
    mapa = {}

    for x in instrumentos:
        base = _normalizar_base(
            x.get("baseCurrency", "")
            or x.get("displayBaseCurrency", "")
        )

        api_symbol = str(
            x.get("symbol", "")
            or x.get("displaySymbol", "")
        ).upper().strip()

        if base and api_symbol:
            mapa.setdefault(base, api_symbol)

    linhas = []

    for _, row in top100.iterrows():
        sym = str(row["simbolo"]).upper().strip()

        if int(row["stablecoin"]) == 1:
            continue

        api_symbol = mapa.get(sym)

        if not api_symbol:
            continue

        linhas.append(
            {
                "PosicaoMercado": int(row["rank"]),
                "Nome": row["nome"],
                "Ativo": sym,
                "Contrato": f"{sym}USDT",
                "Fonte": "KuCoin",
                "InstrumentoAPI": api_symbol,
            }
        )

    return pd.DataFrame(linhas)


def _ordenar_universo(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    return (
        df.sort_values("PosicaoMercado")
        .reset_index(drop=True)
    )


# ============================================================
# CANDLES E REGRA 2MV
# ============================================================
def _rma_wilder(serie: pd.Series, periodo: int = 14) -> pd.Series:
    """
    Wilder Moving Average (RMA), usada no DMI clássico.
    """
    s = pd.to_numeric(
        serie,
        errors="coerce",
    ).astype("float64")

    out = pd.Series(
        float("nan"),
        index=s.index,
        dtype="float64",
    )

    if len(s) < periodo:
        return out

    s = s.fillna(0.0)

    out.iloc[periodo - 1] = (
        s.iloc[:periodo].mean()
    )

    for i in range(periodo, len(s)):
        out.iloc[i] = (
            (
                out.iloc[i - 1]
                * (periodo - 1)
            )
            + s.iloc[i]
        ) / periodo

    return out


def _preparar_estado_2mv(df: pd.DataFrame) -> pd.DataFrame:
    """
    Mantém a mesma estrutura visual do scanner.

    Por dentro, a cor final só é confirmada quando:
    - VERDE: Close > SMA20 High E +DI > -DI
    - VERMELHO: Close < SMA20 Low E -DI > +DI
    - BRANCO: qualquer outra combinação

    O estado puro das médias fica preservado em 'estado_2mv_raw'
    para que ALERTA/COMPRA/VENDA continuem respeitando a transição
    real do preço, e não um simples cruzamento tardio do DMI.
    """
    if df.empty:
        return df

    df = df.copy()

    # --------------------------------------------------------
    # 2MV original
    # --------------------------------------------------------
    df["sma20_high"] = (
        df["high"]
        .rolling(20)
        .mean()
    )

    df["sma20_low"] = (
        df["low"]
        .rolling(20)
        .mean()
    )

    df["estado_2mv_raw"] = ""

    valido_2mv = (
        df["sma20_high"].notna()
        & df["sma20_low"].notna()
    )

    df.loc[
        valido_2mv
        & (
            df["close"]
            > df["sma20_high"]
        ),
        "estado_2mv_raw",
    ] = "verde"

    df.loc[
        valido_2mv
        & (
            df["close"]
            < df["sma20_low"]
        ),
        "estado_2mv_raw",
    ] = "vermelho"

    df.loc[
        valido_2mv
        & (
            df["estado_2mv_raw"] == ""
        ),
        "estado_2mv_raw",
    ] = "branco"

    # --------------------------------------------------------
    # DMI 14 — somente +DI / -DI.
    # O segundo "14" do DMI 14/14 é a suavização do ADX;
    # como o ADX não participa da nossa confirmação, não
    # precisamos calculá-lo.
    # --------------------------------------------------------
    high = pd.to_numeric(
        df["high"],
        errors="coerce",
    )

    low = pd.to_numeric(
        df["low"],
        errors="coerce",
    )

    close = pd.to_numeric(
        df["close"],
        errors="coerce",
    )

    movimento_alta = high.diff()
    movimento_baixa = -low.diff()

    plus_dm = pd.Series(
        0.0,
        index=df.index,
        dtype="float64",
    )

    minus_dm = pd.Series(
        0.0,
        index=df.index,
        dtype="float64",
    )

    mascara_plus = (
        (movimento_alta > movimento_baixa)
        & (movimento_alta > 0)
    )

    mascara_minus = (
        (movimento_baixa > movimento_alta)
        & (movimento_baixa > 0)
    )

    plus_dm.loc[mascara_plus] = (
        movimento_alta.loc[mascara_plus]
    )

    minus_dm.loc[mascara_minus] = (
        movimento_baixa.loc[mascara_minus]
    )

    fechamento_anterior = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - fechamento_anterior).abs(),
            (low - fechamento_anterior).abs(),
        ],
        axis=1,
    ).max(axis=1)

    tr_rma = _rma_wilder(
        true_range,
        14,
    )

    plus_rma = _rma_wilder(
        plus_dm,
        14,
    )

    minus_rma = _rma_wilder(
        minus_dm,
        14,
    )

    denominador = tr_rma.replace(
        0,
        float("nan"),
    )

    df["dmi_plus"] = (
        100.0
        * plus_rma
        / denominador
    )

    df["dmi_minus"] = (
        100.0
        * minus_rma
        / denominador
    )

    # --------------------------------------------------------
    # COR FINAL CONFIRMADA
    # --------------------------------------------------------
    df["estado"] = ""

    # Branco das médias continua branco, independentemente do DMI.
    df.loc[
        df["estado_2mv_raw"] == "branco",
        "estado",
    ] = "branco"

    # Alta somente com médias + DMI alinhados.
    df.loc[
        (
            df["estado_2mv_raw"] == "verde"
        )
        & (
            df["dmi_plus"]
            > df["dmi_minus"]
        ),
        "estado",
    ] = "verde"

    # Baixa somente com médias + DMI alinhados.
    df.loc[
        (
            df["estado_2mv_raw"] == "vermelho"
        )
        & (
            df["dmi_minus"]
            > df["dmi_plus"]
        ),
        "estado",
    ] = "vermelho"

    # Se o preço saiu das médias, mas o DMI não confirmou,
    # o resultado visual/operacional é branco.
    df.loc[
        (
            df["estado_2mv_raw"].isin(
                ["verde", "vermelho"]
            )
        )
        & (
            df["estado"] == ""
        ),
        "estado",
    ] = "branco"

    return df


def _candles_bybit(
    symbol: str,
    interval: str,
    limit: int,
) -> pd.DataFrame:
    data = _get_bybit_json(
        "/v5/market/kline",
        {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        },
    )

    raw = data.get("result", {}).get("list", [])

    if not raw:
        return pd.DataFrame()

    df = pd.DataFrame(
        raw,
        columns=[
            "startTime",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
        ],
    )

    for c in ["open", "high", "low", "close", "volume", "turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["startTime"] = pd.to_numeric(
        df["startTime"],
        errors="coerce",
    )

    df = df.dropna(
        subset=["startTime", "open", "high", "low", "close"]
    )

    df = df.sort_values("startTime").reset_index(drop=True)

    if not df.empty:
        agora = int(time.time() * 1000)
        dur = DURACAO_MS[interval]
        ultimo_inicio = int(df.iloc[-1]["startTime"])

        if ultimo_inicio + dur > agora:
            df = df.iloc[:-1].copy()

    if df.empty:
        return df

    df["Data"] = pd.to_datetime(
        df["startTime"],
        unit="ms",
        utc=True,
    )

    return _preparar_estado_2mv(df)


def _candles_okx(
    symbol: str,
    interval: str,
    limit: int,
) -> pd.DataFrame:
    bar = {
        "W": "1Wutc",
        "D": "1Dutc",
        "120": "2H",
    }[interval]

    data = _get_okx_json(
        "/api/v5/market/candles",
        {
            "instId": symbol,
            "bar": bar,
            "limit": min(int(limit), 300),
        },
    )

    raw = data.get("data", [])

    if not raw:
        return pd.DataFrame()

    registros = []

    for x in raw:
        if len(x) < 9:
            continue

        # confirm=1 = candle fechado.
        if str(x[8]) != "1":
            continue

        registros.append(
            {
                "startTime": x[0],
                "open": x[1],
                "high": x[2],
                "low": x[3],
                "close": x[4],
                "volume": x[5],
                "turnover": x[7],
            }
        )

    df = pd.DataFrame(registros)

    if df.empty:
        return df

    for c in ["open", "high", "low", "close", "volume", "turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["startTime"] = pd.to_numeric(
        df["startTime"],
        errors="coerce",
    )

    df = df.dropna(
        subset=["startTime", "open", "high", "low", "close"]
    )

    df = df.sort_values("startTime").reset_index(drop=True)

    df["Data"] = pd.to_datetime(
        df["startTime"],
        unit="ms",
        utc=True,
    )

    return _preparar_estado_2mv(df)


def _candles_kucoin(
    symbol: str,
    interval: str,
    limit: int,
) -> pd.DataFrame:
    granularity = {
        "W": 10080,
        "D": 1440,
        "120": 120,
    }[interval]

    agora_ms = int(time.time() * 1000)
    quantidade = max(int(limit) + 5, 30)
    inicio_ms = agora_ms - (
        quantidade * granularity * 60 * 1000
    )

    data = _get_kucoin_json(
        "/api/v1/kline/query",
        {
            "symbol": symbol,
            "granularity": granularity,
            "from": inicio_ms,
            "to": agora_ms,
        },
    )

    raw = data.get("data", [])

    if not raw:
        return pd.DataFrame()

    registros = []

    for x in raw:
        if len(x) < 7:
            continue

        registros.append(
            {
                "startTime": x[0],
                "open": x[1],
                "high": x[2],
                "low": x[3],
                "close": x[4],
                "volume": x[5],
                "turnover": x[6],
            }
        )

    df = pd.DataFrame(registros)

    for c in ["open", "high", "low", "close", "volume", "turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["startTime"] = pd.to_numeric(
        df["startTime"],
        errors="coerce",
    )

    df = df.dropna(
        subset=["startTime", "open", "high", "low", "close"]
    )

    # Algumas versões retornam timestamp em segundos; outras em ms.
    if not df.empty and float(df["startTime"].median()) < 10_000_000_000:
        df["startTime"] = df["startTime"] * 1000

    df = df.sort_values("startTime").reset_index(drop=True)

    if not df.empty:
        dur = DURACAO_MS[interval]
        ultimo_inicio = int(df.iloc[-1]["startTime"])

        if ultimo_inicio + dur > agora_ms:
            df = df.iloc[:-1].copy()

    if df.empty:
        return df

    df["Data"] = pd.to_datetime(
        df["startTime"],
        unit="ms",
        utc=True,
    )

    return _preparar_estado_2mv(df)


def _candles_fechados(
    fonte: str,
    symbol_api: str,
    interval: str,
    limit: int = 80,
) -> pd.DataFrame:
    if fonte == "Bybit":
        return _candles_bybit(
            symbol_api,
            interval,
            limit,
        )

    if fonte == "OKX":
        return _candles_okx(
            symbol_api,
            interval,
            limit,
        )

    if fonte == "KuCoin":
        return _candles_kucoin(
            symbol_api,
            interval,
            limit,
        )

    raise RuntimeError("Fonte de mercado desconhecida")


def _estado_atual(
    fonte: str,
    symbol_api: str,
    interval: str,
) -> tuple[str, str, str]:
    # Um pouco mais de histórico deixa a RMA de Wilder mais estável.
    df = _candles_fechados(
        fonte,
        symbol_api,
        interval,
        limit=60,
    )

    if len(df) < 22:
        return "", "", ""

    return (
        str(df.iloc[-1]["estado"]),
        str(df.iloc[-1]["estado_2mv_raw"]),
        str(df.iloc[-2]["estado_2mv_raw"]),
    )


def _situacao(
    s_confirmado: str,
    d_confirmado: str,
    m120_confirmado: str,
) -> str:
    """
    Situação baseada exclusivamente nas cores já confirmadas
    pelo alinhamento entre 2MV e DMI.

    COMPRA:
        S verde + D verde + 120 verde

    ALERTA COMPRA:
        S verde + D verde + 120 branco

    VENDA:
        S vermelho + D vermelho + 120 vermelho

    ALERTA VENDA:
        S vermelho + D vermelho + 120 branco

    Qualquer outra combinação:
        NEUTRO
    """
    if (
        s_confirmado == "verde"
        and d_confirmado == "verde"
        and m120_confirmado == "verde"
    ):
        return "COMPRA"

    if (
        s_confirmado == "verde"
        and d_confirmado == "verde"
        and m120_confirmado == "branco"
    ):
        return "ALERTA COMPRA"

    if (
        s_confirmado == "vermelho"
        and d_confirmado == "vermelho"
        and m120_confirmado == "vermelho"
    ):
        return "VENDA"

    if (
        s_confirmado == "vermelho"
        and d_confirmado == "vermelho"
        and m120_confirmado == "branco"
    ):
        return "ALERTA VENDA"

    return "NEUTRO"


def _scan_um(row: dict) -> dict | None:
    try:
        fonte = row["Fonte"]
        api_symbol = row["InstrumentoAPI"]

        s, s_raw, _ = _estado_atual(
            fonte,
            api_symbol,
            "W",
        )

        d, d_raw, _ = _estado_atual(
            fonte,
            api_symbol,
            "D",
        )

        m120, m120_raw, prev120_raw = _estado_atual(
            fonte,
            api_symbol,
            "120",
        )

        if not s or not d or not m120:
            return None

        return {
            **row,
            "S_raw": s_raw,
            "D_raw": d_raw,
            "120_raw": m120_raw,
            "Prev120_raw": prev120_raw,
            "S": ICONE_ESTADO[s],
            "D": ICONE_ESTADO[d],
            "120": ICONE_ESTADO[m120],
            "Situação": _situacao(
                s,
                d,
                m120,
            ),
        }

    except Exception:
        return None


def _executar_universo(
    universo: pd.DataFrame,
    workers: int,
) -> pd.DataFrame:
    resultados = []
    registros = universo.to_dict("records")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_scan_um, r): r
            for r in registros
        }

        for fut in as_completed(futures):
            r = fut.result()

            if r:
                resultados.append(r)

    if not resultados:
        return pd.DataFrame()

    df = pd.DataFrame(resultados)

    df["ordem_situacao"] = (
        df["Situação"]
        .map(SITUACAO_ORDEM)
        .fillna(99)
    )

    return (
        df.sort_values("PosicaoMercado")
        .reset_index(drop=True)
    )


@st.cache_data(ttl=120, show_spinner=False)
def executar_scan() -> tuple[pd.DataFrame, dict]:
    top100 = carregar_top100()
    erros = []

    provedores = [
        (
            "Bybit",
            instrumentos_bybit,
            _mapear_bybit,
            10,
        ),
        (
            "OKX",
            instrumentos_okx,
            _mapear_okx,
            5,
        ),
        (
            "KuCoin",
            instrumentos_kucoin,
            _mapear_kucoin,
            5,
        ),
    ]

    for nome, obter_instrumentos, mapear, workers in provedores:
        try:
            instrumentos = obter_instrumentos()
            universo = _ordenar_universo(
                mapear(
                    top100,
                    instrumentos,
                )
            )

            if len(universo) < 10:
                raise RuntimeError(
                    f"somente {len(universo)} contratos compatíveis"
                )

            df = _executar_universo(
                universo,
                workers=workers,
            )

            minimo_aceitavel = max(
                10,
                int(len(universo) * 0.55),
            )

            if len(df) < minimo_aceitavel:
                raise RuntimeError(
                    f"somente {len(df)} de {len(universo)} "
                    "ativos retornaram candles"
                )

            info = {
                "top100": int(len(top100)),
                "stablecoins_excluidas": int(
                    top100["stablecoin"].sum()
                ),
                "perpetuos_encontrados": int(len(universo)),
                "analisados": int(len(df)),
                "fonte": nome,
                "atualizado_em": datetime.now(
                    ZoneInfo("America/Recife")
                ).strftime("%d/%m/%Y %H:%M"),
            }

            return df, info

        except Exception as exc:
            erros.append(
                f"{nome}: {type(exc).__name__}: {exc}"
            )

    raise RuntimeError(
        "Nenhuma fonte pública de futuros respondeu. "
        + " | ".join(erros)
    )


# ============================================================
# GRÁFICO 2MV
# ============================================================
def _candlestick_trace(
    df: pd.DataFrame,
    estado: str,
) -> go.Candlestick:
    fatia = df[df["estado"] == estado]

    if estado == "verde":
        fill = CORES["verde"]
        line = CORES["verde"]

    elif estado == "vermelho":
        fill = CORES["vermelho"]
        line = CORES["vermelho"]

    else:
        fill = "#ffffff"
        line = "#9aa0a6"

    return go.Candlestick(
        x=fatia["Data"],
        open=fatia["open"],
        high=fatia["high"],
        low=fatia["low"],
        close=fatia["close"],
        increasing_line_color=line,
        increasing_fillcolor=fill,
        decreasing_line_color=line,
        decreasing_fillcolor=fill,
        name=estado.capitalize(),
        showlegend=False,
        hovertemplate=(
            "%{x}<br>"
            "Abertura: %{open}<br>"
            "Máxima: %{high}<br>"
            "Mínima: %{low}<br>"
            "Fechamento: %{close}<extra></extra>"
        ),
    )


def _figura_2mv(
    df: pd.DataFrame,
    titulo: str,
    quantidade: int,
) -> go.Figure:
    df = df.tail(quantidade).copy()

    fig = go.Figure()

    for estado in ("verde", "vermelho", "branco"):
        fig.add_trace(
            _candlestick_trace(df, estado)
        )

    fig.update_layout(
        title=dict(
            text=titulo,
            x=0.01,
            xanchor="left",
        ),
        xaxis_rangeslider_visible=False,
        margin=dict(
            l=8,
            r=8,
            t=48,
            b=8,
        ),
        height=540,
        hovermode="x",
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        dragmode="pan",
    )

    fig.update_xaxes(
        showgrid=True,
        gridcolor="rgba(128,128,128,.12)",
        fixedrange=False,
    )

    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(128,128,128,.12)",
        side="right",
        fixedrange=False,
    )

    return fig


# ============================================================
# CABEÇALHO — FICA FORA DO FRAGMENTO E NÃO PISCA
# ============================================================
st.markdown(
    '<div class="crypto-title">Scanner Cripto 2MV — Futuros</div>',
    unsafe_allow_html=True,
)

c_atualizar, c_info = st.columns(
    [1.10, 5.90],
    vertical_alignment="center",
)

with c_atualizar:
    atualizar = st.button(
        "🔄 Atualizar",
        type="secondary",
        use_container_width=True,
    )

if atualizar:
    st.cache_data.clear()
    st.rerun()

try:
    with st.spinner("Consultando o mercado de futuros e calculando o 2MV..."):
        df_scan, info_scan = executar_scan()
except Exception as exc:
    st.error(
        "Não consegui acessar as fontes públicas de futuros agora. "
        "Clique em Atualizar daqui a pouco."
    )
    st.stop()

with c_info:
    st.caption(
        f"Atualizado em {info_scan['atualizado_em']}h"
    )

if df_scan.empty:
    st.error(
        "Não foi possível montar o scanner agora. "
        "Tente Atualizar novamente em alguns instantes."
    )
    st.stop()


# ============================================================
# NAVEGAÇÃO E CONTEÚDO EM FRAGMENTO
# Só esta área é redesenhada ao trocar Sinais/Gráfico.
# ============================================================
if "pagina_crypto" not in st.session_state:
    st.session_state["pagina_crypto"] = "sinais"

if "ativo_crypto" not in st.session_state:
    st.session_state["ativo_crypto"] = str(
        df_scan.iloc[0]["Ativo"]
    )


@st.fragment
def conteudo_principal(df_base: pd.DataFrame):
    def ir_para(
        pagina: str,
        ativo: str | None = None,
    ):
        st.session_state["pagina_crypto"] = pagina

        if ativo:
            st.session_state["ativo_crypto"] = ativo

    pagina = st.session_state.get(
        "pagina_crypto",
        "sinais",
    )

    nav1, nav2, nav3 = st.columns(
        [0.80, 1.15, 5.05],
        gap="small",
    )

    nav1.button(
        "📋 Sinais",
        key="nav_sinais_crypto",
        type="primary" if pagina == "sinais" else "secondary",
        use_container_width=True,
        on_click=ir_para,
        args=("sinais",),
    )

    nav2.button(
        "📈 Gráfico",
        key="nav_grafico_crypto",
        type="primary" if pagina == "grafico" else "secondary",
        use_container_width=True,
        on_click=ir_para,
        args=("grafico",),
    )

    st.markdown("")

    # ========================================================
    # PÁGINA SINAIS
    # ========================================================
    if pagina == "sinais":
        st.subheader("Sinais 2MV")

        busca = st.text_input(
            "Procurar ativo",
            placeholder="Ex.: BTC, ETH, SOL, PEPE...",
            key="busca_crypto_sinais",
        ).strip().upper()

        tabela = df_base[
            ["Ativo", "Contrato", "S", "D", "120", "Situação"]
        ].copy()

        if busca:
            mascara = (
                tabela["Ativo"]
                .astype(str)
                .str.upper()
                .str.contains(busca, na=False)
                |
                tabela["Contrato"]
                .astype(str)
                .str.upper()
                .str.contains(busca, na=False)
            )
            tabela = tabela[mascara].copy()

        if "_sort_crypto_coluna" not in st.session_state:
            st.session_state["_sort_crypto_coluna"] = None

        if "_sort_crypto_asc" not in st.session_state:
            st.session_state["_sort_crypto_asc"] = True

        coluna_ordenacao = st.session_state[
            "_sort_crypto_coluna"
        ]

        crescente = st.session_state[
            "_sort_crypto_asc"
        ]

        def ordenar_por(coluna):
            atual = st.session_state.get(
                "_sort_crypto_coluna"
            )

            if atual == coluna:
                st.session_state["_sort_crypto_asc"] = (
                    not st.session_state.get(
                        "_sort_crypto_asc",
                        True,
                    )
                )
            else:
                st.session_state["_sort_crypto_coluna"] = coluna
                st.session_state["_sort_crypto_asc"] = True

        def rotulo_cabecalho(coluna, texto):
            if coluna_ordenacao != coluna:
                return texto

            return (
                f"{texto} ▲"
                if crescente
                else f"{texto} ▼"
            )

        h = st.columns(
            [1.35, 0.62, 0.62, 0.62, 2.25],
            gap="small",
        )

        cabecalhos = [
            ("Ativo", "Ativo"),
            ("S", "S"),
            ("D", "D"),
            ("120", "120"),
            ("Situação", "Situação"),
        ]

        for coluna_ui, (coluna, texto_cab) in zip(
            h,
            cabecalhos,
        ):
            if coluna_ui.button(
                rotulo_cabecalho(
                    coluna,
                    texto_cab,
                ),
                key=f"ordenar_crypto_{coluna}",
                type="secondary",
                use_container_width=True,
            ):
                ordenar_por(coluna)

                coluna_ordenacao = st.session_state[
                    "_sort_crypto_coluna"
                ]
                crescente = st.session_state[
                    "_sort_crypto_asc"
                ]

        if coluna_ordenacao == "Ativo":
            tabela = tabela.sort_values(
                "Contrato",
                ascending=crescente,
                kind="stable",
            )

        elif coluna_ordenacao in ["S", "D", "120"]:
            ordem_cores = {
                "🟢": 0,
                "⚪": 1,
                "🔴": 2,
            }

            tabela["_ordem_cor"] = (
                tabela[coluna_ordenacao]
                .map(ordem_cores)
                .fillna(9)
            )

            tabela = (
                tabela
                .sort_values(
                    by=["_ordem_cor", "Ativo"],
                    ascending=[crescente, True],
                    kind="stable",
                )
                .drop(columns=["_ordem_cor"])
            )

        elif coluna_ordenacao == "Situação":
            tabela["_ordem_situacao"] = (
                tabela["Situação"]
                .map(SITUACAO_ORDEM)
                .fillna(99)
            )

            tabela = (
                tabela
                .sort_values(
                    by=["_ordem_situacao", "Ativo"],
                    ascending=[crescente, True],
                    kind="stable",
                )
                .drop(columns=["_ordem_situacao"])
            )

        try:
            corpo = st.container(
                height=650,
                border=True,
            )
        except TypeError:
            corpo = st.container(
                border=True,
            )

        with corpo:
            for _, linha in tabela.iterrows():
                ativo = str(linha["Ativo"])
                contrato_exibido = str(linha["Contrato"])

                c = st.columns(
                    [1.35, 0.62, 0.62, 0.62, 2.25],
                    gap="small",
                    vertical_alignment="center",
                )

                c[0].button(
                    contrato_exibido,
                    key=f"abrir_crypto_{ativo}",
                    type="tertiary",
                    help=f"Abrir gráfico de {contrato_exibido}",
                    on_click=ir_para,
                    args=("grafico", ativo),
                )

                c[1].markdown(
                    f"<div style='text-align:center'>{linha['S']}</div>",
                    unsafe_allow_html=True,
                )

                c[2].markdown(
                    f"<div style='text-align:center'>{linha['D']}</div>",
                    unsafe_allow_html=True,
                )

                c[3].markdown(
                    f"<div style='text-align:center'>{linha['120']}</div>",
                    unsafe_allow_html=True,
                )

                c[4].markdown(
                    f"<div style='font-size:.80rem'>{linha['Situação']}</div>",
                    unsafe_allow_html=True,
                )

    # ========================================================
    # PÁGINA GRÁFICO
    # ========================================================
    else:
        opcoes = (
            df_base
            .sort_values("PosicaoMercado")["Ativo"]
            .tolist()
        )

        mapa = (
            df_base
            .set_index("Ativo")
            .to_dict("index")
        )

        ativo_atual = st.session_state.get(
            "ativo_crypto",
            opcoes[0],
        )

        if ativo_atual not in opcoes:
            ativo_atual = opcoes[0]
            st.session_state["ativo_crypto"] = ativo_atual

        indice = opcoes.index(ativo_atual)

        c1, c2, c3 = st.columns(
            [2.25, 1.30, 3.45],
            gap="small",
        )

        with c1:
            ativo = st.selectbox(
                "Ativo",
                opcoes,
                index=indice,
                key="seletor_crypto_grafico",
                format_func=lambda x: f"{mapa[x]['Contrato']}",
            )

        st.session_state["ativo_crypto"] = ativo

        with c2:
            periodo = st.radio(
                "Período",
                ["S", "D", "120"],
                index=0,
                key="periodo_crypto_grafico",
                horizontal=True,
            )

        registro = mapa[ativo]
        contrato = registro["Contrato"]

        r = df_base[
            df_base["Ativo"] == ativo
        ].iloc[0]

        st.markdown(
            f"### {contrato}"
        )

        periodo_api = {
            "S": "W",
            "D": "D",
            "120": "120",
        }[periodo]

        titulo_periodo = {
            "S": "Semanal",
            "D": "Diário",
            "120": "120 min",
        }[periodo]

        limite = 95 if periodo == "S" else 120

        try:
            df_graf = _candles_fechados(
                registro["Fonte"],
                registro["InstrumentoAPI"],
                periodo_api,
                limit=limite + 25,
            )
        except Exception as exc:
            st.error(
                f"Não consegui carregar {contrato}: {exc}"
            )
            return

        if df_graf.empty:
            st.warning(
                "Sem dados suficientes para esse gráfico."
            )
            return

        fig = _figura_2mv(
            df_graf,
            f"{contrato} — {titulo_periodo}",
            quantidade=90 if periodo == "S" else 110,
        )

        # Sem scrollZoom: a roda do mouse volta a rolar a página,
        # não a reduzir/ampliar o gráfico.
        st.plotly_chart(
            fig,
            use_container_width=True,
            key=f"graf_crypto_{ativo}_{periodo}",
            config={
                "scrollZoom": False,
                "displayModeBar": False,
                "responsive": True,
            },
        )



conteudo_principal(df_scan)
