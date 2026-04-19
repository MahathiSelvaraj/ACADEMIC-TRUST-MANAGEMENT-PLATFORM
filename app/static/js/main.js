document.addEventListener("DOMContentLoaded", function () {
    const flashes = document.querySelectorAll(".flash");
    flashes.forEach((flash) => {
        flash.addEventListener("click", function () {
            flash.remove();
        });
        setTimeout(() => {
            if (flash.parentElement) {
                flash.remove();
            }
        }, 5000);
    });
    const selectAll = document.getElementById("select-all-records");
    const checkboxes = document.querySelectorAll(".record-checkbox");
    const selectedCount = document.getElementById("selected-count");

    function refreshSelectedCount() {
        if (!selectedCount) {
            return;
        }
        let count = 0;
        checkboxes.forEach((checkbox) => {
            if (checkbox.checked) {
                count += 1;
            }
        });
        selectedCount.textContent = `${count} record(s) selected`;
    }

    if (selectAll && checkboxes.length > 0) {
        selectAll.addEventListener("change", function () {
            checkboxes.forEach((checkbox) => {
                checkbox.checked = selectAll.checked;
            });
            refreshSelectedCount();
        });

        checkboxes.forEach((checkbox) => {
            checkbox.addEventListener("change", function () {
                const allChecked = Array.from(checkboxes).every((item) => item.checked);
                selectAll.checked = allChecked;
                refreshSelectedCount();
            });
        });
        refreshSelectedCount();
    }
});
