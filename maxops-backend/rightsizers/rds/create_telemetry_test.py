import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rightsizers.common.managed_telemetry import create
if __name__ == "__main__": create("rds",Path(__file__).resolve().parent)
