import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rightsizers.common.managed_telemetry import cleanup_cli
if __name__ == "__main__": cleanup_cli("rds")
