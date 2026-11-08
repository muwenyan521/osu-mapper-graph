"""Command-line entry point."""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import anyio
import typer
from rich.console import Console

from .api import OsuApi
from .crawler import Crawler
from .database import Database
from .errors import ApiError, ConfigurationError, DataError, EstimateError
from .exporter import export_csv
from .models import BeatmapPpMetadata, Difficulty, User
from .pp_estimator import SOURCE, AttributesPpEngine

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


def _credentials() -> tuple[str, str]:
    client_id = os.getenv("OSU_CLIENT_ID")
    client_secret = os.getenv("OSU_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ConfigurationError(
            "缺少 OSU_CLIENT_ID 或 OSU_CLIENT_SECRET。无需 osu 用户网页登录；"
            "请在 osu! OAuth 应用中创建 public client，并通过环境变量提供这两个值。",
        )
    return client_id, client_secret


@app.command()
def crawl(
    seed: list[str] = typer.Option(["Sotarks"], "--seed", help="初始 mapper 用户名，可重复传入。"),
    database: Path = typer.Option(Path("data/mapper_graph.sqlite3"), "--database"),
    max_depth: int | None = typer.Option(None, "--max-depth", min=0),
    retry_failed: bool = typer.Option(False, "--retry-failed"),
    refresh_user_tags: bool = typer.Option(False, "--refresh-user-tags"),
    skip_user_tags: bool = typer.Option(False, "--skip-user-tags"),
    request_interval: float = typer.Option(
        1.0,
        "--request-interval",
        min=1.0,
        help="API 请求起始间隔（秒），官方建议不低于 1 秒。",
    ),
) -> None:
    """从 seed 开始遍历 Ranked osu!standard mapper 闭包。"""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        client_id, client_secret = _credentials()
    except ConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    async def run() -> None:
        db = Database(database, recover_processing=True)
        try:
            seed_ids: list[int] = []
            if refresh_user_tags:
                db.refresh_user_tags()
            async with OsuApi(client_id, client_secret, request_delay=request_interval) as api:
                for username in seed:
                    user = await api.user(username)
                    db.seed(user)
                    seed_ids.append(user.id)
                db.recompute_depths(seed_ids)
                stats = await Crawler(
                    api,
                    db,
                    max_depth=max_depth,
                    collect_user_tags=not skip_user_tags,
                ).run(retry_failed=retry_failed)
            console.print(
                f"processed={stats.processed_mappers} discovered={stats.discovered_mappers} "
                f"mapsets={stats.ranked_mapsets} difficulties={stats.ranked_difficulties} "
                f"failures={stats.failures}",
            )
            if stats.failures:
                raise typer.Exit(code=1)
        finally:
            db.close()

    try:
        anyio.run(run)
    except (ApiError, DataError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("seed-crawl")
def seed_crawl(
    seed: list[str] = typer.Option(..., "--seed", help="额外 depth 0 mapper 用户名，可重复传入。"),
    database: Path = typer.Option(Path("data/mapper_graph.sqlite3"), "--database"),
    max_depth: int = typer.Option(2, "--max-depth", min=1),
    skip_user_tags: bool = typer.Option(False, "--skip-user-tags"),
    retry_failed: bool = typer.Option(False, "--retry-failed"),
    request_interval: float = typer.Option(
        1.0,
        "--request-interval",
        min=1.0,
        help="API 请求起始间隔（秒），官方建议不低于 1 秒。",
    ),
) -> None:
    """以额外 seeds 为 depth 0, 按 max-depth 展开 mapper。"""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        client_id, client_secret = _credentials()
    except ConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    async def run() -> None:
        db = Database(database, recover_processing=True)
        try:
            async with OsuApi(client_id, client_secret, request_delay=request_interval) as api:
                users: list[User] = []
                seed_errors: list[str] = []
                for username in seed:
                    try:
                        users.append(await api.user(username))
                    except ApiError as exc:
                        seed_errors.append(f"{username}: {exc}")
                if not users:
                    raise DataError("all seed users failed: " + "; ".join(seed_errors))
                db.prepare_seed_batch(users)
                db.recompute_depths([user.id for user in users])
                stats = await Crawler(
                    api,
                    db,
                    max_depth=max_depth,
                    collect_user_tags=not skip_user_tags,
                ).run(retry_failed=retry_failed)
            console.print(
                f"processed={stats.processed_mappers} discovered={stats.discovered_mappers} "
                f"mapsets={stats.ranked_mapsets} difficulties={stats.ranked_difficulties} "
                f"failures={stats.failures}",
            )
            if seed_errors:
                console.print("unresolved_seeds=" + " | ".join(seed_errors))
            if stats.failures:
                raise typer.Exit(code=1)
        finally:
            db.close()

    try:
        anyio.run(run)
    except (ApiError, DataError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("mapset-crawl")
def mapset_crawl(
    mapset_id: list[int] = typer.Option(
        ..., "--mapset-id", help="osu! beatmapset ID，可重复传入。"
    ),
    database: Path = typer.Option(Path("data/mapper_graph.sqlite3"), "--database"),
    max_depth: int = typer.Option(3, "--max-depth", min=1),
    skip_user_tags: bool = typer.Option(False, "--skip-user-tags"),
    retry_failed: bool = typer.Option(False, "--retry-failed"),
    request_interval: float = typer.Option(1.0, "--request-interval", min=1.0),
) -> None:
    """把 mapset 加入图池并从其 owners 展开到指定深度。"""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        client_id, client_secret = _credentials()
    except ConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    async def run() -> None:
        db = Database(database, recover_processing=True)
        try:
            async with OsuApi(client_id, client_secret, request_delay=request_interval) as api:
                crawler = Crawler(api, db, collect_user_tags=not skip_user_tags)
                new_mapsets, seed_count = await crawler.ingest_mapset_seeds(mapset_id)
                stats = await Crawler(
                    api,
                    db,
                    max_depth=max_depth,
                    collect_user_tags=not skip_user_tags,
                ).run(retry_failed=retry_failed)
            console.print(
                f"new_mapsets={new_mapsets} promoted_seeds={seed_count} "
                f"processed={stats.processed_mappers} discovered={stats.discovered_mappers} "
                f"mapsets={stats.ranked_mapsets} difficulties={stats.ranked_difficulties} "
                f"failures={stats.failures}",
            )
            if stats.failures:
                raise typer.Exit(code=1)
        finally:
            db.close()

    try:
        anyio.run(run)
    except (ApiError, DataError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def export(
    database: Path = typer.Option(Path("data/mapper_graph.sqlite3"), "--database"),
    output: Path = typer.Option(Path("data/mapper_catalog.csv"), "--output"),
    max_depth: int | None = typer.Option(None, "--max-depth", min=0),
) -> None:
    """导出 mapper、host/guest 关系和 difficulty 元信息。"""
    db = Database(database)
    try:
        rows = export_csv(db, output, max_depth)
    finally:
        db.close()
    console.print(f"exported {rows} rows to {output}")


async def _estimate_one_pp(
    api: OsuApi,
    database: Database,
    engine: AttributesPpEngine,
    row: sqlite3.Row,
    detail: Difficulty | None,
) -> None:
    """Estimate and persist one tagged difficulty."""
    beatmap_id = int(row["id"])
    if detail is None:
        raise EstimateError(beatmap_id, "beatmap JSON metadata is missing")
    database.add_difficulty(detail)
    raw_ar = detail.ar if detail.ar is not None else row["ar"]
    raw_od = detail.od if detail.od is not None else row["od"]
    raw_hp = detail.drain if detail.drain is not None else row["hp"]
    raw_circles = detail.count_circles
    raw_sliders = detail.count_sliders
    raw_spinners = detail.count_spinners
    if (
        raw_ar is None
        or raw_od is None
        or raw_hp is None
        or raw_circles is None
        or raw_sliders is None
        or raw_spinners is None
    ):
        raise EstimateError(beatmap_id, "beatmap JSON metadata is incomplete")
    metadata = BeatmapPpMetadata(
        ar=float(raw_ar),
        od=float(raw_od),
        hp=float(raw_hp),
        count_circles=int(raw_circles),
        count_sliders=int(raw_sliders),
        count_spinners=int(raw_spinners),
    )
    attributes = await api.beatmap_attributes(beatmap_id)
    result = engine.estimate(beatmap_id, attributes.attributes, metadata)
    database.upsert_pp_estimate(
        beatmap_id,
        source=SOURCE,
        status="ok",
        pp_nm_100_fc=result.pp_nm_100_fc,
        pp_nm_98_fc=result.pp_nm_98_fc,
        pp_nm_95_fc=result.pp_nm_95_fc,
        attribute_star_rating=attributes.attributes.star_rating,
        attribute_max_combo=attributes.attributes.max_combo,
    )


async def _estimate_pp_rows(
    api: OsuApi,
    database: Database,
    engine: AttributesPpEngine,
    rows: Sequence[sqlite3.Row],
) -> tuple[int, int]:
    """Estimate a batch and return successful and failed counts."""
    details = {detail.id: detail for detail in await api.beatmaps([int(row["id"]) for row in rows])}
    estimated = 0
    failed = 0
    for index, row in enumerate(rows, 1):
        beatmap_id = int(row["id"])
        try:
            await _estimate_one_pp(api, database, engine, row, details.get(beatmap_id))
            estimated += 1
        except (ApiError, EstimateError, KeyError, ValueError) as exc:
            database.upsert_pp_estimate(
                beatmap_id,
                source=SOURCE,
                status="failed",
                error=str(exc),
            )
            failed += 1
        database.commit()
        if index % 25 == 0 or index == len(rows):
            console.print(
                f"progress={index}/{len(rows)} estimated={estimated} failed={failed}"
            )
    return estimated, failed


@app.command("estimate-pp")
def estimate_pp(
    database: Path = typer.Option(Path("data/mapper_graph.sqlite3"), "--database"),
    output: Path = typer.Option(
        Path("data/mapper_catalog_with_pp.csv"),
        "--output",
        help="估算完成后导出的 CSV，不覆盖原始 catalog。",
    ),
    request_interval: float = typer.Option(
        1.0,
        "--request-interval",
        min=1.0,
        help="API 请求起始间隔（秒），官方建议不低于 1 秒。",
    ),
    limit: int | None = typer.Option(None, "--limit", min=1),
    force: bool = typer.Option(False, "--force", help="重新计算已有成功记录。"),
) -> None:
    """用 API attributes 估算 tagged ranked osu!standard difficulty 的 FC PP。"""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        client_id, client_secret = _credentials()
    except ConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    async def run() -> None:
        db = Database(database)
        try:
            rows = db.pending_pp_estimate_rows(force=force, limit=limit)
            ids = [int(row["id"]) for row in rows]
            console.print(f"pending_tagged_ranked_osu={len(ids)} source={SOURCE}")
            if not ids:
                export_csv(db, output)
                return

            async with OsuApi(client_id, client_secret, request_delay=request_interval) as api:
                with AttributesPpEngine() as engine:
                    await _estimate_pp_rows(api, db, engine, rows)
            export_csv(db, output)
        finally:
            db.close()

    try:
        anyio.run(run)
    except (ApiError, DataError, EstimateError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def tags(
    database: Path = typer.Option(Path("data/mapper_graph.sqlite3"), "--database"),
    request_interval: float = typer.Option(
        1.0,
        "--request-interval",
        min=1.0,
        help="API 请求起始间隔（秒），官方建议不低于 1 秒。",
    ),
) -> None:
    """只为已入库谱面补 User tags, 不发现或展开 mapper。"""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        client_id, client_secret = _credentials()
    except ConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    async def run() -> None:
        db = Database(database)
        try:
            async with OsuApi(client_id, client_secret, request_delay=request_interval) as api:
                difficulties, mapsets = await Crawler(api, db).hydrate_pending_tags()
            console.print(f"tagged_difficulties={difficulties} tagged_mapsets={mapsets}")
        finally:
            db.close()

    try:
        anyio.run(run)
    except (ApiError, DataError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("map-only")
def map_only(
    min_depth: int = typer.Option(3, "--min-depth", min=0),
    database: Path = typer.Option(Path("data/mapper_graph.sqlite3"), "--database"),
    request_interval: float = typer.Option(
        1.0,
        "--request-interval",
        min=1.0,
        help="API 请求起始间隔（秒），官方建议不低于 1 秒。",
    ),
) -> None:
    """只抓指定深度以上 pending mapper 的 map, 不继续发现 mapper。"""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        client_id, client_secret = _credentials()
    except ConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    async def run() -> None:
        db = Database(database, recover_processing=True)
        try:
            async with OsuApi(client_id, client_secret, request_delay=request_interval) as api:
                stats = await Crawler(
                    api,
                    db,
                    min_depth=min_depth,
                    collect_user_tags=False,
                    discover_mappers=False,
                ).run()
            console.print(
                f"processed={stats.processed_mappers} discovered={stats.discovered_mappers} "
                f"mapsets={stats.ranked_mapsets} difficulties={stats.ranked_difficulties} "
                f"failures={stats.failures}",
            )
            if stats.failures:
                raise typer.Exit(code=1)
        finally:
            db.close()

    try:
        anyio.run(run)
    except (ApiError, DataError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def status(database: Path = typer.Option(Path("data/mapper_graph.sqlite3"), "--database")) -> None:
    """显示当前断点续跑进度。"""
    db = Database(database)
    try:
        console.print(db.counts())
    finally:
        db.close()


if __name__ == "__main__":
    app()
