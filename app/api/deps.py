import uuid
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.session import get_session_user_id, validate_csrf, is_session_expired, touch_session
from app.crud import user as user_crud
from app.models.user import User, UserRole


class RequiresLoginException(Exception):
    """Raised when a protected route is accessed without a valid session."""
    def __init__(self, next_url: str = "/dashboard") -> None:
        self.next_url = next_url


class CSRFError(Exception):
    """Raised when CSRF validation fails."""


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Load the logged-in user from the session, or return None."""
    uid_str = get_session_user_id(request)
    if not uid_str:
        return None
    # Enforce session timeouts
    if is_session_expired(request):
        from app.core.session import clear_session
        clear_session(request)
        return None
    try:
        uid = uuid.UUID(uid_str)
    except ValueError:
        return None
    user = await user_crud.get_by_id(db, uid)
    # Silently reject disabled accounts
    if user and not user.is_active:
        return None
    if user:
        touch_session(request)
    return user


async def require_login(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency: redirect to login if no valid session."""
    user = await get_current_user(request, db)
    if not user:
        raise RequiresLoginException(next_url=str(request.url.path))
    return user


async def require_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency: require an active admin session."""
    user = await require_login(request, db)
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    return user


async def csrf_protect(request: Request) -> None:
    """Dependency: validate CSRF token on state-mutating requests."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    form = await request.form()
    submitted = form.get("csrf_token", "")
    if not validate_csrf(request, str(submitted)):
        raise CSRFError()

