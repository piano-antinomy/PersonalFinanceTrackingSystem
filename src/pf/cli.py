import os
import sys
import hashlib

import click

from pf.db import Database
from pf.util import ensure_dirs, find_pdfs_in_dir
from pf.classify import classify_statement, extract_institution_and_account, infer_statement_period
from pf.parsers.generic import extract_transactions_from_text, categorize_transaction


@click.group()
def cli() -> None:
    pass


@cli.command(name="import")
@click.option("--input", "input_dir", required=True, type=click.Path(exists=True, file_okay=False, dir_okay=True, readable=True), help="Folder containing PDF statements to import")
def import_cmd(input_dir: str) -> None:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    data_dir = os.path.join(project_root, "data")
    db_dir = os.path.join(data_dir, "db")
    archive_dir = os.path.join(data_dir, "archive")
    unclassified_dir = os.path.join(data_dir, "unclassified")
    ensure_dirs([data_dir, db_dir, archive_dir, unclassified_dir])

    db_path = os.path.join(db_dir, "finance.sqlite")
    db = Database(db_path)
    db.init_if_needed()

    pdf_paths = find_pdfs_in_dir(input_dir)
    if not pdf_paths:
        click.echo("No PDF files found.")
        sys.exit(0)

    imported = 0
    skipped = 0
    for pdf_path in pdf_paths:
        try:
            with open(pdf_path, "rb") as f:
                file_bytes = f.read()
            file_hash = hashlib.sha256(file_bytes).hexdigest()

            if db.statement_exists_by_hash(file_hash):
                skipped += 1
                continue

            # Read text (first pages) for classification and parsing
            text, text_pages = db.read_pdf_text(pdf_path)
            statement_type, confidence = classify_statement(text)
            institution, masked_account = extract_institution_and_account(text)

            if confidence < 0.5:
                # Low confidence → move to unclassified and skip
                dst_dir = os.path.join(unclassified_dir)
                ensure_dirs([dst_dir])
                base_name = os.path.basename(pdf_path)
                dst_path = os.path.join(dst_dir, base_name)
                if os.path.abspath(pdf_path) != os.path.abspath(dst_path):
                    try:
                        os.rename(pdf_path, dst_path)
                    except OSError:
                        pass
                skipped += 1
                continue

            # Ensure account
            account_id = db.ensure_account(
                account_type=statement_type,
                name=f"{institution or 'Unknown'} {masked_account or ''}".strip(),
                institution=institution or "Unknown",
            )

            # Infer period
            period_start, period_end = infer_statement_period(text)

            statement_id = db.insert_statement(
                account_id=account_id,
                period_start=period_start,
                period_end=period_end,
                source_file_path=os.path.abspath(pdf_path),
                source_file_hash=file_hash,
                status="parsed",
            )

            # Extract transactions heuristically
            transactions = extract_transactions_from_text(text_pages)
            for txn in transactions:
                category_id, flags = categorize_transaction(db, txn)
                db.insert_transaction(
                    account_id=account_id,
                    statement_id=statement_id,
                    posted_at=txn["posted_at"],
                    description=txn["description"],
                    merchant=txn.get("merchant"),
                    amount=txn["amount"],
                    currency="USD",
                    category_id=category_id,
                    is_transfer=0,
                    is_income=1 if flags.get("is_income") else 0,
                    is_expense=1 if flags.get("is_expense") else 0,
                    raw_json=None,
                )

            imported += 1

            # Archive the processed file
            yyyy = str(period_end.year)
            mm = f"{period_end.month:02d}"
            dst_dir = os.path.join(archive_dir, yyyy, mm)
            ensure_dirs([dst_dir])
            base_name = os.path.basename(pdf_path)
            dst_path = os.path.join(dst_dir, base_name)
            if os.path.abspath(pdf_path) != os.path.abspath(dst_path):
                try:
                    os.rename(pdf_path, dst_path)
                except OSError:
                    pass
        except Exception as exc:
            click.echo(f"Error importing {pdf_path}: {exc}")
            skipped += 1

    click.echo(f"Imported: {imported}, Skipped: {skipped}")


def main() -> None:
    cli()
