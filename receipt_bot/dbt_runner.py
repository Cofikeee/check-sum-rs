from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


class DbtRunner:
    """Run the deterministic silver dbt project when it is installed.

    The Python importer still materializes a compatible fallback for unit
    tests and minimal installations.  The Docker image includes dbt, so the
    running bot rebuilds the canonical silver models after each bronze write.
    """

    def __init__(self, project_dir: Path | None = None) -> None:
        self.project_dir = project_dir or Path(os.getenv("DBT_PROJECT_DIR", "/app/dbt"))
        self.enabled = os.getenv("DBT_ENABLED", "true").strip().lower() not in {"0", "false", "no"}

    @property
    def available(self) -> bool:
        return self.enabled and shutil.which("dbt") is not None and (self.project_dir / "dbt_project.yml").is_file()

    def build(self) -> bool:
        if not self.available:
            return False
        profiles_dir = os.getenv("DBT_PROFILES_DIR", str(self.project_dir))
        target = os.getenv("DBT_TARGET", "docker")
        completed = subprocess.run(
            [
                "dbt",
                "build",
                "--project-dir",
                str(self.project_dir),
                "--profiles-dir",
                profiles_dir,
                "--target",
                target,
                "--no-use-colors",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"dbt build failed: {detail[-4000:]}")
        return True
