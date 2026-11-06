"""Keep every pytest run away from the user's live WolfKiller data directory."""

from __future__ import annotations

import os
import tempfile


os.environ["WOLFKILLER_DATA_DIR"] = tempfile.mkdtemp(prefix="wolfkiller-pytest-")
os.environ["MODEL_CONFIG_PATH"] = os.path.join(os.environ["WOLFKILLER_DATA_DIR"], "models.json")
os.environ["DEEPSEEK_API_KEY"] = ""
# Never spawn the provider-SDK/latency warmup thread from app.main in tests.
os.environ["WOLFKILLER_PROVIDER_PREWARM"] = "0"
# The session-affinity experiment switch must never leak from the operator's
# shell (or .env, which load_dotenv reads later) into the suite; setting it
# here also stops load_dotenv from filling it in. Tests that care set it
# explicitly with monkeypatch. The cache-route switches get the same
# treatment for the same reason.
os.environ["WOLFKILLER_SESSION_AFFINITY"] = "off"
os.environ["WOLFKILLER_CACHE_ROUTE_PIN"] = "off"
os.environ["WOLFKILLER_SHARED_CACHE_SESSION"] = "off"
os.environ["WOLFKILLER_BURST_WARMUP"] = "off"
