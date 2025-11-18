import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from bson import ObjectId
import requests

from database import db, create_document, get_documents
from schemas import Escrow, Recipient, TelegramProfile

app = FastAPI(title="SplitPay API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "SplitPay backend is running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = (
                os.getenv("DATABASE_NAME") if os.getenv("DATABASE_NAME") else "❌ Not Set"
            )
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
                response["connection_status"] = "Connected"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    return response


class CreateEscrowRequest(BaseModel):
    title: str
    description: Optional[str] = None
    payer_email: EmailStr
    total_amount: float
    currency: str = "USDC"
    chain: str = "testnet"
    recipients: List[Recipient]


@app.post("/api/escrows")
def create_escrow(payload: CreateEscrowRequest):
    # Validate recipient percentages add to 100
    total_pct = sum(r.percentage for r in payload.recipients)
    if abs(total_pct - 100) > 1e-6:
        raise HTTPException(status_code=400, detail="Recipient percentages must add up to 100")

    # Build escrow document using our schema
    escrow_doc = Escrow(
        title=payload.title,
        description=payload.description,
        payer_email=payload.payer_email,
        total_amount=payload.total_amount,
        currency=payload.currency,
        chain=payload.chain,
        recipients=payload.recipients,
        payer_confirmed=False,
        status="funded",
    )

    inserted_id = create_document("escrow", escrow_doc)
    return {"id": inserted_id, "message": "Escrow created"}


@app.get("/api/escrows")
def list_escrows(email: Optional[str] = None):
    """List escrows, optionally filtered by an actor's email (payer or recipient)."""
    filter_query = {}
    if email:
        filter_query = {"$or": [{"payer_email": email}, {"recipients.email": email}]}
    escrows = get_documents("escrow", filter_query, limit=50)
    # Serialize ObjectId
    for e in escrows:
        if isinstance(e.get("_id"), ObjectId):
            e["id"] = str(e["_id"])  # expose as id
            del e["_id"]
    return {"items": escrows}


class ConfirmRequest(BaseModel):
    actor: EmailStr


@app.post("/api/escrows/{escrow_id}/confirm")
def confirm_escrow(escrow_id: str, payload: ConfirmRequest):
    # Minimal logical confirmation: set flags when payer and all recipients confirmed
    from datetime import datetime, timezone

    try:
        oid = ObjectId(escrow_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid escrow id")

    doc = db["escrow"].find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Escrow not found")

    # Determine if actor is payer or a recipient
    updates = {"updated_at": datetime.now(timezone.utc)}
    if payload.actor == doc.get("payer_email"):
        updates["payer_confirmed"] = True
    else:
        # mark recipient confirmed if email matches
        recs = doc.get("recipients", [])
        changed = False
        for r in recs:
            if r.get("email") == payload.actor:
                r["confirmed"] = True
                changed = True
        if changed:
            updates["recipients"] = recs
        else:
            raise HTTPException(status_code=400, detail="Actor not part of this escrow")

    db["escrow"].update_one({"_id": oid}, {"$set": updates})

    # Determine if releasable (both sides confirmed)
    doc = db["escrow"].find_one({"_id": oid})
    all_rec_confirmed = all(r.get("confirmed") for r in doc.get("recipients", []))
    new_status = "releasable" if (doc.get("payer_confirmed") and all_rec_confirmed) else doc.get("status")
    if new_status != doc.get("status"):
        db["escrow"].update_one({"_id": oid}, {"$set": {"status": new_status}})

    return {"message": "Confirmation recorded", "status": new_status}


@app.post("/api/escrows/{escrow_id}/release")
def release_escrow(escrow_id: str):
    # This is a stub for on-chain release. In this demo we simply mark as released when releasable.
    try:
        oid = ObjectId(escrow_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid escrow id")

    doc = db["escrow"].find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Escrow not found")
    if doc.get("status") != "releasable":
        raise HTTPException(status_code=400, detail="Escrow is not yet releasable")

    db["escrow"].update_one({"_id": oid}, {"$set": {"status": "released"}})
    return {"message": "Funds released (simulated)", "status": "released"}


# ----- P2P convenience endpoint -----
class P2PCreateRequest(BaseModel):
    payer_email: EmailStr
    recipient_email: EmailStr
    amount: float
    currency: str = "USDC"
    chain: str = "testnet"
    title: Optional[str] = "P2P Payment"
    description: Optional[str] = None


@app.post("/api/p2p")
def create_p2p_escrow(payload: P2PCreateRequest):
    recipients = [Recipient(email=payload.recipient_email, percentage=100.0)]
    escrow_doc = Escrow(
        title=payload.title or "P2P Payment",
        description=payload.description,
        payer_email=payload.payer_email,
        total_amount=payload.amount,
        currency=payload.currency,
        chain=payload.chain,
        recipients=recipients,
        payer_confirmed=False,
        status="funded",
    )
    inserted_id = create_document("escrow", escrow_doc)
    return {"id": inserted_id, "message": "P2P escrow created"}


# ----- Telegram Bot Webhook -----
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else None


def send_telegram_message(chat_id: int, text: str):
    if not TELEGRAM_API:
        return
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text})
    except Exception:
        pass


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if not TELEGRAM_API:
        # Allow graceful testing without token
        return {"ok": True, "message": "Telegram token not configured"}

    update = await request.json()
    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "").strip()
    from_user = message.get("from", {})
    username = from_user.get("username")

    # Ensure a profile exists
    profile = db["telegramprofile"].find_one({"chat_id": chat_id})
    if not profile:
        profile_doc = TelegramProfile(chat_id=chat_id, username=username)
        _ = create_document("telegramprofile", profile_doc)
        profile = db["telegramprofile"].find_one({"chat_id": chat_id})

    def reply(msg: str):
        send_telegram_message(chat_id, msg)

    # Parse commands: /start, /link <email>, /pay <email> <amount> [USDC], /confirm <escrow_id>, /release <escrow_id>, /my
    if text.startswith("/start"):
        reply(
            "Welcome to SplitPay P2P!\n\nLink your email with /link your@email.com\nCreate a payment: /pay recipient@email.com 25 USDC\nConfirm: /confirm <escrow_id>\nRelease: /release <escrow_id>\nView: /my"
        )
        return {"ok": True}

    if text.startswith("/link"):
        parts = text.split()
        if len(parts) >= 2:
            email = parts[1]
            db["telegramprofile"].update_one({"chat_id": chat_id}, {"$set": {"email": email, "username": username}})
            reply(f"Linked email: {email}")
        else:
            reply("Usage: /link your@email.com")
        return {"ok": True}

    if text.startswith("/pay"):
        parts = text.split()
        if len(parts) >= 3:
            recipient_email = parts[1]
            try:
                amount = float(parts[2])
            except Exception:
                reply("Amount must be a number, e.g., 25")
                return {"ok": True}
            currency = parts[3] if len(parts) >= 4 else "USDC"
            payer_email = (profile or {}).get("email")
            if not payer_email:
                reply("Please link your email first: /link your@email.com")
                return {"ok": True}
            # Create P2P escrow
            recipients = [Recipient(email=recipient_email, percentage=100.0)]
            escrow_doc = Escrow(
                title="P2P Payment",
                description=f"P2P via Telegram from {payer_email} to {recipient_email}",
                payer_email=payer_email,
                total_amount=amount,
                currency=currency,
                chain="testnet",
                recipients=recipients,
                payer_confirmed=False,
                status="funded",
            )
            escrow_id = create_document("escrow", escrow_doc)
            reply(
                f"✅ Created escrow {escrow_id}\nPayer confirm: /confirm {escrow_id}\nRecipient confirm: recipients can also /confirm {escrow_id} after linking their email with /link"
            )
        else:
            reply("Usage: /pay recipient@email 25 [USDC]")
        return {"ok": True}

    if text.startswith("/confirm"):
        parts = text.split()
        if len(parts) >= 2:
            escrow_id = parts[1]
            actor_email = (profile or {}).get("email")
            if not actor_email:
                reply("Please link your email first: /link your@email.com")
                return {"ok": True}
            # call API internally
            try:
                res = confirm_escrow(escrow_id, ConfirmRequest(actor=actor_email))
                reply(f"✅ Confirmed. Status: {res['status']}")
            except HTTPException as e:
                reply(f"❌ {e.detail}")
        else:
            reply("Usage: /confirm <escrow_id>")
        return {"ok": True}

    if text.startswith("/release"):
        parts = text.split()
        if len(parts) >= 2:
            escrow_id = parts[1]
            try:
                res = release_escrow(escrow_id)
                reply(f"✅ Released. Status: {res['status']}")
            except HTTPException as e:
                reply(f"❌ {e.detail}")
        else:
            reply("Usage: /release <escrow_id>")
        return {"ok": True}

    if text.startswith("/my"):
        email = (profile or {}).get("email")
        if not email:
            reply("Link your email first: /link your@email.com")
            return {"ok": True}
        items = list_escrows(email=email)["items"]
        if not items:
            reply("No escrows yet.")
        else:
            lines = [
                f"• {i['id']}: {i['status']} {i['currency']} {i['total_amount']} to {[r['email'] for r in i['recipients']]}"
                for i in items[:10]
            ]
            reply("Your escrows:\n" + "\n".join(lines))
        return {"ok": True}

    # default fallback
    reply("Unknown command. Try /start")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
