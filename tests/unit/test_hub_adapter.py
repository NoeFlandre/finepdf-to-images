"""The real Hub adapter, driven against a fake ``HfApi``.

These three methods are the only code in the project that can change a public dataset. Leaving
them untested because "they just call the library" is how the most consequential path ends up the
least covered — which is exactly what the CRAP gate reported before this file existed.

Nothing here constructs a real ``HfApi``, so no test can reach the network or a credential.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from finepdf_to_images.adapters.hub import HubError, HuggingFaceHub
from finepdf_to_images.domain.publication import PublicationPlan, PublishFile

PLAN = PublicationPlan(
    repo="a/b",
    files=(PublishFile("README.md", b"# card"), PublishFile("manifest.json", b"{}")),
    manifest={"counts": {}},
)


@dataclass
class Lfs:
    sha256: str


@dataclass
class Entry:
    path: str
    lfs: Lfs | None = None


@dataclass
class Commit:
    oid: str = "c0ffee"


@dataclass
class FakeApi:
    """Stands in for ``HfApi``. Records what it was asked to do."""

    entries: list[Entry] = field(default_factory=list)
    sha: str = "deadbeef"
    raises: Exception | None = None
    calls: list[str] = field(default_factory=list)
    committed: list[Any] = field(default_factory=list)

    def list_repo_tree(self, repo: str, **kwargs: Any) -> list[Entry]:
        self.calls.append(f"list:{repo}:{kwargs.get('repo_type')}:{kwargs.get('recursive')}")
        if self.raises:
            raise self.raises
        return self.entries

    def dataset_info(self, repo: str) -> Any:
        self.calls.append(f"info:{repo}")
        if self.raises:
            raise self.raises
        return type("Info", (), {"sha": self.sha})()

    def create_commit(self, **kwargs: Any) -> Commit:
        self.calls.append(f"commit:{kwargs['repo_id']}:{kwargs['repo_type']}")
        if self.raises:
            raise self.raises
        self.committed.append(kwargs)
        return Commit()


def hub(api: FakeApi) -> HuggingFaceHub:
    return HuggingFaceHub(api=api)


# --------------------------------------------------------------------------- reading


def test_file_digests_returns_content_hashes_for_lfs_entries() -> None:
    api = FakeApi(entries=[Entry("data/a.jsonl", Lfs("a" * 64)), Entry("README.md", Lfs("b" * 64))])
    assert hub(api).file_digests("a/b") == {"data/a.jsonl": "a" * 64, "README.md": "b" * 64}


def test_an_entry_without_a_content_hash_is_omitted_rather_than_guessed() -> None:
    """The Hub records a git blob sha for small files, which is not a content SHA-256.

    Omitting it means such a file never compares equal, so a publication re-uploads rather than
    silently skipping something that may have changed. Erring toward re-uploading is the safe
    direction for a gate that decides whether a public dataset is already correct.
    """
    api = FakeApi(entries=[Entry("small.txt", None), Entry("big.bin", Lfs("c" * 64))])
    assert hub(api).file_digests("a/b") == {"big.bin": "c" * 64}


def test_reading_asks_for_the_whole_dataset_tree() -> None:
    api = FakeApi()
    hub(api).file_digests("a/b")
    assert api.calls == ["list:a/b:dataset:True"]


def test_revision_returns_the_repository_head() -> None:
    assert hub(FakeApi(sha="1234")).revision("a/b") == "1234"


@pytest.mark.parametrize("method", ["file_digests", "revision"])
def test_a_read_failure_becomes_a_hub_error_not_a_traceback(method: str) -> None:
    api = FakeApi(raises=RuntimeError("network is down"))
    with pytest.raises(HubError, match="could not read a/b"):
        getattr(hub(api), method)("a/b")


# --------------------------------------------------------------------------- writing


def test_upload_writes_every_file_in_one_commit() -> None:
    api = FakeApi()
    hub(api).upload(PLAN, "a message")
    assert len(api.committed) == 1, "a half-updated published state must not be possible"
    commit = api.committed[0]
    assert commit["repo_id"] == "a/b"
    assert commit["repo_type"] == "dataset"
    assert commit["commit_message"] == "a message"
    assert len(commit["operations"]) == 2


def test_upload_sends_the_planned_bytes_at_the_planned_paths() -> None:
    api = FakeApi()
    hub(api).upload(PLAN, "m")
    operations = api.committed[0]["operations"]
    assert [op.path_in_repo for op in operations] == ["README.md", "manifest.json"]


def test_upload_returns_the_commit_sha() -> None:
    assert hub(FakeApi()).upload(PLAN, "m") == "c0ffee"


def test_upload_falls_back_to_the_head_when_the_commit_has_no_oid() -> None:
    class NoOid(FakeApi):
        def create_commit(self, **kwargs: Any) -> Any:
            super().create_commit(**kwargs)
            return type("C", (), {"oid": ""})()

    assert hub(NoOid(sha="fallback")).upload(PLAN, "m") == "fallback"


def test_a_write_failure_becomes_a_hub_error_naming_the_repo() -> None:
    api = FakeApi(raises=RuntimeError("403 forbidden"))
    with pytest.raises(HubError, match="could not publish to a/b"):
        hub(api).upload(PLAN, "m")


def test_the_adapter_never_reads_or_passes_a_token() -> None:
    """Authentication is huggingface_hub's business, so no credential passes through this code."""
    import inspect

    from finepdf_to_images.adapters import hub as module

    source = inspect.getsource(module)
    for secret in ("token=", "HF_TOKEN", "use_auth_token", "api_key"):
        assert secret not in source, f"{secret} must not appear in the Hub adapter"
