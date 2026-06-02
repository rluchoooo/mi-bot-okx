from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
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
    initial_risk: Decimal
    opened_at: float
    break_even_done: bool = False
    trailing_active: bool = False
    best_price: Decimal = Decimal("0")


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

        bandwidth_rank = _percentile_rank(frame["bb_width"].tail(80).to_numpy(), row.bb_width)
        volume_z = row.volume_z
        was_ttm_squeeze = bool(prev.squeeze_on) or frame["squeeze_on"].tail(8).any()
        squeeze_release = (was_ttm_squeeze and not bool(row.squeeze_on)) or (bandwidth_rank < 0.30 and row.bb_width > prev.bb_width)
        if not squeeze_release or volume_z < 0.35:
            return None

        if funding_rate is not None and abs(funding_rate) > self.config.max_funding_abs:
            return None

        high_trend_up = high_row.close > high_row.ema_50 and high_row.ema_20 > high_row.ema_50
        high_trend_down = high_row.close < high_row.ema_50 and high_row.ema_20 < high_row.ema_50
        above_vwap = row.close > row.vwap
        below_vwap = row.close < row.vwap

        long_breakout = prev.close <= prev.bb_upper and row.close > row.bb_upper and row.close > row.ema_20
        short_breakout = prev.close >= prev.bb_lower and row.close < row.bb_lower and row.close < row.ema_20

        long_momentum_ok = 50 <= row.rsi <= 72 and row.plus_di > row.minus_di and row.close > row.kc_upper
        short_momentum_ok = 28 <= row.rsi <= 50 and row.minus_di > row.plus_di and row.close < row.kc_lower

        if funding_rate is not None:
            if funding_rate > self.config.avoid_one_way_funding:
                long_momentum_ok = False
            if funding_rate < -self.config.avoid_one_way_funding:
                short_momentum_ok = False

        oi_bonus = 0.15 if open_interest is not None and open_interest > 0 else 0.0
        adx_bonus = min((row.adx - self.config.min_adx) / 20, 0.75)
        compression_bonus = max(0.0, 0.30 - bandwidth_rank)
        base_score = float(volume_z + compression_bonus + adx_bonus + min(float(atr_pct * 100), 2.0) + oi_bonus)

        if long_breakout and high_trend_up and above_vwap and long_momentum_ok:
            reason = "TTM squeeze breakout + ADX/DI trend + RSI momentum + VWAP + volume + derivatives filter"
            return Signal(inst_id, "long", close, atr, base_score, reason)
        if short_breakout and high_trend_down and below_vwap and short_momentum_ok:
            reason = "TTM squeeze breakdown + ADX/DI trend + RSI momentum + VWAP + volume + derivatives filter"
            return Signal(inst_id, "short", close, atr, base_score, reason)
        return None


class BotRuntime:
    def __init__(self) -> None:
        self.config = BotConfig()
        self.log: list[str] = []
        self.positions: dict[str, ManagedPosition] = {}
        self.cooldowns: dict[str, float] = {}
        self.derivatives_cache: dict[str, tuple[float, Decimal | None, Decimal | None]] = {}
        self.running = False
        self.thread: threading.Thread | None = None
        self.last_scan = "never"
        self.last_error = ""
        self._lock = threading.Lock()

    def start(self) -> str:
        if self.running:
            return "Bot ya está activo."
        missing = self._missing_secrets()
        if missing:
            return f"Faltan secretos: {', '.join(missing)}"
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        self._log("Bot iniciado en OKX demo.")
        return "Bot iniciado en OKX demo."

    def stop(self) -> str:
        self.running = False
        self._log("Bot detenido por control manual.")
        return "Bot detenido. Las posiciones demo abiertas deben revisarse en OKX."

    def status_markdown(self) -> str:
        with self._lock:
            pos_lines = [
                f"- `{p.inst_id}` {p.side} size={p.size} entry={p.entry} stop={p.stop} tp={p.take_profit} trailing={p.trailing_active}"
                for p in self.positions.values()
            ]
            logs = "\n".join(f"- {line}" for line in self.log[-18:])
        return (
            f"### Estado\n"
            f"- Activo: `{self.running}`\n"
            f"- Modo demo: `{self.config.simulated}`\n"
            f"- Último escaneo: `{self.last_scan}`\n"
            f"- Error reciente: `{self.last_error or 'ninguno'}`\n"
            f"- Posiciones gestionadas: `{len(self.positions)}/{self.config.max_concurrent_positions}`\n\n"
            f"### Posiciones\n{chr(10).join(pos_lines) if pos_lines else '- ninguna'}\n\n"
            f"### Registro\n{logs if logs else '- sin eventos todavía'}"
        )

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
            initial_risk=risk,
            opened_at=time.time(),
            best_price=signal.price,
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
    out["bb_mid"] = out["close"].rolling(20).mean()
    std = out["close"].rolling(20).std(ddof=0)
    out["bb_upper"] = out["bb_mid"] + 2 * std
    out["bb_lower"] = out["bb_mid"] - 2 * std
    out["bb_width"] = (out["bb_upper"] - out["bb_lower"]) / out["bb_mid"]
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
    out["kc_mid"] = out["ema_20"]
    out["kc_upper"] = out["kc_mid"] + 1.5 * out["atr"]
    out["kc_lower"] = out["kc_mid"] - 1.5 * out["atr"]
    out["squeeze_on"] = (out["bb_upper"] < out["kc_upper"]) & (out["bb_lower"] > out["kc_lower"])
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


def _is_disallowed_symbol(inst_id: str) -> bool:
    base = inst_id.split("-")[0].upper()
    banned_exact = {"XAUT", "PAXG", "GLD", "SLV", "TSLA", "AAPL", "NVDA", "MSTR", "COIN", "AMZN", "GOOGL", "META", "MSFT"}
    banned_fragments = ("STOCK", "EQUITY", "GOLD", "SILVER")
    return base in banned_exact or any(fragment in inst_id.upper() for fragment in banned_fragments)


def _fmt_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


runtime = BotRuntime()
if runtime.config.autostart:
    runtime.start()


with gr.Blocks(title="OKX Demo Quant Bot") as demo:
    gr.Markdown("# OKX Demo Quant Bot")
    gr.Markdown(
        "Modo demo para futuros perpetuos OKX. Estrategia: Bollinger squeeze breakout, ATR, VWAP, volumen, tendencia 15m y control de exposición."
    )
    with gr.Row():
        start_btn = gr.Button("Iniciar demo", variant="primary")
        stop_btn = gr.Button("Detener")
        refresh_btn = gr.Button("Actualizar")
    output = gr.Markdown(runtime.status_markdown)
    start_btn.click(lambda: runtime.start(), outputs=output).then(lambda: runtime.status_markdown(), outputs=output)
    stop_btn.click(lambda: runtime.stop(), outputs=output).then(lambda: runtime.status_markdown(), outputs=output)
    refresh_btn.click(lambda: runtime.status_markdown(), outputs=output)
    if hasattr(gr, "Timer"):
        timer = gr.Timer(10)
        timer.tick(lambda: runtime.status_markdown(), outputs=output)


if __name__ == "__main__":
    demo.launch()
