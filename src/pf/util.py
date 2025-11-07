import os
from typing import List


def ensure_dirs(paths: list[str]) -> None:
    for p in paths:
        os.makedirs(p, exist_ok=True)


def find_pdfs_in_dir(root_dir: str) -> list[str]:
    pdf_paths: List[str] = []
    for dirpath, _, filenames in os.walk(root_dir):
        for fn in filenames:
            if fn.lower().endswith(".pdf"):
                pdf_paths.append(os.path.join(dirpath, fn))
    return sorted(pdf_paths)
