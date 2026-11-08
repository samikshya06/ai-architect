import os
import runpy
from pathlib import Path

import dotenv
import langchain_openai
import pytest

from llm import factory


@pytest.mark.parametrize("existing_key", [None, "environment-key"])
def test_factory_loads_env_once_at_import(monkeypatch, tmp_path, existing_key):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=file-key\n", encoding="utf-8")
    if existing_key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", existing_key)

    calls = []
    load_dotenv = dotenv.load_dotenv

    def load_test_env(path):
        calls.append(path)
        return load_dotenv(env_file)

    monkeypatch.setattr(dotenv, "load_dotenv", load_test_env)
    monkeypatch.setattr(
        langchain_openai, "OpenAIEmbeddings", lambda **kwargs: os.getenv("OPENAI_API_KEY")
    )
    monkeypatch.setattr(
        langchain_openai, "ChatOpenAI", lambda **kwargs: os.getenv("OPENAI_API_KEY")
    )
    for key in ("embeddings", "small_llm", "llm"):
        monkeypatch.setitem(factory.config, key, {"provider": "openai", "model": "test"})

    loaded = runpy.run_path(factory.__file__)
    expected = existing_key or "file-key"
    assert loaded["get_embedder"]() == expected
    assert loaded["get_small_llm"]() == expected
    assert loaded["get_llm"]() == expected
    assert calls == [Path(factory.__file__).resolve().parent.parent / ".env"]
