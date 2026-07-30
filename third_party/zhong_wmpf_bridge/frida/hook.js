const getMainModule = (version) => {
    if (version >= 13331) {
        return Process.findModuleByName("flue.dll");
    }
    return Process.findModuleByName("WeChatAppEx.exe");
};

const patchCDPFilter = (base, config) => {
    // xref: SendToClientFilter OR devtools_message_filter_applet_webview.cc
    const offset = config.CDPFilterHookOffset;
    Interceptor.attach(base.add(offset), {
        onEnter(args) {
            this.inputValue = args[0];
        },
        onLeave(retval) {
            const inputValue = this.inputValue.readPointer();
            if (inputValue.isNull() || inputValue.add(8).isNull()) {
                // there's a chance the value could be null
                // return here to avoid crash
                return;
            }

            if (inputValue.add(8).readU32() == 6) {
                inputValue.add(8).writeU32(0x0);
            }
        },
    });
};

const safeReadPointer = (ptr) => {
    if (!ptr || ptr.isNull()) return null;
    try {
        const range = Process.findRangeByAddress(ptr);
        if (!range || !range.protection.includes('r')) return null;
        const result = ptr.readPointer();
        return result.isNull() ? null : result;
    } catch(e) {
        return null;
    }
};

const hookOnLoadScene = (a1, config) => {
    const sceneOffsets = config.SceneOffsets;
    const baseOffset = typeof config.SceneBaseOffset === "number" ? config.SceneBaseOffset : 56;

    const ptr1 = safeReadPointer(a1.add(baseOffset));
    if (!ptr1) { send(`[hook] scene chain: null at this+${baseOffset}`); return; }

    const ptr2 = safeReadPointer(ptr1.add(sceneOffsets[0]));
    if (!ptr2) { send(`[hook] scene chain: null at ptr1+${sceneOffsets[0]}`); return; }

    const ptr3 = safeReadPointer(ptr2.add(8));
    if (!ptr3) { send(`[hook] scene chain: null at ptr2+8`); return; }

    const ptr4 = safeReadPointer(ptr3.add(sceneOffsets[1]));
    if (!ptr4) { send(`[hook] scene chain: null at ptr3+${sceneOffsets[1]}`); return; }

    const ptr5 = safeReadPointer(ptr4.add(16));
    if (!ptr5) { send(`[hook] scene chain: null at ptr4+16`); return; }

    const miniappScenePtr = ptr5.add(sceneOffsets[2]);
    const range = Process.findRangeByAddress(miniappScenePtr);
    if (!range || !range.protection.includes('r')) {
        send(`[hook] scene chain: unmapped at ptr5+${sceneOffsets[2]}`);
        return;
    }

    const sceneValue = miniappScenePtr.readInt();
    send(`[hook] scene: ${sceneValue}`);

    // 1000: from issue #83 <-- will crash the process
    // 1007: from issue #80
    // 1008: from issue #53
    // 1027: from issue #78
    // 1035: from issue #78
    // 1053: from issue #25
    // 1074: from issue #32
    // 1145: from search
    // 1178: from phone (issue #117)
    // 1256: from recent
    // 1260: from frequently used
    // 1302: from services
    // 1308: minigame?
    const sceneNumberArray = [
        1005, 1007, 1008, 1027, 1035, 1053, 1074, 1145, 1178, 1256, 1260, 1302,
        1308,
    ];
    if (!sceneNumberArray.includes(sceneValue)) {
        return;
    }
    send("[hook] hook scene condition -> 1101");
    miniappScenePtr.writeInt(1101);

    // const websocketServerStringPtr = passArgs.add(8).readPointer().add(520);
    // VERBOSE && console.log("[hook] hook websocket server, original: ", websocketServerStringPtr.readUtf8String());
    // websocketServerStringPtr.writeUtf8String("ws://127.0.0.1:8189/");
};

const patchOnLoadStart = (base, config) => {
    // xref: AppletIndexContainer::OnLoadStart
    Interceptor.attach(base.add(config.LoadStartHookOffset), {
        onEnter(args) {
            send(
                `[inteceptor] AppletIndexContainer::OnLoadStart onEnter, ` +
                    `indexContainer.this: ${this.context.rcx}`,
            );
            // write dl to 0x1
            if ((this.context.rdx & 0xff) !== 1) {
                this.context.rdx = (this.context.rdx & ~0xff) | 0x1;
            }
            // handle onLoad scene
            hookOnLoadScene(this.context.rcx, config);
        },
        onLeave(retval) {
            // do nothing
        },
    });
};

const parseConfig = () => {
    const rawConfig = `@@CONFIG@@`;
    if (rawConfig.includes("@@")) {
        // test addresses
        return {
            Version: 18955,
            LoadStartHookOffset: "0x25B52C0",
            CDPFilterHookOffset: "0x30248B0",
            SceneOffsets: [1408, 1344, 488],
        };
    }
    return JSON.parse(rawConfig);
};

const main = () => {
    const config = parseConfig();
    const mainModule = getMainModule(config.Version);
    patchOnLoadStart(mainModule.base, config);
    patchCDPFilter(mainModule.base, config);
};

main();
