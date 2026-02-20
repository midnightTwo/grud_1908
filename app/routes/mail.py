from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.models import User, OutlookAccount
from app.auth import get_current_user
from app.mail_service import fetch_emails, fetch_single_email, invalidate_cache

router = APIRouter(prefix="/api/mail", tags=["mail"])


class MailListItem(BaseModel):
    uid: str
    sender: str
    sender_email: str
    subject: str
    date: str
    date_iso: str
    is_read: bool
    has_attachments: bool
    preview: str  # first ~100 chars of body_text
    folder: str = "INBOX"


class MailDetail(BaseModel):
    uid: str
    sender: str
    sender_email: str
    subject: str
    date: str
    date_iso: str
    body_html: str
    body_text: str
    is_read: bool
    has_attachments: bool
    attachments: list[str]
    folder: str = "INBOX"


async def _get_outlook_account(user: User, db: AsyncSession) -> OutlookAccount:
    """Get the linked outlook account for the current user."""
    if not user.outlook_account_id:
        raise HTTPException(status_code=403, detail="No mailbox configured")

    result = await db.execute(
        select(OutlookAccount).where(OutlookAccount.id == user.outlook_account_id)
    )
    account = result.scalar_one_or_none()
    if not account or not account.is_active:
        raise HTTPException(status_code=403, detail="Mailbox unavailable")
    return account


@router.get("/inbox", response_model=list[MailListItem])
async def get_inbox(
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    account = await _get_outlook_account(user, db)

    try:
        messages = await fetch_emails(
            outlook_email=account.outlook_email,
            refresh_token=account.refresh_token,
            client_id=account.client_id,
            folder="ALL",
            limit=min(limit, 100),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch emails: {str(e)}")

    return [
        MailListItem(
            uid=msg.uid,
            sender=msg.sender,
            sender_email=msg.sender_email,
            subject=msg.subject,
            date=msg.date,
            date_iso=msg.date_iso,
            is_read=msg.is_read,
            has_attachments=msg.has_attachments,
            preview=msg.body_text[:120].replace("\n", " ") if msg.body_text else "",
            folder=msg.folder,
        )
        for msg in messages
    ]


@router.get("/message/{uid}", response_model=MailDetail)
async def get_message(
    uid: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    account = await _get_outlook_account(user, db)

    try:
        msg = await fetch_single_email(
            outlook_email=account.outlook_email,
            refresh_token=account.refresh_token,
            client_id=account.client_id,
            uid=uid,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch email: {str(e)}")

    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    return MailDetail(
        uid=msg.uid,
        sender=msg.sender,
        sender_email=msg.sender_email,
        subject=msg.subject,
        date=msg.date,
        date_iso=msg.date_iso,
        body_html=msg.body_html,
        body_text=msg.body_text,
        is_read=msg.is_read,
        has_attachments=msg.has_attachments,
        attachments=msg.attachments,
        folder=msg.folder,
    )


@router.post("/refresh")
async def refresh_inbox(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    account = await _get_outlook_account(user, db)
    invalidate_cache(account.outlook_email)
    return {"status": "ok", "message": "Cache cleared, next request will fetch fresh data"}
