from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Literal

import gradio as gr
import httpx
import numpy as np
import pandas as pd


Side = Literal["long", "short"]


@dataclass(frozen=True)
class BotConfig:
    api_key: str = field(default_factory=lambda: os.getenv("OKX_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("OKX_API_SECRET", ""))
    api_passphrase: str = field(default_factory=lambda: os.getenv("OKX_API_PASSPHRASE", ""))
    simulated: bool = field(default_factory=lambda: os.getenv("OKX_SIMULATED", "1") != "0")
    autostart: bool = field(default_factory=lambda: os.getenv("BOT_AUTOSTART", "false").lower() == "true")
    base_url: str = field(default_factory=lambda: os.getenv("OKX_BASE_URL", "https://www.okx.com"))
    timeframe: str = field(default_factory=lambda: os.getenv("TIMEFRAME", "5m"))
    confirm_timeframe: str = field(default_factory=lambda: os.getenv("CONFIRM_TIMEFRAME", "15m"))
    top_symbols: int = field(default_factory=lambda: int(os.getenv("TOP_SYMBOLS", "70")))
    max_concurrent_positions: int = field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT_POSITIONS", "10")))
    order_margin_usdt: Decimal = field(default_factory=lambda: Decimal(os.getenv("ORDER_MARGIN_USDT", "15")))
    leverage: Decimal = field(default_factory=lambda: Decimal(os.getenv("LEVERAGE", "10")))
    atr_stop_mult: Decimal = field(default_factory=lambda: Decimal(os.getenv("ATR_STOP_MULT", "2.0")))
    reward_risk: Decimal = field(default_factory=lambda: Decimal(os.getenv("REWARD_RISK", "2.0")))
    break_even_trigger_r: Decimal = field(default_factory=lambda: Decimal(os.getenv("BREAK_EVEN_TRIGGER_R", "0.40")))
    break_even_lock_r: Decimal = field(default_factory=lambda: Decimal(os.getenv("BREAK_EVEN_LOCK_R", "0.15")))
    trailing_trigger_r: Decimal = field(default_factory=lambda: Decimal(os.getenv("TRAILING_TRIGGER_R", "0.70")))
    trailing_lock_r: Decimal = field(default_factory=lambda: Decimal(os.getenv("TRAILING_LOCK_R", "0.50")))
    poll_seconds: float = field(default_factory=lambda: float(os.getenv("POLL_SECONDS", "20")))
    max_spread_bps: Decimal = field(default_factory=lambda: Decimal(os.getenv("MAX_SPREAD_BPS", "8")))
    min_atr_pct: Decimal = field(default_factory=lambda: Decimal(os.getenv("MIN_ATR_PCT", "0.0015")))
    max_atr_pct: Decimal = field(default_factory=lambda: Decimal(os.getenv("MAX_ATR_PCT", "0.025")))
    min_adx: float = field(default_factory=lambda: float(os.getenv("MIN_ADX", "18")))
    max_funding_abs: Decimal = field(default_factory=lambda: Decimal(os.getenv("MAX_FUNDING_ABS", "0.0012")))
    avoid_one_way_funding: Decimal = field(default_factory=lambda: Decimal(os.getenv("AVOID_ONE_WAY_FUNDING", "0.0008")))
    daily_loss_stop_usdt: Decimal = field(default_factory=lambda: Decimal(os.getenv("DAILY_LOSS_STOP_USDT", "45")))
    cooldown_minutes: int = field(default_factory=lambda: int(os.getenv("COOLDOWN_MINUTES", "20")))


@dataclass
class Instrument:
    inst_id: str
    lot_sz: Decimal
    min_sz: Decimal
    ct_val: Decimal
    state: str


@dataclass
class Signal:
    inst_id: str
    side: Side
    price: Decimal
    atr: Decimal
    score: float
    reason: str


@dataclass
class ManagedPosition:
    inst_id: str
    side: Side
    size: Decimal
    entry: Decimal
    stop: Decimal
    take_profit: Decimal
    atr: Decimal
    ct_val: Decimal
    initial_risk: Decimal
    opened_at: float
    break_even_done: bool = False
    trailing_active: bool = False
    best_price: Decimal = Decimal("0")
    last_price: Decimal = Decimal("0")


@dataclass
class TradeRecord:
    inst_id: str
    side: Side
    entry: Decimal
    exit_price: Decimal
    pnl: Decimal
    reason: str
    closed_at: str


class OKXClient:
    def __init__(self, config: BotConfig):
        self.config = config
        self.client = httpx.AsyncClient(base_url=config.base_url, timeout=15)

    async def close(self) -> None:
        await self.client.aclose()

    def _headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        prehash = f"{timestamp}{method.upper()}{path}{body}"
        signature = base64.b64encode(
            hmac.new(self.config.api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
        ).decode()
        headers = {
            "Content-Type": "application/json",
            "OK-ACCESS-KEY": self.config.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.config.api_passphrase,
        }
        if self.config.simulated:
            headers["x-simulated-trading"] = "1"
        return headers

    async def request(self, method: str, path: str, body: dict[str, Any] | None = None, auth: bool = False) -> Any:
        payload = json.dumps(body, separators=(",", ":")) if body else ""
        headers = self._headers(method, path, payload) if auth else {"Content-Type": "application/json"}
        if self.config.simulated:
            headers["x-simulated-trading"] = "1"
        response = await self.client.request(method, path, content=payload or None, headers=headers)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "0":
            raise RuntimeError(f"OKX error {data.get('code')}: {data.get('msg')} {data.get('data')}")
        return data.get("data", [])

    async def instruments(self) -> list[Instrument]:
        rows = await self.request("GET", "/api/v5/public/instruments?instType=SWAP")
        out: list[Instrument] = []
        for row in rows:
            if row.get("settleCcy") != "USDT" or not row.get("instId", "").endswith("-USDT-SWAP"):
                continue
            if _is_disallowed_symbol(row["instId"]):
                continue
            out.append(
                Instrument(
                    inst_id=row["instId"],
                    lot_sz=Decimal(row["lotSz"]),
                    min_sz=Decimal(row["minSz"]),
                    ct_val=Decimal(row["ctVal"]),
                    state=row["state"],
                )
            )
        return out

    async def tickers(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/api/v5/market/tickers?instType=SWAP")

    async def funding_rate(self, inst_id: str) -> Decimal | None:
        rows = await self.request("GET", f"/api/v5/public/funding-rate?instId={inst_id}")
        if not rows:
            return None
        raw = rows[0].get("fundingRate")
        return Decimal(str(raw)) if raw not in (None, "") else None

    async def open_interest(self, inst_id: str) -> Decimal | None:
        rows = await self.request("GET", f"/api/v5/public/open-interest?instType=SWAP&instId={inst_id}")
        if not rows:
            return None
        raw = rows[0].get("oiCcy") or rows[0].get("oi")
        return Decimal(str(raw)) if raw not in (None, "") else None

    async def candles(self, inst_id: str, bar: str, limit: int = 120) -> pd.DataFrame:
        path = f"/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}"
        rows = await self.request("GET", path)
        columns = ["ts", "open", "high", "low", "close", "volume", "vol_ccy", "vol_ccy_quote", "confirm"]
        df = pd.DataFrame(rows, columns=columns)
        if df.empty:
            return df
        numeric_cols = ["open", "high", "low", "close", "volume", "vol_ccy_quote"]
        df[numeric_cols] = df[numeric_cols].astype(float)
        df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
        return df.sort_values("ts").reset_index(drop=True)

    async def set_leverage(self, inst_id: str, lever: Decimal) -> None:
        body = {"instId": inst_id, "lever": str(lever), "mgnMode": "cross"}
        await self.request("POST", "/api/v5/account/set-leverage", body, auth=True)

    async def place_market_order(self, inst_id: str, side: Side, size: Decimal, reduce_only: bool = False) -> Any:
        body = {
            "instId": inst_id,
            "tdMode": "cross",
            "side": "buy" if side == "long" else "sell",
            "posSide": side,
            "ordType": "market",
            "sz": _fmt_decimal(size),
        }
        if reduce_only:
            body["reduceOnly"] = "true"
        return await self.request("POST", "/api/v5/trade/order", body, auth=True)

    async def close_position(self, inst_id: str, side: Side) -> Any:
        body = {"instId": inst_id, "mgnMode": "cross", "posSide": side}
        return await self.request("POST", "/api/v5/trade/close-position", body, auth=True)

    async def positions(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/api/v5/account/positions?instType=SWAP", auth=True)


class StrategyEngine:
    def __init__(self, config: BotConfig):
        self.config = config

    def signal(
        self,
        inst_id: str,
        df: pd.DataFrame,
        higher_df: pd.DataFrame,
        ticker: dict[str, Any],
        funding_rate: Decimal | None,
        open_interest: Decimal | None,
    ) -> Signal | None:
        if len(df) < 80 or len(higher_df) < 60:
            return None
        frame = add_indicators(df)
        higher = add_indicators(higher_df)
        row = frame.iloc[-2]
        prev = frame.iloc[-3]
        high_row = higher.iloc[-2]

        close = Decimal(str(row.close))
        atr = Decimal(str(row.atr))
        if close <= 0 or atr <= 0:
            return None

        atr_pct = atr / close
        if atr_pct < self.config.min_atr_pct or atr_pct > self.config.max_atr_pct:
            return None

        spread_bps = _spread_bps(ticker)
        if spread_bps is None or spread_bps > self.config.max_spread_bps:
            return None

        if row.adx < self.config.min_adx or high_row.adx < self.config.min_adx - 2:
            return None

        volume_z = row.volume_z
        if volume_z < 0.35:
            return None

        if funding_rate is not None and abs(funding_rate) > self.config.max_funding_abs:
            return None

        high_trend_up = high_row.close > high_row.ema_100 and high_row.ema_20 > high_row.ema_50 > high_row.ema_100
        high_trend_down = high_row.close < high_row.ema_100 and high_row.ema_20 < high_row.ema_50 < high_row.ema_100
        above_vwap = row.close > row.vwap
        below_vwap = row.close < row.vwap

        range_width = Decimal(str(row.donchian_upper - row.donchian_lower))
        if range_width < atr * Decimal("1.25"):
            return None

        atr_extension = abs(Decimal(str(row.close - row.vwap))) / atr
        if atr_extension > Decimal("1.80"):
            return None

        long_breakout = prev.close <= prev.donchian_upper and row.close > row.donchian_upper and row.close > row.ema_20
        short_breakout = prev.close >= prev.donchian_lower and row.close < row.donchian_lower and row.close < row.ema_20
        long_pullback_continuation = (
            row.low <= row.ema_20
            and row.close > row.open
            and row.close > row.donchian_mid
            and row.close > prev.high
            and row.plus_di > row.minus_di
        )
        short_pullback_continuation = (
            row.high >= row.ema_20
            and row.close < row.open
            and row.close < row.donchian_mid
            and row.close < prev.low
            and row.minus_di > row.plus_di
        )

        long_momentum_ok = 52 <= row.rsi <= 76 and row.plus_di > row.minus_di and row.close > row.ema_50
        short_momentum_ok = 24 <= row.rsi <= 48 and row.minus_di > row.plus_di and row.close < row.ema_50

        if funding_rate is not None:
            if funding_rate > self.config.avoid_one_way_funding:
                long_momentum_ok = False
            if funding_rate < -self.config.avoid_one_way_funding:
                short_momentum_ok = False

        oi_bonus = 0.15 if open_interest is not None and open_interest > 0 else 0.0
        adx_bonus = min((row.adx - self.config.min_adx) / 20, 0.75)
        structure_bonus = min(float(range_width / atr) / 10, 0.50)
        extension_penalty = float(atr_extension) * 0.20
        base_score = float(volume_z + adx_bonus + structure_bonus + min(float(atr_pct * 100), 2.0) + oi_bonus - extension_penalty)

        if (long_breakout or long_pullback_continuation) and high_trend_up and above_vwap and long_momentum_ok:
            setup = "Donchian breakout" if long_breakout else "EMA pullback continuation"
            reason = f"{setup} + ADX/DI trend + RSI momentum + VWAP + volume + derivatives filter"
            return Signal(inst_id, "long", close, atr, base_score, reason)
        if (short_breakout or short_pullback_continuation) and high_trend_down and below_vwap and short_momentum_ok:
            setup = "Donchian breakdown" if short_breakout else "EMA pullback continuation"
            reason = f"{setup} + ADX/DI trend + RSI momentum + VWAP + volume + derivatives filter"
            return Signal(inst_id, "short", close, atr, base_score, reason)
        return None


class BotRuntime:
    def __init__(self) -> None:
        self.config = BotConfig()
        self.log: list[str] = []
        self.positions: dict[str, ManagedPosition] = {}
        self.closed_trades: list[TradeRecord] = []
        self.cooldowns: dict[str, float] = {}
        self.derivatives_cache: dict[str, tuple[float, Decimal | None, Decimal | None]] = {}
        self.running = False
        self.thread: threading.Thread | None = None
        self.last_scan = "never"
        self.last_error = ""
        self._lock = threading.Lock()

    def start(self) -> str:
        if self.running:
            return self.dashboard_html()
        missing = self._missing_secrets()
        if missing:
            self._log(f"Faltan secretos: {', '.join(missing)}")
            return self.dashboard_html()
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        self._log("Bot iniciado en OKX demo.")
        return self.dashboard_html()

    def stop(self) -> str:
        self.running = False
        self._log("Bot detenido por control manual.")
        return self.dashboard_html()

    def dashboard_html(self) -> str:
        with self._lock:
            positions = list(self.positions.values())
            closed = list(self.closed_trades[-8:])
            logs = list(self.log[-16:])
            last_error = self.last_error
            last_scan = self.last_scan

        total_unrealized = sum((_position_pnl(p) for p in positions), Decimal("0"))
        total_closed = sum((t.pnl for t in self.closed_trades), Decimal("0"))
        total_pnl = total_unrealized + total_closed
        wins = [t for t in self.closed_trades if t.pnl > 0]
        losses = [t for t in self.closed_trades if t.pnl < 0]
        win_rate = (len(wins) / len(self.closed_trades) * 100) if self.closed_trades else 0
        gross_win = sum((t.pnl for t in wins), Decimal("0"))
        gross_loss = abs(sum((t.pnl for t in losses), Decimal("0")))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else Decimal("0")
        best_trade = max((t.pnl for t in self.closed_trades), default=Decimal("0"))
        worst_trade = min((t.pnl for t in self.closed_trades), default=Decimal("0"))
        bias = _market_bias(positions)
        active_badge = "ESCANER ACTIVO" if self.running else "ESCANER DETENIDO"
        status_class = "ok" if self.running else "warn"

        position_rows = "".join(_position_row(p) for p in positions) or (
            "<tr><td colspan='7' class='muted center'>Sin posiciones abiertas. El bot espera una señal de alta calidad.</td></tr>"
        )
        trade_rows = "".join(_trade_card(t) for t in reversed(closed)) or (
            "<div class='empty'>Sin cierres registrados desde este arranque.</div>"
        )
        terminal_lines = "".join(f"<div><span class='term-prefix'>[OKX]</span> {_esc(line)}</div>" for line in logs) or (
            "<div><span class='term-prefix'>[SYSTEM]</span> Terminal listo.</div>"
        )

        return f"""
<div class="terminal-shell">
  <div class="topbar">
    <div class="brand">
      <div class="bolt">OK</div>
      <div>
        <div class="brand-name">OKX QUANT TERMINAL</div>
        <div class="badges"><span>ELITE V3.0</span><span>DEMO EXCHANGE</span><span>USDT-SWAP</span></div>
      </div>
    </div>
    <div class="status-pill {status_class}">{active_badge}</div>
  </div>

  <div class="grid hero-grid">
    <section class="card balance-card">
      <div class="label">CAPITAL OPERATIVO DEMO</div>
      <div class="big">{_fmt_money(self.config.order_margin_usdt * self.config.leverage * self.config.max_concurrent_positions)} USDT</div>
      <div class="sub">Margen por trade {_fmt_money(self.config.order_margin_usdt)} USDT | Notional {_fmt_money(self.config.order_margin_usdt * self.config.leverage)} USDT</div>
      <div class="mini {'pos' if total_pnl >= 0 else 'neg'}">PNL sistema {_fmt_money(total_pnl)} USDT</div>
    </section>
    <section class="card bias-card">
      <div class="label">SESGO OPERATIVO</div>
      <div class="trend">{bias}</div>
      <div class="sub">Filtro: Adaptive Donchian Momentum | Confirmacion: 5m / 15m</div>
    </section>
    <section class="card strategy-card">
      <div class="label">ESTRATEGIA OKX ADAPTIVE DONCHIAN V3.0</div>
      <div class="kv"><span>APALANCAMIENTO</span><b>{self.config.leverage}X</b></div>
      <div class="kv"><span>MONTO</span><b>{_fmt_money(self.config.order_margin_usdt)} USDT</b></div>
      <div class="kv"><span>EJECUCION</span><b>{int(self.config.poll_seconds)} SEGUNDOS</b></div>
      <div class="kv"><span>UNIVERSO</span><b>TOP {self.config.top_symbols} OKX SWAPS</b></div>
      <div class="kv"><span>STOP / TP</span><b>{self.config.atr_stop_mult} ATR / {self.config.reward_risk}R</b></div>
      <div class="kv"><span>PROTECCION</span><b>BE 40% / TS 70%</b></div>
    </section>
  </div>

  <div class="grid stat-grid">
    <section class="stat-card"><div>PNL VIVO</div><strong class="{_pnl_class(total_unrealized)}">{_fmt_money(total_unrealized)}</strong><small>Posiciones {len(positions)} / {self.config.max_concurrent_positions}</small></section>
    <section class="stat-card accent-a"><div>PNL CERRADO</div><strong class="{_pnl_class(total_closed)}">{_fmt_money(total_closed)}</strong><small>Trades {len(self.closed_trades)}</small></section>
    <section class="stat-card accent-b"><div>ULTIMO ESCANEO</div><strong>{_esc(last_scan)}</strong><small>Error: {_esc(last_error or "ninguno")}</small></section>
    <section class="stat-card accent-c"><div>RIESGO</div><strong>{_fmt_money(self.config.daily_loss_stop_usdt)}</strong><small>Stop diario teorico USDT</small></section>
  </div>

  <div class="grid main-grid">
    <section class="card positions-card">
      <div class="section-head"><span>MONITOR DE POSICIONES ACTIVAS</span><b>{len(positions)} ACTIVAS</b></div>
      <table>
        <thead><tr><th>SIMBOLO</th><th>DIRECCION</th><th>ENTRADA</th><th>PRECIO</th><th>STOP</th><th>PNL VIVO</th><th>ESTATUS</th></tr></thead>
        <tbody>{position_rows}</tbody>
      </table>
    </section>
    <section class="card performance-card">
      <div class="section-head"><span>RENDIMIENTO GLOBAL</span></div>
      <div class="bar"><span style="width:{min(max(win_rate, 3), 100):.1f}%"></span></div>
      <div class="perf-grid">
        <div><small>WIN RATE</small><strong>{win_rate:.1f}%</strong></div>
        <div><small>PROFIT FACTOR</small><strong>{profit_factor:.2f}</strong></div>
        <div><small>MEJOR TRADE</small><strong class="{_pnl_class(best_trade)}">{_fmt_money(best_trade)}</strong></div>
        <div><small>PEOR TRADE</small><strong class="{_pnl_class(worst_trade)}">{_fmt_money(worst_trade)}</strong></div>
      </div>
    </section>
  </div>

  <div class="grid lower-grid">
    <section class="card history-card">
      <div class="section-head"><span>HISTORIAL DE TRADES</span></div>
      <div class="trade-list">{trade_rows}</div>
    </section>
    <section class="card terminal-card">
      <div class="section-head"><span>TERMINAL DE EJECUCION OKX</span></div>
      <div class="terminal">{terminal_lines}</div>
    </section>
  </div>
</div>
"""

    def _run_loop(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        client = OKXClient(self.config)
        strategy = StrategyEngine(self.config)
        try:
            instruments = {item.inst_id: item for item in await client.instruments() if item.state == "live"}
            self._log(f"Universo OKX cargado: {len(instruments)} swaps USDT elegibles.")
            while self.running:
                try:
                    await self._tick(client, strategy, instruments)
                    self.last_error = ""
                except Exception as exc:
                    self.last_error = str(exc)
                    self._log(f"Error controlado: {exc}")
                await asyncio.sleep(self.config.poll_seconds)
        finally:
            await client.close()

    async def _tick(self, client: OKXClient, strategy: StrategyEngine, instruments: dict[str, Instrument]) -> None:
        tickers = await client.tickers()
        ticker_map = {row["instId"]: row for row in tickers if row.get("instId") in instruments}
        universe = self._select_universe(ticker_map)
        await self._manage_positions(client, ticker_map)
        self.last_scan = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if len(self.positions) >= self.config.max_concurrent_positions:
            return

        candidates: list[Signal] = []
        for inst_id in universe:
            if inst_id in self.positions or time.time() < self.cooldowns.get(inst_id, 0):
                continue
            df = await client.candles(inst_id, self.config.timeframe)
            higher = await client.candles(inst_id, self.config.confirm_timeframe)
            funding_rate, open_interest = await self._derivatives_context(client, inst_id)
            signal = strategy.signal(inst_id, df, higher, ticker_map[inst_id], funding_rate, open_interest)
            if signal:
                candidates.append(signal)

        for signal in sorted(candidates, key=lambda item: item.score, reverse=True):
            if len(self.positions) >= self.config.max_concurrent_positions:
                break
            await self._open_position(client, instruments[signal.inst_id], signal)

    def _select_universe(self, ticker_map: dict[str, dict[str, Any]]) -> list[str]:
        rows = []
        for inst_id, row in ticker_map.items():
            if _is_disallowed_symbol(inst_id):
                continue
            quote_volume = Decimal(str(row.get("volCcy24h") or row.get("vol24h") or "0"))
            rows.append((inst_id, quote_volume))
        rows.sort(key=lambda item: item[1], reverse=True)
        return [inst_id for inst_id, _ in rows[: self.config.top_symbols]]

    async def _derivatives_context(self, client: OKXClient, inst_id: str) -> tuple[Decimal | None, Decimal | None]:
        cached = self.derivatives_cache.get(inst_id)
        if cached and time.time() - cached[0] < 300:
            return cached[1], cached[2]
        funding_rate = await client.funding_rate(inst_id)
        open_interest = await client.open_interest(inst_id)
        self.derivatives_cache[inst_id] = (time.time(), funding_rate, open_interest)
        return funding_rate, open_interest

    async def _open_position(self, client: OKXClient, instrument: Instrument, signal: Signal) -> None:
        notional = self.config.order_margin_usdt * self.config.leverage
        contracts = (notional / (signal.price * instrument.ct_val)).quantize(instrument.lot_sz, rounding=ROUND_DOWN)
        if contracts < instrument.min_sz:
            self._log(f"{signal.inst_id}: tamaño calculado menor que mínimo.")
            return

        await client.set_leverage(signal.inst_id, self.config.leverage)
        await client.place_market_order(signal.inst_id, signal.side, contracts)
        risk = signal.atr * self.config.atr_stop_mult
        if signal.side == "long":
            stop = signal.price - risk
            take_profit = signal.price + risk * self.config.reward_risk
        else:
            stop = signal.price + risk
            take_profit = signal.price - risk * self.config.reward_risk

        self.positions[signal.inst_id] = ManagedPosition(
            inst_id=signal.inst_id,
            side=signal.side,
            size=contracts,
            entry=signal.price,
            stop=stop,
            take_profit=take_profit,
            atr=signal.atr,
            ct_val=instrument.ct_val,
            initial_risk=risk,
            opened_at=time.time(),
            best_price=signal.price,
            last_price=signal.price,
        )
        self._log(f"Apertura {signal.side} {signal.inst_id}: {signal.reason}, entry={signal.price}, stop={stop}, tp={take_profit}")

    async def _manage_positions(self, client: OKXClient, ticker_map: dict[str, dict[str, Any]]) -> None:
        for inst_id, pos in list(self.positions.items()):
            ticker = ticker_map.get(inst_id)
            if not ticker:
                continue
            price = Decimal(str(ticker.get("last", "0")))
            if price <= 0:
                continue
            pos.last_price = price
            await self._manage_single_position(client, pos, price)

    async def _manage_single_position(self, client: OKXClient, pos: ManagedPosition, price: Decimal) -> None:
        risk = pos.initial_risk
        if risk <= 0:
            return
        favorable = price - pos.entry if pos.side == "long" else pos.entry - price
        r_multiple = favorable / risk
        pos.best_price = max(pos.best_price, price) if pos.side == "long" else min(pos.best_price, price)

        if not pos.break_even_done and r_multiple >= self.config.break_even_trigger_r:
            lock = risk * self.config.break_even_lock_r
            pos.stop = pos.entry + lock if pos.side == "long" else pos.entry - lock
            pos.break_even_done = True
            self._log(f"{pos.inst_id}: break-even mejorado, stop={pos.stop}")

        if not pos.trailing_active and r_multiple >= self.config.trailing_trigger_r:
            lock = risk * self.config.trailing_lock_r
            pos.stop = pos.entry + lock if pos.side == "long" else pos.entry - lock
            pos.trailing_active = True
            self._log(f"{pos.inst_id}: trailing activado, TP lógico desactivado, stop={pos.stop}")

        if pos.trailing_active:
            trail_distance = pos.atr * self.config.atr_stop_mult
            candidate = price - trail_distance if pos.side == "long" else price + trail_distance
            pos.stop = max(pos.stop, candidate) if pos.side == "long" else min(pos.stop, candidate)

        hit_stop = price <= pos.stop if pos.side == "long" else price >= pos.stop
        hit_tp = (price >= pos.take_profit if pos.side == "long" else price <= pos.take_profit) and not pos.trailing_active
        if hit_stop or hit_tp:
            await client.close_position(pos.inst_id, pos.side)
            outcome = "stop/trailing" if hit_stop else "take-profit"
            self.closed_trades.append(
                TradeRecord(
                    inst_id=pos.inst_id,
                    side=pos.side,
                    entry=pos.entry,
                    exit_price=price,
                    pnl=_position_pnl(pos, price),
                    reason=outcome,
                    closed_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                )
            )
            self.closed_trades = self.closed_trades[-100:]
            self._log(f"Cierre {outcome} {pos.side} {pos.inst_id} a precio aprox {price}")
            self.cooldowns[pos.inst_id] = time.time() + self.config.cooldown_minutes * 60
            self.positions.pop(pos.inst_id, None)

    def _missing_secrets(self) -> list[str]:
        checks = {
            "OKX_API_KEY": self.config.api_key,
            "OKX_API_SECRET": self.config.api_secret,
            "OKX_API_PASSPHRASE": self.config.api_passphrase,
        }
        return [key for key, value in checks.items() if not value]

    def _log(self, message: str) -> None:
        with self._lock:
            stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
            self.log.append(f"{stamp} UTC | {message}")
            self.log = self.log[-200:]


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema_20"] = out["close"].ewm(span=20, adjust=False).mean()
    out["ema_50"] = out["close"].ewm(span=50, adjust=False).mean()
    out["ema_100"] = out["close"].ewm(span=100, adjust=False).mean()
    out["donchian_upper"] = out["high"].rolling(20).max().shift(1)
    out["donchian_lower"] = out["low"].rolling(20).min().shift(1)
    out["donchian_mid"] = (out["donchian_upper"] + out["donchian_lower"]) / 2
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    up_move = out["high"].diff()
    down_move = -out["low"].diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=out.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=out.index)
    atr_for_di = out["atr"].replace(0, np.nan)
    out["plus_di"] = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_for_di
    out["minus_di"] = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_for_di
    dx = 100 * (out["plus_di"] - out["minus_di"]).abs() / (out["plus_di"] + out["minus_di"]).replace(0, np.nan)
    out["adx"] = dx.ewm(alpha=1 / 14, adjust=False).mean()
    delta = out["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi"] = 100 - (100 / (1 + rs))
    out.loc[(loss == 0) & (gain > 0), "rsi"] = 100
    out.loc[(gain == 0) & (loss > 0), "rsi"] = 0
    out["rsi"] = out["rsi"].fillna(50)
    volume_ma = out["volume"].rolling(30).mean()
    volume_std = out["volume"].rolling(30).std(ddof=0).replace(0, np.nan)
    out["volume_z"] = ((out["volume"] - volume_ma) / volume_std).fillna(0)
    typical = (out["high"] + out["low"] + out["close"]) / 3
    out["vwap"] = (typical * out["volume"]).rolling(48).sum() / out["volume"].rolling(48).sum()
    return out.dropna().reset_index(drop=True)


def _percentile_rank(values: np.ndarray, value: float) -> float:
    clean = values[~np.isnan(values)]
    if len(clean) == 0:
        return 1.0
    return float((clean <= value).sum() / len(clean))


def _spread_bps(ticker: dict[str, Any]) -> Decimal | None:
    bid = Decimal(str(ticker.get("bidPx") or "0"))
    ask = Decimal(str(ticker.get("askPx") or "0"))
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / Decimal("2")
    return ((ask - bid) / mid) * Decimal("10000")


def _position_pnl(position: ManagedPosition, price: Decimal | None = None) -> Decimal:
    mark = price if price is not None and price > 0 else position.last_price
    if mark <= 0:
        return Decimal("0")
    raw = (mark - position.entry) * position.size * position.ct_val
    return raw if position.side == "long" else -raw


def _position_row(position: ManagedPosition) -> str:
    pnl = _position_pnl(position)
    status = "TRAILING" if position.trailing_active else "LIVE MONITOR"
    side_class = "pos" if position.side == "long" else "neg"
    return f"""
<tr>
  <td>{_esc(position.inst_id.replace("-USDT-SWAP", "USDT"))}</td>
  <td class="{side_class}">{position.side.upper()}</td>
  <td>{_fmt_decimal(position.entry)}</td>
  <td>{_fmt_decimal(position.last_price)}</td>
  <td>{_fmt_decimal(position.stop)}</td>
  <td class="{_pnl_class(pnl)}">{_fmt_money(pnl)}</td>
  <td><span class="tag">{status}</span></td>
</tr>
"""


def _trade_card(trade: TradeRecord) -> str:
    side_class = "pos" if trade.side == "long" else "neg"
    return f"""
<div class="trade-card">
  <div class="coin">{_esc(trade.inst_id.split("-")[0][:2])}</div>
  <div>
    <strong>{_esc(trade.inst_id.replace("-USDT-SWAP", "USDT"))} <span class="{side_class}">{trade.side.upper()}</span></strong>
    <small>E {_fmt_decimal(trade.entry)} | S {_fmt_decimal(trade.exit_price)}</small>
  </div>
  <div>
    <small>CAUSA DE CIERRE</small>
    <strong>{_esc(trade.reason.upper())}</strong>
  </div>
  <b class="{_pnl_class(trade.pnl)}">{_fmt_money(trade.pnl)}</b>
</div>
"""


def _market_bias(positions: list[ManagedPosition]) -> str:
    longs = sum(1 for item in positions if item.side == "long")
    shorts = sum(1 for item in positions if item.side == "short")
    if longs > shorts:
        return "LARGO"
    if shorts > longs:
        return "CORTO"
    return "NEUTRAL"


def _pnl_class(value: Decimal) -> str:
    return "pos" if value >= 0 else "neg"


def _fmt_money(value: Decimal) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value.quantize(Decimal('0.01'))}"


def _esc(value: object) -> str:
    return html.escape(str(value))


def _is_disallowed_symbol(inst_id: str) -> bool:
    base = inst_id.split("-")[0].upper()
    banned_exact = {"XAUT", "PAXG", "GLD", "SLV", "TSLA", "AAPL", "NVDA", "MSTR", "COIN", "AMZN", "GOOGL", "META", "MSFT"}
    banned_fragments = ("STOCK", "EQUITY", "GOLD", "SILVER")
    return base in banned_exact or any(fragment in inst_id.upper() for fragment in banned_fragments)


def _fmt_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


APP_CSS = """
:root {
  --bg: #050607;
  --panel: #101216;
  --panel-2: #0b0d10;
  --line: #242832;
  --text: #f5f8ff;
  --muted: #8e96a6;
  --cyan: #00e5ff;
  --green: #00ff9d;
  --red: #ff3b4f;
  --violet: #8f5cff;
  --gold: #ffd166;
}
body, .gradio-container {
  background: radial-gradient(circle at 12% 0%, rgba(0, 229, 255, 0.08), transparent 30%), var(--bg) !important;
  color: var(--text) !important;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
}
.gradio-container { max-width: none !important; padding: 24px 28px !important; }
footer, .api-docs, .built-with { display: none !important; }
button.show-api { display: none !important; }
.control-row { display: flex !important; justify-content: flex-end !important; gap: 12px !important; margin: 0 0 18px !important; }
button {
  border-radius: 8px !important;
  border: 1px solid var(--line) !important;
  background: #151820 !important;
  color: var(--text) !important;
  font-weight: 900 !important;
  text-transform: uppercase;
  letter-spacing: 0 !important;
}
.terminal-shell { width: 100%; }
.topbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 28px; }
.brand { display: flex; align-items: center; gap: 14px; }
.bolt {
  width: 48px; height: 48px; border-radius: 14px; display: grid; place-items: center;
  background: linear-gradient(135deg, #2aa9ff, #00e5ff); color: white; font-weight: 950;
  box-shadow: 0 0 26px rgba(0, 229, 255, 0.35);
}
.brand-name { font-size: 22px; font-weight: 950; color: var(--cyan); text-shadow: 0 0 22px rgba(0, 229, 255, 0.55); }
.badges { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.badges span, .status-pill, .tag {
  border: 1px solid rgba(0, 229, 255, 0.28); color: var(--cyan); background: rgba(0, 229, 255, 0.08);
  border-radius: 999px; padding: 4px 8px; font-size: 10px; font-weight: 900;
}
.badges span:nth-child(2) { color: var(--gold); border-color: rgba(255, 209, 102, 0.35); background: rgba(255, 209, 102, 0.08); }
.status-pill { padding: 10px 16px; }
.status-pill.ok { color: var(--green); border-color: rgba(0,255,157,.35); background: rgba(0,255,157,.08); }
.status-pill.warn { color: var(--red); border-color: rgba(255,59,79,.35); background: rgba(255,59,79,.08); }
.grid { display: grid; gap: 18px; }
.hero-grid { grid-template-columns: 1fr 1fr 1.05fr; }
.stat-grid { grid-template-columns: repeat(4, 1fr); margin-top: 18px; }
.main-grid { grid-template-columns: 2fr 1fr; margin-top: 18px; }
.lower-grid { grid-template-columns: 1.25fr 1fr; margin-top: 18px; }
.card, .stat-card {
  background: linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,.018)), var(--panel);
  border: 1px solid var(--line); border-radius: 12px; padding: 24px; box-shadow: 0 18px 44px rgba(0,0,0,.34);
}
.balance-card, .strategy-card, .bias-card { min-height: 170px; }
.label, .section-head span, th, small { color: var(--text); font-size: 11px; font-weight: 950; letter-spacing: 0; }
.big { font-size: 30px; font-weight: 950; margin-top: 42px; }
.sub, .muted { color: var(--muted); font-size: 12px; font-weight: 750; margin-top: 8px; }
.mini { margin-top: 18px; font-size: 12px; font-weight: 950; }
.trend { color: var(--green); font-size: 38px; font-weight: 950; margin-top: 46px; }
.kv { display: flex; justify-content: space-between; margin-top: 13px; font-size: 12px; font-weight: 900; }
.kv b { color: var(--cyan); }
.stat-card { min-height: 110px; }
.stat-card div { font-size: 11px; font-weight: 950; margin-bottom: 14px; }
.stat-card strong { display:block; font-size: 28px; font-weight: 950; }
.stat-card small { color: var(--muted); display:block; margin-top: 8px; }
.accent-a { border-color: rgba(143,92,255,.5); }
.accent-b { border-color: rgba(0,229,255,.42); }
.accent-c { border-color: rgba(255,255,255,.35); }
.pos { color: var(--green) !important; }
.neg { color: var(--red) !important; }
.center { text-align: center; }
.section-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:18px; }
.section-head b { color: var(--cyan); background: rgba(0,229,255,.1); padding: 5px 9px; border-radius: 999px; font-size: 10px; }
table { width: 100%; border-collapse: collapse; overflow: hidden; }
th, td { padding: 15px 12px; border-bottom: 1px solid rgba(255,255,255,.06); text-align:left; font-size: 12px; font-weight: 850; }
thead { background: var(--panel-2); }
.bar { height: 10px; background:#1b1f28; border-radius:999px; overflow:hidden; margin: 18px 0 24px; }
.bar span { display:block; height:100%; background: linear-gradient(90deg, var(--green), var(--cyan)); }
.perf-grid { display:grid; grid-template-columns:1fr 1fr; gap:22px 18px; }
.perf-grid strong { display:block; font-size: 24px; margin-top: 6px; }
.trade-list { display:flex; flex-direction:column; gap:12px; }
.trade-card {
  border: 1px solid var(--line); border-radius: 10px; padding: 14px; display:grid;
  grid-template-columns: 42px 1.4fr 1fr auto; align-items:center; gap:14px; background:#0d0f13;
}
.coin { width:34px; height:34px; border-radius:6px; display:grid; place-items:center; background:#030405; color:var(--text); font-weight:950; }
.trade-card small { color: var(--muted); display:block; margin-top:5px; }
.terminal {
  background:#030405; border-radius:10px; border:1px solid #151922; padding:16px; min-height:250px;
  font-family: "Cascadia Mono", Consolas, monospace; color:#b9ffdf; font-size:11px; line-height:1.65; overflow:auto;
}
.term-prefix { color: var(--cyan); font-weight: 950; }
.empty { color: var(--muted); border:1px dashed var(--line); border-radius:10px; padding:18px; font-size:12px; font-weight:800; }
@media (max-width: 1100px) {
  .hero-grid, .main-grid, .lower-grid { grid-template-columns: 1fr; }
  .stat-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 700px) {
  .gradio-container { padding: 16px !important; }
  .topbar, .control-row { align-items: stretch; flex-direction: column; }
  .stat-grid { grid-template-columns: 1fr; }
  .brand-name { font-size: 18px; }
  .big, .trend { font-size: 26px; }
  .trade-card { grid-template-columns: 1fr; }
  table { min-width: 760px; }
  .positions-card { overflow-x: auto; }
}
"""


runtime = BotRuntime()
if runtime.config.autostart:
    runtime.start()


with gr.Blocks(title="OKX Quant Terminal", css=APP_CSS) as demo:
    with gr.Row(elem_classes=["control-row"]):
        start_btn = gr.Button("Iniciar OKX demo", variant="primary")
        stop_btn = gr.Button("Detener")
        refresh_btn = gr.Button("Actualizar")
    output = gr.HTML(runtime.dashboard_html())
    start_btn.click(lambda: runtime.start(), outputs=output)
    stop_btn.click(lambda: runtime.stop(), outputs=output)
    refresh_btn.click(lambda: runtime.dashboard_html(), outputs=output)
    if hasattr(gr, "Timer"):
        timer = gr.Timer(10)
        timer.tick(lambda: runtime.dashboard_html(), outputs=output)


if __name__ == "__main__":
    demo.launch()
