r"""Lab Control（labcontrol 套件）— 實驗室儀器控制與量測框架。

    from labcontrol import Station, Experiment
    station = Station.from_lab(simulate=True)            # LAB/instruments.yaml
    exp = Experiment.from_file(r"C:\Users\even9\LAB\experiments\dc_vna_sweep.yaml", station)
    ds = exp.create_runner(exp.plan_output()).run()

版本規則：X.Y.Z 為功能版本；字尾 a、b、c… 為同一版本的 bug 修正（0.0.1 → 0.0.1a → 0.0.1b）。
"""
APP_NAME = "Lab Control"
__version__ = "0.0.12"

import logging as _logging

_logging.getLogger("labcontrol").addHandler(_logging.NullHandler())

from .core import *  # noqa: F401,F403
from .core import __all__ as _core_all
from .measure import Experiment, Procedure, RunOptions, Runner, SweepPlan  # noqa: F401

__all__ = list(_core_all) + ["Experiment", "Procedure", "RunOptions", "Runner", "SweepPlan", "__version__", "APP_NAME"]
