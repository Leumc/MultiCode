import assert from "node:assert/strict";
import child from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const project = path.resolve(import.meta.dirname, "..");
const hook = path.join(project, "deploy/code-server/lockdown-spawn.cjs");
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "rdp-spawn-"));
const internal = path.join(temporary, "internal.js");
const outside = path.join(temporary, "outside.js");
fs.writeFileSync(internal, "process.stdout.write('INTERNAL_OK')");
fs.writeFileSync(outside, "process.stdout.write('OUTSIDE_BAD')");
process.env.REMOTE_DEV_CODE_ROOT = temporary;
process.env.REMOTE_DEV_ALLOWED_EXECUTABLES = "";
process.env.REMOTE_DEV_ALLOWED_CONNECTS = "127.0.0.1:9000,[::1]:9000";
const policy = await import(hook);

test("allows node only for a script under immutable code root", () => {
    assert.equal(child.execFileSync(process.execPath, [internal], {encoding: "utf8"}), "INTERNAL_OK");
});

test("denies node eval and user scripts", () => {
    assert.throws(() => child.execFileSync(process.execPath, ["-e", "0"]), /policy denied/);
    process.env.REMOTE_DEV_CODE_ROOT = temporary;
    const elsewhere = path.join(os.tmpdir(), "rdp-outside.js");
    fs.copyFileSync(outside, elsewhere);
    assert.throws(() => child.execFileSync(process.execPath, [elsewhere]), /policy denied/);
    fs.unlinkSync(elsewhere);
});

test("denies shell and arbitrary binaries", () => {
    assert.throws(() => child.exec("id"), /policy denied/);
    assert.throws(() => child.spawn("/bin/sh", ["-c", "id"]), /policy denied/);
    assert.throws(() => child.execFileSync("/usr/bin/g++", ["--version"]), /policy denied/);
});

test("denies node loader and extension development host bypasses", () => {
    assert.throws(
        () => child.spawnSync(process.execPath, ["--require", outside, internal]),
        /policy denied/,
    );
    assert.throws(
        () => child.spawnSync(process.execPath, [internal, `--extensionDevelopmentPath=${temporary}`]),
        /policy denied/,
    );
});

test("allows only configured TCP destinations while preserving Unix sockets", () => {
    assert.equal(policy.allowedConnect({host: "127.0.0.1", port: 9000}), true);
    assert.equal(policy.allowedConnect({host: "::1", port: 9000}), true);
    assert.equal(policy.allowedConnect({host: "localhost", port: 9000}), false);
    assert.equal(policy.allowedConnect({host: "127.0.0.1", port: 9001}), false);
    assert.equal(policy.allowedConnect({path: path.join(temporary, "internal.sock")}), true);
    assert.throws(
        () => net.connect({host: "127.0.0.1", port: 9001}),
        /policy denied network connection/,
    );
});

test.after(() => fs.rmSync(temporary, {recursive: true, force: true}));
