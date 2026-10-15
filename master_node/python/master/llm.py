"""The explainer: turns the rule-based anomalies and current findings into a
short plain-English write-up using a language model.

Three deliberate limits:
- The model only EXPLAINS what `insights.detect()` / `troubleshoot.diagnose()`
  already found. It never decides what is an anomaly and nothing it says
  changes how the line behaves (the AI-novelty doc keeps auto-remediation out
  of scope).
- Only a small whitelisted summary of those findings is sent - titles, numbers,
  station names, status text. No tile ids, images, or raw rows.
- Nothing is sent unless a person asks (the page's button). The provider is
  `none` by default.

Two providers, one interface: a local Ollama server (works on the isolated
control LAN) and the Claude API (needs internet and a key in an environment
variable - the key is never read from a file or logged). Plain HTTP through
`urllib`, so there is no new dependency; `post`/`get` are injectable so the
whole thing is tested with no network.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

SYSTEM_PROMPT = (
    "You are the diagnostics assistant for a ceramic tile inspection and sorting line: a conveyor, a camera "
    "station that grades tiles for cracks and broken corners, an acoustic station that taps each tile and "
    "listens, and a pick-and-place gantry that sorts tiles into bins. A rule-based monitor has flagged the "
    "items below (statistical anomalies, current station health, and consistency checks of the stored tile "
    "record). Explain them for the line operator.\n"
    "Rules: use ONLY the numbers and facts given; do not invent measurements, causes or events. Give possible "
    "causes as hypotheses, most likely first, and say what a person could look at to tell them apart. Say when "
    "the data is too thin to conclude. Do not tell anyone to change settings or restart anything; suggest what "
    "to inspect. Be brief: at most 150 words, plain text, no headings."
)


class LlmError(Exception):
    """A provider problem a person can act on (not reachable, bad key, no model)."""


# ---- transport (injectable) -------------------------------------------------

def _http(method: str, url: str, headers: dict, body: Optional[dict], timeout: float) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - url comes from our own config
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            detail = {}
        msg = (detail.get("error") or {}) if isinstance(detail, dict) else {}
        msg = msg.get("message") if isinstance(msg, dict) else str(msg)
        raise LlmError(f"HTTP {e.code} from {url.split('/')[2]}: {msg or e.reason}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LlmError(f"cannot reach {url.split('/')[2]}: {getattr(e, 'reason', e)}") from None
    except ValueError:
        raise LlmError(f"unreadable reply from {url.split('/')[2]}") from None


Http = Callable[[str, str, dict, Optional[dict], float], dict]


# ---- providers --------------------------------------------------------------

@dataclass
class OllamaProvider:
    url: str = "http://localhost:11434"
    model: str = "llama3.2:3b"
    timeout_s: float = 90.0
    http: Http = _http
    name: str = "ollama"

    @property
    def destination(self) -> str:
        return self.url

    @property
    def leaves_the_network(self) -> bool:
        return False

    def check(self) -> dict:
        try:
            tags = self.http("GET", self.url.rstrip("/") + "/api/tags", {}, None, 4.0)
        except LlmError as e:
            return {"ok": False, "detail": str(e)}
        names = [m.get("name", "") for m in tags.get("models", []) if isinstance(m, dict)]
        if not names:
            return {"ok": False, "detail": f"Ollama is running but has no models. Run: ollama pull {self.model}"}
        if not any(n == self.model or n.split(":")[0] == self.model.split(":")[0] for n in names):
            return {"ok": False, "detail": f"model '{self.model}' is not installed (have: {', '.join(names)}). "
                                           f"Run: ollama pull {self.model}"}
        return {"ok": True, "detail": f"{self.model} ready"}

    def complete(self, system: str, user: str) -> str:
        r = self.http("POST", self.url.rstrip("/") + "/api/chat", {}, {
            "model": self.model, "stream": False, "options": {"temperature": 0.2},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}, self.timeout_s)
        text = ((r.get("message") or {}).get("content") or "").strip()
        if not text:
            raise LlmError("Ollama returned an empty answer")
        return text


@dataclass
class AnthropicProvider:
    model: str = "claude-sonnet-5-5"
    api_key_env: str = "ANTHROPIC_API_KEY"
    timeout_s: float = 60.0
    max_tokens: int = 400
    http: Http = _http
    name: str = "anthropic"
    environ: Optional[dict] = None

    @property
    def destination(self) -> str:
        return "api.anthropic.com"

    @property
    def leaves_the_network(self) -> bool:
        return True

    def _key(self) -> str:
        key = (self.environ if self.environ is not None else os.environ).get(self.api_key_env, "")
        if not key:
            raise LlmError(f"no API key: set the {self.api_key_env} environment variable before starting the master")
        return key

    def check(self) -> dict:
        try:
            self._key()
        except LlmError as e:
            return {"ok": False, "detail": str(e)}
        return {"ok": True, "detail": f"{self.model}; key present (not verified until used)"}

    def complete(self, system: str, user: str) -> str:
        r = self.http("POST", ANTHROPIC_URL, {"x-api-key": self._key(), "anthropic-version": ANTHROPIC_VERSION}, {
            "model": self.model, "max_tokens": self.max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}]}, self.timeout_s)
        parts = [b.get("text", "") for b in (r.get("content") or []) if isinstance(b, dict) and b.get("type") == "text"]
        text = "".join(parts).strip()
        if not text:
            raise LlmError("the model returned an empty answer")
        return text


def build_provider(cfg: Optional[dict], http: Http = _http):
    """The provider named in config's `insights:` block, or None when it is `none`/absent."""
    cfg = cfg or {}
    name = str(cfg.get("provider") or "none").lower()
    timeout = float(cfg.get("timeout_s", 60))
    if name == "none":
        return None
    if name == "ollama":
        o = cfg.get("ollama") or {}
        return OllamaProvider(url=o.get("url", "http://localhost:11434"), model=o.get("model", "llama3.2:3b"),
                              timeout_s=timeout, http=http)
    if name == "anthropic":
        a = cfg.get("anthropic") or {}
        return AnthropicProvider(model=a.get("model", "claude-sonnet-5-5"),
                                 api_key_env=a.get("api_key_env", "ANTHROPIC_API_KEY"), timeout_s=timeout, http=http)
    raise ValueError(f"insights.provider must be none, ollama or anthropic (got {name!r})")


# ---- what is sent, and the explanation --------------------------------------

# Evidence keys that carry tile ids / run ids / free text and so never leave the master.
_PRIVATE_EVIDENCE = {"examples", "runs", "error", "tile_ids", "detail", "problems"}


def _numbers(evidence: dict) -> dict:
    return {k: v for k, v in (evidence or {}).items() if k not in _PRIVATE_EVIDENCE}


def summarize_for_model(insights: dict, findings: list[dict], integrity: Optional[list[dict]] = None) -> dict:
    """The only data that leaves this module: whitelisted, no tile ids or rows."""
    out = {
        "window_minutes": round(insights.get("window_s", 0) / 60),
        "recent_minutes": round(insights.get("recent_s", 0) / 60),
        "tiles_recent": insights.get("recent_tiles"),
        "tiles_baseline": insights.get("baseline_tiles"),
        "enough_data": insights.get("enough_data"),
        "anomalies": [{"title": a["title"], "severity": a["severity"], "summary": a["summary"],
                       "numbers": a.get("evidence", {})} for a in insights.get("anomalies", [])],
        "current_findings": [{"station": f.get("station"), "level": f.get("level"), "title": f.get("title"),
                              "reported": f.get("detail")} for f in findings],
    }
    if integrity:
        out["record_checks"] = [{"title": f["title"], "severity": f["severity"], "summary": f["summary"],
                                 "numbers": _numbers(f.get("evidence"))} for f in integrity]
    return out


def explain(provider, insights: dict, findings: list[dict], clock: Callable[[], float] = time.time,
            integrity: Optional[list[dict]] = None) -> dict:
    """Ask the provider to explain. Raises LlmError; the caller shows it."""
    payload = summarize_for_model(insights, findings, integrity)
    if not payload["anomalies"] and not payload["current_findings"] and not payload.get("record_checks"):
        return {"text": "Nothing has been flagged, so there is nothing to explain.", "provider": provider.name,
                "model": getattr(provider, "model", None), "sent": payload, "ms": 0, "ts": clock(), "skipped": True}
    t0 = time.monotonic()
    text = provider.complete(SYSTEM_PROMPT, "Flagged items (JSON):\n" + json.dumps(payload, indent=1))
    return {"text": text, "provider": provider.name, "model": getattr(provider, "model", None), "sent": payload,
            "ms": round((time.monotonic() - t0) * 1000), "ts": clock(), "skipped": False}
