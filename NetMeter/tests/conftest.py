import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["QELNETMETER_HOME"] = tempfile.mkdtemp(prefix="netmeter_tests_")
