from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Final

import anyio
import rosu_pp_py as rosu

from .api import OsuApi
from .database import Database
from .errors import ApiError
from .exporter import export_csv

ACCURACIES: Final[tuple[int, ...]] = (100, 99, 98, 97, 96, 90)
MISS_COUNTS: Final[tuple[int, ...]] = (0, 1, 2, 3, 5, 10)


def effective_accuracy(requested: float, objects: int, misses: int) -> float:
    if objects <= 0:
        return requested
    reachable = 100.0 * max(objects - misses, 0) / objects
    return min(requested, reachable)


def stable_hitresult_candidates(
    objects: int, misses: int, target_accuracy: float
) -> tuple[tuple[int, int, int], ...]:
    remaining = max(objects - misses, 0)
    effective = effective_accuracy(target_accuracy, objects, misses)
    target_score = effective / 100.0 * 6.0 * objects
    max_combo_score = 6 * remaining
    candidates: dict[tuple[int, int, int], float] = {}
    for n100 in range(remaining + 1):
        ideal_n50 = (max_combo_score - target_score - 4 * n100) / 5.0
        max_n50 = remaining - n100
        for proposed in (0, max_n50, int(ideal_n50), int(ideal_n50) + 1):
            n50 = min(max(proposed, 0), max_n50)
            n300 = remaining - n100 - n50
            score = 6 * n300 + 2 * n100 + n50
            candidates[(n300, n100, n50)] = abs(score - target_score)
    closest = min(candidates.values())
    return tuple(
        sorted(
            hitresults
            for hitresults, distance in candidates.items()
            if abs(distance - closest) < 1e-9
        )
    )


def scenario_names() -> tuple[tuple[str, int, int], ...]:
    result: list[tuple[str, int, int]] = [("100_fc", 100, 0)]
    result.extend(
        (f"{accuracy}_{misses}miss" if misses else f"{accuracy}_fc", accuracy, misses)
        for accuracy in ACCURACIES[1:]
        for misses in MISS_COUNTS
    )
    return tuple(result)


def result_payload(
    beatmap: rosu.Beatmap,
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    stable_attrs = rosu.Difficulty(lazer=False).calculate(beatmap)
    objects = sum(int(value or 0) for value in (stable_attrs.n_circles, stable_attrs.n_sliders, stable_attrs.n_spinners))
    scenarios: dict[str, dict[str, object]] = {}
    components: dict[str, dict[str, object]] = {}
    for name, requested, misses in scenario_names():
        accuracy = effective_accuracy(float(requested), objects, misses)
        combo_stable = max(int(stable_attrs.max_combo) - misses, 0)

        stable_values: list[tuple[rosu.PerformanceAttributes, tuple[int, int, int]]] = []
        for n300, n100, n50 in stable_hitresult_candidates(objects, misses, accuracy):
            realized_accuracy = (
                (6 * n300 + 2 * n100 + n50) / (6 * objects) * 100.0 if objects else accuracy
            )
            performance = rosu.Performance(
                accuracy=realized_accuracy,
                combo=combo_stable,
                misses=misses,
                lazer=False,
                hitresult_priority=rosu.HitResultPriority.BestCase,
                n300=n300,
                n100=n100,
                n50=n50,
            )
            stable_values.append((performance.calculate(stable_attrs), (n300, n100, n50)))
        ordered_stable = sorted(stable_values, key=lambda item: item[0].pp)
        stable_best = ordered_stable[-1]
        scenarios[name] = {
            "requested_accuracy": requested,
            "effective_accuracy": accuracy,
            "misses": misses,
            "stable_max_combo": int(stable_attrs.max_combo),
            "stable_candidate_distributions": len(ordered_stable),
            "stable_best": float(stable_best[0].pp),
        }
        components[name] = {
            "stable_best": {
                "pp": float(stable_best[0].pp),
                "hitresults": {"n300": stable_best[1][0], "n100": stable_best[1][1], "n50": stable_best[1][2], "misses": misses},
                **{
                    key: float(getattr(stable_best[0], key) or 0.0)
                    for key in ("pp_aim", "pp_speed", "pp_accuracy", "pp_flashlight", "effective_miss_count")
                },
            },
        }
    return scenarios, components


async def run(args: argparse.Namespace) -> None:
    database = Database(args.database)
    files_dir = args.files_dir
    files_dir.mkdir(parents=True, exist_ok=True)
    if args.clear_estimates:
        cleared = database.clear_attribute_pp_estimates()
        print(f"cleared_attribute_estimates={cleared}")  # noqa: T201
    rows = database.pending_full_pp_rows(limit=args.limit)
    print(f"pending_full_pp={len(rows)} scenarios_per_map={len(scenario_names())}")  # noqa: T201
    try:
        async with OsuApi(args.client_id, args.client_secret, request_delay=args.request_interval) as api:
            for index, row in enumerate(rows, 1):
                beatmap_id = int(row["id"])
                path = files_dir / f"{beatmap_id}.osu"
                relative_path = str(path)
                try:
                    if not path.is_file() or b"osu file format" not in path.read_bytes()[:128].lower():
                        content = await api.beatmap_file(beatmap_id)
                        temporary = path.with_suffix(".osu.part")
                        temporary.write_bytes(content)
                        temporary.replace(path)
                    beatmap = rosu.Beatmap(path=str(path))
                    scenarios, components = result_payload(beatmap)
                    database.upsert_full_pp(
                        beatmap_id,
                        osu_file_path=relative_path,
                        status="ok",
                        scenarios_json=json.dumps(scenarios, separators=(",", ":")),
                        components_json=json.dumps(components, separators=(",", ":")),
                    )
                except (ApiError, OSError, ValueError, RuntimeError) as exc:
                    database.upsert_full_pp(
                        beatmap_id,
                        osu_file_path=relative_path,
                        status="failed",
                        error=str(exc),
                    )
                database.commit()
                if index % 25 == 0 or index == len(rows):
                    print(f"progress={index}/{len(rows)}")  # noqa: T201
        export_csv(database, args.output)
    finally:
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--database", type=Path, default=Path("data/mapper_graph.sqlite3"))
    parser.add_argument("--files-dir", type=Path, default=Path("data/osu_files"))
    parser.add_argument("--output", type=Path, default=Path("data/mapper_catalog_full_pp.csv"))
    parser.add_argument("--request-interval", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--clear-estimates", action="store_true")
    args = parser.parse_args()
    anyio.run(run, args)


if __name__ == "__main__":
    main()
