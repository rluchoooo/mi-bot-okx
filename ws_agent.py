import asyncio
import json
import websockets
from decimal import Decimal
from datetime import timezone
import logging

from models import get_session, Trade
from lifecycle import evaluate
from risk import compute_sl
from config import FIXED_RISK_USDT
from scanner import OKXClient

class WSAgent:
    def __init__(self, runtime):
        """
        runtime is the QuantumBotRuntime instance from app.py
        """
        self.runtime = runtime
        self.running = False
        
    async def start(self):
        self.running = True
        self.runtime._log("[Agente WebSocket] Arrancando...", "SYSTEM")
        uri = "wss://ws.okx.com:8443/ws/v5/public"
        
        # Helper to safely parse strings to Decimal
        def _dec(v):
            if v is None: return None
            return Decimal(str(v))
            
        while self.running:
            try:
                async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as ws:
                    self.runtime._log("[Agente WebSocket] ⚡ Conectado a OKX en tiempo real (Tickers).", "SYSTEM")
                    subscribed = set()
                    
                    while self.running:
                        # 1. Fetch open trades from local DB
                        trades = self.runtime.get_open_trades()
                        current_symbols = {t.symbol for t in trades}
                        
                        # Subscribe to new active symbols
                        to_sub = current_symbols - subscribed
                        if to_sub:
                            args = [{"channel": "tickers", "instId": s} for s in to_sub]
                            await ws.send(json.dumps({"op": "subscribe", "args": args}))
                            subscribed.update(to_sub)
                            
                        # Unsubscribe from closed symbols
                        to_unsub = subscribed - current_symbols
                        if to_unsub:
                            args = [{"channel": "tickers", "instId": s} for s in to_unsub]
                            await ws.send(json.dumps({"op": "unsubscribe", "args": args}))
                            subscribed.difference_update(to_unsub)
                            
                        # 2. Wait for message (5s timeout to allow loop to re-check DB)
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                            data = json.loads(msg)
                            
                            if "data" in data and isinstance(data["data"], list):
                                for tick in data["data"]:
                                    sym = tick["instId"]
                                    last_px = Decimal(tick["last"])
                                    
                                    # Find matching trades
                                    active_trades = [t for t in trades if t.symbol == sym]
                                    for t in active_trades:
                                        td = {
                                            "id": t.id,
                                            "symbol": t.symbol,
                                            "entry": _dec(t.entry_price),
                                            "sl": _dec(t.sl_price) if t.sl_price else Decimal("0"),
                                            "tp": _dec(t.tp_price) if t.tp_price else None,
                                            "qty": _dec(t.qty),
                                            "side": t.side if isinstance(t.side, str) else t.side.value,
                                            "atr": _dec(t.atr_5m),
                                            "trail_activated": bool(t.trail_activated),
                                            "be_activated": bool(t.be_activated),
                                            "tp1_done": bool(t.tp1_done),
                                            "tp2_done": bool(getattr(t, "tp2_done", False)),
                                            "opened_at": t.opened_at,
                                            "strategy": t.strategy if isinstance(t.strategy, str) else t.strategy.value,
                                            "trail_sl": _dec(t.trail_sl) if t.trail_sl else None,
                                            "peak_price": _dec(t.peak_price) if t.peak_price else None,
                                        }
                                        
                                        # Track Peak Price dynamically in milliseconds
                                        changed = False
                                        if td["side"] == "long":
                                            if not td["peak_price"] or last_px > td["peak_price"]:
                                                td["peak_price"] = last_px
                                                changed = True
                                        else:
                                            if not td["peak_price"] or last_px < td["peak_price"]:
                                                td["peak_price"] = last_px
                                                changed = True
                                                
                                        if changed:
                                            with get_session() as db:
                                                db_trade = db.query(Trade).get(t.id)
                                                if db_trade:
                                                    db_trade.peak_price = float(last_px)
                                                    db.commit()
                                                    
                                        orig_sl = compute_sl(td["entry"], td["side"], td["atr"])
                                        
                                        decisions = evaluate(
                                            side=td["side"], entry=td["entry"], tp=td["tp"],
                                            current_sl=td["sl"], price=last_px, qty=td["qty"],
                                            ct_val=Decimal("1.0"), atr_5m=td["atr"], risk_usd=Decimal(str(FIXED_RISK_USDT)),
                                            be_activated=td["be_activated"], trail_activated=td["trail_activated"],
                                            trail_sl=td["trail_sl"], peak_price=td["peak_price"],
                                            strategy_name=str(td["strategy"]),
                                            tp1_done=td["tp1_done"],
                                            tp2_done=td["tp2_done"],
                                            opened_at=td["opened_at"],
                                        )
                                        
                                        for d in decisions:
                                            # Execute instantly via Runtime
                                            client = OKXClient(
                                                api_key=self.runtime.api_key, 
                                                api_secret=self.runtime.api_secret, 
                                                passphrase=self.runtime.passphrase, 
                                                simulated=self.runtime.simulated
                                            )
                                            d.log_message = f"[⚡ WS] {d.log_message}"
                                            
                                            # Fetch ct_val from runtime._instruments cache if available, else 1.0 (fallback)
                                            # Wait, runtime._instruments might not be populated if scanner hasn't run yet?
                                            # It should be, but let's be safe.
                                            inst = getattr(self.runtime, "_instruments", {}).get(sym)
                                            ct_val = Decimal(inst["ctVal"]) if inst else Decimal("1.0")
                                            
                                            await self.runtime._apply_decision(client, td, d, last_px, ct_val)
                                            
                        except asyncio.TimeoutError:
                            continue
            except Exception as e:
                self.runtime._log(f"[Agente WebSocket] Reconectando en 5s... Error: {e}", "WARN")
                await asyncio.sleep(5)
                
    def stop(self):
        self.running = False
