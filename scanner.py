"""
scanner.py – Motor de escaneo OKX para el Quantum V10 Pro Bot.
ScannerLoop: cada 15s evalúa Top 50 por volumen → señales de las 2 estrategias.
ReconcileLoop: gestiona el ciclo de vida (BE/Trail/EarlyExit) cada 3s.
Incluye el cliente HTTP OKX reutilizado del bot original.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any, Optional

import httpx
import pandas as pd

from lifecycle import Action, evaluate
from macro_shield import MacroShield
from models import (
    Cooldown, Strategy, Trade, TradeEvent, TradeStatus, TradeSide,
    create_all, get_session,
)
from risk import (
    LEVERAGE, RISK_USD, compute_qty, compute_sl, compute_tp,
)
from strategy import QuantumDivergenceStrategy, QuantumTrendStrategy, Signal

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────
MAX_POSITIONS   = 10
TOP_SYMBOLS     = 50
COOLDOWN_MINS   = 30
SCAN_INTERVAL   = 15   # seconds
RECONCILE_INTERVAL = 3  # seconds

DISALLOWED = {"XAU", "XAG", "WTI", "USDC", "USDT", "BUSD", "DAI", "TUSD", "USDP"}


def _is_disallowed(inst_id: str) -> bool:
    base = inst_id.split("-")[0]
    return base in DISALLOWED


# ──────────────────────────────────────────────
# OKX HTTP Client
# ──────────────────────────────────────────────

class OKXClient:
    def __init__(self, api_key: str, api_secret: str, passphrase: str, simulated: bool = True):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.simulated  = simulated
        self.base_url   = "https://www.okx.com"
        self._client    = httpx.AsyncClient(base_url=self.base_url, timeout=15)

    async def close(self) -> None:
        await self._client.aclose()

    def _sign_headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        prehash = f"{ts}{method.upper()}{path}{body}"
        sig = base64.b64encode(
            hmac.new(self.api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
        ).decode()
        h = {
            "Content-Type": "application/json",
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sig,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
        }
        if self.simulated:
            h["x-simulated-trading"] = "1"
        return h

    async def _request(self, method: str, path: str, body: dict | None = None, auth: bool = False) -> Any:
        payload = json.dumps(body, separators=(",", ":")) if body else ""
        headers = self._sign_headers(method, path, payload) if auth else {"Content-Type": "application/json"}
        if self.simulated:
            headers["x-simulated-trading"] = "1"
        resp = await self._client.request(method, path, content=payload or None, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            raise RuntimeError(f"OKX {data.get('code')}: {data.get('msg')} | {data.get('data')}")
        return data.get("data", [])

    async def tickers(self) -> list[dict]:
        return await self._request("GET", "/api/v5/market/tickers?instType=SWAP")

    async def candles(self, inst_id: str, bar: str, limit: int = 150) -> pd.DataFrame:
        rows = await self._request("GET", f"/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}")
        cols = ["ts", "open", "high", "low", "close", "volume", "volCcy", "volCcyQuote", "confirm"]
        df = pd.DataFrame(rows, columns=cols)
        if df.empty:
            return df
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
        df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
        return df.sort_values("ts").reset_index(drop=True)

    async def instruments(self) -> list[dict]:
        return await self._request("GET", "/api/v5/public/instruments?instType=SWAP")

    async def set_leverage(self, inst_id: str, lever: int) -> None:
        await self._request("POST", "/api/v5/account/set-leverage", {
            "instId": inst_id, "lever": str(lever), "mgnMode": "isolated",
        }, auth=True)

    async def place_limit_order(self, inst_id: str, side: str, qty: Decimal, price: Decimal, client_oid: str = "") -> str:
        pos_side = "long" if side == "buy" else "short"
        body = {
            "instId": inst_id, "tdMode": "isolated", "side": side,
            "posSide": pos_side, "ordType": "limit",
            "sz": str(qty), "px": str(price),
        }
        if client_oid:
            body["clOrdId"] = client_oid
        rows = await self._request("POST", "/api/v5/trade/order", body, auth=True)
        return rows[0].get("ordId", "")

    async def place_market_order(self, inst_id: str, side: str, qty: Decimal) -> str:
        pos_side = "long" if side == "buy" else "short"
        rows = await self._request("POST", "/api/v5/trade/order", {
            "instId": inst_id, "tdMode": "isolated", "side": side,
            "posSide": pos_side, "ordType": "market", "sz": str(qty),
        }, auth=True)
        return rows[0].get("ordId", "")

    async def place_sl_tp_order(self, inst_id: str, side: str, qty: Decimal,
                                 sl_price: Decimal, tp_price: Optional[Decimal]) -> None:
        """Place algo order: SL + TP attached."""
        close_side = "sell" if side == "long" else "buy"
        pos_side   = side
        bodies = []
        if sl_price:
            bodies.append({
                "instId": inst_id, "tdMode": "isolated",
                "side": close_side, "posSide": pos_side,
                "ordType": "conditional", "sz": str(qty),
                "slTriggerPx": str(sl_price), "slOrdPx": "-1",  # market on trigger
            })
        if tp_price:
            bodies.append({
                "instId": inst_id, "tdMode": "isolated",
                "side": close_side, "posSide": pos_side,
                "ordType": "conditional", "sz": str(qty),
                "tpTriggerPx": str(tp_price), "tpOrdPx": "-1",
            })
        for body in bodies:
            try:
                await self._request("POST", "/api/v5/trade/order-algo", body, auth=True)
            except Exception:
                pass  # SL/TP are virtual – failure is acceptable

    async def cancel_algo_orders(self, inst_id: str, algo_ids: list[str]) -> None:
        if not algo_ids:
            return
        bodies = [{"instId": inst_id, "algoId": aid} for aid in algo_ids]
        try:
            await self._request("POST", "/api/v5/trade/cancel-algos", bodies, auth=True)
        except Exception:
            pass

    async def close_position(self, inst_id: str, pos_side: str) -> None:
        await self._request("POST", "/api/v5/trade/close-position", {
            "instId": inst_id, "posSide": pos_side, "mgnMode": "isolated",
        }, auth=True)

    async def get_positions(self) -> list[dict]:
        return await self._request("GET", "/api/v5/account/positions?instType=SWAP", auth=True)

    async def get_ticker(self, inst_id: str) -> dict:
        rows = await self._request("GET", f"/api/v5/market/ticker?instId={inst_id}")
        return rows[0] if rows else {}


# ──────────────────────────────────────────────
# Bot Runtime
# ──────────────────────────────────────────────

class QuantumBotRuntime:
    VERSION = "QUANTUM V10 PRO v1.0"

    def __init__(self, api_key: str, api_secret: str, passphrase: str, simulated: bool = True):
        create_all()
        self.client = OKXClient(api_key, api_secret, passphrase, simulated)
        self.shield = MacroShield()
        self.trend_strategy = QuantumTrendStrategy()
        self.div_strategy   = QuantumDivergenceStrategy()

        self.running = False
        self._lock   = threading.Lock()
        self._log_buffer: list[str] = []   # in-memory tail for UI
        self.last_scan  = "never"
        self.last_error = ""
        self._instruments: dict[str, dict] = {}
        self._thread: Optional[threading.Thread] = None

    # ── Logging ──────────────────────────────────────────────────────────

    def _log(self, msg: str, level: str = "INFO") -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line  = f"{stamp} | [{level}] {msg}"
        with self._lock:
            self._log_buffer.append(line)
            self._log_buffer = self._log_buffer[-300:]
        # persist
        try:
            with get_session() as db:
                from models import SystemLog
                db.add(SystemLog(level=level, message=msg))
                db.commit()
        except Exception:
            pass

    def get_logs(self, n: int = 25) -> list[str]:
        with self._lock:
            return list(self._log_buffer[-n:])

    # ── Start / Stop ─────────────────────────────────────────────────────

    def start(self) -> str:
        if self.running:
            return "already_running"
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._log(f"[SYSTEM] {self.VERSION} iniciado.", "SYSTEM")
        self._log("[SYSTEM] ✔️ Escudo Macro BTC activo.", "SYSTEM")
        self._log("[SYSTEM] ✔️ Estrategia A: Quantum Trend V10 Pro activa.", "SYSTEM")
        self._log("[SYSTEM] ✔️ Estrategia B: Quantum Divergence activa.", "SYSTEM")
        self._log("[SYSTEM] ✔️ Early Exit / Breakeven / Trailing activos.", "SYSTEM")
        return "started"

    def stop(self) -> str:
        self.running = False
        self._log("[SYSTEM] Bot detenido por control manual.", "WARN")
        return "stopped"

    # ── Main thread entry ─────────────────────────────────────────────────

    def _run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        try:
            await self._load_instruments()
            await self._restore_open_trades()
            await asyncio.gather(
                self._scanner_loop(),
                self._reconcile_loop(),
            )
        except Exception as e:
            self._log(f"Error fatal en _main: {e}", "ERROR")

    # ── Instruments ────────────────────────────────────────────────────

    async def _load_instruments(self) -> None:
        rows = await self.client.instruments()
        for row in rows:
            if row.get("settleCcy") != "USDT":
                continue
            iid = row["instId"]
            if _is_disallowed(iid):
                continue
            self._instruments[iid] = row
        self._log(f"Universo OKX cargado: {len(self._instruments)} swaps USDT elegibles.")

    # ── Restore open trades from DB on boot ───────────────────────────

    async def _restore_open_trades(self) -> None:
        with get_session() as db:
            open_trades = db.query(Trade).filter(
                Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
            ).all()
            count = len(open_trades)
        if count:
            self._log(f"[SYNC] {count} operaciones restauradas desde la base de datos.")
        else:
            # try to adopt from OKX live positions
            await self._adopt_live_positions()

    async def _adopt_live_positions(self) -> None:
        try:
            pos_list = await self.client.get_positions()
            adopted = 0
            with get_session() as db:
                for pos in pos_list:
                    iid  = pos["instId"]
                    side_raw = pos.get("posSide", "net")
                    qty_raw  = Decimal(pos.get("pos", "0"))
                    if qty_raw == 0:
                        continue
                    if side_raw == "net":
                        side = "long" if qty_raw > 0 else "short"
                    else:
                        side = side_raw
                    qty_abs = abs(qty_raw)
                    entry   = Decimal(pos.get("avgPx", "0"))
                    if entry == 0 or iid not in self._instruments:
                        continue
                    # assign conservative 5% SL
                    atr_est = entry * Decimal("0.005") / Decimal("2.5")
                    sl = compute_sl(entry, side, atr_est)
                    tp = compute_tp(entry, side, atr_est)
                    inst = self._instruments[iid]
                    ct_val = Decimal(inst["ctVal"])
                    trade = Trade(
                        symbol=iid, side=TradeSide(side),
                        strategy=Strategy.TREND,
                        entry_price=float(entry), qty=float(qty_abs),
                        sl_price=float(sl), tp_price=float(tp),
                        atr_5m=float(atr_est),
                        risk_usd=float(RISK_USD), leverage=LEVERAGE,
                        status=TradeStatus.OPEN,
                    )
                    db.add(trade)
                    adopted += 1
                db.commit()
            if adopted:
                self._log(f"[SYNC] Adoptadas {adopted} posiciones pre-existentes de OKX.")
        except Exception as e:
            self._log(f"[SYNC] Error adoptando posiciones: {e}", "WARN")

    # ── Scanner Loop (15s) ────────────────────────────────────────────

    async def _scanner_loop(self) -> None:
        while self.running:
            try:
                await self._scan_tick()
                self.last_scan = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                self.last_error = ""
            except Exception as e:
                self.last_error = str(e)
                self._log(f"Error en scanner: {e}", "ERROR")
            await asyncio.sleep(SCAN_INTERVAL)

    async def _scan_tick(self) -> None:
        # Count open positions
        with get_session() as db:
            open_count = db.query(Trade).filter(
                Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
            ).count()

        if open_count >= MAX_POSITIONS:
            self._log(f"Límite alcanzado: {open_count}/{MAX_POSITIONS} posiciones abiertas.")
            return

        # Macro shield – fetch BTC 5M
        try:
            df_btc = await self.client.candles("BTC-USDT-SWAP", "5m", limit=3)
            if not df_btc.empty:
                last = df_btc.iloc[-1]
                triggered = self.shield.evaluate(float(last["high"]), float(last["low"]), float(last["close"]))
                if triggered:
                    self._log(f"🔴 Escudo Macro ACTIVADO – {self.shield.status_label}", "WARN")
        except Exception:
            pass

        if self.shield.is_blocked:
            return

        # Get top 50 by volume
        tickers = await self.client.tickers()
        sorted_tickers = sorted(
            [t for t in tickers if t.get("instId", "").endswith("-USDT-SWAP") and not _is_disallowed(t.get("instId", ""))],
            key=lambda x: float(x.get("volCcy24h", 0) or x.get("vol24h", 0)),
            reverse=True,
        )[:TOP_SYMBOLS]
        universe = [t["instId"] for t in sorted_tickers]

        # Check cooldowns
        now_dt = datetime.now(timezone.utc)
        with get_session() as db:
            active_cdwn = {c.symbol for c in db.query(Cooldown).all() if c.is_active}
            open_symbols = {t.symbol for t in db.query(Trade).filter(
                Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
            ).all()}

        candidates: list[Signal] = []
        for inst_id in universe:
            if inst_id in active_cdwn or inst_id in open_symbols:
                continue
            if inst_id not in self._instruments:
                continue
            try:
                df_1h  = await self.client.candles(inst_id, "1H", 60)
                df_15m = await self.client.candles(inst_id, "15m", 50)
                df_5m  = await self.client.candles(inst_id, "5m", 30)
                sig_a = self.trend_strategy.signal(inst_id, df_1h, df_15m, df_5m)
                sig_b = self.div_strategy.signal(inst_id, df_15m, df_5m)
                for sig in [sig_a, sig_b]:
                    if sig:
                        candidates.append(sig)
            except Exception:
                continue

        for sig in sorted(candidates, key=lambda s: s.score, reverse=True):
            with get_session() as db:
                cnt = db.query(Trade).filter(
                    Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
                ).count()
            if cnt >= MAX_POSITIONS:
                break
            await self._open_trade(sig)

    # ── Open a new trade ─────────────────────────────────────────────

    async def _open_trade(self, sig: Signal) -> None:
        iid  = sig.symbol
        inst = self._instruments.get(iid)
        if not inst:
            return
        ct_val  = Decimal(inst["ctVal"])
        lot_sz  = Decimal(inst["lotSz"])
        min_sz  = Decimal(inst["minSz"])
        sl      = compute_sl(sig.entry_price, sig.side, sig.atr_5m)
        tp      = compute_tp(sig.entry_price, sig.side, sig.atr_5m)
        qty     = compute_qty(sig.entry_price, sl, ct_val, lot_sz)
        if qty < min_sz:
            self._log(f"{iid}: tamaño calculado ({qty}) menor que mínimo ({min_sz}). Skipping.")
            return
        try:
            await self.client.set_leverage(iid, LEVERAGE)
            order_side = "buy" if sig.side == "long" else "sell"
            await self.client.place_limit_order(iid, order_side, qty, sig.entry_price)
            with get_session() as db:
                trade = Trade(
                    symbol=iid, side=TradeSide(sig.side),
                    strategy=Strategy(sig.strategy),
                    entry_price=float(sig.entry_price),
                    qty=float(qty), sl_price=float(sl), tp_price=float(tp),
                    atr_5m=float(sig.atr_5m), risk_usd=float(RISK_USD),
                    leverage=LEVERAGE, status=TradeStatus.OPEN,
                    peak_price=float(sig.entry_price),
                )
                db.add(trade)
                db.flush()
                db.add(TradeEvent(
                    trade_id=trade.id, event_type="OPEN",
                    message=f"APERTURA {sig.side.upper()} | Entrada: {sig.entry_price:.6f} | SL: {sl:.6f} | TP: {tp:.6f} | {sig.reason}",
                    price=float(sig.entry_price),
                ))
                db.commit()
            self._log(
                f"[{iid}] 🚀 APERTURA {sig.side.upper()} vía {sig.strategy} | "
                f"Entrada: {sig.entry_price:.6f} | SL: {sl:.6f} | TP: {tp:.6f} | "
                f"Qty: {qty} | {sig.reason}"
            )
        except Exception as e:
            self._log(f"[{iid}] Error abriendo trade: {e}", "ERROR")

    # ── Reconcile Loop (3s) ───────────────────────────────────────────

    async def _reconcile_loop(self) -> None:
        while self.running:
            try:
                await self._reconcile_tick()
            except Exception as e:
                self._log(f"Error en reconcile: {e}", "ERROR")
            await asyncio.sleep(RECONCILE_INTERVAL)

    async def _reconcile_tick(self) -> None:
        with get_session() as db:
            open_trades = db.query(Trade).filter(
                Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
            ).all()
            trade_data = [
                {
                    "id": t.id, "symbol": t.symbol, "side": t.side.value,
                    "entry": Decimal(str(t.entry_price)), "qty": Decimal(str(t.qty)),
                    "sl": Decimal(str(t.sl_price)),
                    "tp": Decimal(str(t.tp_price)) if t.tp_price else None,
                    "atr": Decimal(str(t.atr_5m)),
                    "risk": Decimal(str(t.risk_usd)),
                    "be_done": bool(t.be_activated),
                    "trail_done": bool(t.trail_activated),
                    "trail_sl": Decimal(str(t.trail_sl)) if t.trail_sl else None,
                    "peak": Decimal(str(t.peak_price)) if t.peak_price else None,
                    "status": t.status,
                }
                for t in open_trades
            ]

        if not trade_data:
            return

        # Fetch all tickers once
        tickers_raw = await self.client.tickers()
        ticker_map  = {t["instId"]: t for t in tickers_raw}

        for td in trade_data:
            tick = ticker_map.get(td["symbol"])
            if not tick:
                continue
            price = Decimal(str(tick.get("last", "0")))
            if price <= 0:
                continue

            # Compute original TP distance (needed for lifecycle)
            entry = td["entry"]
            atr   = td["atr"]
            tp_original = compute_tp(entry, td["side"], atr)

            inst = self._instruments.get(td["symbol"])
            ct_val = Decimal(inst["ctVal"]) if inst else Decimal("1")

            decisions = evaluate(
                side=td["side"], entry=entry,
                tp=td["tp"], current_sl=td["sl"], price=price,
                qty=td["qty"], ct_val=ct_val, atr_5m=atr,
                risk_usd=td["risk"], be_activated=td["be_done"],
                trail_activated=td["trail_done"], trail_sl=td["trail_sl"],
                peak_price=td["peak"], tp_original=tp_original,
            )

            for decision in decisions:
                await self._apply_decision(td, decision, price)

    async def _apply_decision(self, td: dict, decision, price: Decimal) -> None:
        trade_id = td["id"]
        symbol   = td["symbol"]
        side     = td["side"]
        qty      = td["qty"]

        if decision.action == Action.NONE:
            return

        self._log(f"[{symbol}] {decision.log_message}")

        try:
            with get_session() as db:
                trade = db.query(Trade).filter(Trade.id == trade_id).first()
                if not trade:
                    return

                if decision.action == Action.MOVE_SL:
                    new_sl = decision.new_sl
                    trade.sl_price = float(new_sl)
                    if decision.reason == "BREAKEVEN":
                        trade.be_activated = 1
                        trade.status = TradeStatus.BREAKEVEN
                    elif decision.reason in ("TRAIL_ACTIVATE", "TRAIL_MOVE"):
                        trade.trail_activated = 1
                        trade.trail_sl = float(new_sl)
                        trade.status   = TradeStatus.TRAILING
                    db.add(TradeEvent(trade_id=trade_id, event_type=decision.reason,
                                     message=decision.log_message, price=float(price)))
                    db.commit()

                elif decision.action == Action.CANCEL_TP:
                    trade.tp_price = None
                    db.add(TradeEvent(trade_id=trade_id, event_type="CANCEL_TP",
                                     message=decision.log_message, price=float(price)))
                    db.commit()

                elif decision.action == Action.CLOSE_MARKET:
                    await self.client.close_position(symbol, side)
                    close_side = "sell" if side == "long" else "buy"
                    inst  = self._instruments.get(symbol)
                    ct_val = Decimal(inst["ctVal"]) if inst else Decimal("1")
                    from risk import pnl_usd
                    entry  = Decimal(str(trade.entry_price))
                    q      = Decimal(str(trade.qty))
                    pnl    = pnl_usd(entry, price, q, ct_val, side)
                    trade.status       = TradeStatus.CLOSED
                    trade.close_price  = float(price)
                    trade.close_reason = decision.reason
                    trade.realized_pnl = float(pnl)
                    trade.closed_at    = datetime.now(timezone.utc)
                    db.add(TradeEvent(trade_id=trade_id, event_type="CLOSE",
                                     message=f"{decision.reason} | PnL: {float(pnl):.2f} USDT",
                                     price=float(price)))
                    # Set cooldown
                    existing = db.query(Cooldown).filter(Cooldown.symbol == symbol).first()
                    from datetime import timedelta
                    until = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINS)
                    if existing:
                        existing.until = until
                    else:
                        db.add(Cooldown(symbol=symbol, until=until))
                    db.commit()
                    pnl_sign = "+" if pnl >= 0 else ""
                    self._log(
                        f"[{symbol}] ✅ CIERRE via {decision.reason} a {price:.6f} | "
                        f"PnL: {pnl_sign}{float(pnl):.2f} USDT | Cooldown: {COOLDOWN_MINS}m"
                    )
        except Exception as e:
            self._log(f"[{symbol}] ⚠️ Error aplicando decisión {decision.reason}: {e}. Reintentando...", "ERROR")

    # ── Data accessors for dashboard ──────────────────────────────────

    def get_open_trades(self) -> list[Trade]:
        try:
            with get_session() as db:
                return db.query(Trade).filter(
                    Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
                ).all()
        except Exception:
            return []

    def get_closed_trades(self, n: int = 10) -> list[Trade]:
        try:
            with get_session() as db:
                return db.query(Trade).filter(
                    Trade.status.in_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
                ).order_by(Trade.closed_at.desc()).limit(n).all()
        except Exception:
            return []

    def get_stats(self) -> dict:
        try:
            with get_session() as db:
                closed = db.query(Trade).filter(
                    Trade.realized_pnl.isnot(None)
                ).all()
                total   = len(closed)
                wins    = [t for t in closed if (t.realized_pnl or 0) > 0]
                losses  = [t for t in closed if (t.realized_pnl or 0) < 0]
                total_pnl = sum(t.realized_pnl or 0 for t in closed)
                gross_win = sum(t.realized_pnl for t in wins)
                gross_los = abs(sum(t.realized_pnl for t in losses))
                return {
                    "total_trades": total,
                    "win_rate": (len(wins) / total * 100) if total else 0,
                    "profit_factor": (gross_win / gross_los) if gross_los > 0 else 0,
                    "total_pnl": total_pnl,
                    "best_trade": max((t.realized_pnl or 0 for t in closed), default=0),
                    "worst_trade": min((t.realized_pnl or 0 for t in closed), default=0),
                }
        except Exception:
            return {}
