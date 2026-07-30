const create_logger = (options) => ({
    info: (...messages) => {
        console.log(...messages);
    },
    error: (...messages) => {
        console.error(...messages);
    },
    main_debug: (...messages) => {
        if (options.debugMain) {
            console.log(...messages);
        }
    },
    frida_debug: (...messages) => {
        if (options.debugFrida) {
            console.log(...messages);
        }
    }
});

module.exports = {
    create_logger
};
