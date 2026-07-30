const READY_EVENT_CATEGORIES = new Set([
    "setupContext",
    "addJsContext",
    "connectJsContext",
]);

const beginMiniappConnection = (state) => {
    const shouldWaitForReady = state.hasSeenMiniappConnection === true;
    state.awaitingReplayReadyEvent = shouldWaitForReady;
    return shouldWaitForReady;
};

const shouldReplayForProtocolEvent = (state, category) => {
    return state.awaitingReplayReadyEvent === true && READY_EVENT_CATEGORIES.has(category);
};

const markReplayComplete = (state) => {
    state.awaitingReplayReadyEvent = false;
};

module.exports = {
    READY_EVENT_CATEGORIES,
    beginMiniappConnection,
    shouldReplayForProtocolEvent,
    markReplayComplete,
};
