import numpy as np
import pytest

from labcontrol.core.units import parse_quantity, split_unit
from labcontrol.measure.sweep import Axis, SweepPlan


def legacy_pairs(I_start, I_stop, I_step):
    """sweep_main.py MeasurementThread.run 的原始電流序列。"""
    c1, c2 = I_start, I_start
    c1_list, c2_list = [c1], [c2]
    while c1 < I_stop or c2 < I_stop:
        if c1 == c2:
            c1 = round(c1 + I_step, 9)
        else:
            c2 = round(c2 + I_step, 9)
        c1_list.append(c1)
        c2_list.append(c2)
    return np.array(c1_list), np.array(c2_list)


def test_interleaved_pair_matches_legacy_sequence(station):
    station.connect()
    pair = station.source("pair")
    c1, c2 = legacy_pairs(158.619e-3, 159.047e-3, 1e-6)
    ax = Axis.from_config({"target": "pair.level", "unit": "mA", "start": 158.619, "stop": 159.047, "step": 0.0005})
    assert len(ax.values) == len(c1) == 857
    np.testing.assert_allclose(ax.values, (c1 + c2) / 2, atol=1e-12)
    for v, a, b in zip(ax.values, c1, c2):
        assert pair.split(v) == pytest.approx((a, b), abs=1e-12)


def test_units():
    assert split_unit("mA") == ("A", 1e-3)
    assert split_unit("mA/s") == ("A/s", 1e-3)
    assert split_unit("dBm") == ("dBm", 1.0)
    assert parse_quantity("5.0198 GHz") == pytest.approx(5.0198e9)
    assert parse_quantity("0.5 mA/s") == pytest.approx(5e-4)
    assert parse_quantity(3, "mA") == pytest.approx(3e-3)


def test_sweep_plan_nd_and_snake():
    a = Axis("VNA1.power", [-20, -10], name="P")
    b = Axis("pair.level", [1, 2, 3], name="I")
    plan = SweepPlan([a, b], snake=True)
    seq = [(plan.setpoints(i)["P"], plan.setpoints(i)["I"]) for i in range(len(plan))]
    assert seq == [(-20, 1), (-20, 2), (-20, 3), (-10, 3), (-10, 2), (-10, 1)]


def test_descending_axis():
    ax = Axis.from_config({"target": "x", "start": 1, "stop": 0, "step": 0.25})
    np.testing.assert_allclose(ax.values, [1, 0.75, 0.5, 0.25, 0])
