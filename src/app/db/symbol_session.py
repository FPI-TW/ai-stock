"""Symbol feature 自己的 session factory helper。

main 上的 app.db.session 只有 engine + connectivity check；symbol feature
需要 request-scoped SQLAlchemy session，所以這裡基於 engine 再包一層 factory。
"""

from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_engine

_session_factory: sessionmaker[Session] | None = None


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        if engine is None:
            raise RuntimeError("Database engine is not initialized")
        _session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return _session_factory
