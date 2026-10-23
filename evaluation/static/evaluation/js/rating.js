document.addEventListener("DOMContentLoaded", function () {

    document.querySelectorAll(".stars").forEach(group => {

        const stars = group.querySelectorAll(".star");
        const input = group.parentElement.querySelector("input[type='hidden']");
        let selectedValue = 0;

        function highlight(value) {
            stars.forEach((star, i) => {
                star.classList.toggle("active", i < value);
            });
        }

        stars.forEach((star, index) => {

            // Hover preview
            star.addEventListener("mouseenter", () => {
                highlight(index + 1);
            });

            // Save click
            star.addEventListener("click", () => {
                selectedValue = index + 1;
                input.value = selectedValue;
                highlight(selectedValue);
            });
        });

        // Restore selected value
        group.addEventListener("mouseleave", () => {
            highlight(selectedValue);
        });

    });

});