"""Symbol feature 自己的 session factory helper。

main 上的 app.db.session 只有 engine + connectivity check；symbol feature
需要 request-scoped SQLAlchemy session，所以這裡基於 engine 再包一層 factory。
"""

from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_engine


def get_session_factory() -> sessionmaker[Session]:
    engine = get_engine()
    if engine is None:
        raise RuntimeError("Database engine is not initialized")
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)
