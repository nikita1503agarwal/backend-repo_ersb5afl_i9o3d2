"""
Database Schemas for SplitPay

Each Pydantic model maps to a MongoDB collection (class name lowercased).
- User -> "user"
- Escrow -> "escrow"

These models are used for validation and for the database viewer.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, EmailStr, condecimal
from typing import List, Optional, Literal
from decimal import Decimal


class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: EmailStr = Field(..., description="Email address")
    wallet: Optional[str] = Field(None, description="On-chain wallet address")
    is_active: bool = Field(True, description="Whether the user is active")


class Recipient(BaseModel):
    email: EmailStr = Field(..., description="Recipient email")
    percentage: float = Field(..., ge=0, le=100, description="Payout percentage of total amount")
    wallet: Optional[str] = Field(None, description="Recipient on-chain wallet address (optional)")
    confirmed: bool = Field(False, description="Has the recipient confirmed?")


class Escrow(BaseModel):
    title: str = Field(..., description="Short name for this escrow")
    description: Optional[str] = Field(None, description="What is being paid for")
    payer_email: EmailStr = Field(..., description="Payer's email")
    total_amount: condecimal(gt=0) = Field(..., description="Total amount to be distributed")
    currency: Literal["USD", "USDC", "USDT", "ETH", "BTC"] = Field("USDC", description="Currency/asset symbol")
    chain: Literal["ethereum", "polygon", "solana", "bitcoin", "testnet"] = Field(
        "testnet", description="Target blockchain network"
    )
    recipients: List[Recipient] = Field(..., description="Who gets paid and how much")
    payer_confirmed: bool = Field(False, description="Has the payer confirmed?")
    status: Literal["pending", "funded", "releasable", "released", "cancelled"] = Field(
        "funded", description="Lifecycle status of the escrow"
    )


class TelegramProfile(BaseModel):
    chat_id: int = Field(..., description="Telegram chat ID")
    username: Optional[str] = Field(None, description="Telegram @username")
    email: Optional[EmailStr] = Field(None, description="Linked email for confirmations")
    wallet: Optional[str] = Field(None, description="Linked wallet (optional)")
