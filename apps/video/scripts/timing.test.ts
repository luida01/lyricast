import assert from "node:assert/strict";
import {
  findActiveLine,
  getScrollIndex,
  normalizeLineTimings,
} from "../src/timing";

const TRANSITION = 0.45;

function contiguousLines(count: number, span = 3): { start: number; end: number }[] {
  return Array.from({ length: count }, (_value, index) => ({
    start: index * span,
    end: (index + 1) * span,
  }));
}

function assertInRange(value: number, min: number, max: number): void {
  assert.ok(value >= min && value <= max, `expected ${value} in [${min}, ${max}]`);
}

// --- normalizeLineTimings ---

assert.deepEqual(
  normalizeLineTimings([
    { start: 10, end: 12 },
    { start: 11, end: 13 },
    { start: null, end: null },
  ]),
  [
    { start: 10, end: 12 },
    { start: 12, end: 13 },
    { start: 13, end: 13.01 },
  ],
);

// --- findActiveLine ---

const lines = contiguousLines(6);
assert.equal(findActiveLine(lines, 0), 0);
assert.equal(findActiveLine(lines, 2.9), 0);
assert.equal(findActiveLine(lines, 3), 1);
assert.equal(findActiveLine(lines, 17), 5);

const gapped = [{ start: 5, end: 8 }, { start: 10, end: 13 }];
assert.equal(findActiveLine(gapped, 0), -1);
assert.equal(findActiveLine(gapped, 9), -1);
assert.equal(findActiveLine(gapped, 11), 1);

// --- getScrollIndex: no jumps of more than one line ---

for (let time = 0; time <= 18; time += 0.1) {
  const value = getScrollIndex(lines, time, TRANSITION);
  assertInRange(value, 0, 5);
}
const previousValues = Array.from({ length: 180 }, (_v, i) =>
  getScrollIndex(lines, i / 10, TRANSITION),
);
for (let index = 1; index < previousValues.length; index += 1) {
  const jump = previousValues[index] - previousValues[index - 1];
  assert.ok(jump >= 0, `scroll went backwards at step ${index}`);
  assert.ok(jump <= 1.0001, `scroll jumped ${jump} lines at step ${index}`);
}

// --- regression: at a later line the global index must be exact ---

const manyLines = contiguousLines(80);
assert.equal(getScrollIndex(manyLines, 31, TRANSITION), 10); // line 10 centered
assert.equal(getScrollIndex(manyLines, 241, TRANSITION), 79); // last line
assert.equal(getScrollIndex(manyLines, 9999, TRANSITION), 79); // after end
assert.equal(getScrollIndex(manyLines, 0, TRANSITION), 0);

// --- during a gap the scroll holds at the next line ---

const gapLines = contiguousLines(4, 4); // 0-4, 4-8, 8-12, 12-16
assertInRange(getScrollIndex(gapLines, 9.5, TRANSITION), 2, 3);
assert.equal(getScrollIndex([], 5, TRANSITION), 0);

// --- transition starts one line below the active line and ends on it ---

const startOfLine1 = getScrollIndex(lines, 3.0, TRANSITION);
const midTransition = getScrollIndex(lines, 3.2, TRANSITION);
const endOfTransition = getScrollIndex(lines, 3.5, TRANSITION);
assert.ok(startOfLine1 >= 0 && startOfLine1 < 1.0001);
assert.ok(midTransition > startOfLine1, "scroll must move during the transition");
assert.equal(endOfTransition, 1);

console.log("timing tests passed");