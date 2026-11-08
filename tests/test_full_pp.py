from __future__ import annotations

from osu_mapper_graph.full_pp import stable_hitresult_candidates


def test_stable_hitresult_candidates_find_all_nearest_distributions() -> None:
    candidates = stable_hitresult_candidates(20, 0, 83.33333333333333)

    assert candidates == ((15, 5, 0), (16, 0, 4))


def test_stable_hitresult_candidates_cap_accuracy_at_miss_limit() -> None:
    candidates = stable_hitresult_candidates(10, 2, 100.0)

    assert candidates == ((8, 0, 0),)
