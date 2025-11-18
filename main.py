import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import Escrow, Recipient

app = FastAPI(title="SplitPay API", version="0.1.0")

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
def list_escrows():
    escrows = get_documents("escrow", {}, limit=50)
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

    doc = db["escrow"].find_one({"_id": ObjectId(escrow_id)})
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

    db["escrow"].update_one({"_id": ObjectId(escrow_id)}, {"$set": updates})

    # Determine if releasable (both sides confirmed)
    doc = db["escrow"].find_one({"_id": ObjectId(escrow_id)})
    all_rec_confirmed = all(r.get("confirmed") for r in doc.get("recipients", []))
    new_status = "releasable" if (doc.get("payer_confirmed") and all_rec_confirmed) else doc.get("status")
    if new_status != doc.get("status"):
        db["escrow"].update_one({"_id": ObjectId(escrow_id)}, {"$set": {"status": new_status}})

    return {"message": "Confirmation recorded", "status": new_status}


@app.post("/api/escrows/{escrow_id}/release")
def release_escrow(escrow_id: str):
    # This is a stub for on-chain release. In this demo we simply mark as released when releasable.
    doc = db["escrow"].find_one({"_id": ObjectId(escrow_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Escrow not found")
    if doc.get("status") != "releasable":
        raise HTTPException(status_code=400, detail="Escrow is not yet releasable")

    db["escrow"].update_one({"_id": ObjectId(escrow_id)}, {"$set": {"status": "released"}})
    return {"message": "Funds released (simulated)", "status": "released"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
