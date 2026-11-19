from __future__ import annotations

import datetime
import inspect
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.auth.constants import UserRole


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@dataclass(slots=True)
class User:
    """User domain record matching Mongoose User schema (`users` collection)."""

    email: str
    passwordHash: str
    role: UserRole | str = UserRole.USER
    isActive: bool = True
    firstName: str | None = None
    lastName: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    createdAt: datetime.datetime = field(default_factory=_now_utc)
    updatedAt: datetime.datetime = field(default_factory=_now_utc)
    _repo: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if isinstance(self.email, str):
            self.email = self.email.strip().lower()
        if isinstance(self.role, str) and not isinstance(self.role, UserRole):
            self.role = (
                UserRole.ADMIN
                if self.role.lower() == UserRole.ADMIN.value
                else UserRole.USER
            )

    @property
    def _id(self) -> str:
        return self.id

    @property
    def password_hash(self) -> str:
        return self.passwordHash

    @password_hash.setter
    def password_hash(self, value: str) -> None:
        self.passwordHash = value

    @property
    def is_active(self) -> bool:
        return self.isActive

    @is_active.setter
    def is_active(self, value: bool) -> None:
        self.isActive = value

    @property
    def first_name(self) -> str | None:
        return self.firstName

    @property
    def last_name(self) -> str | None:
        return self.lastName

    @property
    def created_at(self) -> datetime.datetime:
        return self.createdAt

    @property
    def updated_at(self) -> datetime.datetime:
        return self.updatedAt

    async def save(self) -> User:
        self.updatedAt = _now_utc()
        if self._repo is not None:
            save_fn = getattr(self._repo, "save_user", None) or getattr(
                self._repo, "save", None
            )
            if callable(save_fn):
                res = save_fn(self)
                if inspect.isawaitable(res):
                    await res
        return self

    def to_object(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "_id": self.id,
            "email": self.email,
            "passwordHash": self.passwordHash,
            "isActive": self.isActive,
            "role": str(
                self.role.value if isinstance(self.role, UserRole) else self.role
            ),
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
        }
        if self.firstName is not None:
            data["firstName"] = self.firstName
        if self.lastName is not None:
            data["lastName"] = self.lastName
        return data

    def toObject(self) -> dict[str, Any]:
        return self.to_object()

    def to_mongo_doc(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "_id": self.id,
            "email": self.email,
            "passwordHash": self.passwordHash,
            "isActive": self.isActive,
            "role": str(
                self.role.value if isinstance(self.role, UserRole) else self.role
            ),
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
        }
        if self.firstName is not None:
            doc["firstName"] = self.firstName
        if self.lastName is not None:
            doc["lastName"] = self.lastName
        return doc

    @classmethod
    def from_mongo_doc(
        cls,
        doc: Mapping[str, Any],
        *,
        repo: Any = None,
    ) -> User:
        raw_id = doc.get("_id") if "_id" in doc else doc.get("id")
        resolved_id = str(raw_id) if raw_id is not None else str(uuid.uuid4())
        raw_role = doc.get("role", UserRole.USER)
        role = (
            UserRole.ADMIN
            if str(raw_role).lower() == UserRole.ADMIN.value
            else UserRole.USER
        )
        created_at = doc.get("createdAt")
        updated_at = doc.get("updatedAt")
        return cls(
            id=resolved_id,
            email=str(doc.get("email", "")),
            passwordHash=str(doc.get("passwordHash") or doc.get("password_hash") or ""),
            role=role,
            isActive=bool(doc.get("isActive", doc.get("is_active", True))),
            firstName=doc.get("firstName") or doc.get("first_name"),
            lastName=doc.get("lastName") or doc.get("last_name"),
            createdAt=(
                created_at if isinstance(created_at, datetime.datetime) else _now_utc()
            ),
            updatedAt=(
                updated_at if isinstance(updated_at, datetime.datetime) else _now_utc()
            ),
            _repo=repo,
        )


@dataclass(slots=True)
class Session:
    """Session record matching the Mongoose Session schema (`sessions`)."""

    userId: str
    token: str
    expiresAt: datetime.datetime
    userAgent: str | None = None
    ip: str | None = None
    ipAddress: str | None = None
    isRevoked: bool = False
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    createdAt: datetime.datetime = field(default_factory=_now_utc)
    updatedAt: datetime.datetime = field(default_factory=_now_utc)
    _repo: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.ip is None and self.ipAddress is not None:
            self.ip = self.ipAddress
        elif self.ipAddress is None and self.ip is not None:
            self.ipAddress = self.ip
        if self.expiresAt.tzinfo is None:
            self.expiresAt = self.expiresAt.replace(tzinfo=datetime.UTC)

    @property
    def _id(self) -> str:
        return self.id

    @property
    def user_id(self) -> str:
        return self.userId

    @property
    def user_agent(self) -> str | None:
        return self.userAgent

    @property
    def ip_address(self) -> str | None:
        return self.ipAddress if self.ipAddress is not None else self.ip

    @property
    def expires_at(self) -> datetime.datetime:
        return self.expiresAt

    @property
    def is_revoked(self) -> bool:
        return self.isRevoked

    @is_revoked.setter
    def is_revoked(self, value: bool) -> None:
        self.isRevoked = value

    async def save(self) -> Session:
        self.updatedAt = _now_utc()
        if self._repo is not None:
            save_fn = getattr(self._repo, "save_session", None) or getattr(
                self._repo, "save", None
            )
            if callable(save_fn):
                res = save_fn(self)
                if inspect.isawaitable(res):
                    await res
        return self

    def to_mongo_doc(self) -> dict[str, Any]:
        return {
            "_id": self.id,
            "userId": self.userId,
            "token": self.token,
            "userAgent": self.userAgent,
            "ip": self.ip,
            "ipAddress": self.ipAddress,
            "expiresAt": self.expiresAt,
            "isRevoked": self.isRevoked,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
        }

    @classmethod
    def from_mongo_doc(
        cls,
        doc: Mapping[str, Any],
        *,
        repo: Any = None,
    ) -> Session:
        raw_id = doc.get("_id") if "_id" in doc else doc.get("id")
        resolved_id = str(raw_id) if raw_id is not None else str(uuid.uuid4())
        raw_exp = doc.get("expiresAt") or doc.get("expires_at")
        if isinstance(raw_exp, datetime.datetime):
            exp = (
                raw_exp
                if raw_exp.tzinfo is not None
                else raw_exp.replace(tzinfo=datetime.UTC)
            )
        else:
            exp = _now_utc()
        created_at = doc.get("createdAt")
        updated_at = doc.get("updatedAt")
        ip_val = doc.get("ip") or doc.get("ipAddress") or doc.get("ip_address")
        return cls(
            id=resolved_id,
            userId=str(doc.get("userId") or doc.get("user_id") or ""),
            token=str(doc.get("token") or ""),
            userAgent=doc.get("userAgent") or doc.get("user_agent"),
            ip=str(ip_val) if ip_val is not None else None,
            ipAddress=str(ip_val) if ip_val is not None else None,
            expiresAt=exp,
            isRevoked=bool(doc.get("isRevoked", doc.get("is_revoked", False))),
            createdAt=(
                created_at if isinstance(created_at, datetime.datetime) else _now_utc()
            ),
            updatedAt=(
                updated_at if isinstance(updated_at, datetime.datetime) else _now_utc()
            ),
            _repo=repo,
        )


class UserRepository:
    """MongoDB / in-memory dual-mode User repository."""

    def __init__(self, mongo_service: Any = None) -> None:
        self._mongo_service = mongo_service
        self._memory_by_id: dict[str, User] = {}
        self._memory_by_email: dict[str, User] = {}
        self._indexes_ensured = False

    def _get_collection(self) -> Any:
        if self._mongo_service is None:
            return None
        get_db = getattr(self._mongo_service, "get_database", None)
        if not callable(get_db):
            return None
        db = get_db()
        return db["users"]

    async def _ensure_indexes(self, collection: Any) -> None:
        if self._indexes_ensured or collection is None:
            return
        self._indexes_ensured = True
        try:
            await collection.create_index("email", unique=True)
        except Exception:  # noqa: S110
            pass

    async def find_by_email(self, email: str) -> User | None:
        normalized = email.strip().lower()
        collection = self._get_collection()
        if collection is not None:
            await self._ensure_indexes(collection)
            doc = await collection.find_one({"email": normalized})
            if doc is None:
                return None
            return User.from_mongo_doc(doc, repo=self)
        return self._memory_by_email.get(normalized)

    async def find_by_id(self, user_id: str) -> User | None:
        collection = self._get_collection()
        if collection is not None:
            doc = await collection.find_one({"_id": user_id})
            if doc is None:
                return None
            return User.from_mongo_doc(doc, repo=self)
        return self._memory_by_id.get(user_id)

    async def create(self, user: User) -> User:
        user._repo = self
        return await self.save_user(user)

    async def save_user(self, user: User) -> User:
        user.email = user.email.strip().lower()
        user.updatedAt = _now_utc()
        user._repo = self
        collection = self._get_collection()
        if collection is not None:
            await self._ensure_indexes(collection)
            doc = user.to_mongo_doc()
            await collection.replace_one({"_id": user.id}, doc, upsert=True)
            return user
        self._memory_by_id[user.id] = user
        self._memory_by_email[user.email] = user
        return user


class SessionRepository:
    """MongoDB / in-memory dual-mode Session repository with TTL index on expiresAt."""

    def __init__(self, mongo_service: Any = None) -> None:
        self._mongo_service = mongo_service
        self._memory_by_token: dict[str, Session] = {}
        self._indexes_ensured = False

    def _get_collection(self) -> Any:
        if self._mongo_service is None:
            return None
        get_db = getattr(self._mongo_service, "get_database", None)
        if not callable(get_db):
            return None
        db = get_db()
        return db["sessions"]

    async def _ensure_indexes(self, collection: Any) -> None:
        if self._indexes_ensured or collection is None:
            return
        self._indexes_ensured = True
        try:
            await collection.create_index("token", unique=True)
            await collection.create_index("expiresAt", expireAfterSeconds=0)
        except Exception:  # noqa: S110
            pass

    async def create(self, session: Session) -> Session:
        session._repo = self
        return await self.save_session(session)

    async def save_session(self, session: Session) -> Session:
        session.updatedAt = _now_utc()
        session._repo = self
        collection = self._get_collection()
        if collection is not None:
            await self._ensure_indexes(collection)
            doc = session.to_mongo_doc()
            await collection.replace_one({"_id": session.id}, doc, upsert=True)
            return session
        self._memory_by_token[session.token] = session
        return session

    async def find_active_by_token(
        self,
        token: str,
        *,
        now: datetime.datetime | None = None,
    ) -> Session | None:
        current = now or _now_utc()
        collection = self._get_collection()
        if collection is not None:
            doc = await collection.find_one(
                {
                    "token": token,
                    "isRevoked": {"$ne": True},
                    "expiresAt": {"$gt": current},
                }
            )
            if doc is None:
                return None
            return Session.from_mongo_doc(doc, repo=self)

        session = self._memory_by_token.get(token)
        if session is None:
            return None
        if session.isRevoked or session.expiresAt <= current:
            return None
        return session

    async def rotate_session(
        self,
        incoming_token: str,
        *,
        new_token: str,
        new_expires_at: datetime.datetime,
        now: datetime.datetime | None = None,
    ) -> Session | None:
        """Atomically rotate an active non-expired session token."""
        current = now or _now_utc()
        collection = self._get_collection()
        if collection is not None:
            await self._ensure_indexes(collection)
            doc = await collection.find_one_and_update(
                {
                    "token": incoming_token,
                    "isRevoked": {"$ne": True},
                    "expiresAt": {"$gt": current},
                },
                {
                    "$set": {
                        "token": new_token,
                        "expiresAt": new_expires_at,
                        "updatedAt": current,
                    }
                },
            )
            if doc is None:
                return None
            updated = dict(doc)
            updated["token"] = new_token
            updated["expiresAt"] = new_expires_at
            updated["updatedAt"] = current
            return Session.from_mongo_doc(updated, repo=self)

        session = self._memory_by_token.get(incoming_token)
        if session is None or session.isRevoked or session.expiresAt <= current:
            return None
        del self._memory_by_token[incoming_token]
        session.token = new_token
        session.expiresAt = new_expires_at
        session.updatedAt = current
        self._memory_by_token[new_token] = session
        return session

    async def delete_by_token(self, token: str) -> Session | None:
        """Delete and return a session by token (matching `findOneAndDelete`)."""
        collection = self._get_collection()
        if collection is not None:
            doc = await collection.find_one_and_delete({"token": token})
            if doc is None:
                return None
            return Session.from_mongo_doc(doc, repo=self)
        session = self._memory_by_token.pop(token, None)
        if session is not None:
            session.isRevoked = True
        return session


__all__ = [
    "Session",
    "SessionRepository",
    "User",
    "UserRepository",
]
