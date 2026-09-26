import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rightsizers.ec2.telemetry_test_harness import validate_main

if __name__ == "__main__":
    validate_main()
