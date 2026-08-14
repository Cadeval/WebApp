document.addEventListener("htmx:afterSwap", () => {
    // Make all metric/graph blocks copyable
    const copyBlocks = document.querySelectorAll(".copy-parent");

    copyBlocks.forEach(block => {
        // Add an accessible title for hover hint
        if (!block.hasAttribute("title")) {
            block.setAttribute("title", "Klicken zum Kopieren");
        }

        // Create a small toast element (once per block)
        const toast = document.createElement("span");
        toast.className = "copy-toast";
        toast.style.display = "none";
        toast.textContent = "Kopiert!";
        block.appendChild(toast);

        block.addEventListener("click", async (ev) => {
            try {
                // Default: copy visible text (change to innerHTML to copy raw markup)
                const text = block.querySelector(".copy-target") .innerText.trim();

                // Avoid copying empty strings
                if (!text) return;

                await navigator.clipboard.writeText(text);

                // Visual feedback
                block.classList.add("copy-flash");
                toast.style.display = "block";
                setTimeout(() => {
                    block.classList.remove("copy-flash");
                    toast.style.display = "none";
                }, 700);
            } catch (err) {
                console.error("Copy failed:", err);
                // Fallback (older browsers)
                const text = block.querySelector(".copy-target") .innerText.trim();
                fallbackCopy(text);
            }
        });
    });

    function fallbackCopy(text) {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); } catch(e) {}
        document.body.removeChild(ta);
    }
});