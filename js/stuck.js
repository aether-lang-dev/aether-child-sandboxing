// A guest that never yields: a busy loop that also catches every exception
// it can. The host's deadline sends CANCEL; the worker's interrupt hook in
// the engine finds it and stops the script with an error a catch cannot
// take, so the host is never left waiting.
hostHear();
hostTell('SAY spinning in while (true), catching everything');
var n = 0;
while (true) {
    try {
        n = n + 1;
    } catch (e) {
    }
}
