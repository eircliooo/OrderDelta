"""数据库引擎与会话。

**踩坑 #4**：`PRAGMA foreign_keys` 在 SQLite 里默认 **OFF**，SQLAlchemy 不会替你开。
不显式挂 connect 事件的话，所有 `ON DELETE CASCADE` 都是装饰品——删项目会留下
孤儿行和孤儿文件，「删除项目会删除对应数据和文件」静默失效。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.models import Base


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    """对 SQLite 连接强制打开外键约束。非 SQLite 驱动会静默跳过。"""
    cursor = getattr(dbapi_connection, "cursor", None)
    if cursor is None:  # pragma: no cover - 非 DBAPI 连接
        return
    try:
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    except Exception:  # pragma: no cover - 非 SQLite 后端
        return


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings.ensure_dirs()
        _engine = create_engine(settings.database_url, future=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def init_db() -> None:
    """建表。MVP-0 不用 Alembic（属 MVP-1），这里直接 create_all。"""
    Base.metadata.create_all(get_engine())


def reset_engine(url: str | None = None) -> None:
    """测试用：切换到独立数据库。"""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(url, future=True) if url else None
    _session_factory = None
    if _engine is not None:
        Base.metadata.create_all(_engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
