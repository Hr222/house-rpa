const test = require("node:test");
const assert = require("node:assert/strict");

const {
    beginMiniappConnection,
    shouldReplayForProtocolEvent,
    markReplayComplete,
} = require("../src/replay-gate");

test("first miniapp connection does not wait for ready event replay", () => {
    const state = {
        hasSeenMiniappConnection: false,
        awaitingReplayReadyEvent: false,
    };

    const result = beginMiniappConnection(state);

    assert.equal(result, false);
    assert.equal(state.awaitingReplayReadyEvent, false);
});

test("reconnection waits for a ready protocol event before replay", () => {
    const state = {
        hasSeenMiniappConnection: true,
        awaitingReplayReadyEvent: false,
    };

    const result = beginMiniappConnection(state);

    assert.equal(result, true);
    assert.equal(state.awaitingReplayReadyEvent, true);
    assert.equal(shouldReplayForProtocolEvent(state, "setupContext"), true);
    assert.equal(shouldReplayForProtocolEvent(state, "chromeDevtoolsResult"), false);
});

test("markReplayComplete clears pending replay state", () => {
    const state = {
        hasSeenMiniappConnection: true,
        awaitingReplayReadyEvent: true,
    };

    markReplayComplete(state);

    assert.equal(state.awaitingReplayReadyEvent, false);
    assert.equal(shouldReplayForProtocolEvent(state, "setupContext"), false);
});
