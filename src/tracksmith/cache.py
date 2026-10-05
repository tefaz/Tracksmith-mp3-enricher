import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class Cache:
    VERSION = 1

    def __init__(self, directory: Path):
        self.directory = directory

    def key(self, namespace: str, identity: dict) -> Path:
        value = json.dumps([self.VERSION, namespace, identity], sort_keys=True).encode()
        return self.directory / namespace / (hashlib.sha256(value).hexdigest() + ".json")

    def get(self, namespace: str, identity: dict, max_age: float | None = None):
        path = self.key(namespace, identity)
        try:
            if max_age is not None and time.time() - path.stat().st_mtime > max_age:
                return None
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def put(self, namespace: str, identity: dict, value) -> None:
        atomic_write(self.key(namespace, identity), json.dumps(value).encode())


DISPOSABLE_NAMESPACES = {
    "http",
    "artwork",
    "artwork_missing",
    "fingerprints",
    "transcription",
    "alignment",
    "waveform",
}


def disposable_inventory(directory, context):
    directory = Path(directory)
    result = {}
    for namespace in sorted(DISPOSABLE_NAMESPACES):
        folder = directory / namespace
        if not folder.is_dir() or folder.is_symlink():
            continue
        files = []
        for path in folder.rglob("*"):
            context.check()
            if path.is_file() and not path.is_symlink():
                files.append((str(path), path.stat().st_size))
        if files:
            result[namespace] = files
    return result


def clear_disposable(directory, inventory, chosen, context):
    root = Path(directory).resolve()
    count = 0
    for namespace in chosen:
        if namespace not in DISPOSABLE_NAMESPACES:
            raise ValueError("This category contains protected user data")
        for name, _ in inventory.get(namespace, []):
            context.check()
            path = Path(name)
            if path.is_symlink() or not path.resolve().is_relative_to(root / namespace):
                raise ValueError("Cache path changed; inspect the cache again")
            if path.exists():
                path.unlink()
                count += 1
    return count
