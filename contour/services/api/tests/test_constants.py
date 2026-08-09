"""Pin every reference constant to its exact value from Global Constraints.

A wrong value here silently corrupts every route the system computes, so
each assertion below checks an exact value (never `pytest.approx`) unless
the source spec itself only promises an approximation.
"""

from contour import constants


def test_sf_bbox_exact_value_and_ordering() -> None:
    assert constants.SF_BBOX == (-122.5350, 37.7000, -122.3500, 37.8350)
    w, s, e, n = constants.SF_BBOX
    assert w < e
    assert s < n


def test_physics_constants_exact_values() -> None:
    assert constants.RIDER_MASS_KG == 85.0
    assert constants.GRAVITY == 9.81
    assert constants.C_RR == 0.005
    assert constants.AIR_DENSITY == 1.225
    assert constants.CDA_M2 == 0.40
    assert constants.V_FLAT_MPS == 4.17


def test_effort_model_constants_exact_values() -> None:
    assert constants.CLIMB_EQUIV_RATIO == 100.0
    assert constants.MIN_RISE_M == 1.0
    assert constants.PROFILE_SAMPLE_M == 10.0
    assert constants.GRADE_K_TABLE == (
        (4, 0.0),
        (6, 0.3),
        (9, 1.0),
        (12, 2.5),
        (15, 5.0),
        (999, 12.0),
    )
    assert dict(constants.DETOUR_BUDGET) == {1: 1.05, 2: 1.15, 3: 1.25, 4: 1.35, 5: 1.50}
    assert dict(constants.K_SCALE) == {1: 0.0, 2: 0.5, 3: 1.0, 4: 2.0, 5: 4.0}
    assert constants.DIVERSITY_OVERLAP_MAX == 0.70


def test_grade_k_table_sorted_ascending_by_grade() -> None:
    grades = [grade for grade, _k in constants.GRADE_K_TABLE]
    assert grades == sorted(grades)


def test_detour_budget_and_k_scale_have_keys_one_through_five() -> None:
    assert set(constants.DETOUR_BUDGET.keys()) == {1, 2, 3, 4, 5}
    assert set(constants.K_SCALE.keys()) == {1, 2, 3, 4, 5}


def test_step5_additional_constants_exact_values() -> None:
    assert constants.UNAVOIDABLE_CLIMB_M == 64.0
    assert constants.SMOOTH_WINDOW_SAMPLES == 9
    assert constants.SMOOTH_POLY_ORDER == 2
    assert constants.GRADE_WINDOW_SAMPLES == 3
    assert constants.DIVERSITY_GRID_M == 20.0
    assert constants.CANDIDATE_COUNT == 8
