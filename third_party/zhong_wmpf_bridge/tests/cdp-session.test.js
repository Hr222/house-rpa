const test = require("node:test");
const assert = require("node:assert/strict");

const {
    buildReplayMessages,
    recordReplayableMessage,
    shouldSwallowReplayResponse,
} = require("../src/cdp-session");

test("recordReplayableMessage stores replayable CDP methods", () => {
    const buffer = new Map();

    assert.equal(
        recordReplayableMessage(
            buffer,
            JSON.stringify({ id: 1, method: "Runtime.enable", params: {} })
        ),
        "Runtime.enable"
    );
    assert.equal(
        recordReplayableMessage(
            buffer,
            JSON.stringify({
                id: 2,
                method: "Debugger.setPauseOnExceptions",
                params: { state: "none" },
            })
        ),
        "Debugger.setPauseOnExceptions"
    );
    assert.equal(
        recordReplayableMessage(
            buffer,
            JSON.stringify({ id: 3, method: "DOM.getDocument", params: {} })
        ),
        null
    );

    assert.deepEqual([...buffer.keys()], [
        "Runtime.enable",
        "Debugger.setPauseOnExceptions",
    ]);
});

test("recordReplayableMessage treats session restoration methods as replayable", () => {
    const buffer = new Map();

    assert.equal(
        recordReplayableMessage(
            buffer,
            JSON.stringify({
                id: 10,
                method: "Page.startScreencast",
                params: { format: "jpeg", quality: 80 },
            })
        ),
        "Page.startScreencast"
    );
    assert.equal(
        recordReplayableMessage(
            buffer,
            JSON.stringify({
                id: 11,
                method: "Target.setAutoAttach",
                params: { autoAttach: true, waitForDebuggerOnStart: false, flatten: true },
            })
        ),
        "Target.setAutoAttach"
    );
    assert.equal(
        recordReplayableMessage(
            buffer,
            JSON.stringify({
                id: 12,
                method: "Runtime.addBinding",
                params: { name: "sendMessageToBackend" },
            })
        ),
        "Runtime.addBinding"
    );

    assert.deepEqual([...buffer.keys()], [
        "Page.startScreencast",
        "Target.setAutoAttach",
        "Runtime.addBinding",
    ]);
});

test("buildReplayMessages assigns fresh internal ids", () => {
    const buffer = new Map([
        ["Runtime.enable", { method: "Runtime.enable", params: {} }],
        ["Debugger.enable", { method: "Debugger.enable", params: {} }],
    ]);
    let nextId = 9000;

    const result = buildReplayMessages(buffer, () => ++nextId);

    assert.deepEqual(result.replayIds, [9001, 9002]);
    assert.deepEqual(result.rawMessages, [
        JSON.stringify({ id: 9001, method: "Runtime.enable", params: {} }),
        JSON.stringify({ id: 9002, method: "Debugger.enable", params: {} }),
    ]);
});

test("shouldSwallowReplayResponse removes handled replay ids", () => {
    const replayIds = new Set([8001, 8002]);

    assert.equal(
        shouldSwallowReplayResponse(JSON.stringify({ id: 7000, result: {} }), replayIds),
        false
    );
    assert.equal(
        shouldSwallowReplayResponse(JSON.stringify({ id: 8001, result: {} }), replayIds),
        true
    );
    assert.deepEqual([...replayIds], [8002]);
});
