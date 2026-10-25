"""OS-released process lock: serialize migrations, discovery and collection per DB."""

from pathlib import Path


class WriterLock:
    def __init__(self, database):
        self.path = Path(str(database) + ".lock") if str(database) != ":memory:" else None
        self.file = None

    def __enter__(self):
        if self.path is None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            import os

            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise OSError("Another writer is using this database; retry after it exits") from exc
        return self

    def __exit__(self, *args):
        if self.file:
            self.file.close()
