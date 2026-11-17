"""Pick the LLM that drives the walk. One small adapter per provider; the agent loop never imports a vendor SDK.

An adapter has one method, chat(system, messages, tools), and works in one neutral format:

  messages  [{"role": "user", "content": "..."},
             {"role": "assistant", "content": "...", "tool_calls": [ToolCall, ...]},
             {"role": "tool", "tool_call_id": "...", "name": "...", "content": "..."}]
  tools     [{"name": "...", "description": "...", "parameters": <JSON schema>}]
  returns   Reply(text, tool_calls)

Built in:
  anthropic:<model>   Claude, through the Anthropic API (needs ANTHROPIC_API_KEY)
  openai:<model>      any OpenAI-compatible API (OPENAI_API_KEY, or --base-url for another server). gpt-6 models
                      run with reasoning off, which Chat Completions needs for tools; set OPENAI_REASONING_EFFORT to change it
  ollama:<model>      a local Ollama model; the same adapter as openai, pointed at http://localhost:11434/v1

Add your own provider: subclass LLM, implement chat, and register it:

    @register("myprovider")
    class MyLLM(LLM):
        def __init__(self, model, base_url=None): ...
        def chat(self, system, messages, tools): ...   # return Reply(text, [ToolCall(id, name, arguments), ...])

then run with --llm myprovider:some-model. Import only the vendor SDK you use, inside __init__.
"""

import json
import os

from dataclasses import dataclass, field

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)

class LLM:
    label = ""   # provider:model, stored in the ledger so walks by different models can be compared

    def chat(self, system, messages, tools):
        raise NotImplementedError

PROVIDERS = {}

def register(name):
    def add(cls):
        PROVIDERS[name] = cls
        return cls
    return add

def make_llm(spec, base_url=None):
    """spec is 'provider:model', for example 'anthropic:claude-sonnet-5-5' or 'ollama:bonsai-2:9b'."""
    provider, _, model = spec.partition(":")
    if provider not in PROVIDERS or not model:
        raise ValueError(f"Use --llm provider:model, with a provider from: {', '.join(PROVIDERS)}.")
    llm = PROVIDERS[provider](model, base_url=base_url)
    llm.label = spec
    return llm

@register("anthropic")
class AnthropicLLM(LLM):
    def __init__(self, model, base_url=None):
        import anthropic
        self.model = model
        self.client = anthropic.Anthropic(base_url=base_url) if base_url else anthropic.Anthropic()

    def chat(self, system, messages, tools):
        response = self.client.messages.create(
            model=self.model, max_tokens=1024, system=system,
            messages=self._messages(messages),
            tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools])
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"]
        return Reply(text, calls)

    @staticmethod
    def _messages(messages):
        """Anthropic wants tool results as blocks in a user message, and all results for one turn together."""
        out = []
        for m in messages:
            if m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif m["role"] == "assistant":
                blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
                blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                           for c in m.get("tool_calls", [])]
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

@register("openai")
class OpenAICompatibleLLM(LLM):
    """Any server that speaks the OpenAI chat API with tool calling: OpenAI, LM Studio, vLLM, Ollama and others."""

    def __init__(self, model, base_url=None, api_key=None):
        import openai
        self.model = model
        kwargs = {}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        self.client = openai.OpenAI(**kwargs)
        # OpenAI's gpt-6 models only take tools on Chat Completions with reasoning turned off, so that is the default
        # for them. Set OPENAI_REASONING_EFFORT (none, low, medium, ...) to override it for any OpenAI model.
        explicit = os.environ.get("OPENAI_REASONING_EFFORT")
        self.reasoning_effort = explicit or (None if base_url else ("none" if model.startswith("gpt-6") else None))

    def chat(self, system, messages, tools):
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}] + self._messages(messages),
            tools=[{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                     "parameters": t["parameters"]}} for t in tools],
            **extra)
        message = response.choices[0].message
        calls = [ToolCall(c.id or f"call_{i}", c.function.name, json.loads(c.function.arguments or "{}"))
                 for i, c in enumerate(message.tool_calls or [])]
        return Reply(message.content or "", calls)

    @staticmethod
    def _messages(messages):
        out = []
        for m in messages:
            if m["role"] == "assistant":
                entry = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    entry["tool_calls"] = [{"id": c.id, "type": "function",
                                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                                           for c in m["tool_calls"]]
                out.append(entry)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

@register("ollama")
class OllamaLLM(OpenAICompatibleLLM):
    def __init__(self, model, base_url=None):
        super().__init__(model, base_url=base_url or "http://localhost:11434/v1", api_key="ollama")
        self.reasoning_effort = None   # Ollama doesn't take this setting
