// Unit tests for the Network event synthesizer.
//
// Run with:  node vendor/wmpfdebugger/test/network-synth.test.js
//
// These tests lock the behavior that fixes the empty Network panel on newer
// WeChat WMPF runtimes (>= 19823), which stopped sending primary
// Network.requestWillBeSent / Network.responseReceived events.

const assert = require("node:assert");
const {
    createNetworkSynthesizer,
    buildUrlFromHeaders,
    guessResourceType,
    guessResourceTypeFromUrl,
    guessResourceTypeFromRequestHeaders,
} = require("../src/network-synth");

let passed = 0;
const test = (name, fn) => {
    fn();
    passed += 1;
    console.log(`  ok - ${name}`);
};

const parse = (json) => JSON.parse(json);

// ---------------------------------------------------------------------------
// URL reconstruction
// ---------------------------------------------------------------------------
test("buildUrlFromHeaders reconstructs h2 pseudo-header URL", () => {
    const url = buildUrlFromHeaders({
        ":scheme": "https",
        ":authority": "api.juzijia.top",
        ":path": "/web/index.php?r=api/index/config",
    });
    assert.strictEqual(url, "https://api.juzijia.top/web/index.php?r=api/index/config");
});

test("buildUrlFromHeaders falls back to Referer for h1 image requests", () => {
    const url = buildUrlFromHeaders({
        Host: "servicewechat.com",
        Referer: "https://servicewechat.com/x/page-frame.html",
    });
    assert.strictEqual(url, "https://servicewechat.com/x/page-frame.html");
});

// ---------------------------------------------------------------------------
// Resource type detection
// ---------------------------------------------------------------------------
test("guessResourceType maps mime types", () => {
    assert.strictEqual(guessResourceType("image/png"), "Image");
    assert.strictEqual(guessResourceType("text/javascript"), "Script");
    assert.strictEqual(guessResourceType("text/css"), "Stylesheet");
    assert.strictEqual(guessResourceType("application/json"), "XHR");
});

test("guessResourceTypeFromUrl maps extensions", () => {
    assert.strictEqual(guessResourceTypeFromUrl("https://x/a.jpg"), "Image");
    assert.strictEqual(guessResourceTypeFromUrl("https://x/a.webp?v=1"), "Image");
    assert.strictEqual(guessResourceTypeFromUrl("https://x/a.js"), "Script");
    assert.strictEqual(guessResourceTypeFromUrl("https://api.x/index.php?r=api/x"), null);
});

test("guessResourceTypeFromRequestHeaders uses Sec-Fetch-Dest and Accept", () => {
    assert.strictEqual(guessResourceTypeFromRequestHeaders({ "Sec-Fetch-Dest": "image" }), "Image");
    assert.strictEqual(guessResourceTypeFromRequestHeaders({ "Sec-Fetch-Dest": "style" }), "Stylesheet");
    assert.strictEqual(guessResourceTypeFromRequestHeaders({ Accept: "image/webp,*/*;q=0.8" }), "Image");
    assert.strictEqual(guessResourceTypeFromRequestHeaders({ accept: "*/*" }), null);
});

// ---------------------------------------------------------------------------
// Synthesis from requestWillBeSentExtraInfo
// ---------------------------------------------------------------------------
test("synthesizes requestWillBeSent for an XHR API call", () => {
    const s = createNetworkSynthesizer();
    const out = s.onMiniappCdpMessage({
        method: "Network.requestWillBeSentExtraInfo",
        params: {
            requestId: "1",
            headers: {
                ":scheme": "https",
                ":authority": "api.juzijia.top",
                ":path": "/web/index.php?r=api/index/config",
                ":method": "GET",
                accept: "*/*",
            },
        },
    });
    assert.strictEqual(out.length, 1);
    const p = parse(out[0]);
    assert.strictEqual(p.method, "Network.requestWillBeSent");
    assert.strictEqual(p.params.requestId, "1");
    assert.strictEqual(p.params.request.url, "https://api.juzijia.top/web/index.php?r=api/index/config");
    assert.strictEqual(p.params.request.method, "GET");
    assert.strictEqual(p.params.type, "XHR");
});

test("does not synthesize twice for the same request id", () => {
    const s = createNetworkSynthesizer();
    const msg = {
        method: "Network.requestWillBeSentExtraInfo",
        params: { requestId: "1", headers: { ":scheme": "https", ":authority": "a", ":path": "/p", ":method": "GET" } },
    };
    assert.strictEqual(s.onMiniappCdpMessage(msg).length, 1);
    assert.strictEqual(s.onMiniappCdpMessage(msg).length, 0);
});

test("a real requestWillBeSent suppresses synthesis (backward compat)", () => {
    const s = createNetworkSynthesizer();
    s.onMiniappCdpMessage({
        method: "Network.requestWillBeSent",
        params: { requestId: "1", request: { url: "https://real/x" }, frameId: "F" },
    });
    const out = s.onMiniappCdpMessage({
        method: "Network.requestWillBeSentExtraInfo",
        params: { requestId: "1", headers: { ":scheme": "https", ":authority": "a", ":path": "/p", ":method": "GET" } },
    });
    assert.strictEqual(out.length, 0);
});

// ---------------------------------------------------------------------------
// Image type detection (the regression that showed images as XHR)
// ---------------------------------------------------------------------------
test("h1 image (URL is Referer) is typed Image via Sec-Fetch-Dest", () => {
    const s = createNetworkSynthesizer();
    const out = s.onMiniappCdpMessage({
        method: "Network.requestWillBeSentExtraInfo",
        params: {
            requestId: "img.1",
            headers: {
                Accept: "image/webp,*/*;q=0.8",
                Host: "servicewechat.com",
                Referer: "https://servicewechat.com/x/page-frame.html",
                "Sec-Fetch-Dest": "image",
            },
        },
    });
    assert.strictEqual(parse(out[0]).params.type, "Image");
});

test("API-served image (index.php?file=x.jpg) is typed Image via Sec-Fetch-Dest", () => {
    const s = createNetworkSynthesizer();
    const out = s.onMiniappCdpMessage({
        method: "Network.requestWillBeSentExtraInfo",
        params: {
            requestId: "img.2",
            headers: {
                ":scheme": "https",
                ":authority": "api.x",
                ":path": "/index.php?file=a.jpg",
                ":method": "GET",
                "Sec-Fetch-Dest": "image",
            },
        },
    });
    assert.strictEqual(parse(out[0]).params.type, "Image");
});

// ---------------------------------------------------------------------------
// responseReceived synthesis must NOT downgrade Image -> XHR
// ---------------------------------------------------------------------------
test("synthesized responseReceived keeps Image type for image content", () => {
    const s = createNetworkSynthesizer();
    const rws = s.onMiniappCdpMessage({
        method: "Network.requestWillBeSentExtraInfo",
        params: { requestId: "i.1", headers: { ":scheme": "https", ":authority": "api.x", ":path": "/web/uploads/a.jpg", ":method": "GET", "Sec-Fetch-Dest": "image" } },
    });
    const rr = s.onMiniappCdpMessage({
        method: "Network.responseReceivedExtraInfo",
        params: { requestId: "i.1", statusCode: 200, headers: { "content-type": "image/webp" } },
    });
    assert.strictEqual(parse(rws[0]).params.type, "Image");
    assert.strictEqual(parse(rr[0]).params.type, "Image");
});

test("synthesized responseReceived keeps XHR type for json content", () => {
    const s = createNetworkSynthesizer();
    s.onMiniappCdpMessage({
        method: "Network.requestWillBeSentExtraInfo",
        params: { requestId: "a.1", headers: { ":scheme": "https", ":authority": "api.x", ":path": "/web/index.php?r=api/x", ":method": "GET", accept: "*/*" } },
    });
    const rr = s.onMiniappCdpMessage({
        method: "Network.responseReceivedExtraInfo",
        params: { requestId: "a.1", statusCode: 200, headers: { "content-type": "application/json; charset=UTF-8" } },
    });
    assert.strictEqual(parse(rr[0]).params.type, "XHR");
});

// ---------------------------------------------------------------------------
// Static resources that only have responseReceived (no requestWillBeSent)
// ---------------------------------------------------------------------------
test("back-fills requestWillBeSent from a real responseReceived for static files", () => {
    const s = createNetworkSynthesizer();
    const out = s.onMiniappCdpMessage({
        method: "Network.responseReceived",
        params: {
            requestId: "js.1",
            response: { url: "https://servicewechat.com/__dev__/WAWorker.js", status: 200, mimeType: "text/javascript" },
        },
    });
    assert.strictEqual(out.length, 1);
    const p = parse(out[0]);
    assert.strictEqual(p.method, "Network.requestWillBeSent");
    assert.strictEqual(p.params.request.url, "https://servicewechat.com/__dev__/WAWorker.js");
    assert.strictEqual(p.params.type, "Script");
});

console.log(`\n${passed} tests passed`);
