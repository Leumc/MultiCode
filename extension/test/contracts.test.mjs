import test from "node:test";
import assert from "node:assert/strict";

import {validateLimits} from "../dist/contracts.mjs";

test("accepts system-default limits", () => {
    assert.equal(validateLimits(1000, 64, 2), null);
});

test("rejects values outside immutable system ceilings", () => {
    assert.match(validateLimits(99, 64, 1), /时间限制/);
    assert.match(validateLimits(60001, 64, 1), /时间限制/);
    assert.match(validateLimits(1000, 15, 1), /内存限制/);
    assert.match(validateLimits(1000, 257, 1), /内存限制/);
    assert.match(validateLimits(1000, 64, 0), /输入组数/);
    assert.match(validateLimits(1000, 64, 21), /输入组数/);
});
