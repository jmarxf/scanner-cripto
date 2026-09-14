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
    "ALTA — AGUARDAR": 2,
    "NEUTRO": 3,
    "BAIXA — AGUARDAR": 4,
    "ALERTA VENDA": 5,
    "VENDA": 6,
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
# HTTP
# ============================================================
def _get_json(path: str, params: dict, timeout: int = 14) -> dict:
    ultimo_erro = None

    for base in BYBIT_BASE_URLS:
        try:
            r = requests.get(
                base + path,
                params=params,
                timeout=timeout,
                headers={"User-Agent": "Scanner-2MV-Cripto/2.0"},
            )
            r.raise_for_status()
            data = r.json()

            if data.get("retCode") == 0:
                return data

            ultimo_erro = RuntimeError(
                f"Bybit retCode={data.get('retCode')}: {data.get('retMsg')}"
            )
        except Exception as exc:
            ultimo_erro = exc

    raise RuntimeError(f"Falha ao consultar a Bybit: {ultimo_erro}")


# ============================================================
# UNIVERSO: TOP 100 + PERPÉTUOS USDT
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

        data = _get_json("/v5/market/instruments-info", params)
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

    return elegiveis


def _normalizar_base(base_coin: str) -> str:
    base = str(base_coin).upper().strip()

    for prefixo in ("1000000", "10000", "1000"):
        if base.startswith(prefixo) and len(base) > len(prefixo):
            return base[len(prefixo):]

    return base


def _mapear_contratos(
    top100: pd.DataFrame,
    instrumentos: list[dict],
) -> pd.DataFrame:
    exatos = {}
    escalados = {}

    for x in instrumentos:
        base = str(x.get("baseCoin", "")).upper().strip()
        symbol = str(x.get("symbol", "")).upper().strip()

        if not base or not symbol:
            continue

        exatos.setdefault(base, symbol)

        normal = _normalizar_base(base)
        if normal != base:
            escalados.setdefault(normal, symbol)

    linhas = []

    for _, row in top100.iterrows():
        sym = str(row["simbolo"]).upper().strip()

        if int(row["stablecoin"]) == 1:
            continue

        contrato = exatos.get(sym) or escalados.get(sym)

        if not contrato:
            continue

        # A posição de mercado continua guardada internamente apenas para
        # preservar a ordem da lista-base. Ela NÃO aparece na interface.
        linhas.append(
            {
                "PosicaoMercado": int(row["rank"]),
                "Nome": row["nome"],
                "Ativo": sym,
                "Contrato": contrato,
            }
        )

    if not linhas:
        return pd.DataFrame(
            columns=["PosicaoMercado", "Nome", "Ativo", "Contrato"]
        )

    return (
        pd.DataFrame(linhas)
        .sort_values("PosicaoMercado")
        .reset_index(drop=True)
    )


# ============================================================
# CANDLES E REGRA 2MV
# ============================================================
def _candles_fechados(
    symbol: str,
    interval: str,
    limit: int = 80,
) -> pd.DataFrame:
    data = _get_json(
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

    # Usa somente candle fechado.
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

    df["sma20_high"] = df["high"].rolling(20).mean()
    df["sma20_low"] = df["low"].rolling(20).mean()

    def estado(r):
        if pd.isna(r["sma20_high"]) or pd.isna(r["sma20_low"]):
            return ""

        if r["close"] > r["sma20_high"]:
            return "verde"

        if r["close"] < r["sma20_low"]:
            return "vermelho"

        return "branco"

    df["estado"] = df.apply(estado, axis=1)
    return df


def _estado_atual(
    symbol: str,
    interval: str,
) -> tuple[str, str]:
    df = _candles_fechados(
        symbol,
        interval,
        limit=55,
    )

    if len(df) < 22:
        return "", ""

    return (
        str(df.iloc[-1]["estado"]),
        str(df.iloc[-2]["estado"]),
    )


def _situacao(
    s: str,
    d: str,
    m120: str,
    prev120: str,
) -> str:
    contexto_alta = (
        s in ("verde", "branco")
        and d == "verde"
    )

    contexto_baixa = (
        s in ("vermelho", "branco")
        and d == "vermelho"
    )

    if contexto_alta:
        if m120 == "verde" and prev120 == "branco":
            return "COMPRA"

        if m120 == "branco" and prev120 == "vermelho":
            return "ALERTA COMPRA"

        return "ALTA — AGUARDAR"

    if contexto_baixa:
        if m120 == "vermelho" and prev120 == "branco":
            return "VENDA"

        if m120 == "branco" and prev120 == "verde":
            return "ALERTA VENDA"

        return "BAIXA — AGUARDAR"

    return "NEUTRO"


def _scan_um(row: dict) -> dict | None:
    try:
        contrato = row["Contrato"]

        s, _ = _estado_atual(contrato, "W")
        d, _ = _estado_atual(contrato, "D")
        m120, prev120 = _estado_atual(contrato, "120")

        if not s or not d or not m120:
            return None

        return {
            **row,
            "S_raw": s,
            "D_raw": d,
            "120_raw": m120,
            "Prev120_raw": prev120,
            "S": ICONE_ESTADO[s],
            "D": ICONE_ESTADO[d],
            "120": ICONE_ESTADO[m120],
            "Situação": _situacao(s, d, m120, prev120),
        }

    except Exception:
        return None


@st.cache_data(ttl=120, show_spinner=False)
def executar_scan() -> tuple[pd.DataFrame, dict]:
    top100 = carregar_top100()
    instrumentos = instrumentos_bybit()
    universo = _mapear_contratos(
        top100,
        instrumentos,
    )

    resultados = []
    registros = universo.to_dict("records")

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(_scan_um, r): r
            for r in registros
        }

        for fut in as_completed(futures):
            r = fut.result()

            if r:
                resultados.append(r)

    if resultados:
        df = pd.DataFrame(resultados)

        df["ordem_situacao"] = (
            df["Situação"]
            .map(SITUACAO_ORDEM)
            .fillna(99)
        )

        df = (
            df.sort_values("PosicaoMercado")
            .reset_index(drop=True)
        )
    else:
        df = pd.DataFrame()

    info = {
        "top100": int(len(top100)),
        "stablecoins_excluidas": int(
            top100["stablecoin"].sum()
        ),
        "perpetuos_encontrados": int(len(universo)),
        "analisados": int(len(df)),
        "atualizado_em": datetime.now(
            ZoneInfo("America/Recife")
        ).strftime("%d/%m/%Y %H:%M"),
    }

    return df, info


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

with st.spinner("Consultando a Bybit e calculando o 2MV..."):
    df_scan, info_scan = executar_scan()

with c_info:
    st.caption(
        f"Atualizado em {info_scan['atualizado_em']}h"
    )

if df_scan.empty:
    st.error(
        "Não foi possível montar o scanner agora. "
        "Verifique a conexão com a Bybit e tente Atualizar novamente."
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
                contrato,
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
