import os
import uuid
from pathlib import Path
from typing import Protocol


class ObjectStorage(Protocol):
    def put(self, content: bytes) -> str: ...

    def get(self, key: str) -> bytes | None: ...

    def delete(self, key: str) -> None: ...


class ObjectStorageError(OSError):
    pass


class LocalObjectStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def put(self, content: bytes) -> str:
        key = uuid.uuid4().hex
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            temporary = self.root / f".{key}.tmp"
            temporary.write_bytes(content)
            os.replace(temporary, self.root / key)
        except OSError as exc:
            raise ObjectStorageError(str(exc)) from exc
        return key

    def get(self, key: str) -> bytes | None:
        if not key.isalnum():
            return None
        try:
            return (self.root / key).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ObjectStorageError(str(exc)) from exc

    def delete(self, key: str) -> None:
        if key.isalnum():
            try:
                (self.root / key).unlink(missing_ok=True)
            except OSError as exc:
                raise ObjectStorageError(str(exc)) from exc
