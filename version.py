"""One place for the version number (ISSUE-030).

The app runs from source rather than installed, so
`importlib.metadata.version("codinian")` raises PackageNotFoundError and the
sidebar has nothing to show. `pyproject.toml` reads this attribute back through
`dynamic = ["version"]`, so the package and the window agree and a release means
editing one line.
"""

__version__ = "0.1.0"
