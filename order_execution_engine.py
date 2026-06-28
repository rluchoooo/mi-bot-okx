import asyncio
from decimal import Decimal
from discord_notifier import discord_notifier


def clean_num(val, precision=8) -> str:
    if val is None or val == "":
        return ""
    if isinstance(val, float):
        val = round(val, precision)
    d = Decimal(str(val))
    s = f"{d:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


class OrderExecutionEngine:
    def __init__(self, client):
        self.client = client
        self.backoff_sequence = [3, 5, 10, 15]

    async def execute_limit_order(self, symbol: str, side: str, qty: float, price: float):
        """
        Creates a LIMIT order close to the price and waits for FILLED confirmation.
        Returns (bool, str) -> (success, error_msg)
        """
        for delay in self.backoff_sequence:
            try:
                body = {
                    "instId": symbol,
                    "tdMode": "isolated",
                    "side": "buy" if side == "long" else "sell",
                    "posSide": side,
                    "ordType": "limit",
                    "px": clean_num(price),
                    "sz": clean_num(qty)
                }
                data = await self.client._req("POST", "/api/v5/trade/order", body=body, auth=True)

                if data and len(data) > 0:
                    item = data[0]
                    if item.get("sCode") != "0":
                        err_msg = f"Rechazado por OKX: {item.get('sMsg')} (sCode: {item.get('sCode')})"
                        await discord_notifier.log_error("OrderExecutionEngine.execute_limit_order", err_msg)
                        return False, err_msg

                    ord_id = item.get("ordId")
                    if not ord_id:
                        return False, "No ordId returned"

                    return True, ord_id

            except Exception as e:
                await discord_notifier.log_error("OrderExecutionEngine.execute_limit_order", str(e))
                return False, str(e)
            await asyncio.sleep(delay)

        return False, "Max retries reached"

    async def execute_tp_closure(self, symbol: str, side: str, qty: float) -> bool:
        """
        Closes a specific quantity at market for TP execution.
        """
        for delay in self.backoff_sequence:
            try:
                body = {
                    "instId": symbol,
                    "tdMode": "isolated",
                    "side": "sell" if side == "long" else "buy",
                    "posSide": side,
                    "ordType": "market",
                    "sz": clean_num(qty)
                }
                data = await self.client._req("POST", "/api/v5/trade/order", body=body, auth=True)
                if data and len(data) > 0:
                    item = data[0]
                    if item.get("sCode") != "0":
                        await discord_notifier.log_error(
                            "OrderExecutionEngine.execute_tp_closure",
                            f"Rechazado por OKX: {item.get('sMsg')}"
                        )
                        return False
                    return True
            except Exception as e:
                err_msg = str(e)
                await discord_notifier.log_error("OrderExecutionEngine.execute_tp_closure", err_msg)
                if "OKX " in err_msg:
                    return False
            await asyncio.sleep(delay)
        return False

    async def _send_single_algo(self, body: dict, label: str):
        """Helper - envía UNA sola orden condicional y loguea el resultado."""
        try:
            data = await self.client._req("POST", "/api/v5/trade/order-algo", body, auth=True)
            if data:
                items = data if isinstance(data, list) else [data]
                for item in items:
                    s_code = item.get("sCode", "?")
                    s_msg  = item.get("sMsg", "")
                    if s_code != "0":
                        await discord_notifier.log_error(
                            f"order-algo.{label}",
                            f"sCode={s_code} | sMsg={s_msg}"
                        )
        except Exception as e:
            await discord_notifier.log_error(f"order-algo.{label}", str(e))

    async def place_native_tp_sl_orders(
        self, symbol: str, side: str, total_qty: float,
        tp1: float, tp2: float, sl: float,
        tick_sz: Decimal, lot_sz: Decimal,
        tp1_done: bool = False, tp2_done: bool = False
    ):
        """
        Coloca SL (100%), TP1 (30%) y TP2 (30%) como órdenes condicionales independientes en OKX.
        Las envía UNA POR UNA para evitar el rechazo 400 de OKX al mezclar tipos en un solo batch.
        """
        from decimal import ROUND_DOWN, ROUND_HALF_UP

        tot_qty_dec = Decimal(str(total_qty))
        qty_30 = (tot_qty_dec * Decimal("0.30")).quantize(lot_sz, rounding=ROUND_DOWN)
        if qty_30 < lot_sz:
            qty_30 = lot_sz

        def r_px(px: float) -> str:
            res = (Decimal(str(px)) / tick_sz).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_sz
            return f"{res.normalize():f}"

        pos_side    = side
        action_side = "sell" if pos_side == "long" else "buy"

        # 1. Stop Loss — toda la posición
        await self._send_single_algo({
            "instId": symbol, "tdMode": "isolated",
            "posSide": pos_side, "side": action_side,
            "ordType": "conditional",
            "sz": str(tot_qty_dec),
            "slTriggerPx": r_px(sl), "slOrdPx": "-1"
        }, "SL")

        await asyncio.sleep(0.5)

        # 2. TP1 — 30%
        if tp1 is not None and qty_30 > 0 and not tp1_done:
            await self._send_single_algo({
                "instId": symbol, "tdMode": "isolated",
                "posSide": pos_side, "side": action_side,
                "ordType": "conditional",
                "sz": str(qty_30),
                "tpTriggerPx": r_px(tp1), "tpOrdPx": "-1"
            }, "TP1")

        await asyncio.sleep(0.5)

        # 3. TP2 — 30%
        if tp2 is not None and qty_30 > 0 and not tp2_done:
            await self._send_single_algo({
                "instId": symbol, "tdMode": "isolated",
                "posSide": pos_side, "side": action_side,
                "ordType": "conditional",
                "sz": str(qty_30),
                "tpTriggerPx": r_px(tp2), "tpOrdPx": "-1"
            }, "TP2")

    async def modify_native_sl(self, symbol: str, pos_side: str, new_sl: float, tick_sz: Decimal = None):
        """
        Finds the current SL order and replaces it with the new SL (Breakeven/Trailing).
        """
        from decimal import Decimal, ROUND_HALF_UP

        if tick_sz is None:
            try:
                res = await self.client._req(
                    "GET", f"/api/v5/public/instruments?instType=SWAP&instId={symbol}"
                )
                if res and len(res) > 0:
                    tick_sz = Decimal(res[0]["tickSz"])
                else:
                    tick_sz = Decimal("0.0001")
            except:
                tick_sz = Decimal("0.0001")

        sl_px = str(
            (Decimal(str(new_sl)) / tick_sz).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_sz
        )

        try:
            pending = await self.client._req(
                "GET", f"/api/v5/trade/orders-algo-pending?instId={symbol}&ordType=conditional", auth=True
            )
            if pending:
                sl_algos = [
                    a for a in pending
                    if a.get("slTriggerPx") and a.get("posSide", "").lower() in (pos_side, "net", "")
                ]
                if sl_algos:
                    payloads = [
                        {"instId": symbol, "algoId": algo["algoId"],
                         "newSlTriggerPx": sl_px, "newSlOrdPx": "-1"}
                        for algo in sl_algos
                    ]
                    if payloads:
                        await self.client._req("POST", "/api/v5/trade/amend-algos", payloads, auth=True)
        except Exception as e:
            await discord_notifier.log_error("OrderExecutionEngine.modify_native_sl", str(e))

    async def restore_native_orders(
        self, symbol: str, side: str, trade, tick_sz: Decimal, lot_sz: Decimal
    ) -> bool:
        """
        Restaurador Inteligente: evalúa la fase de la operación y reenvía las órdenes
        condicionales necesarias. Usado por el Agente Guardián.
        """
        from decimal import ROUND_DOWN, ROUND_HALF_UP

        def r_px(px: float) -> str:
            res = (Decimal(str(px)) / tick_sz).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_sz
            return f"{res.normalize():f}"

        pos_side       = side
        action_side    = "sell" if pos_side == "long" else "buy"
        rem_size_dec   = Decimal(str(
            trade.remaining_size if (hasattr(trade, "remaining_size") and trade.remaining_size and trade.remaining_size > 0)
            else trade.qty
        ))
        qty_30         = (rem_size_dec * Decimal("0.30")).quantize(lot_sz, rounding=ROUND_DOWN)
        if qty_30 < lot_sz:
            qty_30 = lot_sz

        current_sl = getattr(trade, "sl_price", None)
        if not current_sl:
            return False

        # SL — siempre restaurado sobre la posición restante
        await self._send_single_algo({
            "instId": symbol, "tdMode": "isolated",
            "posSide": pos_side, "side": action_side,
            "ordType": "conditional",
            "sz": str(rem_size_dec),
            "slTriggerPx": r_px(current_sl), "slOrdPx": "-1"
        }, "RESTORE_SL")

        await asyncio.sleep(0.5)

        # TP1 si no se ha llenado
        if not getattr(trade, "tp1_filled", 0) and getattr(trade, "tp1_price", None):
            await self._send_single_algo({
                "instId": symbol, "tdMode": "isolated",
                "posSide": pos_side, "side": action_side,
                "ordType": "conditional",
                "sz": str(qty_30),
                "tpTriggerPx": r_px(trade.tp1_price), "tpOrdPx": "-1"
            }, "RESTORE_TP1")
            await asyncio.sleep(0.5)

        # TP2 si no se ha llenado
        if not getattr(trade, "tp2_filled", 0) and getattr(trade, "tp2_price", None):
            await self._send_single_algo({
                "instId": symbol, "tdMode": "isolated",
                "posSide": pos_side, "side": action_side,
                "ordType": "conditional",
                "sz": str(qty_30),
                "tpTriggerPx": r_px(trade.tp2_price), "tpOrdPx": "-1"
            }, "RESTORE_TP2")

        return True
