from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.models import User
from app.auth import verify_password, create_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    login: str
    password: str


class LoginResponse(BaseModel):
    token: str
    display_name: str | None
    login: str


class MeResponse(BaseModel):
    id: int
    login: str
    display_name: str | None


@router.post("/login", response_model=LoginResponse)
async def user_login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.login == req.login))
    user = result.scalar_one_or_none()

    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid login or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    if not user.outlook_account_id:
        raise HTTPException(status_code=403, detail="No mailbox configured for this account")

    token = create_access_token(data={"sub": str(user.id)})

    return LoginResponse(
        token=token,
        display_name=user.display_name,
        login=user.login,
    )
