from sqlalchemy import inspect, Column, Integer, String, Float, DateTime, JSON, ForeignKey, UniqueConstraint
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, timezone

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

SQLALCHEMY_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Adding connection pool settings to handle long-running connections
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,                   # Verify connection is still valid before using
    pool_recycle=1800,                    # Recycle connections after 30 minutes
    pool_size=10,                         # Maximum number of connections to keep
    max_overflow=20,
    pool_timeout=30                       # Timeout for getting a connection from the pool
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
