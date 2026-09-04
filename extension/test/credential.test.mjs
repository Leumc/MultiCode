import assert from "node:assert/strict";
import {mkdtempSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import {readApiToken} from "../dist/credential.mjs";

test("reads and trims the systemd credential file", () => {
  const directory = mkdtempSync(join(tmpdir(), "remote-dev-credential-"));
  writeFileSync(join(directory, "remote-dev-api-token"), "rdp_secret\n", {mode: 0o600});
  assert.equal(readApiToken({CREDENTIALS_DIRECTORY: directory}), "rdp_secret");
});

test("fails closed for a missing or empty credential", () => {
  assert.throws(() => readApiToken({}), /credential/i);
  const directory = mkdtempSync(join(tmpdir(), "remote-dev-credential-"));
  writeFileSync(join(directory, "remote-dev-api-token"), "\n", {mode: 0o600});
  assert.throws(() => readApiToken({CREDENTIALS_DIRECTORY: directory}), /credential/i);
});