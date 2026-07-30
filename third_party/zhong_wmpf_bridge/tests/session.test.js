const test = require("node:test");
const assert = require("node:assert/strict");

const {
    closeOpenClients,
    createSessionCoordinator,
    hasOpenClients,
    shouldResetCdpClients,
} = require("../src/session");

test("closeOpenClients closes only open clients", () => {
    const calls = [];
    const clients = [
        { readyState: 1, close: (code, reason) => calls.push({ code, reason, id: 1 }) },
        { readyState: 3, close: () => calls.push({ id: 2 }) },
        { readyState: 1, close: (code, reason) => calls.push({ code, reason, id: 3 }) },
    ];

    const closed = closeOpenClients(clients, 1012, "reset");

    assert.equal(closed, 2);
    assert.deepEqual(calls, [
        { code: 1012, reason: "reset", id: 1 },
        { code: 1012, reason: "reset", id: 3 },
    ]);
});

test("hasOpenClients returns whether an open client exists", () => {
    assert.equal(hasOpenClients([{ readyState: 3 }]), false);
    assert.equal(hasOpenClients([{ readyState: 3 }, { readyState: 1 }]), true);
});

test("shouldResetCdpClients requires a reconnect and an open cdp client", () => {
    assert.equal(shouldResetCdpClients(0, [{ readyState: 1 }]), false);
    assert.equal(shouldResetCdpClients(2, [{ readyState: 3 }]), false);
    assert.equal(shouldResetCdpClients(1, [{ readyState: 1 }]), true);
});

test("coordinator keeps DevTools session when miniapp reconnects within grace window", async () => {
    const closedReasons = [];
    const infoLogs = [];
    const coordinator = createSessionCoordinator({
        closeCdpClients: (reason) => {
            closedReasons.push(reason);
            return 1;
        },
        logger: {
            info: (message) => infoLogs.push(message),
        },
        disconnectGraceMs: 30,
    });

    coordinator.onMiniappDisconnected();
    assert.equal(coordinator.hasPendingDisconnectReset(), true);
    coordinator.onMiniappConnected();
    assert.equal(coordinator.hasPendingDisconnectReset(), false);

    await new Promise((resolve) => setTimeout(resolve, 50));

    assert.deepEqual(closedReasons, []);
    assert.equal(
        infoLogs.some((message) => message.includes("keeping DevTools session")),
        true
    );
});

test("coordinator resets DevTools session only after disconnect grace expires", async () => {
    const closedReasons = [];
    const coordinator = createSessionCoordinator({
        closeCdpClients: (reason) => {
            closedReasons.push(reason);
            return 1;
        },
        logger: null,
        disconnectGraceMs: 20,
    });

    coordinator.onMiniappDisconnected();
    assert.equal(closedReasons.length, 0);

    await new Promise((resolve) => setTimeout(resolve, 40));

    assert.deepEqual(closedReasons, [
        "miniapp runtime stayed disconnected, DevTools session must reconnect",
    ]);
});
