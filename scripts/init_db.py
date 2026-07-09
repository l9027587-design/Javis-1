"""One-off: create tables in the configured database. Run: python -m scripts.init_db"""
from src.db.session import init_db

if __name__ == "__main__":
    init_db()
    print("Tables created.")
