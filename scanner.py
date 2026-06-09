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
from strategy import QuantumSMCStrategy, SupertrendPullbackStrategy, Signal


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
        ts  = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
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
        if sl or tp:
            algo_ord = {}
            if sl:
                algo_ord["slTriggerPx"] = str(sl)
                algo_ord["slOrdPx"] = "-1"
            if tp:
                algo_ord["tpTriggerPx"] = str(tp)
                algo_ord["tpOrdPx"] = "-1"
            payload["attachAlgoOrds"] = [algo_ord]
            
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

    async def place_algo_order(self, inst_id: str, pos_side: str, qty: Decimal, sl: Decimal = None, tp: Decimal = None) -> None:
        try:
            payload = {
                "instId": inst_id,
                "tdMode": "isolated",
                "posSide": pos_side,
                "sz": str(qty),
            }
            if sl and tp:
                payload["ordType"] = "oco"
                payload["slTriggerPx"] = str(sl)
                payload["slOrdPx"] = "-1"
                payload["tpTriggerPx"] = str(tp)
                payload["tpOrdPx"] = "-1"
            elif sl:
                payload["ordType"] = "conditional"
                payload["slTriggerPx"] = str(sl)
                payload["slOrdPx"] = "-1"
            elif tp:
                payload["ordType"] = "conditional"
                payload["tpTriggerPx"] = str(tp)
                payload["tpOrdPx"] = "-1"
            else:
                return
            await self._req("POST", "/api/v5/trade/order-algo", payload, auth=True)
        except Exception:
            pass


    async def place_market_order(self, inst_id: str, side: str, qty: Decimal, sl: Decimal = None, tp: Decimal = None) -> str:
        pos_side = "long" if side == "buy" else "short"
        payload = {
            "instId": inst_id, "tdMode": "isolated", "side": side,
            "posSide": pos_side, "ordType": "market", "sz": str(qty),
        }
        if sl or tp:
            algo_ord = {}
            if sl:
                algo_ord["slTriggerPx"] = str(sl)
                algo_ord["slOrdPx"] = "-1"
            if tp:
                algo_ord["tpTriggerPx"] = str(tp)
                algo_ord["tpOrdPx"] = "-1"
            payload["attachAlgoOrds"] = [algo_ord]
            
        rows = await self._req("POST", "/api/v5/trade/order", payload, auth=True)
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

    async def get_positions_history(self, inst_id: str, limit: int = 1) -> list[dict]:
        try:
            return await self._req("GET", f"/api/v5/account/positions-history?instId={inst_id}&limit={limit}", auth=True)
        except Exception:
            return []

    async def get_balance(self) -> float:
        try:
            rows = await self._req("GET", "/api/v5/account/balance", auth=True)
            if rows and "details" in rows[0] and len(rows[0]["details"]) > 0:
                # Some OKX accounts have balance in details -> eq
                return float(rows[0].get("totalEq", 0) or rows[0]["details"][0].get("eq", 0))
            elif rows and "totalEq" in rows[0]:
                return float(rows[0]["totalEq"])
        except Exception as e:
            print(f"Error fetching balance: {e}")
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
        self.trend_strat     = QuantumSMCStrategy()
        self.div_strat       = SupertrendPullbackStrategy()

        self.running         = False
        self._lock           = threading.Lock()
        self._log_buffer:    list[str] = []
        self._instruments:   dict[str, dict] = {}
        self.compliance_restricted = set()  # Local set of compliance restricted symbols (error 51155)
        self._pending_orders: dict[int, tuple[str, float]] = {}  # trade_id → (ord_id, ts)
        self.current_exchange_balance: float = 0.0
        self.last_positions: dict = {}

        self.last_scan       = "never"
        self.last_error      = ""
        self._thread: Optional[threading.Thread] = None

    def _new_client(self) -> OKXClient:
        return OKXClient(self.api_key, self.api_secret, self.passphrase, self.simulated)

    # ── Logging ──────────────────────────────────────────────────────

    def _log(self, msg: str, level: str = "INFO") -> None:
        stamp = datetime.utcnow().strftime("%H:%M:%S")
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
                    tp = compute_tp(entry, sl, side)
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

    async def _self_heal_auditor(self, client: OKXClient) -> None:
        try:
            positions = await client.get_positions()
            pending = []
            for o_type in ["oco", "conditional"]:
                res = await client._req("GET", f"/api/v5/trade/orders-algo-pending?instType=SWAP&ordType={o_type}", auth=True)
                if res:
                    pending.extend(res)
            
            with get_session() as db:
                for p in positions:
                    inst_id = p.get("instId", "")
                    if not inst_id.endswith("-USDT-SWAP"):
                        continue
                    pos_side = p.get("posSide", "long").lower()
                    side = TradeSide.LONG if pos_side == "long" else TradeSide.SHORT
                    entry = float(p.get("avgPx", 0))
                    qty = float(p.get("pos", 0))
                    
                    # 1. Adopt orphans
                    trade = db.query(Trade).filter(Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT]), Trade.symbol == inst_id).first()
                    if not trade:
                        self._log(f"[{inst_id}] 🤖 AUDITOR: Posición huérfana detectada. Adoptando en la BD...", "SYSTEM")
                        # Defaults para huérfanas
                        atr_est = entry * 0.015
                        trade = Trade(
                            symbol=inst_id, side=side, strategy="SUPERTREND_PULLBACK_V3",
                            status=TradeStatus.OPEN, entry_price=entry, qty=qty,
                            sl_price=entry * (0.95 if side == TradeSide.LONG else 1.05),
                            tp_price=entry * (1.10 if side == TradeSide.LONG else 0.90),
                            atr_5m=atr_est, leverage=int(p.get("lever", 10))
                        )
                        db.add(trade)
                        db.commit()
                        db.refresh(trade)
                    
                    # 2. Cleanup duplicates & missing
                    algos_for_sym = [a for a in pending if a.get("instId") == inst_id]
                    sl_count = sum(1 for a in algos_for_sym if a.get("slTriggerPx"))
                    tp_count = sum(1 for a in algos_for_sym if a.get("tpTriggerPx"))
                    
                    if sl_count > 1 or tp_count > 1 or (sl_count == 0 and tp_count == 0):
                        if sl_count > 1 or tp_count > 1:
                            self._log(f"[{inst_id}] 🤖 AUDITOR: Basura detectada ({tp_count} TPs, {sl_count} SLs). Limpiando...", "WARN")
                            payload = [{"instId": inst_id, "algoId": a["algoId"]} for a in algos_for_sym]
                            if payload:
                                await client._req("POST", "/api/v5/trade/cancel-algos", payload, auth=True)
                                await asyncio.sleep(0.5)
                        
                        self._log(f"[{inst_id}] 🤖 AUDITOR: Restaurando SL/TP según Base de Datos.", "SYSTEM")
                        if trade.status == TradeStatus.TRAILING:
                            await client.place_algo_order(inst_id, pos_side, Decimal(str(qty)), sl=Decimal(str(trade.trail_sl)))
                        else:
                            await client.place_algo_order(inst_id, pos_side, Decimal(str(qty)), sl=Decimal(str(trade.sl_price)), tp=Decimal(str(trade.tp_price)))

        except Exception as e:
            pass

    # ── Scanner Loop (15s) ────────────────────────────────────────────

    async def _scanner_loop(self, client: OKXClient) -> None:
        loop_counter = 0
        while self.running:
            try:
                loop_counter += 1
                if loop_counter % 6 == 0:
                    await self._self_heal_auditor(client)
                await self._scan_tick(client)
                self.last_scan  = datetime.utcnow().strftime("%H:%M:%S UTC")
                self.last_error = ""
            except Exception as e:
                self.last_error = str(e)
                self._log(f"Error en scanner: {e}", "ERROR")
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

    async def _scan_tick(self, client: OKXClient) -> None:
        # Daily loss check
        with get_session() as db:
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
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
                                    t.closed_at = datetime.utcnow()
                                    # Aplicar Cooldown de 30m
                                    until = datetime.utcnow() + timedelta(minutes=COOLDOWN_MINUTES)
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

        # ALWAYS check real OKX positions to prevent double entries if DB was wiped
        try:
            okx_pos = await client.get_positions()
            for p in okx_pos:
                active_syms.add(p.get("instId"))
        except Exception as e:
            self._log(f"Error checking OKX positions: {e}", "ERROR")

        if len(active_syms) >= MAX_CONCURRENT_TRADES:
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
            if iid in active_syms or iid in cdwn_syms or iid in self.compliance_restricted or iid not in self._instruments:
                continue
            try:
                df_1h, df_15m, df_5m = await asyncio.gather(
                    client.candles(iid, "1H", 100),
                    client.candles(iid, "15m", 100),
                    client.candles(iid, "5m", 100),
                )
                for sig in [
                    self.trend_strat.signal(iid, df_1h, df_15m, df_5m),
                    self.div_strat.signal(iid, df_1h, df_15m, df_5m),
                ]:
                    if sig:
                        candidates.append(sig)
            except Exception as e:
                self._log(f"Error procesando {iid}: {e}", "ERROR")
                continue

        for sig in sorted(candidates, key=lambda s: s.score, reverse=True):
            with get_session() as db:
                if db.query(Trade).filter(Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])).count() >= MAX_CONCURRENT_TRADES:
                    break
            await self._open_trade(client, sig)
            
        if not candidates:
            self._log(f"🔎 Escaneo en {len(universe)} monedas completado. Criterios de estrategia no cumplidos.", "SYSTEM")
        else:
            self._log(f"✅ ¡Señal encontrada! Abriendo {len(candidates)} operación(es).", "SYSTEM")

    async def _open_trade(self, client: OKXClient, sig: Signal) -> None:
        iid  = sig.symbol
        inst = self._instruments.get(iid)
        if not inst:
            return
        ct_val = Decimal(inst["ctVal"])
        lot_sz = Decimal(inst["lotSz"])
        min_sz = Decimal(inst["minSz"])
        tick_sz = Decimal(inst["tickSz"])
        
        from decimal import ROUND_HALF_UP
        
        def _round_tick(val: Decimal) -> Decimal:
            return (val / tick_sz).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_sz
            
        entry_price = _round_tick(Decimal(str(sig.entry_price)))
        if sig.sl_price is not None:
            sl = _round_tick(Decimal(str(sig.sl_price)))
        else:
            sl = _round_tick(compute_sl(entry_price, sig.side, sig.atr_5m))
            
        tp = _round_tick(compute_tp(entry_price, sl, sig.side))
        
        # Ensure SL makes sense for side
        if sig.side == "long" and sl >= entry_price:
            sl = entry_price - tick_sz
        if sig.side == "short" and sl <= entry_price:
            sl = entry_price + tick_sz
            
        qty    = compute_qty(entry_price, sl, ct_val, lot_sz)
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
                
            if sig.order_type == "market":
                ord_id = await client.place_market_order(iid, order_side, qty, sl=sl, tp=tp)
            else:
                ord_id = await client.place_limit_order(iid, order_side, qty, entry_price, sl=sl, tp=tp)
            now_ts = time.time()
            with get_session() as db:
                trade = Trade(
                    symbol=iid, side=TradeSide(sig.side),
                    strategy=Strategy(sig.strategy),
                    entry_price=float(entry_price), qty=float(qty),
                    sl_price=float(sl), tp_price=float(tp),
                    atr_5m=float(sig.atr_5m), risk_usd=float(FIXED_RISK_USDT),
                    leverage=LEVERAGE, status=TradeStatus.OPEN,
                    peak_price=float(entry_price),
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
            err_str = str(e)
            if "51155" in err_str:
                self.compliance_restricted.add(iid)
                self._log(f"[{iid}] Símbolo con restricciones de cumplimiento OKX (51155). Agregado a lista de exclusión local.", "WARN")
            else:
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
                        trade.closed_at = datetime.utcnow()
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
        # Fetch actual positions from OKX
        try:
            okx_pos = await client.get_positions()
            okx_pos_map = {p["instId"]: p for p in okx_pos}
            self.last_positions = okx_pos_map
        except Exception as e:
            self._log(f"Error al obtener posiciones activas en OKX: {e}", "ERROR")
            okx_pos_map = None

        with get_session() as db:
            open_trades = db.query(Trade).filter(
                Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
            ).all()
            if not open_trades:
                return

            active_snapshots = []
            for t in open_trades:
                if okx_pos_map is not None:
                    if t.symbol in okx_pos_map:
                        # Position is active. Sync real entry price and quantity.
                        p = okx_pos_map[t.symbol]
                        real_entry = float(p.get("avgPx", 0))
                        real_qty = float(abs(Decimal(p.get("pos", "0"))))
                        if real_entry > 0:
                            if t.entry_price != real_entry or t.qty != real_qty:
                                self._log(f"[{t.symbol}] 🔄 Sincronizando Entrada/Cantidad real de OKX: {t.entry_price} -> {real_entry} | Qty: {t.qty} -> {real_qty}")
                                t.entry_price = real_entry
                                t.qty = real_qty
                                db.commit()
                        active_snapshots.append(t)
                    else:
                        # Position is closed. Sync natively closed position details from history!
                        history = await client.get_positions_history(t.symbol, limit=1)
                        success_sync = False
                        if history:
                            last_closed = history[0]
                            c_time_ms = float(last_closed.get("cTime", 0))
                            u_time_ms = float(last_closed.get("uTime", 0))
                            closed_at_dt = datetime.utcfromtimestamp(u_time_ms / 1000)
                            
                            opened_at_ts = t.opened_at.replace(tzinfo=timezone.utc).timestamp() if t.opened_at.tzinfo is None else t.opened_at.timestamp()
                            if (c_time_ms / 1000) >= (opened_at_ts - 60):
                                real_entry = float(last_closed.get("openAvgPx", t.entry_price))
                                real_exit = float(last_closed.get("closeAvgPx", 0))
                                real_pnl = float(last_closed.get("realizedPnl", 0))
                                
                                # Determine close reason
                                close_reason = "OKX_NATIVE_CLOSE"
                                side_str = t.side.value if hasattr(t.side, "value") else str(t.side)
                                is_win_or_scratch = (real_exit >= t.entry_price) if side_str == "long" else (real_exit <= t.entry_price)
                                
                                if t.tp_price and abs(real_exit - t.tp_price) / t.tp_price < 0.01:
                                    close_reason = "TAKE_PROFIT_HIT"
                                elif t.trail_sl and abs(real_exit - t.trail_sl) / t.trail_sl < 0.01:
                                    close_reason = "TRAILING_HIT"
                                elif t.sl_price and abs(real_exit - t.sl_price) / t.sl_price < 0.01:
                                    if t.status == TradeStatus.BREAKEVEN or t.be_activated:
                                        close_reason = "BREAKEVEN_HIT"
                                    elif t.status == TradeStatus.TRAILING or t.trail_activated:
                                        close_reason = "TRAILING_HIT"
                                    elif is_win_or_scratch:
                                        close_reason = "BREAKEVEN_HIT"
                                    else:
                                        close_reason = "STOP_LOSS_HIT"
                                else:
                                    if is_win_or_scratch:
                                        close_reason = "BREAKEVEN_HIT"
                                    else:
                                        close_reason = "STOP_LOSS_HIT"
                                    
                                self._log(f"[{t.symbol}] 🔄 Cierre nativo detectado en OKX: Entrada={real_entry} | Salida={real_exit} | PnL={real_pnl} USDT | Razón={close_reason}")
                                
                                t.status = TradeStatus.CLOSED
                                t.entry_price = real_entry
                                t.close_price = real_exit
                                t.realized_pnl = real_pnl
                                t.close_reason = close_reason
                                t.closed_at = closed_at_dt
                                
                                db.add(TradeEvent(
                                    trade_id=t.id, event_type="CLOSE",
                                    message=f"Cierre nativo detectado ({close_reason}) | PnL: {real_pnl:.2f} USDT (OKX)",
                                    price=real_exit
                                ))
                                
                                until = datetime.utcnow() + timedelta(minutes=COOLDOWN_MINUTES)
                                ex = db.query(Cooldown).filter(Cooldown.symbol == t.symbol).first()
                                if ex:
                                    ex.until = until
                                else:
                                    db.add(Cooldown(symbol=t.symbol, until=until))
                                    
                                db.commit()
                                success_sync = True
                                
                        if not success_sync:
                            self._log(f"[{t.symbol}] ⚠️ Posición cerrada en OKX pero sin registro compatible en historial. Cerrando vía simulación.", "WARN")
                            active_snapshots.append(t)
                else:
                    active_snapshots.append(t)

            if not active_snapshots:
                return

            if int(time.time()) % 120 < RECONCILE_INTERVAL:
                self._log(f"[AGENTE SUPERVISOR] Vigilando {len(active_snapshots)} posiciones activas. Comprobando latencias y métricas de Breakeven/Trailing...", "SYSTEM")

            trade_snapshots = [
                {
                    "id":          t.id,
                    "symbol":      t.symbol,
                    "side":        t.side.value if hasattr(t.side, "value") else t.side,
                    "strategy":    t.strategy.value if hasattr(t.strategy, "value") else t.strategy,
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
                for t in active_snapshots
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

            # Compute original TP using new signature: compute_tp(entry, sl, side)
            # Since td["sl_price"] (or td["sl"]) is the original SL
            tp_original = compute_tp(td["entry"], td["sl"], td["side"])
            inst        = self._instruments.get(td["symbol"])
            ct_val      = Decimal(inst["ctVal"]) if inst else Decimal("1")

            decisions = evaluate(
                side=td["side"], entry=td["entry"], tp=td["tp"],
                current_sl=td["sl"], price=price, qty=td["qty"],
                ct_val=ct_val, atr_5m=td["atr"], risk_usd=td["risk"],
                be_activated=td["be_done"], trail_activated=td["trail_done"],
                trail_sl=td["trail_sl"], peak_price=new_peak,
                tp_original=tp_original,
                strategy_name=td.get("strategy", ""),
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
                            await client.place_algo_order(symbol, "long" if side == "buy" else "short", Decimal(str(trade.qty)), sl=new_sl, tp=Decimal(str(trade.tp_price)) if trade.tp_price else None)
                            await notifier.notify_breakeven(symbol, float(new_sl))
                        elif decision.reason in ("TRAIL_ACTIVATE", "TRAIL_MOVE"):
                            trade.trail_activated = 1
                            trade.trail_sl = float(new_sl)
                            trade.status   = TradeStatus.TRAILING
                            await client.cancel_algo_orders(symbol)
                            # Trailing uses SL only, removes TP explicitly
                            await client.place_algo_order(symbol, "long" if side == "buy" else "short", Decimal(str(trade.qty)), sl=new_sl)
                            if decision.reason == "TRAIL_ACTIVATE":
                                await notifier.notify_trailing(symbol, float(new_sl))
                        db.add(TradeEvent(trade_id=trade_id, event_type=decision.reason,
                                          message=decision.log_message, price=float(price)))
                        db.commit()
                        break  # success

                    elif decision.action == Action.CANCEL_TP:
                        trade.tp_price = None
                        await client.cancel_algo_orders(symbol)
                        # Re-place SL only
                        await client.place_algo_order(symbol, "long" if side == "buy" else "short", Decimal(str(trade.qty)), sl=Decimal(str(trade.sl_price)))
                        db.add(TradeEvent(trade_id=trade_id, event_type="CANCEL_TP",
                                          message=decision.log_message, price=float(price)))
                        db.commit()
                        break

                    elif decision.action == Action.CLOSE_MARKET:
                        try:
                            await client.close_position(symbol, side)
                        except Exception as e:
                            # 51000 means "Position does not exist" or similar on OKX
                            if "51000" in str(e) or "51023" in str(e) or "not found" in str(e).lower() or "no position" in str(e).lower() or "doesn't exist" in str(e).lower():
                                self._log(f"[{symbol}] Posición ya cerrada nativamente por OKX. Sincronizando BD.")
                            else:
                                raise e

                        # Try to get the actual exit price and realized PnL from OKX history
                        real_exit = float(price)
                        real_pnl = float(pnl_usd(td["entry"], price, td["qty"], ct_val, side))
                        try:
                            history = await client.get_positions_history(symbol, limit=1)
                            if history:
                                last_closed = history[0]
                                c_time_ms = float(last_closed.get("cTime", 0))
                                opened_at_ts = td["opened_at"].replace(tzinfo=timezone.utc).timestamp() if td["opened_at"].tzinfo is None else td["opened_at"].timestamp()
                                if (c_time_ms / 1000) >= (opened_at_ts - 60):
                                    real_exit = float(last_closed.get("closeAvgPx", real_exit))
                                    real_pnl = float(last_closed.get("realizedPnl", real_pnl))
                                    self._log(f"[{symbol}] Sincronizado cierre de historial OKX: Salida={real_exit} | PnL={real_pnl} USDT")
                        except Exception as hist_err:
                            self._log(f"[{symbol}] No se pudo sincronizar PnL de historial: {hist_err}", "WARN")

                        trade.status       = TradeStatus.CLOSED
                        trade.close_price  = real_exit
                        trade.close_reason = decision.reason
                        trade.realized_pnl = real_pnl
                        trade.closed_at    = datetime.utcnow()
                        db.add(TradeEvent(trade_id=trade_id, event_type="CLOSE",
                                          message=f"{decision.reason} | PnL: {real_pnl:.2f} USDT",
                                          price=real_exit))
                        # Cooldown
                        until = datetime.utcnow() + timedelta(minutes=COOLDOWN_MINUTES)
                        ex = db.query(Cooldown).filter(Cooldown.symbol == symbol).first()
                        if ex:
                            ex.until = until
                        else:
                            db.add(Cooldown(symbol=symbol, until=until))
                        db.commit()
                        sign = "+" if real_pnl >= 0 else ""
                        self._log(
                            f"[{symbol}] ✅ CIERRE via {decision.reason} a {real_exit:.6f} | "
                            f"PnL: {sign}{real_pnl:.2f} USDT | Cooldown {COOLDOWN_MINUTES}m"
                        )
                        await notifier.notify_close(
                            symbol, side, decision.reason,
                            float(td["entry"]), float(real_exit), float(real_pnl)
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
