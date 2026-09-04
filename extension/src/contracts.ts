export const ACTIVE_STATES = new Set(["queued", "compiling", "running"]);

export type CaseResult = {
    position: number;
    status: string;
    stdout: string;
    stderr: string;
    wall_time_ms: number | null;
    peak_memory_kib: number | null;
    exit_code: number | null;
    term_signal: number | null;
    output_truncated: number | boolean;
};

export type JobResult = {
    id: number;
    status: string;
    filename: string;
    compiler: string;
    cases: CaseResult[];
};

export function validateLimits(timeMs: number, memoryMiB: number, caseCount: number): string | null {
    if (!Number.isFinite(timeMs) || timeMs < 100 || timeMs > 60000) {
        return "时间限制必须在 0.1–60 秒之间";
    }
    if (!Number.isInteger(memoryMiB) || memoryMiB < 16 || memoryMiB > 256) {
        return "内存限制必须在 16–256 MiB 之间";
    }
    if (!Number.isInteger(caseCount) || caseCount < 1 || caseCount > 20) {
        return "输入组数必须在 1–20 之间";
    }
    return null;
}
