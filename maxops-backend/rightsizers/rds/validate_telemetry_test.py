import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rightsizers.common.managed_telemetry import validate
if __name__ == "__main__": validate("rds")
