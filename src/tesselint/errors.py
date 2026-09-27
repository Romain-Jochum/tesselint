"""Errors that the CLI turns into one `tesselint: error: …` line and an exit code (DESIGN §7)."""


class TesseLintError(Exception):
    """A user-facing failure; `exit_code` is what `tesselint` returns."""

    exit_code = 1


class UsageError(TesseLintError):
    """Bad flags or arguments, e.g. an unknown tier or epic."""

    exit_code = 2


class InputError(TesseLintError):
    """Bad input files: unparsed story lines, a missing source, a directory that is not a run."""

    exit_code = 4
