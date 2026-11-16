"""発行した書類の控えPDFの保存。

いまはローカルのフォルダに保存する。Supabase Storage などに替えるときは、
save と read の中身だけを差し替える。
"""

import hashlib
from pathlib import PurePosixPath

from app.config import settings


def _full_path(key: str):
    # key はサーバー側で組み立てた値だけを受け付ける(.. や絶対パスは拒否する)
    parts = PurePosixPath(key).parts
    if not parts or key.startswith("/") or ".." in parts:
        raise ValueError(f"不正な保存先です: {key}")
    return settings.document_dir.joinpath(*parts)


def save(key: str, data: bytes) -> str:
    """保存して、改ざん確認用の SHA-256 を返す。同じ場所への上書きはしない。"""
    path = _full_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        f.write(data)
    return hashlib.sha256(data).hexdigest()


def read(key: str) -> bytes:
    return _full_path(key).read_bytes()
