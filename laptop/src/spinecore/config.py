"""Configuration for the laptop-side tools.

Read from environment variables, optionally seeded from ``laptop/.env``. The
Cloudflare token here is the only write access to D1 that exists outside the
kosync protocol — the Worker deliberately exposes no admin surface — so it lives
on the laptop and nowhere else.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / "spine-data"

#: laptop/.env. This file is laptop/src/spinecore/config.py, so that is
#: parents[2]; it was once parents[3], the repo root, where no .env exists.
DOTENV = Path(__file__).resolve().parents[2] / ".env"

#: Asia/Kolkata has no DST, so a fixed offset is exact (see worker/src/time.ts).
IST_OFFSET_SECONDS = 19_800


def load_dotenv(path: Path | None = None) -> None:
    """Populate os.environ from a .env file, without overriding real env vars."""
    path = path or DOTENV
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


@dataclass(frozen=True)
class CloudflareConfig:
    account_id: str
    database_id: str
    api_token: str

    @property
    def query_url(self) -> str:
        return (
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
            f"/d1/database/{self.database_id}/query"
        )


@dataclass(frozen=True)
class ShelfConfig:
    inbox: Path
    library: Path
    quarantine: Path
    archive: Path
    ledger: Path
    data_dir: Path
    #: Optional KCC pass to pre-rotate two-page spreads. Off by default: it is
    #: lossy, and it is only worth it once the device lineup is settled.
    spreads_enabled: bool
    #: One canonical profile for every device. A per-device render changes the
    #: bytes, which changes the partial MD5, which forks progress per device —
    #: the exact failure this system exists to prevent.
    spreads_profile: str


def _path(env: str, default: Path) -> Path:
    return Path(os.environ.get(env, str(default))).expanduser()


def shelf_config() -> ShelfConfig:
    load_dotenv()
    data_dir = _path("SPINE_DATA_DIR", DEFAULT_DATA_DIR)
    return ShelfConfig(
        inbox=_path("SHELF_INBOX", Path.home() / "inbox"),
        library=_path("SHELF_LIBRARY", Path.home() / "library"),
        quarantine=_path("SHELF_QUARANTINE", Path.home() / "quarantine"),
        archive=_path("SHELF_ARCHIVE", data_dir / "archive"),
        ledger=_path("SHELF_LEDGER", data_dir / "shelf-ledger.sqlite3"),
        data_dir=data_dir,
        spreads_enabled=os.environ.get("SHELF_SPREADS", "").lower() in {"1", "true", "yes"},
        spreads_profile=os.environ.get("SHELF_SPREADS_PROFILE", "KoL"),
    )


def cloudflare_config() -> CloudflareConfig | None:
    """Returns None when the D1 credentials are absent.

    Catalogue sync is optional at the individual-file level: a book can be
    published locally and pushed to D1 later. It is not optional overall — every
    published file needs a `documents` row before it is first opened on a device,
    or it renders as "Unknown" on the reading page.
    """
    load_dotenv()
    account = os.environ.get("CF_ACCOUNT_ID", "").strip()
    database = os.environ.get("CF_D1_DATABASE_ID", "").strip()
    token = os.environ.get("CF_API_TOKEN", "").strip()
    if not (account and database and token):
        return None
    return CloudflareConfig(account_id=account, database_id=database, api_token=token)


def stats_since() -> int | None:
    """``KOSTATS_SINCE`` (YYYY-MM-DD) as the unix time of that midnight in IST.

    Sessions that started earlier are not imported. Devices keep their history
    forever and the nightly import is idempotent, so a fresh start has to be a
    cutoff at the source — deleting rows from D1 alone is undone overnight.
    """
    load_dotenv()
    value = os.environ.get("KOSTATS_SINCE", "").strip()
    if not value:
        return None
    day = datetime.date.fromisoformat(value)
    midnight_utc = datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.UTC)
    return int(midnight_utc.timestamp()) - IST_OFFSET_SECONDS
