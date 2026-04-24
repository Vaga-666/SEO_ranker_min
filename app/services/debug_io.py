import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def append_run_log(run_id: int, event: str, **data: Any) -> None:
    run_dir = Path("artifacts") / "runs" / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "debug_log.jsonl"
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "data": data,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def save_run_json(run_id: int, file_name: str, payload: dict[str, Any]) -> None:
    run_dir = Path("artifacts") / "runs" / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / file_name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
