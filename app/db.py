from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def ensure_sqlite_schema(db_engine) -> None:
    if db_engine.dialect.name != "sqlite":
        return

    create_analysis_keywords_sql = """
    CREATE TABLE IF NOT EXISTS analysis_keywords (
        id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL,
        keyword TEXT NOT NULL,
        status VARCHAR(64) NOT NULL DEFAULT 'pending',
        error_message TEXT NULL,
        created_at DATETIME NOT NULL,
        FOREIGN KEY(run_id) REFERENCES analysis_runs (id)
    )
    """
    create_analysis_keywords_run_id_index_sql = """
    CREATE INDEX IF NOT EXISTS ix_analysis_keywords_run_id
    ON analysis_keywords (run_id)
    """
    serp_results_columns_sql = "PRAGMA table_info(serp_results)"
    add_serp_results_keyword_id_sql = """
    ALTER TABLE serp_results
    ADD COLUMN keyword_id INTEGER NULL REFERENCES analysis_keywords(id)
    """
    create_serp_results_keyword_id_index_sql = """
    CREATE INDEX IF NOT EXISTS ix_serp_results_keyword_id
    ON serp_results (keyword_id)
    """

    with db_engine.begin() as connection:
        connection.execute(text(create_analysis_keywords_sql))
        connection.execute(text(create_analysis_keywords_run_id_index_sql))
        serp_results_columns = connection.execute(text(serp_results_columns_sql)).mappings().all()
        serp_results_column_names = {row["name"] for row in serp_results_columns}
        if "keyword_id" not in serp_results_column_names:
            connection.execute(text(add_serp_results_keyword_id_sql))
        connection.execute(text(create_serp_results_keyword_id_index_sql))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
