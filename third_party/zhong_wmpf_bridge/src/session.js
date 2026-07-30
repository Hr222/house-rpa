const WS_OPEN = 1;

const closeOpenClients = (clients, code, reason) => {
    let closed = 0;
    for (const client of clients) {
        if (client.readyState !== WS_OPEN) {
            continue;
        }
        client.close(code, reason);
        closed += 1;
    }
    return closed;
};

const hasOpenClients = (clients) => {
    for (const client of clients) {
        if (client.readyState === WS_OPEN) {
            return true;
        }
    }
    return false;
};

const shouldResetCdpClients = (miniappReconnectCount, cdpClients) => {
    return miniappReconnectCount > 0 && hasOpenClients(cdpClients);
};

const createSessionCoordinator = ({
    closeCdpClients,
    logger,
    disconnectGraceMs = 1500,
    setTimeoutFn = setTimeout,
    clearTimeoutFn = clearTimeout,
}) => {
    let disconnectTimer = null;

    return {
        hasPendingDisconnectReset() {
            return disconnectTimer !== null;
        },

        onMiniappConnected() {
            if (disconnectTimer !== null) {
                clearTimeoutFn(disconnectTimer);
                disconnectTimer = null;
                if (logger) {
                    logger.info(
                        `[sync] miniapp runtime reconnected within ${disconnectGraceMs}ms grace window; keeping DevTools session`
                    );
                }
            }
        },

        onMiniappDisconnected() {
            if (disconnectTimer !== null) {
                return;
            }

            if (logger) {
                logger.info(
                    `[sync] miniapp runtime disconnected; waiting ${disconnectGraceMs}ms before resetting DevTools session`
                );
            }

            disconnectTimer = setTimeoutFn(() => {
                disconnectTimer = null;
                const closed = closeCdpClients(
                    "miniapp runtime stayed disconnected, DevTools session must reconnect"
                );
                if (closed > 0 && logger) {
                    logger.info(
                        `[sync] miniapp runtime stayed disconnected, reset stale DevTools session; closed ${closed} CDP client(s)`
                    );
                }
            }, disconnectGraceMs);
        },

        dispose() {
            if (disconnectTimer !== null) {
                clearTimeoutFn(disconnectTimer);
                disconnectTimer = null;
            }
        },
    };
};

module.exports = {
    WS_OPEN,
    closeOpenClients,
    createSessionCoordinator,
    hasOpenClients,
    shouldResetCdpClients,
};
