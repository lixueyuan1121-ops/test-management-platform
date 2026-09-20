from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.common import ok
from app.schemas.geelib_account import GeelibAuthorizeIn
from app.services import geelib_account as accounts

router = APIRouter(prefix="/api/auth/geelib", tags=["auth"])


@router.get("")
def get_account(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return ok(accounts.account_status(db, user.id))


@router.post("/authorize")
def authorize(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return ok(accounts.start_authorization(db, user.id))
    except accounts.GeelibAccountError as exc:
        raise HTTPException(409, detail=str(exc)) from None


@router.post("/complete")
def complete(body: GeelibAuthorizeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return ok(accounts.finish_authorization(db, user.id, body.state, body.code.get_secret_value()))
    except accounts.GeelibAccountError as exc:
        raise HTTPException(409, detail=str(exc)) from None


@router.delete("")
def disconnect(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    accounts.disconnect(db, user.id)
    return ok()
