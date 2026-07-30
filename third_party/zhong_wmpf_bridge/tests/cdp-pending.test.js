const test = require("node:test");
const assert = require("node:assert/strict");

const { createPendingRequestStore } = require("../src/cdp-pending");

test("tracks pending request ids and resolves them on response", () => {
    const store = createPendingRequestStore();

    assert.deepEqual(
        store.add(JSON.stringify({ id: 101, method: "DOM.getAttributes", params: {} })),
        { id: 101, method: "DOM.getAttributes" }
    );
    assert.equal(store.size(), 1);
    assert.deepEqual(store.resolve(JSON.stringify({ id: 101, result: {} })), {
        id: 101,
        method: "DOM.getAttributes",
    });
    assert.equal(store.size(), 0);
});

test("rejectAll clears pending requests with synthetic error payloads", () => {
    const store = createPendingRequestStore();
    store.add(JSON.stringify({ id: 7, method: "Input.dispatchMouseEvent", params: {} }));
    store.add(JSON.stringify({ id: 8, method: "DOM.getAttributes", params: {} }));

    const rejected = store.rejectAll("miniapp runtime reconnected");

    assert.equal(store.size(), 0);
    assert.deepEqual(rejected.map((item) => ({ id: item.id, method: item.method })), [
        { id: 7, method: "Input.dispatchMouseEvent" },
        { id: 8, method: "DOM.getAttributes" },
    ]);
    assert.equal(
        rejected[0].payload,
        JSON.stringify({
            id: 7,
            error: {
                code: -32000,
                message: "miniapp runtime reconnected",
            },
        })
    );
});
