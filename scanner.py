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
from decimal import ROUND_DOWN, Decimal
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
    breakeven_sl, compute_qty, compute_sl,
    pnl_pct_of_risk, pnl_usd
)
from strategy import Signal, SMCPDHSweepReversal, SMCFVGMitigation, SMCOrderblockBounce, SMCAMDBreakout, SuperTrendEMARegimeMTFPro


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

    async def cancel_algo_orders(self, inst_id: str, pos_side: str = None) -> None:
        try:
            pending = []
            for o_type in ["oco", "conditional"]:
                res = await self._req("GET", f"/api/v5/trade/orders-algo-pending?instId={inst_id}&ordType={o_type}", auth=True)
                if res:
                    pending.extend(res)
            if pos_side:
                pending = [p for p in pending if p.get("posSide", "").lower() == pos_side.lower()]
            if pending:
                # Cancel in batches of 10 if necessary, but usually it's just 2 orders (SL and TP)
                payload = [{"instId": inst_id, "algoId": p["algoId"]} for p in pending[:10]]
                await self._req("POST", "/api/v5/trade/cancel-algos", payload, auth=True)
        except Exception as e:
            # Do not crash the loop if cancelling algo fails
            pass

    async def place_algo_order(self, inst_id: str, pos_side: str, qty: Decimal, sl: Decimal = None, tp: Decimal = None, td_mode: str = "isolated") -> None:
        payload = {
            "instId": inst_id,
            "tdMode": td_mode,
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

    async def close_partial_position(self, inst_id: str, pos_side: str, qty: Decimal) -> str:
        side = "sell" if pos_side == "long" else "buy"
        payload = {
            "instId": inst_id,
            "tdMode": "isolated",
            "side": side,
            "posSide": pos_side,
            "ordType": "market",
            "sz": str(qty)
        }
        rows = await self._req("POST", "/api/v5/trade/order", payload, auth=True)
        return rows[0].get("ordId", "") if rows else ""


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
            if rows:
                data = rows[0]
                
                # Priority 1: USDT available balance in details
                if "details" in data:
                    for item in data["details"]:
                        if item.get("ccy") == "USDT":
                            val = item.get("availBal") or item.get("availEq") or item.get("eq") or "0"
                            if val == "": val = "0"
                            if float(val) > 0:
                                return float(val)
                # Priority 2: totalEq or adjEq
                val = data.get("totalEq") or data.get("adjEq") or "0"
                if val == "": val = "0"
                return float(val)
        except Exception as e:
            from discord_notifier import discord_notifier
            await discord_notifier.log_error("BALANCE", f"Error fetching balance: {e}")
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
        self.strat_pdh = SMCPDHSweepReversal()
        self.strat_fvg = SMCFVGMitigation()
        self.strat_ob = SMCOrderblockBounce()
        self.strat_amd = SMCAMDBreakout()
        self.strat_st_ema = SuperTrendEMARegimeMTFPro()

        self.running         = False
        self.scanning        = False
        self._lock           = threading.Lock()
        self._log_buffer:    list[str] = []
        self._instruments:   dict[str, dict] = {}
        self.compliance_restricted = set()  # Local set of compliance restricted symbols (error 51155)
        self._pending_entries: dict[str, dict] = {}  # ord_id -> dict with entry data
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
            if not self.scanning:
                self.scanning = True
                self._log("MOTOR ESCÁNER REANUDADO INMEDIATAMENTE.", "SYSTEM")
            return "already_running"
        self.running = True
        self.scanning = True
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True)
        self._thread.start()
        self._log("MOTOR QUANTUM ENCENDIDO")
        return "started"

    def stop(self) -> str:
        self.scanning = False
        self._log("[SYSTEM] Escáner pausado. El Guardián seguirá vigilando las operaciones abiertas.", "WARN")
        return "stopped"

    async def _close_all_positions(self) -> None:
        client = self._new_client()
        try:
            with get_session() as db:
                from models import Trade, TradeStatus
                open_trades = db.query(Trade).filter(
                    Trade.position_closed == 0
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
        # First fully kill everything so it unlocks DB
        self.scanning = False
        self.running = False
        if hasattr(self, "ws_agent") and self.ws_agent:
            self.ws_agent.stop()
            
        # Then forcefully close all active ones in OKX synchronously blocking
        try:
            asyncio.run(self._close_all_positions())
        except Exception as e:
            self._log(f"Error closing positions: {e}", "ERROR")

        import time
        time.sleep(1) # Allow loops to break and unlock DB
        
        # Now wipe the database completely by dropping tables
        try:
            # Backup before wipe
            try:
                import csv, os
                from models import get_session, Trade
                backup_file = "history_backup.csv"
                file_exists = os.path.isfile(backup_file)
                with get_session() as db:
                    trades = db.query(Trade).all()
                    if trades:
                        with open(backup_file, mode='a', newline='', encoding='utf-8') as f:
                            writer = csv.writer(f)
                            if not file_exists:
                                writer.writerow(["id", "symbol", "side", "strategy", "status", "entry_price", "close_price", "realized_pnl", "opened_at", "closed_at"])
                            for t in trades:
                                writer.writerow([t.id, t.symbol, t.side, t.strategy, t.status, t.entry_price, t.close_price, getattr(t, "realized_pnl", 0), t.opened_at, t.closed_at])
            except Exception as e:
                self._log(f"Error backup CSV: {e}", "ERROR")

            from models import engine, Base
            Base.metadata.drop_all(engine)
            Base.metadata.create_all(engine)
            self.last_positions = {}
            self._log("🗑️ BASE DE DATOS Y ESTADÍSTICAS BORRADAS AL 100%.", "SYSTEM")
        except Exception as e:
            self._log(f"Error al resetear la base de datos: {e}", "ERROR")
        
        return "Reseteo Completado. Puedes Iniciar el Bot de Nuevo."

    async def _main(self) -> None:
        client = self._new_client()
        try:
            await self._load_instruments(client)
            
            # --- NUEVA ARQUITECTURA ---
            from order_execution_engine import OrderExecutionEngine
            from position_manager import PositionManager
            from exchange_synchronizer import ExchangeSynchronizer
            from recovery_engine import RecoveryEngine
            from discord_notifier import discord_notifier
            
            execution_engine = OrderExecutionEngine(client)
            sync_engine = ExchangeSynchronizer(client)
            recovery = RecoveryEngine(sync_engine)
            
            await recovery.run_recovery()
            
            position_manager = PositionManager(execution_engine, client)
            
            await asyncio.gather(
                self._scanner_loop(client),
                position_manager.run_supervisor(),
                self._balance_loop(client),
                self._monitor_pending_entries_loop(client)
            )
        except Exception as e:
            self._log(f"Error fatal: {e}", "ERROR")
            import traceback
            traceback.print_exc()
        finally:
            await client.close()

    async def _balance_loop(self, client: OKXClient) -> None:
        """Loop dedicado para actualizar el saldo."""
        while self.running:
            try:
                bal = await client.get_balance()
                self.current_exchange_balance = bal
            except Exception as e:
                pass
            await asyncio.sleep(15)

    async def _place_algo_order_safe(self, client: OKXClient, inst_id: str, pos_side: str, qty: Decimal, sl: Decimal = None, tp: Decimal = None, td_mode: str = "isolated") -> None:
        inst = self._instruments.get(inst_id)
        if inst:
            tick_sz = Decimal(inst.get("tickSz", "0.00001"))
            lot_sz = Decimal(inst.get("lotSz", "0.01"))
            from decimal import ROUND_HALF_UP, ROUND_DOWN
            
            # Round quantity to lot size
            qty = qty.quantize(lot_sz, rounding=ROUND_DOWN)
            
            # Round SL and TP to tick size
            if sl is not None:
                sl = (sl / tick_sz).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_sz
            if tp is not None:
                tp = (tp / tick_sz).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_sz
                
        # Call the client method
        await client.place_algo_order(inst_id, pos_side, qty, sl=sl, tp=tp, td_mode=td_mode)

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

    async def _sync_positions_history(self, client: OKXClient) -> None:
        try:
            res = await client._req("GET", "/api/v5/account/positions-history?instType=SWAP&limit=100", auth=True)
            if not res:
                return
            count = 0
            with get_session() as db:
                for r in res:
                    inst_id = r.get("instId")
                    if not inst_id:
                        continue
                    u_time_ms = float(r.get("uTime", 0))
                    if u_time_ms == 0:
                        continue
                    closed_at_dt = datetime.utcfromtimestamp(u_time_ms / 1000)
                    existing = db.query(Trade).filter(
                        Trade.symbol == inst_id,
                        Trade.closed_at == closed_at_dt
                    ).first()
                    if existing:
                        continue
                    side_raw = r.get("posSide", "long").lower()
                    side = TradeSide.LONG if side_raw == "long" else TradeSide.SHORT
                    entry = float(r.get("openAvgPx", 0))
                    exit_px = float(r.get("closeAvgPx", 0))
                    real_pnl = float(r.get("realizedPnl", 0))
                    qty = float(r.get("closeTotalPos", 0))
                    is_win = (exit_px >= entry) if side == TradeSide.LONG else (exit_px <= entry)
                    if is_win:
                        if real_pnl > 5.0:
                            reason = "TAKE_PROFIT_HIT"
                        elif real_pnl > 1.0:
                            reason = "TRAILING_HIT"
                        else:
                            reason = "BREAKEVEN_HIT"
                    else:
                        reason = "STOP_LOSS_HIT"
                    c_time_ms = float(r.get("cTime", u_time_ms))
                    opened_at_dt = datetime.utcfromtimestamp(c_time_ms / 1000)
                    db.add(Trade(
                        symbol=inst_id, side=side, strategy=Strategy.ST_EMA_REGIME_MTF,
                        status=TradeStatus.CLOSED, entry_price=entry, position_size=qty, remaining_size=0,
                        sl_price=entry * (0.95 if side == TradeSide.LONG else 1.05),
                        tp_price=entry * (1.10 if side == TradeSide.LONG else 0.90),
                        atr=entry * 0.015, leverage=int(float(r.get("lever", 10))),
                        realized_pnl=real_pnl, close_price=exit_px, close_reason=reason,
                        opened_at=opened_at_dt, closed_at=closed_at_dt,
                        risk_usd=float(FIXED_RISK_USDT), peak_price=entry
                    ))
                    
                    # Descansar la moneda por 1 hora si fue pérdida (Stop Loss)
                    if not is_win:
                        from models import Cooldown
                        until_dt = datetime.utcnow() + timedelta(hours=1)
                        db.add(Cooldown(symbol=inst_id, until=until_dt))
                        self._log(f"[{inst_id}] 🧊 Moneda enviada a descanso por 1 HORA (Hit SL).", "WARN")
                        
                    count += 1
                db.commit()
            if count:
                self._log(f"[SYNC] Sincronizados {count} trades históricos cerrados desde OKX.")
        except Exception as e:
            self._log(f"[SYNC] Error al sincronizar historial de posiciones: {e}", "WARN")

    async def _restore_trades(self, client: OKXClient) -> None:
        await self._sync_positions_history(client)
        with get_session() as db:
            open_trades = db.query(Trade).filter(
                Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
            ).count()
        if open_trades:
            self._log(f"[SYNC] {open_trades} operaciones restauradas desde SQLite.")
        
        await self._adopt_live(client)
        await self._self_heal_auditor(client)

    async def _adopt_live(self, client: OKXClient) -> None:
        try:
            positions = await client.get_positions()
            count = 0
            with get_session() as db:
                for pos in positions:
                    iid = pos["instId"]
                    qty_raw = Decimal(pos.get("pos", "0"))
                    if qty_raw == 0:
                        continue
                    
                    if iid not in self._instruments:
                        try:
                            res = await client._req("GET", f"/api/v5/public/instruments?instType=SWAP&instId={iid}")
                            if res:
                                self._instruments[iid] = res[0]
                        except Exception as e:
                            self._log(f"Error loading instrument details for {iid}: {e}", "WARN")
                    
                    if iid not in self._instruments:
                        continue
                        
                    side_raw = pos.get("posSide", "net").lower()
                    if side_raw == "net":
                        side = "long" if qty_raw > 0 else "short"
                    else:
                        side = "long" if side_raw == "long" else "short"

                    # Check if already open in DB
                    already_open = db.query(Trade).filter(
                        Trade.symbol == iid,
                        Trade.side == TradeSide(side),
                        Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
                    ).first()
                    if already_open:
                        continue

                    mgn_mode = pos.get("mgnMode", "isolated")
                    await self._dynamically_adopt_trade(client, db, pos, iid, side_raw, mgn_mode)
                    count += 1
                db.commit()
            if count:
                self._log(f"[SYNC] Adoptadas {count} posiciones pre-existentes de OKX.")
        except Exception as e:
            self._log(f"[SYNC] Error adoptando posiciones: {e}", "WARN")

    
    async def _dynamically_adopt_trade(self, client: OKXClient, db, pos: dict, iid: str, side_raw: str, mgn_mode: str) -> None:
        from models import Trade, TradeSide, Strategy, TradeStatus
        from decimal import Decimal
        from order_execution_engine import OrderExecutionEngine
        import lifecycle

        entry = Decimal(pos.get("avgPx", "0"))
        qty_raw = float(pos.get("pos", "0"))
        qty = abs(qty_raw)
        if entry == 0 or qty == 0:
            return

        current_price = Decimal(pos.get("last") or pos.get("markPx") or str(entry))
        
        if side_raw == "net":
            side = TradeSide.LONG if qty_raw > 0 else TradeSide.SHORT
        else:
            side = TradeSide.LONG if side_raw == "long" else TradeSide.SHORT
            
        side_str = "long" if side == TradeSide.LONG else "short"
        # Calcular ATR estricto como lo usa el bot
        atr_est = entry * Decimal("0.005") / Decimal("2.5")
        
        # Calcular niveles de ciclo de vida
        tp1_price = entry + (lifecycle.ATR_TP1 * atr_est) if side_str == "long" else entry - (lifecycle.ATR_TP1 * atr_est)
        tp2_price = entry + (lifecycle.ATR_TP2 * atr_est) if side_str == "long" else entry - (lifecycle.ATR_TP2 * atr_est)
        be_price  = entry + (lifecycle.ATR_BREAKEVEN * atr_est) if side_str == "long" else entry - (lifecycle.ATR_BREAKEVEN * atr_est)
        
        crossed_be  = (current_price >= be_price) if side_str == "long" else (current_price <= be_price)
        crossed_tp1 = (current_price >= tp1_price) if side_str == "long" else (current_price <= tp1_price)

        trade_status = TradeStatus.OPEN
        profit_lock_active = 0
        trailing_active = 0
        tp1_done = 0
        
        from risk import compute_sl, breakeven_sl
        
        if crossed_tp1:
            tp1_done = 1
            trailing_active = 1
            trade_status = TradeStatus.TRAILING
            tp_to_set = None
            df_15m = None
            try:
                df_15m = await client.candles(iid, "15m", 50)
                if df_15m is not None and not df_15m.empty:
                    ema21 = lifecycle._ema(df_15m['close'], 21).iloc[-1]
                    sl_to_set = Decimal(str(ema21))
                else:
                    sl_to_set = current_price * Decimal("0.98") if side_str == "long" else current_price * Decimal("1.02")
            except:
                sl_to_set = current_price * Decimal("0.98") if side_str == "long" else current_price * Decimal("1.02")
            state_msg = "[ALTA GANANCIA] - Trailing Stop"
            
        elif crossed_be:
            profit_lock_active = 1
            trade_status = TradeStatus.BREAKEVEN
            sl_to_set = breakeven_sl(entry, side_str, atr=atr_est)
            tp_to_set = tp1_price
            state_msg = "[POCA GANANCIA] - SL en Breakeven"
        else:
            sl_to_set = compute_sl(entry, side_str, atr_est)
            tp_to_set = tp1_price
            state_msg = "[PÉRDIDA/INICIO] - SL original"

        sl_to_set = Decimal(str(sl_to_set))
        
        # Verify it hasn't been added yet concurrently
        already_open = db.query(Trade).filter(
            Trade.symbol == iid,
            Trade.side == side,
            Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
        ).first()
        
        if already_open:
            return

        trade = Trade(
            symbol=iid, side=side, strategy=Strategy.ST_EMA_REGIME_MTF,
            entry_price=float(entry), position_size=qty, remaining_size=qty,
            sl_price=float(sl_to_set), tp_price=float(tp_to_set) if tp_to_set else None,
            tp1_price=float(tp1_price), tp2_price=float(tp2_price),
            profit_lock_price=float(be_price), profit_lock_sl=float(breakeven_sl(entry, side_str, atr_est)),
            atr=float(atr_est), risk_usd=float(FIXED_RISK_USDT), leverage=int(pos.get("lever", 10)),
            status=trade_status, highest_price=float(max(entry, current_price)), lowest_price=float(min(entry, current_price)),
            profit_lock_active=profit_lock_active, trailing_active=trailing_active,
            tp1_done=tp1_done
        )
        db.add(trade)
        db.commit()
        
        self._log(f"[{iid}] Adoptando dinámicamente: {state_msg}. Enviando protecciones nativas a OKX...")
        
        try:
            await client.cancel_algo_orders(iid, side_str)
            await self._place_algo_order_safe(
                client, iid, side_str, Decimal(str(qty)),
                sl=sl_to_set, tp=tp_to_set, td_mode=mgn_mode
            )
        except Exception as e:
            self._log(f"[{iid}] Error colocando seguros dinámicos en adopción: {e}", "ERROR")


    async def _self_heal_auditor(self, client: OKXClient) -> None:
        self._log("🛡️ AGENTE GUARDIÁN: Patrullando órdenes y posiciones de OKX...", "SYSTEM")
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
                    pos_side_raw = p.get("posSide", "long").lower()
                    qty_raw = float(p.get("pos", 0))
                    if abs(qty_raw) == 0:
                        continue
                    
                    if pos_side_raw == "net":
                        side = TradeSide.LONG if qty_raw > 0 else TradeSide.SHORT
                        pos_side = "net"
                    else:
                        side = TradeSide.LONG if pos_side_raw == "long" else TradeSide.SHORT
                        pos_side = pos_side_raw
                        
                    mgn_mode = p.get("mgnMode", "isolated")
                    entry = float(p.get("avgPx", 0))
                    qty = float(qty_raw)

                    if inst_id not in self._instruments:
                        try:
                            res = await client._req("GET", f"/api/v5/public/instruments?instType=SWAP&instId={inst_id}")
                            if res:
                                self._instruments[inst_id] = res[0]
                        except Exception as e:
                            self._log(f"Error loading instrument details for {inst_id} in auditor: {e}", "WARN")

                    # 1. Adopt orphans (filtering by symbol AND side to prevent cross-side collision)
                    trade = db.query(Trade).filter(
                        Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT]),
                        Trade.symbol == inst_id,
                        Trade.side == side
                    ).first()
                    if not trade:
                        self._log(f"[{inst_id}] 🤖 AUDITOR: Posición huérfana detectada ({pos_side}). Invocando Adopción Dinámica...", "SYSTEM")
                        await self._dynamically_adopt_trade(client, db, p, inst_id, pos_side_raw, mgn_mode)
                        db.refresh(trade)

                    # 2. Cleanup duplicates & missing (New 30/30/40 Architecture Aware)
                    # Filter pending algos by symbol AND side to avoid mixing them up
                    algos_for_sym = [
                        a for a in pending
                        if a.get("instId") == inst_id and a.get("posSide", "").lower() in (pos_side, "net", "")
                    ]
                    sl_count = sum(1 for a in algos_for_sym if a.get("slTriggerPx"))
                    tp_count = sum(1 for a in algos_for_sym if a.get("tpTriggerPx"))
                    
                    # AUTO-HEAL: Fill missing TP targets if adopted previously without them
                    if not getattr(trade, "tp1_price", None) or not getattr(trade, "tp2_price", None):
                        try:
                            import lifecycle
                            self._log(f"[{inst_id}] 🔧 AUTO-HEAL: Calculando TP1/TP2 faltantes...", "SYSTEM")
                            atr_est = float(trade.atr) if trade.atr else (trade.entry_price * 0.005 / 2.5)
                            s_side = trade.side.value if hasattr(trade.side, "value") else str(trade.side).split(".")[-1].lower()
                            if s_side == "long":
                                trade.tp1_price = trade.entry_price + (float(lifecycle.ATR_TP1) * atr_est)
                                trade.tp2_price = trade.entry_price + (float(lifecycle.ATR_TP2) * atr_est)
                            else:
                                trade.tp1_price = trade.entry_price - (float(lifecycle.ATR_TP1) * atr_est)
                                trade.tp2_price = trade.entry_price - (float(lifecycle.ATR_TP2) * atr_est)
                            db.commit()
                            self._log(f"[{inst_id}] 🔧 AUTO-HEAL OK: TP1={trade.tp1_price}, TP2={trade.tp2_price}", "SYSTEM")
                        except Exception as ah_err:
                            self._log(f"[{inst_id}] 🔧 AUTO-HEAL ERROR: {ah_err}", "ERROR")

                    # Expected counts from database based on the 30/30/40 phase
                    expected_sl = 1 if trade.sl_price else 0
                    expected_tp = 0
                    
                    if not getattr(trade, "tp1_filled", 0):
                        expected_tp = 2 # Expecting TP1 and TP2
                    elif not getattr(trade, "tp2_filled", 0):
                        expected_tp = 1 # Expecting TP2
                    else:
                        expected_tp = 0 # Trailing Phase, no TP

                    mismatch = (sl_count != expected_sl) or (tp_count != expected_tp) or (sl_count > 1) or (tp_count > 2)
                    
                    if mismatch:
                        if sl_count > 0 or tp_count > 0:
                            self._log(f"[{inst_id}] 🤖 AUDITOR: Inconsistencia detectada ({pos_side}) | Esperado: SL={expected_sl}, TP={expected_tp} | Encontrado: SL={sl_count}, TP={tp_count}. Limpiando...", "WARN")
                            payload = [{"instId": inst_id, "algoId": a["algoId"]} for a in algos_for_sym]
                            if payload:
                                try:
                                    await client._req("POST", "/api/v5/trade/cancel-algos", payload, auth=True)
                                    await asyncio.sleep(0.5)
                                except Exception as cancel_err:
                                    self._log(f"[{inst_id}] 🤖 AUDITOR: Falló cancelación en restauración: {cancel_err}", "WARN")
                        
                        self._log(f"[{inst_id}] 🤖 AUDITOR: Restaurando motor TP/SL Inteligente según Fase ({pos_side}).", "SYSTEM")
                        try:
                            # Obtener metadata del instrumento para los decimales
                            inst_data = self._instruments.get(inst_id, {})
                            tick_sz = Decimal(inst_data.get("tickSz", "0.0001"))
                            lot_sz = Decimal(inst_data.get("lotSz", "1"))

                            from order_execution_engine import OrderExecutionEngine
                            local_exec_engine = OrderExecutionEngine(client)

                            success = await local_exec_engine.restore_native_orders(
                                symbol=inst_id, 
                                side=side.value if hasattr(side, "value") else str(side), 
                                trade=trade, 
                                tick_sz=tick_sz, 
                                lot_sz=lot_sz
                            )
                            if success:
                                self._log(f"[{inst_id}] 🤖 AUDITOR: Restauración exitosa ({pos_side}).", "SYSTEM")
                            else:
                                self._log(f"[{inst_id}] 🤖 AUDITOR: Falló restauración de SL/TP ({pos_side}) - Motor retornó False", "ERROR")
                        except Exception as place_err:
                            self._log(f"[{inst_id}] 🤖 AUDITOR: Falló restauración de SL/TP ({pos_side}) por excepción: {place_err}", "ERROR")

        except Exception as e:
            self._log(f"Error general en auditor: {e}", "WARN")
        self._log("🛡️ AGENTE GUARDIÁN: Patrullaje completado. Todo en orden.", "SYSTEM")

    # ── Scanner Loop (15s) ────────────────────────────────────────────

    async def _scanner_loop(self, client: OKXClient) -> None:
        loop_counter = 0
        while self.running:
            if not self.scanning:
                await asyncio.sleep(1)
                continue
                
            try:
                loop_counter += 1
                await self._self_heal_auditor(client)
                await self._scan_tick(client)
                self.last_scan  = datetime.utcnow().strftime("%H:%M:%S UTC")
                self.last_error = ""
            except Exception as e:
                self.last_error = str(e)
                self._log(f"Error en scanner: {e}", "ERROR")
                
            # Interrupted sleep to allow instant resumption
            for _ in range(SCAN_INTERVAL_SECONDS):
                if not self.scanning or not self.running:
                    break
                await asyncio.sleep(1)

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

        # ALWAYS check real OKX positions and Auto-Adopt orphaned trades
        try:
            okx_pos = await client.get_positions()
            with get_session() as db:
                for p in okx_pos:
                    sym = p.get("instId")
                    active_syms.add(sym)
                    
                    # Auto-Adopt Orphaned Trades
                    existing = db.query(Trade).filter(Trade.symbol == sym, Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])).first()
                    if not existing:
                        pos_side = p.get("posSide", "").lower()
                        qty_raw = float(p.get("pos", 0))
                        if pos_side == "net":
                            side = TradeSide.LONG if qty_raw >= 0 else TradeSide.SHORT
                        else:
                            side = TradeSide.LONG if pos_side == "long" else TradeSide.SHORT
                        entry = float(p.get("avgPx", 0))
                        qty = abs(qty_raw)
                        lever = int(p.get("lever", 10))
                        
                        # Calculate ATR-based SL/TP levels using risk_manager
                        from risk_manager import risk_manager
                        inst_data = self._instruments.get(sym, {})
                        ct_val_f  = float(inst_data.get("ctVal", 1))
                        lot_sz_f  = float(inst_data.get("lotSz", 1))
                        atr_est   = entry * 0.008  # ~0.8% ATR estimate
                        levels = risk_manager.calculate_levels(entry, atr_est, pos_side, ct_val_f, lot_sz_f, "UNKNOWN")
                        
                        # --- Visión Retroactiva (Inteligencia del Guardián) ---
                        mark_px = float(p.get("markPx", entry))
                        nom_size = abs(qty * entry * ct_val_f)
                        
                        # Inferir TP1 y TP2 basado en el margen nominal remanente ($150 total)
                        tp1_done = False
                        tp2_done = False
                        if nom_size < 125.0:  # < 83% del original (30% vendido -> ~70% = $105)
                            tp1_done = True
                        if nom_size < 80.0:   # < 53% del original (60% vendido -> ~40% = $60)
                            tp2_done = True

                        # Inferir Breakeven basado en precio de marca actual
                        from risk import breakeven_sl
                        be_price_threshold = levels["profit_lock_trigger"]
                        be_reached = (mark_px >= be_price_threshold) if side == TradeSide.LONG else (mark_px <= be_price_threshold)
                        be_activated = tp1_done or be_reached
                        
                        # Definir SL a enviar a OKX
                        final_sl = levels["sl_price"]
                        if be_activated:
                            from decimal import Decimal as _D
                            final_sl = float(breakeven_sl(_D(str(entry)), pos_side, atr=_D(str(atr_est))))

                        t = Trade(
                            symbol=sym,
                            side=side,
                            strategy=Strategy.ST_EMA_REGIME_MTF,
                            status=TradeStatus.OPEN if not tp2_done else TradeStatus.TRAILING,
                            entry_price=entry,
                            qty=qty,
                            remaining_size=qty,
                            sl_price=final_sl,
                            tp_price=levels["tp_final"],
                            tp1_price=levels["tp1_price"],
                            tp2_price=levels["tp2_price"],
                            profit_lock_price=be_price_threshold,
                            atr=atr_est,
                            leverage=lever,
                            tp1_filled=1 if tp1_done else 0,
                            tp2_filled=1 if tp2_done else 0,
                            profit_lock_active=1 if be_activated else 0,
                            trailing_active=1 if tp2_done else 0,
                            highest_price=mark_px if side == TradeSide.LONG else 0.0,
                            lowest_price=mark_px if side == TradeSide.SHORT else 0.0,
                        )
                        db.add(t)
                        db.flush()
                        
                        log_msg = f"[{sym}] 🤖 GUARDIÁN ADOPTA: SL={final_sl:.6f} TP1={levels['tp1_price']:.6f} TP2={levels['tp2_price']:.6f}"
                        if tp1_done: log_msg += " [TP1✅]"
                        if tp2_done: log_msg += " [TP2✅][TRAIL🏃]"
                        if be_activated and not tp1_done: log_msg += " [BE🛡️]"
                        self._log(log_msg, "SYSTEM")
                        
                        try:
                            from order_execution_engine import OrderExecutionEngine
                            from decimal import Decimal as _D
                            _tick = _D(str(inst_data.get("tickSz", "0.0001")))
                            _lot  = _D(str(inst_data.get("lotSz", "1")))
                            _exec = OrderExecutionEngine(client)
                            
                            # Original qty estimate for OKX orders is not needed for place_native_tp_sl_orders, 
                            # because it calculates 30% of the current `total_qty` if TP1/TP2 are not done.
                            # But wait! If we already sold TP1, we don't send TP1. We send TP2.
                            # TP2 should be 30% of the ORIGINAL qty. The current qty is 70%.
                            # 30% of original is (30/70) * current = 42.8% of current.
                            # So if tp1_done is True and tp2_done is False, `place_native_tp_sl_orders` using 30% of `qty` is WRONG.
                            # Let's pass the estimated ORIGINAL qty to `place_native_tp_sl_orders`.
                            orig_qty = qty
                            if tp1_done and not tp2_done:
                                orig_qty = qty * (100.0 / 70.0)
                            elif tp2_done:
                                orig_qty = qty * (100.0 / 40.0)
                                
                            await _exec.place_native_tp_sl_orders(
                                symbol=sym, side=pos_side, total_qty=orig_qty,
                                tp1=levels["tp1_price"], tp2=levels["tp2_price"],
                                sl=final_sl, tick_sz=_tick, lot_sz=_lot,
                                tp1_done=tp1_done, tp2_done=tp2_done
                            )
                            self._log(f"[{sym}] 🟢 Órdenes nativas restauradas en OKX.", "SYSTEM")
                        except Exception as _oe:
                            self._log(f"[{sym}] Error ordenes adoptadas: {_oe}", "ERROR")
                db.commit()
        except Exception as e:
            self._log(f"Error auto-adopting OKX positions: {e}", "ERROR")

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
            if iid in cdwn_syms or iid in self.compliance_restricted or iid not in self._instruments:
                continue
            try:
                df_1h, df_15m, df_5m = await asyncio.gather(
                    client.candles(iid, "1H", 300),
                    client.candles(iid, "15m", 300),
                    client.candles(iid, "5m", 150),
                )
                for sig in [
                    self.strat_pdh.signal(iid, df_1h, df_15m, df_5m),
                    self.strat_fvg.signal(iid, df_1h, df_15m, df_5m),
                    self.strat_ob.signal(iid, df_1h, df_15m, df_5m),
                    self.strat_amd.signal(iid, df_1h, df_15m, df_5m),
                    self.strat_st_ema.signal(iid, df_1h, df_15m, df_5m),
                ]:
                    if sig:
                        candidates.append(sig)
            except Exception as e:
                self._log(f"Error procesando {iid}: {e}", "ERROR")
                continue

        abiertas_ahora = len(active_syms)
        slots_libres   = MAX_CONCURRENT_TRADES - abiertas_ahora

        self._log(
            f"🔎 AGENTE ESCÁNER: Escaneo completado en {len(universe)} pares | "
            f"Candidatos: {len(candidates)} | Activas: {abiertas_ahora}/{MAX_CONCURRENT_TRADES} | "
            f"Slots libres: {slots_libres}",
            "SYSTEM"
        )

        if candidates and slots_libres <= 0:
            self._log(f"⛔ {len(candidates)} señal(es) encontrada(s) pero ya hay {abiertas_ahora} operaciones activas (máx {MAX_CONCURRENT_TRADES}).", "WARN")

        # ── REVISIÓN DE SALIDA POR SEÑAL CONTRARIA (OPPOSITE REGIME) ────────────────
        temp_active = set(active_syms)
        to_open = []
        
        # Filtrar candidatos
        with get_session() as db:
            for sig in candidates:
                if sig.symbol in temp_active:
                    if sig.strategy == "ST_EMA_REGIME_MTF":
                        t = db.query(Trade).filter(
                            Trade.symbol == sig.symbol, 
                            Trade.status.notin_([TradeStatus.CLOSED, TradeStatus.EARLY_EXIT])
                        ).first()
                        if t and getattr(t.strategy, "value", str(t.strategy)) == sig.strategy:
                            side_str = t.side.value if hasattr(t.side, "value") else str(t.side)
                            if (side_str == "long" and sig.side == "short") or (side_str == "short" and sig.side == "long"):
                                self._log(f"[{sig.symbol}] 🚨 SEÑAL CONTRARIA DETECTADA ({sig.reason}). Cerrando posición inmediatamente.", "SYSTEM")
                                # Usaremos un task de asyncio para cerrarlo sin bloquear
                                from lifecycle import LifecycleDecision, Action
                                decision = LifecycleDecision(
                                    action=Action.CLOSE_MARKET,
                                    reason="OPPOSITE_SIGNAL_EXIT",
                                    log_message=f"🛑 Salida por Tendencia Contraria: {sig.reason}"
                                )
                                # Obtener qty y ct_val real
                                inst = self._instruments.get(sig.symbol)
                                ct_val = Decimal(inst["ctVal"]) if inst else Decimal("1")
                                tick_info = next((x for x in universe if x["instId"] == sig.symbol), None)
                                current_price = Decimal(str(tick_info.get("last", "0"))) if tick_info else Decimal("0")
                                if current_price > 0:
                                    asyncio.create_task(self._apply_decision(client, {"id": t.id, "symbol": t.symbol, "side": side_str, "qty": Decimal(str(t.qty))}, decision, current_price, ct_val))
                else:
                    to_open.append(sig)

        # ── APERTURA SIMULTÁNEA DE HASTA 10 PARES ──────────────────────────────
        for sig in sorted(to_open, key=lambda s: s.score, reverse=True):
            if len(temp_active) >= MAX_CONCURRENT_TRADES:
                break
            if sig.symbol in temp_active:
                continue
            to_open.append(sig)
            temp_active.add(sig.symbol)   # reservar el slot ya

        if not to_open:
            return

        self._log(
            f"🚀 LANZANDO {len(to_open)} ENTRADAS EN PARALELO: "
            + ", ".join(s.symbol for s in to_open),
            "SYSTEM"
        )

        results = await asyncio.gather(
            *[self._open_trade(client, sig) for sig in to_open],
            return_exceptions=True
        )

        opened = sum(1 for r in results if r is True)
        if opened > 0:
            self._log(f"✅ {opened} operación(es) abiertas simultáneamente este ciclo.", "SYSTEM")

    async def _open_trade(self, client: OKXClient, sig: Signal) -> bool:
        iid  = sig.symbol
        inst = self._instruments.get(iid)
        if not inst:
            return
        ct_val = Decimal(inst["ctVal"])
        lot_sz = Decimal(inst["lotSz"])
        min_sz = Decimal(inst["minSz"])
        tick_sz = Decimal(inst["tickSz"])
        
        from decimal import ROUND_HALF_UP, ROUND_DOWN
        def _round_tick(val: Decimal) -> Decimal:
            return (val / tick_sz).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_sz
            
        # Obtain live market price for immediate LIMIT fill
        live_entry = Decimal(str(sig.entry_price))
        try:
            ticker_data = await client._req("GET", f"/api/v5/market/ticker?instId={iid}")
            if ticker_data:
                if sig.side == "long":
                    live_entry = Decimal(str(ticker_data[0].get("askPx", ticker_data[0].get("last"))))
                else:
                    live_entry = Decimal(str(ticker_data[0].get("bidPx", ticker_data[0].get("last"))))
        except Exception as e:
            self._log(f"[{iid}] Error fetching live ticker for entry: {e}", "WARN")
            
        entry_price = float(_round_tick(live_entry))
        atr = float(sig.atr_5m)
        
        from risk_manager import risk_manager
        levels = risk_manager.calculate_levels(entry_price, atr, sig.side, float(ct_val), float(lot_sz), sig.strategy)
        
        sl = float(_round_tick(Decimal(str(levels["sl_price"]))))
        
        qty = compute_qty(Decimal(str(entry_price)), Decimal(str(sl)), ct_val, lot_sz)
        if qty < min_sz:
            self._log(f"{iid}: qty {qty} < min {min_sz}. Skip.")
            return
            
        try:
            order_side = "buy" if sig.side == "long" else "sell"
            pos_side   = "long" if sig.side == "long" else "short"
            
            try:
                await client.set_leverage(iid, LEVERAGE, pos_side)
            except Exception:
                pass
                
            from order_execution_engine import OrderExecutionEngine
            from trade_state_repository import trade_state_repo
            from discord_notifier import discord_notifier
            
            execution_engine = OrderExecutionEngine(client)
            
            # Enviar Orden LIMIT
            success, ord_id = await execution_engine.execute_limit_order(iid, sig.side, float(qty), entry_price)
            if success:
                # Store in pending entries memory instead of DB to allow 15 minute wait
                import time
                self._pending_entries[ord_id] = {
                    "symbol": iid,
                    "side": sig.side,
                    "strategy": sig.strategy,
                    "entry_price": entry_price,
                    "qty": float(qty),
                    "atr": atr,
                    "ts": time.time(),
                    "levels": levels,
                    "tick_sz": tick_sz,
                    "lot_sz": lot_sz
                }
                self._log(f"[{iid}] ⏳ Orden Límite enviada (ordId: {ord_id}). Esperando 15 mins para fill...", "SYSTEM")
                return True
            else:
                err_str = str(ord_id)
                self._log(f"[{iid}] ❌ RECHAZADO: {err_str}", "ERROR")
                if "51155" in err_str or "compliance" in err_str.lower():
                    self.compliance_restricted.add(iid)
                    self._log(f"[{iid}] Símbolo con restricciones de cumplimiento OKX. Agregado a lista de exclusión local.", "WARN")
                return False
                
        except Exception as e:
            err_str = str(e)
            if "51155" in err_str:
                self.compliance_restricted.add(iid)
                self._log(f"[{iid}] Símbolo con restricciones de cumplimiento OKX (51155). Agregado a lista de exclusión local.", "WARN")
            else:
                self._log(f"Error general en _open_trade para {iid}: {e}", "ERROR")
                self._log(
                    f"[{iid}] 🚀 {sig.side.upper()} vía {sig.strategy} | "
                    f"Entrada: {sig.entry_price:.6f} | SL: {sl:.6f} | TP: Dinámico Trifuerza | {sig.reason}"
                )
                from discord_notifier import discord_notifier
                await discord_notifier.log_error(f"open_trade {iid}", str(e))
            return False

    # ── Stale Order Loop ──────────────────────────────────────────────

    async def _monitor_pending_entries_loop(self, client: OKXClient) -> None:
        """Monitorea órdenes límite pendientes. Cancela a los 15 mins. Si se llenan, crea el Trade y TPs."""
        while self.running:
            await asyncio.sleep(5)
            try:
                import time
                now = time.time()
                pending_keys = list(self._pending_entries.keys())
                for ord_id in pending_keys:
                    data = self._pending_entries.get(ord_id)
                    if not data: continue
                    
                    # Chequear tiempo (15 minutos)
                    from config import STALE_ORDER_MINUTES
                    if now - data["ts"] > (STALE_ORDER_MINUTES * 60):
                        try:
                            cancel_body = {"instId": data["symbol"], "ordId": ord_id}
                            await client._req("POST", "/api/v5/trade/cancel-order", body=cancel_body, auth=True)
                        except: pass
                        self._log(f"[{data['symbol']}] ⏰ Orden Límite expiró tras {STALE_ORDER_MINUTES}m. Cancelada.", "WARN")
                        self._pending_entries.pop(ord_id, None)
                        continue

                    # Poll OKX for state
                    try:
                        poll_data = await client._req("GET", f"/api/v5/trade/order?instId={data['symbol']}&ordId={ord_id}", auth=True)
                        if not poll_data: continue
                        state = poll_data[0]["state"]
                        
                        if state in ("canceled", "mismatch"):
                            self._pending_entries.pop(ord_id, None)
                            continue
                            
                        if state == "filled":
                            from models import Trade, TradeSide
                            from trade_state_repository import trade_state_repo
                            from discord_notifier import discord_notifier
                            from order_execution_engine import OrderExecutionEngine
                            
                            levels = data["levels"]
                            t = Trade(
                                symbol=data["symbol"],
                                side=TradeSide.LONG if data["side"] == "long" else TradeSide.SHORT,
                                strategy=data["strategy"],
                                entry_price=data["entry_price"],
                                position_size=data["qty"],
                                remaining_size=data["qty"],
                                sl_price=levels["sl_price"],
                                tp_price=levels["tp_final"],
                                atr=data["atr"],
                                tp1_price=levels["tp1_price"],
                                tp2_price=levels["tp2_price"],
                                profit_lock_price=levels["profit_lock_trigger"],
                                leverage=LEVERAGE
                            )
                            trade_state_repo.save_new_trade(t)
                            
                            exec_engine = OrderExecutionEngine(client)
                            pos_side = "long" if data["side"] == "long" else "short"
                            
                            await exec_engine.place_native_tp_sl_orders(
                                symbol=data["symbol"], side=pos_side, total_qty=data["qty"],
                                tp1=levels["tp1_price"], tp2=levels["tp2_price"], sl=levels["sl_price"],
                                tick_sz=data["tick_sz"], lot_sz=data["lot_sz"]
                            )
                            
                            await discord_notifier.log_entry(data["symbol"], data["side"], data["entry_price"], data["atr"])
                            self._log(f"[{data['symbol']}] ✅ Orden Límite FILLED. Trade guardado y TPs/SL colocados.", "SYSTEM")
                            
                            self._pending_entries.pop(ord_id, None)
                    except Exception as e:
                        pass
            except Exception as e:
                self._log(f"Error en monitor_pending_entries_loop: {e}", "WARN")

    # ── Reconcile Loop (30s) ─────────────────────────────────────────

    async def _reconcile_loop(self, client: OKXClient) -> None:
        """El Agente Supervisor: vigila continuamente las posiciones activas."""
        loop_count = 0
        while self.running:
            try:
                if loop_count % 60 == 0:  # Cada 60 segundos
                    await self._sync_positions_history(client)
                if loop_count % 3 == 0:  # Cada ~3 segundos
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
            def _get_side(p):
                ps = p.get("posSide", "").lower()
                if ps == "net":
                    return "long" if float(p.get("pos", "0")) >= 0 else "short"
                return "long" if ps == "long" else "short"

            okx_pos_map = {(p["instId"], _get_side(p)): p for p in okx_pos}
            self.last_positions = okx_pos_map
            self._log(f"Synced {len(okx_pos_map)} positions: {list(okx_pos_map.keys())[:3]}...", "SYSTEM")
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
                    trade_side_str = t.side.value if hasattr(t.side, "value") else str(t.side)
                    key = (t.symbol, trade_side_str.lower())
                    if key in okx_pos_map:
                        # Position is active. Sync real entry price and quantity.
                        p = okx_pos_map[key]
                        real_entry = float(p.get("avgPx", 0))
                        real_qty = float(abs(Decimal(p.get("pos", "0"))))
                        if real_entry > 0:
                            if t.entry_price != real_entry or t.qty != real_qty:
                                self._log(f"[{t.symbol}] 🔄 Sincronizando Entrada/Cantidad real de OKX ({trade_side_str}): {t.entry_price} -> {real_entry} | Qty: {t.qty} -> {real_qty}")
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
                                    if t.status == TradeStatus.BREAKEVEN or t.profit_lock_active:
                                        close_reason = "BREAKEVEN_HIT"
                                    elif t.status == TradeStatus.TRAILING or t.trailing_active:
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
                                
                                if real_pnl < 0:
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
                    "atr":         Decimal(str(t.atr or (t.entry_price * 0.008))),
                    "risk":        Decimal(str(t.risk_usd)),
                    "be_done":     bool(t.profit_lock_active),
                    "trail_done":  bool(t.trailing_active),
                    "trail_sl":    Decimal(str(t.sl_price)) if t.trailing_active else None,
                    "peak":        Decimal(str(getattr(t, "highest_price" if getattr(t.side, "value", str(t.side)) == "long" else "lowest_price"))) if getattr(t, "highest_price" if getattr(t.side, "value", str(t.side)) == "long" else "lowest_price") else None,
                    "status":      t.status,
                    "opened_at":   t.opened_at,
                    "tp1_done":    bool(getattr(t, "tp1_done", 0) or getattr(t, "tp1_filled", 0)),
                    "tp2_done":    bool(getattr(t, "tp2_done", 0) or getattr(t, "tp2_filled", 0)),
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

            # Derive original SL and TP to ensure consistent progress calculations
            original_sl = compute_sl(td["entry"], td["side"], td["atr"])
            inst        = self._instruments.get(td["symbol"])
            ct_val      = Decimal(inst["ctVal"]) if inst else Decimal("1")

            # Fetch 15m candles if trailing or nearing trailing (for all strategies)
            df_15m = None
            is_mtf = td.get("strategy", "") == "ST_EMA_REGIME_MTF"
            if td["trail_done"] or td["tp1_done"] or is_mtf:
                try:
                    df_15m = await client.candles(td["symbol"], "15m", 250)
                except Exception:
                    pass
            
            if is_mtf:
                if df_15m is not None and not df_15m.empty:
                    if self.strat_st_ema.exit_signal(td["side"], df_15m):
                        from lifecycle import Action, LifecycleDecision
                        decisions = [LifecycleDecision(action=Action.CLOSE_MARKET, reason="OPPOSITE_SIGNAL", log_message=f"🛑 Salida por señal contraria MTF: {td['symbol']}")]
                        for decision in decisions:
                            await self._apply_decision(client, td, decision, price, ct_val)
                        continue

                from lifecycle import evaluate_supertrend_mtf
                decisions = evaluate_supertrend_mtf(
                    side=td["side"], entry=td["entry"], current_sl=td["sl"],
                    price=price, atr_15m=td["atr"], be_activated=td["be_done"],
                    trail_activated=td["trail_done"], trail_sl=td["trail_sl"], df_15m=df_15m
                )
            else:
                decisions = evaluate(
                    side=td["side"], entry=td["entry"], tp=td["tp"],
                    current_sl=td["sl"], price=price, qty=td["qty"],
                    ct_val=ct_val, atr_5m=td["atr"], risk_usd=td["risk"],
                    be_activated=td["be_done"], trail_activated=td["trail_done"],
                    trail_sl=td["trail_sl"], peak_price=new_peak,
                    strategy_name=td.get("strategy", ""),
                    tp1_done=td["tp1_done"],
                    tp2_done=td.get("tp2_done", False),
                    opened_at=td["opened_at"],
                    df_5m=df_15m, # Pasamos el de 15m usando el parámetro existente
                )

            for decision in decisions:
                await self._apply_decision(client, td, decision, price, ct_val)

            # Update peak in DB
            if new_peak != td["peak"]:
                try:
                    with get_session() as db:
                        t = db.query(Trade).filter(Trade.id == td["id"]).first()
                        if t:
                            if td["side"] == "long":
                                t.highest_price = float(new_peak)
                            else:
                                t.lowest_price = float(new_peak)
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

        # Get margin mode dynamically from cache
        p_key = (symbol, side.lower())
        mgn_mode = "isolated"
        if hasattr(self, "last_positions") and self.last_positions:
            pos_obj = self.last_positions.get(p_key)
            if pos_obj:
                mgn_mode = pos_obj.get("mgnMode", "isolated")

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
                        if decision.reason == "BREAKEVEN_ACTIVATE":
                            trade.profit_lock_active = 1
                            trade.status = TradeStatus.BREAKEVEN
                            # Use modify_native_sl to move SL on OKX without cancelling TP orders
                            from order_execution_engine import OrderExecutionEngine
                            inst = self._instruments.get(symbol, {})
                            from decimal import Decimal as _D
                            _tick = _D(str(inst.get("tickSz", "0.0001")))
                            _exec = OrderExecutionEngine(client)
                            await _exec.modify_native_sl(symbol, side, float(new_sl), _tick)
                            await notifier.notify_breakeven(symbol, float(new_sl))
                        elif decision.reason in ("TRAIL_ACTIVATE", "TRAIL_MOVE"):
                            trade.trailing_active = 1
                            trade.status   = TradeStatus.TRAILING
                            from order_execution_engine import OrderExecutionEngine
                            inst = self._instruments.get(symbol, {})
                            from decimal import Decimal as _D
                            _tick = _D(str(inst.get("tickSz", "0.0001")))
                            _exec = OrderExecutionEngine(client)
                            
                            if decision.reason == "TRAIL_ACTIVATE":
                                trade.tp_price = None
                                await client.cancel_algo_orders(symbol, "long" if side == "long" else "short")
                                await self._place_algo_order_safe(client, symbol, "long" if side == "long" else "short", Decimal(str(trade.qty)), sl=Decimal(str(new_sl)), td_mode=mgn_mode)
                                await notifier.notify_trailing(symbol, float(new_sl))
                            else:
                                await _exec.modify_native_sl(symbol, side, float(new_sl), _tick)
                        db.add(TradeEvent(trade_id=trade_id, event_type=decision.reason,
                                          message=decision.log_message, price=float(price)))
                        db.commit()
                        break  # success

                    elif decision.action == Action.CLOSE_PARTIAL:
                        orig_qty = Decimal(str(trade.qty))
                        inst = self._instruments.get(symbol)
                        min_sz = Decimal(inst["minSz"]) if inst else Decimal("0.01")
                        lot_sz = Decimal(inst["lotSz"]) if inst else Decimal("0.01")
                        
                        # Triforce Strategy:
                        # TP1: Close 30% of original. (Remaining = 70%)
                        # TP2: Close 30% of original. (Remaining = 40%)
                        # Since trade.qty represents the CURRENT amount, we must calculate the fraction to close.
                        if decision.reason == "TP1_HIT":
                            close_qty_raw = orig_qty * Decimal("0.30")
                        elif decision.reason == "TP2_HIT":
                            # We currently have 70% of the original. We want to close 30% of the original.
                            # So we close 30/70 of the current amount.
                            close_qty_raw = orig_qty * (Decimal("30") / Decimal("70"))
                        else:
                            # Fallback if somehow called for another reason
                            close_qty_raw = orig_qty * Decimal("0.50")
                            
                        close_qty_rounded = close_qty_raw.quantize(lot_sz, rounding=ROUND_DOWN)
                        new_qty = orig_qty - close_qty_rounded
                        
                        # If partial to close is less than min_sz OR remaining is less than min_sz, we close the entire position
                        if close_qty_rounded < min_sz or new_qty < min_sz:
                            self._log(f"[{symbol}] Posición demasiado pequeña para dividir ({orig_qty} contratos). Cerrando toda la posición por {decision.reason}.", "SYSTEM")
                            await client.close_position(symbol, side)
                            await client.cancel_algo_orders(symbol, "long" if side == "long" else "short")
                            trade.status = TradeStatus.CLOSED
                            if decision.reason == "TP1_HIT":
                                trade.tp1_filled = 1
                            elif decision.reason == "TP2_HIT":
                                trade.tp2_filled = 1
                            trade.close_price = float(price)
                            trade.close_reason = decision.reason
                            trade.closed_at = datetime.utcnow()
                            trade.realized_pnl = float(pnl_usd(Decimal(str(trade.entry_price)), price, orig_qty, ct_val, side))
                            db.add(TradeEvent(trade_id=trade_id, event_type="CLOSE",
                                              message=f"Cierre completo en {decision.reason} por tamaño de contrato mínimo.", price=float(price)))
                            
                            # Cooldown solo en pérdidas
                            if trade.realized_pnl < 0:
                                until = datetime.utcnow() + timedelta(minutes=COOLDOWN_MINUTES)
                                ex = db.query(Cooldown).filter(Cooldown.symbol == symbol).first()
                                if ex:
                                    ex.until = until
                                else:
                                    db.add(Cooldown(symbol=symbol, until=until))
                            db.commit()
                            
                            await notifier.notify_close(symbol, side, "TAKE_PROFIT_HIT", float(trade.entry_price), float(price), trade.realized_pnl)
                        else:
                            # Standard partial close
                            self._log(f"[{symbol}] Ejecutando cierre parcial de {close_qty_rounded} contratos por {decision.reason}...")
                            try:
                                await client.close_partial_position(symbol, side, close_qty_rounded)
                                trade.qty = float(new_qty)
                                if decision.reason == "TP1_HIT":
                                    trade.tp1_filled = 1
                                elif decision.reason == "TP2_HIT":
                                    trade.tp2_filled = 1
                                await client.cancel_algo_orders(symbol, "long" if side == "long" else "short")
                                await self._place_algo_order_safe(client, symbol, "long" if side == "long" else "short", new_qty, sl=Decimal(str(trade.sl_price)), tp=Decimal(str(trade.tp_price)) if trade.tp_price else None, td_mode=mgn_mode)
                                db.add(TradeEvent(trade_id=trade_id, event_type=decision.reason,
                                                  message=decision.log_message + f" Vendido: {close_qty_rounded} contratos.", price=float(price)))
                                db.commit()
                                await notifier.notify_tp1(symbol, float(close_qty_rounded), float(price)) # Assuming same format works for TP2
                                self._log(f"[{symbol}] Cierre parcial completado con éxito para {decision.reason}.")
                            except Exception as partial_err:
                                self._log(f"[{symbol}] Error ejecutando cierre parcial: {partial_err}", "ERROR")
                                raise partial_err
                        break

                    elif decision.action == Action.CANCEL_TP:
                        trade.tp_price = None
                        await client.cancel_algo_orders(symbol, "long" if side == "long" else "short")
                        # Re-place SL only
                        await self._place_algo_order_safe(client, symbol, "long" if side == "long" else "short", Decimal(str(trade.qty)), sl=Decimal(str(trade.sl_price)), td_mode=mgn_mode)
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
                        real_pnl = float(pnl_usd(Decimal(str(td["entry"])), price, Decimal(str(td["qty"])), ct_val, side))
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
                        # Cooldown solo en pérdidas
                        if real_pnl < 0:
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
                    Trade.position_closed == 0
                ).all()
        except Exception:
            return []

    def get_closed_trades(self, n: int = 10) -> list[Trade]:
        try:
            with get_session() as db:
                return db.query(Trade).filter(
                    Trade.position_closed == 1
                ).order_by(Trade.closed_at.desc()).limit(n).all()
        except Exception:
            return []

    def get_stats(self) -> dict:
        try:
            from datetime import datetime, timedelta
            now_utc = datetime.utcnow()
            # 11:00 PM UTC-5 is 04:00 AM UTC the next day.
            # So the cutoff for "today" starting at 11 PM UTC-5 is 04:00 UTC.
            cutoff_utc = now_utc.replace(hour=4, minute=0, second=0, microsecond=0)
            if now_utc < cutoff_utc:
                cutoff_utc -= timedelta(days=1)
                
            with get_session() as db:
                closed = db.query(Trade).filter(Trade.realized_pnl.isnot(None)).all()
                closed_today = [t for t in closed if t.closed_at and t.closed_at >= cutoff_utc]
            total     = len(closed)
            wins      = [t for t in closed if (t.realized_pnl or 0) > 0]
            losses    = [t for t in closed if (t.realized_pnl or 0) < 0]
            total_pnl = sum(t.realized_pnl or 0 for t in closed)
            gw = sum(t.realized_pnl for t in wins)
            gl = abs(sum(t.realized_pnl for t in losses))
            
            pnl_today = sum(t.realized_pnl or 0 for t in closed_today)
            
            avg_win = (gw / len(wins)) if wins else 0
            avg_loss = (gl / len(losses)) if losses else 0
            
            return {
                "total_trades":   total,
                "wins_count":     len(wins),
                "losses_count":   len(losses),
                "win_rate":       (len(wins) / total * 100) if total else 0,
                "profit_factor":  (gw / gl) if gl > 0 else 0,
                "avg_win":        avg_win,
                "avg_loss":       avg_loss,
                "total_pnl":      total_pnl,
                "pnl_today":      pnl_today,
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
