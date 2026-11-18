"""Upgrade a real pre-card SQLite schema without touching the application DB."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import settings


def test_message_result_migration_preserves_legacy_messages(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setattr(settings, "database_url", url)
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
    )
    command.upgrade(config, "c82b61f42e30")
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO users (id,email,display_name,password_hash,created_at,is_admin,disabled,voice) VALUES ('u','u@example.test','User','unused',CURRENT_TIMESTAMP,0,0,'F2')"
            )
        )
        db.execute(
            text(
                "INSERT INTO sessions (id,user_id,title,created_at,updated_at) VALUES ('s','u','Legacy',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO messages (id,session_id,role,content,created_at) VALUES ('m','s','assistant','Legacy text',CURRENT_TIMESTAMP)"
            )
        )
    command.upgrade(config, "head")
    assert "simulation_results" in {c["name"] for c in inspect(engine).get_columns("messages")}
    with engine.connect() as db:
        assert db.execute(
            text("SELECT content, simulation_results FROM messages WHERE id='m'")
        ).one() == ("Legacy text", None)
    command.downgrade(config, "c82b61f42e30")
    assert "simulation_results" not in {c["name"] for c in inspect(engine).get_columns("messages")}
    with engine.connect() as db:
        assert (
            db.execute(text("SELECT content FROM messages WHERE id='m'")).scalar_one()
            == "Legacy text"
        )
    engine.dispose()
