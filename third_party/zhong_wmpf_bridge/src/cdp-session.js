const REPLAYABLE_METHODS = new Set([
    "CSS.trackComputedStyleUpdates",
    "Debugger.setAsyncCallStackDepth",
    "Debugger.setPauseOnExceptions",
    "Emulation.setTouchEmulationEnabled",
    "Network.setCacheDisabled",
    "Page.addScriptToEvaluateOnNewDocument",
    "Page.setLifecycleEventsEnabled",
    "Page.startScreencast",
    "Runtime.setAsyncCallStackDepth",
    "Runtime.addBinding",
    "Target.setAutoAttach",
    "Target.setDiscoverTargets",
    "Target.setRemoteLocations",
]);

const isReplayableMethod = (method) => {
    return (
        typeof method === "string" &&
        (method.endsWith(".enable") || REPLAYABLE_METHODS.has(method))
    );
};

const parseJsonMessage = (message) => {
    if (typeof message !== "string") {
        message = message.toString();
    }

    try {
        return JSON.parse(message);
    } catch {
        return null;
    }
};

const recordReplayableMessage = (buffer, message) => {
    const payload = parseJsonMessage(message);
    if (!payload || !isReplayableMethod(payload.method)) {
        return null;
    }

    buffer.set(payload.method, {
        method: payload.method,
        params: payload.params || {},
    });
    return payload.method;
};

const buildReplayMessages = (buffer, nextInternalId) => {
    const replayIds = [];
    const rawMessages = [];

    for (const entry of buffer.values()) {
        const id = nextInternalId();
        replayIds.push(id);
        rawMessages.push(
            JSON.stringify({
                id,
                method: entry.method,
                params: entry.params || {},
            })
        );
    }

    return { replayIds, rawMessages };
};

const shouldSwallowReplayResponse = (message, pendingReplayIds) => {
    const payload = parseJsonMessage(message);
    if (!payload || typeof payload.id !== "number") {
        return false;
    }

    if (!pendingReplayIds.has(payload.id)) {
        return false;
    }

    pendingReplayIds.delete(payload.id);
    return true;
};

module.exports = {
    buildReplayMessages,
    isReplayableMethod,
    parseJsonMessage,
    recordReplayableMessage,
    shouldSwallowReplayResponse,
};
