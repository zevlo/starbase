import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for sub in ("lambdas/ingest", "lambdas/read"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

# Never let tests reach a real account.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ["DISABLE_METRICS"] = "1"
os.environ["TABLE_NAME"] = "starbase-launches-test"
os.environ["RETRY_DELAY_SECONDS"] = "0"

FIXTURES = ROOT / "fixtures"
