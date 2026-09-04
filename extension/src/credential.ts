import {readFileSync} from "node:fs";
import {join} from "node:path";

export function readApiToken(environment: NodeJS.ProcessEnv = process.env): string {
    const directory = environment.CREDENTIALS_DIRECTORY;
    if (!directory) {
        throw new Error("systemd credential directory is unavailable");
    }
    let token: string;
    try {
        token = readFileSync(join(directory, "remote-dev-api-token"), "utf8").trim();
    } catch {
        throw new Error("systemd API token credential is unavailable");
    }
    if (!token) {
        throw new Error("systemd API token credential is empty");
    }
    return token;
}