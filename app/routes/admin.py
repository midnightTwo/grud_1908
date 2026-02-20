import os
import shutil
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.models import User, OutlookAccount
from app.auth import hash_password, verify_password, create_access_token, verify_admin_password
from app.parser import parse_bulk_accounts
from app.config import get_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


# --- Schemas ---

class AdminLoginRequest(BaseModel):
    password: str


class AdminLoginResponse(BaseModel):
    token: str


class BulkUploadRequest(BaseModel):
    admin_token: str
    accounts_text: str


class BulkUploadResponse(BaseModel):
    imported: int
    duplicates: int
    errors: list[str]


class CreateUserRequest(BaseModel):
    admin_token: str
    login: str
    password: str
    display_name: str | None = None
    outlook_account_id: int


class UserResponse(BaseModel):
    id: int
    login: str
    display_name: str | None
    outlook_account_id: int | None
    is_active: bool

    class Config:
        from_attributes = True


class OutlookAccountResponse(BaseModel):
    id: int
    outlook_email: str
    client_id: str
    is_active: bool
    assigned_user: str | None = None

    class Config:
        from_attributes = True


class LinkAccountRequest(BaseModel):
    admin_token: str
    user_id: int
    outlook_account_id: int


class DeleteRequest(BaseModel):
    admin_token: str


# --- Helpers ---

def _verify_admin(token: str):
    """Verify the admin token is actually the admin password."""
    if not verify_admin_password(token):
        raise HTTPException(status_code=403, detail="Invalid admin credentials")


# --- Routes ---

@router.post("/login", response_model=AdminLoginResponse)
async def admin_login(req: AdminLoginRequest):
    if not verify_admin_password(req.password):
        raise HTTPException(status_code=401, detail="Wrong admin password")
    return AdminLoginResponse(token=req.password)


@router.post("/bulk-upload", response_model=BulkUploadResponse)
async def bulk_upload(req: BulkUploadRequest, db: AsyncSession = Depends(get_db)):
    _verify_admin(req.admin_token)

    accounts, parse_errors = parse_bulk_accounts(req.accounts_text)
    imported = 0
    duplicates = 0

    for acc in accounts:
        # Check if already exists
        result = await db.execute(
            select(OutlookAccount).where(OutlookAccount.outlook_email == acc.outlook_email)
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update refresh token and client_id
            existing.refresh_token = acc.refresh_token
            existing.client_id = acc.client_id
            duplicates += 1
        else:
            new_account = OutlookAccount(
                outlook_email=acc.outlook_email,
                refresh_token=acc.refresh_token,
                client_id=acc.client_id,
            )
            db.add(new_account)
            imported += 1

    await db.commit()
    return BulkUploadResponse(imported=imported, duplicates=duplicates, errors=parse_errors)


@router.get("/accounts")
async def list_accounts(admin_token: str, db: AsyncSession = Depends(get_db)):
    _verify_admin(admin_token)

    result = await db.execute(
        select(OutlookAccount).order_by(OutlookAccount.created_at.desc())
    )
    accounts = result.scalars().all()

    response = []
    for acc in accounts:
        # Check if assigned to a user
        user_result = await db.execute(
            select(User).where(User.outlook_account_id == acc.id)
        )
        user = user_result.scalar_one_or_none()

        response.append(OutlookAccountResponse(
            id=acc.id,
            outlook_email=acc.outlook_email,
            client_id=acc.client_id,
            is_active=acc.is_active,
            assigned_user=user.login if user else None,
        ))

    return response


@router.post("/users", response_model=UserResponse)
async def create_user(req: CreateUserRequest, db: AsyncSession = Depends(get_db)):
    _verify_admin(req.admin_token)

    # Check login uniqueness
    result = await db.execute(select(User).where(User.login == req.login))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Login already exists")

    # Check outlook account exists
    result = await db.execute(select(OutlookAccount).where(OutlookAccount.id == req.outlook_account_id))
    outlook_acc = result.scalar_one_or_none()
    if not outlook_acc:
        raise HTTPException(status_code=404, detail="Outlook account not found")

    # Check if outlook account is already assigned
    result = await db.execute(select(User).where(User.outlook_account_id == req.outlook_account_id))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Outlook account already assigned to another user")

    user = User(
        login=req.login,
        password_hash=hash_password(req.password),
        display_name=req.display_name,
        outlook_account_id=req.outlook_account_id,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/users")
async def list_users(admin_token: str, db: AsyncSession = Depends(get_db)):
    _verify_admin(admin_token)

    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()

    return [
        UserResponse(
            id=u.id,
            login=u.login,
            display_name=u.display_name,
            outlook_account_id=u.outlook_account_id,
            is_active=u.is_active,
        )
        for u in users
    ]


@router.post("/link-account")
async def link_account(req: LinkAccountRequest, db: AsyncSession = Depends(get_db)):
    _verify_admin(req.admin_token)

    # Get user
    result = await db.execute(select(User).where(User.id == req.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get outlook account
    result = await db.execute(select(OutlookAccount).where(OutlookAccount.id == req.outlook_account_id))
    outlook_acc = result.scalar_one_or_none()
    if not outlook_acc:
        raise HTTPException(status_code=404, detail="Outlook account not found")

    # Check if account is already assigned to someone else
    result = await db.execute(
        select(User).where(User.outlook_account_id == req.outlook_account_id, User.id != req.user_id)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Account already assigned to another user")

    user.outlook_account_id = req.outlook_account_id
    await db.commit()
    return {"status": "ok", "message": f"Linked {outlook_acc.outlook_email} to {user.login}"}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, admin_token: str, db: AsyncSession = Depends(get_db)):
    _verify_admin(admin_token)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(user)
    await db.commit()
    return {"status": "ok"}


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: int, admin_token: str, db: AsyncSession = Depends(get_db)):
    _verify_admin(admin_token)

    result = await db.execute(select(OutlookAccount).where(OutlookAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Unlink any user first
    result = await db.execute(select(User).where(User.outlook_account_id == account_id))
    user = result.scalar_one_or_none()
    if user:
        user.outlook_account_id = None

    await db.delete(account)
    await db.commit()
    return {"status": "ok"}


@router.get("/download-db")
async def download_database(admin_token: str):
    """Download the SQLite database file."""
    _verify_admin(admin_token)
    
    settings = get_settings()
    db_path = settings.DB_PATH
    
    if not os.path.isfile(db_path):
        raise HTTPException(status_code=404, detail="Database file not found")
    
    # Copy to a temp location to avoid locking issues
    tmp_path = db_path + ".download"
    shutil.copy2(db_path, tmp_path)
    
    return FileResponse(
        path=tmp_path,
        filename="securemail.db",
        media_type="application/x-sqlite3",
    )
