const logArea = document.getElementById("logArea");

// For local dev: ws://127.0.0.1:8000/ws/logs/
// In production: wss://yourdomain.com/ws/logs/
const socket = new WebSocket("ws://" + window.location.host + "/ws/logs/");

socket.onmessage = function (e) {

    // Each incoming message is a single log line
    const logLine = e.data;
    console.log("Message from server:", logLine);
    logArea.textContent += logLine + "\n";
    // optionally auto-scroll
    logArea.scrollTop = logArea.scrollHeight;
};

socket.onopen = function (e) {
    console.log("WebSocket connection established!");
};
socket.onerror = function (e) {
    console.error("WebSocket error:", e);
};
socket.onclose = function (e) {
    console.log("WebSocket connection closed.");
};
document.body.addEventListener('htmx:beforeSwap', function (event) {
    if (event.detail.xhr.status === 204) {
        // Swap content even when the response is empty.
        event.detail.shouldSwap = true;
    }
});