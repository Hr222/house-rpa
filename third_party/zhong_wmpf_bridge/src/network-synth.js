// Synthesize Network.requestWillBeSent / Network.responseReceived events.
//
// Newer WeChat WMPF runtimes (>= ~19823) stopped sending the primary
// Network.requestWillBeSent / Network.responseReceived events for XHR/fetch
// traffic and only emit the *ExtraInfo variants. The Chrome DevTools Network
// panel keys every row on requestWillBeSent, so without it the panel stays
// empty even though dataReceived/loadingFinished flow through.
//
// This module watches the *ExtraInfo events (which carry the full request
// headers, including :method / :authority / :path / :scheme and the response
// status code) and, when a request id has not yet produced a real
// requestWillBeSent, fabricates a spec-compliant requestWillBeSent (and a
// matching responseReceived) so the panel can build the row.
//
// The fix lives entirely in the bridge and does not depend on any Frida
// offsets, so it works across WeChat versions.

const buildUrlFromHeaders = (headers) => {
    if (!headers) {
        return null;
    }

    // HTTP/2 style pseudo-headers
    const scheme = headers[":scheme"];
    const authority = headers[":authority"];
    const path = headers[":path"];
    if (scheme && authority && path) {
        return `${scheme}://${authority}${path}`;
    }

    // HTTP/1.1 style headers (image requests etc.)
    const host = headers.Host || headers.host;
    if (host) {
        const httpScheme = scheme || "https";
        const referer = headers.Referer || headers.referer;
        // requestWillBeSentExtraInfo for h1 has no path; fall back to "/"
        // but try to keep something meaningful if a referer is present.
        if (referer) {
            return referer;
        }
        return `${httpScheme}://${host}/`;
    }

    return null;
};

const extractMethod = (headers) => {
    if (!headers) {
        return "GET";
    }
    return headers[":method"] || headers.method || "GET";
};

// Map a mime type to a Chrome DevTools resource type so the panel shows the
// right category / icon for synthesized static-resource rows.
const guessResourceType = (mimeType) => {
    const m = (mimeType || "").toLowerCase();
    if (m.startsWith("image/")) return "Image";
    if (m.startsWith("video/") || m.startsWith("audio/")) return "Media";
    if (m.startsWith("font/") || m.includes("font")) return "Font";
    if (m.includes("javascript") || m.includes("ecmascript")) return "Script";
    if (m.includes("css")) return "Stylesheet";
    if (m.includes("html")) return "Document";
    if (m.includes("json")) return "XHR";
    return "Other";
};

// Guess a DevTools resource type from a URL path extension. Used for
// requestWillBeSent synthesized from *ExtraInfo events, where no mime type is
// available yet but the URL extension is a reliable hint.
const guessResourceTypeFromUrl = (url) => {
    if (!url) return null;
    // strip query string before matching the extension
    const path = url.split("?")[0].split("#")[0].toLowerCase();
    if (/\.(png|jpe?g|gif|webp|svg|ico|bmp|avif)$/.test(path)) return "Image";
    if (/\.(mp4|webm|ogg|mp3|wav|m4a|mov)$/.test(path)) return "Media";
    if (/\.(woff2?|ttf|otf|eot)$/.test(path)) return "Font";
    if (/\.(js|mjs)$/.test(path)) return "Script";
    if (/\.css$/.test(path)) return "Stylesheet";
    if (/\.(html?|wxml)$/.test(path)) return "Document";
    return null;
};

// Guess a DevTools resource type from request headers. The Sec-Fetch-Dest and
// Accept headers reveal the resource kind even when the URL has no usable
// extension (e.g. HTTP/1.1 image requests that only carry Host + Referer, or
// API-served images like index.php?file=x.jpg).
const guessResourceTypeFromRequestHeaders = (headers) => {
    if (!headers) return null;
    const dest = (
        headers["sec-fetch-dest"] ||
        headers["Sec-Fetch-Dest"] ||
        ""
    ).toLowerCase();
    if (dest === "image") return "Image";
    if (dest === "video" || dest === "audio") return "Media";
    if (dest === "font") return "Font";
    if (dest === "script") return "Script";
    if (dest === "style") return "Stylesheet";
    if (dest === "document" || dest === "iframe") return "Document";

    const accept = (headers.Accept || headers.accept || "").toLowerCase();
    if (accept.startsWith("image/") || accept.includes("image/")) {
        // Accept may be "*/*" for XHR; only treat as image when the FIRST type
        // is an image type (browsers send image/* first for <img>).
        if (/^\s*image\//.test(accept)) return "Image";
    }
    if (/^\s*text\/css/.test(accept)) return "Stylesheet";
    return null;
};

// Strip HTTP/2 pseudo-headers (":method" etc.) which DevTools does not expect
// inside a requestWillBeSent request.headers map.
const stripPseudoHeaders = (headers) => {
    const out = {};
    for (const key of Object.keys(headers || {})) {
        if (!key.startsWith(":")) {
            out[key] = headers[key];
        }
    }
    return out;
};

const createNetworkSynthesizer = () => {
    // requestIds that already have a real (or synthesized) requestWillBeSent
    const knownRequests = new Set();
    // requestIds that already have a real (or synthesized) responseReceived
    const respondedRequests = new Set();
    // cache last known url/frameId per requestId for response synthesis
    const requestMeta = new Map();

    // Returns an array of extra CDP messages (JSON strings) to emit to DevTools
    // before forwarding the original message. The original message is always
    // forwarded by the caller.
    const onMiniappCdpMessage = (payload) => {
        if (!payload || typeof payload.method !== "string") {
            return [];
        }

        const synthesized = [];

        // A real requestWillBeSent appeared: remember it, never synthesize.
        if (payload.method === "Network.requestWillBeSent") {
            const id = payload.params && payload.params.requestId;
            if (id) {
                knownRequests.add(id);
                const url = payload.params.request && payload.params.request.url;
                requestMeta.set(id, {
                    url,
                    frameId: payload.params.frameId,
                    loaderId: payload.params.loaderId,
                });
            }
            return synthesized;
        }

        if (payload.method === "Network.responseReceived") {
            const params = payload.params || {};
            const id = params.requestId;
            if (id) {
                respondedRequests.add(id);
                // Static resources (images, scripts, css) often arrive with a
                // real responseReceived (carrying response.url) but WITHOUT any
                // requestWillBeSent / *ExtraInfo. Back-fill a requestWillBeSent
                // so the panel can build the row.
                if (!knownRequests.has(id)) {
                    knownRequests.add(id);
                    const resp = params.response || {};
                    const url = resp.url;
                    if (url) {
                        requestMeta.set(id, {
                            url,
                            frameId: params.frameId,
                            loaderId: params.loaderId || id,
                        });
                        synthesized.push(
                            JSON.stringify({
                                method: "Network.requestWillBeSent",
                                params: {
                                    requestId: id,
                                    loaderId: params.loaderId || id,
                                    documentURL: url,
                                    request: {
                                        url,
                                        method: "GET",
                                        headers: {},
                                        mixedContentType: "none",
                                        initialPriority: "Low",
                                        referrerPolicy:
                                            "strict-origin-when-cross-origin",
                                    },
                                    timestamp:
                                        params.timestamp || Date.now() / 1000,
                                    wallTime: Date.now() / 1000,
                                    initiator: { type: "other" },
                                    redirectHasExtraInfo: false,
                                    type: resp.mimeType
                                        ? guessResourceType(resp.mimeType)
                                        : "Other",
                                },
                            })
                        );
                    }
                }
            }
            return synthesized;
        }

        // requestWillBeSentExtraInfo: synthesize the primary event if missing.
        if (payload.method === "Network.requestWillBeSentExtraInfo") {
            const params = payload.params || {};
            const id = params.requestId;
            if (!id || knownRequests.has(id)) {
                return synthesized;
            }

            const url = buildUrlFromHeaders(params.headers);
            if (!url) {
                return synthesized;
            }

            knownRequests.add(id);
            const method = extractMethod(params.headers);
            const requestHeaders = stripPseudoHeaders(params.headers);
            const timestamp =
                (params.connectTiming && params.connectTiming.requestTime) ||
                Date.now() / 1000;
            const referer = requestHeaders.Referer || requestHeaders.referer || url;
            // Resolve resource type. Request headers (Sec-Fetch-Dest / Accept)
            // are the browser's authoritative statement of the resource kind, so
            // they take priority over URL-extension guessing — important for h1
            // image requests whose only URL is the page Referer, and for
            // API-served images (index.php?file=x.jpg). Fall back to the URL
            // extension, then XHR.
            const resourceType =
                guessResourceTypeFromRequestHeaders(params.headers) ||
                guessResourceTypeFromUrl(url) ||
                "XHR";

            requestMeta.set(id, { url, frameId: undefined, loaderId: id, type: resourceType });

            synthesized.push(
                JSON.stringify({
                    method: "Network.requestWillBeSent",
                    params: {
                        requestId: id,
                        loaderId: id,
                        documentURL: referer,
                        request: {
                            url,
                            method,
                            headers: requestHeaders,
                            mixedContentType: "none",
                            initialPriority: "High",
                            referrerPolicy: "strict-origin-when-cross-origin",
                        },
                        timestamp,
                        wallTime: Date.now() / 1000,
                        initiator: { type: "script" },
                        redirectHasExtraInfo: false,
                        type: resourceType,
                    },
                })
            );
            return synthesized;
        }

        // responseReceivedExtraInfo: synthesize a responseReceived if missing.
        if (payload.method === "Network.responseReceivedExtraInfo") {
            const params = payload.params || {};
            const id = params.requestId;
            if (!id || respondedRequests.has(id)) {
                return synthesized;
            }

            respondedRequests.add(id);
            const meta = requestMeta.get(id) || {};
            const headers = params.headers || {};
            const statusCode = params.statusCode || 200;
            const mimeType =
                (headers["content-type"] || headers["Content-Type"] || "")
                    .split(";")[0]
                    .trim() || "text/plain";
            // Resource type priority: response content-type (most authoritative)
            // -> the type resolved at request time -> XHR. The response's
            // type field MUST match the requestWillBeSent type, otherwise
            // DevTools overwrites the row's category (e.g. Image -> XHR).
            const responseType =
                guessResourceType(mimeType) !== "Other"
                    ? guessResourceType(mimeType)
                    : meta.type || "XHR";

            synthesized.push(
                JSON.stringify({
                    method: "Network.responseReceived",
                    params: {
                        requestId: id,
                        loaderId: meta.loaderId || id,
                        timestamp: Date.now() / 1000,
                        type: responseType,
                        response: {
                            url: meta.url || "",
                            status: statusCode,
                            statusText: "",
                            headers,
                            mimeType,
                            connectionReused: false,
                            connectionId: 0,
                            fromDiskCache: false,
                            fromServiceWorker: false,
                            fromPrefetchCache: false,
                            encodedDataLength: -1,
                            protocol: "http/1.1",
                            securityState: "secure",
                        },
                        hasExtraInfo: true,
                        frameId: meta.frameId,
                    },
                })
            );
            return synthesized;
        }

        return synthesized;
    };

    const reset = () => {
        knownRequests.clear();
        respondedRequests.clear();
        requestMeta.clear();
    };

    return { onMiniappCdpMessage, reset };
};

module.exports = {
    createNetworkSynthesizer,
    buildUrlFromHeaders,
    extractMethod,
    stripPseudoHeaders,
    guessResourceType,
    guessResourceTypeFromUrl,
    guessResourceTypeFromRequestHeaders,
};
