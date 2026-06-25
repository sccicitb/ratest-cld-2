"""Dev seed — a demo user + a couple of sessions, for parity with the frontend.

Run after migrations:  uv run python -m app.seed
Idempotent: does nothing if the demo user already exists.
"""
from __future__ import annotations

from app.auth.security import hash_password
from app.db import SessionLocal
from app.models import ChatSession, Message, User

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo1234"  # matches the frontend login pre-fill


def seed() -> None:
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == DEMO_EMAIL).first():
            print("Seed: demo user already exists — nothing to do.")
            return

        user = User(
            email=DEMO_EMAIL,
            display_name="Alex Demo",
            password_hash=hash_password(DEMO_PASSWORD),
        )
        db.add(user)
        db.flush()

        rag = ChatSession(user_id=user.id, title="How does RAG work?")
        db.add(rag)
        db.flush()
        db.add_all(
            [
                Message(session_id=rag.id, role="user", content="How does RAG work?"),
                Message(
                    session_id=rag.id,
                    role="assistant",
                    content="**RAG** retrieves relevant chunks from your indexed "
                    "documents and grounds the model's answer in them.",
                ),
            ]
        )
        db.add(ChatSession(user_id=user.id, title="Welcome to RAG Chat"))

        db.commit()
        print(f"Seed: created demo user ({DEMO_EMAIL} / {DEMO_PASSWORD}) + sessions.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
