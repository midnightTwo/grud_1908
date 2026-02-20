from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base


class OutlookAccount(Base):
    __tablename__ = "outlook_accounts"

    id = Column(Integer, primary_key=True, index=True)
    outlook_email = Column(String(255), unique=True, nullable=False, index=True)
    refresh_token = Column(Text, nullable=False)
    client_id = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationship to user
    user = relationship("User", back_populates="outlook_account", uselist=False)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    login = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)
    outlook_account_id = Column(Integer, ForeignKey("outlook_accounts.id"), nullable=True, unique=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationship
    outlook_account = relationship("OutlookAccount", back_populates="user")
