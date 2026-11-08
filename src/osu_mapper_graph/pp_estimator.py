"""Attributes-only osu!standard FC PP estimates."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from subprocess import Popen, TimeoutExpired
from types import TracebackType
from typing import Final, Self, final

from .errors import EstimateError
from .models import BeatmapAttributes, BeatmapPpEstimate, BeatmapPpMetadata

SOURCE: Final[str] = "osu_api_attributes_nm+rosu-pp-4.0.1"


@dataclass(frozen=True, slots=True)
class PpEstimate:
    """Three NM FC estimates returned by the attributes-only engine."""

    pp_nm_100_fc: float
    pp_nm_98_fc: float
    pp_nm_95_fc: float


@final
class AttributesPpEngine:
    """Run the small rosu-pp attributes adapter without reading beatmap files."""

    def __init__(self, project_root: Path | None = None) -> None:
        root = project_root or Path(__file__).resolve().parents[2]
        self._tool_dir: Path = root / "tools" / "attributes-pp"
        self._binary: Path = self._tool_dir / "target" / "release" / "attributes-pp"
        self._process: Popen[str] | None = None

    def prepare(self) -> None:
        """Build the attributes adapter once if it is not already available."""
        if self._binary.is_file():
            return
        cargo = shutil.which("cargo")
        if cargo is None:
            raise EstimateError(0, "cargo is not available to build the attributes engine")
        subprocess.run(  # noqa: S603 - cargo is resolved from PATH, arguments are constants
            [cargo, "build", "--release", "--quiet"],
            cwd=self._tool_dir,
            check=True,
            text=True,
        )

    def __enter__(self) -> Self:
        self.prepare()
        self._process = subprocess.Popen(  # noqa: S603 - executable is the local fixed build output
            [str(self._binary)],
            cwd=self._tool_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._process is None:
            return
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=5)
        except TimeoutExpired:
            self._process.terminate()
            self._process.wait(timeout=5)
        self._process = None

    def estimate(
        self,
        beatmap_id: int,
        attributes: BeatmapAttributes,
        metadata: BeatmapPpMetadata,
    ) -> PpEstimate:
        """Estimate FC PP for 100%, 98%, and 95% using API JSON only."""
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise EstimateError(beatmap_id, "attributes engine is not running")

        payload = {
            "attributes": attributes.model_dump(),
            "metadata": metadata.model_dump(),
        }
        try:
            process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
            if not line:
                raise EstimateError(beatmap_id, "attributes engine exited without a result")
            result = BeatmapPpEstimate.model_validate_json(line)
            return PpEstimate(
                pp_nm_100_fc=result.pp_nm_100_fc,
                pp_nm_98_fc=result.pp_nm_98_fc,
                pp_nm_95_fc=result.pp_nm_95_fc,
            )
        except EstimateError:
            raise
        except (BrokenPipeError, OSError, TypeError, ValueError) as exc:
            raise EstimateError(beatmap_id, f"invalid attributes engine output: {exc}") from exc
