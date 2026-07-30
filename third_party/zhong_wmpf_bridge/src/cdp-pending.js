const parseRequestId = (message) => {
    if (typeof message !== "string") {
        message = message.toString();
    }

    try {
        const payload = JSON.parse(message);
        if (typeof payload.id === "number" && typeof payload.method === "string") {
            return {
                id: payload.id,
                method: payload.method,
            };
        }
    } catch {
        return null;
    }

    return null;
};

const parseResponseId = (message) => {
    if (typeof message !== "string") {
        message = message.toString();
    }

    try {
        const payload = JSON.parse(message);
        if (typeof payload.id === "number") {
            return payload.id;
        }
    } catch {
        return null;
    }

    return null;
};

const createPendingRequestStore = () => {
    const pending = new Map();

    return {
        add(rawMessage) {
            const parsed = parseRequestId(rawMessage);
            if (!parsed) {
                return null;
            }

            pending.set(parsed.id, parsed.method);
            return parsed;
        },

        resolve(rawMessage) {
            const id = parseResponseId(rawMessage);
            if (id === null) {
                return null;
            }

            const method = pending.get(id) || null;
            pending.delete(id);
            return method ? { id, method } : null;
        },

        rejectAll(reason) {
            const out = [];
            for (const [id, method] of pending.entries()) {
                out.push({
                    id,
                    method,
                    payload: JSON.stringify({
                        id,
                        error: {
                            code: -32000,
                            message: reason,
                        },
                    }),
                });
            }
            pending.clear();
            return out;
        },

        snapshot() {
            return [...pending.entries()].map(([id, method]) => ({ id, method }));
        },

        size() {
            return pending.size;
        },
    };
};

module.exports = {
    createPendingRequestStore,
};
