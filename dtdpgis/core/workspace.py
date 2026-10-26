"""Project workspace and run creation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from dtdpgis.core.config import load_profile
from dtdpgis.core.models import RunContext


class ProjectWorkspace:
    def __init__(self, project_root: str | Path, profile: dict[str, Any] | None = None) -> None:
        self.root = Path(project_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.profile = profile if profile is not None else load_profile(self.root)

    def new_run(self, run_id: str | None = None) -> RunContext:
        run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        outputs_dir = self.root / "outputs" / run_id
        quarantine_dir = self.root / "quarantine" / run_id
        reports_dir = self.root / "reports"
        for path in (outputs_dir, quarantine_dir, reports_dir):
            path.mkdir(parents=True, exist_ok=True)
        return RunContext(
            run_id=run_id,
            project_root=self.root,
            outputs_dir=outputs_dir,
            quarantine_dir=quarantine_dir,
            reports_dir=reports_dir,
            state_db=self.root / "state.db",
            profile=self.profile,
        )
