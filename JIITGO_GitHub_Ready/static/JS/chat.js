(() => {

    const socket = io();

    const cfg = window.JIITGO_CHAT;

    const box = document.getElementById("messages");


    function addMessage(m) {

        const wrap = document.createElement("div");

        wrap.className =
            "message " +
            (Number(m.user_id) === Number(cfg.userId)
                ? "mine"
                : "");


        const name = document.createElement("b");
        name.textContent = m.name;


        const p = document.createElement("p");
        p.textContent = m.message;


        const time = document.createElement("small");
        time.textContent = m.time;


        wrap.append(name, p, time);

        box.appendChild(wrap);

        box.scrollTop = box.scrollHeight;
    }


    socket.emit("join_ride_chat", {
        ride_id: cfg.rideId
    });


    socket.on("new_ride_message", addMessage);


    document
        .getElementById("chat-form")
        .addEventListener("submit", function (e) {

            e.preventDefault();

            const input =
                document.getElementById("chat-input");

            const message = input.value.trim();

            if (!message) {
                return;
            }

            socket.emit("send_ride_message", {
                ride_id: cfg.rideId,
                message: message
            });

            input.value = "";
        });


    box.scrollTop = box.scrollHeight;

})();