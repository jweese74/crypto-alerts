import uuid
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User, UserRole


async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_by_email(db: AsyncSession, email: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email.lower().strip()))
    return result.scalar_one_or_none()


async def get_by_username(db: AsyncSession, username: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.username == username.strip()))
    return result.scalar_one_or_none()


async def get_by_email_or_username(db: AsyncSession, login: str) -> Optional[User]:
    login = login.strip()
    result = await db.execute(
        select(User).where(or_(User.email == login.lower(), User.username == login))
    )
    return result.scalar_one_or_none()


async def get_all(db: AsyncSession) -> list[User]:
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return list(result.scalars().all())


async def count(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(User.id)))
    return result.scalar_one()


async def create(
    db: AsyncSession,
    *,
    email: str,
    username: str,
    password: str,
    role: UserRole = UserRole.USER,
    is_active: bool = True,
) -> User:
    user = User(
        email=email.lower().strip(),
        username=username.strip(),
        hashed_password=hash_password(password),
        role=role,
        is_active=is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def set_active(db: AsyncSession, user: User, active: bool) -> User:
    user.is_active = active
    await db.commit()
    await db.refresh(user)
    return user


async def set_role(db: AsyncSession, user: User, role: UserRole) -> User:
    user.role = role
    await db.commit()
    await db.refresh(user)
    return user


async def delete(db: AsyncSession, user: User) -> None:
    await db.delete(user)
    await db.commit()


async def authenticate(db: AsyncSession, login: str, password: str) -> Optional[User]:
    """Return user if credentials are valid, None otherwise."""
    user = await get_by_email_or_username(db, login)
    if not user:
        # Constant-time dummy check to prevent user enumeration via timing
        hash_password("dummy-timing-prevention")
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user
