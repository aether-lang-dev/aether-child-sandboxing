// The JavaScript showcase's guest side of the conversation, on MicroQuickJS
// (mquickjs-ae) in js-worker. The engine here has no file, network, module
// or FFI API: its only way out is three functions the host (js/host.ae)
// mounts in the worker, each a message on the socket to the host:
//   hostHear()           the host's next message
//   hostTell(line)       a message to the host
//   hostRequest(name)    ask the host for a file: .granted, .text, .why

// One escape attempt: what happened, as a PROBE line.
function probe(what, attempt) {
    var got;
    try {
        attempt();
        got = 'ALLOWED';
    } catch (e) {
        got = 'refused (' + e + ')';
    }
    hostTell('PROBE ' + what + ': ' + got);
}

while (true) {
    var msg = hostHear();
    var at = msg.indexOf(' ');
    var verb = at < 0 ? msg : msg.substring(0, at);
    var rest = at < 0 ? '' : msg.substring(at + 1);

    if (verb === 'HELLO') hostTell('HELLO from MicroQuickJS (mquickjs-ae)');

    if (verb === 'ASK') {
        var w = rest.split(' ');
        if (w[0] === 'multiply') hostTell('ANSWER ' + w[1] * w[2]);
        else hostTell('ANSWER ? ' + w[0]);

        // The guest's own turn: it asks the host for things.
        var cfg = hostRequest('config');
        if (cfg.granted) hostTell('SAY config says: ' + cfg.text.trim());
        else hostTell('SAY no config: ' + cfg.why);
        var pw = hostRequest('/etc/passwd');
        if (pw.granted) hostTell('SAY read /etc/passwd through the host');
        else hostTell('SAY the host said no: ' + pw.why);

        // And it tries to help itself. Node's, browsers' and QuickJS's own
        // ways to a file, a socket or native code are simply not there.
        probe("load('/etc/passwd')", function () { load('/etc/passwd'); });
        probe("require('fs')", function () { require('fs'); });
        probe("std.loadFile / os.open (QuickJS's modules)", function () { std.loadFile('/etc/passwd'); });
        probe("new XMLHttpRequest()", function () { new XMLHttpRequest(); });
        probe("fetch('https://1.1.1.1/')", function () { fetch('https://1.1.1.1/'); });
        probe("__host_call('readFile', ...) past the mounts", function () { __host_call('readFile', '["/etc/passwd"]'); });
        hostTell('DONE');
    }

    if (verb === 'BYE') {
        hostTell('BYE');
        break;
    }
}
