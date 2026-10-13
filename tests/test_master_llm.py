"""The LLM explainer: provider selection, what is sent, error reporting and the
key handling - all with a fake transport, no network."""

import json

import pytest

from master.llm import (ANTHROPIC_URL, AnthropicProvider, LlmError, OllamaProvider, build_provider, explain,
                        summarize_for_model)

INSIGHTS = {"window_s": 3600, "recent_s": 900, "recent_tiles": 30, "baseline_tiles": 90, "enough_data": True,
            "anomalies": [{"id": "reject_rate", "kind": "reject_rate", "severity": "warn", "title": "Reject rate has risen",
                           "summary": "The reject rate is 40 % ...", "evidence": {"recent_rate": 0.4, "z": 3.1}}]}
FINDINGS = [{"station": "camera", "level": "fail", "title": "The camera stopped sending heartbeats",
             "detail": "silent for 9 s", "meaning": "x", "checks": ["a", "b"], "since": 123.0}]


class FakeHttp:
    def __init__(self, reply=None, exc=None):
        self.reply, self.exc, self.calls = reply, exc, []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body, "timeout": timeout})
        if self.exc:
            raise self.exc
        return self.reply


def test_provider_selection_and_none_default():
    assert build_provider(None) is None and build_provider({"provider": "none"}) is None
    o = build_provider({"provider": "ollama", "ollama": {"model": "qwen2.5:3b"}, "timeout_s": 30})
    assert isinstance(o, OllamaProvider) and o.model == "qwen2.5:3b" and o.timeout_s == 30 and not o.leaves_the_network
    a = build_provider({"provider": "anthropic"})
    assert isinstance(a, AnthropicProvider) and a.leaves_the_network and a.destination == "api.anthropic.com"
    with pytest.raises(ValueError):
        build_provider({"provider": "gpt"})


def test_only_a_whitelisted_summary_is_sent():
    sent = summarize_for_model(INSIGHTS, FINDINGS)
    assert sent["anomalies"][0] == {"title": "Reject rate has risen", "severity": "warn",
                                    "summary": "The reject rate is 40 % ...", "numbers": {"recent_rate": 0.4, "z": 3.1}}
    assert sent["current_findings"] == [{"station": "camera", "level": "fail",
                                         "title": "The camera stopped sending heartbeats", "reported": "silent for 9 s"}]
    text = json.dumps(sent)
    assert "123.0" not in text and "checks" not in text and "tile_id" not in text


def test_ollama_request_shape_and_reply():
    http = FakeHttp({"message": {"content": "  The camera likely lost its feed.  "}})
    p = OllamaProvider(url="http://localhost:11434/", model="m", http=http)
    r = explain(p, INSIGHTS, FINDINGS, clock=lambda: 5.0)
    c = http.calls[0]
    assert c["url"] == "http://localhost:11434/api/chat" and c["body"]["stream"] is False
    assert c["body"]["messages"][0]["role"] == "system" and "only" in c["body"]["messages"][0]["content"].lower()
    assert "Reject rate has risen" in c["body"]["messages"][1]["content"]
    assert r["text"] == "The camera likely lost its feed." and r["provider"] == "ollama" and r["ts"] == 5.0
    assert r["sent"]["anomalies"]


def test_anthropic_request_shape_and_key_handling():
    http = FakeHttp({"content": [{"type": "text", "text": "Hello "}, {"type": "text", "text": "there"}]})
    p = AnthropicProvider(model="claude-sonnet-5-5", http=http, environ={"ANTHROPIC_API_KEY": "sk-test"})
    assert explain(p, INSIGHTS, [])["text"] == "Hello there"
    c = http.calls[0]
    assert c["url"] == ANTHROPIC_URL and c["headers"]["x-api-key"] == "sk-test"
    assert c["headers"]["anthropic-version"] and c["body"]["model"] == "claude-sonnet-5-5" and c["body"]["max_tokens"]
    # the key must not appear in anything that is returned or shown
    assert "sk-test" not in json.dumps(explain(p, INSIGHTS, []))


def test_missing_key_is_a_clear_error_and_nothing_is_sent():
    http = FakeHttp({})
    p = AnthropicProvider(http=http, environ={})
    with pytest.raises(LlmError, match="ANTHROPIC_API_KEY"):
        explain(p, INSIGHTS, [])
    assert http.calls == [] and p.check()["ok"] is False


def test_nothing_flagged_means_no_call_at_all():
    http = FakeHttp({"message": {"content": "x"}})
    r = explain(OllamaProvider(http=http), {"anomalies": []}, [])
    assert r["skipped"] is True and http.calls == []


def test_transport_failures_surface_as_llm_errors():
    p = OllamaProvider(http=FakeHttp(exc=LlmError("cannot reach localhost:11434: refused")))
    with pytest.raises(LlmError, match="cannot reach"):
        explain(p, INSIGHTS, [])
    empty = OllamaProvider(http=FakeHttp({"message": {"content": "  "}}))
    with pytest.raises(LlmError, match="empty"):
        explain(empty, INSIGHTS, [])
    blank = AnthropicProvider(http=FakeHttp({"content": []}), environ={"ANTHROPIC_API_KEY": "k"})
    with pytest.raises(LlmError, match="empty"):
        explain(blank, INSIGHTS, [])


def test_ollama_check_reports_what_to_do():
    none = OllamaProvider(model="llama3.2:3b", http=FakeHttp({"models": []})).check()
    assert none["ok"] is False and "ollama pull llama3.2:3b" in none["detail"]
    other = OllamaProvider(model="llama3.2:3b", http=FakeHttp({"models": [{"name": "qwen2.5:7b"}]})).check()
    assert other["ok"] is False and "qwen2.5:7b" in other["detail"]
    ok = OllamaProvider(model="llama3.2:3b", http=FakeHttp({"models": [{"name": "llama3.2:3b"}]})).check()
    assert ok["ok"] is True
    down = OllamaProvider(http=FakeHttp(exc=LlmError("cannot reach localhost:11434: refused"))).check()
    assert down["ok"] is False and "cannot reach" in down["detail"]
