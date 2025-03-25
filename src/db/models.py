from typing import Optional

from sqlalchemy import BigInteger, DateTime, Double, ForeignKeyConstraint, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
import datetime
import uuid


class Base(DeclarativeBase):
    pass


class Arbitrage(Base):
    __tablename__ = 'arbitrage'
    __table_args__ = (
        PrimaryKeyConstraint('primary_key', name='arbitrage_pkey'),
        {'comment': 'Arbitrage opportunities'}
    )

    primary_key: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text('uuid_generate_v4()'))
    opportunity_type: Mapped[Optional[str]] = mapped_column(Text)
    hold_percentage: Mapped[Optional[float]] = mapped_column(Double(53))
    sport: Mapped[Optional[str]] = mapped_column(Text)
    market_type: Mapped[Optional[str]] = mapped_column(Text)
    prop_description: Mapped[Optional[str]] = mapped_column(Text)
    market_point: Mapped[Optional[str]] = mapped_column(Text)
    game: Mapped[Optional[str]] = mapped_column(Text)
    commence_time: Mapped[Optional[datetime.datetime]
                          ] = mapped_column(DateTime(True), comment='UTC Time')
    team1_name: Mapped[Optional[str]] = mapped_column(Text)
    team1_book: Mapped[Optional[str]] = mapped_column(Text)
    team1_odds: Mapped[Optional[int]] = mapped_column(BigInteger)
    team1_point: Mapped[Optional[str]] = mapped_column(Text)
    team1_stake: Mapped[Optional[float]] = mapped_column(Double(53))
    team1_link: Mapped[Optional[str]] = mapped_column(Text)
    team2_name: Mapped[Optional[str]] = mapped_column(Text)
    team2_book: Mapped[Optional[str]] = mapped_column(Text)
    team2_odds: Mapped[Optional[int]] = mapped_column(BigInteger)
    team2_point: Mapped[Optional[str]] = mapped_column(Text)
    team2_stake: Mapped[Optional[float]] = mapped_column(Double(53))
    team2_link: Mapped[Optional[str]] = mapped_column(Text)
    profit_percentage: Mapped[Optional[str]] = mapped_column(Text)
    timestamp: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), comment='UTC time')
    market_data: Mapped[Optional[str]] = mapped_column(Text)
    player: Mapped[Optional[str]] = mapped_column(Text)


class EvGraphV2(Base):
    __tablename__ = 'ev_graph_v2'
    __table_args__ = (
        PrimaryKeyConstraint('bet_key', name='ev_graph_duplicate_pkey'),
        {'comment': 'This is a duplicate of ev_graph'}
    )

    bet_key: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[Optional[str]] = mapped_column(Text)
    sport_key: Mapped[Optional[str]] = mapped_column(Text)
    sport_title: Mapped[Optional[str]] = mapped_column(Text)
    commence_time: Mapped[Optional[datetime.datetime]
                          ] = mapped_column(DateTime(True))
    home_team: Mapped[Optional[str]] = mapped_column(Text)
    away_team: Mapped[Optional[str]] = mapped_column(Text)
    sport: Mapped[Optional[str]] = mapped_column(Text)
    timestamp: Mapped[Optional[datetime.datetime]
                      ] = mapped_column(DateTime(True))
    game: Mapped[Optional[str]] = mapped_column(Text)
    team: Mapped[Optional[str]] = mapped_column(Text)
    market_type: Mapped[Optional[str]] = mapped_column(Text)
    market_point: Mapped[Optional[str]] = mapped_column(Text)
    bookmaker: Mapped[Optional[str]] = mapped_column(Text)
    odds: Mapped[Optional[int]] = mapped_column(BigInteger)
    link: Mapped[Optional[str]] = mapped_column(Text)
    market_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    past_data: Mapped[Optional[dict]] = mapped_column(JSONB, default=[])


class FreeBet(Base):
    __tablename__ = 'free_bet'
    __table_args__ = (
        PrimaryKeyConstraint('clerk_id', name='free_bet_pkey'),
        UniqueConstraint('lastRedemptionDate',
                         name='free_bet_lastRedemptionDate_key')
    )

    lastRedemptionDate: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), server_default=text('now()'))
    clerk_id: Mapped[str] = mapped_column(Text, primary_key=True)


class PlusEv(Base):
    __tablename__ = 'plus_ev'
    __table_args__ = (
        PrimaryKeyConstraint('primary_key', name='plus_ev_pkey'),
    )

    primary_key: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text('uuid_generate_v4()'))
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), server_default=text('now()'))
    sport: Mapped[Optional[str]] = mapped_column(Text)
    market_type: Mapped[Optional[str]] = mapped_column(Text)
    market_point: Mapped[Optional[float]] = mapped_column(
        Double(53), server_default=text("'0'::double precision"))
    game: Mapped[Optional[str]] = mapped_column(Text)
    commence_time: Mapped[Optional[datetime.datetime]
                          ] = mapped_column(DateTime(True))
    team: Mapped[Optional[str]] = mapped_column(Text)
    bookmaker: Mapped[Optional[str]] = mapped_column(Text)
    odds: Mapped[Optional[int]] = mapped_column(
        BigInteger, server_default=text("'0'::bigint"))
    fair_odds: Mapped[Optional[int]] = mapped_column(
        BigInteger, server_default=text("'0'::bigint"))
    ev_percentage: Mapped[Optional[float]] = mapped_column(
        Double(53), server_default=text("'0'::double precision"))
    link: Mapped[Optional[str]] = mapped_column(Text)
    market_data: Mapped[Optional[str]] = mapped_column(Text)
    player: Mapped[Optional[str]] = mapped_column(Text)


class Subscriptions(Base):
    __tablename__ = 'subscriptions'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='subscriptions_pkey'),
        UniqueConstraint('subscription_id',
                         name='subscriptions_subscription_id_key')
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text('uuid_generate_v4()'))
    subscription_id: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[str] = mapped_column(Text)
    clerk_user_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    current_period_start: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True))
    current_period_end: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text('now()'))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text('now()'))


class EvGraphPastData(EvGraphV2):
    __tablename__ = 'ev_graph_past_data'
    __table_args__ = (
        ForeignKeyConstraint(['bet_key'], ['ev_graph_v2.bet_key'], ondelete='CASCADE',
                             onupdate='CASCADE', name='ev_graph_past_data_id_fkey'),
        PrimaryKeyConstraint('bet_key', name='ev_graph_past_data_pkey')
    )

    bet_key: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), server_default=text('now()'))
    commence_time: Mapped[Optional[datetime.datetime]
                          ] = mapped_column(DateTime(True))
    past_data: Mapped[Optional[dict]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"))
