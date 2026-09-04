"use strict";

// Loaded into code-server and every Node child through NODE_OPTIONS.
// It blocks user-triggerable process execution while permitting read-only
// code-server internals and explicitly configured language servers.
const child = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

const CODE_ROOT = real(process.env.REMOTE_DEV_CODE_ROOT || "/opt/code-server");
const EXTRA = new Set(
    (process.env.REMOTE_DEV_ALLOWED_EXECUTABLES || "/usr/bin/clangd-19")
        .split(":").filter(Boolean).map(real),
);
const EVAL_FLAGS = new Set(["-e", "--eval", "-p", "--print"]);
const NODE_LOAD_FLAGS = new Set(["-r", "--require", "--import", "--loader", "--experimental-loader"]);
const ALLOWED_CONNECTS = new Set(
    (process.env.REMOTE_DEV_ALLOWED_CONNECTS || "")
        .split(",").map(value => value.trim()).filter(Boolean),
);

function real(value) {
    try { return fs.realpathSync(value); } catch { return path.resolve(value); }
}
function inside(candidate, root) {
    return candidate === root || candidate.startsWith(root + path.sep);
}
function denied(file, args) {
    const error = new Error(`Remote Dev policy denied process execution: ${file} ${(args || []).join(" ")}`);
    error.code = "EACCES";
    return error;
}
function deniedConnect(options) {
    const target = options.path || `${options.host || "localhost"}:${options.port}`;
    const error = new Error(`Remote Dev policy denied network connection: ${target}`);
    error.code = "EACCES";
    return error;
}
function unsafeNodeArg(arg) {
    const value = String(arg);
    return EVAL_FLAGS.has(value)
        || NODE_LOAD_FLAGS.has(value)
        || [...NODE_LOAD_FLAGS].some(flag => value.startsWith(`${flag}=`))
        || /^--(?:extensionDevelopment|extension-development|extensionTests|extension-tests)/.test(value);
}
function allowed(file, args = []) {
    if (typeof file !== "string" || !path.isAbsolute(file)) return false;
    const executable = real(file);
    if (EXTRA.has(executable)) return true;
    if (executable !== real(process.execPath) || args.some(unsafeNodeArg)) return false;
    return args.some(arg => {
        if (typeof arg !== "string" || arg.startsWith("-")) return false;
        return inside(real(arg), CODE_ROOT);
    });
}
function connectOptions(args) {
    const first = args[0];
    if (first && typeof first === "object") return first;
    if (typeof first === "string" && !/^\d+$/.test(first)) return {path: first};
    return {port: first, host: typeof args[1] === "string" ? args[1] : "localhost"};
}
function allowedConnect(options) {
    if (options && typeof options.path === "string") return true;
    if (!options || options.port === undefined) return false;
    const host = options.host || "localhost";
    const endpoint = host.includes(":") ? `[${host}]:${options.port}` : `${host}:${options.port}`;
    return ALLOWED_CONNECTS.has(endpoint);
}
function guardConnect(args) {
    const options = connectOptions(args);
    if (!allowedConnect(options)) throw deniedConnect(options);
}
function guard(file, args) {
    if (!allowed(file, Array.isArray(args) ? args : [])) throw denied(file, args);
}

const original = {
    spawn: child.spawn,
    spawnSync: child.spawnSync,
    execFile: child.execFile,
    execFileSync: child.execFileSync,
    fork: child.fork,
};
const originalNetConnect = net.connect;
const originalNetCreateConnection = net.createConnection;
child.spawn = function(file, args, options) { guard(file, args); return original.spawn.call(this, file, args, options); };
child.spawnSync = function(file, args, options) { guard(file, args); return original.spawnSync.call(this, file, args, options); };
child.execFile = function(file, args, options, callback) { guard(file, args); return original.execFile.call(this, file, args, options, callback); };
child.execFileSync = function(file, args, options) { guard(file, args); return original.execFileSync.call(this, file, args, options); };
child.fork = function(modulePath, args, options) {
    const resolved = real(modulePath);
    if (!inside(resolved, CODE_ROOT)) throw denied(process.execPath, [modulePath, ...(args || [])]);
    return original.fork.call(this, modulePath, args, options);
};
child.exec = function(command) { throw denied("shell", [command]); };
child.execSync = function(command) { throw denied("shell", [command]); };
net.connect = function(...args) { guardConnect(args); return originalNetConnect.apply(this, args); };
net.createConnection = function(...args) { guardConnect(args); return originalNetCreateConnection.apply(this, args); };

module.exports = {allowed, allowedConnect};
