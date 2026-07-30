const test = require("node:test");
const assert = require("node:assert/strict");

const { buildReconnectResetEvents } = require("../src/reconnect-events");

test("buildReconnectResetEvents returns the expected synthetic CDP reset events", () => {
    const events = buildReconnectResetEvents().map((item) => JSON.parse(item));

    assert.deepEqual(events, [
        {
            method: "Runtime.executionContextsCleared",
            params: {},
        },
        {
            method: "DOM.documentUpdated",
            params: {},
        },
    ]);
});
