import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rightsizers.ec2.telemetry_test_harness import create_main

if __name__ == "__main__":
    create_main()
