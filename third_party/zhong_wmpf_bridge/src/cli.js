const { parseArgs } = require("node:util");

const DEBUG_PORT = 9421;
const CDP_PORT = 62000;

const print_help = () => {
    console.log(`Usage: node src/index.js [options]

Options:
  --debug-port <port>  Remote debug server port (default: ${DEBUG_PORT})
  --cdp-port <port>    CDP proxy server port (default: ${CDP_PORT})
  --pid <pid>          Attach to a specific WeChatAppEx pid
  --version <version>  Force a specific WMPF version config
  --debug-main         Output main process debug messages
  --debug-frida        Output Frida client messages
  -h, --help           Show this help message`);
};

const parse_port = (name, value, defaultValue) => {
    if (value === undefined) {
        return defaultValue;
    }

    const port = Number(value);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
        throw new Error(`[main] invalid ${name}: ${value}`);
    }

    return port;
};

const parse_cli_options = () => {
    const { values } = parseArgs({
        options: {
            "debug-port": { type: "string" },
            "cdp-port": { type: "string" },
            pid: { type: "string" },
            version: { type: "string" },
            "debug-main": { type: "boolean" },
            "debug-frida": { type: "boolean" },
            help: { type: "boolean", short: "h" }
        },
        allowPositionals: false
    });

    if (values.help) {
        print_help();
        process.exit(0);
    }

    return {
        debugPort: parse_port("--debug-port", values["debug-port"], DEBUG_PORT),
        cdpPort: parse_port("--cdp-port", values["cdp-port"], CDP_PORT),
        pid: values.pid ? Number(values.pid) : null,
        version: values.version ? Number(values.version) : null,
        debugMain: values["debug-main"] ?? false,
        debugFrida: values["debug-frida"] ?? false
    };
};

module.exports = {
    CDP_PORT,
    DEBUG_PORT,
    parse_cli_options
};
