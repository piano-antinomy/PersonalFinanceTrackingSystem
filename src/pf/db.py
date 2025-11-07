import os
import sqlite3
import datetime as dt
from typing import Optional, List

from pypdf import PdfReader


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_if_needed(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self.connect() as conn:
            c = conn.cursor()
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                  id INTEGER PRIMARY KEY,
                  type TEXT NOT NULL,
                  name TEXT NOT NULL,
                  institution TEXT,
                  currency TEXT NOT NULL DEFAULT 'USD',
                  opened_at DATE,
                  closed_at DATE
                );
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_accounts_type ON accounts(type);")

            c.execute(
                """
                CREATE TABLE IF NOT EXISTS statements (
                  id INTEGER PRIMARY KEY,
                  account_id INTEGER NOT NULL REFERENCES accounts(id),
                  period_start DATE NOT NULL,
                  period_end DATE NOT NULL,
                  source_file_path TEXT NOT NULL,
                  source_file_hash TEXT NOT NULL,
                  imported_at DATETIME NOT NULL,
                  status TEXT NOT NULL DEFAULT 'imported',
                  UNIQUE (account_id, period_start, period_end)
                );
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_statements_account_period ON statements(account_id, period_start, period_end);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_statements_hash ON statements(source_file_hash);")

            c.execute(
                """
                CREATE TABLE IF NOT EXISTS categories (
                  id INTEGER PRIMARY KEY,
                  name TEXT NOT NULL,
                  parent_id INTEGER,
                  type TEXT NOT NULL
                );
                """
            )
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_name ON categories(name);")

            c.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                  id INTEGER PRIMARY KEY,
                  account_id INTEGER NOT NULL REFERENCES accounts(id),
                  statement_id INTEGER REFERENCES statements(id),
                  posted_at DATE NOT NULL,
                  description TEXT NOT NULL,
                  merchant TEXT,
                  amount NUMERIC NOT NULL,
                  currency TEXT NOT NULL DEFAULT 'USD',
                  category_id INTEGER,
                  is_transfer INTEGER NOT NULL DEFAULT 0,
                  is_income INTEGER NOT NULL DEFAULT 0,
                  is_expense INTEGER NOT NULL DEFAULT 0,
                  hash TEXT,
                  raw_json TEXT
                );
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_tx_account_date ON transactions(account_id, posted_at);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_tx_category_date ON transactions(category_id, posted_at);")

            # Seed a few categories if empty
            c.execute("SELECT COUNT(1) FROM categories;")
            if (c.fetchone() or [0])[0] == 0:
                seed = [
                    ("Uncategorized", None, "expense"),
                    ("Groceries", None, "expense"),
                    ("Dining", None, "expense"),
                    ("Transport", None, "expense"),
                    ("Shopping", None, "expense"),
                    ("Utilities", None, "expense"),
                    ("Mortgage", None, "expense"),
                    ("Income:Salary", None, "income"),
                    ("Income:Dividend", None, "income"),
                ]
                c.executemany("INSERT INTO categories(name, parent_id, type) VALUES (?,?,?)", seed)
            conn.commit()

    def statement_exists_by_hash(self, file_hash: str) -> bool:
        with self.connect() as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM statements WHERE source_file_hash = ? LIMIT 1", (file_hash,))
            return c.fetchone() is not None

    def ensure_account(self, account_type: str, name: str, institution: Optional[str]) -> int:
        with self.connect() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT id FROM accounts WHERE name = ? AND IFNULL(institution,'') = IFNULL(?, '') LIMIT 1",
                (name, institution),
            )
            row = c.fetchone()
            if row:
                return int(row[0])
            c.execute(
                "INSERT INTO accounts(type, name, institution, opened_at) VALUES (?,?,?,?)",
                (account_type, name, institution, dt.date.today().isoformat()),
            )
            conn.commit()
            return int(c.lastrowid)

    def insert_statement(
        self,
        account_id: int,
        period_start: dt.date,
        period_end: dt.date,
        source_file_path: str,
        source_file_hash: str,
        status: str,
    ) -> int:
        with self.connect() as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO statements(account_id, period_start, period_end, source_file_path, source_file_hash, imported_at, status)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    account_id,
                    period_start.isoformat(),
                    period_end.isoformat(),
                    source_file_path,
                    source_file_hash,
                    dt.datetime.now().isoformat(timespec="seconds"),
                    status,
                ),
            )
            conn.commit()
            return int(c.lastrowid)

    def insert_transaction(
        self,
        account_id: int,
        statement_id: int,
        posted_at: dt.date,
        description: str,
        merchant: Optional[str],
        amount: float,
        currency: str,
        category_id: Optional[int],
        is_transfer: int,
        is_income: int,
        is_expense: int,
        raw_json: Optional[str],
    ) -> int:
        with self.connect() as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO transactions(account_id, statement_id, posted_at, description, merchant, amount, currency, category_id, is_transfer, is_income, is_expense, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    account_id,
                    statement_id,
                    posted_at.isoformat(),
                    description,
                    merchant,
                    amount,
                    currency,
                    category_id,
                    is_transfer,
                    is_income,
                    is_expense,
                    raw_json,
                ),
            )
            conn.commit()
            return int(c.lastrowid)

    def get_or_create_category(self, name: str, type_: str) -> int:
        with self.connect() as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM categories WHERE name = ? LIMIT 1", (name,))
            row = c.fetchone()
            if row:
                return int(row[0])
            c.execute("INSERT INTO categories(name, type) VALUES (?,?)", (name, type_))
            conn.commit()
            return int(c.lastrowid)

    def read_pdf_text(self, pdf_path: str) -> tuple[str, list[str]]:
        reader = PdfReader(pdf_path)
        pages_text: List[str] = []
        max_pages = min(3, len(reader.pages))
        for i in range(max_pages):
            page = reader.pages[i]
            pages_text.append(page.extract_text() or "")
        full_text = "\n".join(pages_text)
        return full_text, pages_text
