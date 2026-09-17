"""Talking to the Hugging Face Hub.

The only module that can change anything on the Hub. Everything about *what* a publication
consists of is decided in :mod:`finepdf_to_images.domain.publication`; this moves the bytes and
reports what is there.

Nothing here reads or prints a token. ``huggingface_hub`` resolves credentials itself from the
environment or the user's stored login, so a token never passes through this project's code and
cannot end up in a log line or a manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from finepdf_to_images.domain.publication import PublicationPlan, PublishFile


class HubError(RuntimeError):
    """The Hub could not be read or written. Carries the library's reason, not a traceback."""


class Hub(Protocol):
    """The Hub operations a publication needs, and no others."""

    def file_digests(self, repo: str) -> dict[str, str]:
        """Path -> a content identity: a git blob id, or a SHA-256 for an LFS object."""
        ...

    def revision(self, repo: str) -> str:
        """The repository's current commit sha."""
        ...

    def upload(self, plan: PublicationPlan, message: str) -> str:
        """Write every planned file in one commit and return its sha."""
        ...


@dataclass(slots=True)
class FakeHub:
    """An in-memory Hub. The only one the tests use.

    Records every call, so a test can assert that a dry run made no write -- which is the property
    that matters most in this stage and the one hardest to check by reading code.
    """

    files: dict[str, bytes] = field(default_factory=dict)
    commits: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    head: str = "0" * 40
    fail_with: Exception | None = None

    def file_digests(self, repo: str) -> dict[str, str]:
        """Reports git blob ids, which is what the real Hub gives for files this size.

        Returning SHA-256 here -- as an earlier version did -- modelled behaviour the real adapter
        never exhibits, and made every idempotency and verification test assert a property that
        could not hold in production.
        """
        self.calls.append(f"file_digests:{repo}")
        self._maybe_fail()
        return {path: PublishFile(path, data).git_blob_sha1 for path, data in self.files.items()}

    def revision(self, repo: str) -> str:
        self.calls.append(f"revision:{repo}")
        self._maybe_fail()
        return self.head

    def upload(self, plan: PublicationPlan, message: str) -> str:
        self.calls.append(f"upload:{plan.repo}")
        self._maybe_fail()
        for file in plan.files:
            self.files[file.path] = file.data
        self.head = f"{len(self.commits) + 1:040d}"
        self.commits.append(message)
        return self.head

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    @property
    def wrote(self) -> bool:
        """Whether anything was ever uploaded. A dry run must leave this false."""
        return any(call.startswith("upload:") for call in self.calls)


@dataclass(frozen=True, slots=True)
class HuggingFaceHub:
    """The real Hub.

    ``huggingface_hub`` handles authentication, so no token is read, stored or logged here.

    ``api`` substitutes the ``HfApi`` instance, so these three methods -- the only code in the
    project that can change a public dataset -- are exercised by tests instead of being the least
    covered thing in it.
    """

    api: Any = None

    def _api(self) -> Any:
        if self.api is not None:
            return self.api
        from huggingface_hub import HfApi

        return HfApi()

    def file_digests(self, repo: str) -> dict[str, str]:
        """Path -> a content identity for each file in the repository.

        The Hub records a git blob id for an ordinary file and a content SHA-256 only for an LFS
        object, so this reports whichever it has and the domain matches against both. Reporting
        only the LFS hashes -- as an earlier version did -- meant none of the four small files this
        stage publishes could ever match, so verification failed on every successful publication
        and a re-run was never recognised as a no-op.

        A file with neither identity is omitted, and an omitted file counts as not matching: that
        errs toward re-uploading rather than toward silently skipping something that changed.
        """
        try:
            entries = self._api().list_repo_tree(repo, repo_type="dataset", recursive=True)
        except Exception as error:
            raise HubError(f"could not read {repo}: {type(error).__name__}: {error}") from error
        return {
            entry.path: digest for entry in entries if (digest := _identity_of(entry)) is not None
        }

    def revision(self, repo: str) -> str:
        try:
            return str(self._api().dataset_info(repo).sha)
        except Exception as error:
            raise HubError(f"could not read {repo}: {type(error).__name__}: {error}") from error

    def upload(self, plan: PublicationPlan, message: str) -> str:
        """Write every file in **one** commit, so the published state is never half-updated."""
        from huggingface_hub import CommitOperationAdd

        operations = [
            CommitOperationAdd(path_in_repo=file.path, path_or_fileobj=file.data)
            for file in plan.files
        ]
        try:
            commit = self._api().create_commit(
                repo_id=plan.repo,
                repo_type="dataset",
                operations=operations,
                commit_message=message,
            )
        except Exception as error:
            raise HubError(
                f"could not publish to {plan.repo}: {type(error).__name__}: {error}"
            ) from error
        return str(getattr(commit, "oid", "") or self.revision(plan.repo))


def _identity_of(entry: Any) -> str | None:
    """An LFS object's content SHA-256, else the git blob id, else nothing."""
    lfs = getattr(entry, "lfs", None)
    if lfs is not None and getattr(lfs, "sha256", None):
        return str(lfs.sha256)
    blob_id = getattr(entry, "blob_id", None)
    return str(blob_id) if blob_id else None
