const buildReconnectResetEvents = () => {
    return [
        JSON.stringify({
            method: "Runtime.executionContextsCleared",
            params: {},
        }),
        JSON.stringify({
            method: "DOM.documentUpdated",
            params: {},
        }),
    ];
};

module.exports = {
    buildReconnectResetEvents,
};
