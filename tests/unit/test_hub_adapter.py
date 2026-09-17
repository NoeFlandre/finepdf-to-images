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

    #: Deliberately ``Any``: the real Hub returns different entry types for LFS objects and
    #: ordinary files, and the adapter reads them duck-typed. Pinning one shape here would test a
    #: world the adapter never sees.
    entries: list[Any] = field(default_factory=list)
    sha: str = "deadbeef"
    raises: Exception | None = None
    calls: list[str] = field(default_factory=list)
    committed: list[Any] = field(default_factory=list)

    def list_repo_tree(self, repo: str, **kwargs: Any) -> list[Any]:
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


# --------------------------------------------------------------------------- content identity


def test_an_ordinary_file_is_identified_by_its_git_blob_id() -> None:
    """REGRESSION: only LFS entries were reported, and none of the four published files is LFS.

    Against the real Hub that meant every verification failed and no re-run was ever a no-op.
    """

    @dataclass
    class BlobEntry:
        path: str
        blob_id: str

    api = FakeApi(entries=[BlobEntry("README.md", "a" * 40)])
    assert hub(api).file_digests("a/b") == {"README.md": "a" * 40}


def test_an_lfs_object_still_reports_its_content_hash() -> None:
    api = FakeApi(entries=[Entry("big.bin", Lfs("c" * 64))])
    assert hub(api).file_digests("a/b") == {"big.bin": "c" * 64}


def test_an_lfs_hash_is_preferred_over_the_blob_id() -> None:
    """For an LFS pointer the blob id hashes the pointer file, not the content."""

    @dataclass
    class BothEntry:
        path: str
        blob_id: str
        lfs: Lfs

    api = FakeApi(entries=[BothEntry("big.bin", "b" * 40, Lfs("c" * 64))])
    assert hub(api).file_digests("a/b") == {"big.bin": "c" * 64}


def test_an_entry_with_neither_identity_is_omitted() -> None:
    """Omitted counts as not matching, so a publication re-uploads rather than skipping."""

    @dataclass
    class Bare:
        path: str

    assert hub(FakeApi(entries=[Bare("x")])).file_digests("a/b") == {}


# --------------------------------------------------------------------------- deletion


def test_upload_emits_a_delete_operation_for_each_stale_path() -> None:
    api = FakeApi()
    hub(api).upload(PLAN, "m", ["data/old.jsonl", "images/ab/cd/x.png"])
    operations = api.committed[0]["operations"]

    assert len(operations) == 4, "two writes and two deletes, in one commit"
    deletes = [op for op in operations if type(op).__name__ == "CommitOperationDelete"]
    assert [op.path_in_repo for op in deletes] == ["data/old.jsonl", "images/ab/cd/x.png"]


def test_upload_without_deletions_sends_only_writes() -> None:
    api = FakeApi()
    hub(api).upload(PLAN, "m")
    assert all(type(op).__name__ == "CommitOperationAdd" for op in api.committed[0]["operations"])


def test_deletions_ride_in_the_same_commit_as_the_writes() -> None:
    api = FakeApi()
    hub(api).upload(PLAN, "m", ["data/old.jsonl"])
    assert len(api.committed) == 1, "a half-migrated published state must not be possible"
