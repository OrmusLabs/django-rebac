# justfile

# Enable automatic loading of .env file
set dotenv-load := true

# Shell configurations
set shell := ["bash", "-c"]
set windows-shell := ["pwsh", "-NoLogo", "-Command"]

# Default recipe: show help
default:
    @just --list

# 🤠 1. The Ultimate Install Command
# This reads pyproject.toml, creates the venv, installs the package,
# and installs both the 'dev' and 'docs' dependency groups instantly.
install:
    uv sync --group dev --group docs

# 🤠 2. Run linting and formatting
lint:
    uv run ruff check src/
    uv run ruff format src/
lint-fix:
    uv run ruff check --fix src/
    uv run ruff format src/

# 🤠 3. Serve docs (Safely inside the uv venv!)
docs-serve:
    uv run mkdocs serve --livereload -a 0.0.0.0:8007 -w .\docs\ -w .\mkdocs.yml -w .\src\

# 🤠 4. Build docs
docs-build:
    uv run mkdocs build

# Cross-platform clean (uses Python instead of rm)
docs-clean:
    uv run python -c "import shutil, pathlib; shutil.rmtree('site', ignore_errors=True)"
