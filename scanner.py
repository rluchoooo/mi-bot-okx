"""
scanner.py – Motor completo del Quantum V10 Pro Bot.
ScannerLoop (15s) + ReconcileLoop (30s) + REST API embebida (/status /diagnostics /trades).
Incluye: Telegram, stale order cleanup, macro shield reminders, reconcile retries.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

import httpx

from config import (
    COOLDOWN_MINUTES, DAILY_LOSS_LIMIT_USDT, DISALLOWED_BASES,
    FIXED_RISK_USDT, LEVERAGE, LIMIT_ORDER_OFFSET_PCT,
    MAX_CONCURRENT_TRADES, MIN_VOLUME_24H, RECONCILE_INTERVAL,
    RECONCILE_RETRY_SEC, SCAN_INTERVAL_SECONDS, STALE_ORDER_MINUTES,
    TOP_COINS_LIMIT, TRAIL_RETRY_SECONDS,
    EARLY_EXIT_VOL_MULT, EARLY_EXIT_LOOKBACK_MINUTES,
)
from lifecycle import Action, evaluate
from macro_shield import MacroShield
from models import (
    Cooldown, Strategy, Trade, TradeEvent, TradeStatus, TradeSide,
    create_all, get_session,
)
from notifier import notifier
from risk import (
    breakeven_sl, compute_qty, compute_sl, compute_tp,
    new_trail_sl, pnl_usd,
)
from strategy import QuantumDivergenceStrategy, QuantumTrendStrategy, Signal


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _is_disallowed(inst_id: str) -> bool:
    return inst_id.split("-")[0] in DISALLOWED_BASES


# ──────────────────────────────────────────────
# OKX HTTP Client
# ──────────────────────────────────────────────

class OKXClient:
    def __init__(self, api_key: str, api_secret: str, passphrase: str, simulated: bool = True):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.simulated  = simulated
        self._client    = httpx.AsyncClient(base_url="https://www.okx.com", timeout=15)

    async def close(self) -> None:
        await self._client.aclose()

    def _sign(self, method: str, path: str, body: str = "") -> dict[str, str]:
        ts  = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        pre = f"{ts}{method.upper()}{path}{body}"
        sig = base64.b64encode(
            hmac.new(self.api_secret.encode(), pre.encode(), hashlib.sha256).digest()
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

    async def _req(self, method: str, path: str, body: dict | None = None, auth: bool = False) -> Any:
        payload = json.dumps(body, separators=(",", ":")) if body else ""
        headers = self._sign(method, path, payload) if auth else {"Content-Type": "application/json"}
        if self.simulated:
            headers["x-simulated-trading"] = "1"
        r = await self._client.request(method, path, content=payload or None, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text}")
        data = r.json()
        if data.get("code") != "0":
            raise RuntimeError(f"OKX {data.get('code')}: {data.get('msg')} | {data.get('data')}")
        return data.get("data", [])

    async def tickers(self) -> list[dict]:
        return await self._req("GET", "/api/v5/market/tickers?instType=SWAP")

    async def ticker(self, inst_id: str) -> dict:
        rows = await self._req("GET", f"/api/v5/market/ticker?instId={inst_id}")
        return rows[0] if rows else {}

    async def candles(self, inst_id: str, bar: str, limit: int = 150) -> Any:
        import pandas as pd
        rows = await self._req("GET", f"/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}")
        cols = ["ts", "open", "high", "low", "close", "volume", "volCcy", "volCcyQuote", "confirm"]
        df = pd.DataFrame(rows, columns=cols)
        if df.empty:
            return df
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
        df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
        return df.sort_values("ts").reset_index(drop=True)

    async def instruments(self) -> list[dict]:
        return await self._req("GET", "/api/v5/public/instruments?instType=SWAP")

    async def set_leverage(self, inst_id: str, lever: int, pos_side: str) -> None:
        await self._req("POST", "/api/v5/account/set-leverage",
                        {"instId": inst_id, "lever": str(lever), "mgnMode": "isolated", "posSide": pos_side}, auth=True)

    async def place_limit_order(self, inst_id: str, side: str, qty: Decimal, price: Decimal, sl: Decimal = None, tp: Decimal = None) -> str:
        pos_side = "long" if side == "buy" else "short"
        payload = {
            "instId": inst_id, "tdMode": "isolated", "side": side,
            "posSide": pos_side, "ordType": "limit", "sz": str(qty), "px": str(price),
        }
        if sl:
            payload["slTriggerPx"] = str(sl)
            payload["slOrdPx"] = "-1"
        if tp:
            payload["tpTriggerPx"] = str(tp)
            payload["tpOrdPx"] = "-1"
            
        rows = await self._req("POST", "/api/v5/trade/order", payload, auth=True)
        return rows[0].get("ordId", "") if rows else ""

    async def cancel_algo_orders(self, inst_id: str) -> None:
        try:
            pending = []
            for o_type in ["oco", "conditional"]:
                res = await self._req("GET", f"/api/v5/trade/orders-algo-pending?instId={inst_id}&ordType={o_type}", auth=True)
                pending.extend(res)
            if pending:
                # Cancel in batches of 10 if necessary, but usually it's just 2 orders (SL and TP)
                payload = [{"instId": inst_id, "algoId": p["algoId"]} for p in pending[:10]]
                await self._req("POST", "/api/v5/trade/cancel-algos", payload, auth=True)
        except Exception as e:
            # Do not crash the loop if cancelling algo fails
            pass


    async def place_market_order(self, inst_id: str, side: str, qty: Decimal) -> str:
        pos_side = "long" if side == "buy" else "short"
        rows = await self._req("POST", "/api/v5/trade/order", {
            "instId": inst_id, "tdMode": "isolated", "side": side,
            "posSide": pos_side, "ordType": "market", "sz": str(qty),
        }, auth=True)
        return rows[0].get("ordId", "") if rows else ""

    async def cancel_order(self, inst_id: str, ord_id: str) -> None:
        try:
            await self._req("POST", "/api/v5/trade/cancel-order",
                            {"instId": inst_id, "ordId": ord_id}, auth=True)
        except Exception:
            pass

    async def get_order(self, inst_id: str, ord_id: str) -> dict:
        try:
            rows = await self._req("GET", f"/api/v5/trade/order?instId={inst_id}&ordId={ord_id}", auth=True)
            return rows[0] if rows else {}
        except Exception:
            return {}

    async def close_position(self, inst_id: str, pos_side: str) -> None:
        await self._req("POST", "/api/v5/trade/close-position",
                        {"instId": inst_id, "posSide": pos_side, "mgnMode": "isolated"}, auth=True)

    async def get_positions(self) -> list[dict]:
        return await self._req("GET", "/api/v5/account/positions?instType=SWAP", auth=True)

    async def get_balance(self) -> float:
        try:
            rows = await self._req("GET", "/api/v5/account/balance", auth=True)
            if rows and "totalEq" in rows[0]:
                return float(rows[0]["totalEq"])
        except Exception:
            pass
        return 0.0


# ──────────────────────────────────────────────
# Bot Runtime
# ──────────────────────────────────────────────

class QuantumBotRuntime:
    VERSION = "QUANTUM V10 PRO v1.1"

    def __init__(self, api_key: str, api_secret: str, passphrase: str, simulated: bool = True):
        create_all()
        self.api_key    = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.simulated  = simulated

        self.shield          = MacroShield()
        self.trend_strat     = QuantumTrendStrategy()
        self.div_strat       = QuantumDivergenceStrategy()

        self.running         = False
        self._lock           = threading.Lock()
        self._log_buffer:    list[str] = []
        self._instruments:   dict[str, dict] = {}
        self._pending_orders: dict[int, tuple[str, float]] = {}  # trade_id → (ord_id, ts)
        self.current_exchange_balance: float = 0.0

        self.last_scan       = "never"
        self.last_error      = ""
        self._thread: Optional[threading.Thread] = None

    def _new_client(self) -> OKXClient:
        return OKXClient(self.api_key, self.api_secret, self.passphrase, self.simulated)

    # ── Logging ──────────────────────────────────────────────────────

    def _log(self, msg: str, level: str = "INFO") -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line  = f"{stamp} | [{level}] {msg}"
        print(line, flush=True)  # <-- ESTO HACE QUE SE VEA EN LA PESTAÑA LOGS DE HUGGING FACE
        with self._lock:
            self._log_buffer.append(line)
            self._log_buffer = self._log_buffer[-300:]
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

    # ── Start / Stop ─────────────────────────────────────────────────

    def start(self) -> str:
        if self.running:
            return "already_running"
        self.running = True
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True)
        self._thread.start()
        self._log("MOTOR QUANTUM ENCENDIDO")
        return "started"

    def stop(self) -> str:
        self.running = False
        self._log("[SYSTEM] Bot detenido manualmente.", "WARN")
        return "stopped"

    async def _close_all_positions(self) -> None:
        client = self._new_client()
        try:
            with get_session() as db:
                from models import Trade, TradeStatus
                open_trades = db.query(Trade).filter(
                    Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
                ).all()
                for t in open_trades:
                    try:
                        self._log(f"[{t.symbol}] 🗑️ HARD RESET: Cerrando posición en OKX a mercado.", "WARN")
                        await client.close_position(t.symbol, t.side)
                        await client.cancel_algo_orders(t.symbol)
                    except Exception as e:
                        self._log(f"[{t.symbol}] HARD RESET error: {e}", "ERROR")
        finally:
            await client.close()

    def reset_database(self) -> str:
        # First stop the loop so it doesn't open new trades
        self.stop()
        # Then forcefully close all active ones in OKX synchronously blocking
        try:
            asyncio.run(self._close_all_positions())
        except Exception as e:
            self._log(f"Error closing positions: {e}", "ERROR")

        # Now wipe the database completely
        try:
            with get_session() as db:
                from models import Trade, TradeEvent, SystemLog, Cooldown
                db.query(TradeEvent).delete()
                db.query(Trade).delete()
                db.query(SystemLog).delete()
                db.query(Cooldown).delete()
                db.commit()
            self._log("🗑️ BASE DE DATOS Y ESTADÍSTICAS BORRADAS AL 100%.", "SYSTEM")
        except Exception as e:
            self._log(f"Error al resetear la base de datos: {e}", "ERROR")
        
        return "Reseteo Completado. Puedes Iniciar el Bot de Nuevo."

    # ── Main ─────────────────────────────────────────────────────────

    async def _main(self) -> None:
        client = self._new_client()
        try:
            await self._load_instruments(client)
            await self._restore_trades(client)
            await asyncio.gather(
                self._scanner_loop(client),
                self._reconcile_loop(client),
                self._stale_order_loop(client),
            )
        except Exception as e:
            self._log(f"Error fatal: {e}", "ERROR")
            await notifier.notify_error("_main", str(e))
        finally:
            await client.close()

    # ── Instruments ───────────────────────────────────────────────────

    async def _load_instruments(self, client: OKXClient) -> None:
        rows = await client.instruments()
        for r in rows:
            if r.get("settleCcy") != "USDT":
                continue
            iid = r["instId"]
            if _is_disallowed(iid):
                continue
            self._instruments[iid] = r
        self._log(f"Universo OKX cargado: {len(self._instruments)} swaps USDT.")

    # ── Restore / Adopt ───────────────────────────────────────────────

    async def _restore_trades(self, client: OKXClient) -> None:
        with get_session() as db:
            open_trades = db.query(Trade).filter(
                Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
            ).count()
        if open_trades:
            self._log(f"[SYNC] {open_trades} operaciones restauradas desde SQLite.")
        else:
            await self._adopt_live(client)

    async def _adopt_live(self, client: OKXClient) -> None:
        try:
            positions = await client.get_positions()
            count = 0
            with get_session() as db:
                for pos in positions:
                    iid = pos["instId"]
                    qty_raw = Decimal(pos.get("pos", "0"))
                    if qty_raw == 0 or iid not in self._instruments:
                        continue
                    side_raw = pos.get("posSide", "net")
                    side = "long" if qty_raw > 0 else "short" if side_raw == "net" else side_raw
                    entry  = Decimal(pos.get("avgPx", "0"))
                    if entry == 0:
                        continue
                    inst   = self._instruments[iid]
                    ct_val = Decimal(inst["ctVal"])
                    # Assign conservative SL (5% of price)
                    atr_est = entry * Decimal("0.005") / Decimal("2.5")
                    sl = compute_sl(entry, side, atr_est)
                    tp = compute_tp(entry, side, atr_est)
                    db.add(Trade(
                        symbol=iid, side=TradeSide(side), strategy=Strategy.TREND,
                        entry_price=float(entry), qty=float(abs(qty_raw)),
                        sl_price=float(sl), tp_price=float(tp), atr_5m=float(atr_est),
                        risk_usd=float(FIXED_RISK_USDT), leverage=LEVERAGE,
                        status=TradeStatus.OPEN, peak_price=float(entry),
                    ))
                    count += 1
                db.commit()
            if count:
                self._log(f"[SYNC] Adoptadas {count} posiciones pre-existentes de OKX.")
        except Exception as e:
            self._log(f"[SYNC] Error adoptando posiciones: {e}", "WARN")

    # ── Scanner Loop (15s) ────────────────────────────────────────────

    async def _scanner_loop(self, client: OKXClient) -> None:
        while self.running:
            try:
                await self._scan_tick(client)
                self.last_scan  = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                self.last_error = ""
            except Exception as e:
                self.last_error = str(e)
                self._log(f"Error en scanner: {e}", "ERROR")
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

    async def _scan_tick(self, client: OKXClient) -> None:
        # Daily loss check
        with get_session() as db:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            closed_today = db.query(Trade).filter(
                Trade.closed_at >= today_start,
                Trade.realized_pnl.isnot(None),
            ).all()
            daily_loss = sum(t.realized_pnl for t in closed_today if (t.realized_pnl or 0) < 0)
        if abs(daily_loss) >= float(DAILY_LOSS_LIMIT_USDT):
            self._log(f"🛑 Límite de pérdida diaria alcanzado: {daily_loss:.2f} USDT. Scanner pausado.", "WARN")
            return

        # Macro shield – BTC 15M
        try:
            df_btc = await client.candles("BTC-USDT-SWAP", "15m", limit=3)
            if not df_btc.empty:
                last = df_btc.iloc[-1]
                triggered = self.shield.evaluate(float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"]))
                if triggered:
                    shock_dir = self.shield.shock_direction
                    msg = f"🚨 ALARMA MACRO: {self.shield._last_trigger_reason} – BLOQUEANDO OPERACIONES POR 3 HORAS"
                    self._log(msg, "WARN")
                    await notifier.notify_macro_block(
                        self.shield._last_trigger_reason, self.shield.remaining_minutes
                    )
                    
                    # Kill Switch
                    with get_session() as db:
                        open_trades = db.query(Trade).filter(Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])).all()
                        for t in open_trades:
                            if (shock_dir == "bullish" and t.side == "short") or (shock_dir == "bearish" and t.side == "long"):
                                self._log(f"[{t.symbol}] ⚡ KILL SWITCH: Cerrando {t.side.upper()} en contra del shock {shock_dir.upper()}.", "WARN")
                                try:
                                    await client.close_position(t.symbol, t.side)
                                    t.status = TradeStatus.CLOSED
                                    t.close_reason = "MACRO_SHOCK_CUT"
                                    t.closed_at = datetime.now(timezone.utc)
                                    # Aplicar Cooldown de 30m
                                    until = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
                                    ex = db.query(Cooldown).filter(Cooldown.symbol == t.symbol).first()
                                    if ex:
                                        ex.until = until
                                    else:
                                        db.add(Cooldown(symbol=t.symbol, until=until))
                                    db.add(TradeEvent(trade_id=t.id, event_type="MACRO_SHOCK_CUT", message=msg))
                                except Exception as e:
                                    self._log(f"[{t.symbol}] Error en Kill Switch: {e}", "ERROR")
                        db.commit()

        except Exception as e:
            pass

        # Macro shield reminder every 60s
        if self.shield.should_send_reminder():
            reminder = self.shield.reminder_message()
            self._log(reminder, "WARN")

        if self.shield.is_blocked:
            return

        # Open position count
        with get_session() as db:
            open_cnt    = db.query(Trade).filter(Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])).count()
            active_syms = {t.symbol for t in db.query(Trade).filter(Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])).all()}
            cdwn_syms   = {c.symbol for c in db.query(Cooldown).all() if c.is_active}

        if open_cnt >= MAX_CONCURRENT_TRADES:
            return

        # Top 50 by volume
        tickers = await client.tickers()
        universe = sorted(
            [
                t for t in tickers
                if t.get("instId", "").endswith("-USDT-SWAP")
                and not _is_disallowed(t["instId"])
                and float(t.get("volCcy24h", 0) or t.get("vol24h", 0)) >= MIN_VOLUME_24H
            ],
            key=lambda x: float(x.get("volCcy24h", 0) or x.get("vol24h", 0)),
            reverse=True,
        )[:TOP_COINS_LIMIT]

        candidates: list[Signal] = []
        for tick in universe:
            iid = tick["instId"]
            if iid in active_syms or iid in cdwn_syms or iid not in self._instruments:
                continue
            try:
                df_1h, df_15m, df_5m = await asyncio.gather(
                    client.candles(iid, "1H", 60),
                    client.candles(iid, "15m", 50),
                    client.candles(iid, "5m", 30),
                )
                for sig in [
                    self.trend_strat.signal(iid, df_1h, df_15m, df_5m),
                    self.div_strat.signal(iid, df_15m, df_5m),
                ]:
                    if sig:
                        candidates.append(sig)
            except Exception:
                continue

        for sig in sorted(candidates, key=lambda s: s.score, reverse=True):
            with get_session() as db:
                if db.query(Trade).filter(Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])).count() >= MAX_CONCURRENT_TRADES:
                    break
            await self._open_trade(client, sig)

    async def _open_trade(self, client: OKXClient, sig: Signal) -> None:
        iid  = sig.symbol
        inst = self._instruments.get(iid)
        if not inst:
            return
        ct_val = Decimal(inst["ctVal"])
        lot_sz = Decimal(inst["lotSz"])
        min_sz = Decimal(inst["minSz"])
        sl     = compute_sl(sig.entry_price, sig.side, sig.atr_5m)
        tp     = compute_tp(sig.entry_price, sig.side, sig.atr_5m)
        qty    = compute_qty(sig.entry_price, sl, ct_val, lot_sz)
        if qty < min_sz:
            self._log(f"{iid}: qty {qty} < min {min_sz}. Skip.")
            return
        try:
            order_side = "buy" if sig.side == "long" else "sell"
            pos_side   = "long" if sig.side == "long" else "short"
            try:
                await client.set_leverage(iid, LEVERAGE, pos_side)
            except Exception as lev_err:
                # If it fails with posSide, fallback without posSide (for net mode)
                if "51000" in str(lev_err) or "posSide" in str(lev_err):
                    await client._req("POST", "/api/v5/account/set-leverage",
                                      {"instId": iid, "lever": str(LEVERAGE), "mgnMode": "isolated"}, auth=True)
                else:
                    raise lev_err
                
            ord_id = await client.place_limit_order(iid, order_side, qty, sig.entry_price, sl=sl, tp=tp)
            now_ts = time.time()
            with get_session() as db:
                trade = Trade(
                    symbol=iid, side=TradeSide(sig.side),
                    strategy=Strategy(sig.strategy),
                    entry_price=float(sig.entry_price), qty=float(qty),
                    sl_price=float(sl), tp_price=float(tp),
                    atr_5m=float(sig.atr_5m), risk_usd=float(FIXED_RISK_USDT),
                    leverage=LEVERAGE, status=TradeStatus.OPEN,
                    peak_price=float(sig.entry_price),
                )
                db.add(trade)
                db.flush()
                db.add(TradeEvent(
                    trade_id=trade.id, event_type="OPEN",
                    message=f"Orden {ord_id} | {sig.reason}",
                    price=float(sig.entry_price),
                ))
                db.commit()
                tid = trade.id
            with self._lock:
                self._pending_orders[tid] = (ord_id, now_ts)
            self._log(
                f"[{iid}] 🚀 {sig.side.upper()} vía {sig.strategy} | "
                f"Entrada: {sig.entry_price:.6f} | SL: {sl:.6f} | TP: {tp:.6f} | {sig.reason}"
            )
            await notifier.notify_open(iid, sig.side, sig.strategy,
                                       float(sig.entry_price), float(sl), float(tp), float(qty))
        except Exception as e:
            self._log(f"[{iid}] Error abriendo trade: {e}", "ERROR")
            await notifier.notify_error(f"open_trade {iid}", str(e))

    # ── Stale Order Loop ──────────────────────────────────────────────

    async def _stale_order_loop(self, client: OKXClient) -> None:
        """Cancela órdenes límite que no se llenaron en STALE_ORDER_MINUTES."""
        while self.running:
            await asyncio.sleep(60)
            try:
                stale_limit = time.time() - STALE_ORDER_MINUTES * 60
                with self._lock:
                    stale = [(tid, oid, ts) for tid, (oid, ts) in self._pending_orders.items() if ts < stale_limit]
                for tid, ord_id, ts in stale:
                    with get_session() as db:
                        trade = db.query(Trade).filter(Trade.id == tid).first()
                        if not trade or not trade.is_open:
                            with self._lock:
                                self._pending_orders.pop(tid, None)
                            continue
                        order_info = await client.get_order(trade.symbol, ord_id)
                        state = order_info.get("state", "")
                        if state in ("filled", "partially_filled"):
                            with self._lock:
                                self._pending_orders.pop(tid, None)
                            continue
                        # Cancel stale
                        await client.cancel_order(trade.symbol, ord_id)
                        trade.status    = TradeStatus.CLOSED
                        trade.close_reason = "STALE_ORDER"
                        trade.closed_at = datetime.now(timezone.utc)
                        trade.realized_pnl = 0.0
                        db.add(TradeEvent(trade_id=tid, event_type="STALE_CANCEL",
                                          message=f"Orden {ord_id} cancelada (stale > {STALE_ORDER_MINUTES}m)"))
                        db.commit()
                        with self._lock:
                            self._pending_orders.pop(tid, None)
                        self._log(f"[{trade.symbol}] 🗑️ Orden stale cancelada: {ord_id}")
                        await notifier.notify_stale_cancel(trade.symbol, ord_id)
            except Exception as e:
                self._log(f"Error en stale_order_loop: {e}", "WARN")

    # ── Reconcile Loop (30s) ─────────────────────────────────────────

    async def _reconcile_loop(self, client: OKXClient) -> None:
        """El Agente Supervisor: vigila continuamente las posiciones activas."""
        loop_count = 0
        while self.running:
            try:
                if loop_count % 3 == 0:  # Cada ~9 segundos
                    bal = await client.get_balance()
                    if bal > 0:
                        self.current_exchange_balance = bal
                await self._reconcile_tick(client)
            except Exception as e:
                self._log(f"Error en el Agente Supervisor: {e}", "ERROR")
            loop_count += 1
            await asyncio.sleep(RECONCILE_INTERVAL)

    async def _reconcile_tick(self, client: OKXClient) -> None:
        with get_session() as db:
            open_trades = db.query(Trade).filter(
                Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
            ).all()
            if not open_trades:
                return
            
            # Log silencioso o periódico del supervisor para que sepas que está activo
            if int(time.time()) % 120 < RECONCILE_INTERVAL:
                self._log(f"[AGENTE SUPERVISOR] Vigilando {len(open_trades)} posiciones activas. Comprobando latencias y métricas de Breakeven/Trailing...", "SYSTEM")

            trade_snapshots = [
                {
                    "id":          t.id,
                    "symbol":      t.symbol,
                    "side":        t.side.value if hasattr(t.side, "value") else t.side,
                    "entry":       Decimal(str(t.entry_price)),
                    "qty":         Decimal(str(t.qty)),
                    "sl":          Decimal(str(t.sl_price)),
                    "tp":          Decimal(str(t.tp_price)) if t.tp_price else None,
                    "atr":         Decimal(str(t.atr_5m)),
                    "risk":        Decimal(str(t.risk_usd)),
                    "be_done":     bool(t.be_activated),
                    "trail_done":  bool(t.trail_activated),
                    "trail_sl":    Decimal(str(t.trail_sl)) if t.trail_sl else None,
                    "peak":        Decimal(str(t.peak_price)) if t.peak_price else None,
                    "status":      t.status,
                    "opened_at":   t.opened_at,
                }
                for t in open_trades
            ]

        # Fetch tickers
        tickers_raw = await client.tickers()
        ticker_map  = {t["instId"]: t for t in tickers_raw}

        for td in trade_snapshots:
            tick = ticker_map.get(td["symbol"])
            if not tick:
                continue
            price = Decimal(str(tick.get("last", "0")))
            if price <= 0:
                continue

            # Update peak
            current_peak = td["peak"] or price
            new_peak = max(current_peak, price) if td["side"] == "long" else min(current_peak, price)

            # Candles for volume early exit check
            df_5m_vol = None
            if not td["be_done"] and not td["trail_done"]:
                opened_mins = (datetime.now(timezone.utc) - td["opened_at"].replace(tzinfo=timezone.utc)).total_seconds() / 60 if td["opened_at"] else 999
                if opened_mins <= EARLY_EXIT_LOOKBACK_MINUTES:
                    try:
                        df_5m_vol = await client.candles(td["symbol"], "5m", limit=10)
                    except Exception:
                        pass

            tp_original = compute_tp(td["entry"], td["side"], td["atr"])
            inst        = self._instruments.get(td["symbol"])
            ct_val      = Decimal(inst["ctVal"]) if inst else Decimal("1")

            decisions = evaluate(
                side=td["side"], entry=td["entry"], tp=td["tp"],
                current_sl=td["sl"], price=price, qty=td["qty"],
                ct_val=ct_val, atr_5m=td["atr"], risk_usd=td["risk"],
                be_activated=td["be_done"], trail_activated=td["trail_done"],
                trail_sl=td["trail_sl"], peak_price=new_peak,
                tp_original=tp_original,
                df_5m=df_5m_vol, opened_at=td["opened_at"],
            )

            for decision in decisions:
                await self._apply_decision(client, td, decision, price, ct_val)

            # Update peak in DB
            if new_peak != td["peak"]:
                try:
                    with get_session() as db:
                        t = db.query(Trade).filter(Trade.id == td["id"]).first()
                        if t:
                            t.peak_price = float(new_peak)
                            db.commit()
                except Exception:
                    pass

    async def _apply_decision(
        self, client: OKXClient, td: dict, decision, price: Decimal, ct_val: Decimal
    ) -> None:
        trade_id = td["id"]
        symbol   = td["symbol"]
        side     = td["side"]

        if decision.action == Action.NONE:
            return

        self._log(f"[{symbol}] {decision.log_message}")

        # Robust retry loop for MOVE_SL (breakeven / trailing)
        max_attempts = 3 if decision.action == Action.MOVE_SL else 1
        for attempt in range(1, max_attempts + 1):
            try:
                with get_session() as db:
                    trade = db.query(Trade).filter(Trade.id == trade_id).first()
                    if not trade or not trade.is_open:
                        return

                    if decision.action == Action.MOVE_SL:
                        new_sl = decision.new_sl
                        trade.sl_price = float(new_sl)
                        if decision.reason == "BREAKEVEN":
                            trade.be_activated = 1
                            trade.status = TradeStatus.BREAKEVEN
                            await client.cancel_algo_orders(symbol)
                            await notifier.notify_breakeven(symbol, float(new_sl))
                        elif decision.reason in ("TRAIL_ACTIVATE", "TRAIL_MOVE"):
                            trade.trail_activated = 1
                            trade.trail_sl = float(new_sl)
                            trade.status   = TradeStatus.TRAILING
                            if decision.reason == "TRAIL_ACTIVATE":
                                await notifier.notify_trailing(symbol, float(new_sl))
                        db.add(TradeEvent(trade_id=trade_id, event_type=decision.reason,
                                          message=decision.log_message, price=float(price)))
                        db.commit()
                        break  # success

                    elif decision.action == Action.CANCEL_TP:
                        trade.tp_price = None
                        await client.cancel_algo_orders(symbol)
                        db.add(TradeEvent(trade_id=trade_id, event_type="CANCEL_TP",
                                          message=decision.log_message, price=float(price)))
                        db.commit()
                        break

                    elif decision.action == Action.CLOSE_MARKET:
                        await client.close_position(symbol, side)
                        pnl = pnl_usd(td["entry"], price, td["qty"], ct_val, side)
                        trade.status       = TradeStatus.CLOSED
                        trade.close_price  = float(price)
                        trade.close_reason = decision.reason
                        trade.realized_pnl = float(pnl)
                        trade.closed_at    = datetime.now(timezone.utc)
                        db.add(TradeEvent(trade_id=trade_id, event_type="CLOSE",
                                          message=f"{decision.reason} | PnL: {float(pnl):.2f} USDT",
                                          price=float(price)))
                        # Cooldown
                        until = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
                        ex = db.query(Cooldown).filter(Cooldown.symbol == symbol).first()
                        if ex:
                            ex.until = until
                        else:
                            db.add(Cooldown(symbol=symbol, until=until))
                        db.commit()
                        sign = "+" if pnl >= 0 else ""
                        self._log(
                            f"[{symbol}] ✅ CIERRE via {decision.reason} a {price:.6f} | "
                            f"PnL: {sign}{float(pnl):.2f} USDT | Cooldown {COOLDOWN_MINUTES}m"
                        )
                        await notifier.notify_close(
                            symbol, side, decision.reason,
                            float(td["entry"]), float(price), float(pnl)
                        )
                        break

            except Exception as e:
                self._log(
                    f"[{symbol}] ⚠️ Error {decision.reason} (intento {attempt}/{max_attempts}): {e}. "
                    f"Reintentando en {RECONCILE_RETRY_SEC}s...", "WARN"
                )
                if attempt < max_attempts:
                    await asyncio.sleep(RECONCILE_RETRY_SEC)
                else:
                    await notifier.notify_error(f"apply_decision {symbol}", str(e))

    # ── Data accessors ────────────────────────────────────────────────

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
                closed = db.query(Trade).filter(Trade.realized_pnl.isnot(None)).all()
            total     = len(closed)
            wins      = [t for t in closed if (t.realized_pnl or 0) > 0]
            losses    = [t for t in closed if (t.realized_pnl or 0) < 0]
            total_pnl = sum(t.realized_pnl or 0 for t in closed)
            gw = sum(t.realized_pnl for t in wins)
            gl = abs(sum(t.realized_pnl for t in losses))
            return {
                "total_trades":   total,
                "win_rate":       (len(wins) / total * 100) if total else 0,
                "profit_factor":  (gw / gl) if gl > 0 else 0,
                "total_pnl":      total_pnl,
                "best_trade":     max((t.realized_pnl or 0 for t in closed), default=0),
                "worst_trade":    min((t.realized_pnl or 0 for t in closed), default=0),
            }
        except Exception:
            return {}

    def status_json(self) -> dict:
        """Para el endpoint REST /status."""
        return {
            "version":    self.VERSION,
            "running":    self.running,
            "last_scan":  self.last_scan,
            "last_error": self.last_error,
            "macro_shield": self.shield.status_label,
            "open_trades": len(self.get_open_trades()),
            "stats":      self.get_stats(),
        }
