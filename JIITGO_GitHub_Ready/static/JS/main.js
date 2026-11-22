document.addEventListener("DOMContentLoaded", () => {

    // Set minimum departure date/time to now

    const date = document.getElementById(
        "departure"
    );

    if (date) {

        const d = new Date(
            Date.now() -
            new Date().getTimezoneOffset() * 60000
        );

        date.min = d.toISOString().slice(0, 16);

    }


    // Vehicle selection

    const vehicle = document.getElementById(
        "vehicle"
    );

    const capacities = [3, 4, 6];


    // Capacity buttons update vehicle dropdown

    document.querySelectorAll(
        'input[name="capacity"]'
    ).forEach(radio => {

        radio.addEventListener(
            "change",
            () => {

                if (vehicle) {

                    vehicle.selectedIndex = ({
                        3: 0,
                        4: 1,
                        6: 2
                    })[radio.value];

                }

            }
        );

    });


    // Dropdown updates capacity buttons

    if (vehicle) {

        vehicle.addEventListener(
            "change",
            () => {

                const index = vehicle.selectedIndex;

                const radio = document.querySelector(
                    `input[name="capacity"][value="${capacities[index]}"]`
                );

                if (radio) {
                    radio.checked = true;
                }

            }
        );

    }

});