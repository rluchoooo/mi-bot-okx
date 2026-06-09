"""
models.py – Persistencia SQLite con SQLAlchemy para el Quantum V10 Pro Bot.
Almacena el estado de las operaciones, cooldowns y log de eventos.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Column, DateTime, Enum, Float, Integer, String, Text, create_engine, event
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DB_URL = "sqlite:///quantum_bot.db"
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})

# Enable WAL mode for concurrent reads
@event.listens_for(engine, "connect")
def set_wal_mode(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class TradeStatus(str, enum.Enum):
    OPEN       = "OPEN"
    BREAKEVEN  = "BREAKEVEN"
    TRAILING   = "TRAILING"
    EARLY_EXIT = "EARLY_EXIT"
    CLOSED     = "CLOSED"


class TradeSide(str, enum.Enum):
    LONG  = "long"
    SHORT = "short"


class Strategy(str, enum.Enum):
    TREND      = "QUANTUM_SMC_V10_PRO"
    DIVERGENCE = "SUPERTREND_PULLBACK_V3"


class Trade(Base):
    __tablename__ = "trades"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    symbol      = Column(String(32), nullable=False, index=True)
    side        = Column(Enum(TradeSide), nullable=False)
    strategy    = Column(Enum(Strategy), nullable=False)
    status      = Column(Enum(TradeStatus), nullable=False, default=TradeStatus.OPEN)

    entry_price = Column(Float, nullable=False)
    qty         = Column(Float, nullable=False)          # contracts
    sl_price    = Column(Float, nullable=False)
    tp_price    = Column(Float, nullable=True)           # None when trailing active
    atr_5m      = Column(Float, nullable=False)

    # Lifecycle milestones
    be_activated   = Column(Integer, default=0)          # 0/1 bool
    trail_activated = Column(Integer, default=0)
    trail_sl       = Column(Float, nullable=True)        # current trailing stop
    peak_price     = Column(Float, nullable=True)        # best price since open

    # PnL
    realized_pnl = Column(Float, nullable=True)
    close_price  = Column(Float, nullable=True)
    close_reason = Column(String(64), nullable=True)

    # Metadata
    opened_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    closed_at    = Column(DateTime, nullable=True)
    leverage     = Column(Integer, default=10)
    risk_usd     = Column(Float, default=8.0)

    def __repr__(self) -> str:
        return (
            f"<Trade #{self.id} {self.symbol} {self.side} "
            f"entry={self.entry_price:.6f} status={self.status}>"
        )

    @property
    def tp_distance(self) -> float:
        """Total distance from entry to original TP."""
        if self.side == TradeSide.LONG:
            return self.tp_price_original - self.entry_price
        return self.entry_price - self.tp_price_original

    @property
    def is_open(self) -> bool:
        return self.status not in (TradeStatus.CLOSED, TradeStatus.EARLY_EXIT)


class Cooldown(Base):
    __tablename__ = "cooldowns"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    symbol    = Column(String(32), nullable=False, unique=True, index=True)
    until     = Column(DateTime, nullable=False)

    @property
    def is_active(self) -> bool:
        now = datetime.utcnow()
        until = self.until.replace(tzinfo=None) if self.until else now
        return now < until


class TradeEvent(Base):
    __tablename__ = "trade_events"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    trade_id   = Column(Integer, nullable=False, index=True)
    event_type = Column(String(32), nullable=False)  # OPEN, BREAKEVEN, TRAILING, CLOSE, ERROR
    message    = Column(Text, nullable=False)
    price      = Column(Float, nullable=True)
    ts         = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# Runtime log (in-memory + db for last 300 entries)
class SystemLog(Base):
    __tablename__ = "system_log"

    id      = Column(Integer, primary_key=True, autoincrement=True)
    level   = Column(String(16), default="INFO")   # INFO, WARN, ERROR, SYSTEM
    message = Column(Text, nullable=False)
    ts      = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def create_all():
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
