(() => {
  const body = document.body;
  document.querySelectorAll("[data-sidebar-open]").forEach((button) => button.addEventListener("click", () => body.classList.add("sidebar-open")));
  document.querySelectorAll("[data-sidebar-close]").forEach((element) => element.addEventListener("click", () => body.classList.remove("sidebar-open")));
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") body.classList.remove("sidebar-open"); });
  document.querySelectorAll("[data-dismiss-alert]").forEach((button) => button.addEventListener("click", () => button.closest(".app-alert")?.remove()));

  document.querySelectorAll("[data-table-search]").forEach((input) => {
    const table = document.querySelector(input.dataset.tableSearch);
    if (!table) return;
    input.addEventListener("input", () => {
      const query = input.value.trim().toLocaleLowerCase();
      table.querySelectorAll("tbody tr[data-search-row]").forEach((row) => { row.hidden = !row.textContent.toLocaleLowerCase().includes(query); });
    });
  });

  document.querySelectorAll("[data-upload-zone]").forEach((zone) => {
    const input = zone.querySelector('input[type="file"]');
    if (!input) return;
    ["dragenter", "dragover"].forEach((name) => zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.add("dragover"); }));
    ["dragleave", "drop"].forEach((name) => zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.remove("dragover"); }));
    zone.addEventListener("drop", (event) => { if (event.dataTransfer.files.length) { input.files = event.dataTransfer.files; input.dispatchEvent(new Event("change", { bubbles: true })); } });
    input.addEventListener("change", () => { const label = zone.querySelector("[data-file-name]"); if (label && input.files[0]) label.textContent = input.files[0].name; });
    zone.addEventListener("click", (event) => { if (!event.target.closest("button, a, input, label")) input.click(); });
  });
})();
