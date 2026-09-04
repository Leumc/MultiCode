"""Shared constants and immutable system ceilings."""

MAX_SOURCE_BYTES = 1_048_576
MAX_INPUT_BYTES = 1_048_576
MAX_CASES = 20
MAX_DEVELOPER_ACCOUNTS = 3
MAX_USER_INFLIGHT = 5
MAX_USER_RUNNING_MEMORY_MIB = 256
MAX_TIME_MS = 60_000
MIN_TIME_MS = 100
MIN_MEMORY_MIB = 16
MAX_SUBMISSION_WALL_MS = 180_000
STDOUT_LIMIT_BYTES = 1_048_576
STDERR_LIMIT_BYTES = 524_288
COMPILE_MEMORY_MIB = 512
COMPILE_WALL_MS = 30_000
DEFAULT_GLOBAL_COMPILE_SLOTS = 1
DEFAULT_GLOBAL_RUN_SLOTS = 2
DEFAULT_WORKSPACE_QUOTA_MIB = 1024
DEFAULT_ARTIFACT_RETENTION_DAYS = 7
DEFAULT_METADATA_RETENTION_DAYS = 30

JOB_STATES = {
    "queued", "compiling", "running", "completed", "compile_error",
    "runtime_error", "time_limit", "memory_limit", "output_limit",
    "cancelled", "system_error",
}
TERMINAL_STATES = {
    "completed", "compile_error", "runtime_error", "time_limit",
    "memory_limit", "output_limit", "cancelled", "system_error",
}
