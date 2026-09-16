# Reproducible image for the bounded FinePDF proof of concept.
# Contains no credentials and no machine-specific path; the Hugging Face token, when a run
# needs one, is supplied at runtime via the HF_TOKEN environment variable.
# Pinned by digest, not by tag: a moving tag would quietly change the image between builds.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

# Dependency layer first so source edits do not invalidate the resolved environment.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project --no-dev

COPY src ./src
RUN uv sync --locked --no-dev

ENTRYPOINT ["finepdf-to-images"]
CMD ["--help"]
