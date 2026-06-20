"""
reset_and_close.py – Cierra todas las posiciones abiertas en OKX y limpia la base de datos.
Ejecutar UNA SOLA VEZ antes de desplegar en producción.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY    = os.getenv("OKX_API_KEY", "")
API_SECRET = os.getenv("OKX_API_SECRET", "")
PASSPHRASE = os.getenv("OKX_API_PASSPHRASE", "")
SIMULATED  = os.getenv("OKX_SIMULATED", "1") == "1"
BASE_URL   = "https://www.okx.com"


def _sign(method, path, body=""):
    ts  = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    pre = f"{ts}{method.upper()}{path}{body}"
    sig = base64.b64encode(
        hmac.new(API_SECRET.encode(), pre.encode(), hashlib.sha256).digest()
    ).decode()
    h = {
        "Content-Type":       "application/json",
        "OK-ACCESS-KEY":       API_KEY,
        "OK-ACCESS-SIGN":      sig,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
    }
    if SIMULATED:
        h["x-simulated-trading"] = "1"
    return h


async def req(client, method, path, body=None):
    payload = json.dumps(body, separators=(",", ":")) if body else ""
    headers = _sign(method, path, payload)
    r = await client.request(method, path, content=payload or None, headers=headers)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        raise RuntimeError(f"OKX {data.get('code')}: {data.get('msg')} | {data.get('data')}")
    return data.get("data", [])


async def main():
    print("=" * 55)
    print("  QUANTUM V10 PRO – RESET & CLOSE ALL POSITIONS")
    print("=" * 55)
    mode = "DEMO (Simulated)" if SIMULATED else "REAL (LIVE)"
    print(f"  Modo: {mode}")
    print()

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20) as client:
        # 1. Obtener posiciones abiertas
        print(">>> Consultando posiciones abiertas en OKX...")
        positions = await req(client, "GET", "/api/v5/account/positions?instType=SWAP")
        open_pos = [p for p in positions if abs(float(p.get("pos", 0))) > 0]
        print(f"    Encontradas: {len(open_pos)} posiciones abiertas.")

        # 2. Cancelar todas las órdenes pendientes primero
        print(">>> Cancelando órdenes pendientes...")
        try:
            pending = await req(client, "GET", "/api/v5/trade/orders-pending?instType=SWAP")
            if pending:
                cancel_body = [{"instId": o["instId"], "ordId": o["ordId"]} for o in pending]
                await req(client, "POST", "/api/v5/trade/cancel-batch-orders", cancel_body)
                print(f"    Canceladas: {len(pending)} órdenes pendientes.")
            else:
                print("    Sin órdenes pendientes.")
        except Exception as e:
            print(f"    Aviso en cancelación: {e}")

        # 3. Cerrar cada posición a mercado
        closed = 0
        for pos in open_pos:
            inst_id  = pos["instId"]
            pos_side = pos.get("posSide", "net")
            qty      = abs(float(pos.get("pos", 0)))
            if qty == 0:
                continue
            try:
                await req(client, "POST", "/api/v5/trade/close-position", {
                    "instId":  inst_id,
                    "posSide": pos_side,
                    "mgnMode": "isolated",
                })
                print(f"    [CERRADO] {inst_id} ({pos_side}) qty={qty}")
                closed += 1
            except Exception as e:
                print(f"    [ERROR] {inst_id}: {e}")

        print(f"\n>>> {closed}/{len(open_pos)} posiciones cerradas exitosamente.")

    # 4. Limpiar / resetear la base de datos SQLite
    print("\n>>> Reseteando base de datos SQLite...")
    try:
        import models
        models.create_all()
        with models.get_session() as db:
            db.query(models.TradeEvent).delete()
            db.query(models.Trade).delete()
            db.query(models.Cooldown).delete()
            db.query(models.SystemLog).delete()
            db.commit()
        print("    Base de datos limpiada: trades, cooldowns, events, logs.")
    except Exception as e:
        print(f"    Error limpiando DB: {e}")

    print()
    print("=" * 55)
    print("  RESET COMPLETADO - Bot listo para arrancar limpio")
    print("=" * 55)


if __name__ == "__main__":
    asyncio.run(main())
