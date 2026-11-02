"""Keep every pytest run away from the user's live WolfKiller data directory."""

from __future__ import annotations

import os
import tempfile


os.environ["WOLFKILLER_DATA_DIR"] = tempfile.mkdtemp(prefix="wolfkiller-pytest-")
os.environ["MODEL_CONFIG_PATH"] = os.path.join(os.environ["WOLFKILLER_DATA_DIR"], "models.json")
os.environ["DEEPSEEK_API_KEY"] = ""
# Never spawn the provider-SDK/latency warmup thread from app.main in tests.
os.environ["WOLFKILLER_PROVIDER_PREWARM"] = "0"
