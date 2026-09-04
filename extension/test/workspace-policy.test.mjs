import test from "node:test";
import assert from "node:assert/strict";

import {isWorkspaceCppFile} from "../dist/workspace-policy.mjs";

const roots = [{scheme: "file", path: "/srv/workspace"}];

test("accepts a cpp file strictly below a workspace root", () => {
    assert.equal(
        isWorkspaceCppFile({scheme: "file", path: "/srv/workspace/src/main.cpp"}, roots),
        true,
    );
});

test("rejects sibling-prefix and parent traversal paths", () => {
    assert.equal(
        isWorkspaceCppFile({scheme: "file", path: "/srv/workspace-other/main.cpp"}, roots),
        false,
    );
    assert.equal(
        isWorkspaceCppFile({scheme: "file", path: "/srv/workspace/../secret.cpp"}, roots),
        false,
    );
});

test("rejects non-file schemes and non-cpp files", () => {
    assert.equal(
        isWorkspaceCppFile({scheme: "untitled", path: "/srv/workspace/main.cpp"}, roots),
        false,
    );
    assert.equal(
        isWorkspaceCppFile({scheme: "file", path: "/srv/workspace/main.hpp"}, roots),
        false,
    );
});
